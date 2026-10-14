"""M2 Worker Runtime: authenticated deterministic execution tests."""

import hashlib
import sys
from pathlib import Path

import pytest

from arda.core import Database, JobQueue, StaleLeaseError
from arda.worker_runtime import WorkerAuthError, WorkerRegistry, WorkerRuntime


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def worker(db):
    return WorkerRegistry(db).register(
        "mac-primary", "worker-secret", public_key="mac-public-key", capabilities=["pytest"]
    )


def test_worker_authentication_and_heartbeat_are_token_bound(db, worker):
    registry = WorkerRegistry(db)
    assert registry.authenticate("mac-primary", "worker-secret")["id"] == "mac-primary"

    with pytest.raises(WorkerAuthError):
        registry.authenticate("mac-primary", "wrong-secret")

    heartbeat = registry.heartbeat("mac-primary", "worker-secret")
    assert heartbeat["status"] == "online"
    assert heartbeat["last_seen_at"] is not None


def test_worker_token_rotation_invalidates_old_token_without_logging_secret(db, worker):
    registry = WorkerRegistry(db)
    registry.rotate_token("mac-primary", "worker-secret-v2")

    with pytest.raises(WorkerAuthError):
        registry.authenticate("mac-primary", "worker-secret")
    assert registry.authenticate("mac-primary", "worker-secret-v2")["id"] == "mac-primary"

    events = db.execute("SELECT type, payload FROM events WHERE source_id='mac-primary'").fetchall()
    assert any(row["type"] == "worker.credentials.rotated" for row in events)
    assert all(secret not in row["payload"] for secret in ("worker-secret", "worker-secret-v2")
               for row in events)


def test_worker_revoke_invalidates_credential_and_revoked_worker_cannot_rotate(db, worker):
    registry = WorkerRegistry(db)
    registry.revoke("mac-primary")

    with pytest.raises(WorkerAuthError, match="revoked"):
        registry.authenticate("mac-primary", "worker-secret")
    with pytest.raises(WorkerAuthError, match="revoked"):
        registry.rotate_token("mac-primary", "worker-secret-v2")


def test_worker_runtime_completes_test_job_with_verified_artifact_and_cleanup(db, worker, tmp_path):
    queue = JobQueue(db)
    job = queue.enqueue("test", "Run fixture pytest", idempotency_key="m2-success")
    runtime = WorkerRuntime(db, workspace_root=tmp_path / "workspaces")

    result = runtime.run_once(
        "mac-primary",
        "worker-secret",
        command=[sys.executable, "-c", "print('pytest fixture passed')"],
    )

    assert result.job_id == job.id
    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.cleanup_ok is True
    assert not Path(result.workspace).exists()
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
    assert row["status"] == "succeeded"
    artifact = db.execute("SELECT * FROM artifacts WHERE job_id=?", (job.id,)).fetchone()
    assert artifact["verified"] == 1
    assert artifact["sha256"] == hashlib.sha256(result.report).hexdigest()


def test_worker_runtime_timeout_fails_job_and_cleans_workspace(db, worker, tmp_path):
    queue = JobQueue(db)
    job = queue.enqueue("test", "Timeout fixture", idempotency_key="m2-timeout")
    runtime = WorkerRuntime(db, workspace_root=tmp_path / "workspaces")

    result = runtime.run_once(
        "mac-primary",
        "worker-secret",
        command=[sys.executable, "-c", "import time; time.sleep(1)"],
        timeout_seconds=0.05,
    )

    assert result.job_id == job.id
    assert result.status == "failed"
    assert result.cleanup_ok is True
    assert not Path(result.workspace).exists()
    assert db.scalar("SELECT status FROM jobs WHERE id=?", (job.id,)) == "failed"


def test_wrong_worker_token_cannot_claim_job(db, worker, tmp_path):
    queue = JobQueue(db)
    job = queue.enqueue("test", "Protected fixture", idempotency_key="m2-auth")
    runtime = WorkerRuntime(db, workspace_root=tmp_path / "workspaces")

    with pytest.raises(WorkerAuthError):
        runtime.run_once("mac-primary", "wrong-secret", command=[sys.executable, "-c", "pass"])

    assert queue.get(job.id).status == "queued"


def test_cleanup_failure_is_audited_after_success(db, worker, tmp_path):
    queue = JobQueue(db)
    job = queue.enqueue("test", "Cleanup fixture", idempotency_key="m2-cleanup")

    def broken_cleanup(_workspace):
        raise OSError("workspace busy")

    runtime = WorkerRuntime(
        db,
        workspace_root=tmp_path / "workspaces",
        cleanup=broken_cleanup,
    )
    result = runtime.run_once(
        "mac-primary", "worker-secret", command=[sys.executable, "-c", "pass"]
    )

    assert result.status == "succeeded"
    assert result.cleanup_ok is False
    assert db.scalar("SELECT status FROM jobs WHERE id=?", (job.id,)) == "succeeded"
    assert db.scalar("SELECT COUNT(*) FROM events WHERE type='worker.cleanup.failed'") == 1


def test_workspace_setup_failure_does_not_leave_job_running(db, worker, tmp_path):
    queue = JobQueue(db)
    job = queue.enqueue("test", "Missing fixture", idempotency_key="m2-setup-failure")
    runtime = WorkerRuntime(db, workspace_root=tmp_path / "workspaces")

    with pytest.raises(ValueError, match="source directory"):
        runtime.run_once(
            "mac-primary",
            "worker-secret",
            command=[sys.executable, "-c", "pass"],
            source_dir=tmp_path / "does-not-exist",
        )

    assert db.scalar("SELECT status FROM jobs WHERE id=?", (job.id,)) == "failed"


def test_queue_still_fences_duplicate_completion(db, worker):
    queue = JobQueue(db)
    job = queue.enqueue("test", "Fence fixture", idempotency_key="m2-fence")
    lease = queue.claim("mac-primary")
    queue.start(job.id, lease.lease_token)
    report = b"report"
    digest = hashlib.sha256(report).hexdigest()
    queue.record_artifact(job.id, "test-report.json", "test-report.json", digest, content=report)
    queue.complete(job.id, lease.lease_token, result={"ok": True})

    with pytest.raises(StaleLeaseError):
        queue.complete(job.id, lease.lease_token, result={"duplicate": True})
