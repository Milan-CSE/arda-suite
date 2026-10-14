"""M2.4D3.5 append-only TaskContract revision chain."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from arda.core import Database
from arda.requirements import Requirement, TaskContract


class RequirementRevisionError(ValueError):
    """A contract revision would break the append-only chain."""


@dataclass(frozen=True)
class ContractRevision:
    revision_id: str
    parent_revision_id: str | None
    revision_number: int
    task_contract: TaskContract
    reason: str
    provenance: Mapping[str, str]
    added_requirement_ids: tuple[str, ...]
    changed_requirement_ids: tuple[str, ...]
    removed_requirement_ids: tuple[str, ...]

    @property
    def contract_hash(self) -> str:
        return self.task_contract.hash()

    def canonical_payload(self) -> dict:
        return {
            "revision_id": self.revision_id,
            "parent_revision_id": self.parent_revision_id,
            "revision_number": self.revision_number,
            "contract_hash": self.contract_hash,
            "reason": self.reason,
            "provenance": dict(sorted(self.provenance.items())),
            "added_requirement_ids": list(self.added_requirement_ids),
            "changed_requirement_ids": list(self.changed_requirement_ids),
            "removed_requirement_ids": list(self.removed_requirement_ids),
        }

    def hash(self) -> str:
        encoded = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RequirementRevisionChain:
    """Immutable-in-use revision history; append returns a new chain state."""

    def __init__(self, revisions: tuple[ContractRevision, ...] = ()):
        self._revisions = revisions

    @classmethod
    def start(cls, *, revision_id: str, task_contract: TaskContract, provenance: Mapping[str, str]) -> "RequirementRevisionChain":
        if not revision_id or not task_contract.task_id or not task_contract.episode_id:
            raise RequirementRevisionError("initial revision identity is required")
        revision = ContractRevision(
            revision_id=revision_id,
            parent_revision_id=None,
            revision_number=1,
            task_contract=task_contract,
            reason="initial_contract",
            provenance=dict(provenance),
            added_requirement_ids=tuple(sorted(item.requirement_id for item in task_contract.requirements())),
            changed_requirement_ids=(),
            removed_requirement_ids=(),
        )
        return cls((revision,))

    @property
    def head(self) -> ContractRevision:
        if not self._revisions:
            raise RequirementRevisionError("revision chain is empty")
        return self._revisions[-1]

    @property
    def history(self) -> tuple[ContractRevision, ...]:
        return self._revisions

    def append(
        self,
        *,
        revision_id: str,
        parent_revision_id: str,
        task_contract: TaskContract,
        reason: str,
        provenance: Mapping[str, str],
    ) -> "RequirementRevisionChain":
        if not revision_id or not parent_revision_id or not reason:
            raise RequirementRevisionError("revision identity, parent and reason are required")
        if any(item.revision_id == revision_id for item in self._revisions):
            raise RequirementRevisionError("revision_id already exists")
        if parent_revision_id != self.head.revision_id:
            raise RequirementRevisionError("parent must be the current chain head")
        if task_contract.task_id != self.head.task_contract.task_id or task_contract.episode_id != self.head.task_contract.episode_id:
            raise RequirementRevisionError("task and episode identity cannot change")
        added, changed, removed = self._diff(self.head.task_contract, task_contract)
        revision = ContractRevision(
            revision_id=revision_id,
            parent_revision_id=parent_revision_id,
            revision_number=self.head.revision_number + 1,
            task_contract=task_contract,
            reason=reason,
            provenance=dict(provenance),
            added_requirement_ids=added,
            changed_requirement_ids=changed,
            removed_requirement_ids=removed,
        )
        return RequirementRevisionChain(self._revisions + (revision,))

    def get(self, revision_id: str) -> ContractRevision:
        for revision in self._revisions:
            if revision.revision_id == revision_id:
                return revision
        raise RequirementRevisionError("unknown revision_id")

    @staticmethod
    def _diff(previous: TaskContract, current: TaskContract) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        before = {item.requirement_id: item.canonical_payload() for item in previous.requirements()}
        after = {item.requirement_id: item.canonical_payload() for item in current.requirements()}
        added = tuple(sorted(set(after) - set(before)))
        removed = tuple(sorted(set(before) - set(after)))
        changed = tuple(sorted(item_id for item_id in set(before) & set(after) if before[item_id] != after[item_id]))
        return added, changed, removed


class SQLiteRequirementRevisionStore:
    """Durably persist the append-only revision chain in Core SQLite."""

    def __init__(self, db: Database):
        self.db = db
        if not db.table_exists("requirement_contract_revisions"):
            raise RequirementRevisionError("database migrations are not current")

    def append(self, revision: ContractRevision) -> ContractRevision:
        try:
            with self.db.transaction():
                self.append_in_transaction(revision)
        except sqlite3.IntegrityError as error:
            raise RequirementRevisionError("revision violates durable chain invariant") from error
        return revision

    def append_in_transaction(self, revision: ContractRevision) -> ContractRevision:
        """Append while an outer Core transaction owns atomicity."""
        self._validate_parent(revision)
        self.db.execute(
            """INSERT INTO requirement_contract_revisions(
                revision_id, parent_revision_id, revision_number,
                task_id, episode_id, contract_json, contract_hash,
                reason, provenance_json, added_requirement_ids,
                changed_requirement_ids, removed_requirement_ids,
                canonical_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                revision.revision_id,
                revision.parent_revision_id,
                revision.revision_number,
                revision.task_contract.task_id,
                revision.task_contract.episode_id,
                _json(_contract_payload(revision.task_contract)),
                revision.contract_hash,
                revision.reason,
                _json(dict(sorted(revision.provenance.items()))),
                _json(list(revision.added_requirement_ids)),
                _json(list(revision.changed_requirement_ids)),
                _json(list(revision.removed_requirement_ids)),
                revision.hash(),
                _iso(datetime.now(timezone.utc)),
            ),
        )
        return revision

    def load(self, *, task_id: str, episode_id: str) -> RequirementRevisionChain:
        rows = self.db.execute(
            """SELECT * FROM requirement_contract_revisions
               WHERE task_id = ? AND episode_id = ? ORDER BY revision_number""",
            (task_id, episode_id),
        ).fetchall()
        if not rows:
            raise RequirementRevisionError("revision chain not found")
        revisions: list[ContractRevision] = []
        for index, row in enumerate(rows, start=1):
            try:
                revision = _revision_from_row(row)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise RequirementRevisionError("revision payload is corrupt") from error
            if revision.revision_number != index:
                raise RequirementRevisionError("revision numbers are not contiguous")
            if index == 1:
                if revision.parent_revision_id is not None:
                    raise RequirementRevisionError("initial revision has a parent")
            elif revision.parent_revision_id != revisions[-1].revision_id:
                raise RequirementRevisionError("revision parent chain is broken")
            if row["contract_hash"] != revision.contract_hash or row["canonical_hash"] != revision.hash():
                raise RequirementRevisionError("revision canonical hash mismatch")
            revisions.append(revision)
        return RequirementRevisionChain(tuple(revisions))

    def _validate_parent(self, revision: ContractRevision) -> None:
        duplicate = self.db.execute(
            "SELECT revision_id FROM requirement_contract_revisions WHERE revision_id = ?",
            (revision.revision_id,),
        ).fetchone()
        if duplicate is not None:
            raise RequirementRevisionError("revision_id already exists")
        if revision.parent_revision_id is not None:
            parent = self.db.execute(
                """SELECT task_id, episode_id FROM requirement_contract_revisions
                   WHERE revision_id = ?""",
                (revision.parent_revision_id,),
            ).fetchone()
            if parent is not None and (parent["task_id"] != revision.task_contract.task_id or parent["episode_id"] != revision.task_contract.episode_id):
                raise RequirementRevisionError("task and episode identity cannot change")
        row = self.db.execute(
            """SELECT revision_id, revision_number, task_id, episode_id
               FROM requirement_contract_revisions
               WHERE task_id = ? AND episode_id = ?
               ORDER BY revision_number DESC LIMIT 1""",
            (revision.task_contract.task_id, revision.task_contract.episode_id),
        ).fetchone()
        if row is None:
            if revision.parent_revision_id is not None or revision.revision_number != 1:
                raise RequirementRevisionError("first revision must have no parent and number one")
            return
        if revision.parent_revision_id != row["revision_id"] or revision.revision_number != row["revision_number"] + 1:
            raise RequirementRevisionError("parent must be current durable chain head")


def _contract_payload(contract: TaskContract) -> dict:
    return {
        "task_id": contract.task_id,
        "episode_id": contract.episode_id,
        "objective": contract.objective,
        "explicit_requirements": [item.canonical_payload() for item in contract.explicit_requirements],
        "inferred_requirements": [item.canonical_payload() for item in contract.inferred_requirements],
        "forbidden_changes": list(contract.forbidden_changes),
        "allowed_scope": list(contract.allowed_scope),
        "acceptance_criteria": list(contract.acceptance_criteria),
        "unresolved_uncertainties": list(contract.unresolved_uncertainties),
        "task_type": contract.task_type,
        "risk": contract.risk,
        "privacy": contract.privacy,
        "required_capabilities": list(contract.required_capabilities),
        "constraints": list(contract.constraints),
        "evidence_requirements": list(contract.evidence_requirements),
        "source_ref": contract.source_ref,
        "created_by": contract.created_by,
    }


def _requirement_from_payload(payload: dict) -> Requirement:
    from arda.requirements import RequirementSource

    return Requirement(
        requirement_id=payload["requirement_id"],
        statement=payload["statement"],
        kind=payload["kind"],
        source=RequirementSource(payload["source"]),
        target=payload.get("target"),
        expected=payload.get("expected"),
        evidence_refs=tuple(payload.get("evidence_refs", [])),
        confidence=payload.get("confidence"),
        origin=payload.get("origin"),
    )


def _contract_from_payload(payload: dict) -> TaskContract:
    return TaskContract(
        task_id=payload["task_id"],
        episode_id=payload["episode_id"],
        objective=payload["objective"],
        explicit_requirements=tuple(_requirement_from_payload(item) for item in payload.get("explicit_requirements", [])),
        inferred_requirements=tuple(_requirement_from_payload(item) for item in payload.get("inferred_requirements", [])),
        forbidden_changes=tuple(payload.get("forbidden_changes", [])),
        allowed_scope=tuple(payload.get("allowed_scope", [])),
        acceptance_criteria=tuple(payload.get("acceptance_criteria", [])),
        unresolved_uncertainties=tuple(payload.get("unresolved_uncertainties", [])),
        task_type=payload.get("task_type", "other"),
        risk=payload.get("risk", "medium"),
        privacy=payload.get("privacy", "private"),
        required_capabilities=tuple(payload.get("required_capabilities", [])),
        constraints=tuple(payload.get("constraints", [])),
        evidence_requirements=tuple(payload.get("evidence_requirements", [])),
        source_ref=payload.get("source_ref", ""),
        created_by=payload.get("created_by", "SYSTEM"),
    )


def _revision_from_row(row) -> ContractRevision:
    contract = _contract_from_payload(json.loads(row["contract_json"]))
    return ContractRevision(
        revision_id=row["revision_id"],
        parent_revision_id=row["parent_revision_id"],
        revision_number=row["revision_number"],
        task_contract=contract,
        reason=row["reason"],
        provenance=json.loads(row["provenance_json"]),
        added_requirement_ids=tuple(json.loads(row["added_requirement_ids"])),
        changed_requirement_ids=tuple(json.loads(row["changed_requirement_ids"])),
        removed_requirement_ids=tuple(json.loads(row["removed_requirement_ids"])),
    )


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()
