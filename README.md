# Agentic Software Engineering System — URL Shortener

A runnable prototype demonstrating **end-to-end SDLC automation with controlled autonomy**, pairing a stateful DAG workflow orchestrator with a production-grade FastAPI URL Shortener service.

## Project Structure
```text
agentic-url-shortener/
├── docs/
│   └── ARCHITECTURE.md          # Architecture, DAG control flow, trade-offs, and summary
├── scenarios/
│   └── run_scenarios.py         # Executable Greenfield, Brownfield, and Ambiguous scenarios
├── src/
│   ├── agents/
│   │   └── sdlc_agents.py       # Requirement, Impact, Decomposition, Arch, Impl, Security, Test, Docs agents
│   ├── orchestrator/
│   │   ├── engine.py            # Non-linear DAG engine with gates, retries, fallback, rollback, replanning
│   │   └── models.py            # State, DecisionRecord, AuditEvent, and ReliabilityMetrics models
│   ├── policy/
│   │   └── guardrails.py        # SSRF protection, secret scanning, and change-control policies
│   └── service/
│       ├── api.py               # FastAPI REST endpoints + Orchestrator trigger endpoint
│       ├── rate_limiter.py      # Thread-safe Token-Bucket rate limiter
│       ├── shortener.py         # Base62 shortener, custom aliases, TTL expiration, analytics
│       └── storage.py           # Thread-safe Repository with LRU cache-aside layer
├── tests/
│   └── test_orchestrator_and_service.py
└── requirements.txt
```

## Setup & Execution

1. **Install dependencies:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Run automated unit & integration tests:**
   ```bash
   pytest -v
   ```

3. **Execute the three SDLC scenarios (Greenfield, Brownfield, Ambiguous):**
   ```bash
   python scenarios/run_scenarios.py
   ```

4. **Start the live URL Shortener & Orchestrator API server:**
   ```bash
   uvicorn src.service.api:app --reload
   ```
   - Interactive API docs: `http://127.0.0.1:8000/docs`
   - Create short URL: `POST /api/v1/urls`
   - Redirect: `GET /{code}`
   - Analytics: `GET /api/v1/urls/{code}/analytics`
   - Revoke URL: `DELETE /api/v1/urls/{code}`
   - Trigger SDLC Orchestrator run: `POST /api/v1/orchestrator/run`
