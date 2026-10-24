# Phase 02 — Cloud projects and conflict-safe persistence

Status: implemented locally; see [acceptance evidence](evidence.md) and [development instructions](development.md). Cloud deletion handoff, local import, and cloud duplication remain typed/unavailable boundaries. Source: Part B S2, §§6.3–6.4, 7; Part A contract preservation.  
Depends on phases 00–01. Unlocks phase 03.

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md) and project handoff (`../../../PROJECT_STATUS.md`; local record) before implementation. Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) when integrating the frontend.

## Outcome

The existing Workbench offers an explicit opt-in cloud metadata panel for owner-scoped create, list, read and update. Saves handle retries and concurrent edits without overwriting another revision. Guests can make exactly three lifetime cloud creations under the documented policy. Cloud resume/font processing remains unavailable because Phase 02 persists no glyph data.

## Work packages

1. Implement `POST/GET /projects`, `GET/PATCH /projects/{id}` through router → service → owner-scoped repository. Validate name length, script code, bounded style controls and state transitions. Use cursor pagination with deterministic ordering and bounded page size.
2. Project creation transaction atomically increments guest usage only when below three, inserts the project, and completes the idempotency record. Deleting a project does not refund a guest creation; record this proposed anti-abuse policy in an ADR. Registered quotas remain configurable from measured tier budgets.
3. Implement canonical request hashing and idempotency storage. Scope keys to the authenticated identity and include method/path/body hash. Same key/body replays original status/body; different body returns 422; in-progress returns 409/Retry-After. Resource creation and key completion commit together. Reapers respect the documented TTL.
4. Add strong ETags from row_version. PATCH and DELETE require If-Match, with compare-and-set in the transaction. Missing → 428, stale → 412 including only the owner's current safe summary. Successful updates advance version exactly once. No unconditional last-write-wins fallback.
5. Define deletion honestly. Implement the route as a deletion-request handoff and block mutation when deleting; actual physical purge is phase 07. If the saga is unavailable before that phase, expose a typed unavailable capability rather than claim deletion completed. Never hard-delete user project rows as a temporary shortcut.
6. Add a real ProjectService adapter beside the mock. Map server DTOs to an explicit non-demo project view model. Preserve recovery drafts, typing, route navigation and existing visuals. Local-only → syncing → saved → conflict/offline states must be distinguishable. Cloud import of a local project is an explicit action with preview and failure recovery.
7. Define duplicate behavior before enabling the current duplicate control in cloud mode: new project/creation, copied approved metadata only as appropriate, lineage preserved and quota charged atomically. If not implemented in this slice, disable that cloud capability with accurate copy while retaining local demo behavior.

## Persistence and contracts

Use projects, guest_usage, idempotency_keys and future deletion_requests. Confirm nullable active_snapshot/style data defaults and all owner indexes. List/get serializers must not leak cross-owner metadata. Update OpenAPI, generated client and adapter fixtures with schema versions and clear resource states.

## Acceptance tests

- The third guest creation succeeds; the fourth fails with 403 guest_limit_reached. Many concurrent creates near the boundary cannot exceed it.
- Parallel same-key creation yields one resource and one usage increment. Replay is byte-equivalent semantically; body changes are rejected.
- All list/get/update/delete paths return 404 for foreign IDs, including cursor and error-detail paths. Suspended/deleting principals remain blocked.
- Two tabs editing one row produce a real 412; current/local content remains available for resolution. No save timer resurrects a deleted project.
- Pagination neither duplicates nor skips items under the defined cursor ordering. Invalid names/scripts/unknown fields are rejected consistently.
- Interrupted network saves recover without duplicate projects; local drafts remain intact. Refresh, back/forward and current Workbench behavior remain usable.
- Transaction tests use PostgreSQL and deliberately concurrent connections. Explain/index checks cover owner project listing.

## Evidence and rollback

Deliver API/concurrency/guest-boundary integration tests, generated client, adapter tests and Workbench recordings. Keep cloud mode opt-in until the full M1 gate. Rollback stops new cloud writes and restores the local demo choice without erasing server projects. Additive migrations remain readable by the previous release.

## Implemented boundary

The create/list/get/update APIs and metadata adapter are implemented. The Workbench stays local by default; connecting the account alone does not access the cloud-project routes. Cloud cards do not resume a mock font project. Updates persist each partial local patch before sending; 412 and interrupted responses remain recoverable, and a retry checks the latest row to avoid duplicating a write that already committed. Separate tabs' recovery patches do not replace one another. Deletion checks owner and ETag, then reports `capability_unavailable` because the Phase 07 deletion saga is absent. Local import and cloud duplication remain disabled with reasons tied to handwriting/snapshot lineage. These capability limits preserve the local demo paths and do not claim cloud glyph processing.

## Open gates

The [follow-up review](review.md) identified three frontend recovery defects that require fixes and targeted regression/browser evidence before Phase 03. Existing passing tests do not establish those missing recovery guarantees.

Wire casing resolved in 00; quota values remain measured/configurable. Full deletion and cloud-wide abuse protection must pass 06/07/11 before public launch.
