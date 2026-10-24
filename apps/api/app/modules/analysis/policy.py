"""Schema-only pinned policy shared by orchestration and the local worker."""

import json
from pathlib import Path

from penforge_contracts import content_hash
from penforge_contracts.canvas import canvas_policy

POLICY_PATH = (
    Path(__file__).resolve().parents[5] / "packages/contracts/policies/structured-latin.v1.json"
)
PIPELINE = dict(
    script_code="Latn",
    style="unknown",
    model_id=None,
    model_version=None,
    model_hash=None,
    algorithm_version="latin-extraction-v1",
    preprocessing_version="pinned-template-color-dropout-v2",
)


def policy():
    return json.loads(POLICY_PATH.read_bytes())


def image_config():
    p = policy()
    return dict(
        pixels_per_mm=8.0,
        trace_tolerance_px=0.0,
        curve_tolerance_px=0.0,
        quality_gates={
            k: p[k]
            for k in ("evidence_hash", "minimum_pixels_per_mm", "minimum_laplacian_variance")
        },
    )


def policy_hash(script="Latn", drawing_mode=None):
    original = content_hash(
        policy()
        if script == "Latn"
        else {
            "geometry_policy": policy(),
            "script": "Deva",
            "required_set_id": "deva-tier1-ucd17-v1",
            "quality": "unqualified_for_handwriting",
        }
    )

    return (
        content_hash({"structured_policy": original, "canvas_policy": canvas_policy()})
        if drawing_mode
        else original
    )


def calibration_policy():
    from penforge_contracts import CalibrationDefinition

    value = json.loads(POLICY_PATH.with_name("latin-calibration.v1.json").read_bytes())
    definition = CalibrationDefinition.model_validate(value["definition"])
    if (
        definition.version != value["definition_version"]
        or definition.prompt_text != value["initial_passage"]
        or definition.optimization_status != "unmeasured"
    ):
        raise ValueError("calibration_policy_mismatch")
    return value


def pipeline(script="Latn", drawing_mode=None):
    if script not in {"Latn", "Deva"}:
        raise ValueError("unsupported_script")
    if drawing_mode:
        if drawing_mode != "fixed_round_v1" or script != "Latn":
            raise ValueError("unsupported_drawing_mode")
        return PIPELINE | dict(algorithm_version="latin-extraction-v2-round")
    return (
        PIPELINE
        if script == "Latn"
        else PIPELINE | dict(script_code="Deva", algorithm_version="deva-structured-v1")
    )
