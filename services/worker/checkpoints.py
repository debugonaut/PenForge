"""Pin actual algorithm/dependency bytes; no pickle, private paths or writing in logs."""

import hashlib
import importlib.metadata
import sys
from pathlib import Path

from penforge_contracts import content_hash

ROOT = Path(__file__).resolve().parents[2]


def dependencies(inputs, template, configuration, algorithm):
    paths = [ROOT / "uv.lock"]
    for directory in (
        "packages/extraction",
        "packages/capture_safety",
        "packages/font-engine",
        "services/worker",
    ):
        paths.extend(sorted((ROOT / directory).rglob("*.py")))
    code = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    versions = {
        name: importlib.metadata.version(name)
        for name in (
            "fonttools",
            "numpy",
            "opencv-python-headless",
            "pillow",
            "skia-pathops",
            "uharfbuzz",
            "brotli",
            "fontbakery",
            "freetype-py",
            "opentype-sanitizer",
        )
    }
    return dict(
        inputs=inputs,
        template=template,
        model=None,
        configuration=configuration,
        algorithm=algorithm,
        dependencies=content_hash(dict(code=code, versions=versions, python=sys.version)),
    )
