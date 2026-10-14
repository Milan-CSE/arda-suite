from arda.hermes_evaluation import HERMES_PROVIDER_FIXTURES, build_provider_evaluation, evaluate_tool_trace
from arda.hermes_provider import evaluate_hermes_provider
from arda.capabilities import ProviderDescriptor
from arda.provider_promotion import PromotionPolicy, PromotionStatus, evaluate_promotion


def _provider():
    return ProviderDescriptor(
        provider_id="candidate-local",
        provider_kind="model",
        identity={"model": "candidate", "snapshot": "snapshot-1"},
        capabilities=("agent.hermes_loop",),
        constraints={
            "context_window": 65536,
            "tool_calling": True,
            "structured_messages": True,
            "provenance": True,
        },
    )


def _record(index: int, *, accepted: bool = True, violation_rate: float = 0.0):
    traces = tuple(
        evaluate_tool_trace(item, item.expected_tools, abstained=item.requires_abstention)
        for item in HERMES_PROVIDER_FIXTURES
    )
    record = build_provider_evaluation(
        evaluation_id=f"hermes-eval-{index}",
        provider_id="candidate-local",
        provider_snapshot_hash="snapshot-1",
        traces=traces,
        latency_p50_ms=100,
        latency_p95_ms=150,
        timeout_rate=0,
        malformed_response_rate=0,
    )
    metrics = dict(record.metrics)
    metrics["tool_violation_rate"] = violation_rate
    return record.__class__(
        evaluation_id=record.evaluation_id,
        provider_id=record.provider_id,
        provider_snapshot_hash=record.provider_snapshot_hash,
        capability_id=record.capability_id,
        capability_version=record.capability_version,
        fixture_set_hash=record.fixture_set_hash,
        contract_version=record.contract_version,
        accepted=accepted,
        metrics=metrics,
        evaluator_version=record.evaluator_version,
    )


def test_fewer_than_three_clean_runs_remains_evaluated_and_not_routing_eligible():
    result = evaluate_promotion(
        provider_id="candidate-local",
        capability_id="agent.hermes_loop",
        capability_version="0.1",
        evaluations=[_record(1), _record(2)],
        metadata_eligibility=evaluate_hermes_provider(_provider()),
    )
    assert result.status is PromotionStatus.EVALUATED
    assert result.routing_eligible is False
    assert result.reasons == ("insufficient_evaluation_samples",)


def test_three_clean_runs_validate_capability_but_do_not_activate_routing():
    result = evaluate_promotion(
        provider_id="candidate-local",
        capability_id="agent.hermes_loop",
        capability_version="0.1",
        evaluations=[_record(1), _record(2), _record(3)],
        metadata_eligibility=evaluate_hermes_provider(_provider()),
    )
    assert result.status is PromotionStatus.VALIDATED
    assert result.routing_eligible is False
    assert result.reasons == ()
    assert result.evidence_ids == ("hermes-eval-1", "hermes-eval-2", "hermes-eval-3")


def test_explicit_policy_is_required_to_activate_routing():
    result = evaluate_promotion(
        provider_id="candidate-local",
        capability_id="agent.hermes_loop",
        capability_version="0.1",
        evaluations=[_record(1), _record(2), _record(3)],
        metadata_eligibility=evaluate_hermes_provider(_provider()),
        policy=PromotionPolicy(routing_activation_enabled=True),
    )
    assert result.status is PromotionStatus.VALIDATED
    assert result.routing_eligible is True


def test_contract_violation_rejects_even_with_enough_samples():
    result = evaluate_promotion(
        provider_id="candidate-local",
        capability_id="agent.hermes_loop",
        capability_version="0.1",
        evaluations=[_record(1, violation_rate=1.0), _record(2), _record(3)],
        metadata_eligibility=evaluate_hermes_provider(_provider()),
    )
    assert result.status is PromotionStatus.REJECTED
    assert result.routing_eligible is False
    assert "tool_violation_rate_above_threshold" in result.reasons


def test_metadata_ineligible_provider_is_rejected_without_provider_call():
    ineligible = evaluate_hermes_provider(
        ProviderDescriptor(
            provider_id="candidate-local",
            provider_kind="model",
            identity={"model": "qwen3:1.7b"},
            capabilities=("agent.hermes_loop",),
            constraints={"context_window": 40960},
        )
    )
    result = evaluate_promotion(
        provider_id="candidate-local",
        capability_id="agent.hermes_loop",
        capability_version="0.1",
        evaluations=[_record(1), _record(2), _record(3)],
        metadata_eligibility=ineligible,
    )
    assert result.status is PromotionStatus.REJECTED
    assert "metadata:context_window_below_hermes_minimum" in result.reasons
