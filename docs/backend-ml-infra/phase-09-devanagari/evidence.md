# Phase 09 evidence

Updated 2026-10-20. Phase 09 is implemented locally as an experimental, owner-controlled Devanagari path. This evidence covers Unicode handling, template contracts, font engineering, shaping and local export integration. It does not claim real handwriting recognition, handwriting fidelity, language coverage or production readiness.

## Source and data boundaries

Unicode behavior is derived from the pinned Unicode 17.0.0 UCD files in [`packages/contracts/unicode/17.0.0`](../../../packages/contracts/unicode/17.0.0/ReadMe.txt). `sources.json` records the byte hashes, URLs, license and generated-table hashes, including `NormalizationTest.txt`. The generator is deterministic and performs no fetch. Cross-language tests cover the full Unicode 17 grapheme-break and normalization conformance data. Capture keeps observed source codepoints, treats normalization as derived evidence, and uses grapheme clusters for user-facing length/layout boundaries.

The bounded `deva-tier1-ucd17-v1` set is derived from Unicode properties. It covers 121 Devanagari scalars, with 33 explicit space/punctuation interoperability entries; out-of-policy entries have recorded exclusion reasons. No corpus-derived Tier 2 conjunct inventory or common-word list is supplied. The future production gate rejects the starter set and synthetic fixtures as substitutes for licensed, writer-disjoint corpus evidence.

The printable template has three versioned A4 pages with 49, 49 and 33 labels. Each page retains geometry markers, a QR payload and a manifest hash. The image-based marker check measured 1.03 px error. Page renders are saved under `artifacts/phase09/templates` (`../../../artifacts/phase09/templates/`; local record). Physical printing, scanning and camera extraction were not tested.

## Shaping and font evidence

A Noto Sans Devanagari variable font and its OFL 1.1 text are pinned at Google Fonts commit `2bc5d431b584d89e646214bf6cfa75494e76ceea`. The font SHA-256 is `14ec4af41f27482216d1c2229f417ff9b1425e1babb014e57d1d40d03229853e`. Its role is reference-font engineering only; it is not handwriting, Penforge-generated source data, or an evaluation dataset.

The font engine uses `DFLT` and `dev2`, evidence-backed GSUB features, anchor-backed GPOS mark positioning and GDEF glyph classes. HarfBuzz 12.3.2 applies Indic reordering. The source reconciliation is recorded in [ADR 0023](../../decisions/0023-phase-09-unicode-and-shaping.md), [ADR 0024](../../decisions/0024-phase-09-tier-one-and-templates.md) and [ADR 0025](../../decisions/0025-phase-09-forms-fallback-and-gates.md). Unicode examples include `क्ष`, `कि`, `र्क`, `श्र`, `क्त`, both nukta encodings, below-base vowels, stacked marks, joiners and malformed inputs. Feature-disabled traces, source scalar clusters, offsets, advances, `.notdef`, explicit half-form/visible-virama fallback and deterministic hashes are retained in `shaping-fixture.json` (`../../../artifacts/phase09/shaping-fixture.json`; local record). All 11 starter examples passed against this pinned engineering font. This is a regression check, not a human typography approval or production language test.

The isolated font measurement produced six TTF, CFF OTF and WOFF2 builds (two per format). Each repeated pair had identical bytes. Every report is `passed_with_warnings` with `devanagari_experimental` and `corpus_and_handwriting_evaluation_pending`; the warnings remain part of the result. Five validation layers ran in a network-denied worker. macOS CoreText registered the fixture and rendered 29 covered glyphs with zero `.notdef` glyphs. The render is in `coretext.png` (`../../../artifacts/phase09/coretext.png`; local record), with its report in `coretext.json` (`../../../artifacts/phase09/coretext.json`; local record). These are fixture checks, not cross-platform or personal-font acceptance.

## Application and privacy evidence

Migration `0012_devanagari` pins the required set to Devanagari projects and retains the existing 27 application tables. Regression evidence in `migration-privacy.txt` (`../../../artifacts/phase09/migration-privacy.txt`; local record) exercises migration up/down/up and verifies that earlier Phase 08 style/source data survives while Devanagari-only evidence is erased and stale work is denied. Digital capture accepts a bounded source scalar or supported one-grapheme Devanagari cluster, keeps source encodings distinct and does not auto-approve a candidate.

A local Firebase Auth emulator, isolated PostgreSQL schema and network-denied worker were used for the synthetic acceptance environment. The project is explicitly named “Devanagari engineering fixture (Noto, not handwriting).” The live flow reached an unapproved review candidate and real private TTF/OTF/WOFF2 export for approved fixture outlines. Nothing from that fixture qualifies the recognizer or demonstrates the user's handwriting. The current source remains opt-in and exports carry experimental/pending-evaluation warnings.

## Verification

| Check | Result |
| --- | --- |
| Focused Phase 09 backend set: Unicode, capture, inventory, templates, recognition boundary, shaping, features, geometry, migration, export and production gate | **57 passed** |
| TypeScript/frontend tests, including Python/TypeScript Unicode parity and Writing Desk grapheme wrapping | **118 passed** |
| TypeScript check, ESLint, generated contracts, compatibility, research schema/metadata checks, formatting, mypy, boundary checks and local documentation links | **Passed**; see `types-final.txt`, `eslint-final.txt`, `contracts-final.txt` and `quality-run-04.txt` |
| Production Next.js build | **Passed** with an Autoprefixer compatibility warning in `CloudReviewPanel.module.css` |
| Research governance tests | **26 passed** after updating the Devanagari starter-record limitations |
| Auth emulator | **5 passed** in the separate Phase 09 emulator run |
| Broader backend run | **601 passed**; one existing queue fairness test failed once when PostgreSQL returned `Interrupted system call`. The same test passed on an isolated retry. No Phase 09 test failed in the 57-test focused run. |
| Research admission audit | **0 approved handwriting datasets, 0 measured handwriting benchmarks and 0 completed handwriting experiments** |

Logs and generated reports are in `artifacts/phase09` (`../../../artifacts/phase09/`; local record). The research audit validates metadata only; it does not load handwriting or model data.

The previous local browser run exercised the synthetic capture, pending-review and fixture-export path. Current Work settings report local browser control disabled, so I could not complete Phase 09's responsive visual/keyboard matrix or inspect the final Writing Desk engineering-fixture label during this continuation. That browser acceptance remains pending; this evidence does not mark it passed. The previously documented Phase 03/04, Phase 05, Phase 06, Phase 07 and Phase 08 pending browser checks also remain open.

## Remaining gates

There is no handwriting recognizer in this phase. No handwriting dataset was downloaded or approved, and no research participant data was used. Recognition, segmentation, review-effort, writer fidelity, corpus-frequency coverage and numeric capability thresholds remain unmeasured. A licensed 200–500-word corpus, human typography review, physical print/scan assessment and representative renderer review are not present. Devanagari remains experimental and must not be described as production-ready or as a generated personal handwriting font.
