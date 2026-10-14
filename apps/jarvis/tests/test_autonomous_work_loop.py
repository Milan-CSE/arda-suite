from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from arda.autonomous_work_loop import (
    AgentReport,
    AutonomousWorkLoop,
    EvaluationReport,
    GitWorktree,
    WorkResult,
    WorkTask,
)
from arda.core import Database


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "source.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "source.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return repo, sha


def _task(repo: Path, sha: str, task_id: str = "task-001", allowed=("source.txt",)) -> WorkTask:
    return WorkTask(
        task_id=task_id,
        repo=str(repo),
        baseline_ref="main",
        baseline_sha=sha,
        objective="Update the source fixture",
        acceptance=("independent evaluator passes",),
        allowed_paths=allowed,
    )


class EditingAgent:
    def __init__(self, relative="source.txt"):
        self.relative = relative
        self.workspaces: list[Path] = []

    def run(self, task, workspace):
        self.workspaces.append(workspace)
        target = workspace / self.relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("agent change\n", encoding="utf-8")
        return AgentReport(status="done", final_claim="completed", tool_calls=2,
                           trace=({"tool": "write_file"}, {"tool": "verify"}))


class FixedEvaluator:
    def __init__(self, passed=True):
        self.passed = passed
        self.workspaces: list[Path] = []

    def evaluate(self, task, workspace):
        self.workspaces.append(workspace)
        return EvaluationReport(self.passed, checks=({"name": "acceptance", "passed": self.passed},),
                                error=None if self.passed else "acceptance failed")


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    try:
        yield database
    finally:
        database.close()


def test_queue_runs_three_tasks_in_fresh_worktrees_and_persists_evidence(db, tmp_path):
    repo, sha = _repo(tmp_path)
    agent = EditingAgent()
    evaluator = FixedEvaluator()
    loop = AutonomousWorkLoop(db, worker_id="worker-1", workspace_root=tmp_path / "worktrees",
                              agent=agent, evaluator=evaluator)
    tasks = []
    for index in range(3):
        task = _task(repo, sha, f"task-{index}")
        job = loop.enqueue(task)
        tasks.append((job.id, task))

    outcomes = loop.run_queue({job_id: task for job_id, task in tasks})

    assert [outcome.result for outcome in outcomes] == [WorkResult.PASS] * 3
    assert len({str(path) for path in agent.workspaces}) == 3
    assert all(not path.exists() for path in agent.workspaces)
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True) == ""
    assert db.scalar("SELECT COUNT(*) FROM artifacts WHERE kind='work-loop-evidence.json'") == 3
    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE status='succeeded'") == 3


def test_scope_violation_is_not_promoted_even_when_evaluator_would_pass(db, tmp_path):
    repo, sha = _repo(tmp_path)
    agent = EditingAgent("unrelated.txt")
    evaluator = FixedEvaluator(True)
    loop = AutonomousWorkLoop(db, worker_id="worker-1", workspace_root=tmp_path / "worktrees",
                              agent=agent, evaluator=evaluator)
    task = _task(repo, sha, allowed=("source.txt",))
    job = loop.enqueue(task)

    outcome = loop.run_queue({job.id: task})[0]

    assert outcome.result is WorkResult.FAIL
    assert "unrelated path" in outcome.reason
    assert evaluator.workspaces == []
    assert db.scalar("SELECT status FROM jobs WHERE id=?", (job.id,)) == "failed"


def test_host_failure_stops_queue_without_claiming_jobs(db, tmp_path):
    repo, sha = _repo(tmp_path)
    loop = AutonomousWorkLoop(db, worker_id="worker-1", workspace_root=tmp_path / "worktrees",
                              agent=EditingAgent(), evaluator=FixedEvaluator(), host_health=lambda: False)
    task = _task(repo, sha)
    job = loop.enqueue(task)

    assert loop.run_queue({job.id: task}) == ()
    assert db.scalar("SELECT status FROM jobs WHERE id=?", (job.id,)) == "queued"
    assert db.scalar("SELECT COUNT(*) FROM events WHERE type='work_loop.host_unavailable'") == 1


def test_baseline_mismatch_is_invalid_and_original_checkout_stays_untouched(db, tmp_path):
    repo, sha = _repo(tmp_path)
    task = _task(repo, "0" * 40)
    loop = AutonomousWorkLoop(db, worker_id="worker-1", workspace_root=tmp_path / "worktrees",
                              agent=EditingAgent(), evaluator=FixedEvaluator())
    job = loop.enqueue(task)

    outcome = loop.run_queue({job.id: task})[0]

    assert outcome.result is WorkResult.INVALID
    assert "baseline" in outcome.reason
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True) == ""
    assert db.scalar("SELECT status FROM jobs WHERE id=?", (job.id,)) == "failed"


def test_persisted_contract_allows_a_new_worker_instance_to_resume_queue(db, tmp_path):
    repo, sha = _repo(tmp_path)
    task = _task(repo, sha, "durable-task")
    first = AutonomousWorkLoop(db, worker_id="worker-1", workspace_root=tmp_path / "worktrees",
                               agent=EditingAgent(), evaluator=FixedEvaluator())
    job = first.enqueue(task)

    second = AutonomousWorkLoop(db, worker_id="worker-2", workspace_root=tmp_path / "worktrees",
                                agent=EditingAgent(), evaluator=FixedEvaluator())
    outcome = second.run_queue({})[0]

    assert outcome.task_id == task.task_id
    assert outcome.result is WorkResult.PASS
    manifest = db.execute("SELECT context_manifest, policy_snapshot FROM jobs WHERE id=?", (job.id,)).fetchone()
    assert '"task_id":"durable-task"' in manifest["context_manifest"]
    assert '"model":"gpt-5.6-luna"' in manifest["policy_snapshot"]


def test_dirty_original_checkout_is_invalid_before_agent_starts(db, tmp_path):
    repo, sha = _repo(tmp_path)
    (repo / "local-change.txt").write_text("must not leak\n", encoding="utf-8")
    task = _task(repo, sha)
    agent = EditingAgent()
    loop = AutonomousWorkLoop(db, worker_id="worker-1", workspace_root=tmp_path / "worktrees",
                              agent=agent, evaluator=FixedEvaluator())
    job = loop.enqueue(task)

    outcome = loop.run_queue({job.id: task})[0]

    assert outcome.result is WorkResult.INVALID
    assert "not clean" in outcome.reason
    assert agent.workspaces == []
