"""Deterministic A4 engineering templates using the established PDF backend."""

import json
from pathlib import Path

from reportlab import Version
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "packages/contracts/templates"
if __name__ == "__main__":
    if Version != "4.4.9":
        raise ValueError("pdf_toolchain_mismatch")
    catalog = json.loads((BASE / "deva.catalog.index.json").read_bytes())
    combined = canvas.Canvas(
        str(BASE / "penforge-deva-tier1-1.0.0.pdf"), invariant=1, pageCompression=1
    )
    for row in catalog["pages"]:
        individual = canvas.Canvas(str(BASE / row["pdf"]), invariant=1, pageCompression=1)
        for output in (individual, combined):
            output.setPageSize((210 * 72 / 25.4, 297 * 72 / 25.4))
            output.setTitle("Penforge Devanagari engineering template")
            output.setSubject(row["manifest_hash"])
            output.drawImage(
                ImageReader(str(BASE / row["png"])),
                0,
                0,
                width=210 * 72 / 25.4,
                height=297 * 72 / 25.4,
            )
            output.showPage()
        individual.save()
    combined.save()
