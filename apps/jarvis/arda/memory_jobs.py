"""Durable post-processing for real-work memory lessons.

Memory writes are deliberately kept outside the coding queue's critical path.
This module is part of the ``arda`` package so a deployment has one canonical
runtime artifact instead of a separately copied worker script.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Protocol


class LessonMemory(Protocol):
    def add(self, messages: list[dict[str, str]], *, user_id: str,
            metadata: dict[str, str], infer: bool) -> Any: ...


def _lock(root: Path):
    lock_path = root / "memory-worker.lock"
    lock_path.touch(exist_ok=True)
    handle = lock_path.open("r+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def process_pending_memory_jobs(root: str | Path, *, memory: LessonMemory | None = None,
                                user_id: str = "arda-harness-v1") -> int:
    """Process pending lesson jobs and return the number completed.

    A job is renamed to ``.done`` only after both the memory write and episode
    reference update succeed. Memory references are checkpointed into the job
    before the database update, so a crash after the provider write can retry
    without creating a duplicate memory entry.
    """
    from arda.real_work_harness import EpisodeStore

    root_path = Path(root).expanduser().resolve()
    jobs = root_path / "memory-jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    db = EpisodeStore(root_path / "episodes.sqlite3")
    if memory is None:
        mem0_root = os.environ.get("MEM0_LOCAL_ROOT")
        if not mem0_root:
            raise RuntimeError("MEM0_LOCAL_ROOT is required when no memory adapter is injected")
        if mem0_root not in sys.path and Path(mem0_root).exists():
            sys.path.insert(0, mem0_root)
        from config import get_memory
        memory = get_memory()

    lock_handle = _lock(root_path)
    try:
        processed = 0
        for job in sorted(jobs.glob("*.json")):
            payload = json.loads(job.read_text(encoding="utf-8"))
            lesson = payload["lesson"]
            if "memory_refs" not in payload:
                result = memory.add(
                    [{"role": "user", "content": "Arda durable engineering lesson: "
                      + json.dumps(lesson, ensure_ascii=False, sort_keys=True)}],
                    user_id=user_id,
                    metadata={"scope": "real-work", "repo": lesson["repo"],
                              "task_id": lesson["task_id"], "episode_id": payload["episode_id"]},
                    infer=False,
                )
                refs = ([item["id"] for item in result.get("results", []) if item.get("id")]
                        if isinstance(result, dict) else [])
                payload["memory_refs"] = refs
                tmp = job.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(job)
            refs = payload["memory_refs"]
            if not db.update_memory_refs(payload["episode_id"], refs):
                raise RuntimeError(f"episode not found: {payload['episode_id']}")
            job.rename(job.with_suffix(".done"))
            processed += 1
        return processed
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def watch_pending_memory_jobs(root: str | Path, *, poll_seconds: float = 5.0,
                              user_id: str = "arda-harness-v1") -> None:
    """Keep the sidecar alive and retry pending jobs after provider outages."""
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    while True:
        try:
            process_pending_memory_jobs(root, user_id=user_id)
        except Exception as exc:  # the service stays alive and retries safely
            print(f"memory-worker error: {exc}", file=sys.stderr, flush=True)
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process queued Arda memory lessons")
    parser.add_argument("--root", type=Path,
                        default=Path(os.environ.get("ARDA_HARNESS_ROOT", ".")))
    parser.add_argument("--watch", action="store_true", help="keep polling pending jobs")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)
    if args.watch:
        watch_pending_memory_jobs(args.root, poll_seconds=args.poll_seconds)
    print(f"processed={process_pending_memory_jobs(args.root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
