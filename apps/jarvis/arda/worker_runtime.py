"""M2 authenticated worker runtime for deterministic test commands."""
from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from arda.core import Database, EventLog, JobQueue, _iso, _utc_now


class WorkerAuthError(PermissionError):
    """Worker identity or token is invalid."""


def _token_hash(token: str) -> str:
    if not token:
        raise ValueError("worker auth token must not be empty")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class WorkerRegistry:
    """Authenticated worker registration and heartbeat boundary."""

    def __init__(self, db: Database, *, clock=_utc_now):
        self.db = db
        self.clock = clock
        self.events = EventLog(db, clock)

    def register(
        self,
        worker_id: str,
        auth_token: str,
        *,
        public_key: str,
        capabilities: list[str] | None = None,
        trust_level: str = "sandboxed",
        metadata: dict[str, Any] | None = None,
    ):
        now = _iso(self.clock())
        token_hash = _token_hash(auth_token)
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO workers(
                    id, public_key, capabilities, trust_level, status,
                    registered_at, metadata, auth_token_hash
                ) VALUES (?, ?, ?, ?, 'registered', ?, ?, ?)""",
                (worker_id, public_key, json.dumps(capabilities or [], sort_keys=True),
                 trust_level, now, json.dumps(metadata or {}, sort_keys=True), token_hash),
            )
            self.events.append(
                "worker.registered",
                source="core",
                source_id=worker_id,
                payload={"capabilities": capabilities or [], "trust_level": trust_level},
            )
        return self.get(worker_id)

    def authenticate(self, worker_id: str, auth_token: str):
        row = self.db.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
        if row is None or not hmac.compare_digest(row["auth_token_hash"] or "", _token_hash(auth_token)):
            raise WorkerAuthError("worker authentication failed")
        if row["status"] == "revoked":
            raise WorkerAuthError("worker is revoked")
        return row

    def revoke(self, worker_id: str):
        """Invalidate one worker credential without affecting other workers."""
        row = self.get(worker_id)
        with self.db.transaction():
            self.db.execute("UPDATE workers SET status='revoked' WHERE id=?", (worker_id,))
            self.events.append("worker.revoked", source="core", source_id=worker_id)
        return self.get(row["id"])

    def rotate_token(self, worker_id: str, new_auth_token: str):
        """Replace one worker token; revoked workers cannot be silently reactivated."""
        token_hash = _token_hash(new_auth_token)
        row = self.get(worker_id)
        if row["status"] == "revoked":
            raise WorkerAuthError("cannot rotate a revoked worker")
        with self.db.transaction():
            self.db.execute(
                "UPDATE workers SET auth_token_hash=?, status='registered' WHERE id=?",
                (token_hash, worker_id),
            )
            self.events.append(
                "worker.credentials.rotated", source="core", source_id=worker_id
            )
        return self.get(row["id"])

    def heartbeat(self, worker_id: str, auth_token: str):
        self.authenticate(worker_id, auth_token)
        now = _iso(self.clock())
        with self.db.transaction():
            self.db.execute(
                "UPDATE workers SET status='online', last_seen_at=? WHERE id=?",
                (now, worker_id),
            )
            self.events.append(
                "worker.heartbeat",
                source="worker",
                source_id=worker_id,
            )
        return self.get(worker_id)

    def get(self, worker_id: str):
        row = self.db.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
        if row is None:
            raise KeyError(worker_id)
        return row


@dataclass(frozen=True)
class WorkerExecutionResult:
    job_id: str
    status: str
    workspace: str
    report: bytes
    exit_code: int | None
    artifact_id: str | None
    cleanup_ok: bool


class WorkerRuntime:
    """Run one leased test job with argv-only subprocess execution."""

    def __init__(
        self,
        db: Database,
        *,
        workspace_root: str | Path,
        clock: Callable[[], datetime] = _utc_now,
        cleanup: Callable[[Path], None] | None = None,
    ):
        self.db = db
        self.clock = clock
        self.workspace_root = Path(workspace_root).expanduser()
        self.cleanup = cleanup or (lambda path: shutil.rmtree(path))
        self.registry = WorkerRegistry(db, clock=clock)
        self.queue = JobQueue(db, clock=clock)
        self.events = EventLog(db, clock)

    def run_once(
        self,
        worker_id: str,
        auth_token: str,
        *,
        command: Sequence[str],
        source_dir: str | Path | None = None,
        timeout_seconds: float | None = None,
    ) -> WorkerExecutionResult:
        self.registry.authenticate(worker_id, auth_token)
        if not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError("command must be a non-empty argv sequence")
        job = self.queue.claim(worker_id)
        self.queue.start(job.id, job.lease_token)
        report = b""
        artifact_id: str | None = None
        exit_code: int | None = None
        status = "failed"
        cleanup_ok = True
        workspace: Path | None = None
        try:
            self.registry.heartbeat(worker_id, auth_token)
            self.workspace_root.mkdir(parents=True, exist_ok=True)
            workspace = Path(tempfile.mkdtemp(prefix=f"{job.id}-", dir=self.workspace_root))
            if source_dir is not None:
                self._copy_source(Path(source_dir), workspace)
            try:
                completed = subprocess.run(
                    list(command),
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
                exit_code = completed.returncode
                report = self._report(command, exit_code, completed.stdout, completed.stderr)
            except subprocess.TimeoutExpired as exc:
                report = self._report(command, None, self._text(exc.stdout), self._text(exc.stderr), "timeout")
            except OSError as exc:
                report = self._report(command, None, "", str(exc), exc.__class__.__name__)

            digest = hashlib.sha256(report).hexdigest()
            artifact_id = self.queue.record_artifact(
                job.id, "test-report.json", "test-report.json", digest, content=report
            )
            if exit_code == 0:
                self.queue.complete(job.id, job.lease_token, result={"exit_code": exit_code})
                status = "succeeded"
            else:
                error = "command timeout" if exit_code is None else f"command exited {exit_code}"
                self.queue.fail(job.id, job.lease_token, error=error, retryable=False)
                status = "failed"
        except Exception:
            # A setup/runtime failure must not strand a valid lease in running.
            # Preserve the original exception for the caller; cleanup is audited
            # independently in the finally block.
            try:
                self.queue.fail(job.id, job.lease_token, error="worker runtime failure", retryable=False)
            except Exception:
                pass
            raise
        finally:
            if workspace is not None:
                try:
                    self.cleanup(workspace)
                except Exception as exc:  # noqa: BLE001 - cleanup must be audited, not masked
                    cleanup_ok = False
                    self.events.append(
                        "worker.cleanup.failed",
                        source="worker",
                        source_id=job.id,
                        payload={"worker_id": worker_id, "error": str(exc)},
                    )
        return WorkerExecutionResult(job.id, status, str(workspace), report, exit_code, artifact_id, cleanup_ok)

    @staticmethod
    def _copy_source(source_dir: Path, workspace: Path) -> None:
        if not source_dir.is_dir():
            raise ValueError(f"source directory does not exist: {source_dir}")
        shutil.copytree(source_dir, workspace, dirs_exist_ok=True)

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @classmethod
    def _report(
        cls,
        command: Sequence[str],
        exit_code: int | None,
        stdout: Any,
        stderr: Any,
        error: str | None = None,
    ) -> bytes:
        payload = {
            "command": list(command),
            "exit_code": exit_code,
            "stdout": cls._text(stdout),
            "stderr": cls._text(stderr),
        }
        if error:
            payload["error"] = error
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
