"""M2.4D1 deterministic structural verification of PatchResult."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from arda.patch_worker import PatchResult, PatchStatus, PatchWorker


class VerificationStatus(str, Enum):
    STRUCTURAL_PASS = "STRUCTURAL_PASS"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class VerificationRequest:
    episode_id: str
    baseline_workspace: Path
    patched_workspace: Path
    patch_result: PatchResult
    allowed_files: tuple[str, ...]
    forbidden_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class StructuralEvaluation:
    episode_id: str
    patch_id: str
    status: VerificationStatus
    checks: Mapping[str, bool]
    baseline_tree_hash: str
    patched_tree_hash: str
    touched_files: tuple[str, ...]
    evaluator_version: str = "structural-verifier-v0.1"
    failure_class: str | None = None


class StructuralVerifier:
    """Verify PatchResult evidence without running tests or using an LLM."""

    def verify(self, request: VerificationRequest) -> StructuralEvaluation:
        patch = request.patch_result
        baseline = PatchWorker._snapshot(request.baseline_workspace.resolve())
        patched = PatchWorker._snapshot(request.patched_workspace.resolve())
        baseline_hash = PatchWorker._tree_hash(baseline)
        patched_hash = PatchWorker._tree_hash(patched)
        changed = tuple(sorted(set(baseline) | set(patched)))
        changed = tuple(path for path in changed if baseline.get(path) != patched.get(path))
        touched = tuple(sorted(patch.touched_files))

        checks: dict[str, bool] = {
            "patch_applied": patch.status is PatchStatus.APPLIED,
            "episode_matches": patch.episode_id == request.episode_id,
            "base_tree_hash": patch.base_tree_hash == baseline_hash,
            "touched_files_exact": changed == touched,
            "scope_allowed": set(changed).issubset(set(request.allowed_files)),
            "forbidden_paths_unchanged": not set(changed).intersection(request.forbidden_paths),
            "result_tree_hash": patch.result_tree_hash == patched_hash,
            "file_hashes_match": self._file_hashes_match(patch, patched, touched),
            "diff_matches": patch.unified_diff == self._diff(baseline, patched, touched),
            "no_verification_claim": not hasattr(patch, "verification_status"),
        }
        failed = next((name for name, passed in checks.items() if not passed), None)
        status = VerificationStatus.STRUCTURAL_PASS if failed is None else VerificationStatus.REJECTED
        return StructuralEvaluation(
            episode_id=request.episode_id,
            patch_id=patch.patch_id,
            status=status,
            checks=checks,
            baseline_tree_hash=baseline_hash,
            patched_tree_hash=patched_hash,
            touched_files=touched,
            failure_class=failed,
        )

    @staticmethod
    def _file_hashes_match(patch: PatchResult, patched: Mapping[str, bytes], touched: tuple[str, ...]) -> bool:
        return all(
            path in patched and patch.file_hashes.get(path) == hashlib.sha256(patched[path]).hexdigest()
            for path in touched
        )

    @staticmethod
    def _diff(baseline: Mapping[str, bytes], patched: Mapping[str, bytes], touched: tuple[str, ...]) -> str:
        import difflib

        output: list[str] = []
        for path in touched:
            before = baseline.get(path, b"").decode("utf-8")
            after = patched.get(path, b"").decode("utf-8")
            output.extend(difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="\n",
            ))
        return "".join(output)
