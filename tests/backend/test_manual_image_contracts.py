"""Manual selections cannot reinterpret legacy jobs or escape source/batch bounds."""

import copy
from uuid import uuid4

import pytest
from app.modules.analysis.schemas import AnalysisCreate
from penforge_contracts import AnalyzeSamplesJobV3, contract_registry, parse_analysis_payload
from penforge_contracts.schemas import AnalyzeSamplesJob, ImageRegionAssignment
from pydantic import ValidationError


def region():
    return dict(
        region_id=str(uuid4()),
        sample_id=str(uuid4()),
        source_sha256="sha256:" + "a" * 64,
        source_width=400,
        source_height=300,
        rect=dict(x=10, y=20, width=70, height=90),
        codepoints=[103],
        baseline_y_px=85.0,
        units_per_px=2.0,
        ink_threshold=180,
    )


def payload(item=None):
    item = item or region()
    return dict(
        schema_version=3,
        job_type="analyze_samples",
        project_id=str(uuid4()),
        sample_ids=[item["sample_id"]],
        script_code="Latn",
        image_mode="manual_regions_v1",
        image_regions=[item],
    )


def test_manual_mode_is_explicit_versioned_and_cannot_parse_as_legacy():
    value = payload()
    assert isinstance(parse_analysis_payload(value), AnalyzeSamplesJobV3)
    with pytest.raises(ValidationError):
        AnalyzeSamplesJob.model_validate(value)
    request = AnalysisCreate.model_validate(
        {k: v for k, v in value.items() if k not in {"schema_version", "project_id", "script_code"}}
    )
    assert request.payload(uuid4(), None).schema_version == 3


def test_registry_adds_image_capability_without_changing_legacy_version_bound():
    rows = {row.name: row for row in contract_registry().contracts}
    assert rows["analyze_samples_job"].supported_versions == [1, 2]
    assert rows["analyze_image_regions_job"].supported_versions == [3]
    assert rows["analyze_image_regions_job"].schema_name == "AnalyzeSamplesJobV3"


@pytest.mark.parametrize(
    "change",
    [
        {"source_width": True},
        {"source_width": 6001},
        {"source_width": 5000, "source_height": 5000},
        {"rect": {"x": -1, "y": 0, "width": 1, "height": 1}},
        {"rect": {"x": 380, "y": 20, "width": 70, "height": 90}},
        {"rect": {"x": 0, "y": 0, "width": 0, "height": 1}},
        {"codepoints": [65, 66]},
        {"codepoints": [32]},
        {"codepoints": [0x915]},
        {"baseline_y_px": 301.0},
        {"baseline_y_px": float("nan")},
        {"units_per_px": 0.0},
        {"ink_threshold": 256},
        {"source_sha256": "sha256:wrong"},
    ],
)
def test_reject_invalid_selection_without_decoding_image(change):
    with pytest.raises(ValidationError):
        ImageRegionAssignment.model_validate(region() | change)


def test_all_regions_bind_to_listed_sources_and_use_common_scale():
    value = payload()
    first = value["image_regions"][0]
    second = copy.deepcopy(first) | {"region_id": str(uuid4()), "codepoints": [105]}
    assert (
        len(
            AnalyzeSamplesJobV3.model_validate(
                value | {"image_regions": [first, second]}
            ).image_regions
        )
        == 2
    )
    for changed in (
        second | {"units_per_px": 3.0},
        second | {"sample_id": str(uuid4())},
        second | {"region_id": first["region_id"]},
        second | {"source_sha256": "sha256:" + "b" * 64},
    ):
        with pytest.raises(ValidationError):
            AnalyzeSamplesJobV3.model_validate(value | {"image_regions": [first, changed]})


def test_batch_pixel_limit_and_region_count_are_enforced():
    first = region() | {
        "source_width": 4000,
        "source_height": 3000,
        "rect": dict(x=0, y=0, width=2000, height=2000),
    }
    value = payload(first)
    with pytest.raises(ValidationError):
        AnalyzeSamplesJobV3.model_validate(
            value | {"image_regions": [first | {"region_id": str(uuid4())} for _ in range(3)]}
        )
    first = region()
    with pytest.raises(ValidationError):
        AnalyzeSamplesJobV3.model_validate(
            payload(first)
            | {"image_regions": [first | {"region_id": str(uuid4())} for _ in range(97)]}
        )


def test_manual_mode_refuses_mixed_drawing_or_implicit_regions():
    item = region()
    request = dict(sample_ids=[item["sample_id"]], image_regions=[item])
    with pytest.raises(ValidationError):
        AnalysisCreate.model_validate(request)
    with pytest.raises(ValidationError):
        AnalysisCreate.model_validate(
            request | {"image_mode": "manual_regions_v1", "drawing_mode": "fixed_round_v1"}
        )
