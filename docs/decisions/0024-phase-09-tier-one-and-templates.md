# 0024 — Source-backed Tier 1 and engineering capture prompts

Status: accepted for local Phase 09 engineering. Date: 2026-10-20.

Tier 1 uses Unicode 17.0.0 Indic_Syllabic_Category in U+0900–U+097F:
Consonant, Vowel_Independent, Vowel_Dependent, Nukta, Virama, Bindu, Visarga,
Avagraha and Number, plus danda/double danda. Include ASCII space and punctuation
as an explicit interoperability requirement; ASCII letters/digits are not Devanagari
Tier 1. Decompositions of encoded nukta letters are required sequence behaviors,
not additional encoded characters. ZWJ/ZWNJ are shaping controls with contextual
policy, never drawn glyph requirements. Record every block exclusion and source hash.
This bounded inventory does not claim all languages, Vedic text or every Indic script.

The immutable required_set_id is deva-tier1-ucd17-v1. Tier 2 is unavailable without
licensed frequency-derived corpus and reviewer provenance. Neither inventory count
nor a successful starter suite can establish readiness or M3 completion.

Reuse A4 printable geometry/markers/QR checks from Latin, with Devanagari headline,
baseline, upper/lower mark regions instead of cap/x heights. Each cell carries a scalar
or cluster; labels are shaped through a pinned OFL reference font with HarfBuzz, then
rendered as vector outlines. The fixture font and its license are separate from user
fonts. Engineering prompts remain unoptimized and lack independent localized review.
Expected labels never approve identity. Physical printing/camera quality requires
separate evidence. Changing guides, font, tool or generator requires a new manifest.
