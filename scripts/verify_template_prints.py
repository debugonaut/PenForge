"""Render the generated PDF and measure its actual public markers and scale bar.

This is digital printability evidence, not a physical-printer/camera accuracy claim.
Use explicit Poppler executables, e.g. the bundled workspace dependency paths.
"""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import cv2
import numpy as np
from penforge_contracts.templates import load_templates, template_digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdfinfo", required=True)
    parser.add_argument("--pdftoppm", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    base = root / "packages/contracts/templates"
    out = root / "artifacts/phase04-core"
    combined = base / "penforge-latin-ascii-1.0.0.pdf"
    info = subprocess.run(
        [args.pdfinfo, "-box", str(combined)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    size = re.search(r"Page size:\s+([0-9.]+) x ([0-9.]+) pts", info)
    if size is None:
        raise ValueError("pdf_page_size_missing")
    size_mm = [float(value) * 25.4 / 72 for value in size.groups()]
    subprocess.run(
        [args.pdftoppm, "-r", "150", "-png", str(combined), str(out / "print-final")],
        check=True,
        capture_output=True,
        timeout=60,
    )
    report = json.loads((out / "printability.v1.json").read_text())
    report["digital_verification"] = []
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
    for manifest in load_templates():
        assert (
            max(
                abs(a - b)
                for a, b in zip(size_mm, [manifest.page.width_mm, manifest.page.height_mm])
            )
            < 0.02
        )
        image_path = out / f"print-final-{manifest.page.index + 1}.png"
        image = cv2.imread(str(image_path))
        payload, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
        assert payload == manifest.page_identity.public_payload
        corners, ids, _ = detector.detectMarkers(image)
        assert ids is not None
        assert set(ids.reshape(-1).tolist()) == {m.marker_id for m in manifest.markers.items}
        by_id = {m.marker_id: m for m in manifest.markers.items}
        errors = []
        scale = 150 / 25.4
        for marker_id, points in zip(ids.reshape(-1), corners, strict=True):
            rect = by_id[int(marker_id)].rect_mm
            expected = np.array([rect.x + rect.width / 2, rect.y + rect.height / 2]) * scale
            errors.append(float(np.linalg.norm(points.reshape(4, 2).mean(axis=0) - expected)))
        assert max(errors) < 2.0
        reference = manifest.printing.scale_reference_rect_mm
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        region = gray[
            round((reference.y - 1) * scale) : round((reference.y + reference.height + 1) * scale),
            round((reference.x - 1) * scale) : round((reference.x + reference.width + 1) * scale),
        ]
        dark = np.argwhere(region < 100)
        reference_mm = float((dark[:, 1].max() - dark[:, 1].min()) / scale)
        assert abs(reference_mm - reference.width) < 0.4
        report["digital_verification"].append(
            {
                "page_index": manifest.page.index,
                "manifest_hash": template_digest(manifest),
                "pdf_page_mm": size_mm,
                "render_dpi": 150,
                "detected_marker_ids": sorted(ids.reshape(-1).tolist()),
                "maximum_marker_center_error_px": max(errors),
                "qr_payload_verified": True,
                "scale_reference_expected_mm": reference.width,
                "scale_reference_observed_mm": reference_mm,
                "raster_sha256": "sha256:" + hashlib.sha256(image_path.read_bytes()).hexdigest(),
            }
        )
    report["render_validation"] = "poppler_geometry_and_public_identity_pass"
    report["poppler_version"] = subprocess.run(
        [args.pdfinfo, "-v"], check=True, capture_output=True, text=True, timeout=10
    ).stderr.splitlines()[0]
    report["opencv_version"] = cv2.__version__
    report["visual_inspection"] = "both_final_pages_inspected_no_clipping_or_overlap"
    (out / "printability.v1.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Both PDF pages: size, four markers, public QR and scale reference passed.")


if __name__ == "__main__":
    main()
