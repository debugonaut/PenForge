# Freeform English analysis — sequential implementation

2026-10-24. Continue the owner's [natural-writing direction](../design-language.md#freeform-handwriting-to-font-direction--2026-10-24).
The existing six-step wizard remains the destination. Model execution is owner-run.
This plan does not qualify a recognizer, aligner, generator or cursive support.

2026-10-29: the separately approved [assisted photo route](../photo-font/evidence.md)
uses user-selected regions and labels in analysis v3. It does not supply automatic
recognition/alignment or change freeform evidence v1. Natural-writing goals remain open.

## Actual path and gap

| Stage | Current implementation | Required change |
| --- | --- | --- |
| Capture | `AccountStudio` saves an immutable observed StrokeDocument and assigns one character. Uploads also retain original bytes. | Represent natural writing explicitly; keep correction captures available. Never reinterpret existing samples. |
| Admission | `AnalysisCreate` emits v1 or explicit fixed-round v2. `AnalysisService.sources` requires an assignment for each drawing. | A separately reviewed payload version and capability admission for natural writing. User text is alignment context only. |
| Dispatch | Both `AnalysisService.claim` and `PipelineWorker` support analysis versions 1/2. | Deploy compatible claiming, isolation and checkpoint handling before emitting a new payload. |
| Analysis | `_analysis_child` converts an entire assigned drawing or authoritative template cells. Ordinary photos return `isolated_samples_required`. `freeform.experiment` has synthetic connected regions without identities. | Recognition hypotheses plus independently traceable character alignment; neither OCR text nor connected components alone supplies it. |
| Publication | `AnalysisService.publish` validates source/child hashes, attempt, pipeline, template cell or drawing assignment. | Validate bounded alignment against the exact immutable source before admitting pending authentic candidates. No direct database inserts or disguised templates. |
| Review/edit | `GlyphsService.select` creates pending revisions. Edit/rollback create revisions; explicit approval changes active references. `CloudReviewPanel` already presents these actions. | Reuse this path, adding source/uncertainty presentation only when genuine candidates exist. Preserve repeated alternatives. |
| Font | Approved snapshots feed the real isolated compiler, validators, verified download client and Writing Desk. | Use actual bytes in a text specimen; measure baseline, proportions, bearings, space and spacing after alignment exists. |

The saved holdout (`../../research/ENGLISH_VOLUNTEER_HOLDOUT.md`; local record) scored isolated
52-class letter crops. It supplies neither sentence alignment nor writer-fidelity
evidence. The private page checkpoint (`../../research/PAGE_TO_FONT_CHECKPOINT.md`; local record)
used manual regions/masks. Its later completed fonts remain frozen. No suitable
automatic character aligner is established by these results.

## Design choice and reconciliation

Three approaches were considered: reinterpret v2 assignments as words; admit manually
boxed pages as templates; or introduce independent freeform evidence before runtime
coupling. Choose the third. The first breaks v2 semantics, and the second loses honest
source provenance. A new evidence schema alone must not enable a capability or route.

| Source | Resolution in this package |
| --- | --- |
| Owner's latest direction; v3 Part A CAL-01/ML-31 | Words/sentences remain the goal. Retain hypotheses and unresolved connected ink rather than forcing isolated recapture as the final experience. |
| v3 IC-02/05 and Phase 04 | Bind to exact source bytes, document hash, dimensions and private sample/asset/project references. Evidence is pending and content-hashed. This binding is not ownership authorization. |
| v3 Part A §13; Phase 08 gates | Null scores unless measured references exist; recognizer and aligner identities are separate and pinned. No qualification from schema validity. |
| Current v1/v2 contracts | Add independent evidence v1; retain payloads, APIs, manifests and converters unchanged. A future job version requires a separate compatibility review. |
| Owner-run execution restriction | A replaceable protocol has no installed model implementation. Tests supply inert authored responses only. No real inference, fitting, evaluation or data intake. |

## Package 1 — evidence contract and source binding

Implement strict request/result schemas for one original observed Latin canvas capture.
The coordinate frame is the original capture pixels, top-left/downward, with an
identity transform. Record supplied transcription separately from recognition.
Line and word regions, competing whole-capture hypotheses, scalar text ranges and
inclusive source point spans describe proposed characters or unresolved ink.
Character identity refers to a recognizer hypothesis, never directly to supplied text.

Validate unique IDs, hierarchy/containment, scalar offsets, method provenance,
canonical result hash and bounded budgets. Bind against caller-authorized source
metadata and exact bytes; verify dimensions/document hash, actual stroke IDs/ranges,
point containment and complete, non-overlapping accounting of captured points.
Delayed dots/crossbars can belong to a region despite being drawn later. Repeated
letters remain distinct regions. Partial-stroke splits and transcription mismatch
retain mandatory review reasons; no character, outline or font is activated.

The adapter protocol accepts immutable request/source bytes and returns bounded
result bytes. No adapter means unavailable; there is no default fake recognizer.
The binding result is immutable bytes plus review references, not an MLResultManifest
accepted by the existing publication service. Photo regions/masks, non-identity
transforms, edited/legacy sources and typography are outside this first version's
scope and must not be smuggled through it.

Verify with inert fixtures for multiple letters, repeated letters, delayed marks,
unresolved joins, competing text, source substitution, point reuse/omission, bounds,
method/result mutation and budgets. Generate schemas/OpenAPI/TypeScript through the
existing exporters, retain compatibility baseline and legacy geometry regressions.
No UI changes or new HTTP operations are part of this package.

## Subsequent packages, in order

1. Extend durable admission/dispatch/publication with an explicit freeform payload,
   qualified-method policy and isolated adapter. Exercise both claim paths and the
   real pending review/edit/approval/export path using inert authored geometry. Test
   IDOR, cancellation, retries, checkpoint dependencies, source/method substitution,
   stale attempts, edits and protection of approved snapshots/exports. The inert
   adapter belongs in tests and cannot be a normal creation fallback.
2. Prepare a bounded print word/short sentence experiment after identifying an
   admissible alignment method and separately authorized inputs. Freeze inputs,
   annotations, method/license identities and an owner-only execution claim; only
   then supply a new manual command. Saved recognition scores are insufficient to
   choose an aligner. Do not repeat earlier runs or admit extra datasets implicitly.
3. Connect genuine pending letters to the existing wizard and actual font-byte
   specimen. Preserve source shapes, common line scale/baseline, case, delayed marks,
   descenders and authentic repeats; assess bearings/space/spacing and clipped ink.
   Missing-letter generation remains separate, labeled and explicitly approved.
4. Extend beyond the measured print class only with independent alignment, fidelity
   and joining evidence. Cursive and photo masks need their own bounded contracts
   and measurements; no arbitrary-English or exact-match promise follows a print proof.

Record actual implementation/checks in [evidence](evidence.md). Preserve every earlier
browser, hosted, account, legal and model gate in current status (`../../PROJECT_STATUS.md`; local record).
