import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from penforge_contracts import CalibrationDefinition, content_hash
from penforge_contracts.templates import load_templates
from template_fixtures import assert_png_raster_equal

from packages.extraction.deva_labels import shaped
from packages.extraction.templates import template_png, template_svg

ROOT = Path(__file__).parents[2]
BASE = ROOT / "packages/contracts/templates"


def test_pinned_deva_labels_geometry_and_print_identity(tmp_path):
    report = []
    for m in load_templates():
        if m.script_code != "Deva":
            continue
        assert m.guides[0].cap_height_offset_mm is None
        assert m.guides[0].headline_offset_mm < m.guides[0].baseline_offset_mm
        for cell in m.cells:
            _, _, items = shaped(cell.codepoints, m.label_provenance)
            assert items and all(i.codepoint != 0 for i, p in items)
        data = template_png(m, pixels_per_mm=300 / 25.4)
        name = f"{m.template_id}-{m.template_version}-page-{m.page.index}"
        assert_png_raster_equal((BASE / (name + ".png")).read_bytes(), data)
        assert hashlib.sha256((BASE / (name + ".svg")).read_bytes()).hexdigest() == (
            hashlib.sha256(template_svg(m).encode()).hexdigest()
        )
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        payload, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
        assert payload == m.page_identity.public_payload
        detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
        corners, ids, _ = detector.detectMarkers(image)
        assert set(ids.reshape(-1)) == {i.marker_id for i in m.markers.items}
        expected = {r.marker_id: r.rect_mm for r in m.markers.items}
        errors = []
        for marker_id, pts in zip(ids.reshape(-1), corners):
            r = expected[marker_id]
            err = float(
                np.max(
                    np.abs(
                        pts.reshape(4, 2).mean(axis=0)
                        - np.array([r.x + r.width / 2, r.y + r.height / 2]) * 300 / 25.4
                    )
                )
            )
            errors.append(err)
            assert err < 1.5
        report.append(
            dict(
                page=m.page.index,
                manifest_hash=content_hash(m.model_dump(mode="json")),
                label_count=len(m.cells),
                markers=True,
                qr=True,
                marker_error_px=max(errors),
                physical_print="unexecuted",
            )
        )
    assert len(report) == 3
    (tmp_path / "printability.json").write_text(json.dumps(report, indent=2) + "\n")


def test_engineering_calibration_not_optimized_or_truth():
    value = CalibrationDefinition.model_validate_json(
        (ROOT / "packages/contracts/policies/deva-calibration.v1.json").read_bytes()
    )
    assert value.optimization_status == "unmeasured"
    assert value.localization_review == "engineering_unreviewed"
    assert not value.prompt_is_ground_truth
    assert len(value.target_sequences) == 11
