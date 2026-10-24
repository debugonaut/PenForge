# Phase 07 local acceptance evidence

Date: 2026-10-20. Status: implemented locally; OS-gentle browser acceptance remains
unexecuted. Phase 08 is not started. Work was sequential without subtask agents.
Existing untracked files were preserved; no remote, PR, hosted CI, backup or deployment
is claimed. See the summary (`../../../artifacts/phase07/summary.json`; local record),
[data inventory](inventory.md), [run instructions](development.md),
[consent/erasure ADR](../../decisions/0016-phase-07-consent-and-erasure.md) and
[retention/restore ADR](../../decisions/0017-phase-07-retention-and-restore.md).

## Implemented boundary

Consent is explicit, purpose/version specific and off by default; guest contributions
fail closed. Product use never opts into training. Explicit selected samples copy into
separate dataset storage after metadata minimization. Live consent, withdrawal and
immutable dataset selection serialize under compatible ownership locks. No model is
trained and no model unlearning is claimed.

Project, account and contributions_only requests use the existing durable queue,
ObjectStorage and Firebase adapter. Admission revokes private access immediately.
Persisted steps cancel/wait for jobs, preserve surviving aliases, delete contributions
and known storage, purge database scope, then remove Firebase identities last. Failed
storage remains retryable. Completion clears transient identity fields and retains
HMAC references, codes, counts and timestamps. Receipt-authorized progress works after
identity deletion. Unknown provider backup expiry is disclosed.

Retention starts with a hash-checked dry run, rechecks live references and preserves
snapshot/export lineage. Unknown objects are reported, never automatically deleted.
An independent integrity-checked erasure journal prevents database-only restore from
resurrecting access. Restoring or losing that journal/key remains an operational gate.
Admission can pause while accepted withdrawals/deletions keep processing.

## Automated acceptance

- **502 backend tests passed**, with 5 emulator cases deselected; real PostgreSQL,
  not SQLite. The complete Phase 00–06 regressions are included, not added to this total.
  Combined output (`../../../artifacts/phase07/backend-final.txt`; local record).
  A final shared-contract typing correction preserved the generated wire schema;
  all **36 focused privacy tests passed afterward**. This is a rerun subset, not
  additional unique tests. Follow-up output (`../../../artifacts/phase07/privacy-final-followup.txt`; local record).
- **5 Auth-emulator tests passed separately**, including real account identity deletion
  after database/storage cleanup and receipt progress after token invalidation.
  Emulator output (`../../../artifacts/phase07/emulator-final.txt`; local record).
- **113 frontend tests passed**, including explicit privacy transport, required bodies,
  idempotency, origin/bearer validation and receipt-only progress. The total includes
  preserved account/password tests already present in the workspace.
  Frontend output (`../../../artifacts/phase07/frontend-final.txt`; local record).
- Migration head `0010_privacy`, 27 application tables: empty up/down/up, preserved
  prior-release seeded jobs/receipts, migration graph and ORM drift checks passed.
  A deferred-trigger backfill failure found during validation was corrected before
  the final suite. Ordinary immutable/audit guards remain covered.
- Ruff, formatting, mypy, import boundaries, local documentation links/source hashes,
  generated contracts/compatibility/research schemas, TypeScript and ESLint were checked.
  Quality output (`../../../artifacts/phase07/quality-final.txt`; local record) records 172 formatted
  files, 111 mypy source files and 547 valid local links with preserved source hashes.
  Production build (`../../../artifacts/phase07/build-final.txt`; local record) and
  startup smoke (`../../../artifacts/phase07/production-startup.json`; local record) passed; all seven
  application routes returned HTTP 200.

Tests cover consent policy/recent auth, cross-owner denial, explicit contributions,
withdrawal/dataset races, contribution-only isolation, storage failure/retry, owner-only
progress, legacy project DELETE/ETag, claimed guest ancestry, surviving canonical and
rejected aliases, quota settlement, scoped access revocation and immutable guards.

Crash injection runs **before and after all seven saga steps** (14 cases), resumes to
completion and asserts Firebase ordering. Content-free per-point JSON is in
`artifacts/phase07/crash-{before,after}-{step}.json`. Dedicated reports record
claimed storage cleanup (`../../../artifacts/phase07/claimed-storage-inventory.json`; local record),
actual worker-process cancellation (`../../../artifacts/phase07/real-worker-cancellation.json`; local record),
retention dry run (`../../../artifacts/phase07/retention-dry-run.json`; local record) and
restore reconciliation (`../../../artifacts/phase07/restore-reconciliation.json`; local record).
The worker test pauses an actual verification process, accepts deletion, then proves
that stale work cannot publish. Restore reconciliation is idempotent and does not
withdraw a later explicitly granted consent.

## Browser evidence and remaining limits

A local synthetic fixture exercised the real API/worker, local private storage and
Auth emulator. Privacy settings, off-by-default consent, explicit grant/cancel,
selected-sample contribution, withdrawal cleanup preserving the project, recent-auth
failure/sign-in recovery and project deletion through completed steps were exercised.
Keyboard confirmation focus, Escape and focus return passed. Ordinary motion was active.
Screenshots at 1440, 768, 390 and 320 CSS px plus 1280x600 are retained with
measured widths (`../../../artifacts/phase07/browser-responsive.json`; local record) and
workflow record (`../../../artifacts/phase07/browser-workflow.json`; local record). Initial screenshots
named `privacy-initial-*` preceded viewport/zoom correction and are not acceptance proof.

OS-gentle remains **unexecuted**: native System Settings access was not approved; no OS
setting was changed. Account deletion was tested against the real local Auth emulator,
but the final account-deletion browser interaction remains unexecuted after the browser
tab closed on interruption. Phase 03/04 current-wizard browser, Phase 05 OS-gentle and
Phase 06 browser/responsive checks remain pending exactly as before.

Hosted Firebase behavior, provider backup expiry/capability revocation, independent
journal backup, service identity/least privilege, App Check, shared rate limiting,
cloud capacity, real-handwriting quality and public readiness remain unresolved.
This local implementation makes no provider, compliance, training or launch claim.
Stop here; Phase 08 requires separate authorization and review of this evidence.
