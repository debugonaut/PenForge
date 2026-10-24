# Penforge contracts

The Python models are the canonical source for strict JSON Schemas. OpenAPI describes the implemented HTTP surface. Account projects, private capture/review and approved font exports are implemented locally alongside an explicitly selected demo; registration alone does not qualify models or establish hosted readiness. Use the [codebase map](../../docs/CODEBASE_MAP.md) and phase evidence to distinguish actual runtime paths from future contracts.

Read the [source contracts](../../docs/specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), Part A §§3–10 and Part B §§6.4, 8.6, 9.3, alongside the [Phase 00 plan](../../docs/backend-ml-infra/phase-00-foundations/plan.md).

## Generation and verification

From the repository root, after `uv sync --locked` and `npm ci`:

```sh
uv run python packages/contracts/scripts/export.py
npm run contracts:generate
uv run python packages/contracts/scripts/export.py --check
npm run contracts:check
uv run python packages/contracts/scripts/check_compatibility.py
npx tsc -p packages/contracts/tsconfig.json
uv run pytest tests/backend/test_contracts.py
node --test tests/api-boundary.test.mjs
```

`export.py` imports the real app factory. It exports deterministic sorted JSON, independent schemas and job payload definitions. Do not apply a different JSON formatter to these generated files. `openapi-typescript` **7.10.1**, pinned in the root lockfile, generates `src/generated.ts`; the existing pinned Prettier formats its TypeScript output. `src/client.ts` is a small typed fetch transport whose accepted paths and response types come from generated OpenAPI. It performs no calls until explicitly invoked. It has no future feature routes and does not attach credentials or migrate drafts.

`compatibility/v1-baseline.json` retains the initial v1 schemas and implemented HTTP surface. The checker conservatively rejects changed existing field shapes, required fields, defaults, bounds, enums, removed properties and changed operations. Optional property/definition additions and new HTTP paths are allowed. A breaking contract requires an independently versioned replacement, retained old fixtures and an explicit compatibility review. This baseline should not be rewritten merely to make CI green. Workers must retain the previous emitted version for at least one release cycle. Schema comparison cannot prove custom validator compatibility; retained v1 fixtures and validation tests run alongside it.

## Version and validation policy

REST is `v1`. Outline, stroke, template, calibration, ML result, job payload and model registry versions are independent even though each currently starts at integer `1`. JSON uses **snake_case**. Existing frontend camelCase/domain types remain unchanged.

The analysis job now supports **v1 and v2**. Explicit Latin fixed-round canvas requests
emit [payload v2](jobs/analyze_samples_job.v2.json), `drawing_mode: fixed_round_v1`,
with an assignment for every source. Legacy v1 retains its schema and raster converter;
no absent mode is silently upgraded. Queue dispatch checks both envelope and payload
versions. Other job types still support v1 only. The [brush policy](policies/canvas-round.v1.json)
and [local evidence](../../docs/canvas-font/evidence.md) document direct curve geometry,
pressure preservation and exact provenance checks. This does not qualify cursive or
per-letter typography. `AnalyzeSamplesJob` remains the retained v1 Python type;
use `parse_analysis_payload` for versioned runtime dispatch.

Independent [freeform input](schemas/freeform_analysis_input.v1.json) and
[alignment evidence](schemas/freeform_alignment.v1.json) v1 describe original
observed English canvas ink, recognized text hypotheses, line/word regions and
source point spans. Supplied transcription remains context; unresolved ink and
competing hypotheses remain pending. The [binding adapter](../ml_core/freeform.py)
checks exact bytes/document/dimensions, method identity and complete non-overlapping
source-point accounting. This is preparation only: no normal API/worker route calls
it, no model is installed, and it cannot publish a glyph manifest or approve a
letter. Source ownership must be checked independently by a future caller. Photos,
edited/legacy captures, non-identity transforms and font metrics are outside this
version. See the [sequence and limits](../../docs/freeform/plan.md).

Every model forbids unknown properties, nonfinite numbers and unsupported versions. IDs are bounded opaque identifiers or UUID strings, never file paths/URLs. Unicode values are scalar codepoints; unpaired surrogates are rejected. Unicode normalization is not applied to observed evidence. Multiple codepoints are retained for contextual/Devanagari sequences; direct cmap glyphs have exactly one scalar while sequence glyphs are explicitly `gsub_only`.

Outline geometry uses baseline origin, positive x right and y up. It preserves lines, quadratics, cubics, multiple contours, holes under explicit `evenodd` fill, negative descenders and overshoots. Every filled contour explicitly closes to its start. Space U+0020 has metrics with an empty outline; missing letters cannot masquerade as empty approved glyphs. Phase 04 implements exact pixel-union tracing, optional bounded curve fitting and explicit-frame stroke union with measured synthetic controls. Font winding conversion and compiler/rendering parity remain Phase 05 work. This schema does not certify visual fidelity or a valid font.

Provisional safety defaults are 128 contours, 4,096 segments per contour, **8,192 total nodes**, coordinate/metric magnitude of **8 em**, 2,000 strokes, 20,000 points per stroke and **100,000 total points**. `GlyphOutline.model_validate(..., context={"max_outline_nodes": ..., "max_em_extent": ...})` and `StrokeDocument.model_validate(..., context={"max_stroke_points": ...})` allow trusted deployment configuration to tighten/adjust aggregate limits. These limits protect parsing/geometry work; they are **unmeasured safety controls**, not extraction quality thresholds or resource guarantees. Actual body/decoder limits are applied by capture and extraction services; they do not establish hosted capacity.

Observed captures require actual capture dimensions and nondecreasing relative timestamps, with optional observed pressure/tilt/twist/pointer metadata. Missing pen values remain null. A legacy x/y-only document explicitly records `timing: unavailable_legacy`, null dimensions, null pointer/timing/pressure/tilt/twist and ordered copied points. Derived operations require an immutable parent hash; operations do not rewrite raw points. Only translation operations are specified now; future editing operations need explicit contracts.

Template identity is exactly public `template_id:template_version:page_index`. User/project identifiers and secrets are not permitted in the payload. Markers and cell rectangles stay within the page, do not overlap, have unique identities and resolve their guides. The small manifest fixture is **test-only geometry**; the finalized public Phase 04 manifests in `templates/` separately pin physical markers, quiet zones and dropout colors. Digital printability and synthetic distortion evidence are recorded; physical printing and real-camera production gates remain open. Calibration prompts explicitly are not ground truth, and an evaluated prompt requires an evaluation reference.

Worker result manifests pin source hashes, template hash when relevant, model/algorithm/preprocessing provenance, fencing token, ranked child IDs/checksums/byte sizes and a quality report reference. Worker proposals can only be `pending`; a manifest cannot approve a glyph. Scores/resources/ETA are null without evidence references. Backend ownership, lease validity, actual child-byte verification and registration/promotion are later service responsibilities. A checksum alone does not authenticate a worker or authorize content.

Model formats are ONNX, safetensors or JSON rules; arbitrary model URLs/paths and pickle are absent. Benchmark references are required for registry promotion and measured resource claims. No model is selected, trained, validated, loaded or deployed by this package.

## Hash encoding

The registry names `penforge-tagged-json-ieee754-v1`. Python and TypeScript implement the same domain:

1. Null becomes `["null"]`; booleans become `["bool", value]`.
2. Numbers become `["number", hex]`, where `hex` is the 16 lowercase hex digits of the big-endian IEEE754 binary64 representation. Negative zero normalizes to positive zero. Nonfinite numbers and integers outside JavaScript's safe integer range are rejected. `1` and `1.0` have the same encoding.
3. A string becomes `["string", hex]`, containing its strict UTF-8 bytes as lowercase hex. Lone surrogates are rejected. No Unicode normalization occurs.
4. Arrays become `["array", [encoded elements]]`, retaining order.
5. Objects become `["object", [[UTF-8-key-hex, encoded value], ...]]`, sorted lexicographically by UTF-8 key hex. Only string keys and ordinary JSON values are accepted.
6. Serialize this tagged structure as compact ASCII JSON without whitespace, then SHA-256 its bytes. The digest is `sha256:` followed by 64 lowercase hex digits.

This deliberately uses its own versioned encoding, rather than claiming conformance to an external canonical-JSON standard. Tags prevent strings/numbers/containers from colliding, binary64 avoids Python/JavaScript decimal formatting differences, and byte-sorted keys avoid astral-codepoint ordering differences. It preserves finite numeric precision representable by the wire JSON consumer.

For an ML result, first materialize the schema's defaults, then hash the complete serialized manifest body **excluding the root `artifact_hash` property only**. Nested hashes are included. Referenced child bytes have their own ordinary SHA-256; their digests and sizes are pinned in the manifest. The model validates its manifest hash on ingestion. The shared [hash fixtures](fixtures/hash-vectors.v1.json) exercise curves, strokes, non-BMP/Devanagari strings, combining marks, cross-runtime key ordering, float exponent cases and root-field exclusion.

## Existing frontend audit and explicit mapping

| Observed source                                                                                                                 | Boundary decision                                                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/lib/contracts.ts`: `FontProject.isDemo` is literally true                                                                  | `api-boundary.ts` maps server metadata to a separate `mode: cloud` view. It cannot be cast into the demo model. Server `ready` metadata does not establish font availability.                                                            |
| `DrawingCanvas.tsx`: x/y normalized to the canvas rectangle; y grows downward; actual timing/pressure/dimensions are not stored | `preserveLegacyStrokes` copies the source and marks missing observations explicitly. No automatic local/cloud migration or upload occurs.                                                                                                |
| `GlyphShape.tsx`: points multiply by 100 inside `viewBox="0 0 100 100"`; CSS rotates/scales/clips the preview                   | `capturePointToFont`/inverse require supplied capture size, baseline, origin and one shared font-units-per-pixel scale. The SVG adapter flips y exactly once. Nonzero prototype rotate/scale/crop edits are rejected as canonical edits. |
| Mock job states omit cancellation and simulate progress                                                                         | Distinct cloud job mapping retains cancellation, real completed/total units, plan revision, denominator change reason, null progress/ETA and review as a separate flag. Successful analysis may still require review.                    |
| Project storage envelope 1 uses `penforge-projects-v1` plus recovery; Writing Desk has its own draft envelope                   | Both existing stores and service interfaces are untouched. Explicit service selection defaults to demo; integration requires user action.                                                                                                |
| Capture upload creates a local JPEG preview capped at 1,000px; source file bytes are not retained in that path                  | This local demo cannot be treated as a server immutable original. Original upload ingestion is Phase 03.                                                                                                                                 |
| Writing Desk loads actual font files and exports PNG/SVG with a shared renderer                                                 | These real document paths remain untouched and separate from unavailable generated personal fonts.                                                                                                                                       |

The mapping tests use supplied transforms, distinct view types and every durable job state. They do not connect an existing UI control to a cloud endpoint or fabricate observed stroke data.
