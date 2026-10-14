"""M2.4D4A deterministic adversarial verification envelope."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping


class AdversarialContractError(ValueError):
    """The blind/reconstruction/alignment contract was violated."""


class AlignmentVerdict(str, Enum):
    ALIGNED = "ALIGNED"
    PARTIAL = "PARTIAL"
    MISALIGNED = "MISALIGNED"
    INCONCLUSIVE = "INCONCLUSIVE"


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifierProvenance:
    verifier_role: str
    provider: str
    model_id: str
    model_version_if_known: str | None
    auth_mode: str
    prompt_contract_version: str
    input_hash: str
    output_hash: str
    started_at: str
    duration_ms: int
    token_usage: Mapping[str, int] | None = None
    model_snapshot_hash: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "verifier_role": self.verifier_role,
            "provider": self.provider,
            "model_id": self.model_id,
            "model_version_if_known": self.model_version_if_known,
            "auth_mode": self.auth_mode,
            "prompt_contract_version": self.prompt_contract_version,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "token_usage": dict(sorted((self.token_usage or {}).items())),
            "model_snapshot_hash": self.model_snapshot_hash,
        }


@dataclass(frozen=True)
class BlindContext:
    reference: str
    content_sha256: str
    content: str

    def validate_hash(self) -> bool:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest() == self.content_sha256

    def canonical_payload(self) -> dict[str, str]:
        return {"reference": self.reference, "content_sha256": self.content_sha256, "content": self.content}


@dataclass(frozen=True)
class BlindReviewInput:
    episode_id: str
    patch_result_id: str
    patch_tree_hash: str
    changed_files: tuple[str, ...]
    diff: str
    contexts: tuple[BlindContext, ...]
    evidence_refs: tuple[str, ...]
    input_hash: str

    @classmethod
    def build(cls, payload: Mapping[str, Any]) -> "BlindReviewInput":
        forbidden = {
            "objective", "task_objective", "requirements", "task_contract",
            "generator_reasoning", "generator_model", "generator_confidence",
            "forward_intent", "deployment_approval", "deployment_approved",
        }
        leaked = _find_forbidden_keys(payload, forbidden)
        if leaked:
            raise AdversarialContractError(f"blind input contains forbidden field: {leaked[0]}")
        allowed = {"episode_id", "patch_result_id", "patch_tree_hash", "changed_files", "diff", "contexts", "evidence_refs"}
        unknown = set(payload) - allowed
        if unknown:
            raise AdversarialContractError(f"blind input contains unknown fields: {sorted(unknown)}")
        try:
            contexts = tuple(BlindContext(str(item["reference"]), str(item["content_sha256"]), str(item["content"])) for item in payload.get("contexts", ()))
            if any(not item.validate_hash() for item in contexts):
                raise AdversarialContractError("blind context hash mismatch")
            value = {
                "episode_id": str(payload["episode_id"]),
                "patch_result_id": str(payload["patch_result_id"]),
                "patch_tree_hash": str(payload["patch_tree_hash"]),
                "changed_files": sorted(str(item) for item in payload["changed_files"]),
                "diff": str(payload["diff"]),
                "contexts": [item.canonical_payload() for item in contexts],
                "evidence_refs": sorted(str(item) for item in payload.get("evidence_refs", ())),
            }
        except (KeyError, TypeError) as error:
            raise AdversarialContractError("blind input is malformed") from error
        return cls(value["episode_id"], value["patch_result_id"], value["patch_tree_hash"], tuple(value["changed_files"]), value["diff"], contexts, tuple(value["evidence_refs"]), _hash_payload(value))


@dataclass(frozen=True)
class ReconstructedIntent:
    intent_id: str
    summary: str
    requirement_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    risk_flags: tuple[str, ...] = ()

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "summary": self.summary,
            "requirement_ids": list(self.requirement_ids),
            "evidence_refs": list(self.evidence_refs),
            "risk_flags": list(self.risk_flags),
        }


@dataclass(frozen=True)
class ReconstructionRecord:
    reconstruction_id: str
    episode_id: str
    patch_result_id: str
    patch_tree_hash: str
    changed_files: tuple[str, ...]
    reconstructed_problem: str
    intents: tuple[ReconstructedIntent, ...]
    assumptions: tuple[str, ...]
    risks: tuple[str, ...]
    contradiction_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    uncertainty: str
    confidence: float | None
    confidence_basis: tuple[str, ...]
    provenance: VerifierProvenance
    canonical_hash: str

    @classmethod
    def create(cls, *, reconstruction_id: str, blind_input: BlindReviewInput, reconstructed_problem: str, intents: tuple[ReconstructedIntent, ...], assumptions: tuple[str, ...], risks: tuple[str, ...], contradiction_ids: tuple[str, ...], evidence_refs: tuple[str, ...], uncertainty: str, confidence: float | None, confidence_basis: tuple[str, ...], provenance: VerifierProvenance) -> "ReconstructionRecord":
        if confidence is not None and (confidence < 0 or confidence > 1 or not confidence_basis):
            raise AdversarialContractError("confidence requires a value in [0,1] and a confidence basis")
        available = set(blind_input.evidence_refs) | {item.reference for item in blind_input.contexts}
        intent_evidence = {ref for intent in intents for ref in intent.evidence_refs}
        unknown = (set(evidence_refs) | set(confidence_basis) | intent_evidence) - available
        if unknown:
            raise AdversarialContractError("reconstruction references unknown evidence")
        if provenance.input_hash != blind_input.input_hash:
            raise AdversarialContractError("verifier input hash mismatch")
        payload = {
            "reconstruction_id": reconstruction_id,
            "episode_id": blind_input.episode_id,
            "patch_result_id": blind_input.patch_result_id,
            "patch_tree_hash": blind_input.patch_tree_hash,
            "changed_files": list(blind_input.changed_files),
            "reconstructed_problem": reconstructed_problem,
            "intents": [item.canonical_payload() for item in intents],
            "assumptions": list(assumptions),
            "risks": list(risks),
            "contradiction_ids": list(contradiction_ids),
            "evidence_refs": list(evidence_refs),
            "uncertainty": uncertainty,
            "confidence": confidence,
            "confidence_basis": list(confidence_basis),
            "provenance": provenance.canonical_payload(),
        }
        return cls(reconstruction_id, blind_input.episode_id, blind_input.patch_result_id, blind_input.patch_tree_hash, blind_input.changed_files, reconstructed_problem, intents, assumptions, risks, contradiction_ids, evidence_refs, uncertainty, confidence, confidence_basis, provenance, _hash_payload(payload))

    def verify_hash(self) -> bool:
        payload = {
            "reconstruction_id": self.reconstruction_id,
            "episode_id": self.episode_id,
            "patch_result_id": self.patch_result_id,
            "patch_tree_hash": self.patch_tree_hash,
            "changed_files": list(self.changed_files),
            "reconstructed_problem": self.reconstructed_problem,
            "intents": [item.canonical_payload() for item in self.intents],
            "assumptions": list(self.assumptions),
            "risks": list(self.risks),
            "contradiction_ids": list(self.contradiction_ids),
            "evidence_refs": list(self.evidence_refs),
            "uncertainty": self.uncertainty,
            "confidence": self.confidence,
            "confidence_basis": list(self.confidence_basis),
            "provenance": self.provenance.canonical_payload(),
        }
        return self.canonical_hash == _hash_payload(payload)


@dataclass(frozen=True)
class ForwardIntent:
    """Typed, hash-sealed forward view used by D4C alignment."""

    episode_id: str
    patch_result_id: str
    task_contract_revision_id: str
    task_contract_hash: str
    expected_requirement_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    canonical_hash: str

    @classmethod
    def create(
        cls,
        *,
        episode_id: str,
        patch_result_id: str,
        task_contract_revision_id: str,
        task_contract_hash: str,
        expected_requirement_ids: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
    ) -> "ForwardIntent":
        payload = {
            "episode_id": str(episode_id),
            "patch_result_id": str(patch_result_id),
            "task_contract_revision_id": str(task_contract_revision_id),
            "task_contract_hash": str(task_contract_hash),
            "expected_requirement_ids": sorted(str(item) for item in expected_requirement_ids),
            "evidence_refs": sorted(str(item) for item in evidence_refs),
        }
        return cls(
            payload["episode_id"],
            payload["patch_result_id"],
            payload["task_contract_revision_id"],
            payload["task_contract_hash"],
            tuple(payload["expected_requirement_ids"]),
            tuple(payload["evidence_refs"]),
            _hash_payload(payload),
        )

    def verify_hash(self) -> bool:
        payload = {
            "episode_id": self.episode_id,
            "patch_result_id": self.patch_result_id,
            "task_contract_revision_id": self.task_contract_revision_id,
            "task_contract_hash": self.task_contract_hash,
            "expected_requirement_ids": list(self.expected_requirement_ids),
            "evidence_refs": list(self.evidence_refs),
        }
        return self.canonical_hash == _hash_payload(payload)


@dataclass(frozen=True)
class DisagreementRecord:
    disagreement_id: str
    episode_id: str
    reconstruction_id: str
    task_contract_revision_id: str
    kind: str
    left_ref: str
    right_ref: str
    evidence_refs: tuple[str, ...]
    canonical_hash: str


@dataclass(frozen=True)
class AdversarialEvaluation:
    episode_id: str
    task_contract_revision_id: str
    patch_result_id: str
    structural_evaluation_id: str
    behavioral_evaluation_id: str | None
    requirement_audit_id: str | None
    forward_intent_hash: str
    backward_reconstruction_hash: str
    verdict: AlignmentVerdict
    matched_requirement_ids: tuple[str, ...]
    unmatched_requirement_ids: tuple[str, ...]
    contradiction_ids: tuple[str, ...]
    disagreement_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    provenance: VerifierProvenance
    canonical_hash: str

    def verify_hash(self) -> bool:
        return self.canonical_hash == _hash_payload(_evaluation_payload(self))


class AlignmentEvaluator:
    """Deterministic envelope; semantic reconstruction remains provider-owned."""

    def evaluate(self, *, forward_intent: ForwardIntent, reconstruction: ReconstructionRecord, task_contract_revision_id: str, structural_evaluation_id: str, behavioral_evaluation_id: str | None, requirement_audit_id: str | None, known_evidence_refs: set[str], provenance: VerifierProvenance, disagreement_ids: tuple[str, ...] = ()) -> AdversarialEvaluation:
        if not forward_intent.verify_hash():
            raise AdversarialContractError("forward intent hash mismatch")
        if not reconstruction.verify_hash():
            raise AdversarialContractError("reconstruction canonical hash mismatch")
        if provenance.input_hash != reconstruction.provenance.input_hash:
            raise AdversarialContractError("alignment provenance input mismatch")
        if reconstruction.episode_id != forward_intent.episode_id or reconstruction.patch_result_id != forward_intent.patch_result_id:
            raise AdversarialContractError("episode or patch identity mismatch")
        forward_hash = forward_intent.canonical_hash
        evidence = set(reconstruction.evidence_refs)
        if not evidence.issubset(known_evidence_refs):
            raise AdversarialContractError("alignment references unknown evidence")
        required = set(forward_intent.expected_requirement_ids)
        reconstructed = {item for intent in reconstruction.intents for item in intent.requirement_ids}
        matched = tuple(sorted(required & reconstructed))
        unmatched = tuple(sorted(required - reconstructed))
        contradiction_ids = tuple(sorted(reconstruction.contradiction_ids))
        confidence_invalid = reconstruction.confidence is not None and not reconstruction.confidence_basis
        if confidence_invalid or not reconstruction.intents or not evidence or reconstruction.uncertainty or disagreement_ids:
            verdict = AlignmentVerdict.INCONCLUSIVE
        elif contradiction_ids or (required and not matched):
            verdict = AlignmentVerdict.MISALIGNED
        elif unmatched:
            verdict = AlignmentVerdict.PARTIAL
        else:
            verdict = AlignmentVerdict.ALIGNED
        result = AdversarialEvaluation(reconstruction.episode_id, task_contract_revision_id, reconstruction.patch_result_id, structural_evaluation_id, behavioral_evaluation_id, requirement_audit_id, forward_hash, reconstruction.canonical_hash, verdict, matched, unmatched, contradiction_ids, tuple(disagreement_ids), tuple(sorted(evidence)), provenance, "")
        return replace(result, canonical_hash=_hash_payload(_evaluation_payload(result)))


def _evaluation_payload(value: AdversarialEvaluation) -> dict[str, Any]:
    return {
        "episode_id": value.episode_id,
        "task_contract_revision_id": value.task_contract_revision_id,
        "patch_result_id": value.patch_result_id,
        "structural_evaluation_id": value.structural_evaluation_id,
        "behavioral_evaluation_id": value.behavioral_evaluation_id,
        "requirement_audit_id": value.requirement_audit_id,
        "forward_intent_hash": value.forward_intent_hash,
        "backward_reconstruction_hash": value.backward_reconstruction_hash,
        "verdict": value.verdict.value,
        "matched_requirement_ids": list(value.matched_requirement_ids),
        "unmatched_requirement_ids": list(value.unmatched_requirement_ids),
        "contradiction_ids": list(value.contradiction_ids),
        "disagreement_ids": list(value.disagreement_ids),
        "evidence_refs": list(value.evidence_refs),
        "provenance": value.provenance.canonical_payload(),
    }


def _find_forbidden_keys(value: Any, forbidden: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in forbidden:
                found.append(str(key))
            found.extend(_find_forbidden_keys(child, forbidden))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_find_forbidden_keys(child, forbidden))
    return found
