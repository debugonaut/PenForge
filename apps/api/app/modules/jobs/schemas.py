"""Durable status snapshot, shared by polling and resumable events."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Progress(BaseModel):
    completed_units: int = Field(ge=0)
    total_units: int | None
    basis: str
    indeterminate: bool
    stage_status: Literal["waiting", "active", "finished"]
    plan_revision: int = Field(ge=1)
    denominator_change_reason: str | None


class JobStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID
    job_type: str
    state: Literal["queued", "running", "cancel_requested", "succeeded", "failed", "canceled"]
    stage: str | None
    attempts: int
    event_id: int = 0
    progress: Progress
    eta_seconds: None = None
    eta_reason: Literal["insufficient_data"] = "insufficient_data"
    quality_policy: Literal["accuracy_first"] = "accuracy_first"
    queue_wait: dict[str, str | None]
    active_processing: dict[str, float | list[int] | None]
    user_review_wait: bool
    requires_user_action: bool
    error_code: str | None
    retrying: bool
    asset_id: UUID | None
    result_asset_id: UUID | None
