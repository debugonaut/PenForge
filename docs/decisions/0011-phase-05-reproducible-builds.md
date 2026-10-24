# 0011 — Frozen font builds, independent of export requests

Status: accepted for the explicitly authorized local Phase 05. Date: 2026-10-20.

## Decision

The Phase 05 request resolves the conflict in v3 Part B §8.7: per-request export UUIDs
and wall-clock timestamps must not enter font bytes. A versioned canonical manifest
pins the immutable snapshot digest and creation time, sanitized family/style/license,
UPM, vertical metrics, conversion tolerance, embedding fsType, compiler and validator
versions. Its tagged canonical SHA-256 is the build identity. Name ID 3 uses that
identity; head created/modified use snapshot time. Format is a deterministic projection
of the same manifest. Requests, attempts, owners, capabilities and object locations
remain outside the font and manifest. Rebuilds create new export rows.

Same frozen inputs and toolchain mean identical bytes across requests and retries.
Changing any canonical manifest field changes build identity. A compiler refuses a
different installed toolchain. The API freezes a checked-in toolchain policy without
importing FontTools, validators or the engine. Only approved immutable revision bytes
are passed to the engine; revision hashes are checked again before compilation.

Outlines retain their font-space coordinates, baseline and approved advances/bearings.
Even-odd fill is resolved explicitly into technology-specific winding using PathOps;
zero-area contours are rejected before resolution. Float32 PathOps precision and
TrueType integer quantization are explicit parts of the tolerance budget. Cubic to
quadratic conversion reserves 0.71 font units for integer rounding. There is no
per-glyph rescaling, inferred spacing, overlap thickening, feature synthesis or kerning.
Unsupported scripts/sequences/alternates fail closed at this phase boundary.

All five validator layers are required. A missing, skipped or failing layer fails the
export; partial coverage is a separate warning. Complete English requires all 95 ASCII
characters and successful validation. Synthetic success establishes engineering
behavior only, never handwriting accuracy or production readiness.

## Consequences

New font versions require a new manifest policy and identity. Existing terminal exports
and assets stay immutable. Keys use opaque asset UUIDs without owner IDs, names or
content hashes. Operational logs carry only coded outcomes and resource measurements.
Provider/deployment, privacy/deletion, personalization and Phase 06 remain outside scope.
