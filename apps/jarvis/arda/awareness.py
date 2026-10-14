"""M1 canonical project and task state services.

This module owns local state only. It does not call GitHub, an agent, a worker,
or a notification provider.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Any

from arda.core import Database, EventLog, _iso, _utc_now


PROJECT_STATUSES = frozenset({"active", "paused", "blocked", "completed", "archived"})
TASK_STATUSES = frozenset({
    "backlog", "ready", "in_progress", "blocked", "awaiting_decision", "done", "cancelled"
})
TASK_PRIORITIES = frozenset({"low", "normal", "high", "critical"})


class OptimisticLockError(RuntimeError):
    """A write used a stale version of a project or task."""


def _validate(value: str, allowed: frozenset[str], field: str) -> None:
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"invalid {field} {value!r}; expected one of: {choices}")


class ProjectRegistry:
    """CRUD boundary for canonical local project records."""

    def __init__(self, db: Database, *, clock=_utc_now):
        self.db = db
        self.clock = clock
        self.events = EventLog(db, clock)

    def create(
        self,
        project_id: str,
        name: str,
        *,
        brain_path: str | None = None,
        status: str = "active",
        now: datetime | None = None,
    ) -> sqlite3.Row:
        _validate(status, PROJECT_STATUSES, "project status")
        timestamp = _iso(now or self.clock())
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO projects(
                    id, name, status, brain_path, created_at, updated_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (project_id, name, status, brain_path, timestamp, timestamp),
            )
            self.events.append(
                "project.created",
                project_id=project_id,
                source="core",
                source_id=project_id,
                payload={"name": name, "status": status, "brain_path": brain_path},
            )
        return self.get(project_id)

    def get(self, project_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        return row

    def list(self, *, status: str | None = None) -> list[sqlite3.Row]:
        if status is not None:
            _validate(status, PROJECT_STATUSES, "project status")
            return self.db.execute(
                "SELECT * FROM projects WHERE status=? ORDER BY name, id", (status,)
            ).fetchall()
        return self.db.execute("SELECT * FROM projects ORDER BY name, id").fetchall()

    def update_status(
        self,
        project_id: str,
        status: str,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> sqlite3.Row:
        _validate(status, PROJECT_STATUSES, "project status")
        timestamp = _iso(now or self.clock())
        with self.db.transaction():
            updated = self.db.execute(
                """UPDATE projects SET status=?, updated_at=?, version=version+1
                   WHERE id=? AND version=?""",
                (status, timestamp, project_id, expected_version),
            ).rowcount
            if updated != 1:
                self._raise_stale(project_id, expected_version)
            self.events.append(
                "project.status_changed",
                project_id=project_id,
                source="core",
                source_id=project_id,
                payload={"status": status, "expected_version": expected_version},
            )
        return self.get(project_id)

    def _raise_stale(self, project_id: str, expected_version: int) -> None:
        if self.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise KeyError(project_id)
        raise OptimisticLockError(
            f"project {project_id!r} version is stale; expected {expected_version}"
        )


class TaskStore:
    """CRUD boundary for canonical local task records."""

    def __init__(self, db: Database, *, clock=_utc_now):
        self.db = db
        self.clock = clock
        self.events = EventLog(db, clock)

    def create(
        self,
        project_id: str,
        title: str,
        *,
        description: str = "",
        status: str = "backlog",
        priority: str = "normal",
        source: str | None = None,
        deadline: str | None = None,
        blocked_by: str | None = None,
        human_approved: bool = False,
        task_id: str | None = None,
        now: datetime | None = None,
    ) -> sqlite3.Row:
        _validate(status, TASK_STATUSES, "task status")
        _validate(priority, TASK_PRIORITIES, "task priority")
        if priority == "critical" and not human_approved:
            raise ValueError("critical priority requires explicit human approval")
        timestamp = _iso(now or self.clock())
        task_id = task_id or f"task_{uuid.uuid4().hex}"
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO tasks(
                    id, project_id, title, description, status, priority, source,
                    deadline, blocked_by, created_at, updated_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (task_id, project_id, title, description, status, priority, source,
                 deadline, blocked_by, timestamp, timestamp),
            )
            self.events.append(
                "task.created",
                project_id=project_id,
                source="core",
                source_id=task_id,
                payload={
                    "title": title,
                    "status": status,
                    "priority": priority,
                    "deadline": deadline,
                },
            )
        return self.get(task_id)

    def get(self, task_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return row

    def list(self, *, project_id: str | None = None) -> list[sqlite3.Row]:
        if project_id is not None:
            return self.db.execute(
                "SELECT * FROM tasks WHERE project_id=? ORDER BY created_at, id", (project_id,)
            ).fetchall()
        return self.db.execute("SELECT * FROM tasks ORDER BY created_at, id").fetchall()

    def update_status(
        self,
        task_id: str,
        status: str,
        *,
        expected_version: int,
        now: datetime | None = None,
    ) -> sqlite3.Row:
        _validate(status, TASK_STATUSES, "task status")
        timestamp = _iso(now or self.clock())
        completed_at = timestamp if status == "done" else None
        with self.db.transaction():
            updated = self.db.execute(
                """UPDATE tasks SET status=?, updated_at=?, completed_at=?, version=version+1
                   WHERE id=? AND version=?""",
                (status, timestamp, completed_at, task_id, expected_version),
            ).rowcount
            if updated != 1:
                self._raise_stale(task_id, expected_version)
            row = self.get(task_id)
            self.events.append(
                "task.status_changed",
                project_id=row["project_id"],
                source="core",
                source_id=task_id,
                payload={"status": status, "expected_version": expected_version},
            )
        return self.get(task_id)

    def _raise_stale(self, task_id: str, expected_version: int) -> None:
        if self.db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is None:
            raise KeyError(task_id)
        raise OptimisticLockError(
            f"task {task_id!r} version is stale; expected {expected_version}"
        )


class AwarenessReader:
    """Read-only access to project state, metrics projections and local tasks."""

    def __init__(self, db: Database):
        self.db = db

    def get_project_snapshot(self, project_id: str) -> dict[str, Any]:
        project = self.db.execute(
            "SELECT * FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if project is None:
            raise KeyError(project_id)
        metrics = self.db.execute(
            "SELECT * FROM project_metrics WHERE project_id=?", (project_id,)
        ).fetchone()
        tasks = self.db.execute(
            """SELECT * FROM tasks WHERE project_id=?
               ORDER BY CASE status
                   WHEN 'blocked' THEN 0
                   WHEN 'awaiting_decision' THEN 1
                   WHEN 'in_progress' THEN 2
                   WHEN 'ready' THEN 3
                   WHEN 'backlog' THEN 4
                   ELSE 5 END,
                   CASE priority
                       WHEN 'critical' THEN 0
                       WHEN 'high' THEN 1
                       WHEN 'normal' THEN 2
                       ELSE 3 END,
                   created_at, id""",
            (project_id,),
        ).fetchall()
        return {
            "project": dict(project),
            "metrics": dict(metrics) if metrics is not None else None,
            "tasks": [dict(task) for task in tasks],
        }

    def list_project_snapshots(
        self, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        if status is None:
            projects = self.db.execute(
                "SELECT id FROM projects ORDER BY name, id"
            ).fetchall()
        else:
            _validate(status, PROJECT_STATUSES, "project status")
            projects = self.db.execute(
                "SELECT id FROM projects WHERE status=? ORDER BY name, id", (status,)
            ).fetchall()
        return [self.get_project_snapshot(row["id"]) for row in projects]
