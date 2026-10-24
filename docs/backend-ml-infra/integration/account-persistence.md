# Account-backed product integration — evidence (2026-10-21)

Scope: the ordinary Penforge journey now creates, saves and resumes real work in the
person's own account, using the existing identity, capture, analysis, glyph, snapshot
and export services. This record covers what is durable, what was verified locally and
what remains unproven. It does not claim a hosted or production deployment.

## Later admission-policy amendment — 2026-10-24

Creation and account writes now require a registered, email-verified account and the
current service/rights agreement. The earlier guest creation evidence below is historical;
existing guest recovery remains. Default create mode is account; demo is explicit.
This amendment does not close earlier acceptance gates. See the
[service-accountability evidence](../../accountability/evidence.md).

## What now saves to an account

Two additive tables (`0013_account_workspace`) hold mutable working state; approved
geometry, snapshots and font files stay in their existing immutable tables and are only
referenced here.

| Record | Table | Notes |
| --- | --- | --- |
| Project workspace draft | `project_workspaces` | Owner-scoped, `row_version`, 1 MiB bound; strict schema, `extra="forbid"` |
| Writing Desk page | `writing_documents` | Owner-scoped, `row_version`, 256 KiB bound; title/format/document JSON |

The workspace covers script and step, capture draft (strokes plus the submitted stroke
document with its observed timing), assignments and approvals, style, preview text,
capture/asset references, export references and selection, and snapshot references.
Capture bytes stay in the private storage abstraction and are referenced by validated
asset IDs. `DraftGlyph` is a preview mirror only: it can never approve, activate or
replace canonical revisions.

Endpoints are owner-scoped and revision-fenced (`If-Match: "v<n>"`, `428` when absent,
`412` on a stale base):

- `GET|PUT /api/v1/projects/{id}/workspace`
- `GET|POST /api/v1/documents`, `GET|PATCH|DELETE /api/v1/documents/{id}`

Deletion inventory extends to both tables, so project deletion and account deletion
remove drafts and pages with their assets.

## What resumes, and where

- **Account:** refresh, sign-out/sign-in, and a different browser or device recover the
  project list, each project's step/draft, the approved letters, the export references
  and the Writing Desk pages.
- **Device-only:** an anonymous guest keeps its guest identity and work on that browser.
  Local demo drafts stay in browser storage and are never uploaded by signing in.
- Signing in does not import local demo handwriting; the existing explicit import path is
  unchanged.

Session handling changed to match the product direction: Firebase now uses
`browserLocalPersistence` (previously session-only), and a configured build quietly
restores the existing session on load, so a refresh reopens the signed-in desk instead
of showing a sign-in gate. A failed attempt settles as signed-out; it never leaves a
stuck "opening" state. The account screen keeps its explicit resume button.

## Saving and recovery behaviour

- Debounced saves; only a server acknowledgement produces “Saved to your account”.
- Every pending write is stored before sending, keyed by owner/project/operation, with
  its exact body and base revision.
- Kept changes replay themselves when the project is opened, and retire only after the
  server confirms. Confirmed writes retire only matching records, so another tab's
  backup survives.
- A stale base fetches the remote version: non-overlapping edits rebase automatically;
  overlapping edits ask the person which version to keep and keep both backups.
- Failure states are distinct: saving, saved, offline (changes kept on this device),
  couldn’t save — retry, session expired, project deleted elsewhere.
- Responses and subscriptions are fenced on the captured identity, so an account switch
  cannot surface the previous account's data.

## Defects found and fixed during live verification

1. **A real capture could never be saved.** `capture_started_at_ms` is an observed
   sub-millisecond double, but the wire field is a strict integer, so the first save after
   drawing a letter returned `400`. The wire mapping now rounds at the boundary
   (clamped, non-finite values omitted) without inventing a different time.
2. **A ready export could never be saved.** Draft export references carried the canonical
   `sha256:<hex>` value, while the draft schema stores bare lowercase hex. The wire
   mapping now normalises the digest and omits an invalid one.
3. **Exports were blocked locally by unprovisioned allowances.** The dev database had
   `quota_tiers.v1` with only storage and compute, so `compile_export` raised
   `quota_unconfigured`. A reviewed `quota_tiers.v2` adds the `exports` and
   `generation_candidates` resources, and `queue_policy.v1` was advanced to version 2 —
   the documented operator provisioning path, not a code default.
4. **No session restore on load**, and kept changes were hidden after a refresh
   (the save chip said “Saved” while an unsent operation existed). Both are fixed:
   auto-resume in `useAccountSession`, and pending writes replay on open with a
   “Saving…” chip until the server confirms.
5. **Approvals did not mirror into the resumable draft.** The draft glyph status now uses
   the authoritative approved-codepoint coverage, so a resumed project shows approved
   letters instead of “needs review”.

## Verified locally (loopback stack only)

Environment: local PostgreSQL 15+ on a private socket, FastAPI on `127.0.0.1:8011`,
Firebase Auth emulator, Next dev server, and the local worker run on demand
(`WORKER_MODE=scheduled` processes the queue for `WORKER_MAX_RUN_SECONDS`).

| Check | Result |
| --- | --- |
| `npm test` | 129 passed (11 account-persistence regressions, incl. the two wire mappings) |
| `npm run typecheck`, `npm run lint`, `npm run build` | Passed |
| `make test-api` | 467 passed |
| `make test-integration` (real PostgreSQL) | 219 passed, incl. migration `0013` up/down and the workspace/document suites |
| `make lint` (ruff, format, mypy, boundaries, docs links) | Passed |
| `make contracts-check` | Passed |

Live scenario in the running app:

- Created account A, created a real project, drew a letter on the canvas with keyboard
  input, saved it, and the analysis worker returned a canonical glyph result.
- Reviewed the extracted `A`, selected the candidate, approved it explicitly; coverage
  moved to `1 / 52` Latin core and `user_approved: [65]`.
- Compiled TTF, OTF and WOFF2 (`1 mapped character · 94 missing ASCII · partial font`,
  validation passed with warnings), then persisted the three export references and the
  selection in the draft.
- Opened the Writing Desk through “Write with it”, loaded the generated font by export ID
  (“Generated Penforge font loaded… PNG and SVG embed the verified font”), saved the note
  to the account, and exported the page without errors. The stored page keeps
  `generated_export_id`.
- Refreshed mid-journey repeatedly: the session resumed and the project reopened at the
  saved step.
- A clean incognito context signed in as A and listed “1 saved project · Untitled font ·
  Export” — cross-context recovery from the server, not from local storage.
- Account B: A's workspace and document both returned `404`; no A project appeared.
- Stale-base write: first `PUT` at `v16` → `200`; the same base again → `412`
  `revision_conflict` (the branch the UI turns into a keep-mine/keep-saved choice).
- Local demo: `/create?mode=demo` still opens `LOCAL DEMO` with “My first font”.
- Layout probes with no horizontal overflow and the project listed at 1440×900,
  768×1000, 390×844, 320×844 and 1280×600; the primary action stays in view on the
  short viewport.

## Not verified here (explicit limits)

- **Offline mode** was not exercised live (no network emulation in this session). The
  queue/replay path is covered by unit regressions and a live replay of a kept change.
- **A live two-tab UI conflict** was not driven; the server fence (412) and the client
  merge/choice logic were verified separately (live `412` above, unit tests for the UI
  branches).
- **Screenshots at the four widths were blocked**: the preview webview produced no
  composited frames in this session. Only DOM-level measurements were taken, so no visual
  sign-off is claimed here.
- The `/account` screen's deliberate “Resume this tab's session” button was not exercised
  successfully in the incognito partition used for the isolation check; auto-resume was
  verified on the studio routes instead.
- Loopback stack, emulator auth, engineering fixtures and a keyboard-drawn stroke. This is
  not handwriting-quality evidence, not a hosted deployment, and not a qualified model.
- Firebase local persistence falls back to `localStorage` when IndexedDB is unavailable
  (observed here); that fallback is browser-local and does not change account persistence.

## Running it locally

```sh
make setup                 # uv sync + npm ci
make db-up                 # or scripts/local_postgres.py for a native local cluster
make migrate               # alembic upgrade head (0013_account_workspace)
make api                   # 127.0.0.1:8000 (or --port 8011 as used here)
make worker                # processes queued analysis/export work, then exits
npm run dev                # Next app
```

Identity needs `NEXT_PUBLIC_PENFORGE_AUTH_BACKEND=firebase`, a demo project and the auth
emulator URL; the API needs the matching Firebase emulator host, storage root and HMAC
keys. Exports additionally need provisioned allowances:

```sh
.venv/bin/python scripts/configure_quotas.py <reviewed-quota.json> --apply
# then advance queue_policy.v1 to that quota version through the documented operator step
```

`.local/` holds the machine-specific env file, quota input and logs used for this run; it
is not part of the repository.
