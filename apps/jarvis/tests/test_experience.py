import hashlib
import json
import sqlite3

import pytest

from arda.core import Database
from arda.experience import EvaluationMemory, EvaluationRecord, EvaluationRecordError, SQLiteEvaluationStore


def record(evaluation_id="eval-1", *, outcome="VERIFIED", completion="DONE", latency=100):
    values = {
        "evaluation_id": evaluation_id,
        "episode_id": f"episode-{evaluation_id}",
        "provider_id": "local-a",
        "capability_id": "coding.review",
        "model_snapshot": "qwen3:1.7b@digest",
        "route_decision_hash": "decision-hash",
        "provider_result_id": f"provider-{evaluation_id}",
        "output_artifact_id": f"artifact-{evaluation_id}",
        "verification_hash": "verification-hash",
        "outcome_status": outcome,
        "completion_status": completion,
        "latency_ms": latency,
        "token_usage": {"output_tokens": 7},
        "human_intervention": completion == "NEEDS_HUMAN",
        "failure_class": None,
    }
    canonical_hash = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return EvaluationRecord(canonical_hash=canonical_hash, **values)


def test_evaluation_records_are_append_only_and_projection_is_derived(tmp_path):
    database = Database(tmp_path / "evaluation.sqlite3")
    database.migrate()
    try:
        store = SQLiteEvaluationStore(database)
        first = store.append(record())
        store.append(record("eval-2", outcome="INCONCLUSIVE", completion="INCOMPLETE", latency=200))
        assert store.get(first.evaluation_id) == first
        aggregate = EvaluationMemory(store.list()).aggregate("local-a", "coding.review")
        assert aggregate.sample_count == 2
        assert aggregate.verified_count == 1
        assert aggregate.completed_count == 1
        assert aggregate.inconclusive_count == 1
        assert aggregate.average_latency_ms == 150
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("UPDATE evaluation_records SET outcome_status='FAILED' WHERE evaluation_id=?", (first.evaluation_id,))
    finally:
        database.close()


def test_tampered_evaluation_hash_is_detected(tmp_path):
    database = Database(tmp_path / "evaluation-tamper.sqlite3")
    database.migrate()
    try:
        store = SQLiteEvaluationStore(database)
        item = record()
        store.append(item)
        database.execute("DROP TRIGGER evaluation_records_no_update")
        database.execute("UPDATE evaluation_records SET outcome_status='FAILED' WHERE evaluation_id=?", (item.evaluation_id,))
        with pytest.raises(EvaluationRecordError, match="hash mismatch"):
            store.get(item.evaluation_id)
    finally:
        database.close()
