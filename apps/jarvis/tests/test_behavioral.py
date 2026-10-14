"""Failure-first M2.4D2 baseline-vs-patched behavioral harness tests."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from arda.behavioral import (
    BehavioralStatus,
    BehavioralTest,
    BehavioralVerifier,
    TestPlan as BehavioralTestPlan,
)
from arda.execution import ExecutionResult, RuntimeCapabilities
from arda.patch_worker import AllowedFile, PatchEdit, PatchRequest, PatchWorker
from arda.verification import StructuralVerifier, VerificationRequest, VerificationStatus


IMAGE = "fixture@sha256:" + "a" * 64


class ScriptedRuntime:
    image = IMAGE

    def __init__(self, outcomes: dict[tuple[str, str], tuple[str, int, str, str]], mutate: bool = False):
        self.capabilities = RuntimeCapabilities(backend="fixture", backend_version="1")
        self.outcomes = outcomes
        self.mutate = mutate
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        workspace = request.repository_snapshot.name
        name = request.argv[-1]
        status, exit_code, stdout, stderr = self.outcomes[(workspace, name)]
        if self.mutate:
            (request.repository_snapshot / "contaminated.txt").write_text("unexpected", encoding="utf-8")
        return ExecutionResult(
            execution_id=request.execution_id,
            status=status,
            backend="fixture",
            backend_version="1",
            requested={},
            capabilities=self.capabilities.as_dict(),
            enforcement={"network": "enforced"},
            exit_code=exit_code,
            termination_reason=None,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=0.01,
            artifacts={},
            cleanup_ok=True,
        )


def make_case(tmp_path: Path):
    baseline = tmp_path / "baseline"
    patched = tmp_path / "patched"
    baseline.mkdir()
    (baseline / "module.py").write_text("def answer():\n    return 1\n", encoding="utf-8")
    shutil.copytree(baseline, patched)
    before = (baseline / "module.py").read_bytes()
    request = PatchRequest(
        episode_id="episode-1",
        patch_id="patch-1",
        explorer_run_id="run-1",
        base_tree_hash=PatchWorker._tree_hash(PatchWorker._snapshot(baseline)),
        allowed_files=(AllowedFile("module.py", PatchWorker._sha256(before)),),
        edits=(PatchEdit("replace_exact", "module.py", PatchWorker._sha256(before), "return 1", "return 2"),),
    )
    result = PatchWorker().apply(request, patched)
    structural = StructuralVerifier().verify(VerificationRequest(
        episode_id="episode-1",
        baseline_workspace=baseline,
        patched_workspace=patched,
        patch_result=result,
        allowed_files=("module.py",),
    ))
    assert structural.status is VerificationStatus.STRUCTURAL_PASS
    return baseline, patched, result, structural


def plan(*, expected_changed=True, expected_fixed=("target",), regression=("regression",)):
    return BehavioralTestPlan(
        targeted_tests=(BehavioralTest("target", ("fixture", "target")),),
        regression_tests=(BehavioralTest("regression", ("fixture", "regression")),),
        expected_fixed_tests=expected_fixed,
        expected_behavior_changed=expected_changed,
    )


def run_case(tmp_path, runtime, test_plan=None):
    baseline, patched, patch_result, structural = make_case(tmp_path)
    return BehavioralVerifier().verify(
        episode_id="episode-1",
        patch_result_id=patch_result.patch_id,
        structural_evaluation_id="structural-1",
        structural_evaluation=structural,
        patch_result=patch_result,
        baseline_workspace=baseline,
        patched_workspace=patched,
        test_plan=test_plan or plan(),
        runtime=runtime,
    )


def test_failure_becomes_pass_and_regressions_stay_green(tmp_path):
    runtime = ScriptedRuntime({
        ("baseline", "target"): ("failed", 1, "before", ""),
        ("patched", "target"): ("succeeded", 0, "after", ""),
        ("baseline", "regression"): ("succeeded", 0, "stable", ""),
        ("patched", "regression"): ("succeeded", 0, "stable", ""),
    })
    evaluation = run_case(tmp_path, runtime)
    assert evaluation.status is BehavioralStatus.BEHAVIORAL_PASS
    assert evaluation.fixed_failure_count == 1
    assert evaluation.regression_count == 0
    assert evaluation.new_failure_count == 0
    assert len(runtime.requests) == 4


def test_baseline_pass_to_patched_fail_is_regression(tmp_path):
    runtime = ScriptedRuntime({
        ("baseline", "target"): ("succeeded", 0, "before", ""),
        ("patched", "target"): ("failed", 1, "after", "broken"),
        ("baseline", "regression"): ("succeeded", 0, "stable", ""),
        ("patched", "regression"): ("succeeded", 0, "stable", ""),
    })
    evaluation = run_case(tmp_path, runtime)
    assert evaluation.status is BehavioralStatus.REGRESSION
    assert evaluation.regression_count == 1


def test_both_pass_without_observed_change_is_inconclusive(tmp_path):
    runtime = ScriptedRuntime({
        ("baseline", "target"): ("succeeded", 0, "same", ""),
        ("patched", "target"): ("succeeded", 0, "same", ""),
        ("baseline", "regression"): ("succeeded", 0, "stable", ""),
        ("patched", "regression"): ("succeeded", 0, "stable", ""),
    })
    evaluation = run_case(tmp_path, runtime)
    assert evaluation.status is BehavioralStatus.INCONCLUSIVE
    assert evaluation.failure_class == "expected_behavior_not_observed"


def test_runtime_failure_is_execution_error(tmp_path):
    runtime = ScriptedRuntime({
        ("baseline", "target"): ("failed", None, "", ""),
        ("patched", "target"): ("succeeded", 0, "after", ""),
        ("baseline", "regression"): ("succeeded", 0, "stable", ""),
        ("patched", "regression"): ("succeeded", 0, "stable", ""),
    })
    original = runtime.run

    def timeout(request):
        result = original(request)
        return ExecutionResult(**{**result.__dict__, "termination_reason": "timeout"})

    runtime.run = timeout
    evaluation = run_case(tmp_path, runtime)
    assert evaluation.status is BehavioralStatus.EXECUTION_ERROR
    assert evaluation.failure_class == "test_execution_error"


def test_workspace_contamination_is_execution_error(tmp_path):
    runtime = ScriptedRuntime({
        ("baseline", "target"): ("failed", 1, "before", ""),
        ("patched", "target"): ("succeeded", 0, "after", ""),
        ("baseline", "regression"): ("succeeded", 0, "stable", ""),
        ("patched", "regression"): ("succeeded", 0, "stable", ""),
    }, mutate=True)
    evaluation = run_case(tmp_path, runtime)
    assert evaluation.status is BehavioralStatus.EXECUTION_ERROR
    assert evaluation.failure_class == "test_execution_error"
    assert evaluation.baseline_outcomes[0].termination_reason == "workspace_contamination"


def test_structural_gate_blocks_behavioral_execution(tmp_path):
    baseline, patched, patch_result, structural = make_case(tmp_path)
    rejected = structural.__class__(**{**structural.__dict__, "status": VerificationStatus.REJECTED})
    runtime = ScriptedRuntime({})
    evaluation = BehavioralVerifier().verify(
        episode_id="episode-1",
        patch_result_id=patch_result.patch_id,
        structural_evaluation_id="structural-1",
        structural_evaluation=rejected,
        patch_result=patch_result,
        baseline_workspace=baseline,
        patched_workspace=patched,
        test_plan=plan(),
        runtime=runtime,
    )
    assert evaluation.status is BehavioralStatus.EXECUTION_ERROR
    assert evaluation.failure_class == "structural_gate_not_passed"
    assert runtime.requests == []


def test_evaluation_records_plan_runtime_and_output_provenance(tmp_path):
    runtime = ScriptedRuntime({
        ("baseline", "target"): ("failed", 1, "before", ""),
        ("patched", "target"): ("succeeded", 0, "after", ""),
        ("baseline", "regression"): ("succeeded", 0, "stable", ""),
        ("patched", "regression"): ("succeeded", 0, "stable", ""),
    })
    test_plan = plan()
    evaluation = run_case(tmp_path, runtime, test_plan)
    assert evaluation.test_plan_hash == test_plan.hash()
    assert evaluation.runtime_snapshot_hash
    assert evaluation.baseline_tree_hash != evaluation.patched_tree_hash
    assert evaluation.patched_outcomes[0].stdout_sha256
    assert evaluation.patched_outcomes[0].exit_code == 0
