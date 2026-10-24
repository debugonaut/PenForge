"""Analysis requests carry immutable capture IDs and explicit digital normalization."""

from typing import Literal
from uuid import UUID

from penforge_contracts import (
    AnalysisPayload,
    AnalyzeSamplesJob,
    AnalyzeSamplesJobV2,
    TemplateManifest,
)
from penforge_contracts.schemas import AnalyzeOptions, CaptureAssignment, Contract, Digest
from pydantic import Field, model_validator


class AnalysisCreate(Contract):
    job_type: Literal["analyze_samples"] = "analyze_samples"
    sample_ids: list[UUID] = Field(min_length=1, max_length=8)
    assignments: list[CaptureAssignment] = Field(default_factory=list, max_length=8)
    options: AnalyzeOptions = Field(default_factory=AnalyzeOptions)
    drawing_mode: Literal["fixed_round_v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def distinct(self) -> "AnalysisCreate":
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("duplicate_sample")
        self.payload(UUID(int=0), None, "Latn" if self.drawing_mode else "Deva")
        return self

    def payload(
        self, project_id: UUID, manifest_hash: str | None, script_code: str = "Latn"
    ) -> AnalysisPayload:
        fields = dict(
            schema_version=2 if self.drawing_mode else 1,
            job_type="analyze_samples",
            project_id=str(project_id),
            script_code=script_code,
            sample_ids=[str(item) for item in self.sample_ids],
            template_manifest_hash=manifest_hash,
            assignments=self.assignments,
            options=self.options,
        )
        if self.drawing_mode:
            return AnalyzeSamplesJobV2.model_validate(fields | {"drawing_mode": self.drawing_mode})
        return AnalyzeSamplesJob.model_validate(fields)


class AnalysisProgress(Contract):
    completed_units: int
    total_units: int
    basis: Literal["assessed_samples"] = "assessed_samples"


class AnalysisJobResponse(Contract):
    job_id: UUID
    state: str
    stage: str | None
    attempts: int
    progress: AnalysisProgress
    eta_seconds: None = None
    error_code: str | None = None
    requires_user_action: bool = False
    result_artifact_id: UUID | None = None
    quality: list[dict] = Field(default_factory=list)


class TemplateItem(Contract):
    manifest: TemplateManifest
    manifest_sha256: Digest
    svg_url: str
    pdf_url: str


class TemplatePage(Contract):
    items: list[TemplateItem]
