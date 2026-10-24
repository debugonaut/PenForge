"""Lightweight, versioned deterministic build contract; no compiler dependencies."""

import re
import unicodedata
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from .schemas import Contract, Digest
from .serialization import content_hash


def safe_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = " ".join(
        "".join(
            c for c in value if c.isalnum() or unicodedata.category(c).startswith("M") or c in " -_"
        ).split()
    )
    if not value or len(value) > 80:
        raise ValueError("invalid_font_name")
    return value


class FontOptions(Contract):
    schema_version: Literal[1] = 1
    units_per_em: int = Field(default=1000, strict=True, ge=16, le=16384)
    ascender: int = Field(default=1000, strict=True, ge=1, le=32767)
    descender: int = Field(default=-300, strict=True, ge=-32768, le=0)
    line_gap: int = Field(default=0, strict=True, ge=0, le=32767)
    conversion_tolerance: float = Field(default=1.5, ge=0.75, le=2, allow_inf_nan=False)
    fs_type: Literal[0, 2, 4, 8] = 0
    realism: int = Field(default=0, strict=True, ge=0, le=100, exclude_if=lambda v: v == 0)
    # Measured letter-spacing strength. 0 emits no GPOS kern feature at all.
    spacing: int = Field(default=0, strict=True, ge=0, le=100, exclude_if=lambda v: v == 0)
    license_description: str = Field(
        default="Private handwriting font. No training consent granted.", max_length=500
    )

    @model_validator(mode="after")
    def license_safe(self) -> Self:
        if any(unicodedata.category(c).startswith("C") for c in self.license_description):
            raise ValueError("invalid_license_metadata")
        return self


class FontBuildManifest(Contract):
    schema_version: Literal[1] = 1
    snapshot_hash: Digest
    snapshot_created_at: datetime
    family_name: str
    style_name: str = "Regular"
    options: FontOptions = Field(default_factory=FontOptions)
    tool_versions: dict[str, str]
    compiler_version: Literal["penforge-font-v1"] = "penforge-font-v1"
    validation_profile: Literal["penforge-latin-v1", "penforge-devanagari-v1"] = "penforge-latin-v1"
    script_code: Literal["Latn", "Deva"] = Field(default="Latn", exclude_if=lambda v: v == "Latn")
    indic_evidence_hash: Digest | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def frozen_names(self) -> Self:
        if (self.script_code == "Deva") != (
            self.validation_profile == "penforge-devanagari-v1"
            and self.indic_evidence_hash is not None
        ):
            raise ValueError("indic_build_profile_required")
        if self.snapshot_created_at.utcoffset() is None:
            raise ValueError("snapshot_time_requires_timezone")
        if self.family_name != safe_name(self.family_name) or self.style_name != safe_name(
            self.style_name
        ):
            raise ValueError("names_must_be_sanitized")
        return self

    @property
    def build_id(self) -> str:
        return content_hash(self.model_dump(mode="json"))

    @property
    def postscript_name(self) -> str:
        stem = re.sub("[^A-Za-z0-9-]", "", self.family_name + "-" + self.style_name)[:38]
        return (stem or "Penforge-Regular") + "-" + self.build_id[7:31]
