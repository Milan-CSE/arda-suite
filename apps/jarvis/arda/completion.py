"""Deterministic policy separating verified evidence from episode completion."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from arda.episodes import EpisodeStatus, EpisodeStore
from arda.outcomes import OutcomeStatus, VerifiedOutcome


class CompletionError(ValueError):
    """Completion policy input or transition is invalid."""


class CompletionStatus(str, Enum):
    DONE = "DONE"
    FAILED = "FAILED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class CompletionDecision:
    episode_id: str
    outcome_hash: str
    status: CompletionStatus
    reason: str
    canonical_hash: str

    def canonical_payload(self) -> dict[str, str]:
        return {
            "episode_id": self.episode_id,
            "outcome_hash": self.outcome_hash,
            "status": self.status.value,
            "reason": self.reason,
        }

    def verify_hash(self) -> bool:
        expected = hashlib.sha256(
            json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return expected == self.canonical_hash


class EpisodeCompletionPolicy:
    """Require explicit policy permission before turning evidence into DONE."""

    def decide(
        self,
        outcome: VerifiedOutcome,
        *,
        automatic_completion_allowed: bool = False,
        human_approved: bool = False,
    ) -> CompletionDecision:
        if not outcome.verify_hash():
            raise CompletionError("verified outcome hash mismatch")
        if outcome.status is OutcomeStatus.FAILED:
            status, reason = CompletionStatus.FAILED, "verification_failed"
        elif outcome.status is OutcomeStatus.INCONCLUSIVE:
            status, reason = CompletionStatus.INCOMPLETE, "verification_inconclusive"
        elif human_approved:
            status, reason = CompletionStatus.DONE, "human_approved_verified_outcome"
        elif automatic_completion_allowed:
            status, reason = CompletionStatus.DONE, "policy_allowed_verified_outcome"
        else:
            status, reason = CompletionStatus.NEEDS_HUMAN, "verified_outcome_requires_completion_approval"
        payload = {
            "episode_id": outcome.episode_id,
            "outcome_hash": outcome.canonical_hash,
            "status": status.value,
            "reason": reason,
        }
        canonical_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return CompletionDecision(outcome.episode_id, outcome.canonical_hash, status, reason, canonical_hash)


class EpisodeCompletionRecorder:
    """Persist a completion decision as an event; never bypasses policy."""

    def __init__(self, episodes: EpisodeStore):
        self.episodes = episodes

    def record(self, decision: CompletionDecision):
        if not decision.verify_hash():
            raise CompletionError("completion decision hash mismatch")
        status_map = {
            CompletionStatus.DONE: EpisodeStatus.DONE,
            CompletionStatus.FAILED: EpisodeStatus.FAILED,
            CompletionStatus.NEEDS_HUMAN: EpisodeStatus.AWAITING_DECISION,
            CompletionStatus.INCOMPLETE: EpisodeStatus.INCOMPLETE,
        }
        return self.episodes.append_event(
            decision.episode_id,
            event_type="episode.completion.decided",
            status=status_map[decision.status],
            payload={
                "completion_status": decision.status.value,
                "reason": decision.reason,
                "outcome_hash": decision.outcome_hash,
                "decision_hash": decision.canonical_hash,
            },
            idempotency_key=f"{decision.episode_id}:completion:{decision.canonical_hash}",
        )
