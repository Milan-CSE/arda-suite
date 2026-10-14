"""Failure-first SQLite durability tests for M2.4D3.5."""
from __future__ import annotations

import json
import sqlite3

import pytest

from arda.core import Database
from arda.requirement_revisions import (
    ContractRevision,
    RequirementRevisionChain,
    RequirementRevisionError,
    SQLiteRequirementRevisionStore,
)
from arda.requirements import Requirement, TaskContract


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    try:
        yield database
    finally:
        database.close()


def contract(requirements=(), *, task_id="task-1", episode_id="episode-1"):
    return TaskContract(task_id, episode_id, "objective", explicit_requirements=tuple(requirements))


def revisions():
    first = RequirementRevisionChain.start(
        revision_id="rev-1",
        task_contract=contract((Requirement("r1", "exists", "file_exists", target="module.py"),)),
        provenance={"created_by": "USER", "source_ref": "task-1"},
    )
    second = first.append(
        revision_id="rev-2",
        parent_revision_id="rev-1",
        task_contract=contract((Requirement("r1", "contains answer", "file_contains", target="module.py", expected="return 2"), Requirement("r2", "no regression", "no_regression"))),
        reason="clarification",
        provenance={"created_by": "CODEX", "source_ref": "clarification-1"},
    )
    third = second.append(
        revision_id="rev-3",
        parent_revision_id="rev-2",
        task_contract=contract((Requirement("r1", "contains answer", "file_contains", target="module.py", expected="return 2"), Requirement("r2", "no regression", "no_regression"), Requirement("r3", "preserve API", "free_text"))),
        reason="repo_discovery",
        provenance={"created_by": "SYSTEM", "source_ref": "repo-doc-1"},
    )
    return first, second, third


def test_append_only_revision_chain_reopens_with_same_hashes(db, tmp_path):
    first, second, third = revisions()
    store = SQLiteRequirementRevisionStore(db)
    for revision in (first.head, second.head, third.head):
        store.append(revision)
    db.close()

    reopened = Database(tmp_path / "arda.sqlite3")
    reopened.migrate()
    loaded = SQLiteRequirementRevisionStore(reopened).load(task_id="task-1", episode_id="episode-1")
    assert [item.revision_id for item in loaded.history] == ["rev-1", "rev-2", "rev-3"]
    assert [item.hash() for item in loaded.history] == [first.head.hash(), second.head.hash(), third.head.hash()]
    assert loaded.head.task_contract.requirements()[-1].requirement_id == "r3"
    reopened.close()


def test_duplicate_and_out_of_order_parent_are_rejected(db):
    first, second, _ = revisions()
    store = SQLiteRequirementRevisionStore(db)
    store.append(first.head)
    with pytest.raises(RequirementRevisionError, match="revision_id already exists"):
        store.append(first.head)
    with pytest.raises(RequirementRevisionError, match="current durable chain head"):
        store.append(ContractRevision("rev-3", "missing", 3, second.head.task_contract, "bad", {}, (), (), ()))


def test_sqlite_triggers_keep_rows_append_only(db):
    first, _, _ = revisions()
    store = SQLiteRequirementRevisionStore(db)
    store.append(first.head)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE requirement_contract_revisions SET reason='tampered' WHERE revision_id='rev-1'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("DELETE FROM requirement_contract_revisions WHERE revision_id='rev-1'")


def test_tampered_payload_is_detected_on_reopen(db):
    first, _, _ = revisions()
    store = SQLiteRequirementRevisionStore(db)
    store.append(first.head)
    db.execute("DROP TRIGGER requirement_revisions_no_update")
    db.execute("UPDATE requirement_contract_revisions SET contract_json=? WHERE revision_id='rev-1'", (json.dumps({"tampered": True}),))
    with pytest.raises(RequirementRevisionError, match="corrupt|hash mismatch"):
        store.load(task_id="task-1", episode_id="episode-1")


def test_task_and_episode_continuity_is_enforced(db):
    first, _, _ = revisions()
    store = SQLiteRequirementRevisionStore(db)
    store.append(first.head)
    changed = ContractRevision("rev-other", "rev-1", 2, contract(task_id="task-2"), "bad", {}, (), (), ())
    with pytest.raises(RequirementRevisionError, match="identity cannot change"):
        store.append(changed)
