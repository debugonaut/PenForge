"""Explicit review contracts; recommendation never grants approval."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from penforge_contracts import GlyphOutline
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SelectCandidate(ReviewSchema):
    candidate_id: UUID
    base_revision_id: UUID | None


class GlyphPatch(ReviewSchema):
    base_revision_id: UUID | None
    action: Literal["edit", "approve", "reject", "rollback"]
    outline: GlyphOutline | None = None
    metrics: dict[str, float] | None = None
    target_revision_id: UUID | None = None

    @model_validator(mode="after")
    def action_fields(self):
        if self.action == "edit" and self.outline is None:
            raise ValueError("edit_requires_canonical_outline")
        if self.action != "edit" and (self.outline is not None or self.metrics is not None):
            raise ValueError("outline_only_supported_for_edit")
        if (self.action == "rollback") != (self.target_revision_id is not None):
            raise ValueError("rollback_requires_target")
        return self


class SpaceMetric(ReviewSchema):
    advance_width: float = Field(strict=True, gt=0, le=8000, allow_inf_nan=False)


class SnapshotCreate(ReviewSchema):
    kind: Literal["milestone"] = "milestone"
    label: str | None = Field(default=None, max_length=120)


class StyleProfileCreate(ReviewSchema):
    snapshot_id: UUID


class AlternateCreate(ReviewSchema):
    source_revision_id: UUID
    base_revision_id: UUID | None


class AlternateToggle(ReviewSchema):
    enabled: bool = Field(strict=True)


class StyleProfileResponse(ReviewSchema):
    id: UUID
    project_id: UUID
    version: int
    source_snapshot_id: UUID
    profile_hash: str
    algorithm_version: str
    metrics: dict[str, Any]
    created_at: datetime


class ProcessingCapabilityResponse(ReviewSchema):
    task: str
    script: str
    style: str
    input_class: str
    state: Literal["production_validated", "experimental_opt_in", "research_only", "disabled"]
    reason: str


class CapabilitiesResponse(ReviewSchema):
    items: list[ProcessingCapabilityResponse]


class RevisionResponse(ReviewSchema):
    id: UUID
    seq: int
    parent_revision_id: UUID | None
    source_candidate_id: UUID | None
    source_sample_id: UUID | None
    alternate_source_revision_id: UUID | None = None
    alternate_source_snapshot_id: UUID | None = None
    origin: str
    approval: Literal["pending", "approved", "rejected"]
    content_hash: str
    created_at: datetime


class GlyphResponse(ReviewSchema):
    id: UUID
    project_id: UUID
    codepoints: list[int]
    variant_index: int
    row_version: int
    coverage_status: str
    active_revision_id: UUID | None
    working_revision_id: UUID | None
    recommended_candidate_id: UUID | None
    outline: GlyphOutline | None
    outline_edit_supported: bool
    active_outline: GlyphOutline | None
    revisions: list[RevisionResponse]


class GlyphPage(ReviewSchema):
    items: list[GlyphResponse]
    next_cursor: str | None


class CandidateResponse(ReviewSchema):
    id: UUID
    result_artifact_id: UUID
    glyph_id: UUID
    source_sample_id: UUID
    rank: int
    origin: str
    review_status: str
    content_hash: str
    evidence: dict[str, Any]
    outline: GlyphOutline


class CandidatePage(ReviewSchema):
    items: list[CandidateResponse]
    next_cursor: str | None


class SnapshotResponse(ReviewSchema):
    id: UUID
    project_id: UUID
    kind: str
    label: str | None
    manifest: dict[str, Any]
    manifest_sha256: str
    created_at: datetime


class CoverageResponse(ReviewSchema):
    script_details: dict[str, Any] | None = Field(default=None, exclude_if=lambda v: v is None)
    script_code: str
    core_required: int = 52
    ascii_required: int = 95
    core_approved: int
    ascii_approved: int
    core_complete: bool
    ascii_inventory_complete: bool
    complete_font_validated: Literal[False] = False
    observed: list[int]
    recommended: list[int]
    user_approved: list[int]
    generated_pending: list[int]
    missing: list[int]
    rejected: list[int]
    excluded: list[int]
    warnings: list[str]


class PromptResponse(ReviewSchema):
    definition_version: str
    definition_sha256: str
    initial_passage: str
    passage_status: Literal["unoptimized_example_not_ground_truth"]
    script_code: str
    capture_type: Literal["isolated_characters"] = "isolated_characters"
    codepoints: list[int]
    prompt_text: str
    reason_codes: list[str]
    prompt_is_ground_truth: Literal[False] = False
    natural_cursive_status: Literal["experimental_unavailable"] = "experimental_unavailable"
    prompt_policy_version: str = "coverage-followup-v2"
    selection_reasons: dict[str, list[str]] = Field(default_factory=dict)
    evidence_hash: str | None = None
    expected_codepoints: list[int] = Field(default_factory=list)
    observed_approved_codepoints: list[int] = Field(default_factory=list)
