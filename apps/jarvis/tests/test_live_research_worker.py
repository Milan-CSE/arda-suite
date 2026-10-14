import json

from arda.core import Database, JobQueue
from arda.live_research import FetchedSource, SearchCandidate
from arda.live_research_worker import LiveResearchWorker
from arda.research_synthesis import ModelResponse
from arda.worker_protocol import InMemoryWorkerTransport, WorkerProtocolClient, WorkerProtocolServer
from arda.worker_runtime import WorkerRegistry


class SearchFixture:
    def search(self, query, *, max_results):
        return [SearchCandidate("fixture", query, 1, "Fixture", "https://example.com/source", "snippet", "2026-10-01T00:00:00Z", {})]


class FetchFixture:
    def fetch(self, url):
        return FetchedSource(url, url, 200, "text/html", "2026-10-01T00:00:00Z", "Arda validates claims against evidence.", "hash")


class ModelFixture:
    def generate(self, **kwargs):
        pack = json.loads(kwargs["user_prompt"].split("\n", 1)[1].rsplit("\n", 1)[0])["evidence_pack"]
        evidence_id = pack["entries"][0]["evidence_id"]
        return ModelResponse(json.dumps({"status": "OK", "claims": [{"claim_id": "c1", "text": "Arda validates claims against evidence.", "evidence_ids": [evidence_id]}], "conflicts": []}), "fixture", 10, 5)


def test_live_worker_publishes_only_validated_research(tmp_path):
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    WorkerRegistry(db).register("live-worker", "live-secret", public_key="fixture", capabilities=["research"])
    objective = json.dumps({
        "query": "Arda evidence",
        "research_questions": ["What does the source establish?"],
        "source_constraints": {"allowed_source_types": ["live_http"]},
        "max_sources": 1,
        "time_budget_seconds": 30,
        "required_output_format": ["research.json", "research.md"],
        "expected_claims": [],
    })
    job = JobQueue(db).enqueue("research", objective, idempotency_key="live-research-fixture")
    client = WorkerProtocolClient("live-worker", "live-secret", InMemoryWorkerTransport(WorkerProtocolServer(db)))
    result = LiveResearchWorker(client, SearchFixture(), FetchFixture(), ModelFixture(), model="fixture").run_once(run_id="live-fixture")
    assert result["synthesis"]["claims"][0]["evidence_ids"]
    assert JobQueue(db).get(job.id).status == "succeeded"
    assert db.scalar("SELECT COUNT(*) FROM artifacts WHERE job_id=? AND verified=1", (job.id,)) == 2
    db.close()
