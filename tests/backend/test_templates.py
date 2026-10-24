"""Pinned public-template generator acceptance, independent of extraction accuracy."""

import copy
import hashlib
import io
import json
from pathlib import Path
from xml.etree import ElementTree

import cv2
import numpy as np
import pytest
from penforge_contracts import TemplateManifest, content_hash
from penforge_contracts.templates import load_templates
from PIL import Image
from pydantic import ValidationError

from packages.extraction.templates import load_template, template_hash, template_png, template_svg

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "packages/contracts/templates"


def test_archived_template_inventory_is_exact_printable_ascii_without_space():
    manifests = tuple(m for m in load_templates() if m.script_code == "Latn")
    assert len(manifests) == 2
    values = [cell.codepoints for manifest in manifests for cell in manifest.cells]
    assert sorted(values) == [[cp] for cp in range(33, 127)]
    assert len({cell.cell_id for manifest in manifests for cell in manifest.cells}) == 94
    assert [m.page.index for m in manifests] == [0, 1]
    assert all(m.page.width_mm == 210 and m.page.height_mm == 297 for m in manifests)


@pytest.mark.parametrize("page", [0, 1])
def test_archived_manifest_digest_and_regenerated_masters_match(page):
    manifest = load_template(page)
    catalog = json.loads((BASE / "catalog.index.json").read_text())
    index_hash = catalog.pop("index_hash")
    assert content_hash(catalog) == index_hash
    entry = catalog["pages"][page]
    assert entry["manifest_hash"] == template_hash(manifest)
    assert (BASE / entry["svg"]).read_text() == template_svg(manifest)
    assert (BASE / entry["png"]).read_bytes() == template_png(manifest, pixels_per_mm=300 / 25.4)
    svg = ElementTree.fromstring(template_svg(manifest))
    assert svg.get("width") == "210.0mm" and svg.get("height") == "297.0mm"
    assert template_hash(manifest) in template_svg(manifest)
    assert manifest.page_identity.public_payload == f"penforge-latin-ascii:1.0.0:{page}"


@pytest.mark.parametrize("page", [0, 1])
def test_print_master_has_readable_public_identity_and_four_ordered_markers(page):
    manifest = load_template(page)
    data = template_png(manifest, pixels_per_mm=300 / 25.4)
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    payload, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
    assert payload == manifest.page_identity.public_payload
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
    corners, ids, _ = detector.detectMarkers(image)
    assert set(ids.reshape(-1).tolist()) == {m.marker_id for m in manifest.markers.items}
    # Each detected marker belongs at its manifest position, within pixel rounding.
    expected = {m.marker_id: m.rect_mm for m in manifest.markers.items}
    for marker_id, points in zip(ids.reshape(-1), corners, strict=True):
        r = expected[int(marker_id)]
        observed = points.reshape(4, 2).mean(axis=0)
        target = np.array([r.x + r.width / 2, r.y + r.height / 2]) * (300 / 25.4)
        assert np.max(np.abs(observed - target)) < 1.5
    with Image.open(io.BytesIO(data)) as raster:
        assert raster.size == (round(210 * 300 / 25.4), round(297 * 300 / 25.4))


@pytest.mark.parametrize(
    "change",
    [
        "qr_overlap",
        "quiet_zone",
        "print_margin",
        "guide_order",
        "mapping_surrogate",
        "page_identity",
    ],
)
def test_unprintable_or_mismatched_manifest_is_rejected(change):
    value = load_template(0).model_dump(mode="json")
    if change == "qr_overlap":
        value["page_identity"]["rect_mm"]["x"] = 12.0
    elif change == "quiet_zone":
        value["markers"]["quiet_zone_mm"] = 13.0
    elif change == "print_margin":
        value["printing"]["printable_margin_mm"] = 10.0
    elif change == "guide_order":
        value["guides"][0]["x_height_offset_mm"] = 23.0
    elif change == "mapping_surrogate":
        value["cells"][0]["codepoints"] = [0xD800]
    else:
        value["page_identity"]["public_payload"] += ":private-project"
    with pytest.raises(ValidationError):
        TemplateManifest.model_validate(value)


def test_physical_layout_changes_have_new_digests_and_do_not_rewrite_source():
    original = load_template(0)
    source = (BASE / "penforge-latin-ascii-1.0.0-page-0.json").read_bytes()
    changed = copy.deepcopy(original.model_dump(mode="json"))
    changed["page_identity"]["rect_mm"]["x"] += 1.0
    variant = TemplateManifest.model_validate(changed)
    assert template_hash(variant) != template_hash(original)
    assert (
        hashlib.sha256(source).digest()
        == hashlib.sha256((BASE / "penforge-latin-ascii-1.0.0-page-0.json").read_bytes()).digest()
    )


def test_legacy_template_contract_remains_byte_equivalent_without_new_layout_fields():
    fixture = json.loads((ROOT / "packages/contracts/fixtures/template.v1.json").read_text())
    assert TemplateManifest.model_validate(fixture).model_dump(mode="json") == fixture
    with pytest.raises(ValueError, match="template_print_geometry_unpinned"):
        template_png(TemplateManifest.model_validate(fixture))
