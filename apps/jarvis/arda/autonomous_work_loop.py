"""Bounded, evidence-first autonomous coding work loop.

This module is deliberately an orchestration boundary, not an LLM client.  A
Hermes runner and an independent evaluator are injected so the queue can be
tested without credentials or a live provider.  The loop owns the safety
invariants: explicit baseline, disposable git worktree, argv-only commands,
scope audit, final verification, durable evidence, and cleanup.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from arda.core import EventLog, JobQueue, _utc_now


class WorkLoopError(RuntimeError):
    """The work loop cannot safely start or finalize a task."""


class WorkResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INVALID = "INVALID"
    SKIP_ALREADY_SATISFIED = "SKIP_ALREADY_SATISFIED"


@dataclass(frozen=True)
class WorkTask:
    """Persistable contract for one queue item.

    ``baseline_sha`` is mandatory: a symbolic ref alone is not a frozen
    baseline.  Paths are repository-relative POSIX paths or glob patterns.
    """

    task_id: str
    repo: str
    baseline_ref: str
    baseline_sha: str
    objective: str
    acceptance: tuple[str, ...]
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    provider: str = "openai-codex"
    model: str = "gpt-5.6-luna"
    reasoning: str = "medium"
    max_turns: int = 50
    soft_budget_seconds: int = 780
    hard_timeout_seconds: int = 900
    risk: str = "low"
    task_type: str = "maintenance"
    affected_subsystem: str = ""

    def validate(self) -> None:
        if not self.task_id or "/" in self.task_id or "\\" in self.task_id or not self.repo or not self.baseline_ref or not self.baseline_sha:
            raise WorkLoopError("task identity, repo, baseline_ref and baseline_sha are required")
        if len(self.baseline_sha) != 40 or any(char not in "0123456789abcdef" for char in self.baseline_sha.lower()):
            raise WorkLoopError("baseline_sha must be a hexadecimal commit hash")
        if self.baseline_ref.startswith("-"):
            raise WorkLoopError("baseline_ref must not be an option")
        if not self.objective.strip() or not self.acceptance:
            raise WorkLoopError("objective and acceptance criteria are required")
        if self.max_turns <= 0 or self.soft_budget_seconds <= 0 or self.hard_timeout_seconds <= 0:
            raise WorkLoopError("runtime limits must be positive")
        if self.soft_budget_seconds >= self.hard_timeout_seconds:
            raise WorkLoopError("soft budget must be below the hard timeout")

    def canonical(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["acceptance"] = list(self.acceptance)
        payload["allowed_paths"] = list(self.allowed_paths)
        payload["forbidden_paths"] = list(self.forbidden_paths)
        return payload

    @classmethod
    def from_canonical(cls, payload: Mapping[str, Any]) -> "WorkTask":
        # Organization metadata may carry private control-plane keys alongside
        # the execution contract. They must never become constructor fields.
        data = {key: value for key, value in dict(payload).items() if not key.startswith("_")}
        for name in ("acceptance", "allowed_paths", "forbidden_paths"):
            data[name] = tuple(data.get(name, ()))
        task = cls(**data)
        task.validate()
        return task

    @property
    def contract_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class AgentReport:
    status: str
    final_claim: str = ""
    tool_calls: int = 0
    trace: tuple[dict[str, Any], ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class EvaluationReport:
    passed: bool
    checks: tuple[dict[str, Any], ...] = ()
    error: str | None = None


class AgentRunner(Protocol):
    def run(self, task: WorkTask, workspace: Path) -> AgentReport: ...


class IndependentEvaluator(Protocol):
    def evaluate(self, task: WorkTask, workspace: Path) -> EvaluationReport: ...


class HostHealth(Protocol):
    def __call__(self) -> bool: ...


class SubprocessHermesRunner:
    """Run a caller-supplied Hermes CLI without a shell.

    The command builder is intentionally explicit because Hermes installation
    paths differ between hosts.  The loop still owns cwd, timeout, and a
    deny-list for actions that must never be part of v0.1.
    """

    _FORBIDDEN = {"push", "merge", "deploy", "sudo", "production", "--system"}

    def __init__(self, command_builder: Callable[[WorkTask, Path], Sequence[str]], *, env: Mapping[str, str] | None = None):
        self.command_builder = command_builder
        self.env = dict(env or {})

    def run(self, task: WorkTask, workspace: Path) -> AgentReport:
        command = [str(part) for part in self.command_builder(task, workspace)]
        if not command or any(not part for part in command):
            raise WorkLoopError("Hermes command must be a non-empty argv")
        if any(part.lower() in self._FORBIDDEN for part in command):
            raise WorkLoopError("Hermes command contains a forbidden action")
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command, cwd=workspace, env={**os.environ, **self.env},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                stdin=subprocess.DEVNULL, start_new_session=(os.name == "posix"),
            )
            try:
                stdout, stderr = process.communicate(timeout=task.hard_timeout_seconds)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                else:
                    process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    else:
                        process.kill()
                    stdout, stderr = process.communicate()
                return AgentReport("timeout", error=f"hard timeout after {task.hard_timeout_seconds}s",
                                   trace=({"event": "timeout", "elapsed_seconds": time.monotonic() - started},))
            completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        except OSError as exc:
            raise WorkLoopError(f"Hermes process could not start: {exc}") from exc
        trace = ({
            "event": "hermes.process",
            "exit_code": completed.returncode,
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
            "duration_seconds": time.monotonic() - started,
        },)
        return AgentReport(
            "completed" if completed.returncode == 0 else "failed",
            final_claim=completed.stdout[-1000:],
            trace=trace,
            error=None if completed.returncode == 0 else (completed.stderr[-1000:] or "Hermes exited non-zero"),
        )


class CommandEvaluator:
    """Independent argv-only evaluator; every command must pass."""

    def __init__(self, commands: Sequence[Sequence[str]], *, timeout_seconds: int = 300):
        self.commands = tuple(tuple(str(part) for part in command) for command in commands)
        self.timeout_seconds = timeout_seconds

    def evaluate(self, task: WorkTask, workspace: Path) -> EvaluationReport:
        checks: list[dict[str, Any]] = []
        for command in self.commands:
            if not command or any(not part for part in command):
                return EvaluationReport(False, tuple(checks), "empty evaluator command")
            completed = subprocess.run(list(command), cwd=workspace, capture_output=True, text=True,
                                       timeout=self.timeout_seconds, check=False)
            checks.append({
                "command": list(command),
                "exit_code": completed.returncode,
                "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
            })
            if completed.returncode != 0:
                return EvaluationReport(False, tuple(checks), f"evaluator command failed: {command[0]}")
        return EvaluationReport(True, tuple(checks))


@dataclass(frozen=True)
class WorkOutcome:
    task_id: str
    job_id: str | None
    result: WorkResult
    reason: str
    duration_seconds: float
    workspace: str
    agent: AgentReport | None = None
    evaluation: EvaluationReport | None = None
    changed_paths: tuple[str, ...] = ()
    cleanup_ok: bool = True
    evidence_artifact_id: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "job_id": self.job_id,
            "result": self.result.value,
            "reason": self.reason,
            "duration_seconds": self.duration_seconds,
            "workspace": self.workspace,
            "agent": asdict(self.agent) if self.agent else None,
            "evaluation": asdict(self.evaluation) if self.evaluation else None,
            "changed_paths": list(self.changed_paths),
            "cleanup_ok": self.cleanup_ok,
        }


class GitWorktree:
    """Create/remove only worktrees below a caller-owned disposable root."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def create(self, task: WorkTask) -> Path:
        repo = Path(task.repo).expanduser().resolve()
        if not (repo / ".git").exists() and not (repo / "HEAD").exists():
            raise WorkLoopError(f"repository is not a git checkout: {repo}")
        self.root.mkdir(parents=True, exist_ok=True)
        original_status = self._git(repo, "status", "--porcelain", "--untracked-files=all")
        if original_status.strip():
            raise WorkLoopError("original checkout is not clean")
        workspace = Path(tempfile.mkdtemp(prefix=f"{task.task_id}-", dir=self.root)).resolve()
        workspace.rmdir()
        self._git(repo, "worktree", "add", "--detach", str(workspace), task.baseline_ref)
        actual = self._git(repo, "-C", str(workspace), "rev-parse", "HEAD").strip()
        if actual != task.baseline_sha:
            self.remove(repo, workspace)
            raise WorkLoopError(f"baseline mismatch: expected {task.baseline_sha}, got {actual}")
        status = self._git(repo, "-C", str(workspace), "status", "--porcelain", "--untracked-files=all")
        if status.strip():
            self.remove(repo, workspace)
            raise WorkLoopError("fresh worktree is not clean")
        return workspace

    def remove(self, repo: Path, workspace: Path) -> None:
        root = self.root
        try:
            workspace.resolve().relative_to(root)
        except ValueError as exc:
            raise WorkLoopError("refusing to remove a path outside disposable worktree root") from exc
        if workspace.exists():
            self._git(repo, "worktree", "remove", "--force", str(workspace))

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        command = ["git", *args]
        completed = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=30, check=False)
        if completed.returncode != 0:
            raise WorkLoopError(completed.stderr.strip() or "git command failed")
        return completed.stdout


class AutonomousWorkLoop:
    """Process a finite queue; stop safely on host or infrastructure failure."""

    def __init__(
        self,
        db: Any,
        *,
        worker_id: str,
        workspace_root: str | Path,
        agent: AgentRunner,
        evaluator: IndependentEvaluator,
        host_health: HostHealth | None = None,
        clock: Callable[[], Any] = _utc_now,
    ):
        self.queue = JobQueue(db, clock=clock)
        self.events = EventLog(db, clock=clock)
        self.worker_id = worker_id
        self.agent = agent
        self.evaluator = evaluator
        self.host_health = host_health or (lambda: True)
        self.worktrees = GitWorktree(workspace_root)
        self._contracts: dict[str, WorkTask] = {}

    def enqueue(self, task: WorkTask):
        task.validate()
        job = self.queue.enqueue(
            "autonomous-coding",
            task.objective,
            idempotency_key=task.task_id,
            acceptance_criteria=list(task.acceptance),
            context_manifest=task.canonical(),
            policy_snapshot={
                "provider": task.provider,
                "model": task.model,
                "reasoning": task.reasoning,
                "max_turns": task.max_turns,
                "soft_budget_seconds": task.soft_budget_seconds,
                "hard_timeout_seconds": task.hard_timeout_seconds,
            },
            max_attempts=1,
            timeout_seconds=task.hard_timeout_seconds,
        )
        self._contracts[job.id] = task
        return job

    def run_queue(self, tasks: Mapping[str, WorkTask], *, limit: int | None = None) -> tuple[WorkOutcome, ...]:
        outcomes: list[WorkOutcome] = []
        while limit is None or len(outcomes) < limit:
            if not self.host_health():
                self.events.append("work_loop.host_unavailable", source="work-loop", source_id=self.worker_id)
                break
            try:
                job = self.queue.claim(self.worker_id, job_type="autonomous-coding")
            except LookupError:
                break
            task = tasks.get(job.id) or self._contracts.get(job.id) or tasks.get(job.id.removeprefix("job_"))
            if task is None:
                try:
                    manifest = self.queue.describe(job.id, job.lease_token)
                    task = WorkTask.from_canonical(manifest["context_manifest"])
                except Exception:
                    task = None
            if task is None:
                outcome = self._invalid_job(job.id, job.lease_token, "task contract is not available")
                outcomes.append(outcome)
                continue
            outcomes.append(self._run_one(job.id, job.lease_token, task))
        return tuple(outcomes)

    def _run_one(self, job_id: str, lease: str | None, task: WorkTask) -> WorkOutcome:
        started = time.monotonic()
        workspace: Path | None = None
        agent_report: AgentReport | None = None
        evaluation: EvaluationReport | None = None
        changed: tuple[str, ...] = ()
        result = WorkResult.INVALID
        reason = "not_started"
        artifact_id: str | None = None
        terminalized = False
        try:
            if lease is None:
                raise WorkLoopError("job lease missing")
            self.queue.start(job_id, lease)
            self.events.append("work_loop.task.started", source="work-loop", source_id=job_id,
                               payload={"task_id": task.task_id, "contract_hash": task.contract_hash})
            task.validate()
            if not self.host_health():
                raise WorkLoopError("host health gate failed")
            workspace = self.worktrees.create(task)
            agent_report = self.agent.run(task, workspace)
            if agent_report.forbidden_actions:
                reason = "forbidden agent action reported"
                result = WorkResult.INVALID
            else:
                changed = self._changed_paths(task, workspace)
                scope_error = self._scope_error(task, changed)
                if scope_error:
                    reason = scope_error
                    result = WorkResult.FAIL
                else:
                    evaluation = self.evaluator.evaluate(task, workspace)
                    # The evaluator is the authority; the agent's claim is never
                    # used to promote a task to PASS.
                    result = WorkResult.PASS if evaluation.passed else WorkResult.FAIL
                    reason = "independent evaluation passed" if evaluation.passed else (evaluation.error or "acceptance failed")
            payload = self._evidence(task, agent_report, evaluation, result, reason, changed)
            content = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode()
            artifact_id = self.queue.record_artifact(job_id, "work-loop-evidence.json", f"{task.task_id}/evidence.json",
                                                     hashlib.sha256(content).hexdigest(), content=content, lease_token=lease)
        except Exception as exc:  # infrastructure failures are invalid, never success
            reason = str(exc)
            result = WorkResult.INVALID
            if lease:
                try:
                    self.queue.fail(job_id, lease, error=reason, retryable=False)
                except Exception:
                    pass
        finally:
            cleanup_ok = True
            if workspace is not None:
                try:
                    self.worktrees.remove(Path(task.repo).expanduser().resolve(), workspace)
                except Exception as exc:
                    cleanup_ok = False
                    result = WorkResult.INVALID
                    reason = f"cleanup failed: {exc}"
                    self.events.append("work_loop.cleanup_failed", source="work-loop", source_id=job_id,
                                       payload={"error": str(exc)})
            if not cleanup_ok:
                result = WorkResult.INVALID
                reason = reason if reason.startswith("cleanup failed:") else "cleanup failed"
            if lease and not terminalized:
                try:
                    if result is WorkResult.PASS and cleanup_ok:
                        self.queue.complete(job_id, lease, result={"result": result.value, "reason": reason})
                    else:
                        self.queue.fail(job_id, lease, error=reason, retryable=False)
                    terminalized = True
                except Exception:
                    pass
        return WorkOutcome(task.task_id, job_id, result, reason, time.monotonic() - started,
                           str(workspace) if workspace else "", agent_report, evaluation, changed, cleanup_ok, artifact_id)

    def _invalid_job(self, job_id: str, lease: str | None, reason: str) -> WorkOutcome:
        if lease:
            self.queue.fail(job_id, lease, error=reason, retryable=False)
        return WorkOutcome(job_id, job_id, WorkResult.INVALID, reason, 0.0, "")

    @staticmethod
    def _changed_paths(task: WorkTask, workspace: Path) -> tuple[str, ...]:
        completed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=workspace,
                                   capture_output=True, text=True, timeout=30, check=False)
        if completed.returncode != 0:
            raise WorkLoopError(completed.stderr.strip() or "git status failed")
        paths: set[str] = set()
        for line in completed.stdout.splitlines():
            value = line[3:] if len(line) >= 4 else ""
            if " -> " in value:
                value = value.split(" -> ", 1)[1]
            if value:
                paths.add(value)
        return tuple(sorted(paths))

    @staticmethod
    def _scope_error(task: WorkTask, changed: Sequence[str]) -> str | None:
        if any(any(fnmatch.fnmatch(path, pattern) for pattern in task.forbidden_paths) for path in changed):
            return "forbidden path changed"
        if task.allowed_paths and any(not any(fnmatch.fnmatch(path, pattern) for pattern in task.allowed_paths) for path in changed):
            return "unrelated path changed"
        return None

    @staticmethod
    def _evidence(task, agent, evaluation, result, reason, changed):
        return {
            "schema": "arda-autonomous-work-loop-evidence-v0.1",
            "task": task.canonical(),
            "contract_hash": task.contract_hash,
            "result": result.value,
            "reason": reason,
            "changed_paths": list(changed),
            "agent": asdict(agent) if agent else None,
            "evaluation": asdict(evaluation) if evaluation else None,
        }
