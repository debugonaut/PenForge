# Synthetic extraction regression records

`extraction-baseline.v5.json` and `extraction-gates.v5.json` retain the byte-identical
Phase 04 synthetic aggregate and its derived gates, recorded on 2026-10-20. The source
fixture uses the licensed Caveat font; its configuration and license hashes are
retained in the report and `research/evaluation/fixtures/phase04/config.v5.json`.

These 40 records contain synthetic numeric geometry/quality/resource measurements,
configuration and code/template hashes. They contain no private handwriting, source
images, per-person records or model weights. Recognition and human writer-fidelity
metrics remain null and production validation remains false. This fixture verifies
the recorded gate relationships; reading it does not rerun evaluation or establish
current model quality. Historical source reports remain unchanged locally.
