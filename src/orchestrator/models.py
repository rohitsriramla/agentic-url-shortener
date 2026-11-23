"""Core domain models for the stateful, DAG-based SDLC Agentic Orchestrator."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field


class ScenarioType(str, Enum):
    GREENFIELD = "greenfield"
    BROWNFIELD = "brownfield"
    AMBIGUOUS = "ambiguous"


class StageName(str, Enum):
    REQUIREMENT_INTAKE = "requirement_intake"
    CODEBASE_IMPACT_ANALYSIS = "codebase_impact_analysis"
    TASK_DECOMPOSITION = "task_decomposition"
    ARCHITECTURE_DESIGN = "architecture_design"
    HUMAN_DESIGN_GATE = "human_design_gate"
    IMPLEMENTATION = "implementation"
    SECURITY_POLICY_AUDIT = "security_policy_audit"
    TESTING_VALIDATION = "testing_validation"
    DOCUMENTATION = "documentation"
    RELEASE_READINESS_GATE = "release_readiness_gate"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    HALTED_SAFE_STOP = "halted_safe_stop"


class PolicyViolationError(Exception):
    """Raised when an agent output violates security, compliance, or change-control policy."""


class GateFailureError(Exception):
    """Raised when an entry or exit gate fails validation."""


class DecisionRecord(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    stage: str
    decision: str
    rationale: str
    trade_offs: List[str] = Field(default_factory=list)
    approved_by: Optional[str] = None


class AuditEvent(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    run_id: str
    stage: str
    event_type: str  # ENTRY_GATE, AGENT_EXEC, RETRY, FALLBACK, ROLLBACK, POLICY_CHECK, HUMAN_GATE, REPLAN, SAFE_STOP
    status: str
    details: Dict[str, Any] = Field(default_factory=dict)


class ReliabilityMetrics(BaseModel):
    total_stages_executed: int = 0
    successful_stages: int = 0
    failed_stages: int = 0
    retry_count: int = 0
    fallback_count: int = 0
    rollback_count: int = 0
    replan_count: int = 0
    recovery_durations_ms: List[float] = Field(default_factory=list)
    end_to_end_latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_stages_executed == 0:
            return 1.0
        return round(self.successful_stages / self.total_stages_executed, 4)

    @property
    def mttr_ms(self) -> float:
        if not self.recovery_durations_ms:
            return 0.0
        return round(sum(self.recovery_durations_ms) / len(self.recovery_durations_ms), 2)

    def summary(self) -> Dict[str, Any]:
        return {
            "total_stages_executed": self.total_stages_executed,
            "successful_stages": self.successful_stages,
            "failed_stages": self.failed_stages,
            "success_rate": self.success_rate,
            "retry_count": self.retry_count,
            "fallback_count": self.fallback_count,
            "rollback_count": self.rollback_count,
            "replan_count": self.replan_count,
            "mttr_ms": self.mttr_ms,
            "end_to_end_latency_ms": round(self.end_to_end_latency_ms, 2),
        }


class StageNode(BaseModel):
    name: StageName
    dependencies: List[StageName] = Field(default_factory=list)
    parallel_group: Optional[str] = None
    requires_human_approval: bool = False
    max_retries: int = 2
    critical_for_release: bool = True
    status: StageStatus = StageStatus.PENDING
    attempts: int = 0
    output: Dict[str, Any] = Field(default_factory=dict)
    snapshot_before_exec: Optional[Dict[str, Any]] = None


class WorkflowContext(BaseModel):
    run_id: str
    scenario_type: ScenarioType
    raw_requirement: str
    normalized_spec: Dict[str, Any] = Field(default_factory=dict)
    codebase_impact: Dict[str, Any] = Field(default_factory=dict)
    task_graph: List[Dict[str, Any]] = Field(default_factory=list)
    architecture: Dict[str, Any] = Field(default_factory=dict)
    generated_artifacts: Dict[str, Any] = Field(default_factory=dict)
    security_report: Dict[str, Any] = Field(default_factory=dict)
    test_report: Dict[str, Any] = Field(default_factory=dict)
    documentation: Dict[str, Any] = Field(default_factory=dict)
    decision_lineage: List[DecisionRecord] = Field(default_factory=list)
    audit_log: List[AuditEvent] = Field(default_factory=list)
    metrics: ReliabilityMetrics = Field(default_factory=ReliabilityMetrics)
    safe_stopped: bool = False
    safe_stop_reason: Optional[str] = None
    version: int = 1
