"""Archived public template data only; no rendering/CV dependency enters the API."""

from pathlib import Path

from .schemas import TemplateManifest
from .serialization import content_hash

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates"


def load_templates() -> tuple[TemplateManifest, ...]:
    return tuple(
        TemplateManifest.model_validate_json(path.read_bytes())
        for path in sorted(TEMPLATE_ROOT.glob("*.json"))
        if not path.name.endswith(".index.json")
    )


def template_digest(manifest: TemplateManifest) -> str:
    return content_hash(manifest.model_dump(mode="json"))
