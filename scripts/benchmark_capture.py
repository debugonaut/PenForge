"""Synthetic template verification and extraction through the real isolated parsers."""

import hashlib
import tempfile
import time
from pathlib import Path

from packages.capture_safety import DecodeLimits, verify_image
from packages.extraction import load_template, template_hash
from packages.extraction.fixtures import synthetic_sheet
from packages.extraction.isolated import analyze_isolated
from scripts.phase11_pg_benchmark import stats


def run_capture(samples=3):
    manifest = load_template(0)
    source, _ = synthetic_sheet(manifest)
    digest = hashlib.sha256(source).hexdigest()
    verify_times, extract_times, candidate_counts = [], [], []
    for _ in range(samples):
        with tempfile.TemporaryDirectory(prefix="penforge-capture-benchmark-") as temporary:
            working = Path(temporary)
            original = working / "original.png"
            original.write_bytes(source)
            started = time.perf_counter()
            verified = verify_image(
                original,
                declared_mime="image/png",
                expected_sha256=digest,
                output_dir=working,
                limits=DecodeLimits(),
                cancel_check=lambda: None,
            )
            verify_times.append((time.perf_counter() - started) * 1000)
            if verified.original_sha256 != digest or not verified.derived_path.is_file():
                raise RuntimeError("verification_output_invalid")
            analysis = working / "analysis"
            analysis.mkdir()
            (analysis / "source").write_bytes(source)
            started = time.perf_counter()
            report = analyze_isolated(
                analysis,
                dict(
                    source_hash="sha256:" + digest,
                    source_type="template_scan",
                    manifest=manifest.model_dump(mode="json"),
                    manifest_hash=template_hash(manifest),
                    image_config={},
                ),
                cancel_check=lambda: None,
            )
            extract_times.append((time.perf_counter() - started) * 1000)
            if report["state"] != "analyzed" or report["reasons"] or not report["candidates"]:
                raise RuntimeError("extraction_output_invalid")
            candidate_counts.append(len(report["candidates"]))
    return dict(
        status="passed",
        fixture="synthetic_structured_template_page_0",
        samples=samples,
        source_sha256="sha256:" + digest,
        source_bytes=len(source),
        verification_ms=stats(verify_times),
        extraction_ms=stats(extract_times),
        candidate_counts=candidate_counts,
        raw_ms=dict(verification=verify_times, extraction=extract_times),
        recognition_or_handwriting_quality_measured=False,
    )
