"""Pinned brush geometry policy; schema-only and safe for the API image."""

import json
from pathlib import Path

from .serialization import content_hash

POLICY_PATH = Path(__file__).resolve().parents[1] / "policies/canvas-round.v1.json"


def canvas_policy():
    return json.loads(POLICY_PATH.read_bytes())


def drawing_provenance(drawing_mode=None):
    if drawing_mode is None:
        return {
            "algorithm_version": "capture-raster-union-v2",
            "vectorizer_version": "pixel-union-contours-v2",
        }
    if drawing_mode != "fixed_round_v1":
        raise ValueError("unsupported_drawing_mode")
    p = canvas_policy()
    return {
        key: p[key]
        for key in ("algorithm_version", "vectorizer_version", "pathops_version", "pressure_policy")
    } | {"geometry_policy_hash": content_hash(p)}
