"""Strict metadata-only project API contracts."""

from typing import Annotated

from penforge_contracts import ProjectDTO as ContractProjectDTO
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ScriptCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][a-z]{3}$")]


class ProjectSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StyleControls(ProjectSchema):
    thickness: float | None = Field(default=None, strict=True, ge=0, le=100, allow_inf_nan=False)
    slant: float | None = Field(default=None, strict=True, ge=-45, le=45, allow_inf_nan=False)
    spacing: float | None = Field(default=None, strict=True, ge=0, le=100, allow_inf_nan=False)
    smoothness: float | None = Field(default=None, strict=True, ge=0, le=100, allow_inf_nan=False)
    proportion: float | None = Field(default=None, strict=True, ge=0, le=100, allow_inf_nan=False)

    @field_validator("thickness", "slant", "spacing", "smoothness", "proportion")
    @classmethod
    def finite_number(cls, value: float | None) -> float | None:
        # Pydantic's numeric bounds reject infinities and NaN in JSON; keep this
        # explicit for non-JSON call paths and contract clarity.
        if value is not None and not (-1e9 < value < 1e9):
            raise ValueError("style control must be finite")
        return value


class ProjectCreate(ProjectSchema):
    name: Name
    script_code: ScriptCode = "Latn"
    style_controls: StyleControls = Field(default_factory=StyleControls)

    @field_validator("name")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("project name contains unsupported characters")
        return value


class ProjectPatch(ProjectSchema):
    name: Name | None = None
    style_controls: StyleControls | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("project name contains unsupported characters")
        return value


class ProjectResponse(ContractProjectDTO):
    """Named HTTP response shape; the registry DTO remains an independent schema."""


class ProjectPage(ProjectSchema):
    items: list[ProjectResponse]
    next_cursor: str | None
