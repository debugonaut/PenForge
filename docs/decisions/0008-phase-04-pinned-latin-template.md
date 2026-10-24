# 0008 — Phase 04 pinned public Latin template

Status: accepted for local template generation; physical-printer and real-camera validation pending.
Date: 2026-10-19. Scope: Phase 04 template work package only.

## Context

The user authorized Phase 04 after leaving Phase 03 live browser verification pending,
then requested one task at a time. Work now proceeds sequentially. The source is
[v3 Part A IC-03 and Part B §8.4](../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md)
and the [existing phase plan](../backend-ml-infra/phase-04-extraction-review/plan.md).

## Decision

Freeze two A4 public pages in millimeters, with 94 isolated cells mapping exactly to
U+0021–U+007E. Space is handled later through metrics and explicit review. Four ordered
10 mm ArUco markers per page have 3 mm quiet zones. The manifest also pins the QR rectangle,
four-module QR quiet zone, printable margin, 20 mm print reference, guide colors and
baseline/cap/x-height/descender geometry. No private ID, credential or token appears in
the printed identity; QR content is only template ID, version and page.

Both SVG and raster generators consume the archived validated manifest. The PDF generator
uses its actual page dimensions and the generated 300 DPI master; the catalog records
each manifest's tagged canonical digest. Printed destinations provide context only; they
do not establish which shape the writer actually supplied. Black or dark neutral ink is
requested because color-dropout handling must be evaluated separately.

New optional contract fields omit absent values during serialization. Existing v1
template fixtures and hashes remain unchanged; old illustrative manifests without pinned
print geometry cannot be rendered as this new printable template. These drafts had no
registered sample/job lineage before finalization. Further layout changes require a new
template version and retained archived manifests.

## Evidence and limits

[Template evidence](../backend-ml-infra/phase-04-extraction-review/evidence.md) records 68
template/contract checks, both inspected Poppler renders, exact A4 PDF media boxes,
decoded public QR payloads, four correctly placed markers and measured scale references.
No physical sheet was printed or photographed. These are digital printability checks,
not real handwriting, extraction accuracy or production readiness evidence. Phase 05
font compilation remains outside the current authorization.
