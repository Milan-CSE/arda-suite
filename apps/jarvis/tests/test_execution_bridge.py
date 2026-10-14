from __future__ import annotations

from arda.autonomous_work_loop import WorkResult, WorkTask
from arda.awareness import ProjectRegistry
from arda.core import bootstrap_runtime_database
from arda.execution_bridge import ExecutionBridge, ExecutionResult


def make_task() -> WorkTask:
    return WorkTask(
        task_id="TASK-001",
        repo="/repo",
        baseline_ref="main",
        baseline_sha="a" * 40,
        objective="Implement the bounded task",
        acceptance=("tests pass",),
    )


class PassingExecutor:
    def execute(self, task):
        assert task.task_id == "TASK-001"
        return ExecutionResult(
            result=WorkResult.PASS.value,
            accepted_sha="b" * 40,
            episode_id="ep-1",
            evidence={"tests": "PASS"},
        )


class FailingExecutor:
    def execute(self, task):
        return ExecutionResult(result=WorkResult.FAIL.value, reason="scope")


def setup_db(path):
    db = bootstrap_runtime_database(path)
    ProjectRegistry(db).create("sample-project", "Sample Project")
    db.close()


def test_enqueue_is_idempotent_and_run_persists_success(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    setup_db(db_path)
    bridge = ExecutionBridge(db_path, PassingExecutor(), worker_id="worker-1")

    first = bridge.enqueue(make_task(), project_id="sample-project")
    second = bridge.enqueue(make_task(), project_id="sample-project")
    assert first["task"]["id"] == second["task"]["id"] == "TASK-001"
    assert first["job"]["id"] == second["job"]["id"]

    outcome = bridge.run_once()
    assert outcome.result == "PASS"
    assert outcome.task_status == "done"
    db = bootstrap_runtime_database(db_path)
    try:
        task = db.execute("SELECT status FROM tasks WHERE id='TASK-001'").fetchone()
        job = db.execute("SELECT status, result FROM jobs").fetchone()
        events = [row[0] for row in db.execute("SELECT type FROM events").fetchall()]
        assert task["status"] == "done"
        assert job["status"] == "succeeded"
        assert '"accepted_sha"' in job["result"]
        assert "task.execution.started" in events
        assert "task.execution.completed" in events
    finally:
        db.close()


def test_failure_is_terminal_for_task_and_does_not_advance_baseline(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    setup_db(db_path)
    bridge = ExecutionBridge(db_path, FailingExecutor(), worker_id="worker-1")
    bridge.enqueue(make_task(), project_id="sample-project")

    outcome = bridge.run_once()
    assert outcome.result == "FAIL"
    assert outcome.task_status == "blocked"
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-001'").fetchone()[0] == "blocked"
        assert db.execute("SELECT status FROM jobs").fetchone()[0] == "failed"
    finally:
        db.close()


def test_heartbeat_renews_claimed_job(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    setup_db(db_path)
    bridge = ExecutionBridge(db_path, PassingExecutor(), worker_id="worker-1")
    queued = bridge.enqueue(make_task(), project_id="sample-project")
    db = bootstrap_runtime_database(db_path)
    try:
        from arda.core import JobQueue
        job = JobQueue(db).claim("worker-1", job_type="autonomous-coding")
        renewed = bridge.heartbeat(job.id, job.lease_token)
        assert renewed["id"] == job.id
        assert renewed["status"] == "leased"
    finally:
        db.close()
