from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from arda.memory_jobs import process_pending_memory_jobs


class Memory:
    def __init__(self):
        self.calls = []

    def add(self, messages, *, user_id, metadata, infer):
        self.calls.append((messages, user_id, metadata, infer))
        return {"results": [{"id": "memory-1"}]}


def test_processor_is_packaged_and_reconciles_episode_refs(tmp_path: Path):
    jobs = tmp_path / "memory-jobs"
    jobs.mkdir()
    with sqlite3.connect(tmp_path / "episodes.sqlite3") as db:
        db.execute("""CREATE TABLE episodes (
            episode_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, repo TEXT NOT NULL,
            baseline_sha TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
            reasoning TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            attempts INTEGER NOT NULL, result TEXT NOT NULL, failure_class TEXT,
            verification_result TEXT NOT NULL, accepted_sha TEXT, artifact_path TEXT NOT NULL,
            memory_refs TEXT NOT NULL)""")
        db.execute("INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "episode-1", "TASK-001", "/repo", "base", "provider", "model", "medium",
            "start", "finish", 1, "PASS", None, "{}", "accepted", "/artifact", "[]"))
    lesson = {"task_id": "TASK-001", "repo": "repo", "result": "PASS"}
    (jobs / "episode-1.json").write_text(json.dumps({"episode_id": "episode-1", "lesson": lesson}))

    memory = Memory()
    assert process_pending_memory_jobs(tmp_path, memory=memory) == 1
    assert len(memory.calls) == 1
    assert list(jobs.glob("*.json")) == []
    assert list(jobs.glob("*.done"))
    with sqlite3.connect(tmp_path / "episodes.sqlite3") as db:
        assert db.execute("SELECT memory_refs FROM episodes").fetchone()[0] == '["memory-1"]'


def test_processor_retries_from_checkpoint_without_duplicate_add(tmp_path: Path):
    test_processor_is_packaged_and_reconciles_episode_refs(tmp_path)
    done = tmp_path / "memory-jobs" / "episode-1.done"
    payload = json.loads(done.read_text())
    done.rename(tmp_path / "memory-jobs" / "episode-1.json")
    payload["memory_refs"] = ["memory-1"]
    (tmp_path / "memory-jobs" / "episode-1.json").write_text(json.dumps(payload))

    memory = Memory()
    assert process_pending_memory_jobs(tmp_path, memory=memory) == 1
    assert memory.calls == []
