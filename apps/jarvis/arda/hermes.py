"""Hermes v0.1 coordinator: workflow state and Core proposal handoff only."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping

from arda.routing import RouteProposal


class HermesError(ValueError):
    """A Hermes workflow violates its coordination-only contract."""


class HermesRunStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    kind: str
    capability_id: str | None = None
    required_inputs: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.step_id or not self.kind:
            raise HermesError("workflow step identity is required")
        if self.capability_id is not None and "." not in self.capability_id:
            raise HermesError("workflow step capability_id must be canonical")


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    version: str
    steps: tuple[WorkflowStep, ...]

    def validate(self) -> None:
        if not self.workflow_id or not self.version or not self.steps:
            raise HermesError("workflow identity and steps are required")
        ids = set()
        for step in self.steps:
            step.validate()
            if step.step_id in ids:
                raise HermesError("workflow step_id must be unique")
            ids.add(step.step_id)

    @property
    def canonical_hash(self) -> str:
        self.validate()
        payload = {
            "workflow_id": self.workflow_id,
            "version": self.version,
            "steps": [
                {"step_id": step.step_id, "kind": step.kind, "capability_id": step.capability_id,
                 "required_inputs": list(step.required_inputs)} for step in self.steps
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class StepExecution:
    step_id: str
    status: StepStatus = StepStatus.PENDING
    evidence_refs: tuple[str, ...] = ()
    error_class: str | None = None


@dataclass(frozen=True)
class HermesRun:
    hermes_run_id: str
    episode_id: str
    workflow_hash: str
    status: HermesRunStatus
    steps: tuple[StepExecution, ...]


class SQLiteHermesRunStore:
    """Append-only Hermes run snapshots for restart recovery."""

    def __init__(self, db: Any):
        self.db = db
        if not db.table_exists("hermes_run_events"):
            raise HermesError("Hermes run event migration is not current")

    def append(self, run: HermesRun, *, event_type: str, idempotency_key: str) -> HermesRun:
        payload = self._payload(run)
        existing = self.db.execute(
            "SELECT payload_json FROM hermes_run_events WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] != json.dumps(payload, sort_keys=True, separators=(",", ":")):
                raise HermesError("Hermes event idempotency conflict")
            return run
        try:
            self.db.execute(
                "INSERT INTO hermes_run_events(event_id, hermes_run_id, event_type, payload_json, idempotency_key, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (f"hermes_event_{run.hermes_run_id}_{len(self.events(run.hermes_run_id)) + 1}", run.hermes_run_id,
                 event_type, json.dumps(payload, sort_keys=True, separators=(",", ":")), idempotency_key,
                 datetime.now(timezone.utc).isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise HermesError("Hermes event violates append-only identity") from exc
        return run

    def load(self, hermes_run_id: str) -> HermesRun:
        row = self.db.execute(
            "SELECT payload_json FROM hermes_run_events WHERE hermes_run_id=? ORDER BY created_at DESC, event_id DESC LIMIT 1",
            (hermes_run_id,),
        ).fetchone()
        if row is None:
            raise HermesError("Hermes run not found")
        return self._from_payload(json.loads(row["payload_json"]))

    def events(self, hermes_run_id: str) -> tuple[dict[str, Any], ...]:
        rows = self.db.execute(
            "SELECT event_type, payload_json, idempotency_key FROM hermes_run_events WHERE hermes_run_id=? ORDER BY created_at, event_id",
            (hermes_run_id,),
        ).fetchall()
        return tuple({"event_type": row["event_type"], "payload": json.loads(row["payload_json"]), "idempotency_key": row["idempotency_key"]} for row in rows)

    @staticmethod
    def _payload(run: HermesRun) -> dict[str, Any]:
        return {"hermes_run_id": run.hermes_run_id, "episode_id": run.episode_id, "workflow_hash": run.workflow_hash,
                "status": run.status.value, "steps": [{"step_id": step.step_id, "status": step.status.value,
                "evidence_refs": list(step.evidence_refs), "error_class": step.error_class} for step in run.steps]}

    @staticmethod
    def _from_payload(payload: Mapping[str, Any]) -> HermesRun:
        return HermesRun(payload["hermes_run_id"], payload["episode_id"], payload["workflow_hash"],
                         HermesRunStatus(payload["status"]), tuple(StepExecution(item["step_id"], StepStatus(item["status"]), tuple(item["evidence_refs"]), item["error_class"]) for item in payload["steps"]))


class HermesCoordinator:
    """Coordinate workflow state without owning Core authority."""

    def __init__(self, *, episode_exists: Callable[[str], bool], store: SQLiteHermesRunStore | None = None):
        self.episode_exists = episode_exists
        self.store = store

    def create_run(self, *, hermes_run_id: str, episode_id: str, workflow: WorkflowDefinition) -> HermesRun:
        workflow.validate()
        if not self.episode_exists(episode_id):
            raise HermesError("Hermes cannot create or invent an episode")
        run = HermesRun(
            hermes_run_id=hermes_run_id,
            episode_id=episode_id,
            workflow_hash=workflow.canonical_hash,
            status=HermesRunStatus.CREATED,
            steps=tuple(StepExecution(step.step_id) for step in workflow.steps),
        )
        if self.store is not None:
            self.store.append(run, event_type="hermes.run.created", idempotency_key=f"{hermes_run_id}:created")
        return run

    def start(self, run: HermesRun) -> HermesRun:
        self._require_status(run, HermesRunStatus.CREATED)
        updated = HermesRun(run.hermes_run_id, run.episode_id, run.workflow_hash, HermesRunStatus.RUNNING, run.steps)
        if self.store is not None:
            self.store.append(updated, event_type="hermes.run.started", idempotency_key=f"{run.hermes_run_id}:started")
        return updated

    def update_step(self, run: HermesRun, step_id: str, status: StepStatus, *, evidence_refs: tuple[str, ...] = (), error_class: str | None = None) -> HermesRun:
        if run.status not in {HermesRunStatus.RUNNING, HermesRunStatus.WAITING}:
            raise HermesError("workflow run is not active")
        if not any(step.step_id == step_id for step in run.steps):
            raise HermesError("unknown workflow step")
        steps = tuple(
            StepExecution(step.step_id, status, tuple(evidence_refs), error_class) if step.step_id == step_id else step
            for step in run.steps
        )
        run_status = HermesRunStatus.WAITING if status is StepStatus.WAITING else HermesRunStatus.RUNNING
        if status is StepStatus.FAILED:
            run_status = HermesRunStatus.FAILED
        elif steps and all(step.status is StepStatus.COMPLETED for step in steps):
            run_status = HermesRunStatus.COMPLETED
        updated = HermesRun(run.hermes_run_id, run.episode_id, run.workflow_hash, run_status, steps)
        if self.store is not None:
            self.store.append(updated, event_type="hermes.step.updated", idempotency_key=f"{run.hermes_run_id}:step:{step_id}:{status.value}")
        return updated

    def request_route_proposal(self, run: HermesRun, task_view: Mapping[str, Any], proposer: Callable[[Mapping[str, Any]], RouteProposal]) -> RouteProposal:
        if run.status not in {HermesRunStatus.RUNNING, HermesRunStatus.WAITING}:
            raise HermesError("route proposal requires an active workflow run")
        proposal = proposer(dict(task_view))
        if proposal.episode_id != run.episode_id:
            raise HermesError("route proposal episode mismatch")
        return proposal

    @staticmethod
    def _require_status(run: HermesRun, expected: HermesRunStatus) -> None:
        if run.status is not expected:
            raise HermesError(f"workflow run must be {expected.value}")
