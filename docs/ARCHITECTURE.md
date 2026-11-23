# Architecture Overview & Engineering Summary

## 1. System Architecture
The system is organized into two tightly integrated planes:
1. **Control Plane (`src/orchestrator/`, `src/agents/`, `src/policy/`)**: A stateful, DAG-based SDLC orchestration engine that converts raw engineering requests (Greenfield, Brownfield, or Ambiguous) into governed, auditable software artifacts.
2. **Data / Application Plane (`src/service/`)**: A production-style FastAPI URL Shortener implementing Base62 code generation, custom aliases, TTL expiration (`HTTP 410`), thread-safe LRU cache-aside storage, token-bucket rate limiting (`HTTP 429`), SSRF guardrails (`HTTP 400`), and click analytics.

## 2. Non-Linear DAG Control Flow & Governance
```
requirement_intake
   ├──► codebase_impact_analysis  (parallel wave 1)
   └──► task_decomposition        (parallel wave 1)
             │
             ▼
    architecture_design
             │
             ▼
    [HUMAN_DESIGN_GATE]  ◄── Mandatory human checkpoint
             │
             ▼
       implementation    ◄── Bounded retries + deterministic fallback + pre-stage snapshot
        ├───┼───┐
        ▼   ▼   ▼
  security test docs     (parallel wave 2)
        └───┼───┘
            ▼
  [RELEASE_READINESS_GATE] ◄── Mandatory human checkpoint
```

### Key Governance Capabilities
- **Entry & Exit Gates**: Every node verifies upstream DAG completion on entry and non-empty, policy-compliant outputs on exit.
- **Policy Guardrails**: Scans artifacts for hardcoded secrets, enforces mandatory security controls (`rate_limiting`, `ssrf_validation`, `audit_logging`), and blocks unsafe URL schemes/internal IPs.
- **Bounded Retries, Fallback, Rollback & Safe-Stop**: Transient agent errors trigger bounded retries; exhausted retries fall back to deterministic templates; policy violations immediately roll back pre-stage state snapshots and place the workflow in `HALTED_SAFE_STOP`.
- **Dynamic Re-Planning**: `replan_from_updated_requirement()` increments workflow version, records a `REPLAN` audit event, and re-evaluates downstream stages when upstream requirements shift.
- **Observability & Reliability Metrics**: Tracks `success_rate`, `retry_count`, `fallback_count`, `rollback_count`, `replan_count`, `mttr_ms`, and `end_to_end_latency_ms` alongside an append-only `AuditEvent` log and `DecisionRecord` lineage.

## 3. Scenario Coverage
- **Greenfield (`ScenarioType.GREENFIELD`)**: Builds the URL shortener from scratch with explicit human checkpoints at architecture design and release readiness.
- **Brownfield (`ScenarioType.BROWNFIELD`)**: Runs `codebase_impact_analysis` to map backward-compatible schema/API changes for adding custom aliases, TTLs, and analytics to an existing v1 shortener, plus demonstrates transient retry recovery and dynamic re-planning.
- **Ambiguous (`ScenarioType.AMBIGUOUS`)**: Identifies underspecified requirements ("fast, scalable link shortener") and normalizes them into concrete SLOs, Base62 collision handling, HTTP 410 expiry semantics, and token-bucket rate limits.

## 4. Trade-Offs, Assumptions & Limitations
- **Storage Engine**: Uses a thread-safe in-memory repository with an LRU cache layer so the prototype runs with zero external infrastructure dependencies. In multi-node production, `UrlRepository` maps directly to PostgreSQL/DynamoDB + Redis.
- **Redirect Status Code (`302` vs `301`)**: Uses `HTTP 302` so browsers do not permanently cache redirects, ensuring click analytics stay accurate and link revocations/TTLs take effect immediately.
- **Simulated Agent Execution**: SDLC agents execute deterministic domain logic rather than live external LLM API calls so tests, CI gates, and offline evaluations are 100% reproducible.
