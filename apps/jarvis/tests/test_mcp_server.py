import json
import time

from arda.core import Database
from arda.episodes import EpisodeStore
from arda.mcp_read_service import CoreReadService
from arda.mcp_server import EvaluationMCPGateway, IntakeMCPGateway, MCPExecutionBudget, ReadOnlyMCPGateway
from arda.task_intake import TaskIntakeRequest


def gateway():
    return ReadOnlyMCPGateway({
        "arda_episode_status": lambda args: {"episode_id": args["episode_id"], "status": "open"},
        "arda_verification_status": lambda args: {"episode_id": args["episode_id"], "status": "pending"},
        "arda_artifact_read": lambda args: {"artifact_id": args["artifact_id"], "immutable": True},
        "arda_evaluation_read": lambda args: {"episode_id": args["episode_id"], "records": []},
    })


def test_initialize_and_tools_list_expose_read_only_profile():
    server = gateway()
    initialized = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    listed = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert initialized["result"]["serverInfo"]["name"] == "arda-core-readonly"
    assert [tool["name"] for tool in listed["result"]["tools"]] == [
        "arda_artifact_read", "arda_episode_status", "arda_evaluation_read", "arda_verification_status"
    ]


def test_read_only_call_returns_result_and_audit_provenance():
    server = gateway()
    response = server.handle_request({
        "jsonrpc": "2.0", "id": "call-1", "method": "tools/call",
        "params": {
            "name": "arda_episode_status",
            "arguments": {"episode_id": "episode-1"},
            "_meta": {"caller": "nous-hermes", "correlation_id": "corr-1", "episode_id": "episode-1"},
        },
    })
    assert response["result"]["isError"] is False
    assert json.loads(response["result"]["content"][0]["text"])["status"] == "open"
    assert server.audit_records[0].outcome == "success"
    assert server.audit_records[0].episode_id == "episode-1"
    assert server.audit_records[0].request_hash
    assert server.audit_records[0].result_hash


def test_execution_budget_rejects_after_max_tool_calls():
    server = ReadOnlyMCPGateway(
        {
            "arda_episode_status": lambda args: {"episode_id": args["episode_id"], "status": "open"},
            "arda_verification_status": lambda args: {"episode_id": args["episode_id"], "status": "pending"},
            "arda_artifact_read": lambda args: {"artifact_id": args["artifact_id"], "immutable": True},
            "arda_evaluation_read": lambda args: {"episode_id": args["episode_id"], "records": []},
        },
        execution_budget=MCPExecutionBudget(max_tool_calls=1),
    )
    first = server.handle_request({
        "jsonrpc": "2.0", "id": 10, "method": "tools/call",
        "params": {"name": "arda_episode_status", "arguments": {"episode_id": "episode-1"}},
    })
    second = server.handle_request({
        "jsonrpc": "2.0", "id": 11, "method": "tools/call",
        "params": {"name": "arda_verification_status", "arguments": {"episode_id": "episode-1"}},
    })
    assert first["result"]["isError"] is False
    assert second["result"]["isError"] is True
    assert "max_tool_calls" in second["result"]["content"][0]["text"]
    assert [record.outcome for record in server.audit_records] == ["success", "rejected"]


def test_execution_budget_rejects_expired_session_before_handler():
    calls = []
    server = ReadOnlyMCPGateway(
        {
            "arda_episode_status": lambda args: calls.append(args) or {"status": "open"},
            "arda_verification_status": lambda args: {"status": "pending"},
            "arda_artifact_read": lambda args: {"immutable": True},
            "arda_evaluation_read": lambda args: {"records": []},
        },
        execution_budget=MCPExecutionBudget(
            max_duration_seconds=1,
            started_at=time.monotonic() - 2,
        ),
    )
    response = server.handle_request({
        "jsonrpc": "2.0", "id": 12, "method": "tools/call",
        "params": {"name": "arda_episode_status", "arguments": {"episode_id": "episode-1"}},
    })
    assert response["result"]["isError"] is True
    assert "max_duration_seconds" in response["result"]["content"][0]["text"]
    assert calls == []


def test_action_tool_is_rejected_without_dispatch():
    server = gateway()
    response = server.handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "arda_task_intake", "arguments": {"intake_id": "x"}},
    })
    assert response["result"]["isError"] is True
    assert "not enabled in this MCP profile" in response["result"]["content"][0]["text"]
    assert server.audit_records[0].outcome == "rejected"


def test_unknown_method_and_notification_behavior():
    server = gateway()
    assert server.handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/call"})["error"]["code"] == -32602
    assert server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_gateway_can_be_wired_to_core_read_service(tmp_path):
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    EpisodeStore(db).create(
        TaskIntakeRequest(
            intake_id="intake-server",
            human_goal="Inspect status",
            task_type="diagnostic",
            objective="Inspect status",
            risk="low",
            privacy="private",
            source_ref="test:mcp-server",
            acceptance_criteria=("status available",),
        ),
        task_id="task-server",
        episode_id="episode-server",
        revision_id="revision-server",
    )
    server = ReadOnlyMCPGateway.from_core_read_service(CoreReadService(db), audit_path=tmp_path / "mcp-audit.jsonl")
    response = server.handle_request({
        "jsonrpc": "2.0", "id": "core-read", "method": "tools/call",
        "params": {"name": "arda_episode_status", "arguments": {"episode_id": "episode-server"}},
    })
    payload = json.loads(response["result"]["content"][0]["text"])
    assert response["result"]["isError"] is False
    assert payload["episode_id"] == "episode-server"
    assert "success" in (tmp_path / "mcp-audit.jsonl").read_text()
    db.close()


def test_evaluation_profile_exposes_only_fixed_provider_evaluation_request():
    server = EvaluationMCPGateway.from_core_services(
        type("Read", (), {
            "episode_status": lambda _self, args: {"episode_id": args["episode_id"]},
            "verification_status": lambda _self, args: {"episode_id": args["episode_id"]},
            "artifact_read": lambda _self, args: {"artifact_id": args["artifact_id"]},
            "evaluation_read": lambda _self, args: {"episode_id": args["episode_id"]},
        })(),
        type("Eval", (), {"evaluate": lambda _self, args: {"status": "EVALUATION_COMPLETED"}})(),
    )
    names = [tool["name"] for tool in server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
    assert "arda_provider_evaluate" in names
    assert "arda_task_intake" not in names
    assert "arda_request_approval" not in names
    response = server.handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "arda_provider_evaluate", "arguments": {
            "episode_id": "episode-1", "task_contract_hash": "hash",
            "evaluation_id": "eval-1", "fixture_id": "hermes_core_provider_v0.1",
        }},
    })
    assert response["result"]["isError"] is False
    blocked = server.handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "arda_task_intake", "arguments": {"intake_id": "x"}},
    })
    assert blocked["result"]["isError"] is True


def test_intake_profile_exposes_task_intake_but_not_route_or_provider():
    read = type("Read", (), {
        "episode_status": lambda _self, args: {"episode_id": args["episode_id"]},
        "verification_status": lambda _self, args: {"episode_id": args["episode_id"]},
        "artifact_read": lambda _self, args: {"artifact_id": args["artifact_id"]},
        "evaluation_read": lambda _self, args: {"episode_id": args["episode_id"]},
    })()
    action = type("Action", (), {"task_intake": lambda _self, args: {"status": "EPISODE_CREATED"}})()
    server = IntakeMCPGateway.from_core_services(read, action)
    names = {tool["name"] for tool in server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
    assert "arda_task_intake" in names
    assert "arda_route_proposal" not in names
    assert "arda_provider_evaluate" not in names
