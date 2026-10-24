"""Bounded, strict account working-state contracts.

These schemas persist resumable draft state only. Approved outline geometry,
snapshots and font exports live in their own immutable tables; a workspace may
reference them but can never approve, activate or replace them.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from penforge_contracts import StrokeDocument
from pydantic import BaseModel, ConfigDict, Field, field_validator

Step = Literal["capture", "review", "edit", "style", "preview", "export"]
GlyphStatus = Literal["missing", "detected", "needs-review", "edited", "approved"]
SampleKind = Literal["draw", "upload", "demo"]
ScriptCode = Annotated[str, Field(pattern=r"^(Latn|Deva)$")]
ShortText = Annotated[str, Field(strict=True, min_length=1, max_length=200)]
Unit = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class WorkspaceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class DraftPoint(WorkspaceModel):
    x: Unit
    y: Unit


class DraftGlyph(WorkspaceModel):
    """A preview entry. Nothing here is canonical outline geometry."""

    id: Annotated[str, Field(min_length=1, max_length=200)]
    character: Annotated[str, Field(min_length=1, max_length=32)]
    source_character: Annotated[str, Field(min_length=1, max_length=32)]
    status: GlyphStatus
    revision: Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
    rotation: Annotated[float, Field(strict=True, ge=-360, le=360, allow_inf_nan=False)] = 0
    scale: Annotated[float, Field(strict=True, ge=1, le=1000, allow_inf_nan=False)] = 100
    crop: Annotated[float, Field(strict=True, ge=0, le=49, allow_inf_nan=False)] = 0
    strokes: list[list[DraftPoint]] = Field(default_factory=list, max_length=2000)


class DraftStyle(WorkspaceModel):
    thickness: Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)] = 50
    slant: Annotated[float, Field(strict=True, ge=-45, le=45, allow_inf_nan=False)] = 0
    spacing: Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)] = 50
    smoothness: Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)] = 50
    proportion: Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)] = 50


class DraftSample(WorkspaceModel):
    kind: SampleKind
    # Preview strokes only; the submitted source document keeps observed timing.
    strokes: list[list[DraftPoint]] | None = Field(default=None, max_length=2000)
    stroke_document: StrokeDocument | None = None
    capture_started_at_ms: Annotated[int, Field(strict=True, ge=0, le=10**13)] | None = None
    original_capture_id: (
        Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")] | None
    ) = None
    capture_asset_id: UUID | None = None
    file_name: Annotated[str, Field(max_length=1024)] | None = None

    @field_validator("strokes")
    @classmethod
    def bounded_points(cls, value: list[list[DraftPoint]] | None):
        if value is not None and sum(len(stroke) for stroke in value) > 100_000:
            raise ValueError("stroke_point_budget_exceeded")
        return value


class DraftAssignment(WorkspaceModel):
    sample_id: UUID
    codepoints: list[Annotated[int, Field(strict=True, ge=0, le=0x10FFFF)]] = Field(
        min_length=1, max_length=32
    )
    updated_at: datetime


class DraftCapture(WorkspaceModel):
    sample_id: UUID
    asset_id: UUID
    kind: Literal["draw", "image"]
    created_at: datetime


class DraftExport(WorkspaceModel):
    export_id: UUID
    format: Literal["ttf", "otf", "woff2"]
    status: Literal["requested", "processing", "ready", "failed"]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    created_at: datetime


class DraftSnapshot(WorkspaceModel):
    id: UUID
    label: Annotated[str, Field(min_length=1, max_length=120)]
    created_at: datetime


class DraftReview(WorkspaceModel):
    approved_glyph_ids: list[ShortText] = Field(default_factory=list, max_length=1024)
    assignments: list[DraftAssignment] = Field(default_factory=list, max_length=512)


class WorkspaceDocument(WorkspaceModel):
    schema_version: Literal[1] = 1
    script_code: ScriptCode = "Latn"
    step: Step = "capture"
    glyphs: list[DraftGlyph] = Field(default_factory=list, max_length=1024)
    style: DraftStyle = Field(default_factory=DraftStyle)
    sample: DraftSample | None = None
    preview_text: Annotated[str, Field(max_length=10_000)] = ""
    review: DraftReview = Field(default_factory=DraftReview)
    captures: list[DraftCapture] = Field(default_factory=list, max_length=64)
    exports: list[DraftExport] = Field(default_factory=list, max_length=32)
    snapshots: list[DraftSnapshot] = Field(default_factory=list, max_length=32)
    selected_export_id: UUID | None = None
    active_snapshot_id: UUID | None = None

    @field_validator("glyphs")
    @classmethod
    def unique_glyph_ids(cls, value: list[DraftGlyph]):
        if len({glyph.id for glyph in value}) != len(value):
            raise ValueError("duplicate_glyph_id")
        return value


class WorkspacePut(WorkspaceModel):
    workspace: WorkspaceDocument


class WorkspaceResponse(WorkspaceModel):
    project_id: UUID
    workspace: WorkspaceDocument
    row_version: Annotated[int, Field(ge=1)]
    updated_at: datetime
