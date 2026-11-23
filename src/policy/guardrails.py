"""Policy guardrails for security, compliance, and change control."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple
from src.orchestrator.models import PolicyViolationError


FORBIDDEN_SECRET_PATTERNS = [
    r"AKIA[0-9A-Z]{16}",
    r"(?i)password\s*=\s*['\"][^'\"]+['\"]",
    r"(?i)api_key\s*=\s*['\"][^'\"]+['\"]",
    r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
]

DISALLOWED_URL_SCHEMES = ("javascript:", "data:", "file:", "vbscript:")


class PolicyGuardrails:
    """Enforces security, compliance, and change-control policies across SDLC stages."""

    @staticmethod
    def validate_target_url(url: str) -> Tuple[bool, str]:
        lowered = url.strip().lower()
        if any(lowered.startswith(scheme) for scheme in DISALLOWED_URL_SCHEMES):
            return False, f"Blocked unsafe URL scheme: {url}"
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            return False, "Only http:// and https:// schemes are permitted"
        if any(host in lowered for host in ("169.254.169.254", "localhost:", "127.0.0.1")):
            return False, "Blocked internal/metadata SSRF target"
        return True, "OK"

    @staticmethod
    def scan_artifacts_for_secrets(artifacts: Dict[str, Any]) -> List[str]:
        violations: List[str] = []
        serialized = str(artifacts)
        for pattern in FORBIDDEN_SECRET_PATTERNS:
            if re.search(pattern, serialized):
                violations.append(f"Detected potential hardcoded secret matching pattern: {pattern}")
        return violations

    @classmethod
    def enforce_stage_policy(cls, stage_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validates stage outputs against compliance and security rules."""
        violations = cls.scan_artifacts_for_secrets(payload)

        if stage_name == "architecture_design":
            controls = payload.get("security_controls", [])
            required = {"rate_limiting", "ssrf_validation", "audit_logging"}
            missing = required - set(controls)
            if missing:
                violations.append(f"Architecture missing mandatory security controls: {sorted(missing)}")

        if stage_name == "implementation":
            if not payload.get("rate_limit_enabled", False):
                violations.append("Change-control policy requires rate limiting enabled on public redirect/shorten endpoints")
            if not payload.get("ssrf_protection_enabled", False):
                violations.append("Security policy requires SSRF & URL scheme validation")

        if violations:
            raise PolicyViolationError("; ".join(violations))

        return {"passed": True, "checked_stage": stage_name, "violations": []}
