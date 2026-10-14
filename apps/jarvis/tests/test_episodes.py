"""Failure-first Episode identity and append-only lifecycle tests."""
from __future__ import annotations

import sqlite3

import pytest

from arda.core import Database
from arda.episodes import EpisodeError, EpisodeStatus, EpisodeStore
from arda.task_intake import TaskIntakeRequest


def intake(**overrides):
    payload = {
        "intake_id": "intake-episode-1",
        "human_goal": "Fix parser",
        "task_type": "coding",
        "objective": "Handle empty input",
        "risk": "medium",
        "privacy": "private",
        "source_ref": "ui:episode-1",
        "acceptance_criteria": ("empty input returns an error",),
    }
    payload.update(overrides)
    return TaskIntakeRequest(**payload)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "episode.sqlite3")
    database.migrate()
    try:
        yield database
    finally:
        database.close()


def test_episode_create_is_atomic_and_reopens_with_revision_and_event(db, tmp_path):
    episode = EpisodeStore(db).create(intake(), task_id="task-1", episode_id="episode-1", revision_id="revision-1")
    assert episode.status is EpisodeStatus.PLANNED
    assert db.scalar("SELECT COUNT(*) FROM requirement_contract_revisions") == 1
    assert [event.event_type for event in EpisodeStore(db).events("episode-1")] == ["episode.created"]
    db.close()

    reopened = Database(tmp_path / "episode.sqlite3")
    reopened.migrate()
    loaded = EpisodeStore(reopened).get("episode-1")
    assert loaded.task_contract_revision_id == "revision-1"
    assert loaded.task_contract_hash == episode.task_contract_hash
    reopened.close()


def test_same_intake_is_idempotent_but_conflicting_payload_is_rejected(db):
    store = EpisodeStore(db)
    first = store.create(intake(), task_id="task-1", episode_id="episode-1", revision_id="revision-1")
    same = store.create(intake(), task_id="task-other", episode_id="episode-other", revision_id="revision-other")
    assert same.episode_id == first.episode_id
    assert db.scalar("SELECT COUNT(*) FROM episodes") == 1
    with pytest.raises(EpisodeError, match="different payload"):
        store.create(intake(objective="different"), task_id="task-2", episode_id="episode-2", revision_id="revision-2")


def test_lifecycle_event_is_idempotent_and_append_only(db):
    store = EpisodeStore(db)
    store.create(intake(), task_id="task-1", episode_id="episode-1", revision_id="revision-1")
    event = store.append_event("episode-1", event_type="episode.blocked", status=EpisodeStatus.BLOCKED, payload={"reason": "clarification"}, idempotency_key="episode-1:blocked")
    assert event.status is EpisodeStatus.BLOCKED
    with pytest.raises(EpisodeError, match="append-only"):
        store.append_event("episode-1", event_type="episode.blocked", status=EpisodeStatus.BLOCKED, payload={}, idempotency_key="episode-1:blocked")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE episode_events SET payload_json=? WHERE event_id=?", ("{}", event.event_id))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM episode_events WHERE event_id=?", (event.event_id,))


def test_unknown_episode_event_is_rejected(db):
    with pytest.raises(EpisodeError, match="unknown episode"):
        EpisodeStore(db).append_event("missing", event_type="episode.running", status=EpisodeStatus.RUNNING, payload={}, idempotency_key="missing:running")
