# Worker boundary

Phase 00 reserved an independent runtime and Docker build recipe. Phase 03 now provides
the real PostgreSQL `asset_verify` worker: `FOR UPDATE SKIP LOCKED` claims, leases,
heartbeats, attempt fencing, bounded retries, isolated JPEG/PNG/WebP verification and
fenced publication through the API service. Phase 06 hardens recovery/fairness further.
API, worker and research dependency groups stay separate; no research or ML wheels enter
the API. Run with `python -m services.worker` and see the
[Phase 03 development guide](../../docs/backend-ml-infra/phase-03-capture-uploads/development.md).

Phase 04 adds the real `analyze_samples` handler for structured Latin templates and
isolated digital drawings, both in network-denied bounded children. The shared queue
executes handlers sequentially with alternating first choice. Result publication uses
quota-backed ledgers and commit-time fencing; owner review alone activates glyphs.
See [Phase 04 development](../../docs/backend-ml-infra/phase-04-extraction-review/development.md).
Phase 05 adds the real `compile_export` handler. Each requested format compiles and
passes all five validators in a network-denied bounded child. Output ledgers, reserved
budgets and commit-time lease fencing protect immutable font/report publication and
crash recovery. The worker handles formats sequentially and preserves existing capture
and analysis handlers. See [Phase 05 development](../../docs/backend-ml-infra/phase-05-font-export/development.md).
No OCR model, training, provider or public deployment is included. Phase 06 hardening
requires separate authorization.
