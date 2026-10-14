"""M2.5 Episode identity and append-only lifecycle ledger."""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from arda.core import Database
from arda.requirement_revisions import RequirementRevisionChain, SQLiteRequirementRevisionStore
from arda.task_intake import TaskIntakeRequest


class EpisodeError(ValueError):
    """Episode identity, lifecycle or idempotency invariant failed."""


class EpisodeStatus(str, Enum):
    OPEN = "open"
    CLARIFYING = "clarifying"
    PLANNED = "planned"
    RUNNING = "running"
    BLOCKED = "blocked"
    AWAITING_DECISION = "awaiting_decision"
    DONE = "done"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Episode:
    episode_id: str
    intake_id: str
    task_id: str
    task_contract_revision_id: str
    task_contract_hash: str
    intake_payload_hash: str
    status: EpisodeStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class EpisodeEvent:
    event_id: str
    episode_id: str
    event_type: str
    status: EpisodeStatus
    payload: dict[str, Any]
    idempotency_key: str
    created_at: str


class EpisodeStore:
    """Episode projection plus append-only lifecycle events."""

    def __init__(self, db: Database):
        self.db = db
        if not db.table_exists("episodes") or not db.table_exists("episode_events"):
            raise EpisodeError("database migrations are not current")

    def create(
        self,
        request: TaskIntakeRequest,
        *,
        task_id: str | None = None,
        episode_id: str | None = None,
        revision_id: str | None = None,
    ) -> Episode:
        request.validate()
        existing = self.db.execute(
            "SELECT * FROM episodes WHERE intake_id = ?", (request.intake_id,)
        ).fetchone()
        if existing is not None:
            if existing["intake_payload_hash"] != request.payload_hash:
                raise EpisodeError("intake_id conflicts with a different payload")
            return _episode_from_row(existing)

        task_id = task_id or f"task_{uuid.uuid4().hex}"
        episode_id = episode_id or f"episode_{uuid.uuid4().hex}"
        revision_id = revision_id or f"revision_{uuid.uuid4().hex}"
        contract = request.to_contract(task_id=task_id, episode_id=episode_id)
        revision = RequirementRevisionChain.start(
            revision_id=revision_id,
            task_contract=contract,
            provenance={"created_by": request.created_by, "source_ref": request.source_ref},
        ).head
        now = datetime.now(timezone.utc).isoformat()
        status = EpisodeStatus.CLARIFYING if request.candidate_requirements or request.unresolved_uncertainties or not request.acceptance_criteria else EpisodeStatus.PLANNED
        try:
            with self.db.transaction():
                revisions = SQLiteRequirementRevisionStore(self.db)
                revisions.append_in_transaction(revision)
                self.db.execute(
                    """INSERT INTO episodes(
                        episode_id, intake_id, task_id, task_contract_revision_id,
                        task_contract_hash, intake_payload_json, intake_payload_hash,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        episode_id, request.intake_id, task_id, revision_id,
                        contract.hash(), _json(request.canonical_payload()), request.payload_hash,
                        status.value, now, now,
                    ),
                )
                self._append_event_in_transaction(
                    episode_id=episode_id,
                    event_type="episode.created",
                    status=status,
                    payload={"task_id": task_id, "task_contract_revision_id": revision_id},
                    idempotency_key=f"{request.intake_id}:episode.created",
                    created_at=now,
                )
        except sqlite3.IntegrityError as error:
            raise EpisodeError("episode creation violates an identity invariant") from error
        return self.get(episode_id)

    def append_event(
        self,
        episode_id: str,
        *,
        event_type: str,
        status: EpisodeStatus,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> EpisodeEvent:
        if not event_type or not idempotency_key:
            raise EpisodeError("event_type and idempotency_key are required")
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.db.transaction():
                event = self._append_event_in_transaction(
                    episode_id=episode_id, event_type=event_type, status=status,
                    payload=payload, idempotency_key=idempotency_key, created_at=now,
                )
                self.db.execute(
                    "UPDATE episodes SET status=?, updated_at=? WHERE episode_id=?",
                    (status.value, now, episode_id),
                )
        except sqlite3.IntegrityError as error:
            raise EpisodeError("episode event violates an append-only or idempotency invariant") from error
        return event

    def get(self, episode_id: str) -> Episode:
        row = self.db.execute("SELECT * FROM episodes WHERE episode_id=?", (episode_id,)).fetchone()
        if row is None:
            raise EpisodeError("episode not found")
        return _episode_from_row(row)

    def events(self, episode_id: str) -> tuple[EpisodeEvent, ...]:
        rows = self.db.execute(
            "SELECT * FROM episode_events WHERE episode_id=? ORDER BY created_at, event_id",
            (episode_id,),
        ).fetchall()
        return tuple(
            EpisodeEvent(
                row["event_id"], row["episode_id"], row["event_type"],
                EpisodeStatus(row["status"]), json.loads(row["payload_json"]),
                row["idempotency_key"], row["created_at"],
            ) for row in rows
        )

    def _append_event_in_transaction(self, *, episode_id, event_type, status, payload, idempotency_key, created_at):
        if self.db.execute("SELECT 1 FROM episodes WHERE episode_id=?", (episode_id,)).fetchone() is None and event_type != "episode.created":
            raise EpisodeError("unknown episode")
        event = EpisodeEvent(
            event_id=f"episode_event_{uuid.uuid4().hex}", episode_id=episode_id,
            event_type=event_type, status=status, payload=dict(payload),
            idempotency_key=idempotency_key, created_at=created_at,
        )
        self.db.execute(
            """INSERT INTO episode_events(
                event_id, episode_id, event_type, status, payload_json,
                idempotency_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id, event.episode_id, event.event_type, event.status.value,
                _json(event.payload), event.idempotency_key, event.created_at,
            ),
        )
        return event


def _episode_from_row(row) -> Episode:
    return Episode(
        episode_id=row["episode_id"], intake_id=row["intake_id"], task_id=row["task_id"],
        task_contract_revision_id=row["task_contract_revision_id"], task_contract_hash=row["task_contract_hash"],
        intake_payload_hash=row["intake_payload_hash"], status=EpisodeStatus(row["status"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
