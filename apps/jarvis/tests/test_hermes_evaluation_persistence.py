import pytest

from arda.capabilities import (
    CapabilityDescriptor,
    CapabilityLifecycle,
    CapabilityRegistryError,
    ProviderDescriptor,
    SQLiteCapabilityStore,
)
from arda.core import Database
from arda.hermes_evaluation import (
    HERMES_PROVIDER_FIXTURES,
    build_provider_evaluation,
    build_provider_metadata_evaluation,
    evaluate_tool_trace,
)


def test_hermes_fixture_batch_becomes_existing_provider_evaluation_record(tmp_path):
    db = Database(tmp_path / "capabilities.sqlite3")
    db.migrate()
    store = SQLiteCapabilityStore(db)
    store.append_capability(CapabilityDescriptor(
        capability_id="agent.hermes_loop",
        version="0.1",
        description="Hermes loop",
        input_contract="NousHermesProviderRequest-v0.1",
        output_contract="ProviderResult-v0.1",
        lifecycle=CapabilityLifecycle.CANDIDATE,
    ))
    store.append_provider(ProviderDescriptor(
        provider_id="candidate-local",
        provider_kind="model",
        identity={"model": "candidate", "snapshot": "snapshot-1"},
        capabilities=("agent.hermes_loop",),
    ))
    traces = tuple(
        evaluate_tool_trace(item, item.expected_tools, abstained=item.requires_abstention)
        for item in HERMES_PROVIDER_FIXTURES
    )
    record = build_provider_evaluation(
        evaluation_id="hermes-eval-1",
        provider_id="candidate-local",
        provider_snapshot_hash="snapshot-1",
        traces=traces,
        latency_p50_ms=100,
        latency_p95_ms=150,
        timeout_rate=0,
        malformed_response_rate=0,
    )
    store.append_evaluation(record)
    assert store.list_evaluations()[0].accepted is True
    assert store.list_evaluations()[0].metrics["trace_hash"]
    with pytest.raises(CapabilityRegistryError, match="already persisted"):
        store.append_evaluation(record)
    db.close()


def test_metadata_ineligibility_is_persistable_as_existing_evaluation_record(tmp_path):
    db = Database(tmp_path / "capabilities.sqlite3")
    db.migrate()
    store = SQLiteCapabilityStore(db)
    store.append_capability(CapabilityDescriptor(
        capability_id="agent.hermes_loop",
        version="0.1",
        description="Hermes loop",
        input_contract="NousHermesProviderRequest-v0.1",
        output_contract="ProviderResult-v0.1",
        lifecycle=CapabilityLifecycle.CANDIDATE,
    ))
    provider = ProviderDescriptor(
        provider_id="qwen-baseline",
        provider_kind="model",
        identity={"model": "qwen3:1.7b", "snapshot": "snapshot-qwen"},
        capabilities=("agent.hermes_loop",),
        constraints={
            "context_window": 40960,
            "tool_calling": True,
            "structured_messages": True,
            "provenance": True,
        },
    )
    store.append_provider(provider)
    record = build_provider_metadata_evaluation(
        evaluation_id="metadata-qwen-baseline-1",
        provider=provider,
        provider_snapshot_hash="snapshot-qwen",
    )
    store.append_evaluation(record)
    persisted = store.list_evaluations()[0]
    assert persisted.accepted is False
    assert persisted.metrics["evaluation_stage"] == "metadata_eligibility"
    assert "context_window_below_hermes_minimum" in persisted.metrics["eligibility_reasons"]
    db.close()
