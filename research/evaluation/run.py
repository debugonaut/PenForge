"""Report reviewed local observations using the existing governed record format."""

import argparse
import json
from pathlib import Path

from research.evaluation.harness import report
from research.models import BenchmarkRecord, DatasetManifest, WriterRegistry
from research.validate import load_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.observations.stat().st_size > 32 * 1024 * 1024 or args.output.exists():
        raise SystemExit("Invalid input size or existing output.")
    try:
        source = json.loads(args.observations.read_bytes())
        records = [load_record(p) for p in sorted(Path("research/manifests").glob("*.yaml"))]
        registry = next(r for r in records if isinstance(r, WriterRegistry))
        datasets = [
            r
            for r in records
            if isinstance(r, DatasetManifest) and r.dataset_id in source["dataset_ids"]
        ]
        result = [
            report(
                BenchmarkRecord.model_validate(run["template"]),
                run["observations"],
                datasets,
                registry,
                run["metadata"],
            ).model_dump(mode="json")
            for run in source["runs"]
        ]
        # A directory of ordinary benchmark_record JSON/YAML files; never execute
        # recorded commands or accept licenses/downloads from these records.
        args.output.mkdir(parents=True)
        for i, record in enumerate(result):
            (args.output / f"report-{i:04d}.yaml").write_text(
                json.dumps(record, sort_keys=True, indent=2) + "\n"
            )
    except (ValueError, KeyError, StopIteration, OSError):
        raise SystemExit("Evaluation admission failed; inspect inputs privately.") from None


if __name__ == "__main__":
    main()
