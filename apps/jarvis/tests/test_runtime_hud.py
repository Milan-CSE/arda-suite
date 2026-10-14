from arda.models import AgentRuntimeEvent
from arda.runtime_hud import RuntimeProjection


def event(event_type, run_id="run_1", step_id=None, status="running", timestamp="2026-09-24T13:00:00+00:00", **metadata):
    return AgentRuntimeEvent(event_type, run_id, step_id, timestamp, status, metadata)


def test_projection_tracks_real_events_and_ignores_duplicates():
    projection = RuntimeProjection()
    started = event("agent.run.started")
    projection.apply(started)
    projection.apply(started)
    projection.apply(event("agent.tool.started", step_id="step_1", tool="browser_navigate"))
    snapshot = projection.apply(event("agent.tool.completed", step_id="step_1", status="completed", tool="browser_navigate"))
    assert snapshot.status == "running"
    assert len(snapshot.tools) == 1
    assert snapshot.tools[0].status == "completed"


def test_out_of_order_tool_completion_does_not_regress():
    projection = RuntimeProjection()
    projection.apply(event("agent.run.started"))
    projection.apply(event("agent.tool.completed", step_id="step_1", status="completed", tool="read_file"))
    snapshot = projection.apply(event("agent.tool.started", step_id="step_1", tool="read_file", timestamp="2026-09-24T13:00:01+00:00"))
    assert snapshot.tools[0].status == "completed"


def test_late_old_run_events_do_not_overwrite_new_run():
    projection = RuntimeProjection()
    projection.apply(event("agent.run.started", run_id="run_1"))
    projection.apply(event("agent.run.completed", run_id="run_1", status="completed"))
    projection.apply(event("agent.run.started", run_id="run_2", timestamp="2026-09-24T13:01:00+00:00"))
    snapshot = projection.apply(event("agent.tool.started", run_id="run_1", step_id="old", tool="read_file"))
    assert snapshot.run_id == "run_2"
    assert snapshot.tools == ()


def test_missing_terminal_event_does_not_infer_completion():
    projection = RuntimeProjection()
    snapshot = projection.apply(event("agent.tool.started", step_id="step_1", tool="list_dir"))
    assert snapshot.status == "running"
    assert snapshot.terminal_outcome is None


def test_approval_projection():
    projection = RuntimeProjection()
    projection.apply(event("agent.run.started"))
    projection.apply(event("agent.approval.requested", step_id="step_1", status="waiting", tool="browser_navigate"))
    snapshot = projection.apply(event("agent.approval.resolved", step_id="step_1", status="denied", tool="browser_navigate"))
    assert snapshot.approval_status == "denied"
