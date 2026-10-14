"""Deterministic, proposal-only routing over validated capability evidence."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from arda.capabilities import (
    EvaluationAggregate,
    ProviderCapabilityMapping,
    ProviderDescriptor,
    ProviderEvaluationRecord,
    ProviderStatus,
)


class RoutingError(ValueError):
    """A route request or proposal violates the deterministic routing contract."""


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class EvaluationMemory:
    records: tuple[ProviderEvaluationRecord, ...] = ()

    @classmethod
    def from_records(cls, records: tuple[ProviderEvaluationRecord, ...] | list[ProviderEvaluationRecord]):
        return cls(tuple(records))

    def aggregate(self, provider_id: str, capability_id: str) -> EvaluationAggregate | None:
        records = tuple(
            record for record in self.records
            if record.provider_id == provider_id and record.capability_id == capability_id
        )
        if not records:
            return None
        versions = {record.capability_version for record in records}
        if len(versions) != 1:
            raise RoutingError("evaluation memory contains multiple capability versions")
        numeric: dict[str, list[float]] = {}
        for record in records:
            for key, value in record.metrics.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numeric.setdefault(key, []).append(float(value))
        averages = {key: sum(values) / len(values) for key, values in numeric.items()}
        return EvaluationAggregate(
            provider_id=provider_id,
            capability_id=capability_id,
            capability_version=records[0].capability_version,
            sample_count=len(records),
            accepted_count=sum(record.accepted for record in records),
            metric_averages=averages,
        )

    def snapshot_hash(self) -> str:
        return _hash([record.canonical_hash for record in self.records])


@dataclass(frozen=True)
class RouteRequest:
    episode_id: str
    task_contract_revision_id: str
    task_contract_hash: str
    required_capabilities: tuple[str, ...]
    privacy: str = "private"
    max_latency_ms: float | None = None
    min_context_window: int | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.episode_id or not self.task_contract_revision_id or not self.task_contract_hash:
            raise RoutingError("route request identity is required")
        if not self.required_capabilities:
            raise RoutingError("at least one required capability is needed")
        if self.max_latency_ms is not None and self.max_latency_ms <= 0:
            raise RoutingError("max_latency_ms must be positive")
        if self.min_context_window is not None and self.min_context_window <= 0:
            raise RoutingError("min_context_window must be positive")


@dataclass(frozen=True)
class RouteCandidate:
    provider_id: str
    matched_capabilities: tuple[str, ...]
    reasons: tuple[str, ...]
    disqualifiers: tuple[str, ...] = ()
    rank: int = 0


@dataclass(frozen=True)
class RouteProposal:
    proposal_id: str
    episode_id: str
    task_contract_revision_id: str
    task_contract_hash: str
    candidates: tuple[RouteCandidate, ...]
    registry_snapshot_hash: str
    evaluation_snapshot_hash: str
    expires_at: str
    canonical_hash: str

    @classmethod
    def create(
        cls,
        *,
        proposal_id: str,
        request: RouteRequest,
        candidates: tuple[RouteCandidate, ...],
        registry_snapshot_hash: str,
        evaluation_snapshot_hash: str,
        expires_at: str,
    ) -> "RouteProposal":
        payload = {
            "proposal_id": proposal_id,
            "episode_id": request.episode_id,
            "task_contract_revision_id": request.task_contract_revision_id,
            "task_contract_hash": request.task_contract_hash,
            "candidates": [candidate.__dict__ for candidate in candidates],
            "registry_snapshot_hash": registry_snapshot_hash,
            "evaluation_snapshot_hash": evaluation_snapshot_hash,
            "expires_at": expires_at,
        }
        return cls(
            proposal_id=proposal_id,
            episode_id=request.episode_id,
            task_contract_revision_id=request.task_contract_revision_id,
            task_contract_hash=request.task_contract_hash,
            candidates=tuple(candidates),
            registry_snapshot_hash=registry_snapshot_hash,
            evaluation_snapshot_hash=evaluation_snapshot_hash,
            expires_at=expires_at,
            canonical_hash=_hash(payload),
        )

    def verify_hash(self) -> bool:
        payload = {
            "proposal_id": self.proposal_id,
            "episode_id": self.episode_id,
            "task_contract_revision_id": self.task_contract_revision_id,
            "task_contract_hash": self.task_contract_hash,
            "candidates": [candidate.__dict__ for candidate in self.candidates],
            "registry_snapshot_hash": self.registry_snapshot_hash,
            "evaluation_snapshot_hash": self.evaluation_snapshot_hash,
            "expires_at": self.expires_at,
        }
        return self.canonical_hash == _hash(payload)


class DeterministicRouter:
    """Rank only validated, capability-complete, policy-compatible providers."""

    def propose(
        self,
        request: RouteRequest,
        *,
        providers: tuple[ProviderDescriptor, ...],
        mappings: tuple[ProviderCapabilityMapping, ...],
        memory: EvaluationMemory,
        proposal_id: str,
        expires_at: str,
    ) -> RouteProposal:
        request.validate()
        mapping_by_key = {(item.provider_id, item.capability_id): item for item in mappings}
        candidates: list[tuple[tuple[Any, ...], RouteCandidate]] = []
        for provider in providers:
            disqualifiers: list[str] = []
            matched: list[str] = []
            aggregates: list[EvaluationAggregate] = []
            for capability_id in request.required_capabilities:
                mapping = mapping_by_key.get((provider.provider_id, capability_id))
                if mapping is None or mapping.status is not ProviderStatus.VALIDATED:
                    disqualifiers.append(f"missing validated capability: {capability_id}")
                    continue
                aggregate = memory.aggregate(provider.provider_id, capability_id)
                if aggregate is None:
                    disqualifiers.append(f"missing evaluation evidence: {capability_id}")
                    continue
                matched.append(capability_id)
                aggregates.append(aggregate)
            if request.privacy == "private" and provider.constraints.get("local_only") is not True:
                disqualifiers.append("private task requires local-only provider")
            latency = [item.metric_averages.get("latency_p95_ms") for item in aggregates if "latency_p95_ms" in item.metric_averages]
            if request.max_latency_ms is not None and latency and max(latency) > request.max_latency_ms:
                disqualifiers.append("p95 latency exceeds task limit")
            if request.min_context_window is not None:
                context_window = provider.constraints.get("context_window")
                if not isinstance(context_window, int) or context_window < request.min_context_window:
                    disqualifiers.append("provider context window is below task minimum")
            reasons = (
                f"validated capability coverage: {len(matched)}/{len(request.required_capabilities)}",
                f"accepted evidence rate: {sum(item.accepted_rate for item in aggregates) / len(aggregates):.3f}" if aggregates else "no accepted evidence",
            )
            candidate = RouteCandidate(provider.provider_id, tuple(sorted(matched)), reasons, tuple(sorted(disqualifiers)))
            if not candidate.disqualifiers:
                accepted_rate = sum(item.accepted_rate for item in aggregates) / len(aggregates)
                first_pass = sum(item.metric_averages.get("first_pass_rate", 0.0) for item in aggregates) / len(aggregates)
                p95 = sum(item.metric_averages.get("latency_p95_ms", float("inf")) for item in aggregates) / len(aggregates)
                candidates.append(((-accepted_rate, -first_pass, p95, provider.provider_id), candidate))
        candidates.sort(key=lambda item: item[0])
        ranked = tuple(RouteCandidate(**{**candidate.__dict__, "rank": index}) for index, (_, candidate) in enumerate(candidates, start=1))
        registry_hash = _hash([provider.canonical_hash for provider in sorted(providers, key=lambda item: item.provider_id)] + [mapping.__dict__ for mapping in sorted(mappings, key=lambda item: (item.provider_id, item.capability_id))])
        return RouteProposal.create(
            proposal_id=proposal_id,
            request=request,
            candidates=ranked,
            registry_snapshot_hash=registry_hash,
            evaluation_snapshot_hash=memory.snapshot_hash(),
            expires_at=expires_at,
        )
