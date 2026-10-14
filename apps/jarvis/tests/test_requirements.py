"""Failure-first M2.4D3 deterministic requirement audit tests."""
from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from arda.behavioral import BehavioralEvaluation, BehavioralStatus
from arda.patch_worker import AllowedFile, PatchEdit, PatchRequest, PatchWorker
from arda.requirements import (
    Requirement,
    RequirementAuditor,
    RequirementSource,
    RequirementStatus,
    TaskContract,
)
from arda.verification import StructuralVerifier, VerificationRequest, VerificationStatus


def make_case(tmp_path: Path):
    baseline = tmp_path / "baseline"
    patched = tmp_path / "patched"
    baseline.mkdir()
    (baseline / "module.py").write_text("def answer():\n    return 1\n", encoding="utf-8")
    shutil.copytree(baseline, patched)
    before = (baseline / "module.py").read_bytes()
    request = PatchRequest("episode-1", "patch-1", "run-1", PatchWorker._tree_hash(PatchWorker._snapshot(baseline)), (AllowedFile("module.py", PatchWorker._sha256(before)),), (PatchEdit("replace_exact", "module.py", PatchWorker._sha256(before), "return 1", "return 2"),))
    result = PatchWorker().apply(request, patched)
    structural = StructuralVerifier().verify(VerificationRequest("episode-1", baseline, patched, result, ("module.py",)))
    behavioral = BehavioralEvaluation("episode-1", "patch-1", "patch-1", BehavioralStatus.BEHAVIORAL_PASS, "plan", "runtime", structural.baseline_tree_hash, structural.patched_tree_hash, (), (), True, 1, 0, 0)
    return baseline, patched, result, structural, behavioral


def contract(*requirements, allowed=("module.py",), forbidden=()):
    return TaskContract("task-1", "episode-1", "change answer", explicit_requirements=tuple(requirements), allowed_scope=allowed, forbidden_changes=forbidden)


def test_satisfied_requirements_require_typed_evidence(tmp_path):
    baseline, patched, patch, structural, behavioral = make_case(tmp_path)
    task = contract(
        Requirement("r-file", "module exists", "file_exists", target="module.py"),
        Requirement("r-content", "answer changed", "file_contains", target="module.py", expected="return 2"),
        Requirement("r-behavior", "behavior passed", "behavioral_status", expected="BEHAVIORAL_PASS"),
        Requirement("r-regression", "no regression", "no_regression"),
    )
    audit = RequirementAuditor().audit(task_contract=task, patch_result=patch, structural_evaluation=structural, behavioral_evaluation=behavioral, behavioral_evaluation_id="behavior-1", patched_workspace=patched)
    assert audit.status is RequirementStatus.SATISFIED
    assert all(item.status is RequirementStatus.SATISFIED for item in audit.evaluations)
    assert all(item.evidence for item in audit.evaluations)
    assert audit.task_contract_hash == task.hash()


def test_forbidden_change_is_violated(tmp_path):
    _, patched, patch, structural, behavioral = make_case(tmp_path)
    task = contract(Requirement("r-forbidden", "module must not change", "no_forbidden_changes"), forbidden=("module.py",))
    audit = RequirementAuditor().audit(task_contract=task, patch_result=patch, structural_evaluation=structural, behavioral_evaluation=behavioral, behavioral_evaluation_id="behavior-1", patched_workspace=patched)
    assert audit.status is RequirementStatus.VIOLATED
    assert audit.evaluations[0].failure_class == "scope_violation"


def test_unverified_is_not_satisfied(tmp_path):
    _, patched, patch, structural, behavioral = make_case(tmp_path)
    task = contract(Requirement("r-unknown", "user intent", "free_text"))
    audit = RequirementAuditor().audit(task_contract=task, patch_result=patch, structural_evaluation=structural, behavioral_evaluation=behavioral, patched_workspace=patched)
    assert audit.status is RequirementStatus.UNVERIFIED
    assert audit.evaluations[0].status is RequirementStatus.UNVERIFIED


def test_inferred_free_text_never_auto_passes(tmp_path):
    _, patched, patch, structural, behavioral = make_case(tmp_path)
    requirement = Requirement("r-inferred", "probably preserve API", "free_text", RequirementSource.INFERRED)
    audit = RequirementAuditor().audit(task_contract=contract(requirement), patch_result=patch, structural_evaluation=structural, behavioral_evaluation=behavioral, patched_workspace=patched)
    assert audit.status is RequirementStatus.UNVERIFIED
    assert audit.evaluations[0].failure_class == "inferred_requirement_not_typed"


def test_structural_failure_blocks_requirement_satisfaction(tmp_path):
    _, patched, patch, structural, behavioral = make_case(tmp_path)
    rejected = replace(structural, status=VerificationStatus.REJECTED)
    task = contract(Requirement("r-file", "module exists", "file_exists", target="module.py"))
    audit = RequirementAuditor().audit(task_contract=task, patch_result=patch, structural_evaluation=rejected, behavioral_evaluation=behavioral, patched_workspace=patched)
    assert audit.status is RequirementStatus.UNVERIFIED
    assert audit.evaluations[0].failure_class == "structural_gate_not_passed"


def test_unsafe_file_target_is_not_satisfied(tmp_path):
    _, patched, patch, structural, behavioral = make_case(tmp_path)
    task = contract(Requirement("r-escape", "outside file exists", "file_exists", target="../outside"))
    audit = RequirementAuditor().audit(task_contract=task, patch_result=patch, structural_evaluation=structural, behavioral_evaluation=behavioral, patched_workspace=patched)
    assert audit.status is RequirementStatus.VIOLATED
    assert audit.evaluations[0].failure_class == "unsafe_target_path"
