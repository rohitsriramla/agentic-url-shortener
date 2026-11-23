"""Non-linear, stateful DAG Workflow Orchestrator with governance, gates, retries, rollback, and dynamic re-planning."""
from __future__ import annotations

import copy
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Set

from src.agents.sdlc_agents import AgentRegistry, TransientAgentError
from src.orchestrator.models import (
    AuditEvent,
    DecisionRecord,
    GateFailureError,
    PolicyViolationError,
    ScenarioType,
    StageName,
    StageNode,
    StageStatus,
    WorkflowContext,
)
from src.policy.guardrails import PolicyGuardrails


class SDLCOrchestrator:
    """Executes an explicit SDLC DAG with entry/exit gates, parallel sync, human checkpoints, rollback, and re-planning."""

    def __init__(
        self,
        agent_registry: Optional[AgentRegistry] = None,
        auto_approve_human_gates: bool = False,
        approver_name: str = "staff-engineer-reviewer",
    ):
        self.agents = agent_registry or AgentRegistry()
        self.auto_approve_human_gates = auto_approve_human_gates
        self.approver_name = approver_name
        self.stages: Dict[StageName, StageNode] = self._build_dag()

    @staticmethod
    def _build_dag() -> Dict[StageName, StageNode]:
        return {
            StageName.REQUIREMENT_INTAKE: StageNode(
                name=StageName.REQUIREMENT_INTAKE,
                dependencies=[],
            ),
            StageName.CODEBASE_IMPACT_ANALYSIS: StageNode(
                name=StageName.CODEBASE_IMPACT_ANALYSIS,
                dependencies=[StageName.REQUIREMENT_INTAKE],
                parallel_group="analysis_wave",
            ),
            StageName.TASK_DECOMPOSITION: StageNode(
                name=StageName.TASK_DECOMPOSITION,
                dependencies=[StageName.REQUIREMENT_INTAKE],
                parallel_group="analysis_wave",
            ),
            StageName.ARCHITECTURE_DESIGN: StageNode(
                name=StageName.ARCHITECTURE_DESIGN,
                dependencies=[StageName.CODEBASE_IMPACT_ANALYSIS, StageName.TASK_DECOMPOSITION],
            ),
            StageName.HUMAN_DESIGN_GATE: StageNode(
                name=StageName.HUMAN_DESIGN_GATE,
                dependencies=[StageName.ARCHITECTURE_DESIGN],
                requires_human_approval=True,
            ),
            StageName.IMPLEMENTATION: StageNode(
                name=StageName.IMPLEMENTATION,
                dependencies=[StageName.HUMAN_DESIGN_GATE],
                max_retries=2,
            ),
            StageName.SECURITY_POLICY_AUDIT: StageNode(
                name=StageName.SECURITY_POLICY_AUDIT,
                dependencies=[StageName.IMPLEMENTATION],
                parallel_group="verification_wave",
            ),
            StageName.TESTING_VALIDATION: StageNode(
                name=StageName.TESTING_VALIDATION,
                dependencies=[StageName.IMPLEMENTATION],
                parallel_group="verification_wave",
            ),
            StageName.DOCUMENTATION: StageNode(
                name=StageName.DOCUMENTATION,
                dependencies=[StageName.IMPLEMENTATION],
                parallel_group="verification_wave",
                critical_for_release=False,
            ),
            StageName.RELEASE_READINESS_GATE: StageNode(
                name=StageName.RELEASE_READINESS_GATE,
                dependencies=[
                    StageName.SECURITY_POLICY_AUDIT,
                    StageName.TESTING_VALIDATION,
                    StageName.DOCUMENTATION,
                ],
                requires_human_approval=True,
            ),
        }

    def create_context(self, scenario_type: ScenarioType, raw_requirement: str) -> WorkflowContext:
        return WorkflowContext(
            run_id=f"run-{uuid.uuid4().hex[:8]}",
            scenario_type=scenario_type,
            raw_requirement=raw_requirement,
        )

    def _log(self, ctx: WorkflowContext, stage: str, event_type: str, status: str, details: Optional[Dict[str, Any]] = None) -> None:
        ctx.audit_log.append(
            AuditEvent(
                run_id=ctx.run_id,
                stage=stage,
                event_type=event_type,
                status=status,
                details=details or {},
            )
        )

    def _check_entry_gate(self, ctx: WorkflowContext, node: StageNode) -> None:
        if ctx.safe_stopped:
            raise GateFailureError(f"Workflow is in SAFE_STOP state: {ctx.safe_stop_reason}")
        unmet = [dep.value for dep in node.dependencies if self.stages[dep].status != StageStatus.COMPLETED]
        if unmet:
            raise GateFailureError(f"Entry gate blocked for {node.name.value}; unmet dependencies: {unmet}")
        self._log(ctx, node.name.value, "ENTRY_GATE", "PASSED", {"dependencies": [d.value for d in node.dependencies]})

    def _check_exit_gate(self, ctx: WorkflowContext, node: StageNode, output: Dict[str, Any]) -> None:
        if not output:
            raise GateFailureError(f"Exit gate failed for {node.name.value}: empty output")
        PolicyGuardrails.enforce_stage_policy(node.name.value, output)
        self._log(ctx, node.name.value, "POLICY_AND_EXIT_GATE", "PASSED")

    def _snapshot_context(self, ctx: WorkflowContext) -> Dict[str, Any]:
        return copy.deepcopy(
            {
                "normalized_spec": ctx.normalized_spec,
                "codebase_impact": ctx.codebase_impact,
                "task_graph": ctx.task_graph,
                "architecture": ctx.architecture,
                "generated_artifacts": ctx.generated_artifacts,
                "security_report": ctx.security_report,
                "test_report": ctx.test_report,
                "documentation": ctx.documentation,
            }
        )

    def rollback_stage(self, ctx: WorkflowContext, stage_name: StageName, reason: str) -> None:
        """Restores pre-stage context snapshot and marks stage ROLLED_BACK."""
        node = self.stages[stage_name]
        if node.snapshot_before_exec:
            snap = node.snapshot_before_exec
            ctx.normalized_spec = snap["normalized_spec"]
            ctx.codebase_impact = snap["codebase_impact"]
            ctx.task_graph = snap["task_graph"]
            ctx.architecture = snap["architecture"]
            ctx.generated_artifacts = snap["generated_artifacts"]
            ctx.security_report = snap["security_report"]
            ctx.test_report = snap["test_report"]
            ctx.documentation = snap["documentation"]
        node.status = StageStatus.ROLLED_BACK
        node.output = {}
        ctx.metrics.rollback_count += 1
        self._log(ctx, stage_name.value, "ROLLBACK", "COMPLETED", {"reason": reason})

    def replan_from_updated_requirement(self, ctx: WorkflowContext, updated_requirement: str) -> None:
        """Dynamically invalidates downstream stages when upstream requirements change."""
        ctx.raw_requirement = updated_requirement
        ctx.version += 1
        ctx.metrics.replan_count += 1
        for stage in self.stages.values():
            stage.status = StageStatus.PENDING
            stage.attempts = 0
            stage.output = {}
        self._log(
            ctx,
            "orchestrator",
            "REPLAN",
            "TRIGGERED",
            {"new_version": ctx.version, "updated_requirement": updated_requirement},
        )

    def approve_human_gate(self, ctx: WorkflowContext, stage_name: StageName, approved: bool, reviewer: str, notes: str = "") -> None:
        node = self.stages[stage_name]
        if not node.requires_human_approval:
            raise ValueError(f"Stage {stage_name.value} is not a human approval gate")
        if not approved:
            ctx.safe_stopped = True
            ctx.safe_stop_reason = f"Human gate '{stage_name.value}' rejected by {reviewer}: {notes}"
            node.status = StageStatus.HALTED_SAFE_STOP
            self._log(ctx, stage_name.value, "HUMAN_GATE", "REJECTED_SAFE_STOP", {"reviewer": reviewer, "notes": notes})
            return

        node.status = StageStatus.COMPLETED
        node.output = {"approved": True, "reviewer": reviewer, "notes": notes}
        ctx.decision_lineage.append(
            DecisionRecord(
                stage=stage_name.value,
                decision=f"Approved checkpoint {stage_name.value}",
                rationale=notes or "Meets architecture, security, and release criteria.",
                approved_by=reviewer,
            )
        )
        self._log(ctx, stage_name.value, "HUMAN_GATE", "APPROVED", {"reviewer": reviewer, "notes": notes})

    def _execute_single_stage(self, ctx: WorkflowContext, stage_name: StageName) -> StageStatus:
        node = self.stages[stage_name]
        if node.status == StageStatus.COMPLETED:
            return node.status

        self._check_entry_gate(ctx, node)

        if node.requires_human_approval:
            if self.auto_approve_human_gates:
                self.approve_human_gate(ctx, stage_name, True, self.approver_name, "Auto-approved in autonomous demo mode")
                ctx.metrics.total_stages_executed += 1
                ctx.metrics.successful_stages += 1
                return node.status
            node.status = StageStatus.AWAITING_APPROVAL
            self._log(ctx, stage_name.value, "HUMAN_GATE", "AWAITING_APPROVAL")
            return node.status

        node.snapshot_before_exec = self._snapshot_context(ctx)
        node.status = StageStatus.RUNNING

        dispatch: Dict[StageName, Callable[..., Dict[str, Any]]] = {
            StageName.REQUIREMENT_INTAKE: lambda: self.agents.run_requirement_intake(ctx),
            StageName.CODEBASE_IMPACT_ANALYSIS: lambda: self.agents.run_codebase_impact_analysis(ctx),
            StageName.TASK_DECOMPOSITION: lambda: self.agents.run_task_decomposition(ctx),
            StageName.ARCHITECTURE_DESIGN: lambda: self.agents.run_architecture_design(ctx),
            StageName.IMPLEMENTATION: lambda: self.agents.run_implementation(ctx, fallback_mode=False),
            StageName.SECURITY_POLICY_AUDIT: lambda: self.agents.run_security_policy_audit(ctx),
            StageName.TESTING_VALIDATION: lambda: self.agents.run_testing_validation(ctx),
            StageName.DOCUMENTATION: lambda: self.agents.run_documentation(ctx),
        }

        last_err: Optional[Exception] = None
        recovery_start: Optional[float] = None

        while node.attempts <= node.max_retries:
            node.attempts += 1
            ctx.metrics.total_stages_executed += 1
            try:
                output = dispatch[stage_name]()
                self._check_exit_gate(ctx, node, output)
                node.output = output
                node.status = StageStatus.COMPLETED
                ctx.metrics.successful_stages += 1
                if recovery_start is not None:
                    ctx.metrics.recovery_durations_ms.append((time.perf_counter() - recovery_start) * 1000)
                self._log(ctx, stage_name.value, "AGENT_EXEC", "COMPLETED", {"attempt": node.attempts})
                return node.status
            except PolicyViolationError as exc:
                ctx.metrics.failed_stages += 1
                self.rollback_stage(ctx, stage_name, f"Policy violation: {exc}")
                ctx.safe_stopped = True
                ctx.safe_stop_reason = f"Policy violation in {stage_name.value}: {exc}"
                node.status = StageStatus.HALTED_SAFE_STOP
                self._log(ctx, stage_name.value, "SAFE_STOP", "TRIGGERED", {"error": str(exc)})
                return node.status
            except TransientAgentError as exc:
                last_err = exc
                ctx.metrics.failed_stages += 1
                if recovery_start is None:
                    recovery_start = time.perf_counter()
                if node.attempts <= node.max_retries:
                    ctx.metrics.retry_count += 1
                    self._log(ctx, stage_name.value, "RETRY", "SCHEDULED", {"attempt": node.attempts, "error": str(exc)})
                else:
                    # Bounded retries exhausted -> attempt deterministic fallback if supported
                    if stage_name == StageName.IMPLEMENTATION:
                        ctx.metrics.fallback_count += 1
                        self._log(ctx, stage_name.value, "FALLBACK", "INVOKED", {"reason": str(exc)})
                        output = self.agents.run_implementation(ctx, fallback_mode=True)
                        self._check_exit_gate(ctx, node, output)
                        node.output = output
                        node.status = StageStatus.COMPLETED
                        ctx.metrics.successful_stages += 1
                        ctx.metrics.recovery_durations_ms.append((time.perf_counter() - recovery_start) * 1000)
                        return node.status

        # Unrecoverable failure -> rollback + safe stop
        self.rollback_stage(ctx, stage_name, f"Exhausted retries: {last_err}")
        ctx.safe_stopped = True
        ctx.safe_stop_reason = f"Unrecoverable failure in {stage_name.value}: {last_err}"
        node.status = StageStatus.HALTED_SAFE_STOP
        return node.status

    def run(self, ctx: WorkflowContext) -> WorkflowContext:
        """Executes ready DAG waves (sequential and parallel) until completion, human gate pause, or safe-stop."""
        start_time = time.perf_counter()
        while not ctx.safe_stopped:
            ready: List[StageNode] = [
                node
                for node in self.stages.values()
                if node.status == StageStatus.PENDING
                and all(self.stages[dep].status == StageStatus.COMPLETED for dep in node.dependencies)
            ]
            if not ready:
                break

            # Execute parallelizable ready nodes concurrently, human gates synchronously
            human_nodes = [n for n in ready if n.requires_human_approval]
            worker_nodes = [n for n in ready if not n.requires_human_approval]

            if worker_nodes:
                if len(worker_nodes) == 1:
                    self._execute_single_stage(ctx, worker_nodes[0].name)
                else:
                    self._log(
                        ctx,
                        "orchestrator",
                        "PARALLEL_WAVE",
                        "STARTED",
                        {"stages": [n.name.value for n in worker_nodes]},
                    )
                    with ThreadPoolExecutor(max_workers=len(worker_nodes)) as pool:
                        futures = [pool.submit(self._execute_single_stage, ctx, n.name) for n in worker_nodes]
                        for fut in futures:
                            fut.result()

            for h_node in human_nodes:
                status = self._execute_single_stage(ctx, h_node.name)
                if status == StageStatus.AWAITING_APPROVAL:
                    ctx.metrics.end_to_end_latency_ms += (time.perf_counter() - start_time) * 1000
                    return ctx

        ctx.metrics.end_to_end_latency_ms += (time.perf_counter() - start_time) * 1000
        return ctx
