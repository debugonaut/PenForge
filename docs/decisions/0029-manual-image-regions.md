# 0029 — Source-bound assisted photo letters

Status: accepted. Date: 2026-10-29.

The owner approved selecting and labeling letters on existing photos, reviewing
their outlines, then creating real fonts. Processing should be fast, and volunteers
must not need to rewrite existing pages.

Use explicit analysis v3 `manual_regions_v1`. Each selection binds its sample,
original SHA-256, EXIF-oriented dimensions, rectangle, assigned character, baseline,
common page scale and threshold. Decode once per source and process regions together.
Source bytes remain immutable; labels are user input, not recognition results.

Both scheduler paths admit v3. Checkpoints include source, configuration and extraction
implementation. Fenced publication validates assignment/status, attempt, method and
artifacts; candidates stay pending until existing review and explicit approval.
Immutable job-specific outcomes support corrections without a latest-sample race.

Retain v1/v2 drawing/template semantics and job schemas. The contract registry adds
`analyze_image_regions_job` with schema version3 as a separate opt-in capability;
its legacy `analyze_samples_job` entry still advertises versions1/2. This preserves
the original registry field's two-entry constraint and HTTP schema. No compatibility
baseline or checker is weakened.

Alternatives were automatic recognition/alignment and requiring printed templates.
No qualified automatic aligner exists here; templates cannot consume existing
notebook pages. Assisted selection is a distinct useful route. Automatic words,
connected-letter separation and writer-fidelity qualification remain separate work.

Engineering bounds:96 regions,16 million source pixels,6000 pixels per side,
4 million pixels per crop and8 million crop pixels per batch. Existing isolated
CPU/wall/memory and artifact bounds remain enforced. See
[evidence](../photo-font/evidence.md) for actual measurements and limits.
