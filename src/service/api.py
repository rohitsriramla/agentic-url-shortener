"""FastAPI application exposing the URL Shortener service and SDLC Orchestrator endpoints."""
from __future__ import annotations

from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from src.orchestrator.engine import SDLCOrchestrator
from src.orchestrator.models import ScenarioType
from src.service.rate_limiter import TokenBucketRateLimiter
from src.service.shortener import (
    AliasConflictError,
    InvalidUrlError,
    ShortenerService,
    UrlGoneError,
    UrlNotFoundError,
)


class CreateUrlRequest(BaseModel):
    target_url: str = Field(..., description="Destination HTTP/HTTPS URL")
    custom_alias: Optional[str] = Field(default=None, description="Optional custom short slug")
    ttl_seconds: Optional[int] = Field(default=None, description="Optional expiration TTL in seconds")


class CreateUrlResponse(BaseModel):
    code: str
    short_url: str
    target_url: str
    expires_at: Optional[str] = None
    is_custom_alias: bool


class OrchestrateRequest(BaseModel):
    scenario_type: ScenarioType = ScenarioType.GREENFIELD
    requirement: str
    auto_approve: bool = True


def create_app(
    service: Optional[ShortenerService] = None,
    rate_limiter: Optional[TokenBucketRateLimiter] = None,
) -> FastAPI:
    app = FastAPI(
        title="Agentic SDLC URL Shortener System",
        version="1.0.0",
        description="Production-grade URL Shortener built and governed by a stateful DAG SDLC Orchestrator.",
    )
    shortener = service or ShortenerService()
    limiter = rate_limiter or TokenBucketRateLimiter(capacity=60, refill_rate_per_sec=1.0)
    app.state.shortener = shortener
    app.state.rate_limiter = limiter

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "healthy",
            "storage": shortener.repo.stats(),
        }

    @app.post("/api/v1/urls", response_model=CreateUrlResponse, status_code=201)
    def create_short_url(
        payload: CreateUrlRequest,
        request: Request,
        x_client_id: Optional[str] = Header(default=None),
    ) -> CreateUrlResponse:
        client_key = x_client_id or (request.client.host if request.client else "anonymous")
        if not limiter.allow(client_key):
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")

        try:
            record = shortener.create_short_url(
                target_url=payload.target_url,
                custom_alias=payload.custom_alias,
                ttl_seconds=payload.ttl_seconds,
            )
        except InvalidUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AliasConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        base = str(request.base_url).rstrip("/")
        return CreateUrlResponse(
            code=record.code,
            short_url=f"{base}/{record.code}",
            target_url=record.target_url,
            expires_at=record.expires_at,
            is_custom_alias=record.is_custom_alias,
        )

    @app.get("/api/v1/urls/{code}/analytics")
    def get_analytics(code: str) -> dict:
        try:
            return shortener.get_analytics(code)
        except UrlNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/v1/urls/{code}", status_code=204)
    def revoke_url(code: str) -> None:
        try:
            shortener.revoke_url(code)
        except UrlNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/orchestrator/run")
    def run_orchestrator(payload: OrchestrateRequest) -> dict:
        orch = SDLCOrchestrator(auto_approve_human_gates=payload.auto_approve)
        ctx = orch.create_context(payload.scenario_type, payload.requirement)
        orch.run(ctx)
        return {
            "run_id": ctx.run_id,
            "scenario_type": ctx.scenario_type.value,
            "safe_stopped": ctx.safe_stopped,
            "metrics": ctx.metrics.summary(),
            "decision_lineage": [d.model_dump() for d in ctx.decision_lineage],
            "normalized_spec": ctx.normalized_spec,
            "architecture": ctx.architecture,
            "security_report": ctx.security_report,
            "test_report": ctx.test_report,
        }

    @app.get("/{code}")
    def redirect_to_target(code: str, request: Request):
        try:
            target = shortener.resolve_url(
                code=code,
                referrer=request.headers.get("referer", "direct"),
                user_agent=request.headers.get("user-agent", "unknown"),
            )
            return RedirectResponse(url=target, status_code=302)
        except UrlNotFoundError as exc:
            return JSONResponse(status_code=404, content={"detail": str(exc)})
        except UrlGoneError as exc:
            return JSONResponse(status_code=410, content={"detail": str(exc)})

    return app


app = create_app()
