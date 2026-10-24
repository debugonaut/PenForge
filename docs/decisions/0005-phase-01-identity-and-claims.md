# 0005 — Verified identity and lossless account claiming

Status: accepted. Date: 2026-10-19. Scope: Phase 01 identity and ownership claims only.

## Context and decision

The user explicitly authorized Phase 01 and routine reversible implementation choices.
[v3 Part B §6.3](../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md) and
[the phase plan](../backend-ml-infra/phase-01-identity/plan.md) govern identity. No provider
provisioning, cloud storage selection or training was authorized by that Phase 01 request.
Phase 02 was authorized separately; its project persistence decisions are recorded in
[ADR 0006](0006-phase-02-cloud-projects.md). This ADR does not authorize later phases.

Firebase Admin Python 7.7.0 verifies Bearer ID tokens with an explicit project and
`check_revoked=True` on every identity request. The verifier returns verified claims with
no internal user ID; the transactional identity service resolves that ID. Invalid claims,
expiry, wrong project, revoked/disabled/deleted users fail with safe 401 errors. Dependency
failures are safe 503 errors. The extra revocation lookup cost is unmeasured.

Authentication defaults to disabled. Fake authentication remains a local unit-test adapter
and cannot back the public identity routes. The real emulator needs explicit local/test
allowance, a matching process host, loopback and a `demo-` project; preview/production
reject it. Hosted identity itself is blocked until shared rate limiting is implemented.
See [current Firebase evidence](../backend-ml-infra/phase-01-identity/firebase-compatibility.md).

## Persistence, transfer and history

Concurrent first requests insert a single UID-mapped user and guest counter in one
transaction. Upgrades are monotonic and preserve internal ID, timestamps, creation counts
and any existing nondefault tier. New guest counters start at the required three creations.
Deletion does not refund creation history. The Phase 01 decision anticipated project
creation enforcement in Phase 02; [ADR 0006](0006-phase-02-cloud-projects.md) records its
atomic counter, idempotency, and no-refund implementation.
No other quota budget is fabricated. `/me` reports configured current-period balances,
unknown configuration explicitly, and available amounts clamped to zero when over limit.

Same-UID linking needs only the next `/me`; an optional same-UID claim receipt is supported.
Transfer requires a registered destination and verified anonymous source, active accounts,
and source `auth_time` within the configured 600-second default. Token refresh does not
renew `auth_time`. In-place upgraded identities reject their older anonymous principal.

Claims lock both user rows in stable UUID order and atomically move projects, standalone
and project assets/jobs, user/global quota reservation owners and user balances. Target
balances absorb committed/reserved amounts without quota rejection; source limits are
retained when no matching target balance exists. No limit is added to gain allowance.
Both counters and original creator/approval/snapshot/export provenance remain intact.
Immutable receipts replay the exact original response; competing destinations cannot win.
Guest merged state is final and rejects later access or owner writes.

Two existing uniqueness rules needed reconciliation to preserve work:

- Ready originals colliding on hash remain ready and retain their bytes, keys and references.
  The transferred object becomes a same-owner canonical alias; uniqueness applies to
  canonical originals. Every alias must still be inventoried for purge.
- Jobs retain immutable `idempotency_scope_user_id` from their original owner, so two
  independently valid jobs with the same key can coexist after transfer. Authorization
  always uses current `owner_user_id`. Future job creation/replay must use the requesting
  principal's original scope for its own key, never search all claimed keys by current owner.

DB owner-write guards lock users before inserts/updates, serialize deletion and worker
state races with claims, and validate final parent/child/balance/reservation ownership.
Existing running job leases and attempts survive transfer; no worker runtime is introduced.
Historical object prefixes never authorize access. An indexed owner-scoped asset inventory
includes historical keys; prefix-only deletion would be incorrect.

Claiming never grants training consent. Any legacy guest contributions/consents are
withdrawn atomically, keeping historical provenance. No contribution API is introduced.

## Frontend and abuse controls

An isolated `/account` screen uses the generated client through the service boundary.
Firebase JS 13.0.0 loads only after an explicit configured action. Session auth is tab scoped;
guest proof stays in memory and transfer requires a separate approval. Existing demo
projects and Writing Desk storage/services remain independent. No cloud project, draft
migration, handwriting upload or consent write occurs on sign-in.

The injected limiter is bounded and concurrent but process local, usable only in local/test.
Request, first-identity/IP, first-identity/global and claim windows use configurable safety
defaults, not measured capacity. Only HMACed direct-peer IPs leave the boundary; forwarded
headers are untrusted. Rotation versions domain-separate hashes. Production needs a shared
PostgreSQL atomic-window store, expiration cleanup, trusted ingress and a stable secret
rotation policy before identity can be enabled. App Check enforcement remains a hosted gate.

## Migration, rollback and consequences

Additive `0002_identity` upgrades seeded `0001_initial`, keeps metadata aligned, and adds
receipts, origin scope, indexes and guards. A pre-claim downgrade is tested. Once receipts
exist downgrade refuses: restoring older uniqueness constraints could conflict with
preserved work. Use a forward repair; never delete users or work to undo a claim. Runtime
roles must not own/inherit migration ownership; audit append-only remains enforced.

Phase 07 must reconcile authorized account erasure with receipt/origin-history foreign keys
and receipt immutability before any account deletion API is enabled. Cross-project canonical/alias references also
require promotion or dependency ordering during project purge so a retained alias is never
lost with another project’s canonical asset. This phase tests the
account-state/deletion race, not the later purge saga. Phase 01 evidence predated the Phase 02
metadata project routes; see [ADR 0006](0006-phase-02-cloud-projects.md). Worker publication
remains a later phase; owner-scoped service and DB checks establish its identity boundary.

The original v1 contract baseline is retained. New identity fixtures and additive OpenAPI
Bearer schemes are allowed only without changing retained operations' security contracts.
No scientific measurement or production-readiness claim follows from these checks.
