"""Strict Phase 03 capture contracts; source bytes never appear in responses."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from penforge_contracts import StrokeDocument
from penforge_contracts.schemas import CaptureSize
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
ImageSource = Literal["template_scan", "template_photo", "freeform_photo"]
AssetState = Literal[
    "pending_upload", "uploaded", "verifying", "ready", "rejected", "expired", "deleting", "deleted"
]


class CaptureSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemplateReference(CaptureSchema):
    template_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    template_version: str = Field(min_length=1, max_length=50)
    manifest_sha256: Digest
    page_index: int = Field(strict=True, ge=0, le=999)


class UploadInit(CaptureSchema):
    filename: str = Field(min_length=1, max_length=255)
    declared_mime: Literal["image/jpeg", "image/png", "image/webp"]
    declared_size: int = Field(strict=True, ge=1, le=20 * 1024 * 1024)
    source_type: ImageSource
    sha256: Digest | None = None
    template: TemplateReference | None = None

    @model_validator(mode="after")
    def template_source(self) -> "UploadInit":
        if self.source_type.startswith("template_") != (self.template is not None):
            raise ValueError("template_source_requires_pinned_manifest")
        return self


class CapabilityResponse(CaptureSchema):
    method: Literal["PUT", "GET"]
    url: str
    headers: dict[str, str]
    expires_at: datetime
    resumable: Literal[False] = False


class UploadResponse(CaptureSchema):
    asset_id: UUID
    upload: CapabilityResponse


class AssetResponse(CaptureSchema):
    asset_id: UUID
    project_id: UUID
    source_type: str | None
    mime_type: str
    size_bytes: int
    sha256: Digest | None
    state: AssetState
    reject_reason: str | None
    duplicate_of: UUID | None
    job_id: UUID | None
    expires_at: datetime | None
    width_px: int | None
    height_px: int | None
    document_hash: Digest | None = None


class UploadComplete(CaptureSchema):
    asset: AssetResponse
    job_id: UUID
    poll_url: str


class PromptContext(CaptureSchema):
    prompt_policy_version: Literal["coverage-followup-v2"]
    expected_codepoints: list[int] = Field(max_length=12)
    selection_reasons: dict[str, list[str]]
    evidence_hash: Digest
    prompt_is_ground_truth: Literal[False] = False


class AssetSampleCreate(CaptureSchema):
    prompt_context: PromptContext | None = None
    source_kind: Literal["asset"]
    asset_id: UUID
    source_type: ImageSource
    template: TemplateReference | None = None


class StrokeSampleCreate(CaptureSchema):
    prompt_context: PromptContext | None = None
    source_kind: Literal["strokes"]
    stroke_document: StrokeDocument
    parent_asset_id: UUID | None = None
    template: TemplateReference | None = None


SampleCreate = Annotated[AssetSampleCreate | StrokeSampleCreate, Field(discriminator="source_kind")]


class SampleResponse(CaptureSchema):
    sample_id: UUID
    project_id: UUID
    asset_id: UUID
    source_type: str
    source_sha256: Digest
    document_hash: Digest | None
    template: TemplateReference | None
    state: Literal["pending", "analyzed", "needs_better_input", "rejected"] = "pending"
    quality: dict = Field(default_factory=dict)
    quality_reasons: list[str] = Field(default_factory=list)
    capture_size_px: CaptureSize | None = None
    timing: Literal["observed", "unavailable_legacy"] | None = None


class SamplePage(CaptureSchema):
    items: list[SampleResponse]


class VerificationProgress(CaptureSchema):
    completed_units: int
    total_units: int | None
    basis: Literal["verified_checks"] = "verified_checks"


class VerificationJobResponse(CaptureSchema):
    job_id: UUID
    asset_id: UUID
    state: str
    stage: str | None
    attempts: int
    progress: VerificationProgress
    eta_seconds: None = None
    error_code: str | None
    result_asset_id: UUID | None
