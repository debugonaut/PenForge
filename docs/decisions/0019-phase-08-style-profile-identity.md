# 0019 — Snapshot-based deterministic style identity

Status: accepted for local Phase 08. Date: 2026-10-20.

The existing style_profiles table stores new immutable versions. Profile identity hashes
the canonical source snapshot digest, approved revision/sample/hash references, complete
algorithm/configuration and measured descriptors. Creation time, profile UUID and version
counter are outside the hashed body. Identical inputs reuse the same immutable record.
Historical profile values already copied into snapshots and exports remain frozen.

Only owner-approved authentic source revisions qualify. Synthetic ancestry remains
synthetic after editing/restoring. Foreign/unapproved references fail closed. The owner
transaction holds active user/project locks through measurement and publication; workers
receive no style insert/update privileges. Personal measurement needs no training consent.

Version one measures sampled outline bounds, advances/bearings, cap/x/ascender/descender
extents, horizontal filled-ink chord distributions and ink-centroid slant regression.
Curve sampling uses 16 uniform steps and 32 scanlines, rounded to six decimals. These
are explicit geometric descriptors, not physical pen-width/angle or learned embeddings.
Isolated normalized glyphs do not observe line baseline variation or actual interglyph
spacing: those remain null. Raw StrokeDocument sources contribute within-capture stroke
durations, stroke gaps and pressure only when those values were observed. These isolated
capture intervals are not sentence rhythm. Missing observations remain null. Algorithm
or configuration changes require a new identity, never invented metadata.
