# 0023 — Preserve Unicode evidence and use the Indic shaping engine

Status: accepted for explicitly authorized Phase 09 engineering. Date: 2026-10-20.

The user's Phase 09 request supersedes the earlier stop-before-09 authorization,
without authorizing Phase 10, datasets, training, providers or production promotion.
Work packages run sequentially, without subtask agents.

Use ordered Unicode scalar arrays as source identity, never UTF-16 offsets. Preserve
precomposed nukta and decomposed sequences byte-for-byte. Extended grapheme boundaries
use pinned Unicode 17.0.0 UAX #29 revision 47 data, including GB9c; graphemes are not
OpenType clusters, linguistic aksharas or recognized handwriting. Source sequences and
explicit NFC/NFD derived evidence have separate hashes. Runtime normalization records
its implementation/version; it never changes source identity. Capture assignments
accept one supported scalar or one Devanagari grapheme (bounded to 16 scalars).
Malformed Unicode is rejected. Isolated marks are valid capture targets. ZWJ/ZWNJ are
permitted only immediately after virama within an explicit Devanagari sequence; other
format/bidi controls are unsupported for capture, while generic round-trip preserves
valid scalars. Invalid orthographic sequences request review, never silent repair.

Primary references checked 2026-10-20:
- [Unicode 17 chapter 12](https://www.unicode.org/versions/Unicode17.0.0/core-spec/chapter-12/)
- [UAX #29 revision 47](https://www.unicode.org/reports/tr29/tr29-47.html)
- [UAX #15 revision 57](https://www.unicode.org/reports/tr15/tr15-57.html)
- [Microsoft Devanagari OpenType development](https://learn.microsoft.com/en-us/typography/script-development/devanagari)
- [HarfBuzz cluster semantics](https://harfbuzz.github.io/working-with-harfbuzz-clusters.html)

The source specification feature list is an investigation list. Emit explicit DFLT
and dev2 default language systems; no legacy deva without compatibility evidence.
HarfBuzz selects dev2 from script Deva and performs syllable analysis/reordering.
Never implement visual-order swaps in GSUB. Selected evidence-backed substitutions may
use ccmp/nukt/akhn/rphf/rkrf/blwf/half/cjct/pres/abvs/blws/psts/haln and dist/abvm/blwm.
Only emit a lookup when its approved forms/anchors exist. Current Microsoft guidance
omits pstf from its Devanagari feature order; do not add a speculative rule. vatu is
legacy compatibility, unnecessary with rkrf and half. locl needs language-specific
review; no localized forms are invented. Feature tags are never user checkboxes.

Extend the existing compiler and immutable review/snapshot/export lineage. Latin v1
manifests serialize identically and take the original code path. Devanagari engineering
uses an explicit profile and build-hashed feature/anchor evidence. Synthetic authored
shapes and redistributable reference fonts are fixtures, never the owner's handwriting.
Recognition, segmentation and writer fidelity remain independent research gates.
