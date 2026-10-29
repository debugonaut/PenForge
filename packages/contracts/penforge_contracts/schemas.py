"""Phase 00 wire contracts. Versions and safety bounds are independent of ML quality."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    ValidationInfo,
    model_validator,
)

SafeID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")]
UUIDString = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
ScriptCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][a-z]{3}$")]
SemVer = Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=100)]
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Unit = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
Nonnegative = Annotated[float, Field(strict=True, ge=0, le=1e12, allow_inf_nan=False)]
PositiveInt = Annotated[int, Field(strict=True, ge=1, le=2**31 - 1)]
Scalar = Annotated[int, Field(strict=True, ge=0, le=0x10FFFF)]
Codepoints = Annotated[list[Scalar], Field(min_length=1, max_length=16)]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, validate_default=True, str_max_length=10000
    )

    @model_validator(mode="before")
    @classmethod
    def integer_schema_version(cls, value: object) -> object:
        if isinstance(value, dict) and "schema_version" in value:
            if type(value["schema_version"]) is not int:
                raise ValueError("schema_version_requires_integer")
        if isinstance(value, dict):
            for field in ("normalized", "closed", "prompt_is_ground_truth"):
                if field in value and type(value[field]) is not bool:
                    raise ValueError("boolean_requires_boolean")
        return value


def check_scalars(values: list[int]) -> None:
    if any(0xD800 <= value <= 0xDFFF for value in values):
        raise ValueError("invalid_unicode_scalar")


class FontCoordinates(Contract):
    origin: Literal["baseline"] = "baseline"
    x_positive: Literal["right"] = "right"
    y_positive: Literal["up"] = "up"


class Point(Contract):
    x: Finite
    y: Finite


class LineSegment(Contract):
    type: Literal["line"]
    to: Point


class QuadraticSegment(Contract):
    type: Literal["quadratic"]
    c: Point
    to: Point


class CubicSegment(Contract):
    type: Literal["cubic"]
    c1: Point
    c2: Point
    to: Point


Segment = Annotated[LineSegment | QuadraticSegment | CubicSegment, Field(discriminator="type")]


class Contour(Contract):
    closed: Literal[True]
    start: Point
    segments: list[Segment] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def closure(self) -> Self:
        if self.segments[-1].to != self.start:
            raise ValueError("contour_must_explicitly_close")
        return self


class GlyphMetrics(Contract):
    advance_width: Annotated[float, Field(strict=True, ge=0, le=131072, allow_inf_nan=False)]
    left_side_bearing: Finite


class GlyphOutline(Contract):
    schema_version: Literal[1]
    units_per_em: Annotated[int, Field(strict=True, ge=16, le=16384)]
    coordinate_system: FontCoordinates
    codepoints: Codepoints
    mapping: Literal["cmap", "gsub_only"] = "cmap"
    fill_rule: Literal["evenodd"] = "evenodd"
    metrics: GlyphMetrics
    contours: list[Contour] = Field(max_length=128)
    indic: "IndicEvidence | None" = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def geometry(self, info: ValidationInfo) -> Self:
        check_scalars(self.codepoints)
        if self.mapping == "cmap" and len(self.codepoints) != 1:
            raise ValueError("cmap_requires_single_scalar")
        if not self.contours and self.codepoints != [32]:
            raise ValueError("empty_outline_requires_space")
        if self.codepoints == [32] and self.contours:
            raise ValueError("space_has_metrics_only")
        context = info.context or {}
        node_limit = context.get("max_outline_nodes", 8192)
        extent = self.units_per_em * context.get("max_em_extent", 8)
        points: list[Point] = []
        for contour in self.contours:
            points.append(contour.start)
            contour_points = [contour.start]
            for segment in contour.segments:
                points.append(segment.to)
                contour_points.append(segment.to)
                if isinstance(segment, QuadraticSegment):
                    points.append(segment.c)
                    contour_points.append(segment.c)
                elif isinstance(segment, CubicSegment):
                    points.extend((segment.c1, segment.c2))
                    contour_points.extend((segment.c1, segment.c2))
            first = contour_points[0]
            other = next((p for p in contour_points if p != first), None)
            if other is None or all(
                (p.x - first.x) * (other.y - first.y) == (p.y - first.y) * (other.x - first.x)
                for p in contour_points
            ):
                raise ValueError("contour_has_no_filled_area")
        if len(points) > node_limit:
            raise ValueError("outline_node_budget_exceeded")
        if any(abs(p.x) > extent or abs(p.y) > extent for p in points):
            raise ValueError("outline_geometry_budget_exceeded")
        if self.metrics.advance_width > extent or abs(self.metrics.left_side_bearing) > extent:
            raise ValueError("outline_metric_budget_exceeded")
        if self.indic is not None:
            from .serialization import content_hash

            value = self.model_dump(mode="json")
            value.pop("indic", None)
            if self.indic.geometry_hash != content_hash(value):
                raise ValueError("anchor_geometry_hash_mismatch")
        return self


class CanvasCoordinates(Contract):
    origin: Literal["top_left"] = "top_left"
    x_positive: Literal["right"] = "right"
    y_positive: Literal["down"] = "down"
    normalized: Literal[True] = True


class CaptureSize(Contract):
    width: Annotated[int, Field(strict=True, ge=1, le=32768)]
    height: Annotated[int, Field(strict=True, ge=1, le=32768)]


class StrokePoint(Contract):
    x: Unit
    y: Unit
    t_ms: Nonnegative | None = None
    pressure: Unit | None = None
    tilt_x: Annotated[float, Field(strict=True, ge=-90, le=90)] | None = None
    tilt_y: Annotated[float, Field(strict=True, ge=-90, le=90)] | None = None
    twist: Annotated[float, Field(strict=True, ge=0, lt=360)] | None = None


class Stroke(Contract):
    stroke_id: SafeID
    pointer_type: Literal["pen", "mouse", "touch", "keyboard"] | None = None
    points: list[StrokePoint] = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        times = [p.t_ms for p in self.points if p.t_ms is not None]
        if times != sorted(times):
            raise ValueError("stroke_timestamps_not_monotonic")
        return self


class TranslateOperation(Contract):
    type: Literal["translate"]
    stroke_ids: list[SafeID] = Field(min_length=1, max_length=2000)
    dx: Annotated[float, Field(strict=True, ge=-1, le=1)]
    dy: Annotated[float, Field(strict=True, ge=-1, le=1)]


class StrokeDocument(Contract):
    schema_version: Literal[1]
    coordinate_system: CanvasCoordinates
    capture_size_px: CaptureSize | None
    timing: Literal["observed", "unavailable_legacy"]
    strokes: list[Stroke] = Field(min_length=1, max_length=2000)
    parent_source_hash: Digest | None = None
    operations: list[TranslateOperation] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def capture(self, info: ValidationInfo) -> Self:
        ids = [s.stroke_id for s in self.strokes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_stroke_id")
        points = [p for stroke in self.strokes for p in stroke.points]
        if len(points) > (info.context or {}).get("max_stroke_points", 100000):
            raise ValueError("stroke_point_budget_exceeded")
        if self.timing == "observed":
            if self.capture_size_px is None or any(p.t_ms is None for p in points):
                raise ValueError("observed_capture_requires_size_and_timestamps")
            times = [p.t_ms for p in points if p.t_ms is not None]
            if times != sorted(times):
                raise ValueError("capture_stroke_order_not_monotonic")
        elif any(
            p.t_ms is not None
            or p.pressure is not None
            or p.tilt_x is not None
            or p.tilt_y is not None
            or p.twist is not None
            for p in points
        ):
            raise ValueError("legacy_capture_cannot_invent_observed_metadata")
        elif self.capture_size_px is not None or any(
            s.pointer_type is not None for s in self.strokes
        ):
            raise ValueError("legacy_capture_cannot_invent_observed_metadata")
        if self.operations and self.parent_source_hash is None:
            raise ValueError("derived_capture_requires_parent_hash")
        for operation in self.operations:
            if not set(operation.stroke_ids) <= set(ids):
                raise ValueError("operation_references_unknown_stroke")
        return self


class Page(Contract):
    width_mm: Annotated[float, Field(strict=True, gt=0, le=2000)]
    height_mm: Annotated[float, Field(strict=True, gt=0, le=2000)]
    index: Annotated[int, Field(strict=True, ge=0, le=999)]


class Rect(Contract):
    x: Nonnegative
    y: Nonnegative
    width: Annotated[float, Field(strict=True, gt=0, le=2000)]
    height: Annotated[float, Field(strict=True, gt=0, le=2000)]


class Marker(Contract):
    marker_id: Annotated[int, Field(strict=True, ge=0, le=49)]
    rect_mm: Rect


class Markers(Contract):
    type: Literal["aruco"]
    dictionary: Literal["DICT_4X4_50"]
    items: list[Marker] = Field(min_length=3, max_length=50)
    quiet_zone_mm: Nonnegative | None = Field(default=None, exclude_if=lambda x: x is None)


class Guide(Contract):
    guide_id: SafeID
    baseline_offset_mm: Nonnegative
    cap_height_offset_mm: Nonnegative | None = Field(default=None, exclude_if=lambda x: x is None)
    x_height_offset_mm: Nonnegative | None = Field(default=None, exclude_if=lambda x: x is None)
    descender_offset_mm: Nonnegative | None = Field(default=None, exclude_if=lambda x: x is None)
    headline_offset_mm: Nonnegative | None = Field(default=None, exclude_if=lambda x: x is None)
    upper_mark_offset_mm: Nonnegative | None = Field(default=None, exclude_if=lambda x: x is None)
    lower_mark_offset_mm: Nonnegative | None = Field(default=None, exclude_if=lambda x: x is None)


class TemplateCell(Contract):
    cell_id: SafeID
    codepoints: Codepoints
    rect_mm: Rect
    guide_id: SafeID

    @model_validator(mode="after")
    def unicode(self) -> Self:
        check_scalars(self.codepoints)
        return self


class PublicPageIdentity(Contract):
    encoding: Literal["qr"]
    public_payload: Annotated[str, StringConstraints(max_length=180)]
    rect_mm: Rect | None = Field(default=None, exclude_if=lambda x: x is None)
    quiet_zone_modules: Annotated[int, Field(strict=True, ge=4, le=16)] | None = Field(
        default=None, exclude_if=lambda x: x is None
    )


class TemplatePrinting(Contract):
    printable_margin_mm: Nonnegative
    scale_reference_rect_mm: Rect
    guide_color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-f]{6}$")]
    border_color: Annotated[str, StringConstraints(pattern=r"^#[0-9a-f]{6}$")]


class ExtractionPolicy(Contract):
    margin_mm: Annotated[float, Field(strict=True, ge=0, le=50)]
    min_markers: Annotated[int, Field(strict=True, ge=3, le=50)]


def rects_overlap(a: Rect, b: Rect) -> bool:
    return (
        a.x < b.x + b.width
        and b.x < a.x + a.width
        and a.y < b.y + b.height
        and b.y < a.y + a.height
    )


class TemplateManifest(Contract):
    schema_version: Literal[1]
    template_id: SafeID
    template_version: SemVer
    script_code: ScriptCode
    page: Page
    markers: Markers
    page_identity: PublicPageIdentity
    cells: list[TemplateCell] = Field(min_length=1, max_length=1000)
    guides: list[Guide] = Field(min_length=1, max_length=100)
    extraction: ExtractionPolicy
    printing: TemplatePrinting | None = Field(default=None, exclude_if=lambda x: x is None)
    label_provenance: dict[str, str] | None = Field(default=None, exclude_if=lambda x: x is None)

    @model_validator(mode="after")
    def physical_geometry(self) -> Self:
        expected = f"{self.template_id}:{self.template_version}:{self.page.index}"
        if self.page_identity.public_payload != expected:
            raise ValueError("page_identity_must_contain_only_public_template_identity")
        if self.extraction.min_markers > len(self.markers.items):
            raise ValueError("insufficient_manifest_markers")
        if len({m.marker_id for m in self.markers.items}) != len(self.markers.items):
            raise ValueError("duplicate_marker_id")
        if len({c.cell_id for c in self.cells}) != len(self.cells):
            raise ValueError("duplicate_cell_id")
        guide_map = {guide.guide_id: guide for guide in self.guides}
        if len(guide_map) != len(self.guides):
            raise ValueError("duplicate_guide_id")
        quiet = self.markers.quiet_zone_mm or 0
        rectangles = []
        for marker in self.markers.items:
            r = marker.rect_mm
            if min(r.x, r.y) < quiet:
                raise ValueError("marker_quiet_zone_outside_page")
            rectangles.append(
                Rect(
                    x=r.x - quiet,
                    y=r.y - quiet,
                    width=r.width + 2 * quiet,
                    height=r.height + 2 * quiet,
                )
            )
        rectangles += [c.rect_mm for c in self.cells]
        if self.page_identity.rect_mm is not None:
            if self.page_identity.quiet_zone_modules is None:
                raise ValueError("qr_geometry_requires_quiet_zone")
            rectangles.append(self.page_identity.rect_mm)
        if self.printing is not None:
            rectangles.append(self.printing.scale_reference_rect_mm)
        for rect in rectangles:
            if (
                rect.x + rect.width > self.page.width_mm
                or rect.y + rect.height > self.page.height_mm
            ):
                raise ValueError("template_geometry_outside_page")
            if self.printing is not None:
                margin = self.printing.printable_margin_mm
                if (
                    min(rect.x, rect.y) < margin
                    or rect.x + rect.width > self.page.width_mm - margin
                    or rect.y + rect.height > self.page.height_mm - margin
                ):
                    raise ValueError("template_outside_printable_margin")
        for index, rect in enumerate(rectangles):
            if any(rects_overlap(rect, other) for other in rectangles[index + 1 :]):
                raise ValueError("overlapping_template_regions")
        for cell in self.cells:
            guide = guide_map.get(cell.guide_id)
            if guide is None or guide.baseline_offset_mm > cell.rect_mm.height:
                raise ValueError("invalid_cell_guide")
            offsets = [
                guide.cap_height_offset_mm,
                guide.x_height_offset_mm,
                guide.baseline_offset_mm,
                guide.descender_offset_mm,
            ]
            present = [offset for offset in offsets if offset is not None]
            if present != sorted(present) or any(x > cell.rect_mm.height for x in present):
                raise ValueError("invalid_vertical_guide_order")
            indic = [
                guide.upper_mark_offset_mm,
                guide.headline_offset_mm,
                guide.baseline_offset_mm,
                guide.lower_mark_offset_mm,
            ]
            present_indic = [x for x in indic if x is not None]
            if self.script_code == "Deva" and (
                len(present_indic) != 4
                or present_indic != sorted(present_indic)
                or present_indic[-1] > cell.rect_mm.height
            ):
                raise ValueError("invalid_devanagari_regions")
            if self.extraction.margin_mm * 2 >= min(cell.rect_mm.width, cell.rect_mm.height):
                raise ValueError("extraction_margin_consumes_cell")
        return self


class CalibrationRegion(Contract):
    region_id: SafeID
    expected_codepoints: Annotated[list[Scalar], Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def unicode(self) -> Self:
        check_scalars(self.expected_codepoints)
        return self


class CalibrationDefinition(Contract):
    schema_version: Literal[1]
    calibration_id: SafeID
    version: SemVer
    script_code: ScriptCode
    prompt_text: Annotated[str, StringConstraints(min_length=1, max_length=10000)]
    target_codepoints: Annotated[list[Scalar], Field(min_length=1, max_length=4096)]
    expected_regions: list[CalibrationRegion] = Field(max_length=100)
    target_sequences: list[Codepoints] = Field(default_factory=list, exclude_if=lambda x: not x)
    localization_review: Literal["engineering_unreviewed", "reviewed"] | None = Field(
        default=None, exclude_if=lambda x: x is None
    )
    prompt_is_ground_truth: Literal[False] = False
    optimization_status: Literal["unmeasured", "evaluated"] = "unmeasured"
    evaluation_record_hash: Digest | None = None

    @model_validator(mode="after")
    def evidence(self) -> Self:
        check_scalars(self.target_codepoints)
        if self.optimization_status == "evaluated" and self.evaluation_record_hash is None:
            raise ValueError("evaluated_prompt_requires_evidence")
        return self


class ChildArtifact(Contract):
    artifact_id: UUIDString
    sha256: Digest
    size_bytes: Annotated[int, Field(strict=True, ge=1, le=2**40)]
    media_type: Literal[
        "application/json",
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/octet-stream",
        "application/onnx",
        "application/x-safetensors",
    ]


class ResultInput(Contract):
    sample_ids: list[UUIDString] = Field(min_length=1, max_length=200)
    source_hashes: list[Digest] = Field(min_length=1, max_length=200)
    template_manifest_hash: Digest | None = None

    @model_validator(mode="after")
    def matching_sources(self) -> Self:
        if len(self.sample_ids) != len(self.source_hashes):
            raise ValueError("source_hashes_must_match_samples")
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("duplicate_source_sample")
        return self


class PipelineProvenance(Contract):
    script_code: ScriptCode
    style: Literal["print", "cursive", "mixed", "unknown"]
    model_id: SafeID | None
    model_version: SemVer | None
    model_hash: Digest | None
    algorithm_version: SafeID
    preprocessing_version: SafeID

    @model_validator(mode="after")
    def model_provenance(self) -> Self:
        fields = (self.model_id, self.model_version, self.model_hash)
        if any(value is not None for value in fields) and any(value is None for value in fields):
            raise ValueError("incomplete_model_provenance")
        return self


class CandidateEvidence(Contract):
    recognition: Unit | None = None
    segmentation: Unit | None = None
    style_fidelity: Unit | None = None
    model_uncertainty: Unit | None = None
    outline_validity: Literal["pass", "fail", "unmeasured"]
    evaluation_record_hash: Digest | None = None

    @model_validator(mode="after")
    def measured_scores(self) -> Self:
        if any(
            x is not None
            for x in (
                self.recognition,
                self.segmentation,
                self.style_fidelity,
                self.model_uncertainty,
            )
        ):
            if self.evaluation_record_hash is None:
                raise ValueError("scores_require_evaluation_evidence")
        return self


class GlyphCandidate(Contract):
    candidate_id: UUIDString
    codepoints: Codepoints
    rank: Annotated[int, Field(strict=True, ge=1, le=10000)]
    origin: Literal[
        "authentic_extraction",
        "geometric_reconstruction",
        "style_conditioned_generation",
        "manual_drawing",
    ]
    outline: ChildArtifact
    evidence: CandidateEvidence
    review_status: Literal["pending"] = "pending"

    @model_validator(mode="after")
    def unicode(self) -> Self:
        check_scalars(self.codepoints)
        if self.outline.media_type != "application/json":
            raise ValueError("outline_reference_requires_json")
        return self


class MLResultManifest(Contract):
    schema_version: Literal[1]
    artifact_type: Literal["penforge.ml_result"]
    job_id: UUIDString
    attempt_id: UUIDString
    fencing_token: PositiveInt
    stage: SafeID
    input: ResultInput
    pipeline: PipelineProvenance
    candidates: list[GlyphCandidate] = Field(max_length=10000)
    quality_report: ChildArtifact
    artifact_hash: Digest

    @model_validator(mode="after")
    def publication_hash(self) -> Self:
        from .serialization import content_hash

        if len({c.candidate_id for c in self.candidates}) != len(self.candidates):
            raise ValueError("duplicate_candidate_id")
        ranks = [(tuple(c.codepoints), c.rank) for c in self.candidates]
        if len(set(ranks)) != len(ranks):
            raise ValueError("duplicate_candidate_rank")
        if self.quality_report.media_type != "application/json":
            raise ValueError("quality_report_reference_requires_json")
        references: dict[str, ChildArtifact] = {}
        for artifact in [
            self.quality_report,
            *(candidate.outline for candidate in self.candidates),
        ]:
            prior = references.setdefault(artifact.artifact_id, artifact)
            if artifact != prior:
                raise ValueError("conflicting_child_artifact_metadata")
        if (
            content_hash(self.model_dump(mode="json"), exclude_root_keys={"artifact_hash"})
            != self.artifact_hash
        ):
            raise ValueError("artifact_hash_mismatch")
        return self


class AnalyzeOptions(Contract):
    enhance: StrictBool = True
    fuse_samples: StrictBool = False
    quality_policy: Literal["accuracy_first"] = "accuracy_first"


class CaptureToFont(Contract):
    schema_version: Literal[1] = 1
    units_per_em: Annotated[int, Field(strict=True, ge=16, le=16384)] = 1000
    baseline_y: Unit
    units_per_px: Annotated[float, Field(strict=True, gt=0, le=100, allow_inf_nan=False)]
    origin_x_px: Annotated[float, Field(strict=True, ge=0, le=32768, allow_inf_nan=False)] = 0.0
    pen_width_px: Annotated[float, Field(strict=True, gt=0, le=100, allow_inf_nan=False)]


class CaptureAssignment(Contract):
    sample_id: UUIDString
    codepoints: Codepoints
    capture_transform: CaptureToFont

    @model_validator(mode="after")
    def isolated_latin(self) -> Self:
        from .unicode import capture_sequence

        capture_sequence(self.codepoints)
        return self


class AssetVerifyJob(Contract):
    schema_version: Literal[1]
    asset_id: UUIDString


class _AnalyzeSamplesBase(Contract):
    schema_version: Literal[1, 2]
    job_type: Literal["analyze_samples"]
    project_id: UUIDString
    sample_ids: list[UUIDString] = Field(min_length=1, max_length=200)
    script_code: ScriptCode
    template_manifest_hash: Digest | None = None
    options: AnalyzeOptions = Field(default_factory=AnalyzeOptions)
    assignments: list[CaptureAssignment] = Field(
        default_factory=list, max_length=200, exclude_if=lambda x: not x
    )

    @model_validator(mode="after")
    def distinct_samples(self) -> Self:
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("duplicate_source_sample")
        if self.script_code == "Latn" and any(
            len(a.codepoints) != 1 or not 33 <= a.codepoints[0] <= 126 for a in self.assignments
        ):
            raise ValueError("latin_assignment_requires_ascii")
        ids = [item.sample_id for item in self.assignments]
        if len(set(ids)) != len(ids) or any(item not in self.sample_ids for item in ids):
            raise ValueError("invalid_capture_assignments")
        return self


class AnalyzeSamplesJob(_AnalyzeSamplesBase):
    # Retain the v1 JSON schema byte-for-byte, including its absent description.
    schema_version: Literal[1]


class AnalyzeSamplesJobV2(_AnalyzeSamplesBase):
    """Explicit fixed-round brush geometry; old workers must not claim v2."""

    schema_version: Literal[2]
    drawing_mode: Literal["fixed_round_v1"]

    @model_validator(mode="after")
    def round_drawing_scope(self) -> Self:
        if self.script_code != "Latn" or {a.sample_id for a in self.assignments} != set(
            self.sample_ids
        ):
            raise ValueError("round_drawing_requires_latin_assignments")
        return self


class ImageRegionRect(Contract):
    x: Annotated[int, Field(strict=True, ge=0, le=6000)]
    y: Annotated[int, Field(strict=True, ge=0, le=6000)]
    width: Annotated[int, Field(strict=True, gt=0, le=6000)]
    height: Annotated[int, Field(strict=True, gt=0, le=6000)]


class ImageRegionAssignment(Contract):
    """An explicit user selection in the oriented immutable image pixel frame."""

    region_id: UUIDString
    sample_id: UUIDString
    source_sha256: Digest
    source_width: Annotated[int, Field(strict=True, gt=0, le=6000)]
    source_height: Annotated[int, Field(strict=True, gt=0, le=6000)]
    rect: ImageRegionRect
    codepoints: Codepoints
    baseline_y_px: Annotated[float, Field(strict=True, ge=0, le=6000, allow_inf_nan=False)]
    units_per_px: Annotated[float, Field(strict=True, gt=0, le=100, allow_inf_nan=False)]
    ink_threshold: Annotated[int, Field(strict=True, ge=0, le=255)] = 180

    @model_validator(mode="after")
    def bounded_region(self) -> Self:
        if len(self.codepoints) != 1 or not 33 <= self.codepoints[0] <= 126:
            raise ValueError("image_region_requires_latin_ascii")
        if (
            self.source_width * self.source_height > 16_000_000
            or self.rect.x + self.rect.width > self.source_width
            or self.rect.y + self.rect.height > self.source_height
            or self.rect.width * self.rect.height > 4_000_000
            or self.baseline_y_px > self.source_height
        ):
            raise ValueError("image_region_outside_source_or_budget")
        return self


class AnalyzeSamplesJobV3(Contract):
    """User-assigned image regions; no OCR or automatic character alignment."""

    schema_version: Literal[3]
    job_type: Literal["analyze_samples"]
    project_id: UUIDString
    sample_ids: list[UUIDString] = Field(min_length=1, max_length=8)
    script_code: Literal["Latn"]
    template_manifest_hash: None = None
    options: AnalyzeOptions = Field(default_factory=AnalyzeOptions)
    image_mode: Literal["manual_regions_v1"]
    image_regions: list[ImageRegionAssignment] = Field(min_length=1, max_length=96)

    @model_validator(mode="after")
    def distinct_regions(self) -> Self:
        ids = [region.region_id for region in self.image_regions]
        if len(ids) != len(set(ids)) or len(self.sample_ids) != len(set(self.sample_ids)):
            raise ValueError("duplicate_image_region_or_source")
        if {region.sample_id for region in self.image_regions} != set(self.sample_ids):
            raise ValueError("image_regions_require_all_sources")
        if sum(region.rect.width * region.rect.height for region in self.image_regions) > 8_000_000:
            raise ValueError("image_region_batch_budget_exceeded")
        source_settings: dict[str, tuple[str, int, int, float]] = {}
        for region in self.image_regions:
            settings = (
                region.source_sha256,
                region.source_width,
                region.source_height,
                region.units_per_px,
            )
            if source_settings.setdefault(region.sample_id, settings) != settings:
                raise ValueError("image_regions_require_common_source_scale")
        return self


AnalysisPayload = AnalyzeSamplesJob | AnalyzeSamplesJobV2 | AnalyzeSamplesJobV3


def parse_analysis_payload(value: object) -> AnalysisPayload:
    if isinstance(value, dict) and value.get("schema_version") == 3:
        return AnalyzeSamplesJobV3.model_validate(value)
    if isinstance(value, dict) and value.get("schema_version") == 2:
        return AnalyzeSamplesJobV2.model_validate(value)
    return AnalyzeSamplesJob.model_validate(value)


class CompileExportJob(Contract):
    schema_version: Literal[1]
    job_type: Literal["compile_export"]
    export_ids: list[UUIDString] = Field(min_length=1, max_length=3)
    snapshot_id: UUIDString
    formats: list[Literal["ttf", "otf", "woff2"]] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def distinct_exports(self) -> Self:
        if len(set(self.export_ids)) != len(self.export_ids) or len(set(self.formats)) != len(
            self.formats
        ):
            raise ValueError("duplicate_export_or_format")
        if len(self.export_ids) != len(self.formats):
            raise ValueError("exports_must_match_formats")
        return self


class TensorContract(Contract):
    name: SafeID
    dtype: Literal["float32", "float16", "int64", "int32", "uint8", "json"]
    shape: list[Annotated[int, Field(strict=True, ge=-1, le=2**31 - 1)]] = Field(max_length=8)
    preprocessing_version: SafeID


class MeasuredResources(Contract):
    peak_mem_mb: Nonnegative | None = None
    ms_per_unit_p95: Nonnegative | None = None
    benchmark_record_hash: Digest | None = None

    @model_validator(mode="after")
    def measured(self) -> Self:
        if (
            self.peak_mem_mb is not None or self.ms_per_unit_p95 is not None
        ) and self.benchmark_record_hash is None:
            raise ValueError("resource_measurements_require_benchmark")
        return self


class ModelRegistryEntry(Contract):
    schema_version: Literal[1]
    name: SafeID
    version: SemVer
    task: Literal["segmentation", "recognition", "generation", "style_encoder", "refiner", "rules"]
    script_codes: list[ScriptCode] = Field(min_length=1, max_length=100)
    format: Literal["onnx", "safetensors", "json_rules"]
    artifact: ChildArtifact
    input_contract: TensorContract
    output_contract: TensorContract
    compatible_worker: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    resource_profile: MeasuredResources
    license_identifier: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    dataset_manifest_hashes: list[Digest] = Field(max_length=1000)
    training_run_id: SafeID | None = None
    benchmark_record_hashes: list[Digest] = Field(max_length=1000)
    status: Literal["candidate", "approved", "deprecated", "revoked"] = "candidate"
    release_label: Literal[
        "research_only", "experimental_opt_in", "production_validated", "temporarily_disabled"
    ] = "research_only"

    @model_validator(mode="after")
    def validated_model(self) -> Self:
        accepted_media = {
            "onnx": {"application/onnx", "application/octet-stream"},
            "safetensors": {"application/x-safetensors", "application/octet-stream"},
            "json_rules": {"application/json"},
        }
        if self.artifact.media_type not in accepted_media[self.format]:
            raise ValueError("model_artifact_media_mismatch")
        if self.status == "approved" or self.release_label == "production_validated":
            if not self.benchmark_record_hashes:
                raise ValueError("model_promotion_requires_benchmarks")
        return self


class ProjectDTO(Contract):
    """Cloud project metadata; style controls are display metadata, not glyph output."""

    id: UUIDString
    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    script_code: ScriptCode
    status: Literal["draft", "processing", "ready", "archived", "deleting"]
    row_version: PositiveInt
    updated_at: AwareDatetime
    active_snapshot_id: UUIDString | None = None
    created_at: AwareDatetime | None = None
    style_controls: dict[str, Finite] = Field(default_factory=dict)

    @model_validator(mode="after")
    def utc_time(self) -> Self:
        offset = self.updated_at.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("timestamp_requires_utc")
        return self


class WorkProgress(Contract):
    completed_units: Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]
    total_units: Annotated[int, Field(strict=True, ge=1, le=2**53 - 1)] | None
    basis: Literal["validated_units"] = "validated_units"
    plan_revision: PositiveInt = 1
    denominator_change_reason: SafeID | None = None

    @model_validator(mode="after")
    def counts(self) -> Self:
        if self.total_units is not None and self.completed_units > self.total_units:
            raise ValueError("completed_units_exceed_total")
        return self


class JobETA(Contract):
    lower_seconds: Nonnegative | None = None
    upper_seconds: Nonnegative | None = None
    benchmark_profile_id: SafeID | None = None

    @model_validator(mode="after")
    def measured(self) -> Self:
        if self.lower_seconds is not None or self.upper_seconds is not None:
            if (
                self.lower_seconds is None
                or self.upper_seconds is None
                or self.benchmark_profile_id is None
            ):
                raise ValueError("eta_requires_range_and_benchmark")
            if self.lower_seconds > self.upper_seconds:
                raise ValueError("invalid_eta_range")
        return self


class JobDTO(Contract):
    id: UUIDString
    job_type: Literal[
        "asset_verify",
        "analyze_samples",
        "infer_style_generate",
        "compile_export",
        "purge_project",
        "purge_account",
        "maintenance_reconcile",
    ]
    state: Literal["queued", "running", "cancel_requested", "succeeded", "failed", "canceled"]
    stage: SafeID | None = None
    progress: WorkProgress | None = None
    eta: JobETA = Field(default_factory=JobETA)
    requires_user_action: StrictBool = False
    error_code: SafeID | None = None
    result_artifact_id: UUIDString | None = None


class ContractVersion(Contract):
    name: SafeID
    current_version: PositiveInt
    supported_versions: list[PositiveInt] = Field(min_length=1, max_length=2)
    schema_name: SafeID
    capability: Literal["foundation_only", "future_contract"]


class ContractRegistry(Contract):
    rest_api_version: Literal["v1"] = "v1"
    hash_encoding: Literal["penforge-tagged-json-ieee754-v1"] = "penforge-tagged-json-ieee754-v1"
    contracts: list[ContractVersion]


CONTRACT_MODELS: dict[str, type[Contract]] = {
    "glyph_outline": GlyphOutline,
    "stroke_document": StrokeDocument,
    "template_manifest": TemplateManifest,
    "calibration_definition": CalibrationDefinition,
    "ml_result_manifest": MLResultManifest,
    "asset_verify_job": AssetVerifyJob,
    "analyze_samples_job": AnalyzeSamplesJob,
    "compile_export_job": CompileExportJob,
    "model_registry_entry": ModelRegistryEntry,
    "project_dto": ProjectDTO,
    "job_dto": JobDTO,
}


def contract_registry() -> ContractRegistry:
    return ContractRegistry(
        contracts=[
            ContractVersion(
                name=name,
                current_version=2 if name == "analyze_samples_job" else 1,
                supported_versions=[1, 2] if name == "analyze_samples_job" else [1],
                schema_name=AnalyzeSamplesJobV2.__name__
                if name == "analyze_samples_job"
                else model.__name__,
                capability="future_contract",
            )
            for name, model in CONTRACT_MODELS.items()
        ]
        + [
            ContractVersion(
                name="analyze_image_regions_job",
                current_version=3,
                supported_versions=[3],
                schema_name=AnalyzeSamplesJobV3.__name__,
                capability="future_contract",
            )
        ]
    )


def register_contract_schemas(openapi: dict) -> dict:
    """Register schemas without adding unimplemented HTTP operations."""

    def semantic(value: object) -> object:
        # FastAPI removes null defaults from nested request schemas. Preserve the
        # canonical schema while still rejecting any actual constraint collision.
        if isinstance(value, dict):
            return {
                key: semantic(child)
                for key, child in value.items()
                if not (key == "default" and child is None)
            }
        if isinstance(value, list):
            return [semantic(child) for child in value]
        return value

    components = openapi.setdefault("components", {}).setdefault("schemas", {})
    models: list[type[Contract]] = list(CONTRACT_MODELS.values())
    models.extend([ContractRegistry, AnalyzeSamplesJobV2, AnalyzeSamplesJobV3])
    for model in models:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        definitions = schema.pop("$defs", {})
        for name, definition in {**definitions, model.__name__: schema}.items():
            if name in components and semantic(components[name]) != semantic(definition):
                raise ValueError(f"schema_name_collision:{name}")
            components[name] = definition
    return openapi


from .indic import IndicEvidence  # noqa: E402

GlyphOutline.model_rebuild()
