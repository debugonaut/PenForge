"""Bind unqualified alignment to original observed ink; no model or glyph publication.

The caller must authorize the source and pin the method identities independently.
Source IDs/hashes and a successful binding do not grant account or worker access.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from penforge_contracts import StrokeDocument, content_hash
from penforge_contracts.freeform import (
    FreeformAlignment,
    FreeformAnalysisInput,
    FreeformMethods,
)

MAX_SOURCE_BYTES = 24_000_000
MAX_ALIGNMENT_BYTES = 2_000_000


class FreeformAnalysisAdapter(Protocol):
    """Implementations return evidence bytes, never approved glyphs.

    There is no installed/default recognizer. Future worker integration must qualify
    and isolate an implementation before invoking it on private handwriting.
    """

    def analyze(self, request_json: bytes, source_bytes: bytes, /) -> bytes: ...


@dataclass(frozen=True)
class BoundFreeformAlignment:
    evidence_json: bytes = field(repr=False)
    artifact_hash: str
    proposed_region_ids: tuple[str, ...]
    unresolved_region_ids: tuple[str, ...]
    required_reviews: tuple[tuple[str, tuple[str, ...]], ...]


def _source_document(source_bytes: bytes, request: FreeformAnalysisInput) -> StrokeDocument:
    if type(source_bytes) is not bytes or not 0 < len(source_bytes) <= MAX_SOURCE_BYTES:
        raise ValueError("freeform_source_byte_budget_exceeded")
    if "sha256:" + hashlib.sha256(source_bytes).hexdigest() != request.source.source_sha256:
        raise ValueError("freeform_source_hash_mismatch")
    document = StrokeDocument.model_validate_json(source_bytes)
    if content_hash(document.model_dump(mode="json")) != request.source.document_hash:
        raise ValueError("freeform_document_hash_mismatch")
    if document.capture_size_px != request.source.capture_size_px:
        raise ValueError("freeform_capture_dimensions_mismatch")
    if document.timing != "observed" or document.operations or document.parent_source_hash:
        raise ValueError("freeform_original_observed_capture_required")
    return document


def bind_canvas_alignment(
    source_bytes: bytes,
    request: FreeformAnalysisInput,
    methods: FreeformMethods,
    evidence_json: bytes,
) -> BoundFreeformAlignment:
    """Verify source membership and accounting without guessing letter boundaries.

    All points must occur exactly once, either in proposed characters or unresolved
    regions. Bounds describe centerline evidence, not a crop mask or expanded brush.
    Partial-stroke spans retain a mandatory split review; this function never cuts
    them into a fabricated independent StrokeDocument or emits a font outline.
    """
    # Revalidate even Pydantic instances: callers may have mutated nested values.
    request = FreeformAnalysisInput.model_validate(request.model_dump(mode="json"))
    methods = FreeformMethods.model_validate(methods.model_dump(mode="json"))
    document = _source_document(source_bytes, request)
    if type(evidence_json) is not bytes or not 0 < len(evidence_json) <= MAX_ALIGNMENT_BYTES:
        raise ValueError("freeform_alignment_byte_budget_exceeded")
    evidence = FreeformAlignment.model_validate_json(evidence_json)
    if evidence.input != request:
        raise ValueError("freeform_input_mismatch")
    if evidence.methods != methods:
        raise ValueError("freeform_method_mismatch")
    strokes = {stroke.stroke_id: stroke for stroke in document.strokes}
    intervals: dict[str, list[tuple[int, int]]] = {sid: [] for sid in strokes}
    for region in evidence.regions:
        for span in region.source_spans:
            stroke = strokes.get(span.stroke_id)
            if stroke is None or span.end_point >= len(stroke.points):
                raise ValueError("freeform_unknown_source_point")
            intervals[span.stroke_id].append((span.start_point, span.end_point))
    for sid, spans in intervals.items():
        next_point = 0
        for start, end in sorted(spans):
            if start != next_point:
                raise ValueError("freeform_source_points_reused_or_omitted")
            next_point = end + 1
        if next_point != len(strokes[sid].points):
            raise ValueError("freeform_source_points_reused_or_omitted")

    hypotheses = {h.hypothesis_id: h.text for h in evidence.hypotheses}
    proposed, unresolved, reviews = [], [], []
    size = request.source.capture_size_px
    for region in evidence.regions:
        reasons: set[str] = set(region.review_reasons)
        for span in region.source_spans:
            stroke = strokes[span.stroke_id]
            if span.start_point != 0 or span.end_point != len(stroke.points) - 1:
                reasons.add("stroke_split_unvalidated")
            for point in stroke.points[span.start_point : span.end_point + 1]:
                x, y = point.x * size.width, point.y * size.height
                bounds = region.bounds
                if not (bounds.x_min <= x <= bounds.x_max and bounds.y_min <= y <= bounds.y_max):
                    raise ValueError("freeform_ink_outside_region")
        if region.assignment == "proposed_character":
            proposed.append(region.region_id)
            reasons.add("identity_and_alignment_unqualified")
        else:
            unresolved.append(region.region_id)
        if (
            request.transcription is not None
            and region.text_span is not None
            and hypotheses[region.text_span.hypothesis_id] != request.transcription.text
        ):
            reasons.add("transcription_mismatch")
        reviews.append((region.region_id, tuple(sorted(reasons))))
    return BoundFreeformAlignment(
        evidence_json=evidence.model_dump_json().encode(),
        artifact_hash=evidence.artifact_hash,
        proposed_region_ids=tuple(proposed),
        unresolved_region_ids=tuple(unresolved),
        required_reviews=tuple(reviews),
    )


def analyze_with_adapter(
    source_bytes: bytes,
    request: FreeformAnalysisInput,
    methods: FreeformMethods,
    *,
    adapter: FreeformAnalysisAdapter | None = None,
) -> BoundFreeformAlignment:
    """Preparation seam only; no API/worker calls this function in normal creation."""
    request = FreeformAnalysisInput.model_validate(request.model_dump(mode="json"))
    methods = FreeformMethods.model_validate(methods.model_dump(mode="json"))
    _source_document(source_bytes, request)
    if adapter is None:
        raise ValueError("freeform_analysis_unavailable")
    result = adapter.analyze(request.model_dump_json().encode(), source_bytes)
    return bind_canvas_alignment(source_bytes, request, methods, result)
