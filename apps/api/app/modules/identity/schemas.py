"""Phase 01 identity wire contracts. Budgets are reported only when configured."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class IdentityDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GuestCreations(IdentityDTO):
    limit: int = Field(ge=0)
    used: int = Field(ge=0)
    remaining: int = Field(ge=0)
    applicable: bool


class QuotaBalance(IdentityDTO):
    resource: str
    window_kind: Literal["lifetime", "day", "month"]
    window_start: date
    limit_amount: int = Field(ge=0)
    committed: int = Field(ge=0)
    reserved: int = Field(ge=0)
    available: int = Field(ge=0)
    over_limit: bool


class QuotaSummary(IdentityDTO):
    configured: bool
    over_limit: bool
    balances: list[QuotaBalance]


class MeResponse(IdentityDTO):
    user_id: UUID
    firebase_uid: str
    is_anonymous: bool
    sign_in_provider: str
    email_verified: bool
    tier: str
    created_at: datetime
    guest_creations: GuestCreations
    quotas: QuotaSummary


class ClaimRequest(IdentityDTO):
    guest_id_token: SecretStr = Field(min_length=1, max_length=16384)


class TransferCounts(IdentityDTO):
    projects: int = Field(ge=0)
    assets: int = Field(ge=0)
    jobs: int = Field(ge=0)
    reservations: int = Field(ge=0)


class ClaimResponse(IdentityDTO):
    mode: Literal["linked", "transferred"]
    source_user_id: UUID
    destination_user_id: UUID
    transferred: TransferCounts
    guest_creations_preserved: int = Field(ge=0)
    over_limit: bool
