import json

import pytest

from arda.core import Database
from arda.mcp_action_service import CoreActionService
from arda.mcp_read_service import CoreReadService
from arda.mcp_server import ActionMCPGateway


def action_gateway(tmp_path):
    db = Database(tmp_path / "core.sqlite3")
    db.migrate()
    service = CoreActionService(db)
    gateway = ActionMCPGateway.from_core_services(
        CoreReadService(db), service, audit_path=tmp_path / "mcp-audit.jsonl"
    )
    return db, gateway


def call(gateway, request_id, name, arguments, *, episode_id=None):
    return gateway.handle_request({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
            "_meta": {
                "caller": "nous-hermes",
                "correlation_id": f"corr-{request_id}",
                **({"episode_id": episode_id} if episode_id else {}),
            },
        },
    })


def payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def intake_args(intake_id="intake-action-1"):
    return {
        "intake_id": intake_id,
        "human_goal": "Create a tracked diagnostic task",
        "task_type": "diagnostic",
        "objective": "Create a Core task without execution",
        "risk": "low",
        "privacy": "private",
        "source_ref": "mcp:nous-hermes:test",
        "acceptance_criteria": ["episode exists"],
    }


def test_action_profile_exposes_requests_but_no_provider_or_shell(tmp_path):
    db, gateway = action_gateway(tmp_path)
    listed = gateway.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {item["name"] for item in listed["result"]["tools"]}
    assert names == {
        "arda_artifact_read",
        "arda_episode_status",
        "arda_evaluation_read",
        "arda_request_approval",
        "arda_route_proposal",
        "arda_task_intake",
        "arda_verification_status",
    }
    assert "shell" not in names
    assert "arda_provider_invoke" not in names
    db.close()


def test_task_intake_creates_episode_but_never_starts_execution(tmp_path):
    db, gateway = action_gateway(tmp_path)
    response = call(gateway, "intake", "arda_task_intake", intake_args())
    result = payload(response)
    assert response["result"]["isError"] is False
    assert result["status"] == "EPISODE_CREATED"
    assert result["execution_started"] is False
    assert result["episode_status"] == "planned"
    status = payload(call(
        gateway, "status", "arda_episode_status", {"episode_id": result["episode_id"]},
        episode_id=result["episode_id"],
    ))
    assert status["episode_id"] == result["episode_id"]
    db.close()


def test_task_intake_is_idempotent_and_conflict_is_rejected(tmp_path):
    db, gateway = action_gateway(tmp_path)
    first = payload(call(gateway, "first", "arda_task_intake", intake_args()))
    second = payload(call(gateway, "second", "arda_task_intake", intake_args()))
    assert second["episode_id"] == first["episode_id"]
    conflict = call(gateway, "conflict", "arda_task_intake", {
        **intake_args(), "objective": "changed payload",
    })
    assert conflict["result"]["isError"] is True
    assert "different payload" in conflict["result"]["content"][0]["text"]
    assert db.scalar("SELECT COUNT(*) FROM episodes") == 1
    db.close()


def test_task_intake_rejects_non_array_sequence_fields(tmp_path):
    db, gateway = action_gateway(tmp_path)
    response = call(gateway, "bad-shape", "arda_task_intake", {
        **intake_args(), "acceptance_criteria": "not-an-array",
    })
    assert response["result"]["isError"] is True
    assert "must be an array of strings" in response["result"]["content"][0]["text"]
    assert db.scalar("SELECT COUNT(*) FROM episodes") == 0
    db.close()


def test_approval_request_creates_pending_approval_but_cannot_approve_or_consume(tmp_path):
    db, gateway = action_gateway(tmp_path)
    response = call(gateway, "approval", "arda_request_approval", {
        "episode_id": "episode-1",
        "decision_id": "decision-1",
        "approval_payload_hash": "payload-hash",
    }, episode_id="episode-1")
    result = payload(response)
    assert result["status"] == "APPROVAL_REQUESTED"
    assert result["approval_status"] == "pending"
    assert result["execution_started"] is False
    assert db.scalar("SELECT COUNT(*) FROM approvals WHERE status='pending'") == 1
    assert "approve" not in {item["name"] for item in gateway.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]}
    db.close()


def test_route_request_is_rejected_without_core_route_service(tmp_path):
    db, gateway = action_gateway(tmp_path)
    response = call(gateway, "route", "arda_route_proposal", {
        "episode_id": "episode-1",
        "task_contract_hash": "contract-hash",
        "required_capabilities": ["agent.hermes_loop"],
    }, episode_id="episode-1")
    assert response["result"]["isError"] is True
    assert "route proposal service is not configured" in response["result"]["content"][0]["text"]
    db.close()
