"""Create script-specific manifests and reference-shaped printable masters."""

import copy
import hashlib
import json
from pathlib import Path

import uharfbuzz as hb
from fontTools.ttLib import TTFont
from penforge_contracts import TemplateManifest, content_hash
from penforge_contracts.devanagari import inventory

from packages.extraction.templates import template_png, template_svg

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "packages/contracts/templates"


def main():
    base = json.loads((BASE / "penforge-latin-ascii-1.0.0-page-0.json").read_bytes())
    targets = [[r["codepoint"]] for r in inventory()["entries"]]
    starter = json.loads((ROOT / "research/evaluation/corpora/deva-starter-v1.yaml").read_bytes())
    targets += [r["codepoints"] for r in starter["cases"] if r["codepoints"] not in targets]
    ref = ROOT / "research/fixtures/fonts/noto-deva/NotoSansDevanagari.ttf"
    font = TTFont(ref)
    provenance = dict(
        reference_font_sha256=hashlib.sha256(ref.read_bytes()).hexdigest(),
        reference_font_version=font["name"].getDebugName(5),
        license="OFL-1.1",
        harfbuzz=hb.version_string(),
        generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        renderer_sha256=hashlib.sha256(
            (ROOT / "packages/extraction/deva_labels.py").read_bytes()
        ).hexdigest(),
    )
    pages = []
    for page, start in enumerate(range(0, len(targets), len(base["cells"]))):
        value = copy.deepcopy(base)
        value.update(
            template_id="penforge-deva-tier1",
            template_version="1.0.0",
            script_code="Deva",
            label_provenance=provenance,
        )
        value["page"]["index"] = page
        value["page_identity"]["public_payload"] = f"penforge-deva-tier1:1.0.0:{page}"
        value["guides"] = [
            dict(
                guide_id="deva-regions",
                baseline_offset_mm=19.0,
                upper_mark_offset_mm=3.0,
                headline_offset_mm=7.0,
                lower_mark_offset_mm=23.0,
            )
        ]
        cells = []
        for i, cps in enumerate(targets[start : start + len(base["cells"])]):
            cell = copy.deepcopy(base["cells"][i])
            cell.update(cell_id=f"deva-{start + i:03d}", codepoints=cps, guide_id="deva-regions")
            cells.append(cell)
        value["cells"] = cells
        manifest = TemplateManifest.model_validate(value)
        name = f"penforge-deva-tier1-1.0.0-page-{page}"
        (BASE / (name + ".json")).write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n"
        )
        (BASE / (name + ".png")).write_bytes(template_png(manifest, pixels_per_mm=300 / 25.4))
        (BASE / (name + ".svg")).write_text(template_svg(manifest))
        pages.append(
            dict(
                file=name + ".json",
                png=name + ".png",
                svg=name + ".svg",
                pdf=name + ".pdf",
                manifest_hash=content_hash(manifest.model_dump(mode="json")),
            )
        )
    (BASE / "deva.catalog.index.json").write_text(
        json.dumps(dict(pages=pages, label_provenance=provenance), indent=2) + "\n"
    )
    calibration = dict(
        schema_version=1,
        calibration_id="deva-engineering-targets",
        version="1.0.0",
        script_code="Deva",
        prompt_text="कि कु कू कृ क़ क्ष र्क श्र क्त हूँ",
        target_codepoints=sorted({cp for seq in targets for cp in seq}),
        target_sequences=[c["codepoints"] for c in starter["cases"]],
        expected_regions=[],
        prompt_is_ground_truth=False,
        optimization_status="unmeasured",
        evaluation_record_hash=None,
        localization_review="engineering_unreviewed",
    )
    (ROOT / "packages/contracts/policies/deva-calibration.v1.json").write_text(
        json.dumps(calibration, ensure_ascii=True, indent=2) + "\n"
    )
    print("Generated", len(pages), "Devanagari template pages")


if __name__ == "__main__":
    main()
