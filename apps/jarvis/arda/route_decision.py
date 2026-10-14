"""Core-owned policy evaluation for Hermes RouteProposal objects."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from arda.core import PolicyEngine, PolicyLevel
from arda.routing import RouteProposal


class RouteDecisionError(ValueError):
    """A proposal cannot be converted into a Core route decision."""


class RouteDecisionStatus(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"
    DEFER = "DEFER"


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PolicySnapshot:
    action: str
    level: PolicyLevel
    source: str = "core-policy-v0.1"

    @property
    def canonical_hash(self) -> str:
        return _hash({"action": self.action, "level": self.level.value, "source": self.source})


@dataclass(frozen=True)
class RouteDecision:
    decision_id: str
    proposal_id: str
    proposal_hash: str
    episode_id: str
    task_contract_hash: str
    provider_id: str | None
    status: RouteDecisionStatus
    policy_snapshot_hash: str
    approval_required: bool
    approval_payload_hash: str | None
    reason_codes: tuple[str, ...]
    rejected_alternatives: tuple[str, ...]
    canonical_hash: str

    @classmethod
    def create(
        cls,
        *,
        decision_id: str,
        proposal: RouteProposal,
        provider_id: str | None,
        status: RouteDecisionStatus,
        policy_snapshot_hash: str,
        approval_required: bool,
        reason_codes: tuple[str, ...],
        rejected_alternatives: tuple[str, ...],
    ) -> "RouteDecision":
        approval_payload = None
        if approval_required:
            approval_payload = _hash({
                "decision_id": decision_id,
                "proposal_id": proposal.proposal_id,
                "proposal_hash": proposal.canonical_hash,
                "episode_id": proposal.episode_id,
                "provider_id": provider_id,
                "status": status.value,
                "policy_snapshot_hash": policy_snapshot_hash,
            })
        payload = {
            "decision_id": decision_id,
            "proposal_id": proposal.proposal_id,
            "proposal_hash": proposal.canonical_hash,
            "episode_id": proposal.episode_id,
            "task_contract_hash": proposal.task_contract_hash,
            "provider_id": provider_id,
            "status": status.value,
            "policy_snapshot_hash": policy_snapshot_hash,
            "approval_required": approval_required,
            "approval_payload_hash": approval_payload,
            "reason_codes": list(reason_codes),
            "rejected_alternatives": list(rejected_alternatives),
        }
        return cls(
            decision_id=decision_id,
            proposal_id=proposal.proposal_id,
            proposal_hash=proposal.canonical_hash,
            episode_id=proposal.episode_id,
            task_contract_hash=proposal.task_contract_hash,
            provider_id=provider_id,
            status=status,
            policy_snapshot_hash=policy_snapshot_hash,
            approval_required=approval_required,
            approval_payload_hash=approval_payload,
            reason_codes=reason_codes,
            rejected_alternatives=rejected_alternatives,
            canonical_hash=_hash(payload),
        )

    def verify_hash(self) -> bool:
        proposal_stub = {
            "decision_id": self.decision_id,
            "proposal_id": self.proposal_id,
            "proposal_hash": self.proposal_hash,
            "episode_id": self.episode_id,
            "task_contract_hash": self.task_contract_hash,
            "provider_id": self.provider_id,
            "status": self.status.value,
            "policy_snapshot_hash": self.policy_snapshot_hash,
            "approval_required": self.approval_required,
            "approval_payload_hash": self.approval_payload_hash,
            "reason_codes": list(self.reason_codes),
            "rejected_alternatives": list(self.rejected_alternatives),
        }
        return self.canonical_hash == _hash(proposal_stub)


class CoreRoutePolicy:
    """Turn a proposal into a decision without calling a provider."""

    def __init__(self, policy: PolicyEngine | None = None):
        self.policy = policy or PolicyEngine()

    def decide(
        self,
        proposal: RouteProposal,
        *,
        decision_id: str,
        action: str = "provider.invoke",
    ) -> RouteDecision:
        if not proposal.verify_hash():
            raise RouteDecisionError("route proposal hash mismatch")
        if not proposal.candidates:
            snapshot = PolicySnapshot(action, PolicyLevel.DENY)
            return RouteDecision.create(
                decision_id=decision_id, proposal=proposal, provider_id=None,
                status=RouteDecisionStatus.DEFER,
                policy_snapshot_hash=snapshot.canonical_hash,
                approval_required=False,
                reason_codes=("no_eligible_provider",), rejected_alternatives=(),
            )
        level = self.policy.decide(action)
        snapshot = PolicySnapshot(action, level)
        selected = proposal.candidates[0]
        rejected = tuple(item.provider_id for item in proposal.candidates[1:])
        if level is PolicyLevel.DENY:
            status = RouteDecisionStatus.DENY
            approval_required = False
            reasons = ("policy_denied",)
        elif level in (PolicyLevel.CONFIRM, PolicyLevel.HUMAN_ONLY):
            status = RouteDecisionStatus.REQUIRE_APPROVAL
            approval_required = True
            reasons = ("policy_requires_approval",)
        else:
            status = RouteDecisionStatus.ALLOW
            approval_required = False
            reasons = ("policy_auto_allowed",)
        return RouteDecision.create(
            decision_id=decision_id,
            proposal=proposal,
            provider_id=selected.provider_id,
            status=status,
            policy_snapshot_hash=snapshot.canonical_hash,
            approval_required=approval_required,
            reason_codes=reasons,
            rejected_alternatives=rejected,
        )
