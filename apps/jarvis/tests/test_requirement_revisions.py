"""Failure-first M2.4D3.5 requirement reconstruction/versioning tests."""
from __future__ import annotations

import pytest

from arda.requirement_revisions import RequirementRevisionChain, RequirementRevisionError
from arda.requirements import Requirement, RequirementSource, TaskContract


def contract(requirements=()):
    return TaskContract("task-1", "episode-1", "objective", explicit_requirements=tuple(requirements))


def test_initial_revision_is_immutable_and_provenanced():
    chain = RequirementRevisionChain.start(revision_id="rev-1", task_contract=contract((Requirement("r1", "exists", "file_exists", target="module.py"),)), provenance={"source": "user"})
    assert chain.head.revision_number == 1
    assert chain.head.parent_revision_id is None
    assert chain.head.added_requirement_ids == ("r1",)
    assert chain.head.provenance["source"] == "user"
    assert chain.head.contract_hash == chain.head.task_contract.hash()


def test_revision_appends_and_records_added_changed_removed():
    first = RequirementRevisionChain.start(revision_id="rev-1", task_contract=contract((Requirement("r1", "exists", "file_exists", target="module.py"),)), provenance={"source": "user"})
    second_contract = contract((
        Requirement("r1", "contains answer", "file_contains", target="module.py", expected="return 2"),
        Requirement("r2", "no regression", "no_regression"),
    ))
    second = first.append(revision_id="rev-2", parent_revision_id="rev-1", task_contract=second_contract, reason="user_clarification", provenance={"source": "user", "reference": "clarification-1"})
    assert len(second.history) == 2
    assert second.head.revision_number == 2
    assert second.head.added_requirement_ids == ("r2",)
    assert second.head.changed_requirement_ids == ("r1",)
    assert second.head.removed_requirement_ids == ()
    assert first.head.task_contract.requirements()[0].kind == "file_exists"


def test_wrong_parent_or_duplicate_revision_cannot_overwrite_chain():
    chain = RequirementRevisionChain.start(revision_id="rev-1", task_contract=contract(), provenance={})
    with pytest.raises(RequirementRevisionError, match="current chain head"):
        chain.append(revision_id="rev-2", parent_revision_id="missing", task_contract=contract(), reason="reason", provenance={})
    second = chain.append(revision_id="rev-2", parent_revision_id="rev-1", task_contract=contract(), reason="reason", provenance={})
    with pytest.raises(RequirementRevisionError, match="already exists"):
        second.append(revision_id="rev-2", parent_revision_id="rev-2", task_contract=contract(), reason="reason", provenance={})


def test_task_and_episode_identity_cannot_change():
    chain = RequirementRevisionChain.start(revision_id="rev-1", task_contract=contract(), provenance={})
    changed = TaskContract("task-2", "episode-1", "objective")
    with pytest.raises(RequirementRevisionError, match="identity cannot change"):
        chain.append(revision_id="rev-2", parent_revision_id="rev-1", task_contract=changed, reason="bad", provenance={})


def test_candidate_unresolved_and_rejected_are_not_silent_passes(tmp_path):
    from arda.requirements import RequirementAuditor, RequirementStatus
    from tests.test_requirements import make_case

    _, patched, patch, structural, behavioral = make_case(tmp_path)
    task = contract((
        Requirement("candidate", "maybe preserve API", "free_text", RequirementSource.CANDIDATE),
        Requirement("unresolved", "unknown", "free_text", RequirementSource.UNRESOLVED),
        Requirement("rejected", "contradicted", "free_text", RequirementSource.REJECTED),
    ))
    audit = RequirementAuditor().audit(task_contract=task, patch_result=patch, structural_evaluation=structural, behavioral_evaluation=behavioral, patched_workspace=patched)
    assert audit.status is RequirementStatus.VIOLATED
    statuses = {item.requirement_id: item.status for item in audit.evaluations}
    assert statuses["candidate"] is RequirementStatus.UNVERIFIED
    assert statuses["unresolved"] is RequirementStatus.UNVERIFIED
    assert statuses["rejected"] is RequirementStatus.VIOLATED
