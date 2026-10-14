"""Core-owned verification bridge for materialized provider output."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from arda.providers import ProviderOutputArtifact, ProviderResult, SQLiteProviderOutputStore


class ProviderVerificationError(ValueError):
    """A provider artifact cannot be verified under the declared identity."""


class ProviderVerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class ProviderVerificationRequest:
    episode_id: str
    provider_result_id: str
    artifact_id: str
    verification_type: str
    verifier_identity: str
    policy_snapshot_hash: str

    def validate(self) -> None:
        if not all((
            self.episode_id,
            self.provider_result_id,
            self.artifact_id,
            self.verification_type,
            self.verifier_identity,
            self.policy_snapshot_hash,
        )):
            raise ProviderVerificationError("verification request identity is required")


@dataclass(frozen=True)
class ProviderVerificationResult:
    episode_id: str
    provider_result_id: str
    artifact_id: str
    status: ProviderVerificationStatus
    evidence_refs: tuple[str, ...]
    verifier_identity: str
    verifier_version: str
    input_hash: str
    output_hash: str
    canonical_hash: str

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "provider_result_id": self.provider_result_id,
            "artifact_id": self.artifact_id,
            "status": self.status.value,
            "evidence_refs": list(self.evidence_refs),
            "verifier_identity": self.verifier_identity,
            "verifier_version": self.verifier_version,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
        }

    def verify_hash(self) -> bool:
        encoded = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return self.canonical_hash == hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ProviderVerificationBridge:
    """Load immutable provider output and delegate interpretation to a verifier."""

    def __init__(self, artifact_store: SQLiteProviderOutputStore):
        self.artifact_store = artifact_store

    def verify(
        self,
        request: ProviderVerificationRequest,
        provider_result: ProviderResult,
        verifier: Callable[[ProviderOutputArtifact], tuple[ProviderVerificationStatus, tuple[str, ...], str]],
    ) -> ProviderVerificationResult:
        request.validate()
        if provider_result.request_id != request.provider_result_id:
            raise ProviderVerificationError("provider result identity mismatch")
        artifact = self.artifact_store.get(request.artifact_id)
        if artifact.episode_id != request.episode_id or artifact.request_id != request.provider_result_id:
            raise ProviderVerificationError("provider artifact provenance mismatch")
        if provider_result.output_hash != artifact.output_hash:
            raise ProviderVerificationError("provider result output hash mismatch")
        status, evidence_refs, verifier_version = verifier(artifact)
        if not isinstance(status, ProviderVerificationStatus):
            raise ProviderVerificationError("verifier returned an invalid status")
        evidence_refs = tuple(evidence_refs)
        payload = {
            "episode_id": request.episode_id,
            "provider_result_id": request.provider_result_id,
            "artifact_id": request.artifact_id,
            "status": status.value,
            "evidence_refs": list(evidence_refs),
            "verifier_identity": request.verifier_identity,
            "verifier_version": verifier_version,
            "input_hash": artifact.output_hash,
            "output_hash": artifact.output_hash,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return ProviderVerificationResult(
            episode_id=request.episode_id,
            provider_result_id=request.provider_result_id,
            artifact_id=request.artifact_id,
            status=status,
            evidence_refs=evidence_refs,
            verifier_identity=request.verifier_identity,
            verifier_version=verifier_version,
            input_hash=artifact.output_hash,
            output_hash=artifact.output_hash,
            canonical_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        )
