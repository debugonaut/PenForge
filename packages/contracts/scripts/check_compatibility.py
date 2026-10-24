"""Conservative v1 compatibility check; baseline changes require explicit version review.

Existing field shapes, defaults, constraints and operation contracts remain frozen.
Only optional property/definition additions are accepted. Runtime validation changes
also need the retained v1 fixtures; JSON Schema alone cannot describe validators.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "packages/contracts"
IGNORED = {"title", "description", "examples", "$comment"}


def schema_breaks(previous: object, current: object, pointer: str = "") -> list[str]:
    if isinstance(previous, dict) and isinstance(current, dict):
        errors = []
        for key, value in previous.items():
            if key in IGNORED:
                continue
            location = pointer + "/" + key
            if key not in current:
                errors.append(location + ": removed")
                continue
            if key == "required":
                if set(current[key]) != set(value):
                    errors.append(location + ": required fields changed")
            else:
                errors.extend(schema_breaks(value, current[key], location))
        for key in current.keys() - previous.keys() - IGNORED:
            if pointer == "implemented_http_surface/components" and key == "securitySchemes":
                # New authenticated routes may declare Bearer without changing any
                # retained operation. Existing operation security is compared above.
                continue
            if pointer.endswith(("/properties", "/$defs", "/schemas", "/paths")):
                continue
            # New limits, required sets, defaults, discriminators, etc. can alter
            # existing values and are conservatively reviewed as breaking.
            errors.append(pointer + "/" + key + ": added constraint or shape")
        return errors
    if previous != current:
        return [pointer + ": changed"]
    return []


def current_contracts() -> dict:
    result = {
        str(path.relative_to(BASE)): json.loads(path.read_text())
        for folder in ("schemas", "jobs")
        for path in sorted((BASE / folder).glob("*.v1.json"))
    }
    openapi = json.loads((BASE / "openapi.json").read_text())
    result["implemented_http_surface"] = {
        "openapi": openapi["openapi"],
        "paths": openapi["paths"],
        "components": openapi["components"],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASE / "compatibility/v1-baseline.json")
    args = parser.parse_args()
    previous = json.loads(args.baseline.read_text())
    current = current_contracts()
    errors = []
    for name, schema in previous.items():
        if name not in current:
            errors.append(name + ": removed schema")
        else:
            errors.extend(schema_breaks(schema, current[name], name))
    if errors:
        print("Breaking contract changes:\n" + "\n".join(errors))
        return 1
    print("Retained v1 schema compatibility passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
