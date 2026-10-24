"""Synthetic geometry/lineage guards; no handwriting model or private page dependency."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from penforge_contracts import content_hash

from packages.extraction.geometry import trace_mask
from research.font_polish import (
    Frame,
    Policy,
    ink_margin,
    propose_outline,
    render_outline,
    topology,
)


def fixture(kind="holes"):
    mask = np.zeros((48, 40), dtype=np.uint8)
    if kind == "holes":
        cv2.ellipse(mask, (20, 24), (12, 19), 0, 0, 360, 255, -1)
        cv2.ellipse(mask, (20, 15), (5, 6), 0, 0, 360, 0, -1)
        cv2.ellipse(mask, (20, 32), (5, 7), 0, 0, 360, 0, -1)
    elif kind == "punctuation":
        mask[8:13, 18:23] = mask[31:36, 18:23] = 255
    elif kind == "border":
        mask[0:30, 0:6] = 255
    elif kind == "thin":
        mask[8:39, 19] = 255
        mask[3, 19] = 255
    frame = Frame(36, 25, -2.5)
    reference = trace_mask(
        mask,
        [66],
        baseline_px=frame.baseline_px,
        units_per_px=frame.units_per_px,
        origin_x_px=frame.origin_x_px,
        advance_width_px=46,
    )
    margin = np.where(mask > 0, 1, -1).astype(np.float32)
    return mask, margin, reference, frame


@pytest.mark.parametrize("kind", ["holes", "punctuation", "border", "thin"])
def test_proposal_preserves_topology_frame_and_immutable_inputs_or_retains_original(kind):
    mask, margin, reference, frame = fixture(kind)
    before = (mask.copy(), margin.copy(), reference.model_dump(mode="json"))
    result, report = propose_outline(mask, margin, reference, frame)
    assert np.array_equal(mask, before[0]) and np.array_equal(margin, before[1])
    assert reference.model_dump(mode="json") == before[2]
    assert report["approval"] == "pending" and report["writer_fidelity"] is None
    assert report["parent_outline_hash"] == content_hash(before[2])
    assert result.metrics.advance_width == reference.metrics.advance_width
    if report["status"] == "proposed":
        assert topology(render_outline(result, mask.shape, frame)) == topology(mask)
        assert len(result.contours) == len(reference.contours)
        assert abs(report["sampled_area_ratio"] - 1) <= 0.12
        assert report["sampled_area_ratio"] <= 1.03
        assert report["sampled_symmetric_edge_distance_px"] <= 1.25
    else:
        assert result.model_dump(mode="json") == before[2]
        assert report["reason"]


def test_edge_proposal_is_repeatable_has_curves_and_explicit_reconstruction_lineage():
    args = fixture()
    first, report = propose_outline(*args)
    second, repeat = propose_outline(*args)
    assert report["status"] == "proposed"
    assert content_hash(first.model_dump(mode="json")) == content_hash(
        second.model_dump(mode="json")
    )
    assert report == repeat
    assert report["candidate_origin"] == "geometric_reconstruction"
    assert any(s.type == "cubic" for c in first.contours for s in c.segments)


@pytest.mark.parametrize("reason", ["topology", "area", "edge", "empty"])
def test_failed_guard_returns_exact_original_with_reason(reason):
    mask, margin, reference, frame = fixture("punctuation" if reason == "topology" else "holes")
    policy = Policy()
    if reason == "topology":
        margin[8:13, 18:23] = -2
    elif reason == "area":
        policy = Policy(max_area_change=0)
    elif reason == "edge":
        policy = Policy(max_sampled_edge_move_px=0)
    else:
        margin[:] = -2
    result, report = propose_outline(mask, margin, reference, frame, policy)
    assert report["status"] == "retained_original"
    assert (
        report["reason"]
        == {
            "topology": "topology_changed",
            "area": "ink_area_change_exceeded",
            "edge": "edge_movement_exceeded",
            "empty": "empty_proposal",
        }[reason]
    )
    assert result is not reference
    assert result.model_dump(mode="json") == reference.model_dump(mode="json")


def test_wrong_frame_or_reference_is_rejected_before_any_proposal():
    mask, margin, reference, frame = fixture()
    with pytest.raises(ValueError, match="source_frame_mismatch"):
        propose_outline(mask, margin, reference, Frame(35, 25, -2.5))
    changed = copy.deepcopy(reference)
    changed.metrics.advance_width += 1
    # A changed advance is an explicitly supplied typography choice, but other geometry must match.
    result, _ = propose_outline(mask, margin, changed, frame)
    assert result.metrics.advance_width == changed.metrics.advance_width


def test_ink_field_reuses_grey_and_blue_thresholds_without_mutating_source():
    rgb = np.full((10, 10, 3), 180, np.uint8)
    rgb[2:8, 4:6] = [60, 70, 150]
    original = rgb.copy()
    grey = ink_margin(rgb, method="gray_otsu", threshold=140)
    blue = ink_margin(rgb, method="blue_chroma_otsu", threshold=0.1)
    assert grey[4, 4] > 0 and grey[0, 0] < 0
    assert blue[4, 4] > 0 and blue[0, 0] < 0
    assert np.array_equal(rgb, original)
    with pytest.raises(ValueError, match="blue_gate_required"):
        ink_margin(rgb, method="gray_otsu_blue_gate", threshold=140)
    gated = ink_margin(rgb, method="gray_otsu_blue_gate", threshold=140, blue_gate=0.1)
    assert gated[4, 4] > 0 and gated[0, 0] < 0


@pytest.mark.parametrize("change", ["grey_mask", "nonfinite", "shape", "empty"])
def test_bad_masks_and_fields_fail_closed(change):
    mask, margin, reference, frame = fixture()
    if change == "grey_mask":
        mask[0, 0] = 127
    elif change == "nonfinite":
        margin[0, 0] = np.nan
    elif change == "shape":
        margin = margin[1:]
    else:
        mask[:] = 0
    with pytest.raises(ValueError, match="invalid_polish"):
        propose_outline(mask, margin, reference, frame)


@pytest.mark.parametrize(
    "options",
    [
        {"supersample": True},
        {"supersample": 100},
        {"blur_sigma_px": float("nan")},
        {"ink_margin_offset": -0.1},
        {"max_area_change": 0.5},
        {"max_sampled_edge_move_px": 10},
    ],
)
def test_unbounded_or_nonfinite_policy_is_refused(options):
    with pytest.raises(ValueError, match="invalid_polish_policy"):
        Policy(**options)


def test_import_and_proposal_need_no_model_frameworks_in_a_fresh_process():
    script = """
import importlib.abc, json, sys
blocked = {'torch', 'transformers', 'safetensors', 'tensorflow', 'onnxruntime'}
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked: raise RuntimeError('model_import_forbidden')
sys.meta_path.insert(0, Block())
sys.path[:0] = ['packages/contracts', '.']
import numpy as np
from packages.extraction.geometry import trace_mask
from research.font_polish import Frame, propose_outline
mask = np.zeros((16, 16), dtype=np.uint8)
mask[3:13, 3:13] = 255
reference = trace_mask(mask, [65], baseline_px=12, units_per_px=25)
result, report = propose_outline(mask, np.where(mask > 0, 1., -1.).astype(np.float32), reference, Frame(12, 25, 0))
print(json.dumps({'status': report['status'], 'models': sorted(blocked & {name.split('.')[0] for name in sys.modules})}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path.cwd(), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "proposed", "models": []}
