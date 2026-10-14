import dataclasses

import pytest

from arda.core import PolicyEngine, PolicyLevel
from arda.route_decision import CoreRoutePolicy, RouteDecisionError, RouteDecisionStatus
from arda.routing import DeterministicRouter, EvaluationMemory, RouteRequest
from tests.test_routing import mapping, provider, record


def proposal():
    return DeterministicRouter().propose(
        RouteRequest(
            episode_id="episode-1",
            task_contract_revision_id="revision-1",
            task_contract_hash="contract-hash",
            required_capabilities=("coding.review",),
            privacy="private",
        ),
        providers=(provider("local-a"),),
        mappings=(mapping("local-a"),),
        memory=EvaluationMemory.from_records((record("local-a", "eval-1"),)),
        proposal_id="proposal-1",
        expires_at="later",
    )


def test_route_decision_requires_approval_and_binds_payload_to_proposal():
    decision = CoreRoutePolicy().decide(proposal(), decision_id="decision-1")
    assert decision.status is RouteDecisionStatus.REQUIRE_APPROVAL
    assert decision.provider_id == "local-a"
    assert decision.approval_required is True
    assert decision.approval_payload_hash
    assert decision.verify_hash()


def test_route_decision_denies_without_provider_execution():
    policy = PolicyEngine({"provider.invoke": PolicyLevel.DENY})
    decision = CoreRoutePolicy(policy).decide(proposal(), decision_id="decision-2")
    assert decision.status is RouteDecisionStatus.DENY
    assert decision.provider_id == "local-a"
    assert decision.approval_required is False


def test_route_decision_allows_only_when_core_policy_explicitly_allows():
    policy = PolicyEngine({"provider.invoke": PolicyLevel.AUTO})
    decision = CoreRoutePolicy(policy).decide(proposal(), decision_id="decision-auto")
    assert decision.status is RouteDecisionStatus.ALLOW
    assert decision.approval_required is False


def test_route_decision_defers_when_proposal_has_no_eligible_candidate():
    empty = dataclasses.replace(proposal(), candidates=())
    # Re-seal the test fixture because tampering is otherwise rejected first.
    empty = type(proposal()).create(
        proposal_id=empty.proposal_id, request=RouteRequest(
            episode_id=empty.episode_id,
            task_contract_revision_id=empty.task_contract_revision_id,
            task_contract_hash=empty.task_contract_hash,
            required_capabilities=("coding.review",),
        ), candidates=(), registry_snapshot_hash=empty.registry_snapshot_hash,
        evaluation_snapshot_hash=empty.evaluation_snapshot_hash, expires_at=empty.expires_at,
    )
    decision = CoreRoutePolicy().decide(empty, decision_id="decision-3")
    assert decision.status is RouteDecisionStatus.DEFER
    assert decision.provider_id is None


def test_tampered_route_proposal_is_rejected():
    tampered = dataclasses.replace(proposal(), task_contract_hash="changed")
    with pytest.raises(RouteDecisionError, match="hash mismatch"):
        CoreRoutePolicy().decide(tampered, decision_id="decision-4")
