"""Read a measured baseline for the owner's launcher. Never executes a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.pretrained.run import validate_parent


def adaptation_reason(report: dict) -> str | None:
    """Record observed errors, without inventing an owner review or quality gate."""
    validate_parent(report, "baseline", report.get("lineage", {}))
    metric = report["metrics"]["pretrained"]
    if all(metric[key] == 0 for key in ("wer", "scalar_cer", "grapheme_cer")):
        return None
    return (
        "Owner-launched bounded Hindi transfer pilot. Measured validation baseline: "
        f"whole-word error {metric['wer']:.2%}, scalar CER {metric['scalar_cer']:.2%}, "
        f"grapheme CER {metric['grapheme_cer']:.2%}. Explore whether the fixed adaptation "
        "reduces these observed errors; this is not a production quality threshold."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_report", type=Path)
    args = parser.parse_args()
    try:
        reason = adaptation_reason(json.loads(args.baseline_report.read_text()))
    except (ValueError, KeyError, TypeError, OSError):
        raise SystemExit(
            "A valid measured baseline is required; fine-tuning was not started."
        ) from None
    if reason is None:
        # A distinct exit lets the owner launcher skip unnecessary fitting.
        raise SystemExit(3)
    print(reason)


if __name__ == "__main__":
    main()
