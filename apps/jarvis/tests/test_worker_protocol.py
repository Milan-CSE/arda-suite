"""M2.1 protocol contract and in-memory distributed failure tests."""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from arda.core import Database, JobQueue, StaleLeaseError
from arda.worker_protocol import (
    InMemoryWorkerTransport,
    ProtocolAuthenticationError,
    ProtocolReplayConflictError,
    ProtocolUnavailableError,
    WorkerProtocolClient,
    WorkerProtocolServer,
)
from arda.worker_runtime import WorkerRegistry


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    WorkerRegistry(database).register(
        "mac-primary", "worker-secret", public_key="mac-public-key", capabilities=["pytest"]
    )
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def protocol(db):
    server = WorkerProtocolServer(db)
    transport = InMemoryWorkerTransport(server)
    client = WorkerProtocolClient("mac-primary", "worker-secret", transport)
    return server, transport, client


def test_signed_authenticated_claim_and_start_lifecycle(db, protocol):
    _server, _transport, client = protocol
    job = JobQueue(db).enqueue("test", "remote fixture", idempotency_key="protocol-claim")

    claimed = client.request("claim", request_id="req-claim")
    assert claimed["job_id"] == job.id
    assert claimed["worker_id"] == "mac-primary"
    started = client.request(
        "start", {"job_id": job.id, "lease_token": claimed["lease_token"]}, request_id="req-start"
    )
    assert started["status"] == "running"


def test_invalid_authentication_and_signature_never_claim_job(db, protocol):
    _server, _transport, client = protocol
    job = JobQueue(db).enqueue("test", "protected", idempotency_key="protocol-auth")

    client.auth_token = "wrong-secret"
    with pytest.raises(ProtocolAuthenticationError):
        client.request("claim", request_id="req-bad-auth")
    client.auth_token = "worker-secret"
    with pytest.raises(ProtocolAuthenticationError):
        client.request("claim", request_id="req-good-auth", tamper_signature=True)

    assert JobQueue(db).get(job.id).status == "queued"


def test_replay_returns_same_response_and_payload_conflict_is_rejected(db, protocol):
    _server, _transport, client = protocol
    job = JobQueue(db).enqueue("test", "replay", idempotency_key="protocol-replay")

    first = client.request("claim", request_id="req-replay")
    second = client.request("claim", request_id="req-replay")
    assert second == first
    assert db.scalar("SELECT COUNT(*) FROM jobs WHERE status='leased'") == 1

    with pytest.raises(ProtocolReplayConflictError):
        client.request("claim", {"unexpected": True}, request_id="req-replay")


def test_partition_retry_preserves_request_semantics(db, protocol):
    _server, transport, client = protocol
    JobQueue(db).enqueue("test", "partition", idempotency_key="protocol-partition")
    transport.available = False

    with pytest.raises(ProtocolUnavailableError):
        client.request("claim", request_id="req-partition")

    transport.available = True
    first = client.request("claim", request_id="req-partition")
    second = client.request("claim", request_id="req-partition")
    assert second == first


def test_artifact_complete_and_duplicate_completion_are_fenced(db, protocol):
    _server, _transport, client = protocol
    queue = JobQueue(db)
    job = queue.enqueue("test", "artifact", idempotency_key="protocol-artifact")
    claimed = client.request("claim", request_id="req-artifact-claim")
    client.request(
        "start", {"job_id": job.id, "lease_token": claimed["lease_token"]}, request_id="req-artifact-start"
    )
    content = b"protocol report"
    client.request(
        "upload_artifact",
        {
            "job_id": job.id,
            "lease_token": claimed["lease_token"],
            "kind": "test-report.json",
            "path_or_ref": "test-report.json",
            "content_b64": __import__("base64").b64encode(content).decode("ascii"),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        request_id="req-artifact-upload",
    )
    completed = client.request(
        "complete",
        {"job_id": job.id, "lease_token": claimed["lease_token"], "result": {"ok": True}},
        request_id="req-artifact-complete",
    )
    assert completed["status"] == "succeeded"
    assert client.request(
        "complete",
        {"job_id": job.id, "lease_token": claimed["lease_token"], "result": {"ok": True}},
        request_id="req-artifact-complete",
    ) == completed


def test_artifact_upload_requires_current_lease(db, protocol):
    _server, _transport, client = protocol
    job = JobQueue(db).enqueue("test", "artifact fence", idempotency_key="protocol-artifact-fence")
    claimed = client.request("claim", request_id="req-fence-claim")
    client.request(
        "start", {"job_id": job.id, "lease_token": claimed["lease_token"]}, request_id="req-fence-start"
    )

    with pytest.raises(StaleLeaseError):
        client.request(
            "upload_artifact",
            {
                "job_id": job.id,
                "lease_token": "stale-token",
                "kind": "test-report.json",
                "path_or_ref": "test-report.json",
                "content_b64": __import__("base64").b64encode(b"bad").decode("ascii"),
                "sha256": hashlib.sha256(b"bad").hexdigest(),
            },
            request_id="req-fence-upload",
        )
    assert db.scalar("SELECT COUNT(*) FROM artifacts WHERE job_id=?", (job.id,)) == 0
