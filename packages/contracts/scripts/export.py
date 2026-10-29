"""Export deterministic schemas/OpenAPI; --check fails on committed artifact drift."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "apps/api"), str(ROOT / "packages/contracts")]

from penforge_contracts import (  # noqa: E402
    CONTRACT_MODELS,
    AnalyzeSamplesJobV2,
    AnalyzeSamplesJobV3,
    contract_registry,
)


def artifacts() -> dict[Path, dict]:
    from app.main import create_app

    base = ROOT / "packages/contracts"
    result = {
        base / "openapi.json": create_app().openapi(),
        base / "registry.json": contract_registry().model_dump(mode="json"),
    }
    for name, model in CONTRACT_MODELS.items():
        folder = "jobs" if name.endswith("_job") else "schemas"
        result[base / folder / f"{name}.v1.json"] = model.model_json_schema()
    result[base / "jobs/analyze_samples_job.v2.json"] = AnalyzeSamplesJobV2.model_json_schema()
    result[base / "jobs/analyze_samples_job.v3.json"] = AnalyzeSamplesJobV3.model_json_schema()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = []
    for path, value in artifacts().items():
        encoded = json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != encoded:
                changed.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded)
    if changed:
        print("Contract drift: " + ", ".join(changed), file=sys.stderr)
        return 1
    print("Contract artifacts match." if args.check else "Contract artifacts exported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
