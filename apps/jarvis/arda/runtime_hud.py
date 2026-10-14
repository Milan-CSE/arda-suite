"""Pure read-only projection of structured agent runtime events for the native UI."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from arda.models import AgentRuntimeEvent


@dataclass(frozen=True)
class ToolTimelineItem:
    step_id: str
    tool: str
    status: str = "running"
    first_seen: str = ""
    last_seen: str = ""


@dataclass(frozen=True)
class RuntimeSnapshot:
    run_id: str | None = None
    status: str = "idle"
    started_at: str | None = None
    current_tool: str | None = None
    approval_status: str | None = None
    terminal_outcome: str | None = None
    termination_reason: str | None = None
    tools: tuple[ToolTimelineItem, ...] = ()


_TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "timed_out"}
_TOOL_STATUS_RANK = {"running": 1, "failed": 2, "completed": 3}


def _event_key(event: AgentRuntimeEvent) -> tuple:
    return (event.event_type, event.run_id, event.step_id, event.timestamp, event.status,
            tuple(sorted((str(key), repr(value)) for key, value in event.metadata.items())))


class RuntimeProjection:
    """Idempotent projection that never invents a terminal state."""

    def __init__(self) -> None:
        self.snapshot = RuntimeSnapshot()
        self._seen: set[tuple] = set()
        self._terminal_runs: set[str] = set()

    def reset(self) -> None:
        self.snapshot = RuntimeSnapshot()
        self._seen.clear()
        self._terminal_runs.clear()

    def _new_run(self, event: AgentRuntimeEvent) -> None:
        self.snapshot = RuntimeSnapshot(
            run_id=event.run_id,
            status="running",
            started_at=event.timestamp,
        )

    def _replace_snapshot(self, **changes) -> None:
        self.snapshot = replace(self.snapshot, **changes)

    def _update_tool(self, event: AgentRuntimeEvent) -> None:
        step_id = event.step_id
        if not step_id:
            return
        tool = str(event.metadata.get("tool") or step_id)
        status = event.status if event.event_type.endswith("failed") else (
            "completed" if event.event_type.endswith("completed") else "running"
        )
        items = list(self.snapshot.tools)
        for index, item in enumerate(items):
            if item.step_id != step_id:
                continue
            if _TOOL_STATUS_RANK.get(status, 0) >= _TOOL_STATUS_RANK.get(item.status, 0):
                items[index] = ToolTimelineItem(
                    item.step_id, tool, status, item.first_seen or event.timestamp, event.timestamp
                )
            self._replace_snapshot(
                tools=tuple(items),
                current_tool=tool if status == "running" else self.snapshot.current_tool,
            )
            return
        items.append(ToolTimelineItem(step_id, tool, status, event.timestamp, event.timestamp))
        self._replace_snapshot(
            tools=tuple(items),
            current_tool=tool if status == "running" else self.snapshot.current_tool,
        )

    def apply(self, event: AgentRuntimeEvent | None) -> RuntimeSnapshot:
        if event is None:
            return self.snapshot
        key = _event_key(event)
        if key in self._seen:
            return self.snapshot
        self._seen.add(key)

        if event.event_type == "agent.run.started":
            if self.snapshot.run_id != event.run_id:
                self._new_run(event)
            else:
                self._replace_snapshot(
                    status="running",
                    started_at=self.snapshot.started_at or event.timestamp,
                )
        elif self.snapshot.run_id is None:
            self._new_run(event)
        elif event.run_id != self.snapshot.run_id:
            # A late event from an older run must not overwrite the active run.
            if event.run_id in self._terminal_runs:
                return self.snapshot
            if event.event_type == "agent.run.started":
                self._new_run(event)
            else:
                return self.snapshot

        if event.event_type.startswith("agent.tool."):
            self._update_tool(event)
        elif event.event_type == "agent.approval.requested":
            self._replace_snapshot(approval_status="waiting")
        elif event.event_type == "agent.approval.resolved":
            self._replace_snapshot(approval_status=event.status)
        elif event.event_type.startswith("agent.run.") and event.event_type != "agent.run.started":
            self._replace_snapshot(
                status=event.status,
                terminal_outcome=event.status,
                termination_reason=(
                    str(event.metadata["termination_reason"])
                    if event.metadata.get("termination_reason")
                    else self.snapshot.termination_reason
                ),
            )
            if event.status in _TERMINAL_RUN_STATUSES and event.run_id:
                self._terminal_runs.add(event.run_id)
        return self.snapshot

    def elapsed_seconds(self, now: datetime | None = None) -> float | None:
        if not self.snapshot.started_at:
            return None
        try:
            started = datetime.fromisoformat(self.snapshot.started_at)
            current = now or datetime.now(started.tzinfo)
            return max(0.0, (current - started).total_seconds())
        except (TypeError, ValueError):
            return None
