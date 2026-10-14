import pytest

from arda.core import Database
from arda.episodes import EpisodeStore
from arda.mcp_provider_service import CoreProviderEvaluationError, CoreProviderEvaluationService
from arda.task_intake import TaskIntakeRequest


def _episode(db):
    return EpisodeStore(db).create(
        TaskIntakeRequest(
            intake_id="mcp-provider-service-intake",
            human_goal="Run controlled provider evaluation",
            task_type="diagnostic",
            objective="Evaluate one fixed provider fixture",
            risk="low",
            privacy="private",
            source_ref="test:mcp-provider-service",
            acceptance_criteria=("record is verified",),
        ),
        task_id="mcp-provider-service-task",
        episode_id="mcp-provider-service-episode",
        revision_id="mcp-provider-service-revision",
    )


def test_provider_evaluation_service_rejects_unknown_fixture(tmp_path):
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    episode = _episode(db)
    service = CoreProviderEvaluationService(db)
    with pytest.raises(CoreProviderEvaluationError, match="unsupported provider evaluation fixture"):
        service.evaluate({
            "episode_id": episode.episode_id,
            "task_contract_hash": episode.task_contract_hash,
            "evaluation_id": "eval-1",
            "fixture_id": "arbitrary-provider-call",
        })
    db.close()


def test_provider_evaluation_service_rejects_contract_hash_mismatch(tmp_path):
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    episode = _episode(db)
    service = CoreProviderEvaluationService(db)
    with pytest.raises(CoreProviderEvaluationError, match="task contract hash"):
        service.evaluate({
            "episode_id": episode.episode_id,
            "task_contract_hash": "wrong",
            "evaluation_id": "eval-2",
            "fixture_id": "hermes_core_provider_v0.1",
        })
    db.close()
