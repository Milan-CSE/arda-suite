"""Failure-first M2.4D1 structural verification tests."""
from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from arda.patch_worker import AllowedFile, PatchEdit, PatchStatus, PatchWorker, PatchRequest
from arda.verification import VerificationRequest, VerificationStatus, StructuralVerifier


def make_workspaces(tmp_path: Path):
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
    return baseline, patched, result


def verify(baseline, patched, result, **kwargs):
    return StructuralVerifier().verify(VerificationRequest(
        episode_id="episode-1",
        baseline_workspace=baseline,
        patched_workspace=patched,
        patch_result=result,
        allowed_files=("module.py",),
        **kwargs,
    ))


def test_structural_pass_is_independent_of_patch_worker(tmp_path):
    baseline, patched, result = make_workspaces(tmp_path)
    evaluation = verify(baseline, patched, result)
    assert evaluation.status is VerificationStatus.STRUCTURAL_PASS
    assert all(evaluation.checks.values())
    assert evaluation.failure_class is None


def test_wrong_base_hash_is_rejected(tmp_path):
    baseline, patched, result = make_workspaces(tmp_path)
    evaluation = verify(baseline, patched, replace(result, base_tree_hash="0" * 64))
    assert evaluation.status is VerificationStatus.REJECTED
    assert evaluation.failure_class == "base_tree_hash"


def test_wrong_result_hash_is_rejected(tmp_path):
    baseline, patched, result = make_workspaces(tmp_path)
    evaluation = verify(baseline, patched, replace(result, result_tree_hash="0" * 64))
    assert evaluation.status is VerificationStatus.REJECTED
    assert evaluation.failure_class == "result_tree_hash"


def test_extra_changed_file_is_rejected(tmp_path):
    baseline, patched, result = make_workspaces(tmp_path)
    (patched / "unexpected.py").write_text("escape\n", encoding="utf-8")
    evaluation = verify(baseline, patched, result)
    assert evaluation.status is VerificationStatus.REJECTED
    assert evaluation.failure_class == "touched_files_exact"


def test_forbidden_changed_file_is_rejected(tmp_path):
    baseline, patched, result = make_workspaces(tmp_path)
    evaluation = verify(baseline, patched, result, forbidden_paths=("module.py",))
    assert evaluation.status is VerificationStatus.REJECTED
    assert evaluation.failure_class == "forbidden_paths_unchanged"


def test_file_hash_and_diff_tampering_are_rejected(tmp_path):
    baseline, patched, result = make_workspaces(tmp_path)
    tampered = replace(result, file_hashes={"module.py": "0" * 64})
    evaluation = verify(baseline, patched, tampered)
    assert evaluation.status is VerificationStatus.REJECTED
    assert evaluation.failure_class == "file_hashes_match"

    tampered = replace(result, unified_diff="tampered")
    evaluation = verify(baseline, patched, tampered)
    assert evaluation.status is VerificationStatus.REJECTED
    assert evaluation.failure_class == "diff_matches"


def test_non_applied_patch_cannot_be_structural_pass(tmp_path):
    baseline, patched, result = make_workspaces(tmp_path)
    rejected = replace(result, status=PatchStatus.REJECTED)
    evaluation = verify(baseline, patched, rejected)
    assert evaluation.status is VerificationStatus.REJECTED
    assert evaluation.failure_class == "patch_applied"
