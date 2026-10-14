"""M2.4C deterministic, scope-bound patch worker."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


class PatchStatus(str, Enum):
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    CONFLICT = "CONFLICT"
    PREIMAGE_MISMATCH = "PREIMAGE_MISMATCH"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"


@dataclass(frozen=True)
class AllowedFile:
    path: str
    blob_sha256: str


@dataclass(frozen=True)
class PatchEdit:
    operation: str
    path: str
    expected_blob_sha256: str
    old_text: str
    new_text: str
    max_replacements: int = 1


@dataclass(frozen=True)
class PatchLimits:
    max_files: int = 4
    max_added_bytes: int = 32768
    max_removed_bytes: int = 32768
    max_total_bytes: int = 65536


@dataclass(frozen=True)
class PatchRequest:
    episode_id: str
    patch_id: str
    explorer_run_id: str
    base_tree_hash: str
    allowed_files: tuple[AllowedFile, ...]
    edits: tuple[PatchEdit, ...]
    limits: PatchLimits = field(default_factory=PatchLimits)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "patch_id": self.patch_id,
            "explorer_run_id": self.explorer_run_id,
            "base_tree_hash": self.base_tree_hash,
            "allowed_files": [file.__dict__ for file in self.allowed_files],
            "edits": [edit.__dict__ for edit in self.edits],
            "limits": self.limits.__dict__,
        }

    def input_hash(self) -> str:
        encoded = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PatchResult:
    episode_id: str
    patch_id: str
    explorer_run_id: str
    status: PatchStatus
    touched_files: tuple[str, ...]
    unified_diff: str
    base_tree_hash: str
    result_tree_hash: str
    file_hashes: Mapping[str, str]
    edit_count: int
    rejection_reason: str | None
    worker_version: str = "patch-worker-v0.1"


@dataclass(frozen=True)
class PatchEvaluation:
    patch_id: str
    verification_status: str = "pending"
    targeted_tests: tuple[str, ...] = ()
    regression_tests: tuple[str, ...] = ()
    syntax_status: str = "not_run"
    scope_status: str = "pending"
    artifact_hashes: Mapping[str, str] = field(default_factory=dict)
    evaluator_version: str = ""
    failure_class: str | None = None


class PatchWorker:
    """Apply only exact, preimage-verified edits inside an isolated workspace."""

    def apply(self, request: PatchRequest, workspace: str | Path) -> PatchResult:
        root = Path(workspace).resolve()
        baseline = self._snapshot(root)
        base_hash = self._tree_hash(baseline)
        try:
            self._validate_request(request, root, baseline, base_hash)
            changes = self._prepare_changes(request, root, baseline)
            self._validate_limits(request.limits, changes)
            self._write_changes(root, changes)
            after = self._snapshot(root)
            result_hash = self._tree_hash(after)
            touched = tuple(sorted(changes))
            diff = self._unified_diff(changes)
            return PatchResult(
                episode_id=request.episode_id,
                patch_id=request.patch_id,
                explorer_run_id=request.explorer_run_id,
                status=PatchStatus.APPLIED,
                touched_files=touched,
                unified_diff=diff,
                base_tree_hash=base_hash,
                result_tree_hash=result_hash,
                file_hashes={path: self._sha256(content) for path, content in sorted(after.items()) if path in touched},
                edit_count=len(request.edits),
                rejection_reason=None,
            )
        except _PatchFailure as failure:
            self._restore(root, baseline)
            return PatchResult(
                episode_id=request.episode_id,
                patch_id=request.patch_id,
                explorer_run_id=request.explorer_run_id,
                status=failure.status,
                touched_files=(),
                unified_diff="",
                base_tree_hash=base_hash,
                result_tree_hash=self._tree_hash(self._snapshot(root)),
                file_hashes={},
                edit_count=0,
                rejection_reason=failure.reason,
            )
        except (OSError, UnicodeError) as error:
            self._restore(root, baseline)
            return self._rejected(request, base_hash, str(error))

    @staticmethod
    def _rejected(request: PatchRequest, base_hash: str, reason: str) -> PatchResult:
        return PatchResult(
            episode_id=request.episode_id,
            patch_id=request.patch_id,
            explorer_run_id=request.explorer_run_id,
            status=PatchStatus.REJECTED,
            touched_files=(),
            unified_diff="",
            base_tree_hash=base_hash,
            result_tree_hash=base_hash,
            file_hashes={},
            edit_count=0,
            rejection_reason=reason,
        )

    def _validate_request(self, request, root, baseline, actual_tree_hash):
        if not request.patch_id or not request.explorer_run_id:
            raise _PatchFailure(PatchStatus.REJECTED, "missing identity")
        if request.base_tree_hash != actual_tree_hash:
            raise _PatchFailure(PatchStatus.PREIMAGE_MISMATCH, "base tree hash mismatch")
        if len(request.edits) == 0 or len(request.edits) > request.limits.max_files:
            raise _PatchFailure(PatchStatus.REJECTED, "edit count exceeds contract")
        allowed = {item.path: item.blob_sha256 for item in request.allowed_files}
        if len(allowed) != len(request.allowed_files):
            raise _PatchFailure(PatchStatus.REJECTED, "duplicate allowed file")
        paths = [edit.path for edit in request.edits]
        if len(set(paths)) != len(paths):
            raise _PatchFailure(PatchStatus.CONFLICT, "duplicate edit path")
        for edit in request.edits:
            self._validate_path(edit.path, root, baseline)
            if edit.operation != "replace_exact" or edit.max_replacements != 1:
                raise _PatchFailure(PatchStatus.REJECTED, "unsupported edit operation")
            if edit.path not in allowed:
                raise _PatchFailure(PatchStatus.SCOPE_VIOLATION, "path is outside ExplorerRun scope")
            if allowed[edit.path] != edit.expected_blob_sha256:
                raise _PatchFailure(PatchStatus.PREIMAGE_MISMATCH, "allowed file hash mismatch")

    @staticmethod
    def _validate_path(relative: str, root: Path, baseline: Mapping[str, bytes]) -> None:
        path = PurePosixPath(relative)
        if not relative or path.is_absolute() or ".." in path.parts or str(path) != relative:
            raise _PatchFailure(PatchStatus.SCOPE_VIOLATION, "unsafe relative path")
        target = root / relative
        if not target.exists() or target.is_symlink() or not target.is_file():
            raise _PatchFailure(PatchStatus.SCOPE_VIOLATION, "target is not a regular file")
        if relative not in baseline:
            raise _PatchFailure(PatchStatus.SCOPE_VIOLATION, "target is outside workspace snapshot")

    def _prepare_changes(self, request, root, baseline):
        changes: dict[str, tuple[str, str]] = {}
        for edit in request.edits:
            original = baseline[edit.path].decode("utf-8")
            if self._sha256(baseline[edit.path]) != edit.expected_blob_sha256:
                raise _PatchFailure(PatchStatus.PREIMAGE_MISMATCH, f"preimage mismatch: {edit.path}")
            matches = original.count(edit.old_text) if edit.old_text else 0
            if matches == 0:
                raise _PatchFailure(PatchStatus.CONFLICT, f"exact match not found: {edit.path}")
            if matches != 1:
                raise _PatchFailure(PatchStatus.CONFLICT, f"ambiguous exact match: {edit.path}")
            changes[edit.path] = (original, original.replace(edit.old_text, edit.new_text, 1))
        return changes

    @staticmethod
    def _validate_limits(limits, changes):
        added = sum(max(0, len(after.encode()) - len(before.encode())) for before, after in changes.values())
        removed = sum(max(0, len(before.encode()) - len(after.encode())) for before, after in changes.values())
        total = added + removed
        if added > limits.max_added_bytes or removed > limits.max_removed_bytes or total > limits.max_total_bytes:
            raise _PatchFailure(PatchStatus.REJECTED, "patch byte budget exceeded")

    @staticmethod
    def _write_changes(root, changes):
        for relative, (_, after) in changes.items():
            target = root / relative
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
                handle.write(after)
                temporary = Path(handle.name)
            os.replace(temporary, target)

    @staticmethod
    def _unified_diff(changes):
        output: list[str] = []
        for relative in sorted(changes):
            before, after = changes[relative]
            output.extend(difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"a/{relative}", tofile=f"b/{relative}", lineterm="\n",
            ))
        return "".join(output)

    @staticmethod
    def _snapshot(root: Path) -> dict[str, bytes]:
        snapshot: dict[str, bytes] = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                continue
            if path.is_file():
                snapshot[path.relative_to(root).as_posix()] = path.read_bytes()
        return snapshot

    @classmethod
    def _tree_hash(cls, snapshot: Mapping[str, bytes]) -> str:
        digest = hashlib.sha256()
        for relative, content in sorted(snapshot.items()):
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(cls._sha256(content).encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    @staticmethod
    def _sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _restore(root: Path, baseline: Mapping[str, bytes]) -> None:
        current = PatchWorker._snapshot(root)
        for relative in set(current) - set(baseline):
            (root / relative).unlink(missing_ok=True)
        for relative, content in baseline.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


class _PatchFailure(Exception):
    def __init__(self, status: PatchStatus, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason
