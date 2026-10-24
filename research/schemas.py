"""Export or check deterministic research JSON Schema without ML dependencies."""

import argparse
import json
from pathlib import Path

from research.validate import RECORD_TYPES

SCHEMA_ROOT = Path(__file__).resolve().parent / "schemas/v1"


def generate(check: bool = False) -> list[str]:
    mismatches = []
    for name, model in sorted(RECORD_TYPES.items()):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://penforge.invalid/research/v1/{name}.schema.json"
        expected = json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        path = SCHEMA_ROOT / f"{name}.schema.json"
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                mismatches.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
    return mismatches


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    if generate(options.check):
        raise SystemExit("Research JSON Schemas have drifted; run python -m research.schemas")
