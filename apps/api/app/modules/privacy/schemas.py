"""Privacy wire contracts; no capture content in deletion progress."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, model_validator


def explicit_confirmation(value):
    if value is not True:
        raise ValueError("Explicit boolean confirmation is required")
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConsentPut(Strict):
    purpose: Literal["model_training"]
    granted: StrictBool
    policy_version: str = Field(min_length=1, max_length=80)


class ConsentView(Strict):
    purpose: Literal["model_training"] = "model_training"
    policy_version: str
    granted: bool
    eligible: bool
    consent_id: UUID | None = None
    granted_at: datetime | None = None
    withdrawn_at: datetime | None = None
    withdrawal_deletion_id: UUID | None = None
    unlearning_limit: str = "Withdrawal excludes future datasets; it cannot guarantee removal of influence from already trained weights."


class DeletionCreate(Strict):
    scope: Literal["project", "account", "contributions_only"]
    project_id: UUID | None = None
    expected_project_version: int | None = Field(default=None, ge=1)
    confirmed: Annotated[Literal[True], BeforeValidator(explicit_confirmation)]

    @model_validator(mode="after")
    def scope_target(self):
        if (self.scope == "project") != (self.project_id is not None):
            raise ValueError("Only project scope requires project_id")
        if self.scope != "project" and self.expected_project_version is not None:
            raise ValueError("Project version requires project scope")
        return self


class DeletionView(Strict):
    id: UUID
    scope: Literal["project", "account", "contributions_only"]
    state: Literal["requested", "in_progress", "completed", "failed"]
    progress: dict[str, Literal["done", "waiting"]]
    evidence: dict[str, int]
    error_code: str | None
    retryable: bool
    requested_at: datetime
    completed_at: datetime | None
    backup_expiry: None = None
    progress_token: str | None = None


class ContributionCreate(Strict):
    sample_id: UUID
    consent_id: UUID
    policy_version: str
    confirmed: Annotated[Literal[True], BeforeValidator(explicit_confirmation)]


class ContributionView(Strict):
    id: UUID
    status: str
    content_sha256: str | None
    consent_id: UUID | None
    transformation: str
    removed_from_dataset_at: datetime | None


class PrivacyPolicy(Strict):
    version: Literal[1] = 1
    policy_version: Literal["model-training-v1"] = "model-training-v1"
    guest_contributions: Literal[False] = False
    new_requests_enabled: bool = True
    contributions_enabled: bool = True
    retry_base_seconds: int = Field(default=2, ge=0, le=600)
    retry_cap_seconds: int = Field(default=600, ge=1, le=3600)
    stuck_seconds: int = Field(default=3600, ge=60, le=86400)


class RetentionPolicy(Strict):
    version: Literal[1] = 1
    pending_upload_seconds: int = Field(default=86400, ge=60)
    derived_seconds: int = Field(default=2592000, ge=60)
    checkpoint_seconds: int = Field(default=86400, ge=60)
    staged_seconds: int = Field(default=86400, ge=60)
    idempotency_seconds: int = Field(default=86400, ge=60)
    event_seconds: int = Field(default=7776000, ge=60)
    audit_seconds: int = Field(default=31536000, ge=60)
