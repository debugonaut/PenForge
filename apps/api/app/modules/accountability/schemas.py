from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class PolicyResponse(BaseModel):
    version: str
    sha256: str
    document: dict[str, Any]
    ready: bool


class AgreementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terms_accepted: StrictBool
    handwriting_rights_confirmed: StrictBool

    @field_validator("terms_accepted", "handwriting_rights_confirmed")
    @classmethod
    def affirmative(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Both confirmations must be explicitly true")
        return value


class AccessResponse(BaseModel):
    policy_sha256: str
    accepted: bool
    accepted_at: datetime | None
    eligible: bool


class AbuseReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    category: Literal["impersonation", "fraud", "rights", "other"]
    details: str = Field(min_length=20, max_length=4000)
    contact: str | None = Field(default=None, max_length=254)
    resource_id: UUID | None = None


class AbuseReceipt(BaseModel):
    report_id: UUID
