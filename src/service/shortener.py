"""Core URL Shortener Domain Service with Base62 encoding, SSRF guardrails, TTL, and analytics."""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from src.policy.guardrails import PolicyGuardrails
from src.service.storage import ShortUrlRecord, UrlRepository

BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
ALIAS_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{3,32}$")
RESERVED_CODES = {"api", "health", "docs", "openapi.json", "redoc", "metrics"}


class InvalidUrlError(ValueError):
    """Raised when target URL violates format or SSRF policy."""


class AliasConflictError(ValueError):
    """Raised when requested custom alias is already in use or reserved."""


class UrlNotFoundError(KeyError):
    """Raised when short code does not exist."""


class UrlGoneError(RuntimeError):
    """Raised when short URL is expired or revoked (HTTP 410)."""


class ShortenerService:
    """Implements URL shortening, resolution, analytics, and lifecycle controls."""

    def __init__(self, repository: Optional[UrlRepository] = None, code_length: int = 7):
        self.repo = repository or UrlRepository()
        self.code_length = code_length

    def _generate_code(self) -> str:
        return "".join(secrets.choice(BASE62_ALPHABET) for _ in range(self.code_length))

    def create_short_url(
        self,
        target_url: str,
        custom_alias: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> ShortUrlRecord:
        allowed, reason = PolicyGuardrails.validate_target_url(target_url)
        if not allowed:
            raise InvalidUrlError(reason)

        if custom_alias is not None:
            alias = custom_alias.strip()
            if alias.lower() in RESERVED_CODES or not ALIAS_PATTERN.match(alias):
                raise AliasConflictError("Custom alias must be 3-32 alphanumeric/dash/underscore chars and not reserved")
            if self.repo.exists(alias):
                raise AliasConflictError(f"Custom alias '{alias}' is already in use")
            code = alias
            is_custom = True
        else:
            code = ""
            for _ in range(10):
                candidate = self._generate_code()
                if candidate.lower() not in RESERVED_CODES and not self.repo.exists(candidate):
                    code = candidate
                    break
            if not code:
                raise RuntimeError("Failed to allocate unique short code after bounded retries")
            is_custom = False

        expires_at: Optional[str] = None
        if ttl_seconds is not None:
            if ttl_seconds <= 0:
                raise InvalidUrlError("ttl_seconds must be greater than 0")
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()

        record = ShortUrlRecord(
            code=code,
            target_url=target_url.strip(),
            expires_at=expires_at,
            is_custom_alias=is_custom,
        )
        return self.repo.save(record)

    def resolve_url(self, code: str, referrer: str = "direct", user_agent: str = "unknown") -> str:
        record = self.repo.get(code)
        if record is None:
            raise UrlNotFoundError(f"Short code '{code}' not found")
        if record.revoked:
            raise UrlGoneError(f"Short code '{code}' has been revoked")
        if record.is_expired():
            raise UrlGoneError(f"Short code '{code}' has expired")

        self.repo.record_click(code, referrer=referrer, user_agent=user_agent)
        return record.target_url

    def get_analytics(self, code: str) -> Dict[str, Any]:
        record = self.repo.get(code)
        if record is None:
            raise UrlNotFoundError(f"Short code '{code}' not found")
        status = "revoked" if record.revoked else ("expired" if record.is_expired() else "active")
        return {
            "code": record.code,
            "target_url": record.target_url,
            "status": status,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "is_custom_alias": record.is_custom_alias,
            "click_count": record.click_count,
            "referrer_breakdown": record.referrer_breakdown,
            "recent_clicks": [c.model_dump() for c in record.recent_clicks[-10:]],
        }

    def revoke_url(self, code: str) -> None:
        if not self.repo.revoke(code):
            raise UrlNotFoundError(f"Short code '{code}' not found")
