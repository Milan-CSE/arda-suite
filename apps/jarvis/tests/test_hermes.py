import pytest

from arda.hermes import (
    HermesCoordinator,
    HermesError,
    HermesRunStatus,
    StepStatus,
    WorkflowDefinition,
    WorkflowStep,
    SQLiteHermesRunStore,
)
from arda.core import Database


def workflow():
    return WorkflowDefinition(
        workflow_id="review-workflow",
        version="0.1",
        steps=(
            WorkflowStep("route", "route_proposal", "coding.review"),
            WorkflowStep("verify", "verification", "verification.audit"),
        ),
    )


def test_hermes_coordinates_existing_episode_without_creating_authority():
    coordinator = HermesCoordinator(episode_exists=lambda episode_id: episode_id == "episode-1")
    run = coordinator.create_run(hermes_run_id="hermes-1", episode_id="episode-1", workflow=workflow())
    run = coordinator.start(run)
    run = coordinator.update_step(run, "route", StepStatus.COMPLETED, evidence_refs=("proposal:1",))
    assert run.status is HermesRunStatus.RUNNING
    run = coordinator.update_step(run, "verify", StepStatus.COMPLETED, evidence_refs=("verification:1",))
    assert run.status is HermesRunStatus.COMPLETED
    assert run.episode_id == "episode-1"


def test_hermes_cannot_create_unknown_episode_or_bypass_route_proposal():
    coordinator = HermesCoordinator(episode_exists=lambda _: False)
    with pytest.raises(HermesError, match="cannot create"):
        coordinator.create_run(hermes_run_id="hermes-1", episode_id="missing", workflow=workflow())


def test_hermes_only_hands_off_route_proposals_and_rejects_wrong_episode():
    coordinator = HermesCoordinator(episode_exists=lambda _: True)
    run = coordinator.start(coordinator.create_run(hermes_run_id="hermes-1", episode_id="episode-1", workflow=workflow()))

    class Proposal:
        episode_id = "other-episode"

    with pytest.raises(HermesError, match="episode mismatch"):
        coordinator.request_route_proposal(run, {"episode_id": "episode-1"}, lambda _: Proposal())


def test_hermes_does_not_treat_waiting_or_failure_as_completion():
    coordinator = HermesCoordinator(episode_exists=lambda _: True)
    run = coordinator.start(coordinator.create_run(hermes_run_id="hermes-1", episode_id="episode-1", workflow=workflow()))
    waiting = coordinator.update_step(run, "route", StepStatus.WAITING)
    assert waiting.status is HermesRunStatus.WAITING
    failed = coordinator.update_step(waiting, "route", StepStatus.FAILED, error_class="provider_timeout")
    assert failed.status is HermesRunStatus.FAILED


def test_hermes_run_events_survive_database_reopen(tmp_path):
    path = tmp_path / "hermes.sqlite3"
    database = Database(path)
    database.migrate()
    store = SQLiteHermesRunStore(database)
    coordinator = HermesCoordinator(episode_exists=lambda _: True, store=store)
    run = coordinator.start(coordinator.create_run(hermes_run_id="hermes-persisted", episode_id="episode-1", workflow=workflow()))
    run = coordinator.update_step(run, "route", StepStatus.COMPLETED, evidence_refs=("proposal:1",))
    database.close()

    reopened = Database(path)
    reopened.migrate()
    loaded = SQLiteHermesRunStore(reopened).load("hermes-persisted")
    assert loaded.status is HermesRunStatus.RUNNING
    assert loaded.steps[0].status is StepStatus.COMPLETED
    assert len(SQLiteHermesRunStore(reopened).events("hermes-persisted")) == 3
    reopened.close()
