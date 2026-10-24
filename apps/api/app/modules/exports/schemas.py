from datetime import datetime
from typing import Literal
from uuid import UUID

from penforge_contracts import FontBuildManifest, FontOptions, safe_name
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: UUID | None = None
    formats: list[Literal["ttf", "otf", "woff2"]] = Field(min_length=1, max_length=3)
    family_name: str = Field(min_length=1, max_length=80)
    style_name: str = Field(default="Regular", min_length=1, max_length=80)
    options: FontOptions = Field(default_factory=FontOptions)

    @model_validator(mode="after")
    def names(self):
        self.family_name, self.style_name = safe_name(self.family_name), safe_name(self.style_name)
        if len(set(self.formats)) != len(self.formats):
            raise ValueError("duplicate_formats")
        return self


class ExportCreated(BaseModel):
    export_ids: list[UUID]
    job_id: UUID
    snapshot_id: UUID


class ExportProgress(BaseModel):
    completed_units: int
    total_units: int
    basis: Literal["compiled_formats", "validated_layers"]


class ExportResponse(BaseModel):
    id: UUID
    project_id: UUID
    snapshot_id: UUID
    job_id: UUID | None
    format: Literal["ttf", "otf", "woff2"]
    status: Literal["pending", "ready", "failed"]
    stage: str
    progress: ExportProgress
    eta_seconds: None = None
    build_id: str | None
    build_manifest: FontBuildManifest | None
    coverage_report: dict | None
    validation_status: Literal["passed", "passed_with_warnings", "failed"] | None
    validation_report: dict | None
    sha256: str | None
    size_bytes: int | None
    created_at: datetime
    finalized_at: datetime | None


class ExportDownload(BaseModel):
    method: Literal["GET"]
    url: str
    headers: dict[str, str]
    expires_at: datetime
    resumable: Literal[False] = False
