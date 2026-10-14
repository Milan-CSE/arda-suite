"""Deterministic, read-only repository exploration for M2.4A.

The explorer produces an immutable ``ExplorerRun`` observation.  It never
claims downstream success; that belongs to ``ExplorerEvaluation`` and is
written later by an independent evaluator.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from arda.core import Database, _hash_payload, _iso, _utc_now


EXPLORER_VERSION = "lexical-v1"
STRATEGY_ID = "lexical"
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
_DEFAULT_IGNORE = frozenset({
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules",
    ".mypy_cache", ".ruff_cache", "dist", "build",
})
_TEXT_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".css", ".go", ".h", ".hpp", ".java", ".js",
    ".json", ".jsx", ".md", ".py", ".rs", ".sh", ".sql", ".toml", ".ts",
    ".tsx", ".txt", ".yaml", ".yml",
})


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tokens(value: str) -> tuple[str, ...]:
    # Treat snake_case symbols as both a symbol and searchable component words.
    # This keeps the baseline lexical while matching ordinary repository names.
    expanded = value.replace("_", " ").replace("-", " ")
    return tuple(dict.fromkeys(token.lower() for token in _TOKEN_RE.findall(expanded)))


def _read_text(path: Path, max_bytes: int) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > max_bytes or b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _files(root: Path, ignored: frozenset[str]) -> list[Path]:
    result: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        if any(part in ignored for part in path.relative_to(root).parts):
            continue
        result.append(path)
    return sorted(result, key=lambda item: item.relative_to(root).as_posix())


def _tree_manifest(root: Path, paths: Iterable[Path], max_file_bytes: int) -> tuple[list[dict[str, str]], int]:
    manifest: list[dict[str, str]] = []
    total_bytes = 0
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) > max_file_bytes:
            continue
        digest = _sha256(raw)
        manifest.append({"path": relative, "blob_hash": digest})
        total_bytes += len(raw)
    return manifest, total_bytes


def _tree_hash(manifest: list[dict[str, str]]) -> str:
    return _sha256(_canonical(manifest))


@dataclass(frozen=True)
class ExplorerRun:
    run_id: str
    explorer_version: str
    strategy_id: str
    strategy_config_hash: str
    repository_root: str
    repository_commit: str | None
    repository_tree_hash: str
    task_hash: str
    selected_files: tuple[str, ...]
    selected_regions: tuple[dict[str, Any], ...]
    ignored_regions: tuple[str, ...]
    context_budget: dict[str, int]
    context_pack_hash: str
    provenance: tuple[dict[str, Any], ...]
    canonical_hash: str
    latency_ms: int
    resource_usage: dict[str, int]
    created_at: str

    def canonical_payload(self) -> dict[str, Any]:
        """Return only deterministic output; IDs, timestamps and latency are excluded."""
        return {
            "explorer_version": self.explorer_version,
            "strategy_id": self.strategy_id,
            "strategy_config_hash": self.strategy_config_hash,
            "repository_commit": self.repository_commit,
            "repository_tree_hash": self.repository_tree_hash,
            "task_hash": self.task_hash,
            "selected_files": list(self.selected_files),
            "selected_regions": list(self.selected_regions),
            "ignored_regions": list(self.ignored_regions),
            "context_budget": self.context_budget,
            "context_pack_hash": self.context_pack_hash,
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True)
class ExplorerEvaluation:
    evaluation_id: str
    explorer_run_id: str
    evaluator_version: str
    oracle_source: str
    file_recall_at_k: float | None = None
    line_recall: float | None = None
    mrr: float | None = None
    context_efficiency: float | None = None
    irrelevant_context_ratio: float | None = None
    downstream_patch_success: bool | None = None
    targeted_tests: tuple[str, ...] = ()
    regression_tests: tuple[str, ...] = ()
    verification_status: str = "pending"
    created_at: str = ""


def _line_set(regions: Iterable[Mapping[str, Any]]) -> set[tuple[str, int]]:
    lines: set[tuple[str, int]] = set()
    for region in regions:
        for line in range(int(region["start_line"]), int(region["end_line"]) + 1):
            lines.add((str(region["path"]), line))
    return lines


def evaluate_run(
    run: ExplorerRun,
    oracle: Mapping[str, Any],
    *,
    evaluator_version: str = "fixture-oracle-v1",
    oracle_source: str = "external-fixture",
) -> ExplorerEvaluation:
    """Evaluate a run against an independent file/line oracle.

    The oracle format is ``{"relevant_files": [...], "relevant_regions":
    [{"path": ..., "start_line": ..., "end_line": ...}]}``.  No explorer
    score or selection reason is used to construct the oracle.
    """
    relevant_files = {str(path) for path in oracle.get("relevant_files", ())}
    relevant_regions = tuple(oracle.get("relevant_regions", ()))
    if not relevant_files or not relevant_regions:
        raise ValueError("oracle must contain relevant_files and relevant_regions")
    ranked_files: list[str] = []
    for region in run.selected_regions:
        path = str(region["path"])
        if path not in ranked_files:
            ranked_files.append(path)
    selected_lines = _line_set(run.selected_regions)
    oracle_lines = _line_set(relevant_regions)
    relevant_selected_lines = selected_lines & oracle_lines
    file_recall = lambda k: len(set(ranked_files[:k]) & relevant_files) / len(relevant_files)
    first_rank = next((index + 1 for index, path in enumerate(ranked_files) if path in relevant_files), None)
    mrr = 0.0 if first_rank is None else 1.0 / first_rank
    line_recall = len(relevant_selected_lines) / len(oracle_lines)
    context_efficiency = len(relevant_selected_lines) / len(selected_lines) if selected_lines else 0.0
    return ExplorerEvaluation(
        evaluation_id=f"evaluation_{uuid.uuid4().hex}",
        explorer_run_id=run.run_id,
        evaluator_version=evaluator_version,
        oracle_source=oracle_source,
        file_recall_at_k=file_recall(5),
        line_recall=line_recall,
        mrr=mrr,
        context_efficiency=context_efficiency,
        irrelevant_context_ratio=1.0 - context_efficiency,
        verification_status="complete",
        created_at=_iso(_utc_now()),
    )


class ExplorerStore:
    """Append-only persistence boundary for runs and independent evaluations."""

    def __init__(self, db: Database):
        self.db = db

    def save_run(self, run: ExplorerRun) -> None:
        self.db.execute(
            """INSERT INTO explorer_runs(
                run_id, explorer_version, strategy_id, strategy_config_hash,
                repository_root, repository_commit, repository_tree_hash, task_hash,
                selected_files, selected_regions, ignored_regions, context_budget,
                context_pack_hash, provenance, canonical_result, canonical_hash,
                latency_ms, resource_usage, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run.run_id, run.explorer_version, run.strategy_id,
                run.strategy_config_hash, run.repository_root, run.repository_commit,
                run.repository_tree_hash, run.task_hash, json.dumps(list(run.selected_files), ensure_ascii=False),
                json.dumps(list(run.selected_regions), ensure_ascii=False, sort_keys=True),
                json.dumps(list(run.ignored_regions), ensure_ascii=False),
                json.dumps(run.context_budget, sort_keys=True), run.context_pack_hash,
                json.dumps(list(run.provenance), ensure_ascii=False, sort_keys=True),
                json.dumps(run.canonical_payload(), ensure_ascii=False, sort_keys=True),
                run.canonical_hash, run.latency_ms, json.dumps(run.resource_usage, sort_keys=True), run.created_at,
            ),
        )

    def save_evaluation(self, evaluation: ExplorerEvaluation) -> None:
        self.db.execute(
            """INSERT INTO explorer_evaluations(
                evaluation_id, explorer_run_id, evaluator_version, oracle_source,
                file_recall_at_k, line_recall, mrr, context_efficiency,
                irrelevant_context_ratio, downstream_patch_success, targeted_tests,
                regression_tests, verification_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                evaluation.evaluation_id, evaluation.explorer_run_id,
                evaluation.evaluator_version, evaluation.oracle_source,
                evaluation.file_recall_at_k, evaluation.line_recall, evaluation.mrr,
                evaluation.context_efficiency, evaluation.irrelevant_context_ratio,
                None if evaluation.downstream_patch_success is None else int(evaluation.downstream_patch_success),
                json.dumps(list(evaluation.targeted_tests), ensure_ascii=False),
                json.dumps(list(evaluation.regression_tests), ensure_ascii=False),
                evaluation.verification_status, evaluation.created_at or _iso(_utc_now()),
            ),
        )


class LexicalExplorer:
    """Read-only lexical baseline with explicit deterministic tie-breaking."""

    def __init__(
        self,
        *,
        version: str = EXPLORER_VERSION,
        max_file_bytes: int = 512_000,
        ignored_directories: Iterable[str] = _DEFAULT_IGNORE,
    ):
        self.version = version
        self.max_file_bytes = max_file_bytes
        self.ignored_directories = frozenset(ignored_directories)

    def run(
        self,
        repository_root: str | Path,
        objective: str,
        *,
        repository_commit: str | None = None,
        max_files: int = 20,
        max_lines: int = 1200,
        max_region_lines: int = 80,
        store: ExplorerStore | None = None,
    ) -> ExplorerRun:
        if not objective.strip():
            raise ValueError("objective must not be empty")
        if max_files < 1 or max_lines < 1 or max_region_lines < 1:
            raise ValueError("explorer budgets must be positive")
        root = Path(repository_root).resolve()
        if not root.is_dir():
            raise NotADirectoryError(root)
        started = time.perf_counter_ns()
        paths = _files(root, self.ignored_directories)
        manifest, total_bytes = _tree_manifest(root, paths, self.max_file_bytes)
        manifest_by_path = {entry["path"]: entry["blob_hash"] for entry in manifest}
        tree_hash = _tree_hash(manifest)
        objective_tokens = set(_tokens(objective))
        config = {
            "max_file_bytes": self.max_file_bytes,
            "ignored_directories": sorted(self.ignored_directories),
            "max_files": max_files,
            "max_lines": max_lines,
            "max_region_lines": max_region_lines,
        }
        config_hash = _sha256(_canonical(config))
        scored: list[tuple[float, str, list[dict[str, Any]], int]] = []
        for path in paths:
            relative = path.relative_to(root).as_posix()
            text = _read_text(path, self.max_file_bytes)
            if text is None:
                continue
            lines = text.splitlines()
            path_tokens = set(_tokens(relative.replace("/", " ")))
            path_hits = objective_tokens & path_tokens
            regions: list[dict[str, Any]] = []
            line_hits = 0
            for index, line in enumerate(lines):
                hits = objective_tokens & set(_tokens(line))
                if not hits:
                    continue
                line_hits += len(hits)
                start = max(0, index - max_region_lines // 4)
                end = min(len(lines), start + max_region_lines)
                region_score = round((len(hits) / max(1, len(objective_tokens))) + (len(path_hits) * 0.1), 8)
                regions.append({
                    "path": relative,
                    "blob_hash": manifest_by_path[relative],
                    "start_line": start + 1,
                    "end_line": end,
                    "content": "\n".join(lines[start:end]),
                    "selection_reason": sorted({"lexical_match", *(["path_match"] if path_hits else [])}),
                    "score": region_score,
                })
            score = round(len(path_hits) * 0.25 + line_hits, 8)
            if score or path_hits:
                scored.append((score, relative, regions, len(lines)))
        scored.sort(key=lambda item: (-item[0], item[1]))
        chosen = scored[:max_files]
        selected_regions: list[dict[str, Any]] = []
        used_lines = 0
        for _, relative, regions, _ in chosen:
            for region in sorted(regions, key=lambda item: (-item["score"], item["start_line"], item["path"])):
                size = region["end_line"] - region["start_line"] + 1
                if used_lines + size > max_lines:
                    continue
                selected_regions.append(region)
                used_lines += size
        selected_files = tuple(sorted({region["path"] for region in selected_regions}))
        ignored_regions = tuple(sorted(set(manifest_by_path) - set(selected_files)))
        provenance = tuple({
            "path": region["path"],
            "blob_hash": region["blob_hash"],
            "start_line": region["start_line"],
            "end_line": region["end_line"],
        } for region in selected_regions)
        context = {
            "objective": objective,
            "regions": selected_regions,
        }
        context_hash = _sha256(_canonical(context))
        task_hash = _hash_payload({"objective": objective})
        canonical_payload = {
            "explorer_version": self.version,
            "strategy_id": STRATEGY_ID,
            "strategy_config_hash": config_hash,
            "repository_commit": repository_commit,
            "repository_tree_hash": tree_hash,
            "task_hash": task_hash,
            "selected_files": list(selected_files),
            "selected_regions": selected_regions,
            "ignored_regions": list(ignored_regions),
            "context_budget": {"max_files": max_files, "max_lines": max_lines, "max_region_lines": max_region_lines},
            "context_pack_hash": context_hash,
            "provenance": list(provenance),
        }
        run = ExplorerRun(
            run_id=f"explorer_{uuid.uuid4().hex}", explorer_version=self.version,
            strategy_id=STRATEGY_ID, strategy_config_hash=config_hash,
            repository_root=str(root), repository_commit=repository_commit,
            repository_tree_hash=tree_hash, task_hash=task_hash,
            selected_files=selected_files, selected_regions=tuple(selected_regions),
            ignored_regions=ignored_regions,
            context_budget=canonical_payload["context_budget"], context_pack_hash=context_hash,
            provenance=provenance, canonical_hash=_sha256(_canonical(canonical_payload)),
            latency_ms=max(0, (time.perf_counter_ns() - started) // 1_000_000),
            resource_usage={"files_scanned": len(paths), "bytes_scanned": total_bytes, "regions_selected": len(selected_regions)},
            created_at=_iso(_utc_now()),
        )
        if run.canonical_payload() != canonical_payload:
            raise AssertionError("explorer canonical payload construction drifted")
        if store is not None:
            store.save_run(run)
        return run
