"""Versioned operator configuration. Defaults are local safety bounds, not quotas."""

from pydantic import BaseModel, ConfigDict, Field


class QueuePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=1, ge=1)
    global_running: int = Field(default=4, ge=1, le=128)
    owner_running: int = Field(default=2, ge=1, le=32)
    aging_seconds: int = Field(default=300, ge=1, le=86400)
    degradation_level: int = Field(default=0, ge=0, le=4)
    retry_after: int = Field(default=5, ge=1, le=600)
    retry_base: float = Field(default=30, ge=0, le=600)
    retry_cap: float = Field(default=600, ge=0, le=3600)
    checkpoint_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    stream_owner_limit: int = Field(default=3, ge=1, le=20)
    stream_global_limit: int = Field(default=32, ge=1, le=1000)
    stream_lifetime_seconds: int = Field(default=60, ge=1, le=300)
    # Resources must be explicitly configured in quota_balances. No money/quality estimates.
    admission_resources: dict[str, dict[str, int]] = Field(default_factory=dict)


TYPES = ("asset_verify", "analyze_samples", "compile_export")
