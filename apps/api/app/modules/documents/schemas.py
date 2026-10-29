"""Strict account contracts for saved Writing Desk documents.

The document body is bounded typography and words. Generated fonts are referenced
by owner-authorized export UUID, never by a transient URL. Imported font files are
deliberately not persisted: the browser-only file stays a device capability.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from penforge_contracts.unicode import graphemes
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WritingFormat = Literal["letter", "note", "card"]
Paper = Literal["cotton", "ruled", "graph", "kraft"]
Alignment = Literal["left", "center", "right"]
Title = Annotated[str, Field(min_length=1, max_length=120)]
Hex = Annotated[str, Field(pattern=r"^#[0-9a-f]{6}$")]


class DocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class WritingTextStyle(DocumentModel):
    """Non-overlapping document overrides; offsets are native UTF-16 boundaries."""

    field: Literal["title", "recipient", "body", "signature"]
    start: Annotated[int, Field(strict=True, ge=0, le=1600)]
    end: Annotated[int, Field(strict=True, ge=1, le=1600)]
    font_size: Annotated[float, Field(strict=True, ge=12, le=120, allow_inf_nan=False)] | None = (
        Field(default=None, exclude_if=lambda value: value is None)
    )
    weight: Annotated[float, Field(strict=True, ge=400, le=700, allow_inf_nan=False)] | None = (
        Field(default=None, exclude_if=lambda value: value is None)
    )
    spacing: Annotated[float, Field(strict=True, ge=-1, le=16, allow_inf_nan=False)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    baseline_shift: (
        Annotated[float, Field(strict=True, ge=-24, le=24, allow_inf_nan=False)] | None
    ) = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def valid_range(self) -> "WritingTextStyle":
        if self.start >= self.end or all(
            value is None
            for value in (self.font_size, self.weight, self.spacing, self.baseline_shift)
        ):
            raise ValueError("selection requires a nonempty range and a style")
        return self


def validate_text_styles(styles: list[WritingTextStyle], values: dict[str, str]) -> None:
    for field, value in values.items():
        boundaries = {0}
        offset = 0
        for cluster in graphemes(value):
            offset += sum(2 if cp > 0xFFFF else 1 for cp in cluster)
            boundaries.add(offset)
        previous_end = 0
        for style in sorted(
            (style for style in styles if style.field == field), key=lambda style: style.start
        ):
            if (
                style.start not in boundaries
                or style.end not in boundaries
                or style.start < previous_end
            ):
                raise ValueError("selection must use non-overlapping grapheme boundaries")
            previous_end = style.end


class WritingBody(DocumentModel):
    paper: Paper = "cotton"
    recipient: Annotated[str, Field(max_length=200)] = ""
    body: Annotated[str, Field(max_length=800)] = ""
    signature: Annotated[str, Field(max_length=200)] = ""
    ink: Hex = "#192e35"
    accent: Hex = "#d7836f"
    font_size: Annotated[float, Field(strict=True, ge=24, le=72, allow_inf_nan=False)] = 42
    line_height: Annotated[float, Field(strict=True, ge=1, le=1.8, allow_inf_nan=False)] = 1.35
    alignment: Alignment = "left"
    weight: Annotated[float, Field(strict=True, ge=400, le=700, allow_inf_nan=False)] = 500
    spacing: Annotated[float, Field(strict=True, ge=-1, le=3, allow_inf_nan=False)] = 0
    slant: Annotated[float, Field(strict=True, ge=-15, le=15, allow_inf_nan=False)] = 0
    width_scale: Annotated[float, Field(strict=True, ge=0.85, le=1.2, allow_inf_nan=False)] = 1
    # Presentation effect only: per-line wobble applied at render/export time.
    naturalness: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] = 0
    # Empty omitted to preserve the canonical body/hash of older create requests.
    text_styles: list[WritingTextStyle] = Field(
        default_factory=list, max_length=128, exclude_if=lambda value: not value
    )
    # References only. Ownership is rechecked when the document is opened.
    project_id: UUID | None = None
    generated_export_id: UUID | None = None

    @model_validator(mode="after")
    def selection_boundaries(self) -> "WritingBody":
        validate_text_styles(
            self.text_styles,
            {"recipient": self.recipient, "body": self.body, "signature": self.signature},
        )
        return self


class WritingDocumentCreate(DocumentModel):
    title: Title
    format: WritingFormat
    document: WritingBody

    @model_validator(mode="after")
    def title_selection_boundaries(self) -> "WritingDocumentCreate":
        validate_text_styles(self.document.text_styles, {"title": self.title})
        return self

    @field_validator("title")
    @classmethod
    def printable_title(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("title contains unsupported characters")
        return value


class WritingDocumentPatch(DocumentModel):
    title: Title | None = None
    format: WritingFormat | None = None
    document: WritingBody | None = None

    @field_validator("title")
    @classmethod
    def printable_title(cls, value: str | None) -> str | None:
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("title contains unsupported characters")
        return value


class WritingDocumentResponse(DocumentModel):
    id: UUID
    title: str
    format: WritingFormat
    document: WritingBody
    row_version: Annotated[int, Field(ge=1)]
    created_at: datetime
    updated_at: datetime


class WritingDocumentPage(DocumentModel):
    items: list[WritingDocumentResponse]
    next_cursor: str | None
