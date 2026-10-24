"""Accuracy-first plan contracts and pure selection; no runtime/ML dependencies."""

from typing import Literal, TypedDict

from pydantic import Field

from .capabilities import CapabilityKey, Qualification
from .schemas import Contract


class Component(Contract):
    id: str
    version_hash: str
    qualification: Qualification
    tier: Literal["T0", "T1", "T2"]
    worker_versions: list[str]
    quality_order: int = Field(ge=0)  # independently reviewed policy, never speed order
    memory_mb: int | None = Field(default=None, gt=0)
    measured_profile: dict | None = None


class ExecutionPlan(Contract):
    state: Literal["ready", "queued", "unsupported", "needs_intervention"]
    quality_policy: Literal["accuracy_first"] = "accuracy_first"
    component_id: str | None = None
    component_hash: str | None = None
    tier: Literal["T0", "T1", "T2"] | None = None
    reason: str
    estimated_seconds: float | None = None
    estimate_confidence: Literal["matching_measurement", "insufficient_data"] = "insufficient_data"


class PlanSelection(TypedDict):
    component_id: str
    component_hash: str
    tier: Literal["T0", "T1", "T2"]


def plan(
    key: CapabilityKey,
    components: list[Component],
    *,
    worker_version,
    memory_mb,
    hardware_id,
    quota_available,
    degradation_level=0,
    experimental_opt_in=False,
):
    matching = [c for c in components if c.qualification.key == key]
    eligible = [
        c
        for c in matching
        if c.qualification.state == "production_validated"
        or (c.qualification.state == "experimental_opt_in" and experimental_opt_in)
    ]
    if not eligible:
        return ExecutionPlan(
            state="needs_intervention" if matching else "unsupported", reason="no_qualified_method"
        )
    # Quality selection happens BEFORE resource checks; scarce capacity cannot
    # select a less qualified alternative. Ties use stable version identity.
    chosen = sorted(eligible, key=lambda c: (-c.quality_order, c.id, c.version_hash))[0]
    base: PlanSelection = dict(
        component_id=chosen.id, component_hash=chosen.version_hash, tier=chosen.tier
    )
    if (
        worker_version not in chosen.worker_versions
        or chosen.memory_mb is None
        or chosen.memory_mb > memory_mb
    ):
        return ExecutionPlan(**base, state="queued", reason="compatible_capacity_required")
    if (
        not quota_available
        or degradation_level >= 3
        or (degradation_level >= 1 and chosen.tier != "T0")
    ):
        return ExecutionPlan(**base, state="queued", reason="capacity_or_quota_unavailable")
    profile = chosen.measured_profile
    identity = dict(
        key=key.model_dump(mode="json"),
        hardware_id=hardware_id,
        worker_version=worker_version,
        component_hash=chosen.version_hash,
    )
    if (
        profile
        and profile.get("identity") == identity
        and profile.get("benchmark_hash")
        and type(profile.get("seconds")) in (int, float)
        and profile["seconds"] > 0
    ):
        return ExecutionPlan(
            **base,
            state="ready",
            reason="qualified_method",
            estimated_seconds=profile["seconds"],
            estimate_confidence="matching_measurement",
        )
    return ExecutionPlan(**base, state="ready", reason="qualified_method")


def structured_route(source_type):
    """Existing T0 path does not claim recognition or general segmentation."""
    if source_type in {"template_scan", "template_photo", "canvas_drawing"}:
        return {
            "policy_version": "accuracy-first-v1",
            "tier": "T0",
            "method": "structured_geometry",
            "recognition": "disabled",
            "estimated_seconds": None,
            "quality_policy": "accuracy_first",
        }
    return {
        "policy_version": "accuracy-first-v1",
        "tier": "T0",
        "method": "isolated_recapture",
        "recognition": "research_only",
        "estimated_seconds": None,
        "quality_policy": "accuracy_first",
    }
