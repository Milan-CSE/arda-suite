import pytest

from arda.capabilities import ProviderDescriptor
from arda.evaluation_route import EvaluationRouteError, create_evaluation_route
from arda.hermes_provider import HERMES_AGENT_LOOP_CAPABILITY
from arda.route_decision import CoreRoutePolicy, RouteDecisionStatus
from arda.core import PolicyEngine, PolicyLevel


def provider(*, context_window=262144, local_only=True):
    return ProviderDescriptor(
        provider_id="ollama-qwen3.5-4b",
        provider_kind="local_model",
        identity={"model": "qwen3.5:4b", "digest": "digest-4b"},
        capabilities=(HERMES_AGENT_LOOP_CAPABILITY.capability_id,),
        constraints={
            "context_window": context_window,
            "tool_calling": True,
            "structured_messages": True,
            "provenance": True,
            "local_only": local_only,
        },
    )


def route():
    return create_evaluation_route(
        provider=provider(),
        episode_id="episode-eval-1",
        task_contract_revision_id="revision-1",
        task_contract_hash="contract-hash",
        required_capability="agent.hermes_loop",
        proposal_id="proposal-eval-1",
        expires_at="later",
    )


def test_evaluation_route_accepts_candidate_without_validated_mapping():
    evaluation_route = route()
    assert evaluation_route.proposal.candidates[0].provider_id == "ollama-qwen3.5-4b"
    assert "not routing eligible" in evaluation_route.proposal.candidates[0].reasons[0]
    decision = CoreRoutePolicy(PolicyEngine({"provider.evaluate": PolicyLevel.AUTO})).decide(
        evaluation_route.proposal, decision_id="decision-eval-1", action="provider.evaluate"
    )
    assert decision.status is RouteDecisionStatus.ALLOW
    assert decision.provider_id == "ollama-qwen3.5-4b"


def test_evaluation_route_rejects_context_ineligible_candidate():
    with pytest.raises(EvaluationRouteError, match="context_window_below"):
        create_evaluation_route(
            provider=provider(context_window=40960),
            episode_id="episode-eval-1",
            task_contract_revision_id="revision-1",
            task_contract_hash="contract-hash",
            required_capability="agent.hermes_loop",
            proposal_id="proposal-eval-1",
            expires_at="later",
        )


def test_evaluation_route_does_not_bypass_policy():
    decision = CoreRoutePolicy().decide(route().proposal, decision_id="decision-eval-2", action="provider.evaluate")
    assert decision.status is RouteDecisionStatus.REQUIRE_APPROVAL
