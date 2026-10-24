# Font engine boundary

Phase 05 compiles approved canonical snapshots into deterministic quadratic TTF, CFF
OTF and Brotli WOFF2. A frozen manifest fixes metadata, metrics, tolerance and toolchain;
request IDs and wall-clock time never enter font bytes. Missing mappings stay absent.

All five layers are mandatory: structural/checksum/metrics, OTS, the curated FontBakery
profile, HarfBuzz and FreeType render smoke. The isolated child has network denial and
resource limits. The engine imports no API, queue, DB, storage or ML frameworks.

See [ADR 0011](../../docs/decisions/0011-phase-05-reproducible-builds.md),
[validation policy](../../docs/backend-ml-infra/phase-05-font-export/validation-profile.md),
[local run instructions](../../docs/backend-ml-infra/phase-05-font-export/development.md)
and [actual evidence](../../docs/backend-ml-infra/phase-05-font-export/evidence.md).
Synthetic fixture success establishes engineering behavior, not handwriting quality.
