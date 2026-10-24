# Penforge Latin validation profile v1

All five layers are mandatory for every exported format. A required validator that
is unavailable, skipped, times out or fails yields `failed`; no font download becomes
ready. Reports contain coded outcomes, mappings and measurements, never raw exception
messages. Validation reports are immutable assets and terminal export metadata.

1. **Structural:** signature/outline technology, required tables, all table checksums
   and whole SFNT checksum, full FontTools decompilation/reopen/serialization, glyph
   order/count/cmap, finite bounded geometry, advances/bearings, consistent horizontal
   and vertical metrics, approved metric-only space and visible .notdef.
2. **OTS:** execute the verified wheel's real OTS 9.2.0 binary on the original TTF,
   CFF OTF or WOFF2, require success and unchanged cmap. Retain the sanitized hash.
   The validated original bytes are exported; sanitizer output never silently replaces
   the user's outline bytes.
3. **FontBakery:** run the checked-in `penforge-latin-v1` profile, comprising exactly
   `required_tables`, `sfnt_version`, `name/char_restrictions` and
   `os2_metrics_match_hhea`. Every check must appear once. PASS/INFO are successful;
   WARN is retained as a visible best-practice warning. FAIL/ERROR/FATAL/SKIP,
   missing checks or a nonzero exit code block readiness. These four checks are not
   a claim of passing every FontBakery profile. WOFF2 receives structural/OTS checks
   in its original container; FontBakery checks the verified decompressed SFNT.
4. **HarfBuzz:** every covered scalar must shape to its expected nonzero glyph and
   approved advance. Mixed covered letters/digits/punctuation/spaces must avoid
   .notdef; a deliberately missing scalar must map to visible glyph zero. No calt,
   joins, ligatures or kerning behavior is advertised or generated.
5. **Render:** FreeType through Pillow must produce nonblank ink for every covered
   drawn scalar; approved space is deliberately blank. The covered shaping test
   establishes that those renders do not use .notdef. Missing fallback must itself
   have visible ink. Retain a raster and its hash.

Partial coverage is independent of technical validity. A 52-letter inventory may be
valid with a precise missing-ASCII warning. Unapproved space is omitted from cmap,
never given an invented advance. An approved U+0020 maps metrics without an outline.
.notdef never increases coverage. Complete English requires all 95 printable ASCII
mappings and all required validation layers. Supplementary scalar fixtures additionally
exercise cmap 12; they do not expand the complete-English definition.

Snapshots freeze pending generated candidate inventory separately from approved glyphs.
Legacy snapshots that did not freeze that context report `not_frozen_in_legacy_snapshot`,
not an inferred historical observation. Approved generated provenance survives edits and
rollback through the source candidate. Devanagari stays unavailable/shaping unvalidated;
Latin success does not qualify Indic behavior, extended scripts or real handwriting.
