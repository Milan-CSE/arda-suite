"""Deterministic, side-effect-free promotion rules for provider capabilities.

Evaluation is evidence, not approval.  This module only computes a decision
from immutable evaluation records; it never writes a mapping or changes
routing.  A separate, explicit authority operation may consume a validated
decision later.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Iterable

from arda.capabilities import ProviderEvaluationRecord
from arda.hermes_provider import HermesProviderEligibility


class PromotionStatus(str, Enum):
    CANDIDATE = "candidate"
    EVALUATED = "evaluated"
    VALIDATED = "validated"
    REJECTED = "rejected"


@dataclass(frozen=True)
class PromotionPolicy:
    """Conservative v0.1 thresholds for one capability/provider pair."""

    minimum_samples: int = 3
    minimum_accepted_rate: float = 1.0
    maximum_tool_violation_rate: float = 0.0
    maximum_timeout_rate: float = 0.0
    maximum_malformed_response_rate: float = 0.0
    minimum_fixture_pass_rate: float = 1.0
    maximum_latency_p95_ms: float | None = None
    routing_activation_enabled: bool = False

    def __post_init__(self) -> None:
        if self.minimum_samples < 1:
            raise ValueError("minimum_samples must be positive")
        for name in (
            "minimum_accepted_rate",
            "maximum_tool_violation_rate",
            "maximum_timeout_rate",
            "maximum_malformed_response_rate",
            "minimum_fixture_pass_rate",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.maximum_latency_p95_ms is not None and self.maximum_latency_p95_ms < 0:
            raise ValueError("maximum_latency_p95_ms cannot be negative")


@dataclass(frozen=True)
class PromotionDecision:
    provider_id: str
    capability_id: str
    capability_version: str
    status: PromotionStatus
    routing_eligible: bool
    sample_count: int
    accepted_count: int
    accepted_rate: float
    metric_means: dict[str, float]
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    evidence_hashes: tuple[str, ...]


def evaluate_promotion(
    *,
    provider_id: str,
    capability_id: str,
    capability_version: str,
    evaluations: Iterable[ProviderEvaluationRecord],
    metadata_eligibility: HermesProviderEligibility | None = None,
    policy: PromotionPolicy | None = None,
) -> PromotionDecision:
    """Evaluate promotion without persisting or mutating registry state.

    Records for another provider/capability/version are ignored.  A matching
    record set with too few samples remains ``evaluated`` rather than being
    treated as a failure.  Any hard contract/resource failure is ``rejected``.
    ``validated`` never implies routing eligibility unless the caller opts in
    through an explicit policy with ``routing_activation_enabled=True``.
    """
    active_policy = policy or PromotionPolicy()
    records = tuple(
        record
        for record in evaluations
        if record.provider_id == provider_id
        and record.capability_id == capability_id
        and record.capability_version == capability_version
    )
    evidence_ids = tuple(sorted(record.evaluation_id for record in records))
    evidence_hashes = tuple(sorted(record.canonical_hash for record in records))
    sample_count = len(records)
    accepted_count = sum(record.accepted for record in records)
    accepted_rate = accepted_count / sample_count if sample_count else 0.0
    metric_means = _metric_means(records)
    metric_minima = _metric_minima(records)
    metric_maxima = _metric_maxima(records)
    reasons: list[str] = []

    if metadata_eligibility is not None and not metadata_eligibility.eligible:
        reasons.extend(f"metadata:{reason}" for reason in metadata_eligibility.reasons)
    if sample_count == 0:
        reasons.append("no_matching_evaluations")
    elif sample_count < active_policy.minimum_samples:
        reasons.append("insufficient_evaluation_samples")

    if sample_count:
        _append_threshold_reason(
            reasons, "accepted_rate_below_threshold", accepted_rate,
            active_policy.minimum_accepted_rate, operator="min",
        )
        _append_threshold_reason(
            reasons, "fixture_pass_rate_below_threshold", metric_minima.get("fixture_pass_rate"),
            active_policy.minimum_fixture_pass_rate, operator="min",
        )
        _append_threshold_reason(
            reasons, "tool_violation_rate_above_threshold", metric_maxima.get("tool_violation_rate"),
            active_policy.maximum_tool_violation_rate, operator="max",
        )
        _append_threshold_reason(
            reasons, "timeout_rate_above_threshold", metric_maxima.get("timeout_rate"),
            active_policy.maximum_timeout_rate, operator="max",
        )
        _append_threshold_reason(
            reasons, "malformed_response_rate_above_threshold", metric_maxima.get("malformed_response_rate"),
            active_policy.maximum_malformed_response_rate, operator="max",
        )
        if active_policy.maximum_latency_p95_ms is not None:
            _append_threshold_reason(
                reasons, "latency_p95_above_threshold", metric_maxima.get("latency_p95_ms"),
                active_policy.maximum_latency_p95_ms, operator="max",
            )

    hard_failure_reasons = tuple(
        reason for reason in reasons
        if reason not in {"insufficient_evaluation_samples", "no_matching_evaluations"}
    )
    if hard_failure_reasons:
        status = PromotionStatus.REJECTED
    elif sample_count >= active_policy.minimum_samples:
        status = PromotionStatus.VALIDATED
    else:
        status = PromotionStatus.CANDIDATE if sample_count == 0 else PromotionStatus.EVALUATED

    routing_eligible = (
        status is PromotionStatus.VALIDATED
        and active_policy.routing_activation_enabled
        and not reasons
    )
    return PromotionDecision(
        provider_id=provider_id,
        capability_id=capability_id,
        capability_version=capability_version,
        status=status,
        routing_eligible=routing_eligible,
        sample_count=sample_count,
        accepted_count=accepted_count,
        accepted_rate=accepted_rate,
        metric_means=metric_means,
        reasons=tuple(reasons),
        evidence_ids=evidence_ids,
        evidence_hashes=evidence_hashes,
    )


def _metric_means(records: tuple[ProviderEvaluationRecord, ...]) -> dict[str, float]:
    values = _metric_values(records)
    return {key: sum(items) / len(items) for key, items in sorted(values.items())}


def _metric_minima(records: tuple[ProviderEvaluationRecord, ...]) -> dict[str, float]:
    return {key: min(items) for key, items in _metric_values(records).items()}


def _metric_maxima(records: tuple[ProviderEvaluationRecord, ...]) -> dict[str, float]:
    return {key: max(items) for key, items in _metric_values(records).items()}


def _metric_values(records: tuple[ProviderEvaluationRecord, ...]) -> dict[str, list[float]]:
    values: dict[str, list[float]] = {}
    for record in records:
        for key, value in record.metrics.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
                values.setdefault(key, []).append(float(value))
    return values


def _append_threshold_reason(
    reasons: list[str],
    reason: str,
    observed: float | None,
    threshold: float,
    *,
    operator: str,
) -> None:
    if observed is None:
        reasons.append(f"missing_metric:{reason}")
    elif operator == "min" and observed < threshold:
        reasons.append(reason)
    elif operator == "max" and observed > threshold:
        reasons.append(reason)
