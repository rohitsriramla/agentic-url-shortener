"""Demonstrates Greenfield, Brownfield, and Ambiguous SDLC runs plus governance controls."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.sdlc_agents import AgentRegistry
from src.orchestrator.engine import SDLCOrchestrator
from src.orchestrator.models import ScenarioType, StageName


def run_greenfield_with_human_checkpoints() -> dict:
    orch = SDLCOrchestrator(auto_approve_human_gates=False)
    ctx = orch.create_context(
        ScenarioType.GREENFIELD,
        "Build a high-throughput URL shortener with Base62 codes, custom aliases, TTL expiration, rate limiting, and click analytics.",
    )
    # Wave 1 pauses at HUMAN_DESIGN_GATE
    orch.run(ctx)
    assert orch.stages[StageName.HUMAN_DESIGN_GATE].status.value == "awaiting_approval"

    # Human architect reviews and approves design gate
    orch.approve_human_gate(
        ctx,
        StageName.HUMAN_DESIGN_GATE,
        approved=True,
        reviewer="principal-architect@company.com",
        notes="Approved cache-aside + token-bucket + SSRF policy architecture.",
    )
    # Wave 2 executes implementation + parallel verification wave, pauses at RELEASE_READINESS_GATE
    orch.run(ctx)
    assert orch.stages[StageName.RELEASE_READINESS_GATE].status.value == "awaiting_approval"

    # Release owner approves production rollout
    orch.approve_human_gate(
        ctx,
        StageName.RELEASE_READINESS_GATE,
        approved=True,
        reviewer="release-manager@company.com",
        notes="All security guardrails and tests verified.",
    )
    orch.run(ctx)
    return {
        "scenario": "greenfield",
        "run_id": ctx.run_id,
        "final_gate_status": orch.stages[StageName.RELEASE_READINESS_GATE].status.value,
        "metrics": ctx.metrics.summary(),
        "decisions": [d.model_dump() for d in ctx.decision_lineage],
    }


def run_brownfield_with_retry_and_replan() -> dict:
    # Inject a transient failure once in implementation to verify bounded retry + MTTR tracking
    registry = AgentRegistry(simulate_transient_failure_once=True)
    orch = SDLCOrchestrator(agent_registry=registry, auto_approve_human_gates=True)
    ctx = orch.create_context(
        ScenarioType.BROWNFIELD,
        "Extend existing v1 URL shortener to add custom aliases, TTL expiration (HTTP 410), and referrer click analytics without breaking legacy short codes.",
    )
    orch.run(ctx)

    # Simulate upstream scope change triggering dynamic re-planning
    orch.replan_from_updated_requirement(
        ctx,
        "Extend existing v1 URL shortener with custom aliases, TTL expiration (HTTP 410), referrer click analytics, AND strict SSRF IP blocklist.",
    )
    orch.run(ctx)
    return {
        "scenario": "brownfield_with_retry_and_replan",
        "run_id": ctx.run_id,
        "version": ctx.version,
        "codebase_impact": ctx.codebase_impact,
        "metrics": ctx.metrics.summary(),
    }


def run_ambiguous_requirement() -> dict:
    orch = SDLCOrchestrator(auto_approve_human_gates=True)
    ctx = orch.create_context(
        ScenarioType.AMBIGUOUS,
        "Make a fast, scalable link shortener that tracks clicks and stops abuse.",
    )
    orch.run(ctx)
    return {
        "scenario": "ambiguous",
        "run_id": ctx.run_id,
        "ambiguities_identified": ctx.normalized_spec["ambiguities_identified"],
        "normalized_assumptions": ctx.normalized_spec["normalized_assumptions"],
        "metrics": ctx.metrics.summary(),
    }


if __name__ == "__main__":
    results = {
        "greenfield": run_greenfield_with_human_checkpoints(),
        "brownfield": run_brownfield_with_retry_and_replan(),
        "ambiguous": run_ambiguous_requirement(),
    }
    print(json.dumps(results, indent=2))
