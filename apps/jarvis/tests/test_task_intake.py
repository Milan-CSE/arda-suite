"""Failure-first tests for the typed HumanGoal -> TaskContract boundary."""
from __future__ import annotations

import pytest

from arda.task_intake import TaskIntakeError, TaskIntakeRequest


def request(**overrides):
    payload = {
        "intake_id": "intake-1",
        "human_goal": "Fix the parser bug",
        "task_type": "coding",
        "objective": "Correct parser handling for empty input",
        "risk": "medium",
        "privacy": "private",
        "source_ref": "ui:request-1",
    }
    payload.update(overrides)
    return TaskIntakeRequest(**payload)


def test_valid_request_builds_hash_sealed_contract():
    intake = request()
    contract = intake.to_contract(task_id="task-1", episode_id="episode-1")
    assert contract.task_type == "coding"
    assert contract.risk == "medium"
    assert contract.privacy == "private"
    assert contract.source_ref == "ui:request-1"
    assert contract.hash()
    assert intake.payload_hash == intake.compute_payload_hash()


@pytest.mark.parametrize("field", ["human_goal", "objective", "source_ref"])
def test_required_identity_and_objective_fields_reject_empty(field):
    with pytest.raises(TaskIntakeError):
        request(**{field: ""}).validate()


@pytest.mark.parametrize("field,value", [
    ("task_type", "deploy"),
    ("risk", "urgent"),
    ("privacy", "classified"),
    ("created_by", "MODEL"),
])
def test_unknown_vocabulary_is_rejected(field, value):
    with pytest.raises(TaskIntakeError):
        request(**{field: value}).validate()


def test_candidate_requirements_are_not_promoted_to_explicit():
    intake = request(candidate_requirements=("maybe add caching",))
    contract = intake.to_contract(task_id="task-1", episode_id="episode-1")
    assert contract.explicit_requirements == ()
    assert contract.unresolved_uncertainties == ("maybe add caching",)


def test_contract_hash_changes_when_risk_or_scope_changes():
    base = request().to_contract(task_id="task-1", episode_id="episode-1")
    changed = request(risk="high", allowed_scope=("arda/parser.py",)).to_contract(task_id="task-1", episode_id="episode-1")
    assert base.hash() != changed.hash()


def test_task_and_episode_identity_are_required():
    with pytest.raises(TaskIntakeError):
        request().to_contract(task_id="", episode_id="episode-1")
    with pytest.raises(TaskIntakeError):
        request().to_contract(task_id="task-1", episode_id="")
