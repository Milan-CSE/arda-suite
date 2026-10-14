"""Failure-first durable D4C alignment persistence tests."""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from arda.adversarial import (
    AlignmentEvaluator,
    BlindReviewInput,
    ForwardIntent,
    ReconstructedIntent,
    ReconstructionRecord,
    VerifierProvenance,
)
from arda.alignment_store import AlignmentPersistenceError, SQLiteAlignmentStore
from arda.core import Database


def make_evaluation():
    content = "def answer(): return 2"
    blind = BlindReviewInput.build({
        "episode_id": "episode-1",
        "patch_result_id": "patch-1",
        "patch_tree_hash": "tree-hash",
        "changed_files": ["module.py"],
        "diff": "--- a/module.py\n+++ b/module.py\n",
        "contexts": [{
            "reference": "source:module.py",
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content": content,
        }],
        "evidence_refs": ["behavior:target", "source:module.py"],
    })
    provenance = VerifierProvenance(
        "adversarial-verifier", "fixture", "fixture-model", "1", "none", "d4-v0.1",
        blind.input_hash, "output-hash", "2026-10-01T00:00:00Z", 1,
    )
    reconstruction = ReconstructionRecord.create(
        reconstruction_id="reconstruction-1",
        blind_input=blind,
        reconstructed_problem="answer returned the wrong value",
        intents=(ReconstructedIntent("i1", "changes answer", ("r1",), ("behavior:target",)),),
        assumptions=(), risks=(), contradiction_ids=(),
        evidence_refs=("behavior:target", "source:module.py"),
        uncertainty="", confidence=0.8, confidence_basis=("behavior:target",),
        provenance=provenance,
    )
    forward = ForwardIntent.create(
        episode_id="episode-1", patch_result_id="patch-1",
        task_contract_revision_id="rev-1", task_contract_hash="contract-hash",
        expected_requirement_ids=("r1",),
        evidence_refs=("behavior:target", "source:module.py"),
    )
    evaluation = AlignmentEvaluator().evaluate(
        forward_intent=forward,
        reconstruction=reconstruction,
        task_contract_revision_id="rev-1",
        structural_evaluation_id="structural-1",
        behavioral_evaluation_id="behavior-1",
        requirement_audit_id="requirements-1",
        known_evidence_refs={"behavior:target", "source:module.py"},
        provenance=provenance,
    )
    return forward, evaluation


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    try:
        yield database
    finally:
        database.close()


def test_alignment_reopens_with_same_hashes(db, tmp_path):
    forward, evaluation = make_evaluation()
    SQLiteAlignmentStore(db).append(forward, evaluation)
    db.close()

    reopened = Database(tmp_path / "arda.sqlite3")
    reopened.migrate()
    loaded_forward, loaded_evaluation = SQLiteAlignmentStore(reopened).load("episode-1", "patch-1")
    assert loaded_forward.canonical_hash == forward.canonical_hash
    assert loaded_evaluation.canonical_hash == evaluation.canonical_hash
    assert loaded_evaluation.verify_hash()
    reopened.close()


def test_alignment_duplicate_and_tamper_are_rejected(db):
    forward, evaluation = make_evaluation()
    store = SQLiteAlignmentStore(db)
    store.append(forward, evaluation)
    with pytest.raises(AlignmentPersistenceError, match="already exists"):
        store.append(forward, evaluation)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE alignment_evaluations SET evaluation_json='{}' WHERE evaluation_id=?", (evaluation.patch_result_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM alignment_evaluations WHERE evaluation_id=?", (evaluation.patch_result_id,))
