"""M2.4D3 deterministic task requirement audit."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from arda.behavioral import BehavioralEvaluation
from arda.patch_worker import PatchResult
from arda.verification import StructuralEvaluation, VerificationStatus


class RequirementStatus(str, Enum):
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    UNVERIFIED = "UNVERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RequirementSource(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    CANDIDATE = "candidate"
    UNRESOLVED = "unresolved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    statement: str
    kind: str
    source: RequirementSource = RequirementSource.EXPLICIT
    target: str | None = None
    expected: str | None = None
    evidence_refs: tuple[str, ...] = ()
    confidence: float | None = None
    origin: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "statement": self.statement,
            "kind": self.kind,
            "source": self.source.value,
            "target": self.target,
            "expected": self.expected,
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class TaskContract:
    task_id: str
    episode_id: str
    objective: str
    explicit_requirements: tuple[Requirement, ...] = ()
    inferred_requirements: tuple[Requirement, ...] = ()
    forbidden_changes: tuple[str, ...] = ()
    allowed_scope: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    unresolved_uncertainties: tuple[str, ...] = ()
    task_type: str = "other"
    risk: str = "medium"
    privacy: str = "private"
    required_capabilities: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    source_ref: str = ""
    created_by: str = "SYSTEM"

    def requirements(self) -> tuple[Requirement, ...]:
        return self.explicit_requirements + self.inferred_requirements

    def hash(self) -> str:
        payload = {
            "task_id": self.task_id,
            "episode_id": self.episode_id,
            "objective": self.objective,
            "requirements": [item.canonical_payload() for item in self.requirements()],
            "forbidden_changes": list(self.forbidden_changes),
            "allowed_scope": list(self.allowed_scope),
            "acceptance_criteria": list(self.acceptance_criteria),
            "unresolved_uncertainties": list(self.unresolved_uncertainties),
            "task_type": self.task_type,
            "risk": self.risk,
            "privacy": self.privacy,
            "required_capabilities": list(self.required_capabilities),
            "constraints": list(self.constraints),
            "evidence_requirements": list(self.evidence_requirements),
            "source_ref": self.source_ref,
            "created_by": self.created_by,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RequirementEvidence:
    evidence_id: str
    kind: str
    reference: str
    value: str
    sha256: str

    @classmethod
    def create(cls, evidence_id: str, kind: str, reference: str, value: str) -> "RequirementEvidence":
        return cls(evidence_id, kind, reference, value, hashlib.sha256(value.encode("utf-8")).hexdigest())


@dataclass(frozen=True)
class RequirementEvaluation:
    requirement_id: str
    source: RequirementSource
    status: RequirementStatus
    evidence: tuple[RequirementEvidence, ...]
    episode_id: str
    task_contract_hash: str
    patch_result_id: str
    structural_evaluation_id: str
    behavioral_evaluation_id: str | None
    evaluator_version: str = "requirement-auditor-v0.1"
    failure_class: str | None = None


@dataclass(frozen=True)
class RequirementAudit:
    task_contract_hash: str
    episode_id: str
    status: RequirementStatus
    evaluations: tuple[RequirementEvaluation, ...]
    evaluator_version: str = "requirement-auditor-v0.1"


class RequirementAuditor:
    """Evaluate only typed, evidence-producing requirements.

    Free-form requirement text is preserved for provenance but is never parsed
    into a satisfied result by this deterministic slice.
    """

    def audit(
        self,
        *,
        task_contract: TaskContract,
        patch_result: PatchResult,
        structural_evaluation: StructuralEvaluation,
        behavioral_evaluation: BehavioralEvaluation | None,
        behavioral_evaluation_id: str | None = None,
        patched_workspace: str | Path,
    ) -> RequirementAudit:
        evaluations = tuple(
            self._evaluate_one(
                requirement=requirement,
                task_contract=task_contract,
                patch_result=patch_result,
                structural_evaluation=structural_evaluation,
                behavioral_evaluation=behavioral_evaluation,
                behavioral_evaluation_id=behavioral_evaluation_id,
                patched_workspace=Path(patched_workspace).resolve(),
            )
            for requirement in task_contract.requirements()
        )
        statuses = {evaluation.status for evaluation in evaluations}
        if RequirementStatus.VIOLATED in statuses:
            status = RequirementStatus.VIOLATED
        elif RequirementStatus.UNVERIFIED in statuses:
            status = RequirementStatus.UNVERIFIED
        elif evaluations and all(item.status is RequirementStatus.SATISFIED for item in evaluations):
            status = RequirementStatus.SATISFIED
        else:
            status = RequirementStatus.NOT_APPLICABLE
        return RequirementAudit(task_contract.hash(), task_contract.episode_id, status, evaluations)

    def _evaluate_one(self, *, requirement, task_contract, patch_result, structural_evaluation, behavioral_evaluation, behavioral_evaluation_id, patched_workspace):
        base = dict(
            requirement_id=requirement.requirement_id,
            source=requirement.source,
            episode_id=task_contract.episode_id,
            task_contract_hash=task_contract.hash(),
            patch_result_id=patch_result.patch_id,
            structural_evaluation_id=structural_evaluation.patch_id,
            behavioral_evaluation_id=behavioral_evaluation_id,
        )
        if requirement.source is RequirementSource.CANDIDATE:
            return RequirementEvaluation(**base, status=RequirementStatus.UNVERIFIED, evidence=(), failure_class="candidate_not_accepted")
        if requirement.source is RequirementSource.UNRESOLVED:
            return RequirementEvaluation(**base, status=RequirementStatus.UNVERIFIED, evidence=(), failure_class="requirement_unresolved")
        if requirement.source is RequirementSource.REJECTED:
            return RequirementEvaluation(**base, status=RequirementStatus.VIOLATED, evidence=(), failure_class="requirement_rejected")
        if requirement.source is RequirementSource.INFERRED and requirement.kind == "free_text":
            return RequirementEvaluation(**base, status=RequirementStatus.UNVERIFIED, evidence=(), failure_class="inferred_requirement_not_typed")
        if structural_evaluation.status is not VerificationStatus.STRUCTURAL_PASS:
            return RequirementEvaluation(**base, status=RequirementStatus.UNVERIFIED, evidence=(), failure_class="structural_gate_not_passed")
        try:
            status, evidence, failure = self._check(
                requirement, task_contract, patch_result, behavioral_evaluation, patched_workspace,
            )
        except (OSError, UnicodeError, ValueError):
            status, evidence, failure = RequirementStatus.UNVERIFIED, (), "evidence_unavailable"
        return RequirementEvaluation(**base, status=status, evidence=tuple(evidence), failure_class=failure)

    def _check(self, requirement, task_contract, patch_result, behavioral_evaluation, workspace):
        kind = requirement.kind
        if kind == "file_exists":
            path = self._safe_path(workspace, requirement.target)
            if path is None:
                return RequirementStatus.VIOLATED, (), "unsafe_target_path"
            exists = path.is_file()
            evidence = (RequirementEvidence.create("file-exists", "filesystem", requirement.target or "", str(exists)),)
            return (RequirementStatus.SATISFIED if exists else RequirementStatus.VIOLATED), evidence, None if exists else "file_missing"
        if kind in {"file_contains", "file_not_contains"}:
            path = self._safe_path(workspace, requirement.target)
            if path is None or not path.is_file() or requirement.expected is None:
                return RequirementStatus.UNVERIFIED, (), "file_evidence_unavailable"
            content = path.read_text(encoding="utf-8")
            contains = requirement.expected in content
            satisfied = contains if kind == "file_contains" else not contains
            evidence = (RequirementEvidence.create("file-content", "filesystem", requirement.target or "", hashlib.sha256(content.encode("utf-8")).hexdigest()),)
            return (RequirementStatus.SATISFIED if satisfied else RequirementStatus.VIOLATED), evidence, None if satisfied else "content_expectation_failed"
        if kind in {"allowed_scope", "no_forbidden_changes"}:
            changed = set(patch_result.touched_files)
            expected_paths = set(task_contract.allowed_scope if kind == "allowed_scope" else task_contract.forbidden_changes)
            overlap = changed & expected_paths
            if kind == "allowed_scope":
                satisfied = changed.issubset(expected_paths)
            else:
                satisfied = not overlap
            evidence = (RequirementEvidence.create("patch-scope", "patch_result", patch_result.patch_id, ",".join(sorted(changed))),)
            return (RequirementStatus.SATISFIED if satisfied else RequirementStatus.VIOLATED), evidence, None if satisfied else "scope_violation"
        if kind == "behavioral_status":
            if behavioral_evaluation is None or requirement.expected is None:
                return RequirementStatus.UNVERIFIED, (), "behavioral_evidence_missing"
            actual = behavioral_evaluation.status.value
            evidence = (RequirementEvidence.create("behavioral-status", "behavioral_evaluation", behavioral_evaluation.test_plan_hash, actual),)
            satisfied = actual == requirement.expected
            return (RequirementStatus.SATISFIED if satisfied else RequirementStatus.VIOLATED), evidence, None if satisfied else "behavioral_status_mismatch"
        if kind == "no_regression":
            if behavioral_evaluation is None:
                return RequirementStatus.UNVERIFIED, (), "behavioral_evidence_missing"
            actual = str(behavioral_evaluation.regression_count)
            evidence = (RequirementEvidence.create("regression-count", "behavioral_evaluation", behavioral_evaluation.test_plan_hash, actual),)
            satisfied = behavioral_evaluation.regression_count == 0
            return (RequirementStatus.SATISFIED if satisfied else RequirementStatus.VIOLATED), evidence, None if satisfied else "regression_detected"
        return RequirementStatus.UNVERIFIED, (), "unsupported_requirement_kind"

    @staticmethod
    def _safe_path(workspace: Path, relative: str | None) -> Path | None:
        if not relative:
            return None
        candidate = PurePosixPath(relative)
        if candidate.is_absolute() or ".." in candidate.parts or str(candidate) != relative:
            return None
        path = workspace / relative
        try:
            path.resolve().relative_to(workspace)
        except ValueError:
            return None
        return path
