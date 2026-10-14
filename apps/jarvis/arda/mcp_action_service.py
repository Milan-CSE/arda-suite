"""Core-owned low-risk action handlers for the MCP action boundary."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from arda.core import ApprovalService, Database
from arda.episodes import EpisodeStore
from arda.task_intake import TaskIntakeRequest


class MCPActionError(ValueError):
    """An action request cannot be accepted by the Core application layer."""


class CoreActionService:
    """Application services behind request tools, never provider execution."""

    def __init__(
        self,
        db: Database,
        *,
        route_proposer: Callable[[Mapping[str, Any]], Any] | None = None,
    ):
        self.episodes = EpisodeStore(db)
        self.approvals = ApprovalService(db)
        self._route_proposer = route_proposer

    def task_intake(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        request = TaskIntakeRequest(
            intake_id=str(arguments["intake_id"]),
            human_goal=str(arguments["human_goal"]),
            task_type=str(arguments["task_type"]),
            objective=str(arguments["objective"]),
            risk=str(arguments["risk"]),
            privacy=str(arguments["privacy"]),
            source_ref=str(arguments["source_ref"]),
            required_capabilities=_string_tuple(arguments, "required_capabilities"),
            constraints=_string_tuple(arguments, "constraints"),
            acceptance_criteria=_string_tuple(arguments, "acceptance_criteria"),
            evidence_requirements=_string_tuple(arguments, "evidence_requirements"),
            allowed_scope=_string_tuple(arguments, "allowed_scope"),
            forbidden_changes=_string_tuple(arguments, "forbidden_changes"),
            candidate_requirements=_string_tuple(arguments, "candidate_requirements"),
            unresolved_uncertainties=_string_tuple(arguments, "unresolved_uncertainties"),
            created_by="SYSTEM",
        )
        episode = self.episodes.create(request)
        return {
            "status": "EPISODE_CREATED",
            "episode_id": episode.episode_id,
            "task_id": episode.task_id,
            "task_contract_revision_id": episode.task_contract_revision_id,
            "task_contract_hash": episode.task_contract_hash,
            "episode_status": episode.status.value,
            "execution_started": False,
        }

    def route_proposal(self, arguments: Mapping[str, Any]) -> Any:
        if self._route_proposer is None:
            raise MCPActionError("route proposal service is not configured")
        result = self._route_proposer(dict(arguments))
        return {"status": "ROUTE_PROPOSAL_CREATED", "proposal": result}

    def request_approval(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        episode_id = str(arguments["episode_id"])
        decision_id = str(arguments["decision_id"])
        approval_payload_hash = str(arguments["approval_payload_hash"])
        payload = {
            "episode_id": episode_id,
            "decision_id": decision_id,
            "approval_payload_hash": approval_payload_hash,
        }
        approval = self.approvals.request(
            "provider.route_decision",
            decision_id,
            payload,
            requested_by="nous-hermes",
        )
        return {
            "status": "APPROVAL_REQUESTED",
            "approval_id": approval["id"],
            "approval_status": approval["status"],
            "payload_hash": approval["payload_hash"],
            "execution_started": False,
        }


def _string_tuple(arguments: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = arguments.get(key, ())
    if not isinstance(value, (list, tuple)):
        raise MCPActionError(f"{key} must be an array of strings")
    if any(not isinstance(item, str) for item in value):
        raise MCPActionError(f"{key} must be an array of strings")
    return tuple(value)
