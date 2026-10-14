"""Failure-first M2.4C deterministic PatchWorker tests."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from arda.patch_worker import (
    AllowedFile,
    PatchEdit,
    PatchStatus,
    PatchWorker,
    PatchRequest,
)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fixture(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    target = root / "module.py"
    target.write_text("def answer():\n    return 1\n", encoding="utf-8")
    return root, target


def make_request(root: Path, target: Path, **kwargs) -> PatchRequest:
    before = target.read_bytes()
    return PatchRequest(
        episode_id="episode-1",
        patch_id="patch-1",
        explorer_run_id="run-1",
        base_tree_hash=PatchWorker._tree_hash(PatchWorker._snapshot(root)),
        allowed_files=(AllowedFile("module.py", sha(before)),),
        edits=(PatchEdit(
            operation="replace_exact",
            path="module.py",
            expected_blob_sha256=sha(before),
            old_text="return 1",
            new_text="return 2",
        ),),
        **kwargs,
    )


def test_exact_replace_produces_diff_hashes_and_scope_provenance(tmp_path):
    root, target = fixture(tmp_path)
    result = PatchWorker().apply(make_request(root, target), root)

    assert result.status is PatchStatus.APPLIED
    assert result.episode_id == "episode-1"
    assert result.touched_files == ("module.py",)
    assert "--- a/module.py" in result.unified_diff
    assert "+++ b/module.py" in result.unified_diff
    assert result.base_tree_hash != result.result_tree_hash
    assert result.file_hashes["module.py"] == sha(target.read_bytes())
    assert "CORRECT" not in result.status.value


def test_no_exact_match_is_conflict_and_workspace_is_unchanged(tmp_path):
    root, target = fixture(tmp_path)
    before = target.read_bytes()
    request = make_request(root, target)
    request = PatchRequest(**{**request.__dict__, "edits": (PatchEdit(
        operation="replace_exact", path="module.py", expected_blob_sha256=sha(before),
        old_text="return missing", new_text="return 2",
    ),)})
    result = PatchWorker().apply(request, root)
    assert result.status is PatchStatus.CONFLICT
    assert target.read_bytes() == before


def test_ambiguous_match_is_conflict(tmp_path):
    root, target = fixture(tmp_path)
    target.write_text("x\nx\n", encoding="utf-8")
    result = PatchWorker().apply(make_request(root, target), root)
    assert result.status is PatchStatus.CONFLICT
    assert target.read_text(encoding="utf-8") == "x\nx\n"


def test_preimage_hash_mismatch_is_rejected(tmp_path):
    root, target = fixture(tmp_path)
    request = make_request(root, target)
    target.write_text("changed\n", encoding="utf-8")
    result = PatchWorker().apply(request, root)
    assert result.status is PatchStatus.PREIMAGE_MISMATCH


def test_out_of_scope_and_traversal_paths_are_rejected(tmp_path):
    root, target = fixture(tmp_path)
    request = make_request(root, target)
    edit = PatchEdit("replace_exact", "../outside.py", sha(target.read_bytes()), "return 1", "return 2")
    request = PatchRequest(**{**request.__dict__, "edits": (edit,)})
    result = PatchWorker().apply(request, root)
    assert result.status is PatchStatus.SCOPE_VIOLATION
    assert target.read_text(encoding="utf-8") == "def answer():\n    return 1\n"


def test_unselected_file_is_scope_violation(tmp_path):
    root, target = fixture(tmp_path)
    other = root / "other.py"
    other.write_text("x\n", encoding="utf-8")
    request = make_request(root, target)
    edit = PatchEdit("replace_exact", "other.py", sha(other.read_bytes()), "x", "y")
    result = PatchWorker().apply(PatchRequest(**{**request.__dict__, "edits": (edit,)}), root)
    assert result.status is PatchStatus.SCOPE_VIOLATION
    assert other.read_text(encoding="utf-8") == "x\n"


def test_symlink_escape_is_rejected(tmp_path):
    root, target = fixture(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("secret\n", encoding="utf-8")
    (root / "link.py").symlink_to(outside)
    request = make_request(root, target)
    edit = PatchEdit("replace_exact", "link.py", sha(outside.read_bytes()), "secret", "changed")
    result = PatchWorker().apply(PatchRequest(**{**request.__dict__, "edits": (edit,)}), root)
    assert result.status is PatchStatus.SCOPE_VIOLATION
    assert outside.read_text(encoding="utf-8") == "secret\n"


def test_duplicate_edit_path_is_conflict(tmp_path):
    root, target = fixture(tmp_path)
    request = make_request(root, target)
    edit = request.edits[0]
    result = PatchWorker().apply(PatchRequest(**{**request.__dict__, "edits": (edit, edit)}), root)
    assert result.status is PatchStatus.CONFLICT


def test_byte_budget_rejection_rolls_back(tmp_path):
    root, target = fixture(tmp_path)
    request = make_request(root, target)
    edit = PatchEdit(
        "replace_exact", "module.py", sha(target.read_bytes()), "return 1", "return 222222"
    )
    request = PatchRequest(**{**request.__dict__, "edits": (edit,)})
    tiny = request.limits.__class__(max_added_bytes=1, max_removed_bytes=1, max_total_bytes=1)
    request = PatchRequest(**{**request.__dict__, "limits": tiny})
    before = target.read_bytes()
    result = PatchWorker().apply(request, root)
    assert result.status is PatchStatus.REJECTED
    assert target.read_bytes() == before


def test_same_input_is_byte_deterministic(tmp_path):
    root1, target1 = fixture(tmp_path / "one")
    root2, target2 = fixture(tmp_path / "two")
    worker = PatchWorker()
    first = worker.apply(make_request(root1, target1), root1)
    second = worker.apply(make_request(root2, target2), root2)
    assert first.unified_diff == second.unified_diff
    assert first.file_hashes == second.file_hashes
    assert first.base_tree_hash == second.base_tree_hash
    assert first.result_tree_hash == second.result_tree_hash


def test_patch_result_has_no_verification_success_state(tmp_path):
    root, target = fixture(tmp_path)
    result = PatchWorker().apply(make_request(root, target), root)
    assert set(PatchStatus) == {
        PatchStatus.APPLIED, PatchStatus.REJECTED, PatchStatus.CONFLICT,
        PatchStatus.PREIMAGE_MISMATCH, PatchStatus.SCOPE_VIOLATION,
    }
    assert result.status is PatchStatus.APPLIED
