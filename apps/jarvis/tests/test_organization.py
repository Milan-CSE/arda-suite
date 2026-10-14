from __future__ import annotations

import json

from arda.awareness import ProjectRegistry, TaskStore
from arda.core import bootstrap_runtime_database
from arda.organization import OrganizationRegistry


def test_profile_objective_and_deterministic_ready_task_selection(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        tasks = TaskStore(db)
        low = tasks.create("sample-project", "Low impact", status="ready", priority="normal", task_id="TASK-LOW")
        high = tasks.create("sample-project", "High impact", status="ready", priority="high", task_id="TASK-HIGH")
        registry = OrganizationRegistry(db)
        registry.upsert_profile(
            "sample-project", mission="Make opportunities easier to find",
            accepted_baseline="a" * 40,
            policy={"push": "approval", "secrets": "deny"},
            budget={"hard_timeout_seconds": 900},
        )
        objective = registry.create_objective("sample-project", "Source reliability", status="active", weight=20,
                                              objective_id="OBJ-001")
        registry.set_task_meta(low["id"], user_impact=10, risk="low")
        registry.set_task_meta(high["id"], objective_id=objective["id"], user_impact=40, risk="medium")
        selected = registry.select_next_task("sample-project")
        assert selected["id"] == "TASK-HIGH"
        assert selected["selection_score"] == 30 + 20 + 40 - 5
        snapshot = registry.snapshot("sample-project")
        assert snapshot["profile"]["policy"]["secrets"] == "deny"
        assert snapshot["next_task"]["id"] == "TASK-HIGH"
    finally:
        db.close()


def test_paused_organization_has_no_next_task(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("example-project", "MODÜ-GRID", status="paused")
        TaskStore(db).create("example-project", "Ready task", status="ready", task_id="TASK-PAUSED")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("example-project", mission="paused")
        assert registry.select_next_task("example-project") is None
    finally:
        db.close()


def test_budget_exhausted_organization_has_no_next_task(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        TaskStore(db).create("sample-project", "Ready task", status="ready", task_id="TASK-BUDGET")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project", budget={"max_active_jobs": 1})
        from arda.core import JobQueue

        job = JobQueue(db).enqueue(
            "autonomous-coding", "Already running", idempotency_key="budget-active",
            project_id="sample-project",
        )
        JobQueue(db).claim("budget-worker")
        assert registry.select_next_task("sample-project") is None
        assert job.status == "queued"
    finally:
        db.close()


def test_idle_organization_has_no_ready_task(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        TaskStore(db).create("sample-project", "Backlog task", status="backlog", task_id="TASK-IDLE")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project")
        assert registry.select_next_task("sample-project") is None
    finally:
        db.close()


def test_cross_project_objective_is_rejected_for_task_metadata(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        ProjectRegistry(db).create("other", "Other")
        task = TaskStore(db).create("sample-project", "Task", task_id="TASK-CROSS-PROJECT")
        objective = OrganizationRegistry(db).create_objective(
            "other", "Other objective", objective_id="OBJ-OTHER"
        )
        registry = OrganizationRegistry(db)
        try:
            registry.set_task_meta(task["id"], objective_id=objective["id"])
        except ValueError as exc:
            assert "project" in str(exc).lower()
        else:
            raise AssertionError("cross-project objective assignment must be rejected")
    finally:
        db.close()


def test_objective_lifecycle_auto_activation_and_completion(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project", accepted_baseline="a" * 40)
        first = registry.create_objective("sample-project", "Source reliability", status="planned", weight=100,
                                          activation_policy="auto", objective_id="OBJ-FIRST")
        task = TaskStore(db).create("sample-project", "Ready work", status="ready", task_id="TASK-FIRST")
        registry.set_task_meta(task["id"], objective_id=first["id"], contract={"task_id": task["id"]})
        transitions = registry.reconcile_objective_lifecycle("sample-project")
        assert transitions[0]["to"] == "active"
        assert registry.profile("sample-project")["autopilot_state"] == "OFF"
        assert db.execute("SELECT status FROM organization_objectives WHERE id='OBJ-FIRST'").fetchone()[0] == "active"
    finally:
        db.close()


def test_objective_lifecycle_skips_tasks_with_disallowed_risk(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project", policy={"allowed_risks": ["low"]})
        for suffix, weight, risk in (("HIGH", 100, "high"), ("LOW", 10, "low")):
            objective = registry.create_objective(
                "sample-project", f"{risk} risk objective", status="planned", weight=weight,
                activation_policy="auto", objective_id=f"OBJ-{suffix}",
            )
            task = TaskStore(db).create(
                "sample-project", f"{risk} risk work", status="ready", task_id=f"TASK-{suffix}",
            )
            registry.set_task_meta(
                task["id"], objective_id=objective["id"], risk=risk,
                contract={"task_id": task["id"]},
            )

        transitions = registry.reconcile_objective_lifecycle("sample-project")

        assert transitions == [{
            "objective_id": "OBJ-LOW", "from": "planned", "to": "active",
            "reason": "AUTO_POLICY_ELIGIBLE",
        }]
        statuses = dict(db.execute("SELECT id, status FROM organization_objectives").fetchall())
        assert statuses == {"OBJ-HIGH": "planned", "OBJ-LOW": "active"}
        assert registry.select_next_task("sample-project", require_contract=True)["id"] == "TASK-LOW"
    finally:
        db.close()


def test_objective_lifecycle_advances_dependencies_once_and_respects_activation_policy(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project")
        tasks = TaskStore(db)
        configurations = (
            ("FIRST", "auto", 10, ()),
            ("SECOND", "auto", 20, ("OBJ-FIRST",)),
            ("APPROVAL", "approval_required", 100, ()),
            ("MANUAL", "manual_only", 100, ()),
        )
        for suffix, policy, weight, dependencies in configurations:
            objective = registry.create_objective(
                "sample-project", suffix, status="planned", weight=weight,
                activation_policy=policy, depends_on=dependencies, objective_id=f"OBJ-{suffix}",
            )
            task = tasks.create("sample-project", suffix, status="ready", task_id=f"TASK-{suffix}")
            registry.set_task_meta(
                task["id"], objective_id=objective["id"], contract={"task_id": task["id"]},
            )

        first_activation = {
            "objective_id": "OBJ-FIRST", "from": "planned", "to": "active",
            "reason": "AUTO_POLICY_ELIGIBLE",
        }
        assert registry.reconcile_objective_lifecycle("sample-project") == [first_activation]
        assert registry.reconcile_objective_lifecycle("sample-project") == []
        assert registry.select_next_task("sample-project", require_contract=True)["id"] == "TASK-FIRST"

        first_task = tasks.get("TASK-FIRST")
        tasks.update_status(first_task["id"], "done", expected_version=first_task["version"])
        first_completion = {
            "objective_id": "OBJ-FIRST", "from": "active", "to": "done",
            "reason": "ALL_TASKS_DONE",
        }
        second_activation = {
            "objective_id": "OBJ-SECOND", "from": "planned", "to": "active",
            "reason": "AUTO_POLICY_ELIGIBLE",
        }
        assert registry.reconcile_objective_lifecycle("sample-project") == [first_completion, second_activation]
        assert registry.reconcile_objective_lifecycle("sample-project") == []
        assert registry.select_next_task("sample-project", require_contract=True)["id"] == "TASK-SECOND"

        second_task = tasks.get("TASK-SECOND")
        tasks.update_status(second_task["id"], "done", expected_version=second_task["version"])
        second_completion = {
            "objective_id": "OBJ-SECOND", "from": "active", "to": "done",
            "reason": "ALL_TASKS_DONE",
        }
        assert registry.reconcile_objective_lifecycle("sample-project") == [second_completion]
        assert registry.reconcile_objective_lifecycle("sample-project") == []
        assert dict(db.execute("SELECT id, status FROM organization_objectives").fetchall()) == {
            "OBJ-FIRST": "done", "OBJ-SECOND": "done",
            "OBJ-APPROVAL": "planned", "OBJ-MANUAL": "planned",
        }
        audit_rows = db.execute(
            "SELECT source_id, payload FROM events "
            "WHERE project_id=? AND type='organization.objective_transitioned' ORDER BY rowid",
            ("sample-project",),
        ).fetchall()
        expected_transitions = [first_activation, first_completion, second_activation, second_completion]
        assert [json.loads(row["payload"]) for row in audit_rows] == expected_transitions
        assert [row["source_id"] for row in audit_rows] == [
            transition["objective_id"] for transition in expected_transitions
        ]
    finally:
        db.close()


def test_invalid_profile_policy_and_budget_are_rejected(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        invalid_profiles = (
            {"policy": {"allowed_risks": "medium"}},
            {"budget": {"max_active_jobs": 0}},
            {"budget": {"hard_timeout_seconds": 0}},
        )
        for payload in invalid_profiles:
            try:
                registry.upsert_profile("sample-project", **payload)
            except ValueError:
                continue
            raise AssertionError(f"invalid organization profile accepted: {payload}")
    finally:
        db.close()


def test_enqueue_gate_applies_risk_and_active_job_budget(tmp_path):
    db = bootstrap_runtime_database(tmp_path / "core.sqlite3")
    try:
        ProjectRegistry(db).create("sample-project", "Sample Project")
        registry = OrganizationRegistry(db)
        registry.upsert_profile("sample-project", policy={"allowed_risks": ["low"]}, budget={"max_active_jobs": 1})
        try:
            registry.assert_enqueue_allowed("sample-project", risk="high", priority="normal")
        except ValueError as exc:
            assert "risk" in str(exc)
        else:
            raise AssertionError("disallowed risk was accepted")
        from arda.core import JobQueue
        JobQueue(db).enqueue("autonomous-coding", "active", idempotency_key="active", project_id="sample-project")
        try:
            registry.assert_enqueue_allowed("sample-project", risk="low", priority="normal")
        except ValueError as exc:
            assert "budget" in str(exc)
        else:
            raise AssertionError("active-job budget was bypassed")
    finally:
        db.close()
