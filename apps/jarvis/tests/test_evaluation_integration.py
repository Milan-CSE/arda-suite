from arda.completion import EpisodeCompletionPolicy, EpisodeCompletionRecorder
from arda.core import Database
from arda.evaluation_integration import EpisodeEvaluationIntegration
from arda.experience import EvaluationMemory, SQLiteEvaluationStore
from arda.episodes import EpisodeStore
from arda.outcomes import EpisodeOutcomeRecorder, VerifiedOutcome
from arda.provider_verification import ProviderVerificationBridge, ProviderVerificationRequest, ProviderVerificationStatus
from arda.providers import FakeProviderAdapter, ProviderBoundary, ProviderOutputArtifact, ProviderResultStatus, ProviderRequest, SQLiteProviderOutputStore
from arda.core import PolicyEngine, PolicyLevel
from arda.route_decision import CoreRoutePolicy
from tests.test_episodes import intake
from tests.test_route_decision import proposal


def test_realistic_episode_chain_generates_evaluation_without_manual_insertion(tmp_path):
    database = Database(tmp_path / "episode-evaluation.sqlite3")
    database.migrate()
    try:
        episode_store = EpisodeStore(database)
        episode_store.create(intake(), task_id="task-1", episode_id="episode-1", revision_id="revision-1")
        decision = CoreRoutePolicy(PolicyEngine({"provider.invoke": PolicyLevel.AUTO})).decide(
            proposal(), decision_id="decision-1"
        )
        request = ProviderRequest(
            request_id="provider-request-1", provider_id=decision.provider_id, episode_id="episode-1",
            capability_id="coding.review", task_contract_hash="contract-hash",
            route_proposal_id=decision.proposal_id, route_decision_id=decision.decision_id,
            route_decision_hash=decision.canonical_hash, policy_snapshot_hash=decision.policy_snapshot_hash,
            input_manifest=({"ref": "context", "sha256": "context-hash"},),
        )
        result = ProviderBoundary().invoke(decision, request, FakeProviderAdapter("local-a", output='{"status":"OK"}'))
        output_store = SQLiteProviderOutputStore(database)
        artifact = output_store.materialize(request, result, b'{"status":"OK"}', model_snapshot="qwen3:1.7b@digest")
        verification_request = ProviderVerificationRequest(
            episode_id="episode-1", provider_result_id=request.request_id, artifact_id=artifact.artifact_id,
            verification_type="json_status", verifier_identity="fixture-verifier",
            policy_snapshot_hash=decision.policy_snapshot_hash,
        )
        verification = ProviderVerificationBridge(output_store).verify(
            verification_request, result,
            lambda _: (ProviderVerificationStatus.PASS, (f"artifact:{artifact.artifact_id}",), "fixture-v0.1"),
        )
        outcome = VerifiedOutcome.from_verification(
            verification, requirement_status="SATISFIED", behavioral_status="PASS", alignment_status="ALIGNED", confidence=0.9,
        )
        EpisodeOutcomeRecorder(episode_store).record(outcome)
        completion = EpisodeCompletionPolicy().decide(outcome, human_approved=True)
        EpisodeCompletionRecorder(episode_store).record(completion)
        evaluation = EpisodeEvaluationIntegration(SQLiteEvaluationStore(database)).record(
            evaluation_id="evaluation-1", request=request, result=result, artifact=artifact,
            outcome=outcome, completion=completion,
        )
        aggregate = EvaluationMemory(SQLiteEvaluationStore(database).list()).aggregate("local-a", "coding.review")
        assert evaluation.outcome_status == "VERIFIED"
        assert evaluation.completion_status == "DONE"
        assert aggregate.sample_count == 1
        assert aggregate.verified_count == 1
        assert aggregate.completed_count == 1
    finally:
        database.close()
