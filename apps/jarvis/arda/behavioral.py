"""M2.4D2 deterministic baseline-vs-patched behavioral verification."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol

from arda.execution import ExecutionRequest, ExecutionResult, OutputLimits, ResourceLimits
from arda.patch_worker import PatchResult
from arda.verification import StructuralEvaluation, VerificationStatus


class BehavioralStatus(str, Enum):
    BEHAVIORAL_PASS = "BEHAVIORAL_PASS"
    BEHAVIORAL_FAIL = "BEHAVIORAL_FAIL"
    REGRESSION = "REGRESSION"
    INCONCLUSIVE = "INCONCLUSIVE"
    EXECUTION_ERROR = "EXECUTION_ERROR"


@dataclass(frozen=True)
class BehavioralTest:
    name: str
    argv: tuple[str, ...]
    cwd: str = "/workspace/source"
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class TestPlan:
    targeted_tests: tuple[BehavioralTest, ...] = ()
    regression_tests: tuple[BehavioralTest, ...] = ()
    expected_fixed_tests: tuple[str, ...] = ()
    expected_behavior_changed: bool = True
    environment_allowlist: Mapping[str, str] = field(default_factory=dict)
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    output_limits: OutputLimits = field(default_factory=OutputLimits)

    def canonical_payload(self) -> dict[str, Any]:
        def serialize(test: BehavioralTest) -> dict[str, Any]:
            return {"name": test.name, "argv": list(test.argv), "cwd": test.cwd, "timeout_seconds": test.timeout_seconds}

        return {
            "targeted_tests": [serialize(test) for test in self.targeted_tests],
            "regression_tests": [serialize(test) for test in self.regression_tests],
            "expected_fixed_tests": list(self.expected_fixed_tests),
            "expected_behavior_changed": self.expected_behavior_changed,
            "environment_allowlist": dict(sorted(self.environment_allowlist.items())),
            "resource_limits": self.resource_limits.__dict__,
            "output_limits": self.output_limits.__dict__,
        }

    def hash(self) -> str:
        payload = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TestOutcome:
    test_name: str
    phase: str
    status: str
    exit_code: int | None
    stdout_sha256: str
    stderr_sha256: str
    duration_seconds: float
    termination_reason: str | None
    workspace_tree_hash: str


@dataclass(frozen=True)
class BehavioralEvaluation:
    episode_id: str
    patch_result_id: str
    structural_evaluation_id: str
    status: BehavioralStatus
    test_plan_hash: str
    runtime_snapshot_hash: str
    baseline_tree_hash: str
    patched_tree_hash: str
    baseline_outcomes: tuple[TestOutcome, ...]
    patched_outcomes: tuple[TestOutcome, ...]
    intended_behavior_changed: bool
    fixed_failure_count: int
    new_failure_count: int
    regression_count: int
    evaluator_version: str = "behavioral-verifier-v0.1"
    failure_class: str | None = None


class BehavioralRuntime(Protocol):
    capabilities: Any

    def run(self, request: ExecutionRequest) -> ExecutionResult: ...


class BehavioralVerifier:
    """Run the exact same bounded test plan against two immutable snapshots."""

    def verify(
        self,
        *,
        episode_id: str,
        patch_result_id: str,
        structural_evaluation_id: str,
        structural_evaluation: StructuralEvaluation,
        patch_result: PatchResult,
        baseline_workspace: str | Path,
        patched_workspace: str | Path,
        test_plan: TestPlan,
        runtime: BehavioralRuntime,
    ) -> BehavioralEvaluation:
        baseline = Path(baseline_workspace).resolve()
        patched = Path(patched_workspace).resolve()
        base_hash = self._tree_hash(baseline)
        patched_hash = self._tree_hash(patched)
        runtime_hash = self._runtime_hash(runtime)
        tests = (("targeted", test_plan.targeted_tests), ("regression", test_plan.regression_tests))

        if structural_evaluation.status is not VerificationStatus.STRUCTURAL_PASS:
            return self._evaluation(
                episode_id, patch_result_id, structural_evaluation_id, BehavioralStatus.EXECUTION_ERROR,
                test_plan, runtime_hash, base_hash, patched_hash, (), (), False, 0, 0, 0, "structural_gate_not_passed",
            )

        baseline_outcomes: list[TestOutcome] = []
        patched_outcomes: list[TestOutcome] = []
        for phase, phase_tests in tests:
            for test in phase_tests:
                baseline_outcomes.append(self._run_one(runtime, baseline, test_plan, phase, test))
                patched_outcomes.append(self._run_one(runtime, patched, test_plan, phase, test))

        by_name = {outcome.test_name: outcome for outcome in baseline_outcomes}
        patched_by_name = {outcome.test_name: outcome for outcome in patched_outcomes}
        fixed = sum(by_name[name].status == "failed" and patched_by_name.get(name, by_name[name]).status == "passed" for name in test_plan.expected_fixed_tests if name in by_name and name in patched_by_name)
        regressions = sum(
            outcome.status == "passed" and patched_by_name.get(outcome.test_name, outcome).status != "passed"
            for outcome in baseline_outcomes
        )
        new_failures = sum(
            outcome.status != "passed" and patched_by_name.get(outcome.test_name, outcome).status == "failed"
            for outcome in patched_outcomes
            if outcome.test_name not in by_name
        )
        execution_error = any(outcome.status == "execution_error" for outcome in (*baseline_outcomes, *patched_outcomes))
        if execution_error:
            status, failure = BehavioralStatus.EXECUTION_ERROR, "test_execution_error"
        elif regressions:
            status, failure = BehavioralStatus.REGRESSION, "regression"
        elif test_plan.expected_behavior_changed and fixed < len(test_plan.expected_fixed_tests):
            status, failure = BehavioralStatus.INCONCLUSIVE, "expected_behavior_not_observed"
        elif not test_plan.expected_behavior_changed and fixed:
            status, failure = BehavioralStatus.INCONCLUSIVE, "unexpected_behavior_change"
        elif test_plan.expected_behavior_changed and not test_plan.expected_fixed_tests:
            status, failure = BehavioralStatus.INCONCLUSIVE, "no_expected_fixed_tests"
        elif any(outcome.status == "failed" for outcome in patched_outcomes):
            status, failure = BehavioralStatus.BEHAVIORAL_FAIL, "patched_test_failure"
        else:
            status, failure = BehavioralStatus.BEHAVIORAL_PASS, None
        return self._evaluation(
            episode_id, patch_result_id, structural_evaluation_id, status, test_plan, runtime_hash,
            base_hash, patched_hash, tuple(baseline_outcomes), tuple(patched_outcomes),
            test_plan.expected_behavior_changed, fixed, new_failures, regressions, failure,
        )

    def _run_one(self, runtime, workspace, plan, phase, test):
        before = self._tree_hash(workspace)
        try:
            result = runtime.run(ExecutionRequest(
                execution_id=f"behavior-{phase}-{test.name}",
                repository_snapshot=workspace,
                argv=test.argv,
                cwd=test.cwd,
                environment_allowlist=plan.environment_allowlist,
                network_policy="none",
                resource_limits=plan.resource_limits,
                timeout_seconds=test.timeout_seconds,
                output_limits=plan.output_limits,
            ))
        except Exception:
            return TestOutcome(test.name, phase, "execution_error", None, "", "", 0.0, "runtime_exception", before)
        after = self._tree_hash(workspace)
        if before != after:
            return TestOutcome(test.name, phase, "execution_error", result.exit_code, self._hash(result.stdout), self._hash(result.stderr), result.duration_seconds, "workspace_contamination", after)
        status = "passed" if result.status == "succeeded" and result.exit_code == 0 else "failed"
        if result.termination_reason:
            status = "execution_error"
        return TestOutcome(test.name, phase, status, result.exit_code, self._hash(result.stdout), self._hash(result.stderr), result.duration_seconds, result.termination_reason, after)

    @classmethod
    def _evaluation(cls, episode_id, patch_id, structural_id, status, plan, runtime_hash, base_hash, patched_hash, baseline, patched, intended, fixed, new, regressions, failure):
        return BehavioralEvaluation(
            episode_id=episode_id,
            patch_result_id=patch_id,
            structural_evaluation_id=structural_id,
            status=status,
            test_plan_hash=plan.hash(),
            runtime_snapshot_hash=runtime_hash,
            baseline_tree_hash=base_hash,
            patched_tree_hash=patched_hash,
            baseline_outcomes=tuple(baseline),
            patched_outcomes=tuple(patched),
            intended_behavior_changed=intended,
            fixed_failure_count=fixed,
            new_failure_count=new,
            regression_count=regressions,
            failure_class=failure,
        )

    @staticmethod
    def _runtime_hash(runtime) -> str:
        capabilities = getattr(runtime, "capabilities", {})
        payload = capabilities.as_dict() if hasattr(capabilities, "as_dict") else dict(capabilities)
        payload["image"] = getattr(runtime, "image", None)
        return BehavioralVerifier._hash(json.dumps(payload, sort_keys=True, default=str))

    @staticmethod
    def _tree_hash(path: Path) -> str:
        return PatchWorkerTreeHash.hash(path)

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PatchWorkerTreeHash:
    @staticmethod
    def hash(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()
