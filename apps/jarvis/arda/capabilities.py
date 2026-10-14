"""Typed capability/provider descriptors for the Hermes governance boundary.

This first slice is intentionally in-memory. It validates canonical descriptor
identity and lifecycle invariants; SQLite persistence and routing are later
layers.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from arda.core import Database


class CapabilityRegistryError(ValueError):
    """A capability/provider descriptor violates a governance invariant."""


class CapabilityLifecycle(str, Enum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class ProviderStatus(str, Enum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    SUSPENDED = "suspended"
    RETIRED = "retired"


_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CapabilityDescriptor:
    capability_id: str
    version: str
    description: str
    input_contract: str
    output_contract: str
    evidence_required: tuple[str, ...] = ()
    policy_requirements: tuple[str, ...] = ()
    side_effect_class: str = "none"
    lifecycle: CapabilityLifecycle = CapabilityLifecycle.CANDIDATE
    namespace: str = ""
    operation: str = ""

    def __post_init__(self) -> None:
        derived_namespace, derived_operation = self.capability_id.split(".", 1) if "." in self.capability_id else ("", "")
        if not self.namespace:
            object.__setattr__(self, "namespace", derived_namespace)
        if not self.operation:
            object.__setattr__(self, "operation", derived_operation)
        self.validate()

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "namespace": self.namespace,
            "operation": self.operation,
            "description": self.description,
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "evidence_required": list(self.evidence_required),
            "policy_requirements": list(self.policy_requirements),
            "side_effect_class": self.side_effect_class,
            "lifecycle": self.lifecycle.value,
        }

    @property
    def canonical_hash(self) -> str:
        return _canonical_hash(self.canonical_payload())

    def validate(self) -> None:
        if not _ID_RE.fullmatch(self.capability_id):
            raise CapabilityRegistryError("capability_id must use lowercase namespace.operation syntax")
        namespace, operation = self.capability_id.split(".", 1)
        if self.namespace != namespace or self.operation != operation:
            raise CapabilityRegistryError("capability namespace/operation does not match capability_id")
        if not self.version or not self.description or not self.input_contract or not self.output_contract:
            raise CapabilityRegistryError("capability descriptor requires version, description and contracts")


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_id: str
    provider_kind: str
    identity: Mapping[str, str]
    capabilities: tuple[str, ...] = ()
    constraints: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_id or not self.provider_kind:
            raise CapabilityRegistryError("provider_id and provider_kind are required")
        if any(not value or "." not in value for value in self.capabilities):
            raise CapabilityRegistryError("provider capabilities must use canonical IDs")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_kind": self.provider_kind,
            "identity": dict(sorted(self.identity.items())),
            "capabilities": sorted(self.capabilities),
            "constraints": self.constraints,
        }

    @property
    def canonical_hash(self) -> str:
        return _canonical_hash(self.canonical_payload())


@dataclass(frozen=True)
class ProviderEvaluationRecord:
    evaluation_id: str
    provider_id: str
    provider_snapshot_hash: str
    capability_id: str
    capability_version: str
    fixture_set_hash: str
    contract_version: str
    accepted: bool
    metrics: Mapping[str, Any]
    evaluator_version: str

    @property
    def canonical_hash(self) -> str:
        return _canonical_hash({
            "evaluation_id": self.evaluation_id,
            "provider_id": self.provider_id,
            "provider_snapshot_hash": self.provider_snapshot_hash,
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "fixture_set_hash": self.fixture_set_hash,
            "contract_version": self.contract_version,
            "accepted": self.accepted,
            "metrics": self.metrics,
            "evaluator_version": self.evaluator_version,
        })


@dataclass(frozen=True)
class EvaluationAggregate:
    provider_id: str
    capability_id: str
    capability_version: str
    sample_count: int
    accepted_count: int
    metric_averages: Mapping[str, float]

    @property
    def accepted_rate(self) -> float:
        return self.accepted_count / self.sample_count if self.sample_count else 0.0

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "sample_count": self.sample_count,
            "accepted_count": self.accepted_count,
            "metric_averages": dict(sorted(self.metric_averages.items())),
        }

    @property
    def canonical_hash(self) -> str:
        return _canonical_hash(self.canonical_payload())


@dataclass(frozen=True)
class ProviderCapabilityMapping:
    provider_id: str
    capability_id: str
    capability_version: str
    status: ProviderStatus
    evidence_snapshot_hash: str | None = None


@dataclass(frozen=True)
class ProviderEvaluationAggregate:
    provider_id: str
    capability_id: str
    sample_count: int
    accepted_count: int
    accepted_rate: float
    metric_means: Mapping[str, float]
    metric_p95: Mapping[str, float]
    fixture_set_hashes: tuple[str, ...]
    evaluator_versions: tuple[str, ...]


class CapabilityRegistry:
    """Authoritative descriptor setup with read-only query snapshots."""

    def __init__(self) -> None:
        self._items: dict[str, CapabilityDescriptor] = {}

    def register(self, descriptor: CapabilityDescriptor) -> None:
        descriptor.validate()
        if descriptor.capability_id in self._items:
            raise CapabilityRegistryError("capability_id already registered")
        self._items[descriptor.capability_id] = descriptor

    def set_lifecycle(self, capability_id: str, lifecycle: CapabilityLifecycle) -> None:
        current = self._items.get(capability_id)
        if current is None:
            raise CapabilityRegistryError("capability not found")
        if current.lifecycle is CapabilityLifecycle.RETIRED and lifecycle is not CapabilityLifecycle.RETIRED:
            raise CapabilityRegistryError("retired capability cannot be reactivated")
        self._items[capability_id] = CapabilityDescriptor(**{
            **current.canonical_payload(),
            "lifecycle": lifecycle,
            "namespace": current.namespace,
            "operation": current.operation,
        })

    def get(self, capability_id: str) -> CapabilityDescriptor:
        try:
            return self._items[capability_id]
        except KeyError as error:
            raise CapabilityRegistryError("capability not found") from error

    def list(self) -> tuple[CapabilityDescriptor, ...]:
        return tuple(self._items[key] for key in sorted(self._items))


class ProviderRegistry:
    """Provider descriptors and evidence-backed capability mappings."""

    def __init__(self, capabilities: CapabilityRegistry | None = None) -> None:
        self._capabilities = capabilities
        self._providers: dict[str, ProviderDescriptor] = {}
        self._mappings: dict[tuple[str, str], ProviderCapabilityMapping] = {}
        self._evaluations: dict[str, ProviderEvaluationRecord] = {}

    def register(self, descriptor: ProviderDescriptor) -> None:
        if descriptor.provider_id in self._providers:
            raise CapabilityRegistryError("provider_id already registered")
        self._providers[descriptor.provider_id] = descriptor

    def record_evaluation(self, record: ProviderEvaluationRecord) -> None:
        if record.evaluation_id in self._evaluations:
            raise CapabilityRegistryError("evaluation_id already recorded")
        if record.provider_id not in self._providers:
            raise CapabilityRegistryError("evaluation provider is not registered")
        provider = self._providers[record.provider_id]
        if record.capability_id not in provider.capabilities:
            raise CapabilityRegistryError("provider has not declared this capability")
        if self._capabilities is not None:
            descriptor = self._capabilities.get(record.capability_id)
            if descriptor.version != record.capability_version:
                raise CapabilityRegistryError("evaluation capability version mismatch")
        self._evaluations[record.evaluation_id] = record

    def register_mapping(self, mapping: ProviderCapabilityMapping) -> None:
        if mapping.provider_id not in self._providers:
            raise CapabilityRegistryError("mapping provider is not registered")
        provider = self._providers[mapping.provider_id]
        if mapping.capability_id not in provider.capabilities:
            raise CapabilityRegistryError("provider has not declared this capability")
        if self._capabilities is not None:
            descriptor = self._capabilities.get(mapping.capability_id)
            if descriptor.version != mapping.capability_version:
                raise CapabilityRegistryError("mapping capability version mismatch")
        if mapping.status is ProviderStatus.VALIDATED:
            accepted = any(
                item.provider_id == mapping.provider_id
                and item.capability_id == mapping.capability_id
                and item.capability_version == mapping.capability_version
                and item.accepted
                and item.canonical_hash == mapping.evidence_snapshot_hash
                for item in self._evaluations.values()
            )
            if not accepted:
                raise CapabilityRegistryError("validated mapping requires an accepted evaluation")
        key = (mapping.provider_id, mapping.capability_id)
        current = self._mappings.get(key)
        if current and current.status is ProviderStatus.RETIRED and mapping.status is not ProviderStatus.RETIRED:
            raise CapabilityRegistryError("retired provider mapping cannot be reactivated")
        self._mappings[key] = mapping

    def get(self, provider_id: str) -> ProviderDescriptor:
        try:
            return self._providers[provider_id]
        except KeyError as error:
            raise CapabilityRegistryError("provider not found") from error

    def list_providers(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(self._providers[key] for key in sorted(self._providers))

    def get_mapping(self, provider_id: str, capability_id: str) -> ProviderCapabilityMapping:
        try:
            return self._mappings[(provider_id, capability_id)]
        except KeyError as error:
            raise CapabilityRegistryError("provider capability mapping not found") from error

    def list_mappings(self) -> tuple[ProviderCapabilityMapping, ...]:
        return tuple(self._mappings[key] for key in sorted(self._mappings))

    def list_mappings(self) -> tuple[ProviderCapabilityMapping, ...]:
        return tuple(self._mappings[key] for key in sorted(self._mappings))

    def list_evaluations(self) -> tuple[ProviderEvaluationRecord, ...]:
        return tuple(self._evaluations[key] for key in sorted(self._evaluations))


class SQLiteCapabilityStore:
    """Append-only persistence for capability/provider governance evidence."""

    def __init__(self, db: Database):
        self.db = db
        required = (
            "capability_descriptors", "provider_descriptors", "provider_evaluations",
            "provider_capability_events",
        )
        if not all(db.table_exists(name) for name in required):
            raise CapabilityRegistryError("database migrations are not current")

    def append_capability(self, descriptor: CapabilityDescriptor) -> None:
        descriptor.validate()
        try:
            self.db.execute(
                """INSERT INTO capability_descriptors(
                    capability_id, version, canonical_hash, lifecycle, descriptor_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    descriptor.capability_id, descriptor.version, descriptor.canonical_hash,
                    descriptor.lifecycle.value, _json(descriptor.canonical_payload()), _now(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise CapabilityRegistryError("capability descriptor already persisted") from error

    def append_provider(self, descriptor: ProviderDescriptor) -> None:
        try:
            self.db.execute(
                """INSERT INTO provider_descriptors(
                    provider_id, canonical_hash, descriptor_json, created_at
                ) VALUES (?, ?, ?, ?)""",
                (descriptor.provider_id, descriptor.canonical_hash, _json(descriptor.canonical_payload()), _now()),
            )
        except sqlite3.IntegrityError as error:
            raise CapabilityRegistryError("provider descriptor already persisted") from error

    def append_evaluation(self, record: ProviderEvaluationRecord) -> None:
        provider = self.db.execute(
            "SELECT descriptor_json FROM provider_descriptors WHERE provider_id=?", (record.provider_id,)
        ).fetchone()
        if provider is None:
            raise CapabilityRegistryError("evaluation provider is not registered")
        capability = self.db.execute(
            "SELECT version FROM capability_descriptors WHERE capability_id=?", (record.capability_id,)
        ).fetchone()
        if capability is None:
            raise CapabilityRegistryError("evaluation capability is not registered")
        if capability["version"] != record.capability_version:
            raise CapabilityRegistryError("evaluation capability version mismatch")
        declared = json.loads(provider["descriptor_json"]).get("capabilities", [])
        if record.capability_id not in declared:
            raise CapabilityRegistryError("provider has not declared this capability")
        try:
            self.db.execute(
                """INSERT INTO provider_evaluations(
                    evaluation_id, provider_id, capability_id, capability_version,
                    canonical_hash, evaluation_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.evaluation_id, record.provider_id, record.capability_id,
                    record.capability_version, record.canonical_hash,
                    _json(_evaluation_payload(record)), _now(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise CapabilityRegistryError("provider evaluation already persisted") from error

    def append_mapping(self, mapping: ProviderCapabilityMapping, *, reason: str, transition_id: str) -> None:
        provider = self.db.execute(
            "SELECT descriptor_json FROM provider_descriptors WHERE provider_id=?", (mapping.provider_id,)
        ).fetchone()
        if provider is None:
            raise CapabilityRegistryError("mapping provider is not registered")
        capability = self.db.execute(
            "SELECT version FROM capability_descriptors WHERE capability_id=?", (mapping.capability_id,)
        ).fetchone()
        if capability is None:
            raise CapabilityRegistryError("mapping capability is not registered")
        if capability["version"] != mapping.capability_version:
            raise CapabilityRegistryError("mapping capability version mismatch")
        declared = json.loads(provider["descriptor_json"]).get("capabilities", [])
        if mapping.capability_id not in declared:
            raise CapabilityRegistryError("provider has not declared this capability")
        if mapping.status is ProviderStatus.VALIDATED:
            accepted = self.db.execute(
                """SELECT 1 FROM provider_evaluations
                   WHERE provider_id=? AND capability_id=? AND capability_version=?
                     AND canonical_hash=? AND json_extract(evaluation_json, '$.accepted') = 1""",
                (
                    mapping.provider_id, mapping.capability_id, mapping.capability_version,
                    mapping.evidence_snapshot_hash,
                ),
            ).fetchone()
            if accepted is None:
                raise CapabilityRegistryError("validated mapping requires an accepted evaluation")
        current = self.current_mapping(mapping.provider_id, mapping.capability_id)
        if current and current.status is ProviderStatus.RETIRED and mapping.status is not ProviderStatus.RETIRED:
            raise CapabilityRegistryError("retired provider mapping cannot be reactivated")
        payload = {
            "provider_id": mapping.provider_id,
            "capability_id": mapping.capability_id,
            "capability_version": mapping.capability_version,
            "status": mapping.status.value,
            "evidence_snapshot_hash": mapping.evidence_snapshot_hash,
            "reason": reason,
        }
        try:
            self.db.execute(
                """INSERT INTO provider_capability_events(
                    transition_id, provider_id, capability_id, capability_version,
                    status, evidence_snapshot_hash, reason, transition_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    transition_id, mapping.provider_id, mapping.capability_id,
                    mapping.capability_version, mapping.status.value,
                    mapping.evidence_snapshot_hash, reason, _json(payload), _now(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise CapabilityRegistryError("provider capability transition already persisted") from error

    def current_mapping(self, provider_id: str, capability_id: str) -> ProviderCapabilityMapping | None:
        row = self.db.execute(
            """SELECT * FROM provider_capability_events
               WHERE provider_id=? AND capability_id=? ORDER BY created_at DESC, transition_id DESC LIMIT 1""",
            (provider_id, capability_id),
        ).fetchone()
        if row is None:
            return None
        return ProviderCapabilityMapping(
            provider_id=row["provider_id"], capability_id=row["capability_id"],
            capability_version=row["capability_version"], status=ProviderStatus(row["status"]),
            evidence_snapshot_hash=row["evidence_snapshot_hash"],
        )

    def list_capabilities(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self.db.execute(
            "SELECT * FROM capability_descriptors ORDER BY capability_id"
        ).fetchall())

    def list_providers(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self.db.execute(
            "SELECT * FROM provider_descriptors ORDER BY provider_id"
        ).fetchall())

    def list_evaluations(self) -> tuple[ProviderEvaluationRecord, ...]:
        rows = self.db.execute(
            "SELECT evaluation_json FROM provider_evaluations ORDER BY created_at, evaluation_id"
        ).fetchall()
        return tuple(_evaluation_from_payload(json.loads(row["evaluation_json"])) for row in rows)

    def evaluation_aggregate(
        self, provider_id: str, capability_id: str
    ) -> ProviderEvaluationAggregate:
        rows = self.db.execute(
            """SELECT evaluation_json FROM provider_evaluations
               WHERE provider_id=? AND capability_id=? ORDER BY created_at, evaluation_id""",
            (provider_id, capability_id),
        ).fetchall()
        records = [json.loads(row["evaluation_json"]) for row in rows]
        if not records:
            raise CapabilityRegistryError("no evaluations found")
        numeric: dict[str, list[float]] = {}
        for record in records:
            for key, value in record.get("metrics", {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numeric.setdefault(key, []).append(float(value))
        return ProviderEvaluationAggregate(
            provider_id=provider_id,
            capability_id=capability_id,
            sample_count=len(records),
            accepted_count=sum(bool(record.get("accepted")) for record in records),
            accepted_rate=sum(bool(record.get("accepted")) for record in records) / len(records),
            metric_means={key: sum(values) / len(values) for key, values in sorted(numeric.items())},
            metric_p95={key: _percentile(values, 0.95) for key, values in sorted(numeric.items())},
            fixture_set_hashes=tuple(sorted({record["fixture_set_hash"] for record in records})),
            evaluator_versions=tuple(sorted({record["evaluator_version"] for record in records})),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _evaluation_payload(record: ProviderEvaluationRecord) -> dict[str, Any]:
    return {
        "evaluation_id": record.evaluation_id,
        "provider_id": record.provider_id,
        "provider_snapshot_hash": record.provider_snapshot_hash,
        "capability_id": record.capability_id,
        "capability_version": record.capability_version,
        "fixture_set_hash": record.fixture_set_hash,
        "contract_version": record.contract_version,
        "accepted": record.accepted,
        "metrics": dict(record.metrics),
        "evaluator_version": record.evaluator_version,
        "canonical_hash": record.canonical_hash,
    }


def _evaluation_from_payload(payload: Mapping[str, Any]) -> ProviderEvaluationRecord:
    record = ProviderEvaluationRecord(
        evaluation_id=str(payload["evaluation_id"]),
        provider_id=str(payload["provider_id"]),
        provider_snapshot_hash=str(payload["provider_snapshot_hash"]),
        capability_id=str(payload["capability_id"]),
        capability_version=str(payload["capability_version"]),
        fixture_set_hash=str(payload["fixture_set_hash"]),
        contract_version=str(payload["contract_version"]),
        accepted=bool(payload["accepted"]),
        metrics=dict(payload.get("metrics", {})),
        evaluator_version=str(payload["evaluator_version"]),
    )
    if payload.get("canonical_hash") != record.canonical_hash:
        raise CapabilityRegistryError("provider evaluation canonical hash mismatch")
    return record


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
