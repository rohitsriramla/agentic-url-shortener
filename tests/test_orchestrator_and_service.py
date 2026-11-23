"""Comprehensive unit and integration tests for the SDLC Orchestrator and URL Shortener API."""
from __future__ import annotations

import time
from fastapi.testclient import TestClient

from src.agents.sdlc_agents import AgentRegistry
from src.orchestrator.engine import SDLCOrchestrator
from src.orchestrator.models import PolicyViolationError, ScenarioType, StageName, StageStatus
from src.policy.guardrails import PolicyGuardrails
from src.service.api import create_app
from src.service.rate_limiter import TokenBucketRateLimiter
from src.service.shortener import ShortenerService


# --- Orchestrator & Governance Tests ---

def test_dag_pauses_at_human_gates_and_completes_after_approval():
    orch = SDLCOrchestrator(auto_approve_human_gates=False)
    ctx = orch.create_context(ScenarioType.GREENFIELD, "Build a URL shortener service")

    orch.run(ctx)
    assert orch.stages[StageName.ARCHITECTURE_DESIGN].status == StageStatus.COMPLETED
    assert orch.stages[StageName.HUMAN_DESIGN_GATE].status == StageStatus.AWAITING_APPROVAL
    assert orch.stages[StageName.IMPLEMENTATION].status == StageStatus.PENDING

    orch.approve_human_gate(ctx, StageName.HUMAN_DESIGN_GATE, True, "architect@corp.com", "Looks solid")
    orch.run(ctx)
    assert orch.stages[StageName.SECURITY_POLICY_AUDIT].status == StageStatus.COMPLETED
    assert orch.stages[StageName.TESTING_VALIDATION].status == StageStatus.COMPLETED
    assert orch.stages[StageName.DOCUMENTATION].status == StageStatus.COMPLETED
    assert orch.stages[StageName.RELEASE_READINESS_GATE].status == StageStatus.AWAITING_APPROVAL

    orch.approve_human_gate(ctx, StageName.RELEASE_READINESS_GATE, True, "release@corp.com", "Ship it")
    orch.run(ctx)
    assert orch.stages[StageName.RELEASE_READINESS_GATE].status == StageStatus.COMPLETED
    assert not ctx.safe_stopped
    assert ctx.metrics.success_rate == 1.0


def test_human_gate_rejection_triggers_safe_stop():
    orch = SDLCOrchestrator(auto_approve_human_gates=False)
    ctx = orch.create_context(ScenarioType.GREENFIELD, "Build a URL shortener service")
    orch.run(ctx)

    orch.approve_human_gate(ctx, StageName.HUMAN_DESIGN_GATE, False, "security@corp.com", "Missing threat model")
    assert ctx.safe_stopped is True
    assert orch.stages[StageName.HUMAN_DESIGN_GATE].status == StageStatus.HALTED_SAFE_STOP
    assert orch.stages[StageName.IMPLEMENTATION].status == StageStatus.PENDING


def test_bounded_retry_recovers_from_transient_agent_failure():
    registry = AgentRegistry(simulate_transient_failure_once=True)
    orch = SDLCOrchestrator(agent_registry=registry, auto_approve_human_gates=True)
    ctx = orch.create_context(ScenarioType.BROWNFIELD, "Add custom aliases and TTL to existing shortener")

    orch.run(ctx)
    assert not ctx.safe_stopped
    assert ctx.metrics.retry_count == 1
    assert ctx.metrics.mttr_ms >= 0.0
    assert orch.stages[StageName.IMPLEMENTATION].status == StageStatus.COMPLETED


def test_dynamic_replan_resets_and_reexecutes_dag():
    orch = SDLCOrchestrator(auto_approve_human_gates=True)
    ctx = orch.create_context(ScenarioType.AMBIGUOUS, "Build a fast link shortener")
    orch.run(ctx)
    assert ctx.version == 1

    orch.replan_from_updated_requirement(ctx, "Build a fast link shortener with custom aliases and 24h TTL")
    assert ctx.version == 2
    assert ctx.metrics.replan_count == 1
    orch.run(ctx)
    assert orch.stages[StageName.RELEASE_READINESS_GATE].status == StageStatus.COMPLETED


def test_policy_guardrails_block_secrets_and_ssrf():
    try:
        PolicyGuardrails.enforce_stage_policy("implementation", {"api_key": "sk-secret-12345"})
        assert False, "Expected PolicyViolationError"
    except PolicyViolationError:
        pass

    allowed, _ = PolicyGuardrails.validate_target_url("http://169.254.169.254/latest/meta-data/")
    assert allowed is False
    allowed_js, _ = PolicyGuardrails.validate_target_url("javascript:alert(1)")
    assert allowed_js is False


# --- URL Shortener API Integration Tests ---

def test_url_shortener_end_to_end_lifecycle():
    app = create_app(
        service=ShortenerService(),
        rate_limiter=TokenBucketRateLimiter(capacity=5, refill_rate_per_sec=0.1),
    )
    client = TestClient(app)

    # 1. Create auto-generated short link
    res = client.post("/api/v1/urls", json={"target_url": "https://example.com/docs/architecture"})
    assert res.status_code == 201
    code = res.json()["code"]
    assert len(code) == 7

    # 2. Redirect & verify click analytics
    redir = client.get(f"/{code}", headers={"referer": "https://news.ycombinator.com"}, follow_redirects=False)
    assert redir.status_code == 302
    assert redir.headers["location"] == "https://example.com/docs/architecture"

    analytics = client.get(f"/api/v1/urls/{code}/analytics")
    assert analytics.status_code == 200
    assert analytics.json()["click_count"] == 1
    assert analytics.json()["referrer_breakdown"]["https://news.ycombinator.com"] == 1

    # 3. Custom alias conflict
    c1 = client.post("/api/v1/urls", json={"target_url": "https://example.com", "custom_alias": "launch-2026"})
    assert c1.status_code == 201
    c2 = client.post("/api/v1/urls", json={"target_url": "https://example.com", "custom_alias": "launch-2026"})
    assert c2.status_code == 409

    # 4. Revocation -> HTTP 410 Gone
    del_res = client.delete("/api/v1/urls/launch-2026")
    assert del_res.status_code == 204
    gone_res = client.get("/launch-2026", follow_redirects=False)
    assert gone_res.status_code == 410

    # 5. SSRF protection -> HTTP 400
    bad = client.post("/api/v1/urls", json={"target_url": "http://127.0.0.1:8080/admin"})
    assert bad.status_code == 400


def test_ttl_expiration_and_rate_limiting():
    app = create_app(
        service=ShortenerService(),
        rate_limiter=TokenBucketRateLimiter(capacity=2, refill_rate_per_sec=0.0),
    )
    client = TestClient(app)

    r1 = client.post("/api/v1/urls", json={"target_url": "https://example.com/1", "ttl_seconds": 1}, headers={"X-Client-Id": "user-a"})
    assert r1.status_code == 201
    code = r1.json()["code"]

    r2 = client.post("/api/v1/urls", json={"target_url": "https://example.com/2"}, headers={"X-Client-Id": "user-a"})
    assert r2.status_code == 201

    # 3rd request exceeds bucket capacity=2 -> HTTP 429
    r3 = client.post("/api/v1/urls", json={"target_url": "https://example.com/3"}, headers={"X-Client-Id": "user-a"})
    assert r3.status_code == 429

    # Wait for TTL expiration -> HTTP 410
    time.sleep(1.05)
    expired = client.get(f"/{code}", follow_redirects=False)
    assert expired.status_code == 410
