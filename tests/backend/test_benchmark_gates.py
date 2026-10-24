"""Operational benchmark output is evidence only when its required gates pass."""

from scripts.phase11_worker_benchmark import finish_report


def test_failed_or_missing_validator_and_resource_breach_fail_benchmark(tmp_path):
    base = dict(
        capture={"status": "passed"},
        peak_process_tree_rss_bytes=100,
        results=[
            dict(
                format="ttf",
                corrected_pairs=1,
                validation_statuses=["failed"],
                validation_layers={"harfbuzz": "failed"},
            )
        ],
    )
    assert finish_report(base, tmp_path / "failed.json") == 1
    base["results"][0]["validation_statuses"] = ["passed"]
    assert finish_report(base, tmp_path / "missing.json") == 1
    base["error"] = "benchmark_resource_limit"
    assert finish_report(base, tmp_path / "limit.json") == 1


def test_all_required_gates_must_pass_including_capture(tmp_path):
    from penforge_font_engine.validation import LAYERS

    report = dict(
        peak_process_tree_rss_bytes=100,
        capture={"status": "passed"},
        results=[
            dict(
                format="ttf",
                corrected_pairs=1,
                validation_statuses=["passed"],
                validation_layers={name: "passed" for name in LAYERS},
            )
        ],
    )
    report["results"] = [
        dict(report["results"][0], format=value) for value in ("ttf", "otf", "woff2")
    ]
    assert finish_report(report, tmp_path / "passed.json") == 0
    report["capture"]["status"] = "failed"
    assert finish_report(report, tmp_path / "capture-failed.json") == 1
