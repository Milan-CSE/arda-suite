"""Build immutable evaluation records from a completed Core evidence chain."""
from __future__ import annotations

from arda.completion import CompletionDecision, CompletionStatus
from arda.experience import EvaluationRecord, SQLiteEvaluationStore, _hash
from arda.outcomes import VerifiedOutcome
from arda.providers import ProviderOutputArtifact, ProviderRequest, ProviderResult, ProviderResultStatus


class EvaluationIntegrationError(ValueError):
    """The evidence chain is incomplete or internally inconsistent."""


class EpisodeEvaluationIntegration:
    def __init__(self, store: SQLiteEvaluationStore):
        self.store = store

    def record(
        self,
        *,
        evaluation_id: str,
        request: ProviderRequest,
        result: ProviderResult,
        artifact: ProviderOutputArtifact,
        outcome: VerifiedOutcome,
        completion: CompletionDecision,
    ) -> EvaluationRecord:
        if result.status is not ProviderResultStatus.RETURNED:
            raise EvaluationIntegrationError("only returned provider results can produce evaluations")
        if artifact.episode_id != request.episode_id or artifact.request_id != request.request_id:
            raise EvaluationIntegrationError("provider artifact does not match request")
        if result.request_id != request.request_id or result.output_hash != artifact.output_hash:
            raise EvaluationIntegrationError("provider result does not match artifact")
        if outcome.episode_id != request.episode_id or outcome.provider_result_id != request.request_id:
            raise EvaluationIntegrationError("verified outcome does not match provider request")
        if completion.episode_id != request.episode_id or completion.outcome_hash != outcome.canonical_hash:
            raise EvaluationIntegrationError("completion decision does not match verified outcome")
        if not outcome.verify_hash() or not completion.verify_hash():
            raise EvaluationIntegrationError("outcome or completion hash mismatch")
        record = EvaluationRecord(
            evaluation_id=evaluation_id,
            episode_id=request.episode_id,
            provider_id=request.provider_id,
            capability_id=request.capability_id,
            model_snapshot=artifact.model_snapshot,
            route_decision_hash=request.route_decision_hash,
            provider_result_id=result.request_id,
            output_artifact_id=artifact.artifact_id,
            verification_hash=outcome.canonical_hash,
            outcome_status=outcome.status.value,
            completion_status=completion.status.value,
            latency_ms=result.latency_ms,
            token_usage=dict(result.token_usage),
            human_intervention=completion.status is CompletionStatus.NEEDS_HUMAN,
            failure_class=result.error_class if result.status is not ProviderResultStatus.RETURNED else None,
            canonical_hash="",
        )
        canonical_hash = _hash(record.canonical_payload())
        record = EvaluationRecord(**{**record.__dict__, "canonical_hash": canonical_hash})
        return self.store.append(record)
