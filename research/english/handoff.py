"""Read a saved baseline for the owner's launcher; never runs a model."""

import argparse
import json
from pathlib import Path

from research.english.execution import adaptation_reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        reason = adaptation_reason(json.loads(args.report.read_text()))
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit(
            "Review the English baseline before fitting. No fine-tuning was started."
        ) from None
    if reason is None:
        raise SystemExit(3)
    print(reason)


if __name__ == "__main__":
    main()
