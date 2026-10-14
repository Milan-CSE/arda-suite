"""Deterministic eligibility checks for Nous Hermes provider candidates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from arda.capabilities import CapabilityDescriptor, CapabilityLifecycle, ProviderDescriptor


HERMES_AGENT_LOOP_CAPABILITY = CapabilityDescriptor(
    capability_id="agent.hermes_loop",
    version="0.1",
    description="Provider can sustain the Nous Hermes tool-using agent loop.",
    input_contract="NousHermesProviderRequest-v0.1",
    output_contract="ProviderResult-v0.1",
    evidence_required=("context_window", "tool_calling", "structured_messages", "provenance"),
    policy_requirements=("provider.invoke",),
    side_effect_class="provider-boundary",
    lifecycle=CapabilityLifecycle.CANDIDATE,
)


@dataclass(frozen=True)
class HermesProviderEligibility:
    provider_id: str
    capability_id: str
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_hermes_provider(
    provider: ProviderDescriptor,
    *,
    minimum_context_window: int = 64000,
) -> HermesProviderEligibility:
    """Evaluate metadata only; no provider or network call is performed."""
    reasons: list[str] = []
    context_window = provider.constraints.get("context_window")
    if not isinstance(context_window, int) or context_window < minimum_context_window:
        reasons.append("context_window_below_hermes_minimum")
    if provider.constraints.get("tool_calling") is not True:
        reasons.append("tool_calling_not_proven")
    if provider.constraints.get("structured_messages") is not True:
        reasons.append("structured_messages_not_proven")
    if provider.constraints.get("provenance") is not True:
        reasons.append("provenance_not_proven")
    return HermesProviderEligibility(
        provider_id=provider.provider_id,
        capability_id=HERMES_AGENT_LOOP_CAPABILITY.capability_id,
        eligible=not reasons,
        reasons=tuple(reasons),
    )

