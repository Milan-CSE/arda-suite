"""M2.3A deterministic research worker contract and failure-first tests."""

import hashlib
import json

import pytest

from arda.core import Database, JobQueue
from arda.research_worker import (
    DeterministicResearchWorker,
    FixtureSourceAdapter,
    ResearchContractError,
    ResearchBudgetError,
    ResearchSourceError,
    build_research,
    canonical_json,
    render_markdown,
    validate_research_result,
    ResearchTask,
)
from arda.worker_protocol import InMemoryWorkerTransport, WorkerProtocolClient, WorkerProtocolServer
from arda.worker_runtime import WorkerRegistry


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    WorkerRegistry(database).register(
        "research-fixture", "research-secret", public_key="fixture", capabilities=["research"]
    )
    try:
        yield database
    finally:
        database.close()


def _objective(*, source_refs=("fixture://sqlite",), expected_claims=None):
    return json.dumps({
        "research_questions": ["What does the source establish?"],
        "source_refs": list(source_refs),
        "source_constraints": {"allowed_source_types": ["fixture"]},
        "max_sources": len(source_refs),
        "time_budget_seconds": 30,
        "required_output_format": ["research.json", "research.md"],
        "expected_claims": expected_claims or [],
    }, sort_keys=True)


def _worker(db, adapter):
    server = WorkerProtocolServer(db)
    client = WorkerProtocolClient("research-fixture", "research-secret", InMemoryWorkerTransport(server))
    return DeterministicResearchWorker(client, adapter)


def test_fixture_pipeline_produces_claim_evidence_and_two_artifacts(db):
    queue = JobQueue(db)
    objective = _objective(expected_claims=[
        {"claim_id": "wal", "text": "SQLite WAL mode preserves durable state."},
        {"claim_id": "missing", "text": "The source proves live browsing."},
    ])
    job = queue.enqueue("research", objective, idempotency_key="research-fixture-success")
    adapter = FixtureSourceAdapter({
        "fixture://sqlite": {
            "source_id": "src-sqlite", "source_type": "fixture", "uri": "fixture://sqlite",
            "retrieved_at": "2026-10-01T00:00:00Z", "title": "SQLite fixture",
            "content": "SQLite WAL mode preserves durable state. Lease fencing rejects stale workers.",
            "provenance": {"fixture": "m2.3a"},
        }
    })

    result = _worker(db, adapter).run_once(run_id="research-success")

    assert queue.get(job.id).status == "succeeded"
    assert result["claims"][0]["support_status"] == "supported"
    assert result["claims"][0]["evidence_ids"]
    assert result["claims"][1]["support_status"] == "not_found"
    assert len(db.execute("SELECT id FROM artifacts WHERE job_id=?", (job.id,)).fetchall()) == 2
    assert canonical_json(result) == canonical_json(json.loads(canonical_json(result)))
    assert b"not found" in render_markdown(result).lower()


def test_research_job_is_filtered_from_non_research_claims(db):
    JobQueue(db).enqueue("test", "not research", idempotency_key="non-research")
    JobQueue(db).enqueue("research", _objective(), idempotency_key="research-filter")
    server = WorkerProtocolServer(db)
    client = WorkerProtocolClient("research-fixture", "research-secret", InMemoryWorkerTransport(server))
    claimed = client.request("claim", {"job_type": "research"}, request_id="research-filter-claim")
    assert claimed["job_id"]


def test_missing_source_is_explicit_failure_without_artifacts(db):
    queue = JobQueue(db)
    job = queue.enqueue("research", _objective(source_refs=("fixture://missing",)), idempotency_key="research-missing")
    with pytest.raises(ResearchSourceError):
        _worker(db, FixtureSourceAdapter({})).run_once(run_id="research-missing-run")
    assert queue.get(job.id).status == "retry_wait"
    assert db.scalar("SELECT COUNT(*) FROM artifacts WHERE job_id=?", (job.id,)) == 0


def test_partial_source_set_is_explicit_failure_without_partial_artifacts(db):
    queue = JobQueue(db)
    job = queue.enqueue(
        "research", _objective(source_refs=("fixture://sqlite", "fixture://missing")),
        idempotency_key="research-partial",
    )
    adapter = FixtureSourceAdapter({
        "fixture://sqlite": {
            "source_id": "src-sqlite", "source_type": "fixture", "uri": "fixture://sqlite",
            "retrieved_at": "2026-10-01T00:00:00Z", "content": "A valid fixture sentence.",
        }
    })
    with pytest.raises(ResearchSourceError, match="source unavailable"):
        _worker(db, adapter).run_once(run_id="research-partial-run")
    assert queue.get(job.id).status == "retry_wait"
    assert db.scalar("SELECT COUNT(*) FROM artifacts WHERE job_id=?", (job.id,)) == 0


def test_malformed_source_and_budget_exhaustion_are_explicit_failures():
    task = ResearchTask.from_objective(_objective())
    with pytest.raises(ResearchSourceError, match="malformed source"):
        build_research(task, FixtureSourceAdapter({"fixture://sqlite": {"source_id": "broken"}}))

    ticks = iter((0.0, 31.0))
    with pytest.raises(ResearchBudgetError):
        build_research(
            task,
            FixtureSourceAdapter({"fixture://sqlite": {
                "source_id": "src", "source_type": "fixture", "uri": "fixture://sqlite",
                "retrieved_at": "2026-10-01T00:00:00Z", "content": "A sentence.",
            }}),
            clock=lambda: next(ticks),
        )


def test_supported_claim_without_evidence_is_rejected():
    with pytest.raises(ResearchContractError):
        validate_research_result({"claims": [{"claim_id": "x", "support_status": "supported", "evidence_ids": []}]})


def test_same_verified_artifact_identity_is_deduplicated(db):
    queue = JobQueue(db)
    job = queue.enqueue("research", _objective(), idempotency_key="research-artifact-dedupe")
    lease = queue.claim("research-fixture")
    queue.start(job.id, lease.lease_token)
    content = b"research artifact"
    digest = hashlib.sha256(content).hexdigest()
    first = queue.record_artifact(job.id, "research.json", "research.json", digest, content=content, lease_token=lease.lease_token)
    second = queue.record_artifact(job.id, "research.json", "research.json", digest, content=content, lease_token=lease.lease_token)
    assert first == second
    assert db.scalar("SELECT COUNT(*) FROM artifacts WHERE job_id=?", (job.id,)) == 1
