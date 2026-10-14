"""Minimal stdio-friendly read-only MCP gateway surface.

This is intentionally a transport adapter, not a second authority layer.  It
implements the small JSON-RPC surface needed for discovery and read-only tool
calls.  Core-owned handlers are injected by the host; no provider, shell,
filesystem, approval, or evaluation mutation is available here.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from arda.mcp_contract import (
    MCPGatewayContract,
    MCPGatewayContractError,
    MCPToolAccess,
)


class MCPServerError(ValueError):
    """The MCP request is malformed or not enabled in this gateway profile."""


class MCPExecutionBudgetExceeded(MCPServerError):
    """The current agent loop exceeded its Core-side execution budget."""


@dataclass
class MCPExecutionBudget:
    """Fail-closed budget shared by one MCP gateway process/session."""

    max_tool_calls: int = 8
    max_duration_seconds: float = 180.0
    started_at: float = field(default_factory=time.monotonic)
    tool_calls: int = 0

    def reserve(self) -> None:
        if self.max_tool_calls <= 0 or self.max_duration_seconds <= 0:
            raise MCPExecutionBudgetExceeded("invalid MCP execution budget")
        if self.tool_calls >= self.max_tool_calls:
            raise MCPExecutionBudgetExceeded(
                "MCP execution budget exhausted: max_tool_calls"
            )
        if time.monotonic() - self.started_at >= self.max_duration_seconds:
            raise MCPExecutionBudgetExceeded(
                "MCP execution budget exhausted: max_duration_seconds"
            )
        self.tool_calls += 1


@dataclass(frozen=True)
class MCPAuditRecord:
    request_hash: str
    tool_name: str | None
    caller: str
    correlation_id: str
    episode_id: str | None
    outcome: str
    result_hash: str
    created_at: str


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class MCPGateway:
    """JSON-RPC dispatcher for one explicit Core MCP access profile."""

    protocol_version = "2024-11-05"

    def __init__(
        self,
        handlers: Mapping[str, Callable[[Mapping[str, Any]], Any]],
        *,
        allowed_access: tuple[MCPToolAccess, ...],
        server_name: str,
        audit_path: str | Path | None = None,
        execution_budget: MCPExecutionBudget | None = None,
    ):
        allowed = {
            descriptor.name
            for descriptor in MCPGatewayContract.list_tools()
            if descriptor.access in allowed_access
        }
        unknown = set(handlers) - allowed
        if unknown:
            raise MCPServerError(f"handler is not enabled in this MCP profile: {sorted(unknown)}")
        self._handlers = dict(handlers)
        self._allowed_access = frozenset(allowed_access)
        self._server_name = server_name
        self._audit: list[MCPAuditRecord] = []
        self._audit_path = Path(audit_path) if audit_path is not None else None
        self._execution_budget = execution_budget or MCPExecutionBudget()

    @classmethod
    def from_core_read_service(
        cls,
        service: Any,
        *,
        audit_path: str | Path | None = None,
        execution_budget: MCPExecutionBudget | None = None,
    ) -> "ReadOnlyMCPGateway":
        """Build the allowlisted gateway without exposing domain stores."""
        return cls({
            "arda_episode_status": service.episode_status,
            "arda_verification_status": service.verification_status,
            "arda_artifact_read": service.artifact_read,
            "arda_evaluation_read": service.evaluation_read,
        }, audit_path=audit_path, execution_budget=execution_budget)

    @property
    def audit_records(self) -> tuple[MCPAuditRecord, ...]:
        return tuple(self._audit)

    def handle_request(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        if request.get("jsonrpc") != "2.0":
            return self._error(request.get("id"), -32600, "invalid JSON-RPC request")
        method = request.get("method")
        request_id = request.get("id")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return self._response(request_id, {
                "protocolVersion": self.protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self._server_name, "version": "0.1"},
            })
        if method == "tools/list":
            return self._response(request_id, {"tools": [self._tool_schema(name) for name in sorted(self._handlers)]})
        if method == "tools/call":
            return self._call_tool(request)
        return self._error(request_id, -32601, "method not found")

    def _call_tool(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        params = request.get("params")
        if not isinstance(params, Mapping):
            return self._error(request_id, -32602, "params must be an object")
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        meta = params.get("_meta", {})
        if not isinstance(tool_name, str) or not isinstance(meta, Mapping):
            return self._error(request_id, -32602, "invalid tool call")
        caller = str(meta.get("caller", "nous-hermes"))
        correlation_id = str(meta.get("correlation_id", f"mcp-request-{request_id}"))
        episode_id = meta.get("episode_id")
        request_hash = _hash({"id": request_id, "method": request.get("method"), "params": dict(params)})
        try:
            self._execution_budget.reserve()
            descriptor = MCPGatewayContract.descriptor(tool_name)
            if descriptor.access not in self._allowed_access or tool_name not in self._handlers:
                raise MCPServerError(f"tool is not enabled in this MCP profile: {tool_name}")
            call = MCPGatewayContract.validate_call(
                tool_name,
                arguments,
                caller=caller,
                correlation_id=correlation_id,
                episode_id=episode_id if isinstance(episode_id, str) else None,
            )
            result = self._handlers[tool_name](call.arguments)
            result_hash = _hash(result)
            self._record(request_hash, tool_name, caller, correlation_id, call.episode_id, "success", result_hash)
            return self._response(request_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, sort_keys=True)}],
                "isError": False,
            })
        except (MCPGatewayContractError, MCPServerError, TypeError, ValueError) as error:
            result = {"error": str(error)}
            result_hash = _hash(result)
            self._record(request_hash, tool_name, caller, correlation_id, episode_id if isinstance(episode_id, str) else None, "rejected", result_hash)
            return self._response(request_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": True,
            })

    def _tool_schema(self, tool_name: str) -> dict[str, Any]:
        descriptor = MCPGatewayContract.descriptor(tool_name)
        properties = {name: {"type": "string"} for name in descriptor.required_arguments}
        if tool_name == "arda_evaluation_read":
            properties["episode_id"] = {"type": "string"}
        return {
            "name": descriptor.name,
            "description": descriptor.description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": list(descriptor.required_arguments),
                "additionalProperties": True,
            },
        }

    def _record(self, request_hash: str, tool_name: str, caller: str, correlation_id: str, episode_id: str | None, outcome: str, result_hash: str) -> None:
        record = MCPAuditRecord(
            request_hash=request_hash,
            tool_name=tool_name,
            caller=caller,
            correlation_id=correlation_id,
            episode_id=episode_id,
            outcome=outcome,
            result_hash=result_hash,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._audit.append(record)
        if self._audit_path is not None:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record.__dict__, ensure_ascii=False, sort_keys=True) + "\n")
            os.chmod(self._audit_path, 0o600)

    @staticmethod
    def _response(request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class ReadOnlyMCPGateway(MCPGateway):
    """MCP profile exposing only Core read tools."""

    def __init__(self, handlers: Mapping[str, Callable[[Mapping[str, Any]], Any]], *, audit_path: str | Path | None = None, execution_budget: MCPExecutionBudget | None = None):
        super().__init__(
            handlers,
            allowed_access=(MCPToolAccess.READ,),
            server_name="arda-core-readonly",
            audit_path=audit_path,
            execution_budget=execution_budget,
        )

    @classmethod
    def from_core_read_service(cls, service: Any, *, audit_path: str | Path | None = None, execution_budget: MCPExecutionBudget | None = None) -> "ReadOnlyMCPGateway":
        return cls({
            "arda_episode_status": service.episode_status,
            "arda_verification_status": service.verification_status,
            "arda_artifact_read": service.artifact_read,
            "arda_evaluation_read": service.evaluation_read,
        }, audit_path=audit_path, execution_budget=execution_budget)


class ActionMCPGateway(MCPGateway):
    """Read plus low-risk Core request tools; never an execution gateway."""

    def __init__(self, handlers: Mapping[str, Callable[[Mapping[str, Any]], Any]], *, audit_path: str | Path | None = None, execution_budget: MCPExecutionBudget | None = None):
        super().__init__(
            handlers,
            allowed_access=(MCPToolAccess.READ, MCPToolAccess.REQUEST),
            server_name="arda-core-action-boundary",
            audit_path=audit_path,
            execution_budget=execution_budget,
        )

    @classmethod
    def from_core_services(
        cls,
        read_service: Any,
        action_service: Any,
        *,
        audit_path: str | Path | None = None,
        execution_budget: MCPExecutionBudget | None = None,
    ) -> "ActionMCPGateway":
        return cls({
            "arda_episode_status": read_service.episode_status,
            "arda_verification_status": read_service.verification_status,
            "arda_artifact_read": read_service.artifact_read,
            "arda_evaluation_read": read_service.evaluation_read,
            "arda_task_intake": action_service.task_intake,
            "arda_route_proposal": action_service.route_proposal,
            "arda_request_approval": action_service.request_approval,
        }, audit_path=audit_path, execution_budget=execution_budget)


class EvaluationMCPGateway(MCPGateway):
    """Read tools plus one fixed Core-owned provider-evaluation request."""

    def __init__(self, handlers: Mapping[str, Callable[[Mapping[str, Any]], Any]], *, audit_path: str | Path | None = None, execution_budget: MCPExecutionBudget | None = None):
        super().__init__(handlers, allowed_access=(MCPToolAccess.READ, MCPToolAccess.REQUEST), server_name="arda-core-evaluation-boundary", audit_path=audit_path, execution_budget=execution_budget)

    @classmethod
    def from_core_services(cls, read_service: Any, evaluation_service: Any, *, audit_path: str | Path | None = None, execution_budget: MCPExecutionBudget | None = None) -> "EvaluationMCPGateway":
        return cls({
            "arda_episode_status": read_service.episode_status,
            "arda_verification_status": read_service.verification_status,
            "arda_artifact_read": read_service.artifact_read,
            "arda_evaluation_read": read_service.evaluation_read,
            "arda_provider_evaluate": evaluation_service.evaluate,
        }, audit_path=audit_path, execution_budget=execution_budget)


class IntakeMCPGateway(MCPGateway):
    """Read tools plus task intake only; no routing, approval or execution."""

    def __init__(self, handlers: Mapping[str, Callable[[Mapping[str, Any]], Any]], *, audit_path: str | Path | None = None, execution_budget: MCPExecutionBudget | None = None):
        super().__init__(handlers, allowed_access=(MCPToolAccess.READ, MCPToolAccess.REQUEST), server_name="arda-core-intake-boundary", audit_path=audit_path, execution_budget=execution_budget)

    @classmethod
    def from_core_services(cls, read_service: Any, action_service: Any, *, audit_path: str | Path | None = None, execution_budget: MCPExecutionBudget | None = None) -> "IntakeMCPGateway":
        return cls({
            "arda_episode_status": read_service.episode_status,
            "arda_verification_status": read_service.verification_status,
            "arda_artifact_read": read_service.artifact_read,
            "arda_evaluation_read": read_service.evaluation_read,
            "arda_task_intake": action_service.task_intake,
        }, audit_path=audit_path, execution_budget=execution_budget)


def serve_stdio(gateway: MCPGateway) -> None:
    """Serve newline-delimited JSON-RPC messages; logs never use stdout."""
    import sys

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = gateway.handle_request(request)
        except (json.JSONDecodeError, TypeError):
            response = gateway._error(None, -32700, "parse error")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
