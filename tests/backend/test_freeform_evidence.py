"""Authored inert evidence only. No model execution, private data or publication."""

import copy
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from penforge_contracts import (
    MLResultManifest,
    StrokeDocument,
    content_hash,
    parse_analysis_payload,
)
from penforge_contracts.freeform import FreeformAlignment, FreeformAnalysisInput, FreeformMethods

from packages.ml_core.freeform import analyze_with_adapter, bind_canvas_alignment


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def seal(value):
    value["artifact_hash"] = content_hash(value, exclude_root_keys={"artifact_hash"})
    return value


def span(stroke, start=0, end=1):
    return dict(stroke_id=stroke, start_point=start, end_point=end)


def text_span(start, end, hypothesis="text-1"):
    return dict(hypothesis_id=hypothesis, start=start, end=end)


def bounds(left, top, right, bottom):
    return dict(x_min=left, y_min=top, x_max=right, y_max=bottom)


@pytest.fixture
def authored():
    # One immutable word capture. The i dot comes after both l strokes, and two
    # authentic l occurrences remain distinct. These marks are not a recognizer.
    doc = StrokeDocument.model_validate(
        dict(
            schema_version=1,
            coordinate_system={},
            capture_size_px=dict(width=100, height=100),
            timing="observed",
            strokes=[
                dict(
                    stroke_id=sid,
                    pointer_type="mouse",
                    points=[dict(x=x, y=y, t_ms=i * 1000 + j) for j, (x, y) in enumerate(points)],
                )
                for i, (sid, points) in enumerate(
                    [
                        ("stem-i", [(0.1, 0.5), (0.1, 0.8)]),
                        ("first-l", [(0.3, 0.2), (0.3, 0.8)]),
                        ("second-l", [(0.5, 0.2), (0.5, 0.8)]),
                        ("dot-i", [(0.1, 0.35)]),
                    ]
                )
            ],
        )
    )
    data = encoded(doc.model_dump(mode="json"))
    request = FreeformAnalysisInput.model_validate(
        dict(
            schema_version=1,
            source=dict(
                project_id="00000000-0000-4000-8000-000000000001",
                sample_id="00000000-0000-4000-8000-000000000002",
                asset_id="00000000-0000-4000-8000-000000000003",
                source_sha256="sha256:" + hashlib.sha256(data).hexdigest(),
                document_hash=content_hash(doc.model_dump(mode="json")),
                capture_size_px=dict(width=100, height=100),
            ),
            transcription=dict(text="ill"),
        )
    )
    methods = FreeformMethods.model_validate(
        dict(
            recognition=dict(
                component_id="inert-authored-recognition",
                component_version="1.0.0",
                implementation_hash="sha256:" + "a" * 64,
            ),
            alignment=dict(
                component_id="inert-authored-alignment",
                component_version="1.0.0",
                implementation_hash="sha256:" + "b" * 64,
            ),
        )
    )
    evidence = seal(
        dict(
            schema_version=1,
            artifact_type="penforge.freeform_alignment",
            input=request.model_dump(mode="json"),
            methods=methods.model_dump(mode="json"),
            coordinate_frame="original_capture_pixels_top_left_y_down",
            analysis_to_source_transform="identity",
            text_offset_unit="unicode_scalar",
            capability_status="research_only",
            review_status="pending",
            hypotheses=[dict(hypothesis_id="text-1", text="ill", recognition=None)],
            lines=[
                dict(
                    line_id="line-1",
                    bounds=bounds(0, 0, 100, 100),
                    baseline_y_px=80,
                    text_span=text_span(0, 3),
                )
            ],
            words=[
                dict(
                    word_id="word-1",
                    line_id="line-1",
                    bounds=bounds(5, 10, 55, 90),
                    text_span=text_span(0, 3),
                )
            ],
            regions=[
                dict(
                    region_id=rid,
                    word_id="word-1",
                    bounds=rect,
                    source_spans=spans,
                    assignment="proposed_character",
                    text_span=text_span(i, i + 1),
                    segmentation=None,
                    review_reasons=[],
                )
                for i, (rid, rect, spans) in enumerate(
                    [
                        ("i-1", bounds(8, 30, 12, 85), [span("stem-i"), span("dot-i", 0, 0)]),
                        ("l-1", bounds(28, 15, 32, 85), [span("first-l")]),
                        ("l-2", bounds(48, 15, 52, 85), [span("second-l")]),
                    ]
                )
            ],
        )
    )
    return data, request, methods, evidence


def bind(authored, evidence=None):
    data, request, methods, original = authored
    return bind_canvas_alignment(data, request, methods, encoded(evidence or original))


def test_multiple_letters_delayed_marks_repeats_are_bound_without_source_mutation(authored):
    data, request, methods, evidence = authored
    frozen = copy.deepcopy(evidence)
    result = bind(authored)
    assert result.proposed_region_ids == ("i-1", "l-1", "l-2")
    assert result.unresolved_region_ids == ()
    assert all(
        "identity_and_alignment_unqualified" in flags for _, flags in result.required_reviews
    )
    assert (
        FreeformAlignment.model_validate_json(result.evidence_json).model_dump(mode="json")
        == frozen
    )
    assert result == bind(authored) and evidence == frozen
    assert request.source.source_sha256 == "sha256:" + hashlib.sha256(data).hexdigest()
    assert all(
        p.pressure is None
        for s in StrokeDocument.model_validate_json(data).strokes
        for p in s.points
    )
    assert "ill" not in repr(result) and "evidence_json" not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.artifact_hash = "changed"
    # A binding result is not a publishable glyph manifest or a v1/v2 analysis job.
    with pytest.raises(ValueError):
        MLResultManifest.model_validate_json(result.evidence_json)
    with pytest.raises(ValueError):
        parse_analysis_payload(json.loads(result.evidence_json))


def test_unresolved_connected_region_keeps_all_ink_without_character_assignment(authored):
    evidence = copy.deepcopy(authored[3])
    evidence["regions"] = [
        dict(
            region_id="joined-word",
            word_id="word-1",
            bounds=evidence["words"][0]["bounds"],
            source_spans=[s for r in evidence["regions"] for s in r["source_spans"]],
            assignment="unresolved",
            text_span=text_span(0, 3),
            segmentation=None,
            review_reasons=["connected_ink", "alignment_ambiguous"],
        )
    ]
    result = bind(authored, seal(evidence))
    assert not result.proposed_region_ids and result.unresolved_region_ids == ("joined-word",)
    assert "connected_ink" in result.required_reviews[0][1]


def test_transcription_never_supplies_identity_or_overwrites_competing_recognition(authored):
    evidence = copy.deepcopy(authored[3])
    evidence["hypotheses"].append(dict(hypothesis_id="text-2", text="lll", recognition=None))
    evidence["regions"][0]["text_span"] = text_span(0, 1, "text-2")
    # Different hypotheses cannot be silently mixed within a parent text span.
    with pytest.raises(ValueError, match="text_span_outside_parent"):
        bind(authored, seal(evidence))
    evidence["words"][0]["text_span"] = None
    result = bind(authored, seal(evidence))
    assert "transcription_mismatch" in dict(result.required_reviews)["i-1"]
    assert [
        h.text for h in FreeformAlignment.model_validate_json(result.evidence_json).hypotheses
    ] == ["ill", "lll"]
    evidence["methods"]["recognition"] = None
    with pytest.raises(ValueError, match="recognition_provenance"):
        bind(authored, seal(evidence))


@pytest.mark.parametrize(
    "path,value,reason",
    [
        (("schema_version",), True, "schema_version_requires_integer"),
        (("schema_version",), 2, "literal_error"),
        (("review_status",), "approved", "literal_error"),
        (("capability_status",), "production_validated", "literal_error"),
        (("analysis_to_source_transform",), "resize", "literal_error"),
        (("text_offset_unit",), "utf16", "literal_error"),
        (("input", "source", "source_type"), "freeform_photo", "literal_error"),
        (("input", "transcription", "prompt_is_ground_truth"), True, "literal_error"),
        (("hypotheses", 0, "text"), "अ", "english_text_required"),
        (("hypotheses", 0, "text"), "\ud800", "string_unicode"),
        (("regions", 0, "region_id"), "l-1", "duplicate_freeform_identity"),
        (("regions", 0, "word_id"), "other", "outside_word"),
        (("words", 0, "line_id"), "other", "outside_line"),
        (("lines", 0, "baseline_y_px"), 101, "baseline_outside_capture"),
        (("regions", 0, "bounds", "x_min"), "8", "float_type"),
        (("regions", 0, "bounds", "y_max"), 101, "outside_word"),
        (("regions", 0, "text_span", "end"), 4, "out_of_bounds_hypothesis_span"),
        (("regions", 0, "text_span", "end"), 2, "requires_one_written_ascii_character"),
        (("regions", 0, "source_spans", 0, "stroke_id"), "unknown", "unknown_source_point"),
        (("regions", 0, "source_spans", 0, "end_point"), 2, "unknown_source_point"),
        (("regions", 0, "bounds", "y_min"), 40, "ink_outside_region"),
    ],
)
def test_invalid_or_forged_evidence_fails_closed(authored, path, value, reason):
    evidence = copy.deepcopy(authored[3])
    target = evidence
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    # Unpaired surrogates are rejected by the schema before canonical hashing.
    if value == "\ud800":
        with pytest.raises(ValueError, match=reason):
            FreeformAlignment.model_validate(evidence)
    else:
        with pytest.raises(ValueError, match=reason):
            bind(authored, seal(evidence))


@pytest.mark.parametrize("operation", ["reuse", "gap", "tail", "missing_stroke"])
def test_no_ink_is_reused_or_silently_discarded(authored, operation):
    evidence = copy.deepcopy(authored[3])
    spans = evidence["regions"][0]["source_spans"]
    if operation == "reuse":
        spans.append(copy.deepcopy(spans[0]))
    elif operation == "gap":
        spans[0]["start_point"] = 1
    elif operation == "tail":
        spans[0]["end_point"] = 0
    else:
        spans.pop()
    with pytest.raises(ValueError, match="points_reused_or_omitted"):
        bind(authored, seal(evidence))


def test_splitting_connected_stroke_cannot_lose_its_required_review(authored):
    evidence = copy.deepcopy(authored[3])
    region = evidence["regions"][1]
    region["source_spans"] = [span("first-l", 0, 0)]
    remainder = copy.deepcopy(region)
    remainder.update(
        region_id="unresolved-tail",
        assignment="unresolved",
        text_span=None,
        source_spans=[span("first-l", 1, 1)],
        review_reasons=["connected_ink"],
    )
    evidence["regions"].append(remainder)
    result = bind(authored, seal(evidence))
    assert "stroke_split_unvalidated" in dict(result.required_reviews)["l-1"]
    assert "stroke_split_unvalidated" in dict(result.required_reviews)["unresolved-tail"]


def test_hash_and_source_method_context_binding_are_independent(authored):
    data, request, methods, original = authored
    evidence = copy.deepcopy(original)
    evidence["lines"][0]["baseline_y_px"] = 79
    with pytest.raises(ValueError, match="artifact_hash_mismatch"):
        bind(authored, evidence)
    evidence = copy.deepcopy(original)
    evidence["methods"]["alignment"]["implementation_hash"] = "sha256:" + "c" * 64
    with pytest.raises(ValueError, match="method_mismatch"):
        bind(authored, seal(evidence))
    for field in ("asset_id", "sample_id", "project_id"):
        evidence = copy.deepcopy(original)
        evidence["input"]["source"][field] = "00000000-0000-4000-8000-000000000099"
        with pytest.raises(ValueError, match="input_mismatch"):
            bind(authored, seal(evidence))
    evidence = copy.deepcopy(original)
    evidence["input"]["transcription"]["text"] = "lll"
    with pytest.raises(ValueError, match="input_mismatch"):
        bind(authored, seal(evidence))
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        bind_canvas_alignment(data + b" ", request, methods, encoded(original))
    for field, value, reason in [
        ("document_hash", "sha256:" + "e" * 64, "document_hash_mismatch"),
        ("capture_size_px", dict(width=101, height=100), "dimensions_mismatch"),
    ]:
        changed = request.model_dump(mode="json")
        changed["source"][field] = value
        with pytest.raises(ValueError, match=reason):
            bind_canvas_alignment(
                data, FreeformAnalysisInput.model_validate(changed), methods, encoded(original)
            )


def test_no_default_adapter_and_inert_adapter_is_replaceable(authored):
    data, request, methods, evidence = authored
    with pytest.raises(ValueError, match="analysis_unavailable"):
        analyze_with_adapter(data, request, methods)

    class InertAdapter:
        def analyze(self, request_json, source_bytes, /):
            assert json.loads(request_json) == request.model_dump(mode="json")
            assert source_bytes is data
            return encoded(evidence)

    assert analyze_with_adapter(data, request, methods, adapter=InertAdapter()) == bind(authored)


def test_adapter_cannot_mutate_the_callers_pinned_method_before_binding(authored):
    data, request, methods, original = authored

    class MutatingInertAdapter:
        def analyze(self, request_json, source_bytes, /):
            methods.alignment.implementation_hash = "sha256:" + "c" * 64
            changed = copy.deepcopy(original)
            changed["methods"] = methods.model_dump(mode="json")
            return encoded(seal(changed))

    with pytest.raises(ValueError, match="method_mismatch"):
        analyze_with_adapter(data, request, methods, adapter=MutatingInertAdapter())


def test_two_proposed_letters_cannot_claim_the_same_recognized_text_position(authored):
    evidence = copy.deepcopy(authored[3])
    evidence["regions"][2]["text_span"] = text_span(1, 2)
    with pytest.raises(ValueError, match="text_position_assigned_twice"):
        bind(authored, seal(evidence))


def test_recognition_can_be_absent_while_unresolved_ink_is_preserved(authored):
    data, request, methods, evidence = authored
    evidence = copy.deepcopy(evidence)
    evidence["methods"]["recognition"] = None
    evidence["hypotheses"] = []
    for region in [*evidence["lines"], *evidence["words"], *evidence["regions"]]:
        region["text_span"] = None
    for region in evidence["regions"]:
        region.update(assignment="unresolved", review_reasons=["identity_ambiguous"])
    methods = FreeformMethods.model_validate(evidence["methods"])
    result = bind_canvas_alignment(data, request, methods, encoded(seal(evidence)))
    assert result.unresolved_region_ids == ("i-1", "l-1", "l-2")
    assert result.proposed_region_ids == ()


@pytest.mark.parametrize("kind", ["derived", "legacy"])
def test_first_version_rejects_edited_and_legacy_sources_without_fabricating_observations(
    authored, kind
):
    data, request, methods, evidence = authored
    doc = json.loads(data)
    if kind == "derived":
        doc["parent_source_hash"] = "sha256:" + "d" * 64
        doc["operations"] = [dict(type="translate", stroke_ids=["stem-i"], dx=0.0, dy=0.0)]
    else:
        doc.update(timing="unavailable_legacy", capture_size_px=None)
        for stroke in doc["strokes"]:
            stroke["pointer_type"] = None
            for point in stroke["points"]:
                point["t_ms"] = None
    document = StrokeDocument.model_validate(doc)
    raw = encoded(document.model_dump(mode="json"))
    updated = request.model_dump(mode="json")
    updated["source"].update(
        source_sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
        document_hash=content_hash(document.model_dump(mode="json")),
    )
    with pytest.raises(ValueError, match="original_observed_capture|dimensions_mismatch"):
        bind_canvas_alignment(
            raw, FreeformAnalysisInput.model_validate(updated), methods, encoded(evidence)
        )


@pytest.mark.parametrize("kind", ["regions", "spans", "unresolved_reason", "empty_bounds"])
def test_graph_and_aggregate_budgets_are_enforced(authored, kind):
    evidence = copy.deepcopy(authored[3])
    if kind == "regions":
        evidence["regions"] *= 200
        reason = "too_long"
    elif kind == "spans":
        evidence["regions"] = [
            dict(evidence["regions"][0], region_id=f"r{i}", source_spans=[span("stem-i")] * 2000)
            for i in range(5)
        ]
        reason = "span_budget"
    elif kind == "unresolved_reason":
        evidence["regions"][0].update(assignment="unresolved", review_reasons=[])
        reason = "unresolved_ink_requires_review_reason"
    else:
        evidence["regions"][0]["bounds"]["x_max"] = 8
        reason = "empty_capture_bounds"
    with pytest.raises(ValueError, match=reason):
        bind(authored, seal(evidence))


def test_byte_span_and_score_budgets_and_unknown_fields(authored, monkeypatch):
    import packages.ml_core.freeform as binding

    data, request, methods, original = authored
    evidence = copy.deepcopy(original)
    evidence["hypotheses"][0]["recognition"] = dict(value=0.9)
    with pytest.raises(ValueError, match="evaluation_record_hash"):
        bind(authored, seal(evidence))
    for value in (float("nan"), float("inf"), -0.1, 1.1, "0.9"):
        evidence["hypotheses"][0]["recognition"] = dict(
            value=value, evaluation_record_hash="sha256:" + "e" * 64
        )
        with pytest.raises(ValueError):
            FreeformAlignment.model_validate(evidence)
    evidence = copy.deepcopy(original)
    evidence["regions"][0]["outline"] = {"stock_font": "unacceptable"}
    with pytest.raises(ValueError, match="extra_forbidden"):
        bind(authored, seal(evidence))
    monkeypatch.setattr(binding, "MAX_ALIGNMENT_BYTES", 1)
    with pytest.raises(ValueError, match="alignment_byte_budget"):
        bind(authored)
    monkeypatch.setattr(binding, "MAX_SOURCE_BYTES", 1)
    with pytest.raises(ValueError, match="source_byte_budget"):
        bind(authored)


def test_no_model_or_geometry_framework_is_imported_by_the_preparation_boundary():
    code = """
import builtins
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'torch', 'transformers', 'onnxruntime', 'safetensors', 'cv2', 'pathops'}:
        raise AssertionError('prohibited execution dependency: ' + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
from packages.ml_core.freeform import analyze_with_adapter
from penforge_contracts import FreeformAlignment
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=dict(
            os.environ, PYTHONPATH=os.pathsep.join([str(root / "packages/contracts"), str(root)])
        ),
    )
    assert result.returncode == 0, result.stderr
