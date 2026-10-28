"""Run inside the built images, with no network and an external memory cap."""

import argparse
import asyncio
import copy
import hashlib
import importlib
import io
import json
import locale
import re
import socket
import ssl
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from penforge_contracts import GlyphOutline, StrokeDocument
from penforge_contracts.unicode import graphemes

INSTALLER_PROBES = ("pip", "pip._vendor.urllib3", "pip_api", "pip_api._call")


def installer_boundary(source: Path, directory: Path) -> dict:
    child = Path("packages/font-engine/penforge_font_engine/_fontbakery_child.py").resolve()
    # Exercise the installed child against an attempted import. A missing guard
    # would let the stub return success, which this negative control rejects.
    runner = """
import importlib, runpy, sys, types
requested, child, source, output = sys.argv[1:]
package = types.ModuleType('fontbakery')
package.__path__ = []
cli = types.ModuleType('fontbakery.cli')
def check():
    importlib.import_module(requested)
    return 0
cli.main = check
sys.modules.update({'fontbakery': package, 'fontbakery.cli': cli})
sys.argv = [child, source, output]
runpy.run_path(child, run_name='__main__')
"""
    for index, name in enumerate(INSTALLER_PROBES):
        output = directory / f"installer-probe-{index}.json"
        result = subprocess.run(
            [sys.executable, "-I", "-c", runner, name, str(child), str(source), str(output)],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 2 and not output.exists(), name
    return {
        "status": "passed",
        "probes": list(INSTALLER_PROBES),
        "child_sha256": hashlib.sha256(child.read_bytes()).hexdigest(),
    }


def application_imports(*, worker: bool) -> None:
    # Exercise native wheel loading in its real parent import context. These
    # imports do not initialize provider clients or open database connections.
    import psycopg.pq
    from app.core.config import Settings
    from app.main import create_app
    from cryptography.hazmat.bindings.openssl.binding import Binding

    assert psycopg.pq.__impl__ == "binary"
    assert psycopg.pq.version() > 0
    assert Binding().lib.OpenSSL_version_num() > 0
    importlib.import_module("app.adapters.auth.firebase")
    importlib.import_module("google.cloud.firestore")
    application = create_app(
        Settings(app_env="production", auth_backend="disabled", storage_backend="disabled")
    )

    async def lifespan() -> None:
        async with application.router.lifespan_context(application):
            assert application.state.storage is None
            assert application.state.identity_service is None
            assert "/api/v1/health" in application.openapi()["paths"]

    asyncio.run(asyncio.wait_for(lifespan(), timeout=5))
    if worker:
        # Import definitions only; never enter the worker loop or create clients.
        importlib.import_module("services.worker.__main__")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--image-id", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_id):
        parser.error("image-id must be an exact sha256 config ID")
    runtime_manifest = Path("/usr/share/penforge-runtime/manifest.json")
    with runtime_manifest.open("rb") as stream:
        manifest_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "role": "worker" if args.worker else "api",
        "image_config_id": args.image_id,
        "runtime_manifest_sha256": manifest_sha,
        "status": "passed",
        "application_imports": True,
        "fonts": {},
        "validator_installer_boundary": None,
    }
    # Exercise runtime-loaded CA/locale/NSS data, not only static ELF dependencies.
    assert ssl.create_default_context().cert_store_stats()["x509_ca"] > 0
    locale.setlocale(locale.LC_CTYPE, "C.UTF-8")
    assert socket.getaddrinfo("localhost", 8000)
    application_imports(worker=args.worker)
    root = Path("packages/contracts")
    assert graphemes("A\u0301") == [[65, 769]]
    assert len(graphemes("कि")) == 1
    StrokeDocument.model_validate(json.loads((root / "fixtures/strokes.v1.json").read_text()))
    outline = json.loads((root / "fixtures/outline.v1.json").read_text())
    GlyphOutline.model_validate(outline)
    if args.worker:
        from penforge_contracts import FontBuildManifest, FontOptions
        from penforge_font_engine.compiler import compile_font, installed_versions
        from penforge_font_engine.validation import validate_font
        from PIL import Image, ImageDraw

        from packages.capture_safety import DecodeLimits, verify_image
        from packages.extraction import extract_image, load_template, template_hash, template_png
        from packages.extraction.templates import PIXELS_PER_MM

        # Real parsers, synthetic ink, no external data or network.
        template = load_template(0)
        page = Image.open(io.BytesIO(template_png(template))).convert("RGB")
        for cell in template.cells[:3]:
            r = cell.rect_mm
            x, y = round(r.x * PIXELS_PER_MM), round(r.y * PIXELS_PER_MM)
            ImageDraw.Draw(page).line(
                (x + 40, y + 40, x + 70, y + 110, x + 110, y + 50), fill="black", width=5
            )
        encoded = io.BytesIO()
        page.save(encoded, format="PNG")
        source = encoded.getvalue()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.png"
            path.write_bytes(source)
            verify_image(
                path,
                declared_mime="image/png",
                output_dir=Path(temporary),
                limits=DecodeLimits(),
                cancel_check=lambda: None,
            )
            extraction = extract_image(source, template, template_hash(template))
            assert not extraction.reasons and extraction.glyphs
        rows = []
        for cp in (32, 65, 86):
            glyph = copy.deepcopy(outline)
            glyph["codepoints"] = [cp]
            if cp == 32:
                glyph["contours"] = []
                glyph["metrics"] = {"advance_width": 250.0, "left_side_bearing": 0.0}
            elif cp == 86:
                glyph["metrics"]["advance_width"] += 100
            rows.append(
                dict(outline=glyph, approval="approved", origin="user_capture", variant_index=0)
            )
        manifest = FontBuildManifest(
            snapshot_hash="sha256:" + "a" * 64,
            snapshot_created_at=datetime(2026, 10, 21, tzinfo=UTC),
            family_name="Penforge Image Smoke",
            tool_versions=installed_versions(),
            options=FontOptions(spacing=100),
        )
        with tempfile.TemporaryDirectory() as temporary:
            frozen = json.loads((root / "fixtures/font-runtime-hashes.v1.json").read_text())
            assert installed_versions() == frozen["tool_versions"]
            for format in ("ttf", "otf", "woff2"):
                data = compile_font(manifest, rows, format)
                assert hashlib.sha256(data).hexdigest() == frozen["fonts"][format]
                report = validate_font(data, manifest, rows, format, Path(temporary) / format)
                assert report["status"] in {"passed", "passed_with_warnings"}, report
                assert report["layers"]["harfbuzz"]["measured_spacing"]["corrected_pairs"] > 0
                receipt["fonts"][format] = hashlib.sha256(data).hexdigest()
                if format == "ttf":
                    probe_source = Path(temporary) / "probe-font.ttf"
                    probe_source.write_bytes(data)
                    receipt["validator_installer_boundary"] = installer_boundary(
                        probe_source, Path(temporary)
                    )
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
