"""Durable append-only persistence for D4C alignment evidence."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from arda.adversarial import (
    AdversarialEvaluation,
    AlignmentVerdict,
    ForwardIntent,
    VerifierProvenance,
    _evaluation_payload,
)
from arda.core import Database


class AlignmentPersistenceError(ValueError):
    """A durable alignment record violates an append-only or hash invariant."""


class SQLiteAlignmentStore:
    """Persist typed forward intent and immutable alignment evaluation together."""

    def __init__(self, db: Database):
        self.db = db
        if not db.table_exists("alignment_evaluations"):
            raise AlignmentPersistenceError("database migrations are not current")

    def append(self, forward_intent: ForwardIntent, evaluation: AdversarialEvaluation) -> None:
        if not forward_intent.verify_hash():
            raise AlignmentPersistenceError("forward intent hash mismatch")
        if not evaluation.verify_hash():
            raise AlignmentPersistenceError("alignment evaluation hash mismatch")
        if evaluation.forward_intent_hash != forward_intent.canonical_hash:
            raise AlignmentPersistenceError("forward intent identity mismatch")
        if evaluation.episode_id != forward_intent.episode_id or evaluation.patch_result_id != forward_intent.patch_result_id:
            raise AlignmentPersistenceError("episode or patch identity mismatch")
        try:
            self.db.execute(
                """INSERT INTO alignment_evaluations(
                    evaluation_id, episode_id, patch_result_id,
                    task_contract_revision_id, forward_intent_json,
                    evaluation_json, canonical_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evaluation.patch_result_id,
                    evaluation.episode_id,
                    evaluation.patch_result_id,
                    evaluation.task_contract_revision_id,
                    _json(_forward_payload(forward_intent)),
                    _json({**_evaluation_payload(evaluation), "canonical_hash": evaluation.canonical_hash}),
                    evaluation.canonical_hash,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise AlignmentPersistenceError("alignment evaluation already exists") from error

    def load(self, episode_id: str, patch_result_id: str) -> tuple[ForwardIntent, AdversarialEvaluation]:
        row = self.db.execute(
            "SELECT * FROM alignment_evaluations WHERE episode_id=? AND patch_result_id=?",
            (episode_id, patch_result_id),
        ).fetchone()
        if row is None:
            raise AlignmentPersistenceError("alignment evaluation not found")
        try:
            forward = _forward_from_payload(json.loads(row["forward_intent_json"]))
            evaluation = _evaluation_from_payload(json.loads(row["evaluation_json"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AlignmentPersistenceError("alignment payload is corrupt") from error
        if not forward.verify_hash() or not evaluation.verify_hash():
            raise AlignmentPersistenceError("alignment canonical hash mismatch")
        if row["canonical_hash"] != evaluation.canonical_hash:
            raise AlignmentPersistenceError("alignment row hash mismatch")
        if evaluation.forward_intent_hash != forward.canonical_hash:
            raise AlignmentPersistenceError("alignment forward intent hash mismatch")
        return forward, evaluation


def _forward_payload(value: ForwardIntent) -> dict[str, Any]:
    return {
        "episode_id": value.episode_id,
        "patch_result_id": value.patch_result_id,
        "task_contract_revision_id": value.task_contract_revision_id,
        "task_contract_hash": value.task_contract_hash,
        "expected_requirement_ids": list(value.expected_requirement_ids),
        "evidence_refs": list(value.evidence_refs),
        "canonical_hash": value.canonical_hash,
    }


def _forward_from_payload(payload: dict[str, Any]) -> ForwardIntent:
    value = ForwardIntent.create(
        episode_id=payload["episode_id"],
        patch_result_id=payload["patch_result_id"],
        task_contract_revision_id=payload["task_contract_revision_id"],
        task_contract_hash=payload["task_contract_hash"],
        expected_requirement_ids=tuple(payload["expected_requirement_ids"]),
        evidence_refs=tuple(payload["evidence_refs"]),
    )
    if value.canonical_hash != payload["canonical_hash"]:
        raise AlignmentPersistenceError("forward intent canonical hash mismatch")
    return value


def _evaluation_from_payload(payload: dict[str, Any]) -> AdversarialEvaluation:
    provenance_payload = payload["provenance"]
    provenance = VerifierProvenance(
        verifier_role=provenance_payload["verifier_role"],
        provider=provenance_payload["provider"],
        model_id=provenance_payload["model_id"],
        model_version_if_known=provenance_payload["model_version_if_known"],
        auth_mode=provenance_payload["auth_mode"],
        prompt_contract_version=provenance_payload["prompt_contract_version"],
        input_hash=provenance_payload["input_hash"],
        output_hash=provenance_payload["output_hash"],
        started_at=provenance_payload["started_at"],
        duration_ms=provenance_payload["duration_ms"],
        token_usage=provenance_payload.get("token_usage"),
        model_snapshot_hash=provenance_payload.get("model_snapshot_hash"),
    )
    return AdversarialEvaluation(
        episode_id=payload["episode_id"],
        task_contract_revision_id=payload["task_contract_revision_id"],
        patch_result_id=payload["patch_result_id"],
        structural_evaluation_id=payload["structural_evaluation_id"],
        behavioral_evaluation_id=payload["behavioral_evaluation_id"],
        requirement_audit_id=payload["requirement_audit_id"],
        forward_intent_hash=payload["forward_intent_hash"],
        backward_reconstruction_hash=payload["backward_reconstruction_hash"],
        verdict=AlignmentVerdict(payload["verdict"]),
        matched_requirement_ids=tuple(payload["matched_requirement_ids"]),
        unmatched_requirement_ids=tuple(payload["unmatched_requirement_ids"]),
        contradiction_ids=tuple(payload["contradiction_ids"]),
        disagreement_ids=tuple(payload["disagreement_ids"]),
        evidence_refs=tuple(payload["evidence_refs"]),
        provenance=provenance,
        canonical_hash=payload["canonical_hash"],
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
