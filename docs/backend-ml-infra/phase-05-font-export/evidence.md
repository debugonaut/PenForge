# Phase 05 — local implementation and acceptance

**Private handwriting follow-up, 2026-10-24:** the existing compiler and five-layer
validators have now also produced a real manually reviewed W03 draft in TTF, CFF OTF
and WOFF2, covering 93/95 ASCII. Repeated bytes match; native CoreText renders all
93 mappings without missing glyphs or fallback, and real Writing Desk imports/exports
were checked. The private build evidence (`../../../research/PAGE_TO_FONT_BUILD.md`; local record)
distinguishes this from the authored fixtures below and records partial coverage,
manual typography and provenance limits. Thirty existing font regressions pass.
This detached local file-import proof does not implement account photo ingestion,
qualify a model or close earlier responsive, OS-gentle, hosted or production gates.

Date: 2026-10-20. Phase 05 alone was explicitly authorized. Work packages were completed
sequentially without subtask agents. Phases 00–04 remain intact; the previously deferred
Phase 03/04 current-wizard browser checks remain pending. Phase 06 has not started.

## Implemented behavior

- [ADR 0011](../../decisions/0011-phase-05-reproducible-builds.md) freezes canonical build
  identity before compilation. Snapshot hash/time, sanitized metadata, metrics,
  conversion/embedding policy and pinned toolchain determine bytes. Request UUIDs,
  current time and owner identity do not. Distinct requests and retries reproduce bytes.
- The independent engine produces real quadratic `glyf`/`loca` TTF with `gasp`, CFF OTF
  with T2CharStringPen and FontTools/Brotli WOFF2. Even-odd normalization resolves holes
  and technology-specific winding. Geometry is bounded; degenerate/open/invalid contours
  fail. No glyph rescaling, generated alternates, joins, calt, ligatures or kerning is added.
  The declared 1.5-unit conversion budget reserves 0.71 units for TrueType rounding.
- Approved advances/bearings, descenders, baseline and proportions survive compilation.
  Approved space is metrics-only. If space was not approved, its cmap entry is absent
  and coverage warns; no synthetic advance is invented. Visible .notdef is never counted.
  Format 4/12 cmap and supplementary scalars are exercised. Devanagari is unavailable.
- The export API atomically authorizes, pins/freezes a snapshot, reserves owner AND
  global budgets, inserts format/output-ledger rows and enqueues one durable job.
  Identity-scoped idempotency replays the same receipt. Hash-verified canonical revision
  bytes feed isolated network-denied children. Live lease/attempt and commit-time guards
  fence staging/publication. Crashes/retries clean pending objects and settle capacity.
  Final fonts/reports/rows are immutable; rebuilds create new rows.
- Owner-only metadata/download routes use short-lived capabilities. Keys contain opaque
  asset UUIDs, never owner IDs, writing, private names or signed URLs. The browser fetch
  checks origin/path, MIME, size and SHA-256, renews an expired capability once, and loads
  verified bytes through FontFace. Private export UI shows actual stages/units, nullable
  ETA, coverage and validator reports. It remains separate from the local demo.
- Writing Desk distinguishes sample, imported and generated fonts. Selected generated
  bytes feed the existing SVG/PNG renderer; words, letter/note/card structure, paper,
  accent and ink remain independent. Font bytes are held in the tab, not local drafts.

## Executed automated checks

The complete relevant backend suite passed **447 tests**, with **4 existing Auth-emulator
scenarios deselected**, against real PostgreSQL and actual isolated workers/validators.
Those **4 emulator scenarios also passed in a separate fresh run**; they are disjoint
from the 447-test run. See emulator output (`../../../artifacts/phase05-acceptance/emulator-final.txt`; local record).
One existing Starlette/httpx deprecation warning remains. The frontend suite passed
**98 tests**, including all existing recovery/writing/motion regressions and new
download/embedding/operation-scoped recovery checks. These are whole-suite totals, not
sums of overlapping focused runs. See backend output (`../../../artifacts/phase05-acceptance/backend-tests.txt`; local record)
and frontend output (`../../../artifacts/phase05-acceptance/frontend-tests.txt`; local record).

Coverage includes cross-owner 404s, snapshot ownership, atomic budget failure, unapproved
exclusion, distinct-request and retry hashes, changed manifest identity, terminal guards,
concurrent finalization, skipped/forged validator results, expired leases at commit,
queued inactive/expired scopes, and a **real SIGKILL after staged object write** followed
by lease expiry/reclaim. Recovery leaves no orphan object or live reservation. Existing
claim, ownership, upload, extraction/review and Phase 02 recovery regressions pass.

The real PostgreSQL migration fixture executes upgrade → downgrade → upgrade, checks
head `0008_font_exports`, 24 application tables and zero SQLAlchemy metadata drift.
Migration 0008 is additive; downgrade refuses when frozen Phase 05 exports exist so
lineage cannot be silently lost. Production rollback stops new builds/rolls forward;
it does not delete old assets. Schema-cycle checks use disposable isolated schemas.

Contract generation/drift, retained-version compatibility, generated TypeScript and
research metadata validation pass. Ruff, formatting, mypy, API/engine import boundaries,
local links/source hashes, frontend TypeScript, ESLint, production build and startup
are recorded in the acceptance summary (`../../../artifacts/phase05-acceptance/summary.json`; local record).
Docker and hosted CI were **not run**. Production startup served all seven application
routes with HTTP 200; see route results (`../../../artifacts/phase05-acceptance/production-routes.json`; local record).

## Actual fonts and five validation layers

All three formats passed **structural/checksum/metrics, real OTS 9.2.0, the four-check
FontBakery profile, HarfBuzz and FreeType render smoke** for both partial and full ASCII
fixtures. Every layer is mandatory. Missing/skipped/failing validators block readiness.
See the exact [validation policy](validation-profile.md) and
[dependency/license decision](dependencies.md). This is a curated FontBakery profile,
not a claim of passing every FontBakery check.

The retained measurements (`../../../artifacts/phase05-fonts/measurements.json`; local record) identify
12 isolated runs: two repeats × two fixtures × three formats. Paired SHA-256 values
match exactly in every format. Per-run directories retain actual font bytes, frozen
manifest, complete reports including per-codepoint HarfBuzz output, OTS/FontBakery
evidence and raster hashes. The analytic fixtures use the same authored test shape
under assigned scalars; they are deliberately not a handwriting/alphabet quality test.
The retained fixtures add one supplementary scalar to their core 52-letter / 95-ASCII
inventory; unmapped ASCII counts remain 43 / 0 respectively. Exact 52/95 cases also
pass engine tests. Partial results are `passed_with_warnings`; full ASCII is `passed`.

| Core fixture | TTF SHA-256 | CFF OTF SHA-256 | WOFF2 SHA-256 |
| --- | --- | --- | --- |
| 52 letters + supplementary scalar | `4f35814f92efb88afbce63a402ea40171449f75ce700c954782c58d2ab7fdf77` | `580197baf102a8c5b33d4403b1ee27607c095fce6eed43b9be100da4271a205d` | `94960efa566d6a912bebc796044b9d230150e040dafd66097f4b8763b4b66c61` |
| 95 ASCII + supplementary scalar | `bdee4a23c078ddfbe3e481b043444145aee4570803016e05cb7bdc942616f209` | `5e9c55eadaa652fe26df95756e807ef99af3c697a579315c83106bec76646efc` | `a6f742a25e6456bc8f22fed4cf3ab2f0cc4000d575b57a8787fe5d53ab7f5621` |

Local measurements: Apple M4 arm64, 16 GiB RAM, macOS 15.7.4, Python 3.12.15. Observed
child compile+validation wall times were approximately 0.235–0.371 seconds; child peak
RSS approximately 71.6–74.5 MiB. Exact records distinguish child and observed parent RSS.
These small synthetic inputs establish neither cloud capacity/cost nor user-job ETA.
Configured 60-second/768-MiB caps are provisional safety bounds. A killed attempt has
unknown resource use, not an invented measurement. No training or accuracy measurement ran.

## Browser and desktop acceptance

The actual in-app Chromium browser exercised the local private workflow with a disposable
Firebase Auth emulator guest, PostgreSQL schema and synthetic approved snapshots. All
three format rows reached `ready`; the panel displayed 52 mapped / 43 missing ASCII and
visible partial/validation warnings. A real TTF download succeeded. A test-only local
factory first returned a capability after its one-second TTL had elapsed: the asset
request returned 404, the browser renewed authorization, and the subsequent fetch
succeeded. Content-free API output (`../../../artifacts/phase05-browser/api-final.log`; local record)
records the injected event and 404/200 recovery; production code has no test expiry hook.

Both export and Writing Desk were checked at actual CSS widths **1440, 768, 390, 320**
plus a **1280×600** short viewport. No page horizontal overflow was observed. Keyboard
Tab reached the generated-font link with visible focus. Exact measured dimensions and
captures are in export viewport evidence (`../../../artifacts/phase05-browser/responsive.json`; local record)
and Writing Desk evidence (`../../../artifacts/phase05-browser/writing-final-responsive.json`; local record).
The file `responsive-initial-unapplied.json` is an explicitly failed initial resize
attempt and is not acceptance evidence. Current `export-*.jpg` captures were replaced
with correctly measured sizes. OS gentle-mode browser verification was not executed:
native Computer Use permissions are unavailable and the browser exposes no motion
override. Existing motion-selection tests pass; this is not a fresh gentle-mode UI pass.

Writing Desk explicitly loaded **CFF OTF, WOFF2 and TTF** through the authenticated flow.
It displayed the generated label; the WOFF2/TTF preview SVG contained the corresponding
actual embedded format. Existing letter/note/card field preservation tests pass; the
earlier real letter SVG and final graph-paper/forest-ink note SVG/PNG are retained.
The final PNG is **1560×1360** and was downloaded to a distinct new filename, then
copied into artifacts. The final SVG parses, retains `AbcXYZ` and selected ink, and its
decoded font is byte-for-byte the downloaded TTF, SHA-256
`1c56a767e5f179b2032e369c757edd662abcac326d1e4978b25cd791553613c2`.
See byte proof (`../../../artifacts/phase05-browser/writing-final-byte-proof.json`; local record),
SVG (`../../../artifacts/phase05-browser/writing-final-note.svg`; local record),
PNG (`../../../artifacts/phase05-browser/writing-final-note.png`; local record) and
ready/focus capture (`../../../artifacts/phase05-browser/export-1440.jpg`; local record).
The synthetic shape repeated in these captures is expected, not a claimed personal font.

A downloaded TTF was registered **for one process only** with native macOS CoreText,
rendered six covered `AbcXYZ` glyphs without glyph zero, written to a PNG and unregistered.
This is an actual desktop font consumer through its native API. See
CoreText result (`../../../artifacts/phase05-desktop/final-coretext.json`; local record),
render (`../../../artifacts/phase05-desktop/final-downloaded-coretext.png`; local record) and
[source](../../../scripts/phase05_coretext.swift). No Font Book UI install or OS-wide
font registration is claimed; native UI automation permissions were unavailable.

## Remaining boundaries and next step

Real-handwriting extraction/fidelity, physical capture/printing quality, extended-script
shaping, hosted Firebase/App Check, shared rate limiting, providers, costs, deployment,
consent/retention/deletion and public readiness remain unresolved. The local demo's
extraction/style/project-font previews and demo export stay simulated and labelled.
No local writing is uploaded automatically. No model/training/personalization,
Devanagari implementation or provider work was performed. No commit, PR, hosted CI,
backup or deployment is claimed. A local Git directory is now present with untracked
workspace files; this does not establish a remote or saved revision.

Stop after this local Phase 05 implementation. Recommend **Phase 06 — Queue recovery
and quotas** only after separate user authorization. See [development](development.md)
for run/rollback instructions. Preserve all prior pending checks instead of relabelling
the synthetic Phase 05 exercise as Phase 03/04 live acceptance.
