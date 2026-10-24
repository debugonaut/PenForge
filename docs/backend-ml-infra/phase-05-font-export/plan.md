# Phase 05 — Real, deterministic font export

Status: implemented locally on 2026-10-20, with automated, Phase 05 browser and CoreText acceptance. OS-gentle browser verification remains unexecuted; see [evidence](evidence.md). Source: Part B S5, §§6.8, 8.5, 8.7–8.8; Part A IC-01/04.  
Depends on phase 04. Unlocks phase 06. M1 export has no unvalidated alternates or cursive joins.

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md) and project handoff (`../../../PROJECT_STATUS.md`; local record) before implementation. Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) when integrating the frontend.

## Outcome

Approved user glyphs compile into actual installable TTF, CFF OTF and WOFF2 fonts, with immutable snapshots, provenance and truthful partial-coverage/validation reports.

## Work packages

1. Implement the font-engine package independently of DB/storage/ML dependencies. Consume canonical outline+metrics snapshots, convert cubic→quadratic for TrueType within a declared tolerance, and use T2CharStringPen for CFF. Format is determined by actual table technology, not filename.
2. Normalize contours with explicit winding/hole handling and bounded complexity; remove degenerate overlaps through a reviewed compatible path. Preserve baseline, side bearings and natural proportions. Reject invalid geometry instead of repairing it silently beyond tolerance.
3. Build deterministic glyph order/names, visible .notdef, explicitly measured/approved space advance, cmap, metrics and required tables. Missing characters are absent from cmap with warnings, not replaced by fabricated authentic glyphs. Freeze UPM/options and vertical metrics in the snapshot/config.
4. Sanitize family/style/PostScript names and metadata; record license/embedding fsType. Reconcile v2's proposed export-record timestamps/UUID name ID with identical-snapshot reproducibility in an ADR before implementation. Proposed resolution: freeze a canonical build manifest with snapshot creation time, family/style/options/tool versions and a content-derived unique font ID; keep per-request export UUIDs in database/report metadata outside font bytes. Separate export requests for the same frozen build inputs then produce identical bytes, as do retries. WOFF2 uses the validated compression dependency; no fake extension switching.
5. Implement POST exports as one transaction: authorize, create/pin consistent export snapshot, reserve budgets, insert format rows and one compile_export durable job. Export params contain IDs/options only. Existing snapshots must belong to this project/owner. Replay returns the same export IDs.
6. Run structural reopen/roundtrip/table/checksum/metrics checks; OTS; a documented FontBakery subset; HarfBuzz Latin shaping; render smoke with nonblank covered text and visible missing-glyph fallback. Packaging availability is verified during implementation. Missing required validators blocks a fully passed result.
7. Store font/report assets immutably and finalize each row once under the job lease. Hash uploaded bytes, retain actual cmap and tool/model provenance. Final ready/failed rows and assets cannot be overwritten. A new build creates a new export, not a replacement.
8. Expose owner-only export metadata and signed download URLs with short TTL. Coverage includes missing sequences, generated pending/approved synthetic counts and unvalidated features. A technically valid partial font can download with accurate warnings; all-95 status additionally requires successful validation.
9. Integrate real export states into existing export UI. For Writing Desk, load the owner-authorized generated font via an authenticated short-lived asset flow and preserve paper/ink/layout exports. Keep sample Caveat, user-imported fonts and newly generated fonts labeled distinctly.

## Acceptance tests

- Canonical fixtures with lines/quadratics/cubics, holes, descenders, non-BMP mappings and space compile/reopen correctly for each format.
- Full 95 ASCII and partial 52-letter fonts are technically valid with correct distinct coverage. .notdef is visible and not counted; unapproved glyphs are excluded.
- Repeated build bytes match for the same snapshot/options/toolchain, including two distinct export requests created at different wall-clock times and same-record retries. Canonical metadata/timestamps, order and compression do not introduce drift; deliberately changed build metadata changes the build identity.
- Latin shaping maps every covered codepoint without .notdef; mixed punctuation/digits/spaces and missing characters work predictably. calt is not advertised before phase 08.
- OTS/structural failures cannot become ready. Skipped shaping cannot yield passed; warning/failure policy is explicit.
- Cross-user snapshots/exports/downloads return 404; expired capability fails; logs omit signed URLs and names/content.
- Concurrent/retried finalization is idempotent; stale worker cannot finalize; DB immutability tests protect ready/failed exports.
- Downloaded fonts render in browser and a representative desktop font consumer; Writing Desk renders/exports the actual selected font without losing layout fields.

## Evidence and rollback

Deliver actual fixture fonts, hashes, validator reports, shaping/render captures, reproducibility tests, generated API client and package/resource inventory. Benchmarks report local hardware and inputs, not cloud capacity. Roll back compiler version for new work; preserve all old assets/snapshots. Stop new exports if validation fails without revoking already validated downloads.

## Open gates

Local OTS/FontBakery packaging/profile and conversion policy are resolved in
[ADR 0011](../../decisions/0011-phase-05-reproducible-builds.md), the
[validation profile](validation-profile.md) and [dependency decision](dependencies.md).
[Development instructions](development.md) describe provisional safety budgets;
fixture measurements do not establish hosting capacity. Real-handwriting/physical
capture quality, hosted identity/App Check, shared rate limiting, provider selection,
consent/deletion and public readiness remain unresolved. Devanagari exports remain
shaping_unvalidated/unavailable until 09; Latin validity does not justify an Indic-ready
claim. Phase 03/04 browser checks remain intentionally pending. Stop after Phase 05;
Phase 06 requires separate authorization.
