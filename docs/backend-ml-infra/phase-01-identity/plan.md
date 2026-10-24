# Phase 01 — Firebase identity, guests and account claiming

Status: implemented and locally verified on 2026-10-19; hosted checks remain gated. Source: Part B S1, §§6.3, 10; Part A privacy/ownership constraints.  
Depends on phase 00. Unlocks phase 02.

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md) and project handoff (`../../../PROJECT_STATUS.md`; local record) before implementation. Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) when integrating the frontend.

[Actual acceptance evidence](evidence.md), [development/configuration](development.md),
[Firebase compatibility](firebase-compatibility.md) and [ADR 0005](../../decisions/0005-phase-01-identity-and-claims.md) record the implementation. The requirements below remain the phase scope; they are not evidence of hosted execution.

## Outcome

Every API operation has a verified server-side identity. Guests retain their work when upgrading or claiming it from an existing account. Authentication does not move handwriting into training datasets.

## Work packages

1. Add a Firebase verifier behind the S0 protocol. Accept Bearer ID tokens only; verify signature, issuer, audience/project, expiry and sensitive-operation revocation. Derive anonymous/provider state from verified claims. No owner, user tier or UID supplied in request bodies is authoritative.
2. Build owner-scoped identity repositories and services. Upsert `users` by Firebase UID exactly once; update last_seen and in-place anonymous upgrades; create `guest_usage` in the same transaction. Use injectable clock/IDs. Reject suspended/deleting/merged identities consistently.
3. Implement `GET /api/v1/me` with identity, tier, guest creation allowance and available quota summary. Do not expose service credentials, raw tokens or internal storage keys.
4. Implement claim Case A: UID unchanged after Firebase linking means no content moves. Implement Case B: new registered token plus old anonymous token, both verified; recent guest auth_time; active destination; no previous claim to a different destination. Repeated identical claims return the same result.
5. Make claim transactional across projects, assets, jobs and reservations. Document asset-prefix ownership: historical storage keys cannot grant access or be omitted during later purge. Lock both users in a stable order to prevent competing claims. Existing jobs may continue only under the updated ownership and valid lease rules.
6. Claim is not denied because it puts the destination above quota. Record the over-limit state and restrict subsequent work through quotas, preserving already-owned data. Creation counts are not fabricated/reset to farm quota.
7. Add a rate-limiter interface and local/emulator tests. Decide a multi-instance PostgreSQL implementation before deployment; anonymous identity minting is not a sufficient abuse control. Store an HMAC of IP only when needed; document secret rotation. Evaluate App Check with current Firebase feature/plan evidence, not assumptions.
8. Add Firebase Auth emulator fixtures for anonymous, password, Google/linking and claim flows. Keep fake auth confined to local unit tests. Introduce frontend auth state through the existing service boundary; no landing redesign and no silent cloud import of local drafts.

## Data, API and migrations

Use `users`, `guest_usage`, `audit_events` and ownership columns from S0. Add only constraints/indexes found missing by integration tests. `POST /identity/claim` is naturally idempotent, with safe response metadata. Audit counts/actions only; never write guest tokens to audit or error fields.

## Acceptance tests

- Valid anonymous token creates exactly one identity, including concurrent first requests. Missing, malformed, forged, expired, wrong-project and revoked tokens fail with typed 401 errors.
- In-place linking preserves internal ID, all projects and creation history while updating the registered tier.
- Claim moves every applicable owner reference atomically. Another user gets 404 for claimed resources; the stale guest principal cannot continue access.
- Two competing destinations cannot both claim; replay to the same destination succeeds; replay to another fails safely. Old auth_time is rejected.
- Suspended/deleting accounts and anonymous destination claims are rejected. Destination over-quota does not lose the guest's work.
- Account deletion or job execution racing a claim cannot leave mixed ownership. New tests cover historical key prefixes and purge inventory.
- Rate limits produce Retry-After and rate headers. Logs never contain tokens, email/password credentials or raw IP.
- Existing local demo remains usable without Firebase configuration. Auth unavailable is explicit rather than silently treated as a verified guest.

## Evidence, rollout and rollback

Publish emulator results, claim transaction/lock tests and official Firebase compatibility references dated at implementation. Run hosted feature checks only after configuration is approved. Roll out auth behind an explicit service configuration; roll back frontend service selection without deleting local drafts or cloud users. Do not delete Firebase users to undo a claim.

## Open gates

Firebase project configuration and plan eligibility, App Check, guest contribution restrictions and Q7 credential policy. None are silently resolved by using the emulator. Project creation's final three-creation transaction is delivered in phase 02.


## Later owner-approved service policy — 2026-10-24

A separate service-accountability domain now requires registered/verified accounts
and current Terms/rights receipts for creation, edits and raw object transfers.
Existing identity recovery, optional training consent/withdrawal and account-erasure
routes have no new Terms gate. Migration 0014 receipts are removed by the validated
account-deletion saga; new audit admissions use the existing stable privacy HMAC format.
Public legal/operator/provider gates remain open. See
[local implementation evidence](../../accountability/evidence.md). Earlier acceptance
results and pending browser/hosted checks remain unchanged.
