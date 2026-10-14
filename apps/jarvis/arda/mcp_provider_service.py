"""Core-owned provider evaluation request for the guarded MCP profile."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from arda.capabilities import CapabilityRegistryError, ProviderDescriptor, ProviderEvaluationRecord, SQLiteCapabilityStore
from arda.core import Database, PolicyEngine, PolicyLevel
from arda.evaluation_route import create_evaluation_route
from arda.episodes import EpisodeStatus, EpisodeStore
from arda.hermes_provider import HERMES_AGENT_LOOP_CAPABILITY
from arda.providers import OllamaNativeGateway, OllamaProviderAdapter, ProviderBoundary, ProviderRequest, ProviderResultStatus, SQLiteProviderOutputStore
from arda.provider_verification import ProviderVerificationBridge, ProviderVerificationRequest, ProviderVerificationStatus
from arda.route_decision import CoreRoutePolicy, RouteDecisionStatus


class CoreProviderEvaluationError(ValueError):
    """A guarded provider evaluation request cannot be accepted."""


PROVIDER_ID = "ollama-qwen3.5-4b"
MODEL = "qwen3.5:4b"
MODEL_DIGEST = "2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd"


class CoreProviderEvaluationService:
    """Only one fixed fixture may invoke the provider boundary."""

    def __init__(self, db: Database, *, ollama_url: str = "http://127.0.0.1:11434"):
        self.db = db
        self.ollama_url = ollama_url

    def evaluate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        required = ("episode_id", "task_contract_hash", "evaluation_id", "fixture_id")
        values = {key: arguments.get(key) for key in required}
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            raise CoreProviderEvaluationError("episode_id, task_contract_hash, evaluation_id and fixture_id are required")
        if values["fixture_id"] != "hermes_core_provider_v0.1":
            raise CoreProviderEvaluationError("unsupported provider evaluation fixture")
        existing = self.db.execute("SELECT evaluation_json FROM provider_evaluations WHERE evaluation_id=?", (values["evaluation_id"],)).fetchone()
        if existing is not None:
            payload = json.loads(existing["evaluation_json"])
            return {"status": "EVALUATION_ALREADY_RECORDED", "evaluation_id": payload["evaluation_id"], "canonical_hash": payload["canonical_hash"]}

        episode = EpisodeStore(self.db).get(values["episode_id"])
        if episode.task_contract_hash != values["task_contract_hash"]:
            raise CoreProviderEvaluationError("task contract hash does not match episode")
        provider = ProviderDescriptor(
            provider_id=PROVIDER_ID,
            provider_kind="local_model",
            identity={"model": MODEL, "digest": MODEL_DIGEST, "environment": "rpi5"},
            capabilities=("agent.hermes_loop",),
            constraints={"context_window": 262144, "tool_calling": True, "structured_messages": True, "provenance": True, "local_only": True},
        )
        capabilities = SQLiteCapabilityStore(self.db)
        for append in (lambda: capabilities.append_capability(HERMES_AGENT_LOOP_CAPABILITY), lambda: capabilities.append_provider(provider)):
            try:
                append()
            except CapabilityRegistryError:
                pass
        route = create_evaluation_route(
            provider=provider, episode_id=episode.episode_id,
            task_contract_revision_id=episode.task_contract_revision_id,
            task_contract_hash=episode.task_contract_hash,
            required_capability="agent.hermes_loop",
            proposal_id=f"proposal-{values['evaluation_id']}",
            expires_at="2099-01-01T00:00:00+00:00", privacy="private",
        )
        decision = CoreRoutePolicy(PolicyEngine({"provider.evaluate": PolicyLevel.AUTO})).decide(
            route.proposal, decision_id=f"decision-{values['evaluation_id']}", action="provider.evaluate"
        )
        if decision.status is not RouteDecisionStatus.ALLOW:
            raise CoreProviderEvaluationError("Core policy did not allow controlled evaluation")

        system_prompt = "Return JSON only with status READY, answer, and evidence_ids [core-e2e-fixture]. Do not claim execution or invent evidence."
        user_prompt = "Run the fixed Core provider evaluation fixture and return the required structured response."
        manifest = (
            {"role": "system", "ref": f"memory://mcp-evaluation/{values['evaluation_id']}/system", "sha256": _sha(system_prompt)},
            {"role": "user", "ref": f"memory://mcp-evaluation/{values['evaluation_id']}/user", "sha256": _sha(user_prompt)},
        )
        prompts = {item["ref"]: content for item, content in zip(manifest, (system_prompt, user_prompt))}
        request = ProviderRequest(
            request_id=f"provider-request-{values['evaluation_id']}", provider_id=PROVIDER_ID,
            episode_id=episode.episode_id, capability_id="agent.hermes_loop",
            task_contract_hash=episode.task_contract_hash, route_proposal_id=route.proposal.proposal_id,
            route_decision_id=decision.decision_id, route_decision_hash=decision.canonical_hash,
            policy_snapshot_hash=decision.policy_snapshot_hash, input_manifest=manifest,
            allowed_outputs=("application/json",), timeout_seconds=240,
        )
        output_store = SQLiteProviderOutputStore(self.db)
        adapter = OllamaProviderAdapter(
            PROVIDER_ID, model=MODEL, gateway=OllamaNativeGateway(self.ollama_url), input_loader=prompts.__getitem__,
            output_store=output_store,
            response_schema={"type": "object", "required": ["status", "answer", "evidence_ids"], "properties": {
                "status": {"type": "string", "enum": ["READY"]}, "answer": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
            }}, max_output_tokens=256,
        )
        result = ProviderBoundary().invoke(decision, request, adapter)
        if result.status is not ProviderResultStatus.RETURNED:
            raise CoreProviderEvaluationError(f"provider returned {result.status.value}")
        artifact = output_store.get_by_request_id(request.request_id)
        if artifact is None:
            raise CoreProviderEvaluationError("provider output artifact was not materialized")

        def verify(loaded):
            payload = json.loads(loaded.content.decode("utf-8"))
            if payload.get("status") != "READY" or not isinstance(payload.get("answer"), str) or payload.get("evidence_ids") != ["core-e2e-fixture"]:
                return ProviderVerificationStatus.FAIL, ("structured_response_invalid",), "mcp-core-e2e-verifier-v0.1"
            return ProviderVerificationStatus.PASS, ("core-e2e-fixture",), "mcp-core-e2e-verifier-v0.1"

        verification = ProviderVerificationBridge(output_store).verify(
            ProviderVerificationRequest(episode.episode_id, request.request_id, artifact.artifact_id, "structured_provider_output", "arda-core-mcp-verifier", decision.policy_snapshot_hash),
            result, verify,
        )
        if verification.status is not ProviderVerificationStatus.PASS:
            raise CoreProviderEvaluationError("provider verification did not pass")
        record = ProviderEvaluationRecord(
            evaluation_id=values["evaluation_id"], provider_id=PROVIDER_ID, provider_snapshot_hash=provider.canonical_hash,
            capability_id="agent.hermes_loop", capability_version="0.1", fixture_set_hash=_sha("hermes_core_provider_v0.1"),
            contract_version="nous-hermes-provider-v0.1", accepted=True,
            metrics={"evaluation_stage": "mcp_core_provider_boundary", "fixture_pass_rate": 1.0, "tool_violation_rate": 0.0, "timeout_rate": 0.0, "malformed_response_rate": 0.0, "latency_p95_ms": float(result.latency_ms or 0), "artifact_hash": artifact.output_hash, "verification_status": verification.status.value},
            evaluator_version="mcp-core-e2e-verifier-v0.1",
        )
        capabilities.append_evaluation(record)
        EpisodeStore(self.db).append_event(
            episode.episode_id, event_type="provider.evaluation.completed", status=EpisodeStatus.DONE,
            payload={"evaluation_id": record.evaluation_id, "evaluation_hash": record.canonical_hash},
            idempotency_key=f"{record.evaluation_id}:completed",
        )
        return {"status": "EVALUATION_COMPLETED", "evaluation_id": record.evaluation_id, "provider_id": PROVIDER_ID, "route_status": decision.status.value, "verification": verification.status.value, "artifact_id": artifact.artifact_id, "artifact_hash": artifact.output_hash, "evaluation_hash": record.canonical_hash, "routing_enabled": False}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
