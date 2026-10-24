"""Validated operator input; limits are proposals until explicitly provisioned."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Resource = Literal["storage_bytes", "compute_seconds", "exports", "generation_candidates"]


class QuotaRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    window_kind: Literal["lifetime", "day", "month"]
    limit_amount: int = Field(ge=0)


class QuotaConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    version: int = Field(ge=1)
    tiers: dict[str, dict[Resource, QuotaRule]]
    global_limits: dict[Resource, QuotaRule] = Field(alias="global")

    @model_validator(mode="after")
    def launch_tiers(self):
        if not {"free_guest", "free_user"} <= self.tiers.keys():
            raise ValueError("both_identity_tiers_required")
        for limits in self.tiers.values():
            if not {"storage_bytes", "compute_seconds"} <= limits.keys():
                raise ValueError("core_resource_limits_required")
            if not limits.keys() <= self.global_limits.keys():
                raise ValueError("global_limit_required_for_every_resource")
        return self
