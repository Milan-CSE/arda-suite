"""Immutable evaluation records and read-only experience projections."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


class EvaluationRecordError(ValueError):
    """An evaluation record violates provenance or append-only invariants."""


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvaluationRecord:
    evaluation_id: str
    episode_id: str
    provider_id: str
    capability_id: str
    model_snapshot: str
    route_decision_hash: str
    provider_result_id: str
    output_artifact_id: str
    verification_hash: str
    outcome_status: str
    completion_status: str
    latency_ms: int | None
    token_usage: Mapping[str, int]
    human_intervention: bool
    failure_class: str | None
    canonical_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "episode_id": self.episode_id,
            "provider_id": self.provider_id,
            "capability_id": self.capability_id,
            "model_snapshot": self.model_snapshot,
            "route_decision_hash": self.route_decision_hash,
            "provider_result_id": self.provider_result_id,
            "output_artifact_id": self.output_artifact_id,
            "verification_hash": self.verification_hash,
            "outcome_status": self.outcome_status,
            "completion_status": self.completion_status,
            "latency_ms": self.latency_ms,
            "token_usage": dict(sorted(self.token_usage.items())),
            "human_intervention": self.human_intervention,
            "failure_class": self.failure_class,
        }

    def verify_hash(self) -> bool:
        return self.canonical_hash == _hash(self.canonical_payload())


class SQLiteEvaluationStore:
    def __init__(self, db: Any):
        self.db = db
        if not db.table_exists("evaluation_records"):
            raise EvaluationRecordError("evaluation record migration is not current")

    def append(self, record: EvaluationRecord) -> EvaluationRecord:
        if not record.verify_hash():
            raise EvaluationRecordError("evaluation record hash mismatch")
        try:
            self.db.execute(
                """INSERT INTO evaluation_records(
                    evaluation_id, episode_id, provider_id, capability_id, model_snapshot,
                    route_decision_hash, provider_result_id, output_artifact_id,
                    verification_hash, outcome_status, completion_status, latency_ms,
                    token_usage_json, human_intervention, failure_class, canonical_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.evaluation_id, record.episode_id, record.provider_id, record.capability_id,
                    record.model_snapshot, record.route_decision_hash, record.provider_result_id,
                    record.output_artifact_id, record.verification_hash, record.outcome_status,
                    record.completion_status, record.latency_ms,
                    json.dumps(dict(sorted(record.token_usage.items())), sort_keys=True, separators=(",", ":")),
                    int(record.human_intervention), record.failure_class, record.canonical_hash,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise EvaluationRecordError("evaluation record identity conflict") from exc
        return record

    def get(self, evaluation_id: str) -> EvaluationRecord:
        row = self.db.execute("SELECT * FROM evaluation_records WHERE evaluation_id=?", (evaluation_id,)).fetchone()
        if row is None:
            raise EvaluationRecordError("evaluation record not found")
        record = self._from_row(row)
        if not record.verify_hash():
            raise EvaluationRecordError("evaluation record hash mismatch")
        return record

    def list(self) -> tuple[EvaluationRecord, ...]:
        rows = self.db.execute("SELECT * FROM evaluation_records ORDER BY created_at, evaluation_id").fetchall()
        records = tuple(self._from_row(row) for row in rows)
        if any(not record.verify_hash() for record in records):
            raise EvaluationRecordError("evaluation record hash mismatch")
        return records

    @staticmethod
    def _from_row(row: Any) -> EvaluationRecord:
        return EvaluationRecord(
            evaluation_id=row["evaluation_id"], episode_id=row["episode_id"], provider_id=row["provider_id"],
            capability_id=row["capability_id"], model_snapshot=row["model_snapshot"],
            route_decision_hash=row["route_decision_hash"], provider_result_id=row["provider_result_id"],
            output_artifact_id=row["output_artifact_id"], verification_hash=row["verification_hash"],
            outcome_status=row["outcome_status"], completion_status=row["completion_status"],
            latency_ms=row["latency_ms"], token_usage=json.loads(row["token_usage_json"]),
            human_intervention=bool(row["human_intervention"]), failure_class=row["failure_class"],
            canonical_hash=row["canonical_hash"],
        )


@dataclass(frozen=True)
class EvaluationAggregate:
    provider_id: str
    capability_id: str
    sample_count: int
    verified_count: int
    completed_count: int
    inconclusive_count: int
    average_latency_ms: float | None


class EvaluationMemory:
    """Read-only projection derived entirely from immutable raw records."""

    def __init__(self, records: tuple[EvaluationRecord, ...] | list[EvaluationRecord]):
        self.records = tuple(records)

    def aggregate(self, provider_id: str, capability_id: str) -> EvaluationAggregate:
        records = tuple(record for record in self.records if record.provider_id == provider_id and record.capability_id == capability_id)
        latencies = [record.latency_ms for record in records if record.latency_ms is not None]
        return EvaluationAggregate(
            provider_id=provider_id,
            capability_id=capability_id,
            sample_count=len(records),
            verified_count=sum(record.outcome_status == "VERIFIED" for record in records),
            completed_count=sum(record.completion_status == "DONE" for record in records),
            inconclusive_count=sum(record.outcome_status == "INCONCLUSIVE" for record in records),
            average_latency_ms=sum(latencies) / len(latencies) if latencies else None,
        )
