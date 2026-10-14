from arda.capabilities import ProviderDescriptor
from arda.hermes_provider import HERMES_AGENT_LOOP_CAPABILITY, evaluate_hermes_provider


def provider(constraints):
    return ProviderDescriptor(
        provider_id="candidate-local",
        provider_kind="model",
        identity={"model": "candidate"},
        capabilities=(HERMES_AGENT_LOOP_CAPABILITY.capability_id,),
        constraints=constraints,
    )


def test_hermes_capability_is_typed_and_candidate_lifecycle():
    assert HERMES_AGENT_LOOP_CAPABILITY.capability_id == "agent.hermes_loop"
    assert HERMES_AGENT_LOOP_CAPABILITY.lifecycle.value == "candidate"


def test_qwen_like_context_mismatch_is_not_eligible():
    result = evaluate_hermes_provider(provider({
        "context_window": 40960,
        "tool_calling": True,
        "structured_messages": True,
        "provenance": True,
    }))
    assert result.eligible is False
    assert result.reasons == ("context_window_below_hermes_minimum",)


def test_hermes_provider_requires_all_operational_capabilities():
    result = evaluate_hermes_provider(provider({"context_window": 65536}))
    assert result.eligible is False
    assert set(result.reasons) == {
        "tool_calling_not_proven",
        "structured_messages_not_proven",
        "provenance_not_proven",
    }


def test_provider_is_eligible_only_when_all_requirements_are_proven():
    result = evaluate_hermes_provider(provider({
        "context_window": 65536,
        "tool_calling": True,
        "structured_messages": True,
        "provenance": True,
    }))
    assert result.eligible is True
    assert result.reasons == ()

