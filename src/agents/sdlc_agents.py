"""Specialized SDLC Agents with deterministic execution, simulated transient fault injection, and fallbacks."""
from __future__ import annotations

from typing import Any, Dict, List
from src.orchestrator.models import DecisionRecord, ScenarioType, WorkflowContext


class TransientAgentError(RuntimeError):
    """Simulated transient LLM/tool failure that triggers bounded retry or fallback."""


class AgentRegistry:
    """Provides specialized agents for each SDLC stage."""

    def __init__(self, simulate_transient_failure_once: bool = False):
        self.simulate_transient_failure_once = simulate_transient_failure_once
        self._failed_once_stages: set[str] = set()

    def _maybe_inject_fault(self, stage: str) -> None:
        if self.simulate_transient_failure_once and stage not in self._failed_once_stages:
            self._failed_once_stages.add(stage)
            raise TransientAgentError(f"Simulated transient tool timeout in stage '{stage}'")

    def run_requirement_intake(self, ctx: WorkflowContext) -> Dict[str, Any]:
        raw = ctx.raw_requirement.strip()
        ambiguities: List[str] = []
        assumptions: List[str] = []

        if ctx.scenario_type == ScenarioType.AMBIGUOUS or "fast" in raw.lower() or "scalable" in raw.lower():
            ambiguities.extend([
                "Slug collision strategy unspecified (deterministic hash vs. random Base62 with retry).",
                "Retention/TTL semantics for expired links unspecified (hard delete vs. HTTP 410 Gone).",
                "Rate-limit thresholds and abuse prevention quotas unspecified.",
                "Analytics granularity unspecified (raw click stream vs. aggregated counters + referrer/geo).",
            ])
            assumptions.extend([
                "Default short code length: 7-char Base62 (~3.5T namespace) with collision retry.",
                "Expired links return HTTP 410 Gone and are excluded from active cache.",
                "Token-bucket rate limiter enforced at 60 req/min per client key/IP.",
                "Analytics captures timestamp, user-agent, referrer, and latency metrics asynchronously.",
            ])
        elif ctx.scenario_type == ScenarioType.BROWNFIELD:
            ambiguities.append("Backward compatibility expectations for existing v1 short codes during v2 rollout.")
            assumptions.append("Preserve 100% backward compatibility for existing 6/7-char slugs; add custom alias & TTL support non-destructively.")
        else:
            assumptions.append("RESTful JSON API with sub-50ms p95 redirect target and idempotent custom alias creation.")

        normalized = {
            "goal": "Production-grade URL Shortener Service with core CRUD/redirect APIs, click analytics, TTL expiration, rate limiting, and circuit-breaker resilience.",
            "scenario": ctx.scenario_type.value,
            "functional_requirements": [
                "POST /api/v1/urls - Create short URL (auto Base62 or custom alias, optional TTL)",
                "GET /{code} - Redirect (302/301) with cache-aside lookup and async analytics recording",
                "GET /api/v1/urls/{code}/analytics - Retrieve click counts, referrer breakdown, and status",
                "DELETE /api/v1/urls/{code} - Soft-revoke short link",
                "GET /health - Liveness, readiness, and dependency circuit-breaker status",
            ],
            "non_functional_requirements": [
                "p95 redirect latency < 20ms (in-memory/Redis cache hit)",
                "SSRF & unsafe URL scheme blocking",
                "Token-bucket rate limiting per client",
                "Structured audit logging and observability counters",
            ],
            "ambiguities_identified": ambiguities,
            "normalized_assumptions": assumptions,
        }
        ctx.normalized_spec = normalized
        ctx.decision_lineage.append(
            DecisionRecord(
                stage="requirement_intake",
                decision="Normalized requirement into explicit functional/NFR specification",
                rationale="Resolved underspecified behaviors into safe defaults (Base62, HTTP 410 on expiry, token-bucket rate limit).",
                trade_offs=["Chose HTTP 302 temporary redirect over 301 permanent redirect so analytics remain accurate and revocations take effect immediately."],
            )
        )
        return normalized

    def run_codebase_impact_analysis(self, ctx: WorkflowContext) -> Dict[str, Any]:
        if ctx.scenario_type == ScenarioType.BROWNFIELD:
            impact = {
                "mode": "brownfield",
                "impacted_modules": [
                    "src/service/storage.py (schema extension for custom_alias, expires_at, revoked)",
                    "src/service/shortener.py (collision check, TTL enforcement, HTTP 410 handling)",
                    "src/service/rate_limiter.py (new token-bucket middleware before URL creation)",
                    "src/service/api.py (new /analytics and DELETE endpoints + backward-compatible GET /{code})",
                ],
                "data_flow_changes": "Write path adds URL policy validation + rate limit check -> atomic store write; Read path checks cache -> validates expiry/revocation -> emits analytics event.",
                "backward_compatibility": "Existing v1 slugs continue resolving without migration downtime.",
                "risk_hotspots": ["Slug namespace collision between legacy numeric IDs and custom aliases", "Cache invalidation on DELETE/expiry"],
            }
        else:
            impact = {
                "mode": ctx.scenario_type.value,
                "impacted_modules": ["All modules created fresh under src/service/"],
                "data_flow_changes": "Clean layered architecture: API -> Policy/RateLimiter -> ShortenerService -> Repository + AnalyticsCollector.",
                "backward_compatibility": "N/A (new service)",
                "risk_hotspots": ["Hot-key thundering herd on viral links", "Unbounded analytics memory growth"],
            }
        ctx.codebase_impact = impact
        ctx.decision_lineage.append(
            DecisionRecord(
                stage="codebase_impact_analysis",
                decision=f"Mapped architectural blast radius ({impact['mode']})",
                rationale="Identified impacted APIs, storage schemas, and cache invalidation paths before decomposition.",
                trade_offs=["Explicit cache invalidation on delete/expiry adds 1 write op but prevents stale redirects."],
            )
        )
        return impact

    def run_task_decomposition(self, ctx: WorkflowContext) -> Dict[str, Any]:
        tasks = [
            {"id": "T1", "title": "Define Pydantic schemas & error contracts", "depends_on": [], "owner_agent": "ImplementationAgent"},
            {"id": "T2", "title": "Implement thread-safe Storage & Cache-Aside Repository", "depends_on": ["T1"], "owner_agent": "ImplementationAgent"},
            {"id": "T3", "title": "Implement Base62 generator, SSRF validator, and Token-Bucket Rate Limiter", "depends_on": ["T1"], "owner_agent": "ImplementationAgent"},
            {"id": "T4", "title": "Implement ShortenerService with TTL & Circuit Breaker", "depends_on": ["T2", "T3"], "owner_agent": "ImplementationAgent"},
            {"id": "T5", "title": "Expose FastAPI REST & Redirect endpoints + Analytics", "depends_on": ["T4"], "owner_agent": "ImplementationAgent"},
            {"id": "T6", "title": "Security policy audit & automated unit/integration test suite", "depends_on": ["T5"], "owner_agent": "TestingAndSecurityAgents"},
            {"id": "T7", "title": "Generate OpenAPI/Architecture docs & Release Readiness bundle", "depends_on": ["T5"], "owner_agent": "DocumentationAgent"},
        ]
        ctx.task_graph = tasks
        return {"tasks": tasks, "critical_path": ["T1", "T2", "T4", "T5", "T6"]}

    def run_architecture_design(self, ctx: WorkflowContext) -> Dict[str, Any]:
        arch = {
            "pattern": "Modular Hexagonal / Layered Service with Cache-Aside & Token-Bucket Rate Limiting",
            "components": {
                "api_layer": "FastAPI router with strict Pydantic validation and structured error responses",
                "guardrail_layer": "PolicyGuardrails (SSRF/scheme blocking) + TokenBucketRateLimiter",
                "core_domain": "ShortenerService (Base62 generation, custom alias validation, TTL checks, idempotency)",
                "storage_layer": "Thread-safe InMemory/SQLite-ready Repository with LRU hot-cache and atomic counters",
                "analytics_engine": "Non-blocking click recorder aggregating referrers, user-agents, and time-series buckets",
            },
            "security_controls": ["rate_limiting", "ssrf_validation", "audit_logging", "input_sanitization"],
            "reliability_patterns": ["cache_aside", "bounded_collision_retry", "circuit_breaker_health_check", "graceful_degradation"],
        }
        ctx.architecture = arch
        ctx.decision_lineage.append(
            DecisionRecord(
                stage="architecture_design",
                decision="Selected Modular Layered Architecture with Cache-Aside and Token-Bucket Rate Limiting",
                rationale="Balances low-latency redirects with strict SSRF protection, testability, and clean extension seams.",
                trade_offs=[
                    "In-memory + pluggable repository interface chosen for zero-dependency runnable prototype; interface supports drop-in Redis/PostgreSQL adapters in production.",
                    "Cryptographically random Base62 slugs prevent sequential enumeration attacks at the cost of a collision check on write.",
                ],
            )
        )
        return arch

    def run_implementation(self, ctx: WorkflowContext, fallback_mode: bool = False) -> Dict[str, Any]:
        if not fallback_mode:
            self._maybe_inject_fault("implementation")

        artifacts = {
            "service_modules": [
                "src/service/storage.py",
                "src/service/rate_limiter.py",
                "src/service/shortener.py",
                "src/service/api.py",
            ],
            "rate_limit_enabled": True,
            "ssrf_protection_enabled": True,
            "fallback_used": fallback_mode,
            "endpoints": [
                "POST /api/v1/urls",
                "GET /{code}",
                "GET /api/v1/urls/{code}/analytics",
                "DELETE /api/v1/urls/{code}",
                "GET /health",
            ],
        }
        ctx.generated_artifacts = artifacts
        return artifacts

    def run_security_policy_audit(self, ctx: WorkflowContext) -> Dict[str, Any]:
        report = {
            "ssrf_protection": "Verified (blocks localhost, metadata IPs, javascript:/file:/data: schemes)",
            "enumeration_resistance": "Verified (cryptographic random Base62 codes)",
            "abuse_mitigation": "Verified (token-bucket rate limiter enforced on write endpoints)",
            "secret_scan": "Clean (0 hardcoded credentials)",
            "status": "PASS",
        }
        ctx.security_report = report
        return report

    def run_testing_validation(self, ctx: WorkflowContext) -> Dict[str, Any]:
        report = {
            "unit_tests_planned": 14,
            "integration_tests_planned": 8,
            "edge_cases_covered": [
                "Custom alias conflict (HTTP 409)",
                "Expired URL access (HTTP 410)",
                "Revoked URL access (HTTP 410)",
                "Unsafe URL scheme / SSRF target blocked (HTTP 400)",
                "Rate limit exceeded (HTTP 429)",
                "Non-existent code lookup (HTTP 404)",
            ],
            "status": "READY_FOR_PYTEST",
        }
        ctx.test_report = report
        return report

    def run_documentation(self, ctx: WorkflowContext) -> Dict[str, Any]:
        doc = {
            "readme_updated": True,
            "architecture_doc": "docs/ARCHITECTURE.md",
            "api_contract": "OpenAPI 3.1 auto-generated by FastAPI at /openapi.json",
            "decision_count": len(ctx.decision_lineage),
        }
        ctx.documentation = doc
        return doc
