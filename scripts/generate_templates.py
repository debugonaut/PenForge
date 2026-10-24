"""Generate exact-size public SVG/raster masters from pinned template manifests.

PDF authoring is a separate bundled reportlab step, so API/worker dependency images
need no PDF renderer. No private ID or token is printed.
"""

from pathlib import Path

from penforge_contracts.serialization import content_hash

from packages.extraction.templates import load_template, template_hash, template_png, template_svg


def main():
    import json

    base = Path(__file__).resolve().parents[1] / "packages/contracts/templates"
    entries = []
    for page in (0, 1):
        manifest = load_template(page)
        stem = f"penforge-latin-ascii-1.0.0-page-{page}"
        (base / f"{stem}.svg").write_text(template_svg(manifest))
        (base / f"{stem}.png").write_bytes(template_png(manifest, pixels_per_mm=300 / 25.4))
        entries.append(
            {
                "file": f"{stem}.json",
                "manifest_hash": template_hash(manifest),
                "page_index": page,
                "svg": f"{stem}.svg",
                "png": f"{stem}.png",
                "pdf": f"{stem}.pdf",
            }
        )
    record = {
        "schema_version": 1,
        "template_id": "penforge-latin-ascii",
        "version": "1.0.0",
        "pages": entries,
        "physical_units": "mm",
        "print_scale": "100_percent_actual_size",
        "marker_quiet_zone_mm": 3.0,
        "public_identity_only": True,
        "space": "metrics_only_not_drawn",
    }
    record["index_hash"] = content_hash(record)
    (base / "catalog.index.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
