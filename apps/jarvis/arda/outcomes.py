"""Core aggregation of verification evidence into a non-terminal outcome."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from arda.episodes import EpisodeStatus, EpisodeStore
from arda.provider_verification import ProviderVerificationResult, ProviderVerificationStatus


class OutcomeError(ValueError):
    """Verification evidence cannot form a valid Core outcome."""


class OutcomeStatus(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class VerifiedOutcome:
    episode_id: str
    provider_result_id: str
    verification_refs: tuple[str, ...]
    requirement_status: str
    behavioral_status: str
    alignment_status: str
    confidence: float | None
    evidence_refs: tuple[str, ...]
    status: OutcomeStatus
    canonical_hash: str

    @classmethod
    def from_verification(
        cls,
        verification: ProviderVerificationResult,
        *,
        requirement_status: str = "UNVERIFIED",
        behavioral_status: str = "UNVERIFIED",
        alignment_status: str = "UNVERIFIED",
        confidence: float | None = None,
        evidence_refs: Iterable[str] = (),
    ) -> "VerifiedOutcome":
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise OutcomeError("confidence must be between 0 and 1")
        if not verification.verify_hash():
            raise OutcomeError("verification result hash mismatch")
        if verification.status is ProviderVerificationStatus.FAIL:
            status = OutcomeStatus.FAILED
        elif verification.status is ProviderVerificationStatus.INCONCLUSIVE:
            status = OutcomeStatus.INCONCLUSIVE
        elif any(value in {"FAIL", "VIOLATED"} for value in (requirement_status, behavioral_status, alignment_status)):
            status = OutcomeStatus.FAILED
        elif any(value in {"UNVERIFIED", "INCONCLUSIVE"} for value in (requirement_status, behavioral_status, alignment_status)):
            status = OutcomeStatus.INCONCLUSIVE
        else:
            status = OutcomeStatus.VERIFIED
        refs = tuple(dict.fromkeys((*verification.evidence_refs, *evidence_refs)))
        payload = {
            "episode_id": verification.episode_id,
            "provider_result_id": verification.provider_result_id,
            "verification_refs": [verification.canonical_hash],
            "requirement_status": requirement_status,
            "behavioral_status": behavioral_status,
            "alignment_status": alignment_status,
            "confidence": confidence,
            "evidence_refs": list(refs),
            "status": status.value,
        }
        canonical_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(
            episode_id=verification.episode_id,
            provider_result_id=verification.provider_result_id,
            verification_refs=(verification.canonical_hash,),
            requirement_status=requirement_status,
            behavioral_status=behavioral_status,
            alignment_status=alignment_status,
            confidence=confidence,
            evidence_refs=refs,
            status=status,
            canonical_hash=canonical_hash,
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "provider_result_id": self.provider_result_id,
            "verification_refs": list(self.verification_refs),
            "requirement_status": self.requirement_status,
            "behavioral_status": self.behavioral_status,
            "alignment_status": self.alignment_status,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
            "status": self.status.value,
        }

    def verify_hash(self) -> bool:
        expected = hashlib.sha256(
            json.dumps(self.canonical_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return expected == self.canonical_hash


class EpisodeOutcomeRecorder:
    """Append outcome evidence without granting terminal completion."""

    def __init__(self, episodes: EpisodeStore):
        self.episodes = episodes

    def record(self, outcome: VerifiedOutcome):
        if not outcome.verify_hash():
            raise OutcomeError("verified outcome hash mismatch")
        episode = self.episodes.get(outcome.episode_id)
        return self.episodes.append_event(
            outcome.episode_id,
            event_type="episode.verification.completed",
            status=EpisodeStatus.AWAITING_DECISION,
            payload={
                "provider_result_id": outcome.provider_result_id,
                "outcome_status": outcome.status.value,
                "outcome_hash": outcome.canonical_hash,
                "verification_refs": list(outcome.verification_refs),
                "evidence_refs": list(outcome.evidence_refs),
                "requirement_status": outcome.requirement_status,
                "behavioral_status": outcome.behavioral_status,
                "alignment_status": outcome.alignment_status,
            },
            idempotency_key=f"{episode.episode_id}:verification:{outcome.canonical_hash}",
        )
