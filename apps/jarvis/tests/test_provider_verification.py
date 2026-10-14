import hashlib

import pytest

from arda.core import Database
from arda.provider_verification import (
    ProviderVerificationBridge,
    ProviderVerificationError,
    ProviderVerificationRequest,
    ProviderVerificationStatus,
)
from arda.providers import (
    ProviderResult,
    ProviderResultStatus,
    SQLiteProviderOutputStore,
)
from tests.test_providers import allowed_decision, request_for


def _materialized(tmp_path):
    database = Database(tmp_path / "verification.sqlite3")
    database.migrate()
    decision = allowed_decision()
    request = request_for(decision)
    content = b'{"status":"OK"}'
    result = ProviderResult(
        request_id=request.request_id,
        provider_id=request.provider_id,
        request_hash=request.request_hash,
        status=ProviderResultStatus.RETURNED,
        output_ref="provider-artifact://provider_art_provider-request-1",
        output_hash=hashlib.sha256(content).hexdigest(),
        latency_ms=10,
    )
    store = SQLiteProviderOutputStore(database)
    artifact = store.materialize(request, result, content, model_snapshot="qwen3:1.7b@digest")
    verification_request = ProviderVerificationRequest(
        episode_id=request.episode_id,
        provider_result_id=request.request_id,
        artifact_id=artifact.artifact_id,
        verification_type="json_status",
        verifier_identity="fixture-verifier",
        policy_snapshot_hash=decision.policy_snapshot_hash,
    )
    return database, store, result, verification_request


def test_bridge_returns_separate_verification_result_without_closing_episode(tmp_path):
    database, store, result, request = _materialized(tmp_path)
    try:
        evaluation = ProviderVerificationBridge(store).verify(
            request,
            result,
            lambda artifact: (ProviderVerificationStatus.PASS, (f"sha256:{artifact.output_hash}",), "fixture-v0.1"),
        )
        assert evaluation.status is ProviderVerificationStatus.PASS
        assert evaluation.verify_hash()
        assert evaluation.evidence_refs[0].startswith("sha256:")
    finally:
        database.close()


def test_bridge_rejects_provenance_mismatch_before_verifier(tmp_path):
    database, store, result, request = _materialized(tmp_path)
    try:
        wrong = ProviderVerificationRequest(
            episode_id="other-episode",
            provider_result_id=request.provider_result_id,
            artifact_id=request.artifact_id,
            verification_type=request.verification_type,
            verifier_identity=request.verifier_identity,
            policy_snapshot_hash=request.policy_snapshot_hash,
        )
        with pytest.raises(ProviderVerificationError, match="provenance"):
            ProviderVerificationBridge(store).verify(wrong, result, lambda *_: (_ for _ in ()).throw(AssertionError()))
    finally:
        database.close()
