import hashlib

import pytest

from arda.core import Database
from arda.episodes import EpisodeError, EpisodeStatus, EpisodeStore
from arda.outcomes import EpisodeOutcomeRecorder, OutcomeError, OutcomeStatus, VerifiedOutcome
from arda.provider_verification import (
    ProviderVerificationResult,
    ProviderVerificationStatus,
)
from tests.test_episodes import intake


def _verification():
    payload = {
        "episode_id": "episode-1",
        "provider_result_id": "provider-request-1",
        "artifact_id": "provider_art_1",
        "status": "PASS",
        "evidence_refs": ["sha256:abc"],
        "verifier_identity": "fixture-verifier",
        "verifier_version": "fixture-v0.1",
        "input_hash": "a" * 64,
        "output_hash": "a" * 64,
    }
    canonical_hash = hashlib.sha256(
        __import__("json").dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return ProviderVerificationResult(
        episode_id="episode-1",
        provider_result_id="provider-request-1",
        artifact_id="provider_art_1",
        status=ProviderVerificationStatus.PASS,
        evidence_refs=("sha256:abc",),
        verifier_identity="fixture-verifier",
        verifier_version="fixture-v0.1",
        input_hash="a" * 64,
        output_hash="a" * 64,
        canonical_hash=canonical_hash,
    )


def test_outcome_remains_inconclusive_until_all_required_layers_are_verified():
    outcome = VerifiedOutcome.from_verification(_verification(), requirement_status="SATISFIED")
    assert outcome.status is OutcomeStatus.INCONCLUSIVE
    assert outcome.verify_hash()


def test_outcome_records_event_without_closing_episode(tmp_path):
    database = Database(tmp_path / "outcome.sqlite3")
    database.migrate()
    try:
        episodes = EpisodeStore(database)
        episodes.create(intake(), task_id="task-1", episode_id="episode-1", revision_id="revision-1")
        outcome = VerifiedOutcome.from_verification(
            _verification(),
            requirement_status="SATISFIED",
            behavioral_status="PASS",
            alignment_status="ALIGNED",
            confidence=0.9,
        )
        event = EpisodeOutcomeRecorder(episodes).record(outcome)
        assert event.event_type == "episode.verification.completed"
        assert episodes.get("episode-1").status is EpisodeStatus.AWAITING_DECISION
        assert episodes.events("episode-1")[-1].payload["outcome_hash"] == outcome.canonical_hash
        with pytest.raises(EpisodeError, match="append-only"):
            EpisodeOutcomeRecorder(episodes).record(outcome)
        assert len(episodes.events("episode-1")) == 2
    finally:
        database.close()


def test_outcome_rejects_tampered_verification():
    verification = _verification()
    tampered = ProviderVerificationResult(**{**verification.__dict__, "evidence_refs": ("sha256:tampered",)})
    with pytest.raises(OutcomeError, match="verification result hash"):
        VerifiedOutcome.from_verification(tampered)
