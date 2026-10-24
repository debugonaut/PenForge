# 0001 — Additive foundation and accuracy-first boundaries

Status: accepted. Date: 2026-10-19. Scope: Phase 00 only.

## Context and decision

The user explicitly authorized Phase 00 implementation after the complete existing plans.
The frontend is the root Next.js application, not `apps/web`. Add `apps/api` and the
source's package/runtime boundaries beside it; do not relocate or redesign the frontend.
The workspace still has no Git metadata; these files imply neither a commit nor a backup.

The runnable API surface is `/api/v1/health` and `/api/v1/contracts`. The registry
publishes version metadata; its future schemas are not implemented processing endpoints.
No identity persistence, projects API, queue consumer, extractor, model or font compiler
is introduced. Local fake authentication and private filesystem capabilities are injected
adapters. Preview/production configuration rejects local adapters; no cloud provider is
selected. Readiness is a separate loopback management application requiring a secret,
not a route mounted on the public application.

## Reconciliation matrix

| Sources                                                     | Accepted resolution                                                                                                                | Affected implementation / later gate                                      |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Part B §5 `apps/web`; actual `src/app`                      | Preserve root frontend; additive directories                                                                                       | `apps/api`, root Makefile                                                 |
| Part B §8.10 T0/degradation; Part A §0/8/12 accuracy-first  | A classical method must qualify for its input class. Lack of resources permits queue/defer, never inferior silent output           | Research promotion records now; planner Phase 08                          |
| Part B public cloud foundation; Part A optional self-hosted | Personal hardware is optional advanced capacity; public English foundation cannot depend on it                                     | Phase 11 provider/availability gate, ₹0 unproven                          |
| Part B structured M1; Part A CAL-01–03                      | Guided passages remain preferred; structured fallback is dependable M1 target. Prompt text is context, never observed truth        | Canonical calibration contracts; Phase 04/08 measurement                  |
| Part B §8.12 within-em bounds; Part A IC-01                 | Allow negative descenders and overshoots; provisional safety limits are not quality gates                                          | Outline schema, coordinate fixtures; ADR 0003                             |
| Part B pending revisions; Part A IC-05                      | Recommendation differs from approval. Generated geometry starts pending; approval evidence required                                | DB guards; ADR 0002; candidate APIs later                                 |
| Part B scalar progress; Part A §10                          | Preserve actual stage units, plan revision, changed denominator reason, nullable benchmark ETA                                     | Job schemas and explicit frontend mapping                                 |
| Part B §9.2 conflicting input outcome                       | Assessment may succeed with retake flags; malformed/security-invalid data fails                                                    | Job DTO distinguishes completed processing from user review; worker later |
| Part B ownership in object prefix; claim moves owners       | Key prefixes do not authorize access. Ownership is metadata; future deletion must inventory asset IDs, including historic prefixes | Deferred DB owner consistency; identity/deletion phases                   |
| Part B revision review mutation; Part A immutable content   | Geometry/provenance immutable; pending review metadata may transition; active revision same glyph and approved                     | DB guards; ADR 0002                                                       |
| Part A illustrative fields; existing UI prototype           | Independently version contracts, explicit casing/status/coordinate fixtures; no casting cloud projects into demo types             | ADR 0003                                                                  |

## Deliberately pending

Part B §8.7 embeds request UUID/time while promising reproducible font bytes. The phase
index proposes a content-derived build identity. This remains **proposed** until Phase 05
validates a build manifest and distinct-request reproducibility; Phase 00 generates no fonts.

Q7, undefined Q8–Q10, provider terms, global service identity, model selection, licensing
for new datasets, quality/ranking thresholds, compute limits, safe concurrency, ETA,
optimized prompts, template print geometry and script readiness retain their later gates.
Research can proceed locally through governed metadata without resolving them by assertion.

## Evidence and consequences

Source: [v3 Part A §§0–17 and Part B §§5/11](../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md),
[phase index](../backend-ml-infra/plan.md), [Phase 00](../backend-ml-infra/phase-00-foundations/plan.md).
Audit and measurements: [Phase 00 evidence](../backend-ml-infra/phase-00-foundations/evidence.md).
Python 3.12 is pinned with a lockfile. Docker recipes isolate dependency groups; local
PostgreSQL tests used a temporary bundled PostgreSQL 16.2 because Docker is unavailable.
No public deployment, model quality or font fidelity is established by these checks.
