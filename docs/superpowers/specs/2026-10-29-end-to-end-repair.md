# End-to-end handwriting repair — 2026-10-29

Status: explicitly approved by the owner on 2026-10-29, with a requirement for fast
extraction, analysis and conversion. Batch regions, decode once, retain checkpoint
and recovery reuse, measure processing and report queue wait separately. No unmeasured
hosted deadline is implied.

## Outcome

Keep the accepted six-step account studio and use actual source-bound outlines and
validated font bytes throughout capture, review, approval, preview, export and Writing
Desk. Existing source images, recorded strokes, approved snapshots and private fonts
remain immutable. Existing local demo behavior remains explicitly simulated.

## Existing-flow repairs

1. Recognize confirmed account capture references when navigating from capture to
   review. Later steps still require actual retrieved glyphs.
2. Automatically load initial review data with owner/project/client lifecycle fences.
3. Commit a canvas stroke visually only after its observed document accepts the same
   stroke; reject invalid timing/budget events without submitting an older document.
4. Load approved outline details for the specimen and match exact ordered codepoint
   sequences. Pending or missing outlines cannot appear as approved typography.
5. A new ordinary export freezes current approvals instead of reusing a prior build's
   snapshot. Explicit historical snapshot requests and exact retry recovery survive.

These are repairs to the accepted architecture, without a replacement UI or model.

## Ordinary-image extension: proposed next slice

Ordinary photos are currently saved but cannot yield usable pending glyphs. Three
possible paths are: source-bound human selection, automatic page recognition and
alignment, or requiring printed templates. Recommend source-bound selection first:
it reuses existing photos and does not depend on an unqualified recognizer. Automatic
word/sentence extraction remains a subsequent measured capability, and template
uploads continue to use their pinned cell manifests.

The person selects each letter's rectangle on their existing image and confirms its
character, common scale and baseline. The tool extracts that image's ink, presents
the pending outline for correction, and requires approval before font compilation.
Repeated letters can remain alternatives. No identity or unseen letter is invented.

Use a distinct analysis v3 `manual_regions_v1` payload, not disguised templates or
changed meanings for earlier drawing contracts. A bounded region records its unique
ID, owner-authorized sample, exact source digest/dimensions, integer crop, assigned
supported character, baseline and shared units per pixel. API admission validates
source readiness, bounds and budgets. The isolated worker crops immutable decoded
pixels and produces mask, crop and outline evidence. Both claiming paths support v3;
publication checks attempt, source, region, policy and artifact identity before
admitting pending authentic candidates. Existing review, revisions, explicit approval,
snapshot compilation and verified download remain authoritative.

The preview uses the same oriented pixel frame as extraction. Crop and mask previews
must show what survives thresholding. Ambiguous connected ink and guide-line overlap
remain visible for correction; no claimed OCR or handwriting accuracy follows from
manual labeling. Font geometry preserves the recorded common baseline and scale.

## Verification and limits

Run focused regressions for each repaired state transition and actual synthetic
database/worker scenarios for drawing, template images, approvals and all three font
formats. Then use an isolated local emulator/API/frontend stack to verify the UI
journey and actual font-byte rendering/download. Use existing pinned Node 24 and
Python environments. No new model inference/training, private-font rebuild, cloud
provisioning, public publication, merge or deployment is included.

For the image extension, additionally test malformed/out-of-bounds regions, source
substitution, owner isolation, exact image orientation, repeated characters, dots,
holes, descenders, pending-only publication, cancellation/retries and immutable prior
snapshots. Authored synthetic geometry is engineering evidence, not a measured score
for arbitrary volunteer handwriting.

Hosting remains a separate integration after the local journeys work: provider choice,
storage conformance, shared admission limits, hosted identity and recovery checks.
Do not remove existing production refusals merely to make deployment start.

## Implementation order

1. Complete and verify the existing-flow repairs above in parallel.
2. Implement the image extension after the owner confirms the proposed assisted
   upload workflow; freeze contracts and compatibility before connecting the UI.
3. Exercise both real local journeys through font bytes and Writing Desk exports.
4. Record actual results and the remaining accuracy/hosting limitations in the handoff.

The original workspace HEAD and index are preserved. This local design is not a
publication or deployment approval.
