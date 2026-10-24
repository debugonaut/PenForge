# Phase 06 — Durable recovery, fair scheduling and resource governance

Status: implemented locally on 2026-10-20; automated checks recorded in [evidence](evidence.md). Responsive/browser acceptance is unexecuted because local browser use is disabled in Work settings. Full acceptance remains pending. Source: Part B S6, §§6.7, 6.9, 7.5, 9; Part A §§10–13.  
Depends on working phase 03–05 jobs. Hardening is incremental from 03, not a second queue rewrite.

The user explicitly authorized Phase 06 only and required sequential work without subtask agents. [Development](development.md) records the implemented settings, run commands and rollback. No Phase 07 work is authorized. The work packages below preserve the source plan; actual behavior, local measurements and unexecuted checks are distinguished in the evidence.

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md) and project handoff (`../../../PROJECT_STATUS.md`; local record) before implementation. Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) when integrating the frontend.

## Outcome

Real processing survives worker loss, respects user/global limits and communicates actual progress. Retries never replace approved work or quietly switch to an inferior method.

## Work packages

1. Harden atomic claiming by supported job types, payload/result schema versions, scripts and validated resource profiles. Use priority, fewest currently running owner jobs and age; enforce configured per-owner/global concurrency with a transaction/advisory lock where strict caps require serialization.
2. Heartbeat and every state/checkpoint/result write use attempt fencing plus unexpired lease and active account/project checks. Reaping expiry atomically queues retry or terminal failure/cancel, records events and settles reservations once. A stale process must stop; an attempts value alone is insufficient after cancellation or lease expiry.
3. Implement content-addressed checkpoint records from input/template/model/config/algorithm/dependency hashes. Verify hashes/schema before reuse; changed inputs invalidate downstream stages. Publish stage blobs → immutable manifest → fenced DB reference. Reconcile orphan publication safely after a crash.
4. Add bounded retry/backoff+jitter, poison detection and cancellation at safe substep boundaries. Malformed/security-invalid data fails permanently; needs_better_input succeeds with retake flags. Resource unavailability may wait for qualified capacity, never silently downgrade model quality. Graceful shutdown stops claims, checkpoints and requeues without penalizing clean termination.
5. Atomic quota reservations lock balance rows in stable ID order and reserve both user and global capacity in the job transaction. Commit actual usage; release unused amounts on failure/cancel/expiry. Validate committed usage cannot silently exceed hard caps. Reconcile orphan/stale reservations. Limits/windows are versioned configuration derived from measurement.
6. Introduce degradation levels while preserving accuracy-first reconciliation. Disable unavailable optional features, reduce concurrency, queue or reject new work with Retry-After. Reads, corrections and validated downloads continue. A cheaper algorithm is allowed only when independently qualified for the same input class.
7. Add real progress counters by stage: completed/total units and basis, stage status, indeterminate cases and changed denominator explanation. ETA stays null/insufficient_data until a matching script/style/model/hardware/input benchmark exists; include queue wait separately from active processing and user review.
8. Implement GET job polling baseline and equivalent SSE from durable job_events. Resume Last-Event-ID; heartbeats; bound stream lifetime/connections. Poll events server-side, without LISTEN/NOTIFY. Reauthorize ownership/account state throughout long-lived streams. Frontend reconnect falls back to adaptive polling without resetting progress.
9. Support scheduled and persistent workers using one protocol, memory-aware model cache and stage watchdogs. Optional self-hosted advanced workers receive scoped access through the phase 11 identity design; they are not required for public English availability.

## Acceptance tests

- Many workers claim once per attempt; strict concurrency caps and fair scheduling hold under load. Query EXPLAIN checks cover claim/reaper indexes.
- kill -9 mid-stage resumes after expiry from the last valid checkpoint. Corrupted/mismatched checkpoints are rejected. Worker killed after two pages resumes page three.
- Expired/stale worker heartbeat, checkpoint, candidate registration and export finalization all fail. Cancellation/deletion racing publication cannot leak new artifacts.
- Retry backoff, exhausted attempts, poison failures and graceful SIGTERM settle state correctly. Terminal states cannot reopen.
- N simultaneous reservations near the limit never exceed user/global budgets; failure/cancel/expiry release exactly once. Stable lock ordering avoids deadlocks.
- SSE/polling agree; disconnect/reconnect with Last-Event-ID yields ordered safe events; no source text/images/tokens enter events. Stream revocation works after account/ownership changes.
- Unknown ETA is honest; quality policy remains unchanged during capacity shortage. No fabricated time-driven percent.

## Evidence and rollback

Deliver chaos/concurrency reports, measured stage/queue RSS/latency profiles, fairness results and updated contracts. Test old queued payloads with new workers and N-1 support. Rollout workers supporting new schemas before emitting them. Rollback stops new schema emission and drains compatible jobs; do not truncate the queue or destroy checkpoints.

## Open gates

Numeric concurrency/time/memory/node thresholds require measurements. Provider pooler/SSE runtime limits and service identity are resolved in 11, not inferred from local success.
