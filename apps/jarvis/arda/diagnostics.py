"""Read-only M1.5 diagnostics for the Arda Core control plane."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from arda.core import runtime_database_path


CURRENT_SCHEMA_VERSION = 15
KNOWN_JOB_STATUSES = {"queued", "leased", "running", "retry_wait", "succeeded", "failed"}


class Diagnostics:
    """Query-only diagnostics; this class never migrates or reconciles state."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> "Diagnostics":
        if not self.path.exists():
            return self
        uri = f"file:{self.path.resolve()}?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True, timeout=2)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only=ON")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn is not None:
            self.conn.close()

    @property
    def available(self) -> bool:
        return self.conn is not None

    def _scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        if self.conn is None:
            raise sqlite3.OperationalError("database unavailable")
        return self.conn.execute(sql, params).fetchone()[0]

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        if self.conn is None:
            raise sqlite3.OperationalError("database unavailable")
        return self.conn.execute(sql, params).fetchall()

    def _has_table(self, name: str) -> bool:
        return bool(self._scalar(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ))

    def status(self) -> dict[str, Any]:
        if not self.available:
            return {"database": str(self.path), "available": False}
        return {
            "database": str(self.path),
            "available": True,
            "projects": self._scalar("SELECT COUNT(*) FROM projects"),
            "tasks": self._scalar("SELECT COUNT(*) FROM tasks"),
            "jobs": self._scalar("SELECT COUNT(*) FROM jobs"),
            "events": self._scalar("SELECT COUNT(*) FROM events"),
            "latest_event": self._latest_event(),
            "latest_scheduler_tick": self._latest_scheduler_tick(),
        }

    def projects(self) -> list[dict[str, Any]]:
        if not self.available:
            return []
        return [dict(row) for row in self._rows(
            """SELECT p.*, COUNT(t.id) AS task_count,
                      SUM(CASE WHEN t.status='blocked' THEN 1 ELSE 0 END) AS blocked_task_count
               FROM projects p LEFT JOIN tasks t ON t.project_id=p.id
               GROUP BY p.id ORDER BY p.name, p.id"""
        )]

    def jobs(self) -> list[dict[str, Any]]:
        if not self.available:
            return []
        return [dict(row) for row in self._rows(
            """SELECT id, project_id, type, objective, status, priority,
                      attempt_count, max_attempts, leased_by, lease_expires_at,
                      created_at, updated_at, error
               FROM jobs ORDER BY created_at DESC, id DESC"""
        )]

    def episodes(self, limit: int = 20) -> list[dict[str, Any]]:
        """Read-only Episode projection list; never creates or repairs state."""
        if not self.available or not self._has_table("episodes"):
            return []
        limit = max(1, min(int(limit), 200))
        return [dict(row) for row in self._rows(
            """SELECT e.episode_id, e.intake_id, e.task_id,
                      e.task_contract_revision_id, e.task_contract_hash,
                      e.status, e.created_at, e.updated_at,
                      COUNT(ev.event_id) AS event_count
               FROM episodes e LEFT JOIN episode_events ev ON ev.episode_id=e.episode_id
               GROUP BY e.episode_id ORDER BY e.created_at DESC, e.episode_id DESC LIMIT ?""",
            (limit,),
        )]

    def capabilities(self) -> list[dict[str, Any]]:
        """Return capability descriptor projections without mutating state."""
        if not self.available or not self._has_table("capability_descriptors"):
            return []
        return [dict(row) for row in self._rows(
            """SELECT capability_id, version, lifecycle, canonical_hash, created_at
               FROM capability_descriptors ORDER BY capability_id"""
        )]

    def providers(self) -> list[dict[str, Any]]:
        """Return provider descriptors with append-only evaluation counts."""
        if not self.available or not self._has_table("provider_descriptors"):
            return []
        rows = self._rows(
            """SELECT p.provider_id, p.canonical_hash, p.descriptor_json, p.created_at,
                      (SELECT COUNT(*) FROM provider_evaluations e
                       WHERE e.provider_id = p.provider_id) AS evaluation_count
               FROM provider_descriptors p ORDER BY p.provider_id"""
        )
        result = []
        for row in rows:
            descriptor = json.loads(row["descriptor_json"])
            result.append({
                "provider_id": row["provider_id"],
                "provider_kind": descriptor["provider_kind"],
                "capabilities": descriptor["capabilities"],
                "canonical_hash": row["canonical_hash"],
                "evaluation_count": row["evaluation_count"],
                "created_at": row["created_at"],
            })
        return result

    def episode(self, episode_id: str) -> dict[str, Any] | None:
        """Return one Episode and its immutable lifecycle events."""
        if not self.available or not self._has_table("episodes"):
            return None
        rows = self._rows("SELECT * FROM episodes WHERE episode_id=?", (episode_id,))
        if not rows:
            return None
        result = dict(rows[0])
        result.pop("intake_payload_json", None)
        result["events"] = [dict(row) for row in self._rows(
            """SELECT event_id, event_type, status, payload_json,
                      idempotency_key, created_at
               FROM episode_events WHERE episode_id=?
               ORDER BY created_at, event_id""",
            (episode_id,),
        )] if self._has_table("episode_events") else []
        for event in result["events"]:
            event["payload"] = json.loads(event.pop("payload_json"))
        return result

    def events(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.available:
            return []
        limit = max(1, min(int(limit), 200))
        return [dict(row) for row in self._rows(
            """SELECT event_id, type, project_id, source, source_id,
                      observed_at, payload, created_at
               FROM events ORDER BY created_at DESC, event_id DESC LIMIT ?""",
            (limit,),
        )]

    def github_status(self) -> dict[str, Any]:
        configured = bool(os.environ.get("GITHUB_TOKEN"))
        if not self.available:
            return {
                "provider": "github", "configured": configured, "mapped_projects": 0,
                "last_successful_sync": None, "last_attempt": None,
                "stale": False, "last_error_class": None,
            }
        rows = self._rows(
            """SELECT type, project_id, created_at, payload FROM events
               WHERE source='github' AND type IN ('github.sync.completed', 'github.sync.failed')
               ORDER BY created_at"""
        )
        successes = [row for row in rows if row["type"] == "github.sync.completed"]
        failures = [row for row in rows if row["type"] == "github.sync.failed"]
        last_success = successes[-1] if successes else None
        last_attempt = rows[-1] if rows else None
        last_error_class = None
        if failures:
            try:
                payload = json.loads(failures[-1]["payload"])
                last_error_class = payload.get("error_class")
                if not last_error_class:
                    message = str(payload.get("error", "ProviderError"))
                    last_error_class = message.split(":", 1)[0].strip() or "ProviderError"
            except (TypeError, json.JSONDecodeError):
                last_error_class = "ProviderError"
        configured = configured or bool(rows)
        return {
            "provider": "github",
            "configured": configured,
            "mapped_projects": len({row["project_id"] for row in rows if row["project_id"]}),
            "last_successful_sync": last_success["created_at"] if last_success else None,
            "last_attempt": last_attempt["created_at"] if last_attempt else None,
            "stale": bool(last_attempt and (not last_success or last_attempt["created_at"] > last_success["created_at"])),
            "last_error_class": last_error_class,
        }

    def doctor(self) -> dict[str, Any]:
        if not self.available:
            return {
                "sqlite": {"ok": False, "message": "SQLite erişilemiyor"},
                "migrations": {"ok": False, "current": None, "expected": CURRENT_SCHEMA_VERSION},
                "queue": {"ok": False, "message": "database unavailable"},
                "scheduler": {"ok": False, "message": "database unavailable"},
                "github": self.github_status(),
                "disk": self._disk_status(),
                "event_log": {"ok": False, "message": "database unavailable"},
                "backup": {"status": "unknown", "message": "backup subsystem not configured"},
            }
        checks: dict[str, Any] = {
            "sqlite": {"ok": self._sqlite_ok()},
            "migrations": self._migration_status(),
            "queue": self._queue_status(),
            "scheduler": self._scheduler_status(),
            "github": self.github_status(),
            "disk": self._disk_status(),
            "event_log": self._event_log_status(),
            "backup": {"status": "unknown", "message": "backup subsystem not configured"},
        }
        return checks

    def _sqlite_ok(self) -> bool:
        try:
            return self._scalar("SELECT 1") == 1
        except sqlite3.Error:
            return False

    def _migration_status(self) -> dict[str, Any]:
        try:
            current = self._scalar("SELECT MAX(version) FROM schema_migrations")
        except sqlite3.Error as exc:
            return {"ok": False, "current": None, "expected": CURRENT_SCHEMA_VERSION, "error": str(exc)}
        return {"ok": current == CURRENT_SCHEMA_VERSION, "current": current, "expected": CURRENT_SCHEMA_VERSION}

    def _queue_status(self) -> dict[str, Any]:
        try:
            invalid = self._scalar(
                "SELECT COUNT(*) FROM jobs WHERE status NOT IN ({})".format(",".join("?" * len(KNOWN_JOB_STATUSES))),
                tuple(KNOWN_JOB_STATUSES),
            )
            malformed = self._scalar(
                """SELECT COUNT(*) FROM jobs
                   WHERE status IN ('leased', 'running')
                     AND (lease_token IS NULL OR lease_expires_at IS NULL)"""
            )
            return {"ok": invalid == 0 and malformed == 0, "invalid_statuses": invalid, "malformed_leases": malformed}
        except sqlite3.Error as exc:
            return {"ok": False, "error": str(exc)}

    def _scheduler_status(self) -> dict[str, Any]:
        try:
            latest = self._latest_scheduler_tick()
            schedules = self._scalar("SELECT COUNT(*) FROM schedules WHERE enabled=1")
            return {"ok": latest is not None, "enabled_schedules": schedules, "last_tick": latest}
        except sqlite3.Error as exc:
            return {"ok": False, "error": str(exc)}

    def _event_log_status(self) -> dict[str, Any]:
        try:
            count = self._scalar("SELECT COUNT(*) FROM events")
            return {"ok": True, "event_count": count}
        except sqlite3.Error as exc:
            return {"ok": False, "error": str(exc)}

    def _disk_status(self) -> dict[str, Any]:
        try:
            free = shutil.disk_usage(self.path.parent).free
            return {"ok": free >= 1 * 1024**3, "free_bytes": free}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def _latest_event(self) -> dict[str, Any] | None:
        rows = self._rows("SELECT type, source, created_at FROM events ORDER BY created_at DESC LIMIT 1")
        return dict(rows[0]) if rows else None

    def _latest_scheduler_tick(self) -> dict[str, Any] | None:
        rows = self._rows(
            "SELECT type, created_at FROM events WHERE type='scheduler.tick.completed' ORDER BY created_at DESC LIMIT 1"
        )
        return dict(rows[0]) if rows else None


def default_database_path() -> Path:
    return runtime_database_path()


def render(value: Any, *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if isinstance(value, list):
        return "\n".join(render(row, as_json=False) for row in value) or "(none)"
    if isinstance(value, dict):
        return "\n".join(f"{key}: {render(item, as_json=False)}" for key, item in value.items())
    return str(value)
