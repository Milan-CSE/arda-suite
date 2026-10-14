import pytest

from arda.completion import (
    CompletionStatus,
    EpisodeCompletionPolicy,
    EpisodeCompletionRecorder,
)
from arda.core import Database
from arda.episodes import EpisodeStatus, EpisodeStore
from arda.outcomes import VerifiedOutcome
from tests.test_episodes import intake
from tests.test_outcomes import _verification


def _verified_outcome():
    return VerifiedOutcome.from_verification(
        _verification(),
        requirement_status="SATISFIED",
        behavioral_status="PASS",
        alignment_status="ALIGNED",
        confidence=0.9,
    )


def test_verified_outcome_does_not_complete_without_explicit_permission():
    decision = EpisodeCompletionPolicy().decide(_verified_outcome())
    assert decision.status is CompletionStatus.NEEDS_HUMAN
    assert decision.reason == "verified_outcome_requires_completion_approval"
    assert decision.verify_hash()


def test_policy_allows_explicit_automatic_or_human_completion():
    outcome = _verified_outcome()
    assert EpisodeCompletionPolicy().decide(outcome, automatic_completion_allowed=True).status is CompletionStatus.DONE
    assert EpisodeCompletionPolicy().decide(outcome, human_approved=True).status is CompletionStatus.DONE


def test_inconclusive_outcome_cannot_be_marked_done():
    outcome = VerifiedOutcome.from_verification(_verification(), requirement_status="UNVERIFIED")
    decision = EpisodeCompletionPolicy().decide(outcome, automatic_completion_allowed=True)
    assert decision.status is CompletionStatus.INCOMPLETE


def test_completion_decision_is_persisted_as_separate_event(tmp_path):
    database = Database(tmp_path / "completion.sqlite3")
    database.migrate()
    try:
        episodes = EpisodeStore(database)
        episodes.create(intake(), task_id="task-1", episode_id="episode-1", revision_id="revision-1")
        outcome = _verified_outcome()
        decision = EpisodeCompletionPolicy().decide(outcome, human_approved=True)
        event = EpisodeCompletionRecorder(episodes).record(decision)
        assert event.event_type == "episode.completion.decided"
        assert episodes.get("episode-1").status is EpisodeStatus.DONE
        assert episodes.events("episode-1")[-1].payload["decision_hash"] == decision.canonical_hash
    finally:
        database.close()
