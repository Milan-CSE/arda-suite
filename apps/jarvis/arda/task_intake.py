"""Typed HumanGoal -> TaskContract intake boundary."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from arda.requirements import Requirement, TaskContract


class TaskIntakeError(ValueError):
    """The human goal cannot become a valid Core task contract."""


TASK_TYPES = frozenset({"research", "coding", "review", "maintenance", "diagnostic", "other"})
RISKS = frozenset({"low", "medium", "high", "critical"})
PRIVACY_CLASSES = frozenset({"public", "internal", "private", "secret"})
CREATORS = frozenset({"USER", "SYSTEM"})


@dataclass(frozen=True)
class TaskIntakeRequest:
    intake_id: str
    human_goal: str
    task_type: str
    objective: str
    risk: str
    privacy: str
    source_ref: str
    required_capabilities: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    explicit_requirements: tuple[Requirement, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    allowed_scope: tuple[str, ...] = ()
    forbidden_changes: tuple[str, ...] = ()
    candidate_requirements: tuple[str, ...] = ()
    unresolved_uncertainties: tuple[str, ...] = ()
    created_by: str = "USER"

    def validate(self) -> "TaskIntakeRequest":
        for field in ("intake_id", "human_goal", "objective", "source_ref"):
            if not getattr(self, field).strip():
                raise TaskIntakeError(f"{field} is required")
        if self.task_type not in TASK_TYPES:
            raise TaskIntakeError("unknown task_type")
        if self.risk not in RISKS:
            raise TaskIntakeError("unknown risk")
        if self.privacy not in PRIVACY_CLASSES:
            raise TaskIntakeError("unknown privacy")
        if self.created_by not in CREATORS:
            raise TaskIntakeError("unknown created_by")
        for name, values in (("required_capabilities", self.required_capabilities), ("allowed_scope", self.allowed_scope), ("forbidden_changes", self.forbidden_changes)):
            if any(not str(value).strip() for value in values):
                raise TaskIntakeError(f"empty value in {name}")
        return self

    def canonical_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "intake_id": self.intake_id,
            "human_goal": self.human_goal,
            "task_type": self.task_type,
            "objective": self.objective,
            "risk": self.risk,
            "privacy": self.privacy,
            "source_ref": self.source_ref,
            "required_capabilities": list(self.required_capabilities),
            "constraints": list(self.constraints),
            "explicit_requirements": [item.canonical_payload() for item in self.explicit_requirements],
            "acceptance_criteria": list(self.acceptance_criteria),
            "evidence_requirements": list(self.evidence_requirements),
            "allowed_scope": list(self.allowed_scope),
            "forbidden_changes": list(self.forbidden_changes),
            "candidate_requirements": list(self.candidate_requirements),
            "unresolved_uncertainties": list(self.unresolved_uncertainties),
            "created_by": self.created_by,
        }

    def compute_payload_hash(self) -> str:
        encoded = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @property
    def payload_hash(self) -> str:
        return self.compute_payload_hash()

    def to_contract(self, *, task_id: str, episode_id: str) -> TaskContract:
        self.validate()
        if not task_id or not episode_id:
            raise TaskIntakeError("task_id and episode_id are required")
        return TaskContract(
            task_id=task_id,
            episode_id=episode_id,
            objective=self.objective,
            explicit_requirements=self.explicit_requirements,
            inferred_requirements=(),
            forbidden_changes=self.forbidden_changes,
            allowed_scope=self.allowed_scope,
            acceptance_criteria=self.acceptance_criteria,
            unresolved_uncertainties=self.unresolved_uncertainties + self.candidate_requirements,
            task_type=self.task_type,
            risk=self.risk,
            privacy=self.privacy,
            required_capabilities=self.required_capabilities,
            constraints=self.constraints,
            evidence_requirements=self.evidence_requirements,
            source_ref=self.source_ref,
            created_by=self.created_by,
        )
