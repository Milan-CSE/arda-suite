import dataclasses
import hashlib
import sqlite3

import pytest

from arda.providers import (
    FakeProviderAdapter,
    OllamaProviderAdapter,
    ProviderBoundary,
    ProviderBoundaryError,
    ProviderOutputArtifact,
    ProviderRequest,
    ProviderResult,
    ProviderResultStatus,
    SQLiteProviderOutputStore,
)
from arda.core import Database
from arda.route_decision import CoreRoutePolicy, RouteDecisionStatus
from arda.research_synthesis import ModelProviderError, OllamaNativeGateway
from tests.test_route_decision import proposal


def request_for(decision):
    return ProviderRequest(
        request_id="provider-request-1",
        provider_id=decision.provider_id,
        episode_id=decision.episode_id,
        capability_id="coding.review",
        task_contract_hash="contract-hash",
        route_proposal_id=decision.proposal_id,
        route_decision_id=decision.decision_id,
        route_decision_hash=decision.canonical_hash,
        policy_snapshot_hash=decision.policy_snapshot_hash,
        input_manifest=({"ref": "context-1", "sha256": "context-hash"},),
        allowed_outputs=("provider-result",),
    )


def allowed_decision():
    from arda.core import PolicyEngine, PolicyLevel

    return CoreRoutePolicy(PolicyEngine({"provider.invoke": PolicyLevel.AUTO})).decide(
        proposal(), decision_id="decision-1"
    )


def test_provider_boundary_invokes_only_allowed_route_and_returns_untrusted_result():
    decision = allowed_decision()
    adapter = FakeProviderAdapter("local-a")
    result = ProviderBoundary().invoke(decision, request_for(decision), adapter)
    assert result.status is ProviderResultStatus.RETURNED
    assert result.output_hash
    assert adapter.calls == 1


def test_approval_required_route_never_calls_adapter():
    decision = CoreRoutePolicy().decide(proposal(), decision_id="decision-approval")
    adapter = FakeProviderAdapter("local-a")
    with pytest.raises(ProviderBoundaryError, match="ALLOW"):
        ProviderBoundary().invoke(decision, request_for(decision), adapter)
    assert adapter.calls == 0


def test_denied_route_never_calls_adapter():
    from arda.core import PolicyEngine, PolicyLevel

    decision = CoreRoutePolicy(PolicyEngine({"provider.invoke": PolicyLevel.DENY})).decide(
        proposal(), decision_id="decision-deny"
    )
    adapter = FakeProviderAdapter("local-a")
    with pytest.raises(ProviderBoundaryError, match="ALLOW"):
        ProviderBoundary().invoke(decision, request_for(decision), adapter)
    assert adapter.calls == 0


@pytest.mark.parametrize("status", [
    ProviderResultStatus.TIMEOUT,
    ProviderResultStatus.UNAVAILABLE,
    ProviderResultStatus.MALFORMED,
    ProviderResultStatus.RATE_LIMITED,
])
def test_failure_statuses_are_explicit_and_not_verified(status):
    decision = allowed_decision()
    result = ProviderBoundary().invoke(
        decision, request_for(decision), FakeProviderAdapter("local-a", status=status, error_class=status.value)
    )
    assert result.status is status
    assert result.output_hash is None


def test_request_or_decision_binding_mismatch_is_rejected():
    decision = allowed_decision()
    tampered = dataclasses.replace(request_for(decision), task_contract_hash="other")
    with pytest.raises(ProviderBoundaryError, match="task contract"):
        ProviderBoundary().invoke(decision, tampered, FakeProviderAdapter("local-a"))


def test_wrong_adapter_identity_is_rejected_before_provider_call():
    decision = allowed_decision()
    adapter = FakeProviderAdapter("different-provider")
    with pytest.raises(ProviderBoundaryError, match="adapter provider_id"):
        ProviderBoundary().invoke(decision, request_for(decision), adapter)
    assert adapter.calls == 0


def test_ollama_adapter_translates_gateway_response_to_untrusted_result():
    decision = allowed_decision()
    request = dataclasses.replace(
        request_for(decision),
        input_manifest=(
            {"ref": "system-ref", "sha256": "system-hash", "role": "system"},
            {"ref": "user-ref", "sha256": "user-hash", "role": "user"},
        ),
    )
    seen = {}

    def transport(**kwargs):
        seen.update(kwargs)
        return {"message": {"content": '{"status":"OK"}'}, "model": "qwen3:1.7b", "eval_count": 4}

    adapter = OllamaProviderAdapter(
        "local-a",
        model="qwen3:1.7b",
        gateway=OllamaNativeGateway("http://127.0.0.1:11434", transport=transport),
        input_loader={"system-ref": "system", "user-ref": "user"}.get,
    )
    result = ProviderBoundary().invoke(decision, request, adapter)
    assert result.status is ProviderResultStatus.RETURNED
    assert result.output_hash
    assert result.token_usage == {"output_tokens": 4}
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    assert seen["payload"]["model"] == "qwen3:1.7b"
    assert seen["payload"]["messages"][0]["content"] == "system"


def test_ollama_adapter_maps_provider_failure_without_retry_or_fabricated_output():
    decision = allowed_decision()
    request = dataclasses.replace(
        request_for(decision),
        input_manifest=(
            {"ref": "system-ref", "sha256": "system-hash", "role": "system"},
            {"ref": "user-ref", "sha256": "user-hash", "role": "user"},
        ),
    )
    calls = 0

    def transport(**_kwargs):
        nonlocal calls
        calls += 1
        raise ModelProviderError("provider unavailable")

    adapter = OllamaProviderAdapter(
        "local-a",
        model="qwen3:1.7b",
        gateway=OllamaNativeGateway("http://127.0.0.1:11434", transport=transport),
        input_loader=lambda ref: ref,
    )
    result = ProviderBoundary().invoke(decision, request, adapter)
    assert result.status is ProviderResultStatus.UNAVAILABLE
    assert result.output_hash is None
    assert calls == 1


def test_ollama_adapter_replays_materialized_request_without_new_inference(tmp_path):
    database = Database(tmp_path / "provider-replay.sqlite3")
    database.migrate()
    try:
        decision = allowed_decision()
        request = dataclasses.replace(
            request_for(decision),
            input_manifest=(
                {"ref": "system-ref", "sha256": "system-hash", "role": "system"},
                {"ref": "user-ref", "sha256": "user-hash", "role": "user"},
            ),
        )
        calls = 0

        def transport(**_kwargs):
            nonlocal calls
            calls += 1
            return {"message": {"content": '{"status":"OK"}'}, "model": "qwen3:1.7b", "eval_count": 4}

        store = SQLiteProviderOutputStore(database)
        adapter = OllamaProviderAdapter(
            "local-a", model="qwen3:1.7b",
            gateway=OllamaNativeGateway("http://127.0.0.1:11434", transport=transport),
            input_loader=lambda ref: ref, output_store=store,
        )
        first = ProviderBoundary().invoke(decision, request, adapter)
        store.materialize(request, first, b'{"status":"OK"}', model_snapshot="qwen3:1.7b@digest")
        second = ProviderBoundary().invoke(decision, request, adapter)
        assert calls == 1
        assert second.output_ref == first.output_ref.replace("ollama://", "provider-artifact://")
        assert second.output_hash == first.output_hash
    finally:
        database.close()


def test_provider_output_store_materializes_raw_bytes_and_rejects_wrong_hash(tmp_path):
    database = Database(tmp_path / "provider-output.sqlite3")
    database.migrate()
    try:
        decision = allowed_decision()
        request = request_for(decision)
        content = b'{"status":"OK"}'
        result = ProviderResult(
            request_id=request.request_id,
            provider_id=request.provider_id,
            request_hash=request.request_hash,
            status=ProviderResultStatus.RETURNED,
            output_ref="ollama://response-1",
            output_hash=hashlib.sha256(content).hexdigest(),
            latency_ms=10,
        )
        store = SQLiteProviderOutputStore(database)
        artifact = store.materialize(request, result, content, model_snapshot="qwen3:1.7b@digest")
        assert isinstance(artifact, ProviderOutputArtifact)
        assert artifact.content == content
        assert store.get(artifact.artifact_id).output_hash == result.output_hash
        assert store.materialize(request, result, content, model_snapshot="qwen3:1.7b@digest").artifact_id == artifact.artifact_id
        with pytest.raises(ProviderBoundaryError, match="content hash"):
            store.materialize(request, result, b"tampered", model_snapshot="qwen3:1.7b@digest")
        with pytest.raises(sqlite3.IntegrityError):
            database.execute("UPDATE provider_output_artifacts SET content=? WHERE artifact_id=?", (b"tampered", artifact.artifact_id))
    finally:
        database.close()
