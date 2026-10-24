"""PDF companion to generate_templates.py. Requires reportlab==4.4.9 (bundled)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from reportlab import Version
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "packages/contracts/templates"


def main():
    if Version != "4.4.9":
        raise ValueError("template_pdf_renderer_version_mismatch")
    catalog = json.loads((BASE / "catalog.index.json").read_text())
    combined = canvas.Canvas(
        str(BASE / "penforge-latin-ascii-1.0.0.pdf"),
        invariant=1,
        pageCompression=1,
    )
    pages = []
    for entry in catalog["pages"]:
        manifest = json.loads((BASE / entry["file"]).read_text())
        page = manifest["page"]
        width, height = page["width_mm"] * 72 / 25.4, page["height_mm"] * 72 / 25.4
        pages.append(
            {
                "index": page["index"],
                "width_mm": page["width_mm"],
                "height_mm": page["height_mm"],
                "manifest_hash": entry["manifest_hash"],
            }
        )
        image = ImageReader(str(BASE / entry["png"]))
        individual = canvas.Canvas(
            str(BASE / entry["pdf"]), pagesize=(width, height), invariant=1, pageCompression=1
        )
        for output in (individual, combined):
            output.setPageSize((width, height))
            output.setTitle(
                f"Penforge Latin ASCII {manifest['template_version']} / page {page['index'] + 1}"
            )
            output.setAuthor("Penforge")
            output.setSubject(f"Print at 100% / Actual size. Manifest {entry['manifest_hash']}")
            output.drawImage(image, 0, 0, width=width, height=height)
            output.showPage()
        individual.save()
    combined.save()
    files = list(BASE.glob("*.pdf"))
    report = {
        "schema_version": 1,
        "renderer": "reportlab",
        "version": Version,
        "dpi": 300,
        "pages": pages,
        "render_validation": "pending_poppler",
        "physical_printer_measurement": "not_executed",
        "limitations": [
            "Exact PDF page size and marker geometry are tested; physical printer scaling and real camera captures require separate evidence.",
            "No private project/user identifiers, tokens or credentials are printed.",
        ],
        "files": {
            path.name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() for path in files
        },
    }
    out = ROOT / "artifacts/phase04-core/printability.v1.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
