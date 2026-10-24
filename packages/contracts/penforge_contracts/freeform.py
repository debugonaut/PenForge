"""Unqualified freeform evidence, independent of existing analysis job versions.

These contracts cannot publish glyphs or authorize source access. Version 1 describes
original observed canvas points in their original frame; photo masks need a separate
source boundary. Recognition, alignment and owner approval remain distinct.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from .schemas import CaptureSize, Contract, Digest, SafeID, SemVer, Unit, UUIDString
from .serialization import content_hash

Pixel = Annotated[float, Field(strict=True, ge=0, le=32768, allow_inf_nan=False)]
Offset = Annotated[int, Field(strict=True, ge=0, le=4096)]
PointIndex = Annotated[int, Field(strict=True, ge=0, le=19999)]
EnglishText = Annotated[str, StringConstraints(min_length=1, max_length=4096, strict=True)]
ReviewReason = Literal[
    "identity_ambiguous",
    "alignment_ambiguous",
    "connected_ink",
    "clipped_ink",
    "unassigned_ink",
]


def check_text(text: str) -> None:
    # This initial boundary deliberately does not silently normalize text or claim
    # extended Latin/Indic support. Offsets count Unicode scalars, not UTF-16 units.
    if any(ch != "\n" and not 32 <= ord(ch) <= 126 for ch in text):
        raise ValueError("freeform_english_text_required")


class FreeformCanvasSource(Contract):
    project_id: UUIDString
    sample_id: UUIDString
    asset_id: UUIDString
    source_type: Literal["canvas_drawing"] = "canvas_drawing"
    source_sha256: Digest
    document_hash: Digest
    capture_size_px: CaptureSize
    drawing_mode: Literal["fixed_round_v1"] = "fixed_round_v1"


class SuppliedTranscription(Contract):
    text: EnglishText
    origin: Literal["user_transcription"] = "user_transcription"
    prompt_is_ground_truth: Literal[False] = False

    @model_validator(mode="after")
    def text_domain(self) -> Self:
        check_text(self.text)
        return self


class FreeformAnalysisInput(Contract):
    schema_version: Literal[1]
    source: FreeformCanvasSource
    script_code: Literal["Latn"] = "Latn"
    transcription: SuppliedTranscription | None = None
    quality_policy: Literal["accuracy_first"] = "accuracy_first"


class FreeformModelIdentity(Contract):
    model_id: SafeID
    model_version: SemVer
    model_hash: Digest


class FreeformMethod(Contract):
    component_id: SafeID
    component_version: SemVer
    implementation_hash: Digest
    model: FreeformModelIdentity | None = None


class FreeformMethods(Contract):
    recognition: FreeformMethod | None
    alignment: FreeformMethod


class MeasuredFreeformScore(Contract):
    value: Unit
    evaluation_record_hash: Digest


class RecognitionHypothesis(Contract):
    hypothesis_id: SafeID
    text: EnglishText
    recognition: MeasuredFreeformScore | None = None

    @model_validator(mode="after")
    def text_domain(self) -> Self:
        check_text(self.text)
        return self


class HypothesisSpan(Contract):
    hypothesis_id: SafeID
    start: Offset
    end: Offset

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.start >= self.end:
            raise ValueError("text_span_requires_nonempty_half_open_range")
        return self


class CapturePixelBounds(Contract):
    x_min: Pixel
    y_min: Pixel
    x_max: Pixel
    y_max: Pixel

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("empty_capture_bounds")
        return self

    def contains(self, other: CapturePixelBounds) -> bool:
        return (
            self.x_min <= other.x_min <= other.x_max <= self.x_max
            and self.y_min <= other.y_min <= other.y_max <= self.y_max
        )


class FreeformLine(Contract):
    line_id: SafeID
    bounds: CapturePixelBounds
    baseline_y_px: Pixel | None = None
    text_span: HypothesisSpan | None = None


class FreeformWord(Contract):
    word_id: SafeID
    line_id: SafeID
    bounds: CapturePixelBounds
    text_span: HypothesisSpan | None = None


class SourcePointSpan(Contract):
    """Inclusive point indices; not a connected-component/letter equivalence."""

    stroke_id: SafeID
    start_point: PointIndex
    end_point: PointIndex

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start_point > self.end_point:
            raise ValueError("source_point_span_reversed")
        return self


class FreeformInkRegion(Contract):
    region_id: SafeID
    word_id: SafeID | None = None
    bounds: CapturePixelBounds
    source_spans: list[SourcePointSpan] = Field(min_length=1, max_length=2000)
    assignment: Literal["proposed_character", "unresolved"]
    text_span: HypothesisSpan | None = None
    segmentation: MeasuredFreeformScore | None = None
    review_reasons: list[ReviewReason] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def assignment_evidence(self) -> Self:
        if len(set(self.review_reasons)) != len(self.review_reasons):
            raise ValueError("duplicate_review_reason")
        if self.assignment == "proposed_character":
            if self.word_id is None or self.text_span is None:
                raise ValueError("proposed_character_requires_word_and_hypothesis")
        elif not self.review_reasons:
            raise ValueError("unresolved_ink_requires_review_reason")
        return self


class FreeformAlignment(Contract):
    schema_version: Literal[1]
    artifact_type: Literal["penforge.freeform_alignment"]
    input: FreeformAnalysisInput
    methods: FreeformMethods
    coordinate_frame: Literal["original_capture_pixels_top_left_y_down"]
    analysis_to_source_transform: Literal["identity"]
    text_offset_unit: Literal["unicode_scalar"] = "unicode_scalar"
    capability_status: Literal["research_only"] = "research_only"
    review_status: Literal["pending"] = "pending"
    hypotheses: list[RecognitionHypothesis] = Field(max_length=32)
    lines: list[FreeformLine] = Field(max_length=32)
    words: list[FreeformWord] = Field(max_length=256)
    regions: list[FreeformInkRegion] = Field(min_length=1, max_length=512)
    artifact_hash: Digest

    @model_validator(mode="after")
    def evidence_graph(self) -> Self:
        ids = (
            [h.hypothesis_id for h in self.hypotheses]
            + [line.line_id for line in self.lines]
            + [word.word_id for word in self.words]
            + [region.region_id for region in self.regions]
        )
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_freeform_identity")
        if sum(len(r.source_spans) for r in self.regions) > 8192:
            raise ValueError("freeform_span_budget_exceeded")
        if self.hypotheses and self.methods.recognition is None:
            raise ValueError("hypotheses_require_recognition_provenance")
        hypotheses = {h.hypothesis_id: h.text for h in self.hypotheses}
        lines = {line.line_id: line for line in self.lines}
        words = {word.word_id: word for word in self.words}
        size = self.input.source.capture_size_px

        def text(span: HypothesisSpan | None) -> str | None:
            if span is None:
                return None
            value = hypotheses.get(span.hypothesis_id)
            if value is None or span.end > len(value):
                raise ValueError("unknown_or_out_of_bounds_hypothesis_span")
            return value[span.start : span.end]

        def nested(child: HypothesisSpan | None, parent: HypothesisSpan | None) -> None:
            if (
                child is not None
                and parent is not None
                and (
                    child.hypothesis_id != parent.hypothesis_id
                    or not parent.start <= child.start < child.end <= parent.end
                )
            ):
                raise ValueError("text_span_outside_parent")

        for line in self.lines:
            value = text(line.text_span)
            if value is not None and "\n" in value:
                raise ValueError("line_span_contains_line_break")
            if line.baseline_y_px is not None and line.baseline_y_px > size.height:
                raise ValueError("baseline_outside_capture")
        for word in self.words:
            parent_line = lines.get(word.line_id)
            if parent_line is None or not parent_line.bounds.contains(word.bounds):
                raise ValueError("word_outside_line")
            value = text(word.text_span)
            if value is not None and any(ch.isspace() for ch in value):
                raise ValueError("word_span_contains_whitespace")
            nested(word.text_span, parent_line.text_span)
        assigned_offsets: set[tuple[str, int]] = set()
        for region in self.regions:
            value = text(region.text_span)
            if region.word_id is not None:
                parent_word = words.get(region.word_id)
                if parent_word is None or not parent_word.bounds.contains(region.bounds):
                    raise ValueError("ink_region_outside_word")
                nested(region.text_span, parent_word.text_span)
            if region.assignment == "proposed_character" and (
                value is None or len(value) != 1 or not 33 <= ord(value) <= 126
            ):
                raise ValueError("proposed_identity_requires_one_written_ascii_character")
            if region.assignment == "proposed_character" and region.text_span is not None:
                identity = (region.text_span.hypothesis_id, region.text_span.start)
                if identity in assigned_offsets:
                    raise ValueError("text_position_assigned_twice")
                assigned_offsets.add(identity)
        for bounds in [
            *(line.bounds for line in self.lines),
            *(word.bounds for word in self.words),
            *(region.bounds for region in self.regions),
        ]:
            if bounds.x_max > size.width or bounds.y_max > size.height:
                raise ValueError("region_outside_capture")
        if (
            content_hash(self.model_dump(mode="json"), exclude_root_keys={"artifact_hash"})
            != self.artifact_hash
        ):
            raise ValueError("freeform_artifact_hash_mismatch")
        return self
