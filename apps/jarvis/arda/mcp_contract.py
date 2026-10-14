"""Allowlisted MCP gateway contract for the external Nous Hermes Agent.

This module deliberately describes and validates the boundary only.  It does
not implement MCP transport and it never invokes a provider, worker, shell, or
filesystem operation.  Execution belongs to Arda Core after policy approval.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class MCPGatewayContractError(ValueError):
    """An external tool call violates the Arda Core gateway contract."""


class MCPToolAccess(str, Enum):
    READ = "read"
    REQUEST = "request"


@dataclass(frozen=True)
class MCPToolDescriptor:
    name: str
    access: MCPToolAccess
    description: str
    mutates_core_state: bool
    required_arguments: tuple[str, ...] = ()
    forbidden_effects: tuple[str, ...] = ()

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "access": self.access.value,
            "description": self.description,
            "mutates_core_state": self.mutates_core_state,
            "required_arguments": list(self.required_arguments),
            "forbidden_effects": list(self.forbidden_effects),
        }

    @property
    def canonical_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MCPToolCall:
    """Validated intent to use one gateway tool; not an execution result."""

    tool_name: str
    arguments: Mapping[str, Any]
    caller: str
    correlation_id: str
    episode_id: str | None
    contract_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "caller": self.caller,
            "correlation_id": self.correlation_id,
            "episode_id": self.episode_id,
        }

    @property
    def canonical_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


_READ_TOOLS = (
    MCPToolDescriptor(
        name="arda_episode_status",
        access=MCPToolAccess.READ,
        description="Read the current projection and immutable lifecycle evidence for an episode.",
        mutates_core_state=False,
        required_arguments=("episode_id",),
        forbidden_effects=("state_mutation", "provider_invocation", "artifact_mutation"),
    ),
    MCPToolDescriptor(
        name="arda_verification_status",
        access=MCPToolAccess.READ,
        description="Read verification and completion evidence for an episode.",
        mutates_core_state=False,
        required_arguments=("episode_id",),
        forbidden_effects=("state_mutation", "provider_invocation"),
    ),
    MCPToolDescriptor(
        name="arda_artifact_read",
        access=MCPToolAccess.READ,
        description="Read an immutable, hash-addressed evidence artifact.",
        mutates_core_state=False,
        required_arguments=("artifact_id",),
        forbidden_effects=("artifact_mutation", "filesystem_write", "provider_invocation"),
    ),
    MCPToolDescriptor(
        name="arda_evaluation_read",
        access=MCPToolAccess.READ,
        description="Read evaluation records or derived provider capability observations.",
        mutates_core_state=False,
        required_arguments=("episode_id",),
        forbidden_effects=("state_mutation", "evaluation_mutation", "provider_invocation"),
    ),
)

_REQUEST_TOOLS = (
    MCPToolDescriptor(
        name="arda_task_intake",
        access=MCPToolAccess.REQUEST,
        description="Submit a human goal for Core validation and episode creation.",
        mutates_core_state=True,
        required_arguments=("intake_id", "human_goal", "task_type", "objective", "risk", "privacy", "source_ref"),
        forbidden_effects=("provider_invocation", "policy_bypass", "approval_grant"),
    ),
    MCPToolDescriptor(
        name="arda_route_proposal",
        access=MCPToolAccess.REQUEST,
        description="Request a deterministic provider proposal for an existing episode.",
        mutates_core_state=False,
        required_arguments=("episode_id", "task_contract_hash", "required_capabilities"),
        forbidden_effects=("provider_invocation", "route_decision", "policy_bypass"),
    ),
    MCPToolDescriptor(
        name="arda_request_approval",
        access=MCPToolAccess.REQUEST,
        description="Request human or Core approval for an existing gated decision.",
        mutates_core_state=True,
        required_arguments=("episode_id", "decision_id", "approval_payload_hash"),
        forbidden_effects=("approval_grant", "provider_invocation", "policy_bypass"),
    ),
    MCPToolDescriptor(
        name="arda_provider_evaluate",
        access=MCPToolAccess.REQUEST,
        description="Run one fixed, Core-owned provider evaluation fixture for an existing episode.",
        mutates_core_state=True,
        required_arguments=("episode_id", "task_contract_hash", "evaluation_id", "fixture_id"),
        forbidden_effects=("normal_routing", "approval_grant", "policy_bypass", "raw_output_exposure"),
    ),
)

_TOOLS = {item.name: item for item in (*_READ_TOOLS, *_REQUEST_TOOLS)}


class MCPGatewayContract:
    """Read-only registry and validator for the Arda MCP boundary."""

    @classmethod
    def list_tools(cls) -> tuple[MCPToolDescriptor, ...]:
        return tuple(_TOOLS[name] for name in sorted(_TOOLS))

    @classmethod
    def descriptor(cls, tool_name: str) -> MCPToolDescriptor:
        try:
            return _TOOLS[tool_name]
        except KeyError as exc:
            raise MCPGatewayContractError(f"tool is not allowlisted: {tool_name}") from exc

    @classmethod
    def validate_call(
        cls,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        caller: str = "nous-hermes",
        correlation_id: str,
        episode_id: str | None = None,
    ) -> MCPToolCall:
        descriptor = cls.descriptor(tool_name)
        if not caller.strip() or not correlation_id.strip():
            raise MCPGatewayContractError("caller and correlation_id are required")
        if not isinstance(arguments, Mapping):
            raise MCPGatewayContractError("tool arguments must be an object")
        missing = tuple(name for name in descriptor.required_arguments if name not in arguments)
        if missing:
            raise MCPGatewayContractError(
                f"missing required arguments for {tool_name}: {', '.join(missing)}"
            )
        if any(value is None for value in arguments.values()):
            raise MCPGatewayContractError("tool arguments cannot contain null values")
        return MCPToolCall(
            tool_name=tool_name,
            arguments=dict(arguments),
            caller=caller,
            correlation_id=correlation_id,
            episode_id=episode_id,
            contract_hash=descriptor.canonical_hash,
        )
