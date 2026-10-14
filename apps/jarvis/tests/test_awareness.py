"""M1 Awareness Layer: canonical project and task state tests."""

import sqlite3

import pytest

from arda.awareness import AwarenessReader, OptimisticLockError, ProjectRegistry, TaskStore
from arda.core import Database
from arda.scheduler import Scheduler


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def project(db):
    return ProjectRegistry(db).create(
        "sample-project", "Opvia", brain_path="brain/projects/sample-project"
    )


def test_project_registry_creates_and_updates_with_audit_events(db):
    registry = ProjectRegistry(db)
    created = registry.create("sample-project", "Opvia", brain_path="brain/projects/sample-project")

    assert created["id"] == "sample-project"
    assert created["version"] == 1
    assert registry.get("sample-project")["name"] == "Opvia"

    updated = registry.update_status("sample-project", "paused", expected_version=1)
    assert updated["status"] == "paused"
    assert updated["version"] == 2

    events = db.execute(
        "SELECT type, source, project_id FROM events ORDER BY created_at"
    ).fetchall()
    assert [(row["type"], row["source"], row["project_id"]) for row in events] == [
        ("project.created", "core", "sample-project"),
        ("project.status_changed", "core", "sample-project"),
    ]


def test_project_registry_rejects_duplicate_and_stale_updates(db):
    registry = ProjectRegistry(db)
    registry.create("sample-project", "Opvia")

    with pytest.raises(sqlite3.IntegrityError):
        registry.create("sample-project", "Duplicate")

    with pytest.raises(OptimisticLockError):
        registry.update_status("sample-project", "blocked", expected_version=0)


def test_task_store_validates_vocabulary_and_critical_priority(db, project):
    tasks = TaskStore(db)

    with pytest.raises(ValueError, match="status"):
        tasks.create("sample-project", "Bad status", status="running")
    with pytest.raises(ValueError, match="human approval"):
        tasks.create("sample-project", "Critical task", priority="critical")

    task = tasks.create(
        "sample-project", "Review SMTP", priority="critical", human_approved=True
    )
    assert task["status"] == "backlog"
    assert task["priority"] == "critical"


def test_task_store_updates_status_with_optimistic_lock_and_completion(db, project):
    tasks = TaskStore(db)
    task = tasks.create("sample-project", "Implement metrics")

    ready = tasks.update_status(task["id"], "ready", expected_version=1)
    assert ready["status"] == "ready"
    assert ready["version"] == 2

    done = tasks.update_status(task["id"], "done", expected_version=2)
    assert done["completed_at"] is not None
    assert done["version"] == 3

    with pytest.raises(OptimisticLockError):
        tasks.update_status(task["id"], "cancelled", expected_version=2)

    event_types = [
        row["type"]
        for row in db.execute(
            "SELECT type FROM events WHERE project_id=? ORDER BY created_at", ("sample-project",)
        )
    ]
    assert event_types == ["project.created", "task.created", "task.status_changed", "task.status_changed"]


def test_awareness_reader_returns_project_metrics_and_tasks_without_writing(db, project):
    tasks = TaskStore(db)
    tasks.create("sample-project", "Blocked SMTP", status="blocked", priority="high")
    tasks.create("sample-project", "Decision", status="awaiting_decision")

    scheduler = Scheduler(db)
    scheduler.install_defaults(run_immediately=True)
    scheduler.run_due()
    before_events = db.scalar("SELECT COUNT(*) FROM events")

    reader = AwarenessReader(db)
    snapshot = reader.get_project_snapshot("sample-project")

    assert snapshot["project"]["name"] == "Opvia"
    assert snapshot["metrics"]["open_task_count"] == 2
    assert snapshot["metrics"]["blocked_task_count"] == 1
    assert snapshot["metrics"]["awaiting_decision_count"] == 1
    assert [task["title"] for task in snapshot["tasks"]] == ["Blocked SMTP", "Decision"]
    assert db.scalar("SELECT COUNT(*) FROM events") == before_events


def test_awareness_reader_distinguishes_missing_projection_from_missing_project(db, project):
    reader = AwarenessReader(db)

    snapshot = reader.get_project_snapshot("sample-project")
    assert snapshot["metrics"] is None
    assert snapshot["tasks"] == []

    with pytest.raises(KeyError):
        reader.get_project_snapshot("missing")
