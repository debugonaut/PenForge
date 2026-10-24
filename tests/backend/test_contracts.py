import copy
import json
from pathlib import Path

import pytest
from penforge_contracts import (
    AnalyzeSamplesJob,
    CalibrationDefinition,
    CompileExportJob,
    GlyphOutline,
    JobDTO,
    MLResultManifest,
    ModelRegistryEntry,
    StrokeDocument,
    TemplateManifest,
    canonical_bytes,
    content_hash,
)
from pydantic import ValidationError

FIXTURES = Path(__file__).resolve().parents[2] / "packages/contracts/fixtures"
UUID = "00000000-0000-4000-8000-000000000001"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.v1.json").read_text())


@pytest.mark.parametrize(
    "name,model",
    [
        ("outline", GlyphOutline),
        ("space", GlyphOutline),
        ("strokes", StrokeDocument),
        ("legacy-strokes", StrokeDocument),
        ("template", TemplateManifest),
        ("calibration", CalibrationDefinition),
        ("calibration-latin", CalibrationDefinition),
        ("analysis-drawing", AnalyzeSamplesJob),
        ("ml-result", MLResultManifest),
        ("model-registry", ModelRegistryEntry),
    ],
)
def test_valid_contract_fixture_round_trip(name, model):
    value = fixture(name)
    assert model.model_validate_json(json.dumps(value)).model_dump(mode="json") == value


def test_curves_holes_descenders_non_bmp_and_space_are_preserved():
    outline = GlyphOutline.model_validate(fixture("outline"))
    assert outline.codepoints == [0x1D4B6]
    assert len(outline.contours) == 2
    assert outline.fill_rule == "evenodd"
    assert outline.contours[0].start.y < 0
    assert outline.contours[0].segments[0].type == "cubic"
    assert outline.contours[0].segments[1].type == "quadratic"
    space = GlyphOutline.model_validate(fixture("space"))
    assert space.metrics.advance_width > 0
    assert space.contours == []


@pytest.mark.parametrize("version", [0, 2, True, 1.0, "1"])
def test_unknown_or_coerced_versions_rejected(version):
    outline = fixture("outline")
    outline["schema_version"] = version
    with pytest.raises(ValidationError):
        GlyphOutline.model_validate(outline)


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), -float("inf"), 9000, "10"])
def test_nonfinite_unsafe_or_coerced_geometry_rejected(coordinate):
    outline = fixture("outline")
    outline["contours"][0]["start"]["x"] = coordinate
    with pytest.raises(ValidationError):
        GlyphOutline.model_validate(outline)


def test_geometry_versions_nodes_unicode_and_contour_closure():
    for field, value in [
        ("codepoints", [0xD800]),
        ("codepoints", [0x110000]),
        ("codepoints", [65, 66]),
        ("contours", []),
    ]:
        outline = fixture("outline")
        outline[field] = value
        with pytest.raises(ValidationError):
            GlyphOutline.model_validate(outline)
    outline = fixture("outline")
    outline["codepoints"] = [0x915, 0x94D, 0x937]
    outline["mapping"] = "gsub_only"
    assert GlyphOutline.model_validate(outline).codepoints == [0x915, 0x94D, 0x937]
    for mutate in [
        lambda value: value.update(unknown=True),
        lambda value: value["contours"][0].update(closed=False),
        lambda value: value["contours"][0]["segments"][-1]["to"].update(y=-181),
        lambda value: value["contours"][0]["segments"][0].update(type="javascript"),
    ]:
        value = fixture("outline")
        mutate(value)
        with pytest.raises(ValidationError):
            GlyphOutline.model_validate(value)
    with pytest.raises(ValidationError, match="node_budget"):
        GlyphOutline.model_validate(fixture("outline"), context={"max_outline_nodes": 3})


def test_missing_pressure_round_trips_without_fabrication_and_raw_hash_stays_immutable():
    raw = fixture("strokes")
    original_hash = content_hash(raw)
    capture = StrokeDocument.model_validate(raw)
    assert capture.strokes[0].points[0].pressure == 0.4
    assert capture.strokes[0].points[1].pressure is None
    assert capture.strokes[1].pointer_type == "mouse"
    assert capture.strokes[1].points[0].pressure is None
    derived = copy.deepcopy(raw)
    derived["parent_source_hash"] = original_hash
    derived["operations"] = [{"type": "translate", "stroke_ids": ["s1"], "dx": 0.02, "dy": 0}]
    StrokeDocument.model_validate(derived)
    assert content_hash(raw) == original_hash
    assert content_hash(derived) != original_hash
    assert all(
        p.t_ms is None
        for s in StrokeDocument.model_validate(fixture("legacy-strokes")).strokes
        for p in s.points
    )


def test_legacy_capture_cannot_claim_observed_data_or_derived_edit_without_lineage():
    legacy = fixture("legacy-strokes")
    legacy["strokes"][0]["points"][0]["pressure"] = 0.5
    with pytest.raises(ValidationError, match="cannot_invent"):
        StrokeDocument.model_validate(legacy)
    legacy = fixture("legacy-strokes")
    legacy["strokes"][0]["pointer_type"] = "pen"
    with pytest.raises(ValidationError, match="cannot_invent"):
        StrokeDocument.model_validate(legacy)
    raw = fixture("strokes")
    raw["operations"] = [{"type": "translate", "stroke_ids": ["s1"], "dx": 0, "dy": 0}]
    with pytest.raises(ValidationError, match="parent_hash"):
        StrokeDocument.model_validate(raw)


def test_observed_stroke_order_matches_relative_capture_time():
    raw = fixture("strokes")
    raw["strokes"][1]["points"][0]["t_ms"] = 2
    with pytest.raises(ValidationError, match="stroke_order_not_monotonic"):
        StrokeDocument.model_validate(raw)


@pytest.mark.parametrize("mutation", ["timestamps", "range", "duplicate", "budget", "unknown"])
def test_invalid_strokes_rejected(mutation):
    value = fixture("strokes")
    context = {}
    if mutation == "timestamps":
        value["strokes"][0]["points"][0]["t_ms"] = 100
    elif mutation == "range":
        value["strokes"][0]["points"][0]["x"] = 1.01
    elif mutation == "duplicate":
        value["strokes"][1]["stroke_id"] = "s1"
    elif mutation == "budget":
        context = {"max_stroke_points": 1}
    elif mutation == "unknown":
        value["strokes"][0]["points"][0]["token"] = "private"
    with pytest.raises(ValidationError):
        StrokeDocument.model_validate(value, context=context)


@pytest.mark.parametrize(
    "mutation", ["private_identity", "outside", "overlap", "guide", "markers", "unknown"]
)
def test_template_identity_and_physical_geometry(mutation):
    value = fixture("template")
    if mutation == "private_identity":
        value["page_identity"]["public_payload"] += ":user-id:token"
    elif mutation == "outside":
        value["cells"][0]["rect_mm"]["x"] = 205
    elif mutation == "overlap":
        value["cells"][1]["rect_mm"] = value["cells"][0]["rect_mm"].copy()
    elif mutation == "guide":
        value["cells"][0]["guide_id"] = "missing"
    elif mutation == "markers":
        value["markers"]["items"][1]["marker_id"] = 0
    else:
        value["project_id"] = UUID
    with pytest.raises(ValidationError):
        TemplateManifest.model_validate(value)


def test_hash_fixtures_and_result_body_exclusion_are_stable():
    for vector in fixture("hash-vectors"):
        assert (
            content_hash(vector["value"], exclude_root_keys=set(vector["exclude_root_keys"]))
            == vector["sha256"]
        )
    assert canonical_bytes({"b": 1, "a": -0.0}) == canonical_bytes({"a": 0, "b": 1.0})
    assert content_hash({"a": "1"}) != content_hash({"a": 1})
    result = fixture("ml-result")
    assert content_hash(result, exclude_root_keys={"artifact_hash"}) == result["artifact_hash"]
    result["candidates"][0]["outline"]["size_bytes"] += 1
    with pytest.raises(ValidationError, match="artifact_hash_mismatch"):
        MLResultManifest.model_validate(result)


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), 2**53, 1e20, "\ud800", {1: 2}, (1, 2)]
)
def test_hashing_rejects_nonportable_values(value):
    with pytest.raises((ValueError, UnicodeEncodeError)):
        canonical_bytes(value)


def test_worker_manifest_cannot_approve_candidates_or_invent_scores():
    result = fixture("ml-result")
    result["candidates"][0]["review_status"] = "approved"
    with pytest.raises(ValidationError):
        MLResultManifest.model_validate(result)
    result = fixture("ml-result")
    result["candidates"][0]["evidence"]["recognition"] = 0.99
    with pytest.raises(ValidationError, match="scores_require"):
        MLResultManifest.model_validate(result)


@pytest.mark.parametrize("mutation", ["pickle", "measured", "promoted", "unknown"])
def test_registry_rejects_unsafe_or_unmeasured_claims(mutation):
    value = fixture("model-registry")
    if mutation == "pickle":
        value["format"] = "pickle"
    elif mutation == "measured":
        value["resource_profile"]["peak_mem_mb"] = 123
    elif mutation == "promoted":
        value["status"] = "approved"
    else:
        value["artifact"]["url"] = "https://example.com/model.py"
    with pytest.raises(ValidationError):
        ModelRegistryEntry.model_validate(value)


def test_job_payload_rejects_private_bytes_tokens_and_wrong_versions():
    payload = dict(
        schema_version=1,
        job_type="analyze_samples",
        project_id=UUID,
        sample_ids=[UUID],
        script_code="Latn",
    )
    assert AnalyzeSamplesJob.model_validate(payload).options.quality_policy == "accuracy_first"
    for key in ["token", "image_bytes", "owner_user_id"]:
        with pytest.raises(ValidationError):
            AnalyzeSamplesJob.model_validate({**payload, key: "private"})
    with pytest.raises(ValidationError):
        AnalyzeSamplesJob.model_validate({**payload, "schema_version": 2})
    with pytest.raises(ValidationError, match="duplicate_source"):
        AnalyzeSamplesJob.model_validate({**payload, "sample_ids": [UUID, UUID]})
    with pytest.raises(ValidationError):
        AnalyzeSamplesJob.model_validate({**payload, "options": {"enhance": "true"}})


def test_export_job_rejects_duplicate_or_mismatched_formats():
    payload = dict(
        schema_version=1,
        job_type="compile_export",
        export_ids=[UUID],
        snapshot_id=UUID,
        formats=["ttf"],
    )
    assert CompileExportJob.model_validate(payload).formats == ["ttf"]
    with pytest.raises(ValidationError, match="must_match"):
        CompileExportJob.model_validate({**payload, "formats": ["ttf", "otf"]})
    with pytest.raises(ValidationError, match="duplicate"):
        CompileExportJob.model_validate({**payload, "formats": ["ttf", "ttf"]})


def test_job_work_units_and_eta_require_evidence_not_animation_time():
    payload = dict(id=UUID, job_type="analyze_samples", state="cancel_requested")
    value = JobDTO.model_validate(payload)
    assert value.progress is None
    assert value.eta.lower_seconds is None
    with pytest.raises(ValidationError, match="exceed_total"):
        JobDTO.model_validate({**payload, "progress": {"completed_units": 2, "total_units": 1}})
    with pytest.raises(ValidationError, match="requires_range"):
        JobDTO.model_validate({**payload, "eta": {"lower_seconds": 10, "upper_seconds": 20}})


def test_compatibility_checker_catches_breaking_changes_and_allows_optional_additions():
    import importlib.util

    path = FIXTURES.parent / "scripts/check_compatibility.py"
    spec = importlib.util.spec_from_file_location("compatibility", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = {
        "type": "object",
        "properties": {"id": {"type": "string", "maxLength": 100}},
        "required": ["id"],
        "additionalProperties": False,
    }
    for current in [
        {**previous, "properties": {}},
        {**previous, "required": ["id", "new"]},
        {**previous, "required": []},
        {**previous, "properties": {"id": {"type": "string", "maxLength": 20}}},
        {**previous, "additionalProperties": True},
    ]:
        assert module.schema_breaks(previous, current)
    compatible = {
        **previous,
        "properties": {**previous["properties"], "optional": {"type": "string"}},
    }
    assert module.schema_breaks(previous, compatible) == []


@pytest.mark.parametrize("field,value", [("sha256", "sha256:" + "3" * 64), ("size_bytes", 200)])
def test_manifest_rejects_conflicting_metadata_for_one_immutable_child(field, value):
    manifest = fixture("ml-result")
    manifest["quality_report"] = copy.deepcopy(manifest["candidates"][0]["outline"])
    manifest["artifact_hash"] = content_hash(manifest, exclude_root_keys={"artifact_hash"})
    # Consistent repeated references are valid.
    MLResultManifest.model_validate(manifest)
    manifest["quality_report"][field] = value
    manifest["artifact_hash"] = content_hash(manifest, exclude_root_keys={"artifact_hash"})
    with pytest.raises(ValidationError, match="conflicting_child_artifact_metadata"):
        MLResultManifest.model_validate(manifest)


def test_hash_exclusion_still_validates_input_domain():
    from datetime import datetime

    class CustomDict(dict):
        pass

    for value in [{"artifact_hash": datetime.now()}, CustomDict(artifact_hash="valid")]:
        with pytest.raises(ValueError, match="unsupported_json_value"):
            canonical_bytes(value, exclude_root_keys={"artifact_hash"})


def test_identity_v1_wire_fixtures_and_additive_security_compatibility():
    import importlib.util

    from app.modules.identity.schemas import ClaimResponse, MeResponse

    values = json.loads((FIXTURES / "identity.v1.json").read_text())
    assert MeResponse.model_validate(values["me"]).guest_creations.remaining == 1
    assert ClaimResponse.model_validate(values["claim"]).over_limit is True
    spec = importlib.util.spec_from_file_location(
        "identity_compatibility", FIXTURES.parent / "scripts/check_compatibility.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = {
        "paths": {"/health": {"get": {"responses": {"200": {}}}}},
        "components": {"schemas": {}},
    }
    current = {
        "paths": {**previous["paths"], "/me": {"get": {"security": [{"HTTPBearer": []}]}}},
        "components": {
            "schemas": {},
            "securitySchemes": {"HTTPBearer": {"type": "http", "scheme": "bearer"}},
        },
    }
    assert module.schema_breaks(previous, current, "implemented_http_surface") == []
    current["paths"]["/health"] = {
        "get": {"responses": {"200": {}}, "security": [{"HTTPBearer": []}]}
    }
    assert module.schema_breaks(previous, current, "implemented_http_surface")
