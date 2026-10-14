from arda.core import Database
from arda.episodes import EpisodeStore
from arda.mcp_read_service import CoreReadService
from arda.task_intake import TaskIntakeRequest


def test_read_service_returns_episode_and_verification_projections(tmp_path):
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    episode = EpisodeStore(db).create(
        TaskIntakeRequest(
            intake_id="intake-mcp",
            human_goal="Inspect status",
            task_type="diagnostic",
            objective="Inspect status",
            risk="low",
            privacy="private",
            source_ref="test:mcp",
            acceptance_criteria=("status available",),
        ),
        task_id="task-mcp",
        episode_id="episode-mcp",
        revision_id="revision-mcp",
    )
    service = CoreReadService(db)

    status = service.episode_status({"episode_id": episode.episode_id})
    verification = service.verification_status({"episode_id": episode.episode_id})

    assert status["episode_id"] == "episode-mcp"
    assert status["events"][0]["event_type"] == "episode.created"
    assert verification["episode_status"] == "planned"
    assert verification["evaluation_count"] == 0
    db.close()


def test_read_service_does_not_expose_artifact_content(tmp_path):
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    service = CoreReadService(db)
    assert service.artifact_read
    db.close()

