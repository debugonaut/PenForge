# Assisted photos and real account-font repair

Implemented locally on 2026-10-29 under the owner's approved
[repair design](../superpowers/specs/2026-10-29-end-to-end-repair.md) and
[image-region decision](../decisions/0029-manual-image-regions.md).

## Implemented behavior

The account studio opens existing ordinary photos without re-uploading them.
Full-resolution originals are streamed within the admitted20MiB limit and checked
against recorded MIME, byte count and SHA-256. Pointer rectangles and numeric
controls share the original EXIF-oriented frame. The person labels each complete
English letter/number/punctuation mark, sets its baseline, and uses one capital
height and threshold for the page. Original crop and extracted mask stay visible.

White-alpha integer luma and strict thresholding match the isolated worker. No OCR,
guide removal, smoothing or character invention runs. Notebook guides can require
a lower threshold or a different rectangle. Empty/edge-cut ink keeps a correction
reason. Up to96 regions submit in one v3 job, with one decode per source.

Both claiming paths support v3; legacy workers leave it queued. Checkpoints and
fenced publication bind source, assignment, method, attempt and artifacts. Only
pending candidates enter existing review; repeats remain alternatives. Explicit
selection/approval precede immutable snapshots and real font export.

Owner/project/sample/digest/frame-scoped browser recovery stores selections, settings
and the exact request key before POST. It survives refresh, preserves unreadable
records and blocks new processing when recovery cannot be saved. Original image
bytes never enter localStorage. Partial/zero results retain failed boxes; later
batches process only corrected failures. Job-scoped outcomes prevent reanalysis
from changing an earlier batch's result.

## Existing-flow repairs

- Confirmed account captures permit initial Review navigation; Review autoloads
  sources/coverage and discards stale owner/project responses.
- Visible canvas strokes commit only after raw capture accepts them. Saving cannot
  substitute an older raw document for rejected visible input.
- The specimen fetches actual approved details with exact mappings. Its words are
  editable, its rendering is bounded, and missing shapes are labeled.
- New ordinary exports freeze current approvals; explicit historical exports and
  exact request retries retain their original behavior.
- Balanced side-spacing is an explicit pending edit: translate x coordinates and
  adjust advance width without scaling, clipping or moving the baseline. Approval
  remains required; original captures/revisions/snapshots remain immutable.
- The Writing Desk's generated-font handoff waits for identity/account restoration
  and verifies actual bytes. Explicit export links take precedence over local drafts;
  manual font choices fence automatic loading and clear stale generated references.

## Executed verification

Actual synthetic worker checks cover drawing, pinned templates, approval and all3
font formats. The new ordinary-photo suite exercises the real `PipelineWorker`,
source verification, repeated alternatives, approval, TTF/OTF/WOFF2, owner-only
downloads and immutable sources. Partial/zero cases retain bound region failures;
later analysis cannot change older job receipts. Unknown/repeated region reports
refuse publication even after outer report/manifest hashes are recomputed.
The final focused photo database suite passed8 tests.

One synthetic fixture measured0.548s for analysis/publication and1.730s for all3
font formats. A separate simple94-region batch took63.8ms in extraction and272ms
including child startup, with approximately80MiB observed peak RSS. These are local
engineering timings, not hosted latency or arbitrary-handwriting measurements.
Queue wait and human selection/review are separate.

The application unit gate passed831 tests with research suites excluded. The combined
frontend suite passed220 before the final Writing Desk repair; its focused checks
passed31. Current local checkpoint records subsequent combined/browser closeout.

Browser verification has drawn an A, saved/approved its spacing revision, rendered
its real specimen and compiled all3 formats. It has uploaded an ordinary synthetic
PNG, restored a saved B selection after refresh, processed it, inspected immutable
crop/mask evidence and approved it. Final updated-font Writing Desk/download checks
are being completed; no unexecuted browser result is claimed here.

## Limits

No real model, private volunteer evaluation or existing private font was executed
or rebuilt. This does not establish automatic words/sentences, connected cursive
separation, a handwriting accuracy percentage, writer fidelity, missing-character
generation or hosted readiness. Missing characters remain missing.

Hosted storage, shared limits, provider/identity/recovery, physical devices and
OS/PDF acceptance retain their separate gates. No feature publication, merge or
deployment is included in this local package.
