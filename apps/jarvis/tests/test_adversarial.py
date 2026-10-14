"""Failure-first M2.4D4A deterministic adversarial envelope tests."""
from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from arda.adversarial import (
    AdversarialContractError,
    AlignmentEvaluator,
    AlignmentVerdict,
    BlindReviewInput,
    ForwardIntent,
    ReconstructedIntent,
    ReconstructionRecord,
    VerifierProvenance,
)


def blind():
    content = "def answer(): return 2"
    return BlindReviewInput.build({
        "episode_id": "episode-1",
        "patch_result_id": "patch-1",
        "patch_tree_hash": "tree-hash",
        "changed_files": ["module.py"],
        "diff": "--- a/module.py\n+++ b/module.py\n",
        "contexts": [{"reference": "source:module.py", "content_sha256": hashlib.sha256(content.encode()).hexdigest(), "content": content}],
        "evidence_refs": ["behavior:target", "source:module.py"],
    })


def provenance(input_hash):
    return VerifierProvenance("adversarial-verifier", "fixture", "fixture-model", "1", "none", "d4-v0.1", input_hash, "output-hash", "2026-10-01T00:00:00Z", 1)


def reconstruction(*, intents=(ReconstructedIntent("i1", "changes answer", ("r1",), ("behavior:target",)),), uncertainty=""):
    source = blind()
    return ReconstructionRecord.create(
        reconstruction_id="reconstruction-1",
        blind_input=source,
        reconstructed_problem="answer returned the wrong value",
        intents=tuple(intents),
        assumptions=("module is the target",),
        risks=(),
        contradiction_ids=(),
        evidence_refs=("behavior:target", "source:module.py"),
        uncertainty=uncertainty,
        confidence=0.8,
        confidence_basis=("behavior:target",),
        provenance=provenance(source.input_hash),
    )


def evaluate(record, expected=("r1",), disagreements=("disagreement-1",)):
    source = blind()
    return AlignmentEvaluator().evaluate(
        forward_intent=ForwardIntent.create(
            episode_id="episode-1",
            patch_result_id="patch-1",
            task_contract_revision_id="rev-1",
            task_contract_hash="contract-hash",
            expected_requirement_ids=expected,
            evidence_refs=("behavior:target", "source:module.py"),
        ),
        reconstruction=record,
        task_contract_revision_id="rev-1",
        structural_evaluation_id="structural-1",
        behavioral_evaluation_id="behavior-1",
        requirement_audit_id="requirements-1",
        known_evidence_refs=set(source.evidence_refs) | {"source:module.py"},
        provenance=provenance(source.input_hash),
        disagreement_ids=disagreements,
    )


def test_blind_input_rejects_task_and_generator_leaks():
    with pytest.raises(AdversarialContractError, match="forbidden field"):
        BlindReviewInput.build({"episode_id": "e", "patch_result_id": "p", "patch_tree_hash": "t", "changed_files": [], "diff": "", "requirements": []})
    with pytest.raises(AdversarialContractError, match="forbidden field"):
        BlindReviewInput.build({"episode_id": "e", "patch_result_id": "p", "patch_tree_hash": "t", "changed_files": [], "diff": "", "contexts": [{"generator_reasoning": "secret"}]})


def test_reconstruction_is_hashed_and_unknown_evidence_is_rejected():
    record = reconstruction()
    assert record.verify_hash()
    tampered = replace(record, reconstructed_problem="changed")
    assert not tampered.verify_hash()
    source = blind()
    with pytest.raises(AdversarialContractError, match="unknown evidence"):
        ReconstructionRecord.create(
            reconstruction_id="bad",
            blind_input=source,
            reconstructed_problem="x",
            intents=(), assumptions=(), risks=(), contradiction_ids=(),
            evidence_refs=("unknown",), uncertainty="unknown", confidence=None,
            confidence_basis=(), provenance=provenance(source.input_hash),
        )


def test_confidence_without_basis_is_rejected():
    source = blind()
    with pytest.raises(AdversarialContractError, match="confidence"):
        ReconstructionRecord.create(
            reconstruction_id="bad-confidence", blind_input=source,
            reconstructed_problem="x", intents=(), assumptions=(), risks=(),
            contradiction_ids=(), evidence_refs=(), uncertainty="unknown",
            confidence=0.5, confidence_basis=(), provenance=provenance(source.input_hash),
        )


def test_alignment_verdicts_and_disagreement_are_explicit():
    aligned = evaluate(reconstruction())
    assert aligned.verdict is AlignmentVerdict.INCONCLUSIVE
    assert aligned.disagreement_ids == ("disagreement-1",)
    assert aligned.verify_hash()
    assert not hasattr(aligned, "deployment_approved")

    partial = evaluate(reconstruction(), expected=("r1", "r2"), disagreements=())
    assert partial.verdict is AlignmentVerdict.PARTIAL

    misaligned = evaluate(reconstruction(intents=(ReconstructedIntent("i2", "other change", ("r2",), ("behavior:target",)),)), disagreements=())
    assert misaligned.verdict is AlignmentVerdict.MISALIGNED

    inconclusive = evaluate(reconstruction(intents=(), uncertainty="not enough evidence"), disagreements=())
    assert inconclusive.verdict is AlignmentVerdict.INCONCLUSIVE


def test_forward_intent_is_typed_and_hash_sealed():
    intent = ForwardIntent.create(
        episode_id="episode-1",
        patch_result_id="patch-1",
        task_contract_revision_id="rev-1",
        task_contract_hash="contract-hash",
        expected_requirement_ids=("r1",),
        evidence_refs=("source:module.py",),
    )
    assert intent.verify_hash()
    assert intent.canonical_hash


def test_blind_context_and_intent_evidence_integrity_are_enforced():
    with pytest.raises(AdversarialContractError, match="context hash"):
        BlindReviewInput.build({
            "episode_id": "e", "patch_result_id": "p", "patch_tree_hash": "t",
            "changed_files": [], "diff": "",
            "contexts": [{"reference": "source:x", "content_sha256": "bad", "content": "x"}],
        })
    source = blind()
    with pytest.raises(AdversarialContractError, match="unknown evidence"):
        ReconstructionRecord.create(
            reconstruction_id="bad-intent-evidence", blind_input=source,
            reconstructed_problem="x",
            intents=(ReconstructedIntent("i", "x", (), ("unknown",)),),
            assumptions=(), risks=(), contradiction_ids=(), evidence_refs=(),
            uncertainty="unknown", confidence=None, confidence_basis=(),
            provenance=provenance(source.input_hash),
        )

    evaluation = evaluate(reconstruction())
    assert not replace(evaluation, verdict=AlignmentVerdict.MISALIGNED).verify_hash()


def test_wrong_identity_or_reconstruction_hash_is_rejected():
    record = reconstruction()
    source = blind()
    forward_intent = ForwardIntent.create(
        episode_id="episode-1",
        patch_result_id="patch-1",
        task_contract_revision_id="rev-1",
        task_contract_hash="contract-hash",
        expected_requirement_ids=("r1",),
        evidence_refs=("behavior:target", "source:module.py"),
    )
    with pytest.raises(AdversarialContractError, match="hash mismatch"):
        AlignmentEvaluator().evaluate(
            forward_intent=ForwardIntent(
                forward_intent.episode_id,
                forward_intent.patch_result_id,
                forward_intent.task_contract_revision_id,
                forward_intent.task_contract_hash,
                forward_intent.expected_requirement_ids,
                forward_intent.evidence_refs,
                "0" * 64,
            ),
            reconstruction=replace(record, canonical_hash="0" * 64),
            task_contract_revision_id="rev-1", structural_evaluation_id="structural-1",
            behavioral_evaluation_id=None, requirement_audit_id=None,
            known_evidence_refs=set(source.evidence_refs) | {"source:module.py"},
            provenance=provenance(source.input_hash),
        )
    with pytest.raises(AdversarialContractError, match="identity mismatch"):
        AlignmentEvaluator().evaluate(
            forward_intent=ForwardIntent.create(
                episode_id="wrong",
                patch_result_id="patch-1",
                task_contract_revision_id="rev-1",
                task_contract_hash="contract-hash",
                expected_requirement_ids=("r1",),
                evidence_refs=("behavior:target", "source:module.py"),
            ),
            reconstruction=record, task_contract_revision_id="rev-1",
            structural_evaluation_id="structural-1", behavioral_evaluation_id=None,
            requirement_audit_id=None, known_evidence_refs=set(source.evidence_refs) | {"source:module.py"},
            provenance=provenance(source.input_hash),
        )
