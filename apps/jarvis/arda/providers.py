"""Provider boundary contract and deterministic fake adapter.

This module deliberately stops at an untrusted ProviderResult. Verification and
publication remain Core responsibilities.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol

from arda.research_synthesis import (
    ModelOutputError,
    ModelProviderError,
    OllamaNativeGateway,
)
from arda.route_decision import RouteDecision, RouteDecisionStatus


class ProviderBoundaryError(ValueError):
    """A provider request/result violates the Core adapter boundary."""


class ProviderResultStatus(str, Enum):
    RETURNED = "returned"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    RATE_LIMITED = "rate_limited"
    REJECTED = "rejected"


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ProviderRequest:
    request_id: str
    provider_id: str
    episode_id: str
    capability_id: str
    task_contract_hash: str
    route_proposal_id: str
    route_decision_id: str
    route_decision_hash: str
    policy_snapshot_hash: str
    input_manifest: tuple[Mapping[str, str], ...] = ()
    allowed_outputs: tuple[str, ...] = ()
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not self.request_id or not self.provider_id or not self.episode_id:
            raise ProviderBoundaryError("provider request identity is required")
        if not self.capability_id or "." not in self.capability_id:
            raise ProviderBoundaryError("provider request capability_id must be canonical")
        if self.timeout_seconds <= 0:
            raise ProviderBoundaryError("provider request timeout must be positive")
        for item in self.input_manifest:
            if not item.get("ref") or not item.get("sha256"):
                raise ProviderBoundaryError("input manifest entries require ref and sha256")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "provider_id": self.provider_id,
            "episode_id": self.episode_id,
            "capability_id": self.capability_id,
            "task_contract_hash": self.task_contract_hash,
            "route_proposal_id": self.route_proposal_id,
            "route_decision_id": self.route_decision_id,
            "route_decision_hash": self.route_decision_hash,
            "policy_snapshot_hash": self.policy_snapshot_hash,
            "input_manifest": [dict(sorted(item.items())) for item in self.input_manifest],
            "allowed_outputs": list(self.allowed_outputs),
            "timeout_seconds": self.timeout_seconds,
        }

    @property
    def request_hash(self) -> str:
        return _hash(self.canonical_payload())


@dataclass(frozen=True)
class ProviderResult:
    request_id: str
    provider_id: str
    request_hash: str
    status: ProviderResultStatus
    output_ref: str | None
    output_hash: str | None
    latency_ms: int | None
    token_usage: Mapping[str, int] = field(default_factory=dict)
    provenance: Mapping[str, str] = field(default_factory=dict)
    error_class: str | None = None

    def validate_for(self, request: ProviderRequest) -> None:
        if self.request_id != request.request_id:
            raise ProviderBoundaryError("provider result request_id mismatch")
        if self.provider_id != request.provider_id:
            raise ProviderBoundaryError("provider result provider_id mismatch")
        if self.request_hash != request.request_hash:
            raise ProviderBoundaryError("provider result request_hash mismatch")
        if self.status is ProviderResultStatus.RETURNED and not self.output_hash:
            raise ProviderBoundaryError("returned provider result requires output_hash")


@dataclass(frozen=True)
class ProviderOutputArtifact:
    artifact_id: str
    episode_id: str
    request_id: str
    provider_id: str
    model_snapshot: str
    request_hash: str
    output_hash: str
    content: bytes
    content_type: str
    created_at: str


class SQLiteProviderOutputStore:
    """Append-only materialization for raw provider output."""

    def __init__(self, db: Any):
        self.db = db
        if not db.table_exists("provider_output_artifacts"):
            raise ProviderBoundaryError("provider output artifact migration is not current")

    def materialize(
        self,
        request: ProviderRequest,
        result: ProviderResult,
        content: bytes,
        *,
        model_snapshot: str,
        content_type: str = "text/plain; charset=utf-8",
    ) -> ProviderOutputArtifact:
        result.validate_for(request)
        if result.status is not ProviderResultStatus.RETURNED:
            raise ProviderBoundaryError("only returned provider results can be materialized")
        if not model_snapshot or not content_type:
            raise ProviderBoundaryError("model_snapshot and content_type are required")
        output_hash = hashlib.sha256(content).hexdigest()
        if output_hash != result.output_hash:
            raise ProviderBoundaryError("provider output content hash mismatch")
        existing = self.db.execute(
            "SELECT * FROM provider_output_artifacts WHERE request_id=?", (request.request_id,)
        ).fetchone()
        if existing is not None:
            if existing["output_hash"] != output_hash or existing["request_hash"] != request.request_hash:
                raise ProviderBoundaryError("provider output identity conflict")
            return self._from_row(existing)
        artifact_id = f"provider_art_{request.request_id}"
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            self.db.execute(
                """INSERT INTO provider_output_artifacts(
                    artifact_id, episode_id, request_id, provider_id, model_snapshot,
                    request_hash, output_hash, content, content_type, size_bytes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact_id, request.episode_id, request.request_id, request.provider_id,
                    model_snapshot, request.request_hash, output_hash, content, content_type,
                    len(content), created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ProviderBoundaryError("provider output artifact identity conflict") from exc
        return ProviderOutputArtifact(
            artifact_id, request.episode_id, request.request_id, request.provider_id,
            model_snapshot, request.request_hash, output_hash, content, content_type, created_at,
        )

    def get(self, artifact_id: str) -> ProviderOutputArtifact:
        row = self.db.execute(
            "SELECT * FROM provider_output_artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise ProviderBoundaryError("provider output artifact not found")
        content = bytes(row["content"])
        if hashlib.sha256(content).hexdigest() != row["output_hash"]:
            raise ProviderBoundaryError("provider output artifact hash mismatch")
        return self._from_row(row)

    def get_by_request_id(self, request_id: str) -> ProviderOutputArtifact | None:
        row = self.db.execute(
            "SELECT * FROM provider_output_artifacts WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        artifact = self._from_row(row)
        if hashlib.sha256(artifact.content).hexdigest() != artifact.output_hash:
            raise ProviderBoundaryError("provider output artifact hash mismatch")
        return artifact

    @staticmethod
    def _from_row(row: Any) -> ProviderOutputArtifact:
        return ProviderOutputArtifact(
            row["artifact_id"], row["episode_id"], row["request_id"], row["provider_id"],
            row["model_snapshot"], row["request_hash"], row["output_hash"], bytes(row["content"]),
            row["content_type"], row["created_at"],
        )


class ProviderAdapter(Protocol):
    provider_id: str

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        ...


class ProviderBoundary:
    """Invoke an adapter only after Core has produced an ALLOW decision."""

    def invoke(self, decision: RouteDecision, request: ProviderRequest, adapter: ProviderAdapter) -> ProviderResult:
        if decision.status is not RouteDecisionStatus.ALLOW:
            raise ProviderBoundaryError("provider invocation requires an ALLOW route decision")
        if not decision.verify_hash():
            raise ProviderBoundaryError("route decision hash mismatch")
        if request.provider_id != decision.provider_id:
            raise ProviderBoundaryError("provider request does not match route decision")
        if request.route_proposal_id != decision.proposal_id:
            raise ProviderBoundaryError("provider request proposal mismatch")
        if request.task_contract_hash != decision.task_contract_hash:
            raise ProviderBoundaryError("provider request task contract mismatch")
        if request.route_decision_id != decision.decision_id:
            raise ProviderBoundaryError("provider request decision mismatch")
        if request.route_decision_hash != decision.canonical_hash:
            raise ProviderBoundaryError("provider request decision hash mismatch")
        if request.policy_snapshot_hash != decision.policy_snapshot_hash:
            raise ProviderBoundaryError("provider request policy snapshot mismatch")
        if adapter.provider_id != request.provider_id:
            raise ProviderBoundaryError("adapter provider_id mismatch")
        result = adapter.invoke(request)
        result.validate_for(request)
        return result


class FakeProviderAdapter:
    """Deterministic failure-matrix adapter; never represents a verified outcome."""

    def __init__(
        self,
        provider_id: str,
        *,
        status: ProviderResultStatus = ProviderResultStatus.RETURNED,
        output: str = "fixture-provider-output",
        error_class: str | None = None,
    ):
        self.provider_id = provider_id
        self.status = status
        self.output = output
        self.error_class = error_class
        self.calls = 0

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        self.calls += 1
        output_hash = hashlib.sha256(self.output.encode("utf-8")).hexdigest() if self.status is ProviderResultStatus.RETURNED else None
        return ProviderResult(
            request_id=request.request_id,
            provider_id=self.provider_id,
            request_hash=request.request_hash,
            status=self.status,
            output_ref="fixture://provider-output" if output_hash else None,
            output_hash=output_hash,
            latency_ms=1,
            provenance={"adapter": "fake-provider-v0.1"},
            error_class=self.error_class,
        )


class OllamaProviderAdapter:
    """Translate a Core request to the existing Ollama ModelGateway.

    The adapter resolves only manifest references supplied by Core. It does not
    select models, retry, route around policy, or turn the response into a
    verified outcome.
    """

    def __init__(
        self,
        provider_id: str,
        *,
        model: str,
        gateway: OllamaNativeGateway,
        input_loader: Any,
        output_store: Any = None,
        response_schema: Mapping[str, Any] | None = None,
        max_output_tokens: int = 1024,
    ):
        if not provider_id or not model or not callable(input_loader):
            raise ValueError("provider_id, model, and input_loader are required")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        self.provider_id = provider_id
        self.model = model
        self.gateway = gateway
        self.input_loader = input_loader
        self.output_store = output_store
        self.response_schema = response_schema
        self.max_output_tokens = max_output_tokens

    def invoke(self, request: ProviderRequest) -> ProviderResult:
        if self.output_store is not None and hasattr(self.output_store, "get_by_request_id"):
            cached = self.output_store.get_by_request_id(request.request_id)
            if cached is not None:
                return ProviderResult(
                    request_id=request.request_id,
                    provider_id=self.provider_id,
                    request_hash=request.request_hash,
                    status=ProviderResultStatus.RETURNED,
                    output_ref=f"provider-artifact://{cached.artifact_id}",
                    output_hash=cached.output_hash,
                    latency_ms=0,
                    provenance={"adapter": "ollama-native-v0.1", "model": self.model, "replay": "true"},
                )
        prompts: dict[str, str] = {}
        for item in request.input_manifest:
            role = item.get("role")
            if role not in {"system", "user"}:
                raise ProviderBoundaryError("Ollama input manifest requires system/user roles")
            if role in prompts:
                raise ProviderBoundaryError("Ollama input manifest has duplicate prompt role")
            content = self.input_loader(item["ref"])
            if not isinstance(content, str):
                raise ProviderBoundaryError("Ollama input loader must return text")
            prompts[role] = content
        if set(prompts) != {"system", "user"}:
            raise ProviderBoundaryError("Ollama input manifest requires system and user prompts")

        started = time.monotonic()
        try:
            response = self.gateway.generate(
                system_prompt=prompts["system"],
                user_prompt=prompts["user"],
                model=self.model,
                max_output_tokens=self.max_output_tokens,
                timeout_seconds=request.timeout_seconds,
                response_schema=self.response_schema,
                temperature=0.0,
            )
        except ModelOutputError as exc:
            return self._failure(request, ProviderResultStatus.MALFORMED, "model_output_error", started)
        except ModelProviderError as exc:
            return self._failure(request, ProviderResultStatus.UNAVAILABLE, "model_provider_error", started)
        except TimeoutError as exc:
            return self._failure(request, ProviderResultStatus.TIMEOUT, "timeout", started)

        content = getattr(response, "content", None)
        if not isinstance(content, str):
            return self._failure(request, ProviderResultStatus.MALFORMED, "missing_text_content", started)
        output_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        input_tokens = getattr(response, "input_tokens", None)
        output_tokens = getattr(response, "output_tokens", None)
        usage = {}
        if input_tokens is not None:
            usage["input_tokens"] = int(input_tokens)
        if output_tokens is not None:
            usage["output_tokens"] = int(output_tokens)
        provider_request_id = getattr(response, "provider_request_id", None)
        output_ref = f"ollama://{provider_request_id or request.request_id}"
        if self.output_store is not None:
            artifact = self.output_store.materialize(
                request,
                ProviderResult(
                    request_id=request.request_id,
                    provider_id=self.provider_id,
                    request_hash=request.request_hash,
                    status=ProviderResultStatus.RETURNED,
                    output_ref=output_ref,
                    output_hash=output_hash,
                    latency_ms=round((time.monotonic() - started) * 1000),
                    provenance={"adapter": "ollama-native-v0.1", "model": self.model},
                ),
                content.encode("utf-8"),
                model_snapshot=self.model,
            )
            output_ref = f"provider-artifact://{artifact.artifact_id}"
        return ProviderResult(
            request_id=request.request_id,
            provider_id=self.provider_id,
            request_hash=request.request_hash,
            status=ProviderResultStatus.RETURNED,
            output_ref=output_ref,
            output_hash=output_hash,
            latency_ms=round((time.monotonic() - started) * 1000),
            token_usage=usage,
            provenance={"adapter": "ollama-native-v0.1", "model": self.model},
        )

    def _failure(self, request: ProviderRequest, status: ProviderResultStatus, error_class: str, started: float) -> ProviderResult:
        return ProviderResult(
            request_id=request.request_id,
            provider_id=self.provider_id,
            request_hash=request.request_hash,
            status=status,
            output_ref=None,
            output_hash=None,
            latency_ms=round((time.monotonic() - started) * 1000),
            provenance={"adapter": "ollama-native-v0.1", "model": self.model},
            error_class=error_class,
        )
