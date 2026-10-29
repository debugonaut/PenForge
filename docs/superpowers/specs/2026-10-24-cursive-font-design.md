# Cursive handwriting font — private rebuild design

## Goal

Create a careful, local digital-font preview from the owner-supplied cursive page and
let the owner test it in PenForge's existing Writing Desk. Keep the source image,
traced contours, review sheets and generated font files in the ignored private research
run. Do not add handwriting-derived assets or a sample-font entry to public site code.

## Source and review boundary

The existing first pass is frozen evidence. Its 94 assigned drawn characters and
proposed space cover the printable ASCII set, but all entries remain pending. Its
threshold halos, fragmented `W`, detached components, edge-touching brackets and
shared preview baseline make it unsuitable for compilation. Preserve it unchanged.

Create a new, uniquely named private derivative from the supplied page. Recheck every
crop against the original blue-ink strokes, including letters that cross ruled lines,
small marks, counters, punctuation and crop edges. Retain source-supported faint ink;
remove page rules and JPEG artifacts only where they can be distinguished from the
writer's strokes. Record uncertain glyphs rather than silently inventing or deleting
parts. Agent review produces proposals only. The owner must explicitly approve the
per-glyph review sheets before a frozen approved snapshot is built or compiled.

## Font and preview approach

Use the existing deterministic font engine and its pinned compiler 1.0.1 / spacing
policy v2. Map the 94 observed glyphs and a measured space, with explicit metrics and
consistent baselines. Build local TTF, CFF OTF and WOFF2 after owner approval; verify
all printable ASCII coverage, structural and raster validation, cross-format outline
consistency and repeat-build determinism.

Test the resulting local font through the Writing Desk's existing font-import control.
Use the real stationery renderer and a sample string containing upper/lowercase,
digits and punctuation. No public preview route or tracked font asset is needed. Keep
the preview font private and uncommitted.

## Limits

The source page provides individually written glyphs and a sentence, but PenForge's
current shaping support does not produce cursive contextual joins. The preview can
faithfully reproduce the individually reviewed glyph shapes and measured spacing; it
must not be described as an exact connected-script reproduction. Pair kerning does
not establish natural joins or writer fidelity. Do not use OCR, model inference,
training, account upload or a public handwriting asset for this work.

## Acceptance checks

- A fresh private derivative leaves the frozen first-pass files and source bytes
  unchanged.
- Every printable ASCII glyph has a source crop, reviewed contour and explicit
  metrics; unresolved marks remain documented and block approval.
- The owner reviews and approves the complete per-glyph sheets before compilation.
- The approved build passes the font engine's required validators and deterministic
  rebuild check for TTF, OTF and WOFF2.
- The owner can import each intended test font in the local Writing Desk and inspect
  coverage, alphabet/punctuation specimens and real stationery rendering.
- Git contains no source image, private review sheet or generated handwriting font;
  no commit or push is part of this Cursive work package.
