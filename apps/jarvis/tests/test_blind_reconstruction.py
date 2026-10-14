"""M2.4D4B blind reconstruction contract tests."""
from __future__ import annotations

import hashlib
import json

import pytest

from arda.adversarial import AdversarialContractError, BlindReviewInput
from arda.blind_reconstruction import (
    BlindReconstructionError,
    BlindReconstructionGateway,
    candidate_schema,
)
from arda.research_synthesis import ModelOutputError, ModelResponse


class FixtureGateway:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return ModelResponse(content, "fixture-model", 10, 20)


class SequenceGateway:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return ModelResponse(self.contents.pop(0), "fixture-model", 10, 20)


def blind_input():
    content = "def answer(): return 2"
    return BlindReviewInput.build({
        "episode_id": "episode-1",
        "patch_result_id": "patch-1",
        "patch_tree_hash": "tree-hash",
        "changed_files": ["module.py"],
        "diff": "--- a/module.py\n+++ b/module.py\n",
        "contexts": [{"reference": "source:module.py", "content_sha256": hashlib.sha256(content.encode()).hexdigest(), "content": content}],
        "evidence_refs": ["behavior:target", "source:module.py"],
    })


def candidate(**overrides):
    value = {
        "reconstructed_problem": "answer returned the wrong value",
        "intents": [{"intent_id": "i1", "summary": "fix answer", "requirement_ids": ["r1"], "evidence_refs": ["behavior:target"], "risk_flags": []}],
        "assumptions": [], "risks": [], "contradiction_ids": [],
        "evidence_refs": ["behavior:target", "source:module.py"],
        "uncertainty": "", "confidence": 0.8, "confidence_basis": ["behavior:target"],
    }
    value.update(overrides)
    return value


def test_real_gateway_adapter_seals_candidate_through_d4a():
    gateway = FixtureGateway(candidate())
    record = BlindReconstructionGateway(gateway, provider="fixture", model_snapshot_hash="snapshot-1").reconstruct(blind_input(), reconstruction_id="reconstruction-1", model="qwen2.5:7b-instruct")
    assert record.verify_hash()
    assert record.provenance.input_hash == blind_input().input_hash
    assert record.provenance.model_snapshot_hash == "snapshot-1"
    assert gateway.calls[0]["temperature"] == 0.0
    assert "requirements" not in gateway.calls[0]["user_prompt"]
    assert "generator_reasoning" not in gateway.calls[0]["user_prompt"]


def test_unknown_evidence_is_rejected_deterministically():
    with pytest.raises(BlindReconstructionError, match="unknown evidence"):
        BlindReconstructionGateway(FixtureGateway(candidate(evidence_refs=["made-up"])), provider="fixture").reconstruct(blind_input(), reconstruction_id="bad", model="m")


def test_confidence_without_basis_is_rejected():
    with pytest.raises(BlindReconstructionError, match="confidence"):
        BlindReconstructionGateway(FixtureGateway(candidate(confidence=0.9, confidence_basis=[])), provider="fixture").reconstruct(blind_input(), reconstruction_id="bad", model="m")


def test_uncertainty_with_high_confidence_is_rejected():
    with pytest.raises(BlindReconstructionError, match="high confidence"):
        BlindReconstructionGateway(FixtureGateway(candidate(uncertainty="context is insufficient")), provider="fixture").reconstruct(blind_input(), reconstruction_id="bad", model="m")


def test_unknown_candidate_field_is_rejected():
    with pytest.raises(BlindReconstructionError, match="unknown fields"):
        BlindReconstructionGateway(FixtureGateway(candidate(deployment_approved=True)), provider="fixture").reconstruct(blind_input(), reconstruction_id="bad", model="m")


def test_invalid_json_is_rejected_without_record():
    with pytest.raises(ModelOutputError, match="not valid JSON"):
        BlindReconstructionGateway(FixtureGateway("not-json"), provider="fixture").reconstruct(blind_input(), reconstruction_id="bad", model="m")


def test_prompt_contains_only_blind_payload():
    gateway = FixtureGateway(candidate())
    BlindReconstructionGateway(gateway, provider="fixture").reconstruct(blind_input(), reconstruction_id="reconstruction-1", model="m")
    prompt = gateway.calls[0]["user_prompt"]
    assert "episode-1" in prompt and "patch-1" in prompt
    for forbidden in ("objective", "task_contract", "forward_intent", "generator_model", "behavioral_verdict"):
        assert forbidden not in prompt


def test_d4a_rejects_blind_input_with_forward_fields_before_gateway():
    with pytest.raises(AdversarialContractError):
        BlindReviewInput.build({"episode_id": "e", "patch_result_id": "p", "patch_tree_hash": "t", "changed_files": [], "diff": "", "forward_intent": {}})


def test_one_bounded_correction_can_recover_a_valid_candidate():
    valid = json.dumps(candidate())
    gateway = SequenceGateway(["not-json", valid])
    adapter = BlindReconstructionGateway(gateway, provider="fixture")
    record = adapter.reconstruct(blind_input(), reconstruction_id="corrected", model="m")
    assert record.verify_hash()
    assert len(gateway.calls) == 2
    assert "contract_correction" in gateway.calls[1]["user_prompt"]
    assert '"requirements"' not in gateway.calls[1]["user_prompt"]
    assert adapter.last_attempts[0].accepted is False
    assert adapter.last_attempts[1].accepted is True
    assert adapter.last_attempts[0].schema_hash == adapter.last_attempts[1].schema_hash


def test_second_contract_failure_is_blocked_and_telemetry_is_retained():
    invalid = json.dumps(candidate(deployment_approved=True))
    gateway = SequenceGateway([invalid, invalid])
    adapter = BlindReconstructionGateway(gateway, provider="fixture")
    with pytest.raises(BlindReconstructionError, match="unknown fields"):
        adapter.reconstruct(blind_input(), reconstruction_id="blocked", model="m")
    assert len(gateway.calls) == 2
    assert len(adapter.last_attempts) == 2
    assert all(attempt.accepted is False for attempt in adapter.last_attempts)


def test_canonical_schema_requires_nested_intent_id():
    schema = candidate_schema(["source:module.py"])
    intent = schema["properties"]["intents"]["items"]
    assert "intent_id" in intent["required"]
    assert intent["additionalProperties"] is False
