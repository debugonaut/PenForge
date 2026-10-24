# Phase 03 follow-up review

Date: 2026-10-19. Status: frontend and backend continuation findings corrected;
fresh backend checks passed; follow-up browser acceptance blocked by unavailable browser
access and Computer Use permissions.

This follow-up reviews the locally implemented capture flow after the historical
[Phase 03 acceptance](evidence.md). It does not authorize Phase 04 or establish hosted,
container, deletion or ML readiness. Historical browser artifacts remain distinct from
new automated checks below. The later user instruction authorizes closing Phase 03 and
then Phase 04 only; this review itself grants no additional scope.

## Backend continuation findings and corrections

The continuation probes found truncated JPEG entropy accepted despite a valid EOI,
embedded PNG data accepted after complete scanlines, and stale derivative bytes left
unaccounted after a crashed worker. Existing fixes were preserved and freshly verified:

- Strict JPEG validation reads all coefficients through the pinned Pillow/libjpeg
  dependency and treats native corruption warnings as rejection. The tested v6b LP64 ABI
  is explicit; another ABI fails closed until separately supported.
- Strict PNG validation checks bounded scanline sizes/filters, complete single-stream
  zlib consumption, chunk integrity/order and Adam7 passes. Embedded bytes, second streams,
  incomplete/extra scanlines and unfinished streams are rejected; valid interlaced and
  split-IDAT fixtures pass.
- Fenced staging removes prior attempt derivatives before writing, rechecks lease expiry
  after storage returns, and uses the persisted attempt budget for terminal cleanup.
  Decoder temporary files live within the attempt tree. A real SIGKILL-after-write test
  leaves files, then proves a subsequent attempt removes stale output, publishes once,
  leaves no orphan/temp files and commits exactly the physical original plus thumbnail
  bytes to both quota balances.
- Additive `0005_publication_fence` checks the live verification lease at commit. Expiry
  between finalization and commit rolls back the transaction. Seeded previous-release
  succeeded and queued jobs survive the migration unchanged.

Fresh execution: **325 backend tests passed** (213 non-integration and 112 real
PostgreSQL tests), plus **4 real Auth-emulator tests passed**. Focused decoder/storage
and upload/actual-worker runs passed **47** and **26** tests respectively. Ruff/format,
mypy, import boundaries, document/source integrity and contract compatibility/drift checks
passed. [The evidence](evidence.md) links each captured output and the corrected synthetic
resource measurements. Historical counts are not used to prove these corrections.

## Frontend findings and corrections

### Duplicated image drafts retained a foreign local-original reference

The local project duplicate operation copied `sample.originalCaptureId` into the new
project, while private capture correctly requires the original vault record's
`localProjectId` to match the submitting draft. A copied draft therefore displayed its
image but could not upload it as a private source.

The duplicate now retains its local preview and filename while clearing only the copied
original reference. The source draft and original vault bytes remain untouched. The
private capture panel explains that the original image must be selected again for this
draft and disables private upload until that explicit selection. Local demo review and
extraction remain available. A regression verifies the copied preview, absent copied
reference and unchanged source sample.

### Late capture responses could replace account-change feedback

Account changes already prevented token acquisition under another UID, but an earlier
list or submission could still complete its error/finalizer after the panel had cleared
the previous account. This could restore stale feedback, project rows or progress.

The panel now fences list results, submission progress, error handling, recovery reloads
and finalizers by request/lifecycle generation. Identity changes abort the current check,
clear the old destination and require an explicit new connection. Initial identity
hydration is supported on the first connection. Recovery records and original bytes are
retained; old-UID operations are never replayed under a destination UID after claiming.

Two component-function tests exercise the actual handlers with minimal hook/dependency
adapters: initial identity hydration followed by an account switch during a pending list,
and an old submission's late failure/progress after a switch. These are automated handler
tests, not browser or React DOM acceptance.

### Retryable project-create HTTP failures did not restore the primary retry form

The Phase 02 service retained an original create body/key on all failures, but the
Workbench automatically restored only its custom network error. A typed HTTP 503/409
could leave the primary form in independent-create mode despite a retained unknown-commit
request.

The Workbench now uses `pendingCreateFromError`, which validates the attached local
request before restoring it for any create failure. An explicit independent-creation
action remains available. A regression exercises typed HTTP 503, restoration of the exact
body/key, same-owner replay and rejection under another identity. The previously verified
open-editor ETag snapshot, explicit field-level rebase and per-operation recovery keys are
preserved.

## Automated frontend verification before the later wizard revision

- `npm test`: **84 passed**. This includes the prior 78 tests plus duplicate-image
  recovery, two handler identity-race tests, HTTP-create restoration and two same-operation
  confirmation regressions.
- `npm run typecheck`: passed.
- `npm run lint`: passed.
- Existing capture service tests exercise interrupted initialization, lost completion and
  sample responses, original-byte preservation, owner/source mismatch rejection, duplicate
  alias registration and terminal verification retake states.
- Same-operation service tests keep two sample requests unresolved, confirm one, then
  reject the older request. Both callers retain the same confirmed receipt. A stale
  prepared caller rereads the canonical confirmed operation without another API request.
  These tests use the actual service with injected recovery matching the adapter's terminal
  semantics; they do not emulate browser IndexedDB transaction scheduling.
- The IndexedDB event driver executes the real adapter's byte preservation and terminal
  patch logic. Its confirmed-then-late-retry test does not simulate browser cross-tab
  transaction scheduling. No new guarantee is inferred from that sequential test.

The historical browser artifact reports two explicit raw-drawing saves and three total
samples after the image save. It does not prove that separate user save actions collapse
into one sample. The acceptance wording has been corrected to match those counts.

## Follow-up browser evidence

Pending: fresh follow-up browser checks for upload/retry after reload, copied-image
reselection, keyboard drawing, account changes, ordinary/OS gentle motion and affected
responsive/short widths on the current wizard. A later user-directed wizard revision
changes the UI under test; the earlier 84-test continuation run is not visual acceptance
of that revision.

The fresh browser attempt could not reach the page. CUA's browser inventory returned
**Local browser use is disabled in Work settings**; selecting the running native Firefox
returned **Computer Use permissions are not granted**. No screenshot or interaction was
obtained. No browser setting, permission or security control was changed, and no other
browser-control technology was used. This is a tooling/permission gate, not a passing
browser check or evidence of a product failure. Resume these checks when the supported
browser or native-app control is available.

The existing Phase 02 browser artifacts separately cover two tabs, stale editor → list
refresh → 412 → explicit rebase, original create body/key restoration after reload,
independent unresolved backups and an unchanged populated local draft. They do not stand
in for Phase 03 IndexedDB recovery or account-switch browser acceptance.

## Remaining boundaries

Provider provisioning/public deployment, hosted Firebase/App Check, shared rate limiting,
dependency/provider review, Docker/hosted CI, Linux/container decoder enforcement and
Phase 07 deletion remain gates. Extraction, training and font generation are outside
Phase 03. Decoder/resource measurements do not establish scientific handwriting quality.
