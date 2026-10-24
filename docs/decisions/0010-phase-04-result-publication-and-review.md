# ADR 0010 — Fenced proposals, owner review and immutable milestones

Date: 2026-10-20. Status: accepted for local Phase 04.

## Decision

Analysis uses the existing durable PostgreSQL queue with separate typed payloads.
Verification and extraction are dispatched sequentially. Track A consumes verified
original image bytes and the pinned page manifest. Track C consumes immutable stroke
JSON and an explicit isolated-character destination/frame; no OCR or image segmentation
identifies a digital drawing. Natural pages return isolated-sample guidance.

The entire analysis parser runs in a bounded private subprocess with OS network denial,
a live deny probe, CPU/file limits and parent-enforced wall/RSS caps. API processes import
contracts and orchestration only. Crop/mask/outline/result bytes have immutable owner-
scoped storage records. A quota-backed staging ledger commits before storage writes.
Writes take the same owner/project/job locks as claims, deletion and reaping, preventing
a stale writer from recreating objects after cleanup releases capacity. SIGKILL recovery
uses the ledger; ready historical results are never treated as abandoned attempt output.

Publication validates result schema and content hash, current owner/project, input byte
hashes, pinned template, exact pipeline and configuration, attempt identity, child sizes/
hashes/media types, source mapping and authentic/manual provenance. Unknown recognition
or fidelity scores remain null. A live-attempt insert guard and deferred completion
trigger enforce fencing through transaction commit. Migration 0006 adds immutable result/
candidate lineage and revision provenance; 0007 adds the live and commit-time fences.

Workers create pending sibling proposals. A separate non-owner worker role receives no
revision, snapshot, active-glyph or candidate-review privileges. Local tooling may still
use administrator credentials; hosted operation requires distinct credentials and role
membership review. The grant helper configures an existing role and provisions nothing.

Selecting a candidate creates a pending immutable revision. Owner approval is separate,
requires a strong ETag and base revision, and alone activates it. Identical saves are
suppressed; rollback creates a new pending revision. Later jobs cannot replace an
approved selection. Outlines exceeding the safe database JSON text budget use immutable
asset references, with SQL NULL for the absent inline source. Idempotency receipts
retain original metadata and rehydrate revision geometry rather than copying large
outlines into bounded receipt rows. The API explicitly reports inline edit support.
Recommendations use valid authentic evidence and capture risk flags;
unknown recognition/style quality does not justify a confidence ranking.

Milestones use repeatable-read transactions and freeze approved revision mappings,
project style controls, copied profile values and model IDs. Space is a metric-only
explicit proposal; .notdef and alternates do not increase 52/95 inventory counts. Even a
95-character inventory has `complete_font_validated=false` until later compilation and
validation. Calibration text is unoptimized context, with isolated follow-ups when
segmentation is unavailable.

## Evidence and limits

See [Phase 04 evidence](../backend-ml-infra/phase-04-extraction-review/evidence.md).
The integration suite covers both actual isolated paths, forged artifacts, claims,
deleting state, stale attempts, lease expiry during publication, real SIGKILL cleanup,
restricted-role denials, concurrent edits and immutable snapshots. Browser verification
remains pending at the user's choice. Real capture accuracy, variable-pressure hardware
calibration, human correction burden, physical printing, hosted/container/provider/privacy
release gates remain open. Phase 05 compilation is not implemented or authorized here.
