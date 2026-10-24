# Phase 08 — actual local evidence

Date: 2026-10-20. Scope: explicitly authorized Phase 08 only, sequentially without
subtask agents. Stop before Phase 09. No external handwriting download, license/portal
acceptance, training/fine-tuning, contribution training use, model promotion, GPU/paid
compute, Devanagari production, provider provisioning or deployment was performed.
The workspace remains untracked local work; no commit, remote, PR, hosted CI or backup
is claimed. The preserved source specification is unchanged.

## Capability states

State is keyed by task, script, writing style and input class, not a product-wide badge.
[ADR 0018](../../decisions/0018-phase-08-capability-evidence.md) requires independent
evidence for recognition, segmentation, outline/writer fidelity, shaping, blinded human
preference, correction burden and resources. Loading, OCR improvement and synthetic
engineering tests cannot qualify a production handwriting capability.

| Capability | Current state | Evidence and limits |
| --- | --- | --- |
| Deterministic approved-snapshot style measurement | experimental_opt_in | Real canonical geometry and observed capture intervals; synthetic engineering verification. No learned embedding or fidelity claim. |
| Authentic alternates / contextual font cycling | experimental_opt_in | Explicit source selection, separate owner approval, validated TTF/OTF/WOFF2, real FontFace preview. No quality claim about a writer. |
| Recognition; natural/freeform alignment and cursive splitting | research_only | Synthetic-only opt-in connected-region experiment. No OCR/model or reliable character segmentation; unavailable in ordinary creation. |
| Learned style embedding; glyph generation | disabled | Typed future boundaries only, no runtime/model/proposal preview. |
| New production handwriting capabilities | none production_validated | No approved handwriting dataset, held-out evaluation or independent human approval. |

The existing structured Latin / isolated drawing T0 remains the default input route
within its prior experimental extraction boundary. This phase does not upgrade its
handwriting-quality label. Capability responses distinguish print/cursive/mixed and
input class. The default passage remains unoptimized.

## Implemented behavior

- [ADR 0019](../../decisions/0019-phase-08-style-profile-identity.md): existing
  style_profiles stores immutable versions with snapshot/revision hashes, full algorithm
  configuration and canonical profile hash. Repeated identical snapshots reuse identity.
  Measurements include sampled bounds, advances/bearings, cap/x/ascender/descender
  extents, centroid lean and horizontal ink-chord distributions. Ink chords are geometric
  proxies, not physical nib widths. Interglyph spacing and line baseline variation are
  null for isolated captures. Timing/gaps/pressure are included only when observed in raw
  StrokeDocument sources; isolated intervals do not establish sentence rhythm.
- Measurement holds owner/project transaction locks and has bounded geometry/source
  budgets. It excludes unapproved/foreign/synthetic ancestry. No worker profile-writing
  path exists. Historical profiles, snapshots and exports remain immutable.
- Phase 07 inventory covers profiles, optional embedding assets, source/alternate
  lineage, alignment artifacts and future synthetic proposals. Retention scans profile
  references. Project/account erasure removes applicable artifacts and fences stale
  operations; training withdrawal preserves personal fonts while blocking future
  contribution inclusion. The external content-free erasure journal remains authoritative.
- [ADR 0020](../../decisions/0020-phase-08-registry-safety.md): read-only model_versions
  mapping, live approval/revocation, exact integrity/schema/task/script/style/input/worker
  and provenance checks, bounded lazy cache, hit integrity checks and explicit eviction.
  Controlled ObjectStorage model keys only. JSON enum maps are constrained; safetensors
  validates its bounded header/tensor layout. ONNX is opaque verified bytes with a
  canonical-container prefix check, not graph/operator validation or inference. No
  inference framework is installed. Registry content/history is immutable; revocation
  cannot be undone by mutating an existing row. App/worker grants cannot approve models.
- Evaluation reporters extend BenchmarkRecord, retain writer/duplicate/ancestor
  admission, partition script/style/input/condition and separate synthetic from real
  held-out evidence. CER/top-k/ECE, alignment/split/merge, mask IoU/exact boundary F1,
  paired outline/raster error, same-writer held-out fidelity, blinded preference,
  correction burden, first-useful-font time and resource dimensions are independent.
  Human metrics remain unavailable without actual blinded review. The CLI reports
  reviewed local observations; it neither runs arbitrary recorded commands nor obtains
  datasets. No eligible handwriting run was performed.
- [ADR 0021](../../decisions/0021-phase-08-accuracy-first-planning.md): choose quality
  qualification before capacity. No qualified component yields unsupported/intervention;
  a resource/quota/degradation shortage queues the best qualified choice. It never picks
  a weaker method. ETA remains null without an exact component/key/hardware/worker
  measured profile. Existing Phase 06 queue/quota fences remain authoritative.
- Follow-ups prioritize actual uncertainty/missing identity, absent distinct samples and
  context. Expected requested codepoints remain separate from approved observed identity.
  The policy/reasons/evidence hash are versioned; prompt_is_ground_truth stays false.
  Explicitly selected next-capture context is frozen with the existing retry operation.
- Authentic alternatives reuse existing glyph/revision/snapshot storage. They preserve
  approved source revision plus frozen source snapshot, require distinct source bytes and
  explicit approval, never add base coverage, and support ETag-safe pause/re-enable.
  Approved primary selection survives. No random geometry is introduced.
- [ADR 0022](../../decisions/0022-phase-08-contextual-cycling.md): feaLib single chained
  contextual lookup cycles adjacent repeats left-to-right. N=2 and N=3 use source-hash /
  revision-hash order independent of insertion. Realism 0 uses base; 1–50 permits two
  forms; 51–100 permits three. Missing sets preserve base glyphs. Export publication
  requires invariants plus the existing five validation layers. Apps may disable calt.
- The synthetic freeform spike preserves connected regions without forced character
  boundaries. Prompted words are hypotheses; mismatches request isolated recapture.
  A synthetic proposal contract stays pending/disabled and cannot self-activate.

## Automated verification

Real PostgreSQL 16.2 / psycopg was used, never SQLite. Migration head is
0011_personalization; there are still 27 application tables.

| Check | Actual result / retained artifact |
| --- | --- |
| Complete backend, excluding emulator | 559 passed; 5 emulator scenarios deselected — log (`../../../artifacts/phase08/backend-final.txt`; local record) |
| Real Firebase Auth emulator | 5 passed separately — log (`../../../artifacts/phase08/auth-emulator-final.txt`; local record) |
| Complete frontend | 114 passed — log (`../../../artifacts/phase08/frontend-final.txt`; local record) |
| Added independent-process shaping verification | 7 passed; subset of backend — log (`../../../artifacts/phase08/calt-independent-final.txt`; local record) |
| Large style snapshot receipt / prior glyph regressions | 13 passed; subset of backend — log (`../../../artifacts/phase08/style-recovery-final.txt`; local record) |
| Registry network-denied child | 26 passed, 1 wrapper deselected; subset — OS-isolated log (`../../../artifacts/phase08/registry-network-denied.txt`; local record) |
| Migration up/down/up and ORM drift | Passed in full backend database suite |
| Previous-release seeded migration | 0010 privacy request/journal unchanged; 0011 upgrade and erasure resume passed — receipt (`../../../artifacts/phase08/previous-release-migration.json`; local record) |
| Contracts / retained compatibility / research audit | Passed; 0 approved datasets, 0 measured ML benchmarks, 0 completed experiments |
| Ruff / format / mypy / boundaries / documentation | Passed — quality log (`../../../artifacts/phase08/quality-final.txt`; local record) |
| TypeScript / ESLint / production build and startup | Passed — static log (`../../../artifacts/phase08/frontend-static-final.txt`; local record), build log (`../../../artifacts/phase08/frontend-build-final.txt`; local record) and startup (`../../../artifacts/phase08/production-startup.json`; local record) |

The final count includes previous-phase regressions. Subsets are not additional unique
tests. Starlette emits its existing httpx deprecation warning. Two earlier full attempts
are retained: a missing new-route test allowlist and an older random-request-ID assertion
were corrected. Browser repetition found the full-profile snapshot receipt exceeding
32 KiB; compact immutable receipt rehydration fixed it and retains legacy receipts.
No source-history or export byte mutation was used to fix failures.

## Exact synthetic engineering evidence

No new dataset or handwriting model artifact exists. IAM and DHCD remain unapproved
candidates. Existing Caveat remains a local demo typeface, never a new private generated
result. Authored analytic outlines / StrokeDocuments and tiny enum maps are test controls.
No research benchmark/experiment record was marked measured or completed.

Resource summary (`../../../artifacts/phase08/measurements/summary.json`; local record) records Apple M4,
16 GiB RAM, macOS 15.7.4 arm64, Python 3.12.15 and the dependency-lock digest. Three fresh
style processes returned identical canonical hash
625494ac6cf5f056871eb098af5af6523d0f3679bb0146ef5aabec56e7e7f560.
Observed descriptor time was 1.224–1.285 ms and whole-process peak RSS 56.7–57.4 MB for
six analytic glyphs. These are three observations, not production latency percentiles.

Six real isolated font children (N=2/3 × TTF/OTF/WOFF2) had network-denied Seatbelt
execution and passed technical validation with the intentional calt interoperability
warning. Compile time was 19.2–34.9 ms; validation 225.3–356.1 ms; child wall
249.1–382.8 ms; child peak RSS 75.7–76.4 MB. Exact per-run hashes and resources are in
the summary and its per-format reports. Hardware/provider extrapolation, hosted capacity,
cost and ETA remain null. Canonical style bytes are retained beside that summary.

Font fixtures (`../../../artifacts/phase08/fonts`; local record) retain all six binaries, SHA-256,
exact HarfBuzz sequences and independent-process hash/sequence comparison. Cases include
eeeeeeee, hello wellness, separate runs, spaces/digits/punctuation, every enabled repeat,
features-off base fallback, insufficient alternates and no added .notdef. All five
validators (structure, OTS, curated FontBakery, HarfBuzz, FreeType render) run. These checks
establish font engineering, not handwriting accuracy, writer fidelity or human preference.

Freeform control (`../../../artifacts/phase08/freeform-synthetic-control.json`; local record) retains
the mismatch, null identity, preserved connected region and isolated fallback.

## Browser verification and limitations

The current private workflow ran against an isolated PostgreSQL schema,
local ObjectStorage, Firebase Auth emulator, Next dev server and existing PipelineWorker.
The fixture is visibly named “Phase 08 synthetic geometry”; its 95 analytic ASCII base
mappings are fixture approvals, not human acceptance of handwriting quality.

Verified via actual UI: opt-in storage connection; persisted style measurement/re-entry;
two distinct earlier e samples kept as pending alternates and separately approved;
pause/re-enable; unchanged base coverage; all three real exports ready; downloaded TTF
FontFace loaded; calt on/off by pointer and Enter; Tab to preview; width disclosure by
keyboard; explicit follow-up selection/clear; research-only/unavailable explanations.
The large-profile retry fix was rechecked successfully, including identity-preserving
replay. A canceled request also exposed one local psycopg pool-pre-ping ACTIVE transaction
error; retry/re-entry recovered. No private data was logged in evidence.

DOM layout checks at 1440, 768, 390, 320 CSS px and 1280×600 reported no horizontal
overflow. Existing browser zoom was 90%; viewport overrides were compensated and actual
innerWidth verified, then reset. Narrow controls measured approximately 44 px or larger.
This verifies pointer-sized targets, not a physical touchscreen/device. Native touch
hardware testing is unexecuted. Screenshots are retained in
browser artifacts (`../../../artifacts/phase08/browser`; local record); viewport override captures showed
partial/stale painting and are not full visual acceptance. Default-viewport rendering of
the measured profile and real font was visually inspected. Responsive DOM/interaction
checks passed; complete visual acceptance at every required viewport remains unexecuted
because the capture surface did not reliably paint those overrides. No component test or
build is substituted for that limit. Ordinary motion was active (OS reduce-motion false).
OS-gentle is unexecuted; OS preference access was not used.

Phase 03/04 current-wizard browser, Phase 05 OS-gentle, all Phase 06 browser, and Phase 07
OS-gentle/final account-deletion browser checks remain pending exactly as before.

## Remaining gates

Real-handwriting recognition/segmentation quality, learned writer fidelity, blinded human
preference, optimized passage comparison, trained generation, quantitative promotion
thresholds and production model/runtime remain unmeasured or blocked on separately
approved data and independent review. No confidence intervals are invented for absent
runs. Provider capacity, backup expiry, hosted identity, deployment and public readiness
remain unresolved. See [development/rollback](development.md). Phase 09 is separate work.

Machine-readable closeout (`../../../artifacts/phase08/summary.json`; local record) and
source hashes (`../../../artifacts/phase08/source-integrity.json`; local record) identify this local
implementation without inventing a Git commit. The final follow-up clear was rechecked
after the notice fix: requested context clears, no stale attachment notice remains, and
the immutable profile persists. A fresh reload at mobile width reproduced the capture
painting limit; it is retained as a limitation, not upgraded to visual acceptance.
