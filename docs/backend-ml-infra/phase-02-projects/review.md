# Phase 02 follow-up review

Date: 2026-10-19. Status: **three frontend recovery findings corrected and locally verified**.

This review checked the current code against the Phase 02 plan, ADR 0006 and recorded acceptance evidence. The backend metadata boundary and deliberate capability limits are appropriate. Passing existing checks do not cover all frontend recovery paths described below. Application code was not changed during the original review. The correction record below documents the later authorized fixes.

## Findings

### P2 — Refresh replaces the ETag base of an open stale editor

Historical source: `CloudProjectWorkbench.tsx`, editor state around line 164, list refresh around line 223, and update base around line 304. That unused earlier panel was removed during v1 preparation. The active [projects route](../../../src/app/projects/page.tsx) uses the [account project service](../../../src/lib/account-project-service.ts); the recovery findings and regressions below remain applicable evidence, not a requirement to retain the obsolete panel.

The editor retains original field values but no original row version. Saving looks up the current row in the refreshed project list. A user can therefore open revision 1, edit the title, allow another tab to save revision 2, refresh the list, and save the stale editor with revision 2's ETag. The server accepts the write and no conflict is presented for the overwritten same-field change.

Capture the editor's base revision together with its original values and keep that base through list refresh. Only an explicit conflict resolution/rebase should replace it. Add a regression for stale edit → remote same-field save → local list refresh → save; retain the existing separate-field merge behavior. This path was established from current state/update code; it was not freshly reproduced through browser UI in this review.

### P2 — Singleton interrupted-create recovery blocks other operations and identities

Source: [cloud-project-service.ts](../../../src/lib/cloud-project-service.ts), pending-create key around line 13 and create handling around lines 330–351; the Workbench has no pending-create restore/disposition control.

An interrupted create persists one browser-wide owner/body/key record. After reload, a different title produces `pending_create_mismatch`; a different signed-in UID produces `pending_create_identity_mismatch` for every new create. A guest claimed into another UID cannot retry as the old merged guest. The persisted body is not exposed to restore the form or inspect the unknown-commit request.

Focused execution of the current service reproduced both mismatch states after an injected network failure. Introduce owner/operation-scoped recovery and an explicit way to inspect/restore/retry pending creation details. Preserve unknown-commit evidence; avoid silently issuing a new key or discarding it. Define claim/account-switch behavior without replaying another identity's operation or stranding the destination's independent creates. Test reload, body restoration, account changes, claims and simultaneous creates.

### P2 — Aggregate recovery storage loses records under interleaved tabs

Source: [cloud-project-service.ts](../../../src/lib/cloud-project-service.ts), read/modify/write around lines 161–173 and removal around lines 230–240; existing coverage in [api-boundary.test.mjs](../../../tests/api-boundary.test.mjs) around line 591.

Each operation rewrites one localStorage array. Two tabs can read the same starting array, then overwrite it with their separate additions. Removing a resolved entry can likewise erase a concurrent addition. Recovery IDs distinguish records but do not make this aggregate update atomic.

A deterministic two-context Storage interleaving through the actual service methods left only one backup while both requests were unresolved. This is a service-level concurrency reproduction, not a fresh physical-browser tab race. The existing test named “concurrent edits” awaits operations sequentially and does not exercise this interleaving.

Use independent owner/operation storage keys or a transactional store, preserving legacy recovery bytes during migration. Add interleaved write/write and remove/write regressions, plus browser checks with two unresolved saves. Resolving one operation must not delete another operation's backup.

## Fresh verification

| Check                                | Result                                                                                               |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `make test-api`                      | 180 passed, 86 deselected                                                                            |
| `npm test`                           | 54 passed                                                                                            |
| Real PostgreSQL integration          | 82 passed, 184 deselected                                                                            |
| `make lint`                          | Passed; documentation validation passed 296 links and both source hashes after this review was added |
| `make contracts-check`               | Passed; schemas, generated client, compatibility and research metadata                               |
| `npm run typecheck` / `npm run lint` | Passed                                                                                               |

The initial PostgreSQL rerun was denied access to the local Unix socket by the sandbox. Repeating the isolated test suite with permitted local socket access passed all 82 tests. The tests use disposable per-run schemas. The shared PostgreSQL service was not restarted or reset.

The four Auth emulator tests and production build remain recorded Phase 02 results; they were not rerun in this review. No new browser acceptance session was performed. The prior browser trace used an empty local demo collection, so it does not by itself prove preservation of an already populated local draft; existing local service tests still pass.

The existing Starlette/httpx deprecation warning persists. Docker/hosted CI, hosted Firebase/App Check, shared rate limiting, provider/dependency review and the deletion handoff retain their documented gates.

## Other clarification

Owner pagination uses live `(updated_at DESC, id DESC)` keysets. A row edited between pages can move above the cursor and be absent from that traversal. Current database tests cover static pagination. Document live-list refresh semantics, and test updates between pages if the product promises stronger traversal guarantees.

## Correction verification — 2026-10-19

The user subsequently authorized correcting all three findings before implementing Phase 03. The open editor now owns an immutable base project/ETag snapshot; list refresh cannot replace its precondition. Explicit conflict recovery fetches the latest row and applies only the fields changed locally.

Each pending create and update recovery has an independent owner/operation localStorage key. New creates are independent operations; restored creates retry the stored original body and idempotency key. The Workbench exposes original details/key and a restoration action. Identity switches clear the old service/editor, fence late results and token acquisition, and leave original recovery evidence intact. A claimed guest's old key is never replayed under the destination UID; its inspectable evidence remains available while destination creates work independently. Account-switch/claim paths are covered by tests, not a fresh browser claim session.

Legacy singleton/aggregate bytes are retained unchanged. Resolution writes an operation-specific legacy marker and removes only that operation's new key. Malformed legacy bytes are retained. No shared recovery array is rewritten.

`npm test` passed **60 frontend tests** at correction time. New regressions include stale editor → remote same-field save → list refresh → 412 → explicit field merge; reload/restoration of original create details/key; independent owners and simultaneous creates; retained evidence after a merged-guest error; identity changes during token retrieval; legacy preservation; and actual storage `setItem` write/write and `removeItem`/write interleavings while requests remain unresolved. These replace the prior sequential test's unsupported concurrency implication. TypeScript and ESLint passed.

Subsequent Phase 03 work preserved these regressions and added capture-service/storage tests; the current frontend suite is **78 passed**. See [Phase 03 acceptance evidence](../phase-03-capture-uploads/evidence.md).

Fresh two-tab browser acceptance passed: stale save after list refresh received 412 and rebased; an interrupted create restored after reload and retried the exact body/key; two unresolved tab saves produced two operation keys, and resolving one retained the other. An existing populated local drawing draft remained byte-identical throughout. The machine-readable browser result (`../../../artifacts/phase-02/recovery-followup-browser.json`; local record) reports no page errors; the two-tab recovery screenshot (`../../../artifacts/phase-02/recovery-followup-two-tabs.png`; local record) records the session. This is additional evidence; the original review/check results above remain historical. The same two-tab scenario was rerun against the current API during the Phase 03 closeout with the same result.

## Handoff

The three recovery corrections are verified locally. Preserve field-level merging, owner isolation, immutable lineage, local demo behavior and accepted design/motion as the separately authorized Phase 03 proceeds. Hosted/provider, deletion and research gates remain unchanged.
