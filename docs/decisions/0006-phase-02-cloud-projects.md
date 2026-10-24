# ADR 0006 — Phase 02 cloud projects and safe persistence

Date: 2026-10-19  
Status: Accepted for the locally implemented Phase 02 slice.

## Context

Phase 01 supplies verified Firebase identity and ownership-claim history, while the existing Workbench persists full handwriting drafts locally. Phase 02 must introduce cloud project metadata without treating the demo's `FontProject` as a server DTO or silently moving user content. The v3 source requires owner-scoped APIs, the three-creation guest limit, idempotency, conditional writes, and truthful deletion behavior.

Source links: [v3 §6.3–6.4, §6.5, §6.8 and API summary §8](../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md); [Phase 02 plan](../backend-ml-infra/phase-02-projects/plan.md); [Phase 01 identity and claim decisions](0005-phase-01-identity-and-claims.md); [Phase 00 contract boundary](0003-canonical-contract-boundary.md).

## Decisions

1. Project APIs use the existing router → service → repository layering. Ownership comes from the verified principal and transactional identity resolution. List cursors are deterministic keysets ordered by `(updated_at DESC, id DESC)`, signed with an HMAC and bound to the current owner. The API exposes only validated project metadata; mutable fields in this phase are name and bounded style controls.
2. Guest project creation is capped at three for the lifetime of the identity. The counter update, project insert, and idempotency receipt complete in one PostgreSQL transaction. Exact retries replay the stored response before checking quota; failed requests roll back the reservation and receipt. Deletion never refunds a guest creation. Existing same-UID identity upgrades and Phase 01 claim/receipt history remain authoritative.
3. Idempotency is scoped by internal authenticated user identity and key. The canonical request hash covers method, path, and normalized body. The same key and body replay; a changed body returns `idempotency_key_reuse`; an in-progress row returns a retryable 409 with `Retry-After`. Receipts last 24 hours by default, configurable from 60 seconds to seven days, after which the same key may start a new operation.
4. Project row versions are exposed as strong ETags of the form `"vN"`. PATCH and DELETE require `If-Match`. Updates use compare-and-set and increment once; stale updates return 412 with a safe current-project summary only after owner scoping. Missing preconditions return 428. There is no last-write-wins path.
5. DELETE preserves owner and precondition checks, then returns typed `capability_unavailable` (503) because Phase 02 has no deletion-request saga. It does not hard-delete or mark a project deleted. Phase 07 owns coordinated deletion and physical purge evidence.
6. Cloud mode is an explicit Workbench panel and is independent of account authentication state. It sends metadata entered in that panel only; it never uploads or migrates handwriting, local drafts, or imported font bytes. Local demo storage and its resume/duplicate/delete behavior remain the default. Cloud project DTOs map to a distinct non-demo view with `fontAvailability: unavailable`.
7. Cloud import and duplication remain visibly unavailable. Phase 02 cannot preserve handwriting lineage, immutable snapshots, or creation/quota history for those operations. Local demo import and duplication are unchanged.
8. Before a metadata update is sent, the browser stores that operation's exact partial patch under an independent owner/operation localStorage key. Distinct edits from multiple tabs remain separate; no aggregate recovery collection is rewritten. An open editor owns its original project/ETag snapshot across list refresh; only explicit conflict resolution fetches a new base. The editor sends only changed fields, so rebasing a title edit keeps style values changed by another tab. An interrupted response is checked against the latest server row before retrying; an already committed patch is not written a second time.

## Recovery correction — 2026-10-19

The [follow-up review](../backend-ml-infra/phase-02-projects/review.md) identified stale-editor ETag replacement, singleton create recovery and shared-array storage races. The authorized correction uses an independent key for every create/update operation. Legacy singleton/aggregate bytes remain unchanged and readable; per-operation resolution markers prevent resolved legacy records from resurfacing. This avoids cross-tab lost updates without claiming localStorage transactions.

Pending creates expose original details/key and restore a read-only retry form. Each new create has its own key; retries load the stored original body rather than trusting form state. Identity switches fence token acquisition and late UI results. Unknown-commit evidence is retained through authorization/claim failures. An old guest request is never replayed under a destination UID after claim; its original evidence remains inspectable and independent destination operations continue. The receipt-retention limit remains authoritative.

The [acceptance evidence](../backend-ml-infra/phase-02-projects/evidence.md) records 60 frontend tests including deterministic interleaved storage operations and fresh two-tab browser checks with a populated local draft.

## Consequences and limits

Interrupted creates retain the same identity-scoped key and request body locally. Conflicting local metadata patches remain recoverable per Firebase UID and project until the owner chooses the current cloud version or rebases the local patch with the latest ETag. Imported fonts and real Writing Desk PNG/SVG output remain available on the local path.

This slice persists project metadata only. It implements no handwriting upload, glyph processing, generated personal font, model quality claim, provider provisioning, public deployment, or deletion purge. Hosted Firebase/App Check, shared rate limiting, service and dependency gates remain open; ML thresholds, training recipes, resources, and ETA remain unmeasured.
