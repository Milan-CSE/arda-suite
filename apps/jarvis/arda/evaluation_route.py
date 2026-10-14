"""Core-owned bootstrap route for controlled provider evaluation runs."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from arda.hermes_provider import evaluate_hermes_provider
from arda.routing import RouteCandidate, RouteProposal, RouteRequest
from arda.capabilities import ProviderDescriptor


class EvaluationRouteError(ValueError):
    """A provider cannot enter the controlled evaluation route."""


@dataclass(frozen=True)
class EvaluationRoute:
    proposal: RouteProposal
    purpose: str = "provider_evaluation"


def create_evaluation_route(
    *,
    provider: ProviderDescriptor,
    episode_id: str,
    task_contract_revision_id: str,
    task_contract_hash: str,
    required_capability: str,
    proposal_id: str,
    expires_at: str,
    privacy: str = "private",
    minimum_context_window: int = 64000,
) -> EvaluationRoute:
    """Create a candidate-only route; it never creates a validated mapping."""
    if required_capability not in provider.capabilities:
        raise EvaluationRouteError("provider has not declared required evaluation capability")
    if required_capability != "agent.hermes_loop":
        raise EvaluationRouteError("unsupported evaluation capability")
    eligibility = evaluate_hermes_provider(
        provider, minimum_context_window=minimum_context_window
    )
    if not eligibility.eligible:
        raise EvaluationRouteError("provider metadata is not eligible: " + ",".join(eligibility.reasons))
    if privacy == "private" and provider.constraints.get("local_only") is not True:
        raise EvaluationRouteError("private evaluation requires a local-only provider")
    request = RouteRequest(
        episode_id=episode_id,
        task_contract_revision_id=task_contract_revision_id,
        task_contract_hash=task_contract_hash,
        required_capabilities=(required_capability,),
        privacy=privacy,
        min_context_window=minimum_context_window,
    )
    candidate = RouteCandidate(
        provider_id=provider.provider_id,
        matched_capabilities=(required_capability,),
        reasons=("controlled evaluation candidate; not routing eligible",),
        rank=1,
    )
    registry_hash = _hash({"provider": provider.canonical_hash, "purpose": "provider_evaluation"})
    proposal = RouteProposal.create(
        proposal_id=proposal_id,
        request=request,
        candidates=(candidate,),
        registry_snapshot_hash=registry_hash,
        evaluation_snapshot_hash=_hash({"evaluation_evidence": "pending", "provider": provider.provider_id}),
        expires_at=expires_at,
    )
    return EvaluationRoute(proposal=proposal)


def _hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
