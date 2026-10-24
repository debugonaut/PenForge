# Freeform package 1 — evidence and source binding

Implemented locally on 2026-10-24. The [plan](plan.md) records the actual missing path,
design alternatives, source reconciliation and remaining packages. [ADR 0028](../decisions/0028-freeform-evidence-boundary.md)
records the independent contract decision.

## Implemented behavior

The new strict [input/alignment contracts](../../packages/contracts/penforge_contracts/freeform.py)
have their own v1, separate from retained analysis job versions. They describe one
original observed English canvas capture in its original pixel frame. Supplied
transcription is explicitly not ground truth. Independent recognizer/aligner
identities, competing text hypotheses, scalar ranges, line/word regions and inclusive
stroke-point spans preserve proposed identities and unresolved ink. Scores need
measurement references; every result stays research-only and pending. All source
and text IDs, dimensions, hierarchy, ranges and result hashes are validated. An ink
region can hold multiple strokes, including a late dot/crossbar; repeated letters
keep distinct regions and text positions. Nothing fills an unseen character.

The [adapter/source binder](../../packages/ml_core/freeform.py) accepts immutable
bytes, checks raw SHA-256 and canonical document hash independently, verifies original
observed dimensions/metadata, pins request/method identity and checks real stroke
indices and centerline containment. It accounts for every point exactly once as
proposed or unresolved evidence. It does not assume that one stroke or connected
component equals one letter. Partial spans require split review; it never severs
them into independent strokes or invents cap geometry. Supplied-text mismatch keeps
recognition intact and adds a review reason. All proposed identities also retain an
unqualified identity/alignment reason. Returned evidence is immutable bytes with
bounded review references and no glyph outlines.

There is no default adapter: absent analysis reports unavailable. The protocol has
no model loading implementation and is not called by normal API/worker creation.
Tests provide authored inert bytes. A binding cannot be parsed as an existing
MLResultManifest or analysis job, so it cannot bypass publication/approval.
Caller-side authorization, method qualification and process isolation are still
required for future integration; possession of an ID/hash is not authorization.

Generated JSON Schemas, registry, OpenAPI components and TypeScript were exported
through the existing tools. Existing HTTP operations and every existing OpenAPI
component are exactly unchanged. All 14 prior standalone schema/job files and the
retained compatibility baseline are byte-identical to their scoped preimages.

## Verification performed

**42 new evidence tests** cover inert multi-letter writing, delayed dots, authentic
repeats, uncertain connected regions, competing recognition/transcription, unknown
references, scalar offsets, parent containment, duplicate text claims, source-point
reuse/omission, hash/source/method/context substitution, mutable adapter inputs,
legacy/edited-source rejection, byte/span budgets and measured-score requirements.
A fresh subprocess blocks model and geometry-framework imports while importing the
preparation boundary. No model framework or model was loaded.

**129 focused backend tests** passed across those tests and retained contracts,
canvas geometry and legacy extraction geometry. **178 frontend tests** passed.
TypeScript, ESLint, Python schema export drift, generated TypeScript drift, retained
v1 compatibility, scoped Ruff check/format, mypy and architecture boundaries passed.
The production build passed with the existing CSS `align-items: start` compatibility
warnings. Documentation link/source-integrity checks passed.

The initial new-test run exposed an expected-error assertion mismatch (Pydantic
rejects lone surrogates before the custom text validator) and the extraction package's
eager geometry imports. The adapter now belongs to the lightweight ML-core boundary;
the existing extraction initializer was preserved. Static checking also corrected
local variable/set type annotations. The complete scoped rerun passed. No existing
runtime gate was weakened to obtain a passing result.

Scoped preimages, the contract inventory and frontend/build logs are local-only under
`artifacts/freeform-boundary-20261024/`. No files were staged, committed or pushed.
The temporary incoming handoff was read and deleted as requested; no copy was made.

## Limits and next package

This is a completed contract/source-binding preparation package, not an automatic
recognizer, aligner, faithful glyph extractor, font generator or end-to-end product
proof. No DB schema, API operation, worker claim, candidate-publication rule, editor
or export changed. No browser interaction changed or new browser acceptance was
performed. All prior responsive/gentle/account/physical-device gates remain open.
No training, fitting, inference, real evaluation, new data intake, frozen font rebuild,
provider action or public release occurred.

This v1 deliberately excludes photo masks, resized/warped coordinate frames,
legacy/edited sources, missing-letter synthesis and learned typography. Bounds
describe centerline evidence, not crop masks or a promise of uncut brush extents.
Complete point accounting is not proof of reliable character alignment; connected
segment ownership, joining and geometry still need independent evidence. A private
measurement hash or component name alone does not qualify a method.

Next implement explicit versioned admission, both worker claim paths, isolated
adapter invocation/checkpoint dependencies and fenced publication into existing
pending glyph review. Test ownership, cancellation/retries, edits, approval and
immutable exports with inert outputs. A real bounded print word/sentence proof
follows only after a suitable method/input package exists for owner execution.
The saved isolated-letter pilot and manual private font do not supply that method.
