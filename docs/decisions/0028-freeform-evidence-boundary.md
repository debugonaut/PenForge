# 0028 — Independent freeform evidence before runtime admission

Status: accepted for local preparation. Date: 2026-10-24.

The owner clarified that English words/sentences must feed individually editable
letters in the existing wizard. Current analysis v2 means an isolated assigned
fixed-round drawing, and saved recognition results provide no character aligner.

Add independent freeform input/alignment evidence v1 and a source-binding adapter
seam. Keep recognition hypotheses, supplied transcription, point spans, unresolved
ink and explicit review separate. Bind exact original bytes, canonical document,
dimensions, methods and region hierarchy; account for all source points without reuse.
This is research-only pending evidence, not a publishable glyph manifest or an access
grant. The adapter has no default model or synthetic runtime fallback.

Do not change analysis v1/v2, approval semantics, capability flags or normal creation.
Runtime admission/publication, photo masks, non-identity transforms and font metrics
need their own implementation and evidence. Real model execution remains owner-run.
See the [gap analysis, reconciliation and sequence](../freeform/plan.md).
