import dataclasses

import pytest

from arda.capabilities import (
    ProviderCapabilityMapping,
    ProviderDescriptor,
    ProviderEvaluationRecord,
    ProviderStatus,
)
from arda.routing import DeterministicRouter, EvaluationMemory, RouteRequest, RoutingError


def provider(provider_id, *, local_only=True, capability="coding.review"):
    return ProviderDescriptor(
        provider_id=provider_id,
        provider_kind="model",
        identity={"snapshot": f"snapshot-{provider_id}"},
        capabilities=(capability,),
        constraints={"local_only": local_only},
    )


def provider_with_context(provider_id, context_window):
    return dataclasses.replace(
        provider(provider_id),
        constraints={"local_only": True, "context_window": context_window},
    )


def record(provider_id, evaluation_id, *, accepted=True, first_pass=0.9, latency=100.0):
    return ProviderEvaluationRecord(
        evaluation_id=evaluation_id,
        provider_id=provider_id,
        provider_snapshot_hash=f"snapshot-{provider_id}",
        capability_id="coding.review",
        capability_version="0.1",
        fixture_set_hash="fixture-review-v1",
        contract_version="review-v0.1",
        accepted=accepted,
        metrics={"first_pass_rate": first_pass, "latency_p95_ms": latency},
        evaluator_version="review-evaluator-v1",
    )


def mapping(provider_id, status=ProviderStatus.VALIDATED):
    return ProviderCapabilityMapping(provider_id, "coding.review", "0.1", status, "evidence")


def request(**kwargs):
    required_capabilities = kwargs.pop("required_capabilities", ("coding.review",))
    return RouteRequest(
        episode_id="episode-1",
        task_contract_revision_id="revision-1",
        task_contract_hash="contract-hash",
        required_capabilities=required_capabilities,
        **kwargs,
    )


def test_evaluation_memory_aggregates_metrics_and_is_snapshot_bound():
    memory = EvaluationMemory.from_records((record("local-a", "eval-1"), record("local-a", "eval-2", first_pass=0.7, latency=200)))
    aggregate = memory.aggregate("local-a", "coding.review")
    assert aggregate.sample_count == 2
    assert aggregate.accepted_rate == 1.0
    assert aggregate.metric_averages["first_pass_rate"] == pytest.approx(0.8)
    assert memory.snapshot_hash()


def test_router_ranks_validated_private_candidates_deterministically():
    providers = (provider("local-slow"), provider("local-fast"), provider("cloud", local_only=False))
    mappings = tuple(mapping(item.provider_id) for item in providers)
    records = (
        record("local-slow", "slow-1", first_pass=0.8, latency=300),
        record("local-fast", "fast-1", first_pass=0.95, latency=100),
        record("cloud", "cloud-1", first_pass=1.0, latency=10),
    )
    proposal = DeterministicRouter().propose(
        request=request(), providers=providers, mappings=mappings,
        memory=EvaluationMemory.from_records(records), proposal_id="proposal-1", expires_at="later",
    )
    assert [candidate.provider_id for candidate in proposal.candidates] == ["local-fast", "local-slow"]
    assert proposal.candidates[0].rank == 1
    assert proposal.verify_hash()


def test_router_excludes_unvalidated_and_slow_candidates():
    providers = (provider("local-a"), provider("local-b"))
    mappings = (mapping("local-a", ProviderStatus.CANDIDATE), mapping("local-b"))
    memory = EvaluationMemory.from_records((record("local-b", "eval-b", latency=500),))
    proposal = DeterministicRouter().propose(
        request=request(max_latency_ms=100), providers=providers, mappings=mappings,
        memory=memory, proposal_id="proposal-2", expires_at="later",
    )
    assert proposal.candidates == ()


def test_router_excludes_provider_below_context_window_requirement():
    proposal = DeterministicRouter().propose(
        request=request(min_context_window=64000),
        providers=(provider_with_context("local-a", 40960),),
        mappings=(mapping("local-a"),),
        memory=EvaluationMemory.from_records((record("local-a", "eval-a"),)),
        proposal_id="proposal-context",
        expires_at="later",
    )
    assert proposal.candidates == ()


def test_router_accepts_provider_meeting_context_window_requirement():
    proposal = DeterministicRouter().propose(
        request=request(min_context_window=64000),
        providers=(provider_with_context("local-a", 65536),),
        mappings=(mapping("local-a"),),
        memory=EvaluationMemory.from_records((record("local-a", "eval-a"),)),
        proposal_id="proposal-context-ok",
        expires_at="later",
    )
    assert [candidate.provider_id for candidate in proposal.candidates] == ["local-a"]


def test_route_request_requires_capability():
    with pytest.raises(RoutingError, match="at least one"):
        request(required_capabilities=())
