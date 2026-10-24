# PenForge architecture review — 2026-10-24

Status: **initial investigation retained; implementation and verification now recorded
in [results](results.md)**. The owner clarified that actual simplification was requested,
then explicitly requested the remaining agent-facing guide and handoff closeout.

All three candidates below were implemented sequentially, together with architecture
guard repairs and observed studio bugs. Candidate discussions and baseline counts are
historical investigation evidence, not pending selection/approval instructions. Use
the [codebase map](../../CODEBASE_MAP.md) for current entry points and shared seams.

## Scope and evidence limits

- Mode: `ORIENT`, depth: `working`. Broad runtime map, then a focused browser
  persistence slice. This is not an exhaustive security, ML or deployment audit.
- Revision: **unborn local `main`**. `git log -3 --format='%h %s'` reports no commits;
  the implementation is untracked. No reliable base/head diff or history-derived
  hotspot analysis exists. Current source, symbols and hashes are the evidence.
- Read applicable root instructions, current status, domain decisions, manifests,
  composition roots, browser transport and persistence code, and representative tests.
  No nested `AGENTS.md` was found by the repository file search.
- Existing local work, private research, fonts and models are outside the proposed
  edits. No training, inference, evaluation, font rebuild, dependency installation,
  account mutation, provider work, commit or publication was performed.
- The traces below are supported by source inspection, not a new end-to-end account
  session. Earlier browser, hosted, production and handwriting-quality gates remain.

## Runtime map

| Module | Responsibility and state | Main interface / dependencies |
| --- | --- | --- |
| Browser experience | Next.js routes, capture/review and Writing Desk; React view state | `src/app`, `AccountStudio`, `WritingDesk`; GSAP, React, locally served fonts |
| Browser persistence | Account workspace/document mapping, optimistic saves and per-operation recovery; explicit demo store | `account-project-service`, `account-workspace`, `account-documents`, `mock-services`; fetch and browser storage adapters |
| Shared contracts | Python schema source, generated TypeScript, canonical geometry/hashes, pinned Unicode | `packages/contracts`; generated output is not hand-edited |
| HTTP application | Verified identity, owner scope, row versions, transactions and job admission | `apps/api/app/main.py`; FastAPI routers → domain modules → PostgreSQL repositories |
| Durable processing | Queue claims, leases, checkpoints, bounded decoding/extraction/compilation and fenced publication | `services/worker`; PostgreSQL, private object storage and isolated child processes |
| Font and document rendering | Independent font compiler/validators; browser SVG/PNG stationery renderer | `penforge_font_engine`, `src/lib/writing.ts`; real font bytes, FontTools/OTS/HarfBuzz/FreeType |

Research tooling is a separate, owner-executed workflow. It is not a request-time
dependency of the HTTP application. Firebase identity does not authorize migration
of local demo handwriting. The current local object-storage adapter does not establish
that the hosted topology is available.

## Representative source traces

### Account edit to durable workspace

1. `CreateExperience` in [create/page.tsx](../../../src/app/create/page.tsx) loads the
   selected mode and account project. `update` schedules `WorkspaceSaver` for account
   projects; the explicit demo path uses `projectService`.
2. `WorkspaceSaver` in [account-project-service.ts](../../../src/lib/account-project-service.ts)
   preserves an owner/operation recovery record and the base version. Conflict
   reconciliation belongs here, not in a generic HTTP implementation.
3. `createWorkspaceClient` in [account-workspace.ts](../../../src/lib/account-workspace.ts)
   maps to snake_case, obtains the supplied identity-fenced token and sends PUT with
   `If-Match`. It parses allowlisted failures and the current server version.
4. [Workspace routes](../../../apps/api/app/modules/workspaces/router.py) resolve the
   principal and call `WorkspacesService`. Its [implementation](../../../apps/api/app/modules/workspaces/service.py)
   checks ownership and uses repository compare-and-set in a transaction. Missing
   working state is 404; initial creation requires `"v0"`.

### Approved glyphs to font bytes and a document

1. `CloudReviewPanel` and the glyph module keep proposed geometry separate from
   approval. `GlyphsService.freeze_in_transaction` pins revision identities in a snapshot.
2. `CloudExportPanel` → `createExportClient.create` → `ExportsService.create` validates
   inputs, freezes the build manifest, reserves capacity and records a durable job.
3. `ExportWorker.execute` calls `compile_isolated`, retaining lease/checkpoint and
   publication checks; the independent font engine runs the required validators.
4. `createExportClient.font` obtains a private capability, checks its origin/path,
   format, size and SHA-256, and returns verified bytes. It does not forward the
   identity token to the capability download.
5. `WritingDesk` loads actual bytes; `writing.ts` supplies shared preview/export
   rendering. Imported device fonts and generated account exports remain distinct.

The completed private W03 font proof used the standalone compiler and local file
import. It did not enter the account photo-review pipeline. That product gap is
documented in the existing checkpoint (`../../../research/PAGE_TO_FONT_CHECKPOINT.md`; local record).

## Candidate 1 — deepen authenticated JSON handling

**Strength: Strong. Dependency category: local-substitutable.**

- Files: `src/lib/account-workspace.ts:createWorkspaceClient` (around line 518),
  `src/lib/account-documents.ts:createDocumentsClient` (around line 93),
  `packages/contracts/src/client.ts:createFoundationClient` (origin validation),
  `tests/account-persistence.test.mjs`.
- Problem: the two account modules repeat token validation, private fetch options,
  JSON serialization, error-envelope parsing, allowlisting and conflict-version extraction.
  Understanding the shared policy requires reading both implementations.
- Direction: put that common implementation behind one internal module; keep each
  domain interface and its mapping/error adapter. The exact interface is intentionally
  deferred until candidate selection.
- Depth/locality: concentrate common behavior and its tests in one module; existing
  callers keep their domain interface. Leverage comes from changing policy once for
  both consumers, rather than adding a pass-through helper to every file.
- Deletion test: removing the proposed common module would reproduce request policy
  in both consumers. It earns its seam only if that duplication actually disappears.
- Existing real seam: browser fetch and injected fake fetch are two adapters already
  used by callers/tests. No speculative transport plugin system is needed.
- Risks to preserve: each domain's error class and allowlist, token-provider failures,
  `AbortError`, workspace 404 → null, document DELETE 204, exact conditional/idempotency
  headers, unknown error-body privacy and malformed-success behavior.
- Tests: `account-persistence.test.mjs` mostly exercises save/recovery logic and stubs
  the workspace module for those cases. Add public-client characterization cases before
  moving request logic; retain recovery tests as independent caller-level evidence.
- Scope exclusion: identity claims, projects, capture binaries, export capabilities
  and job event streams have different policies. Do not migrate them in this first slice.
- ADR compatibility: retains ADR 0003's explicit wire mapping and ADR 0006's conditional
  writes/recovery semantics. It does not merge server modules or alter contract versions.

## Candidate 2 — deepen the TypeScript test loader

**Strength: Worth exploring. Dependency category: in-process.**

- Files: `tests/account-persistence.test.mjs:transpileUrl`,
  `tests/export-client.test.mjs:compiled`, `tests/extraction-client.test.mjs:url`,
  `tests/capture-service.test.mjs:transpileUrl`, `tests/api-boundary.test.mjs`,
  `tests/helpers/unicode.mjs`.
- Problem: test files repeat TypeScript compilation, string import replacement and
  data-URL construction; each test knows how to assemble part of the source graph.
- Direction: one test-only loader module owns compilation and explicit import adapters.
  Preserve individual fixtures and assertions; do not introduce a new runner or dependency.
- Depth/locality: loader knowledge moves out of each test file. Leverage is shared
  loading behavior across multiple suites through one small interface.
- Deletion test: removing the loader should bring compiler/import mechanics back into
  several suites. A mere renamed `transpileModule` call is too shallow to justify it.
- Seam/adapters: source modules and explicit inert substitutes already vary. Preserve
  module identity, import replacement order and the deliberate substitutes; do not
  silently load real auth, React, backend or research execution into a test.
- Risk: broad test rewrites could conceal changed coverage or alter module caching.
  Keep assertions byte-identical where possible and verify discovery/counts per suite.
- Trade-off: this improves verification locality without simplifying runtime policy.
  It can follow Candidate 1 if new module imports make existing test assembly cumbersome.

## Candidate 3 — deepen identity-fenced token acquisition

**Strength: Worth exploring; requires a narrower lifecycle audit before design.**
**Dependency category: ports & adapters.**

- Files: `src/lib/use-account-backend.ts:open`,
  `src/lib/account-project-service.ts:createAccountProjectService`,
  `src/lib/cloud-project-service.ts:createCloudProjectService`,
  `src/components/writing/WritingDesk.tsx` document/export loaders,
  `src/components/creation/CaptureCloudPanel.tsx` capture actions.
- Problem: the same check-before/after-token pattern is repeated in factories and
  view code. Each call site must remember the owner captured by that request.
- Direction: centralize token acquisition fencing, while keeping request-generation,
  unmount cancellation, subscription and domain-error adapters explicit where they differ.
- Depth/locality: the identity check gains one implementation and focused race tests;
  leverage comes from several request producers crossing that seam consistently.
- Deletion test: token race prevention would be reimplemented in multiple consumers.
  Do not turn the whole account lifecycle into one broad hook to hide unrelated state.
- Seam/adapters: real `IdentityService` and deterministic fake identity/token providers
  vary already. Reuse them rather than creating an auth framework.
- Risk: this is an authorization-sensitive seam. Error classes currently differ;
  preserving the check on both sides of the await is essential. It is not the first
  recommendation merely because many similar lines exist.

## Evidence ledger

| Claim | Status | Evidence / limitation |
| --- | --- | --- |
| Workspace/document request mechanics are repeated | SOURCE | Two factory implementations above; domain mappings and 204/404 behavior differ |
| Test import mechanics are repeated | SOURCE | `rg -n 'transpileModule' tests` and named loader functions |
| Identity fencing is repeated | SOURCE | Named factories and `WritingDesk` token providers; this review does not prove every identity race |
| HTTP app stays separate from ML/font execution | SOURCE + RUNTIME | Composition roots and fresh `scripts/check_boundaries.py`; AST checks are narrower than runtime import proof |
| Initial frontend regression suite passed | BASELINE RUNTIME | `npm test`: 129 passed, 0 failed/skipped/cancelled; final suite has 157 passing tests |
| TypeScript, lint and generated-contract drift checks pass | RUNTIME | Fresh commands below, each exit 0 |
| Frequently changed areas established by Git history | UNKNOWN | Unborn `main`, no commits |
| Supported account import for the W03 photo exists | CONTRADICTED | Checkpoint and current source registration constraints; local font import does not supply it |
| No-config creation always fails closed into unavailable UI | CONTRADICTED at source | `CreateExperience` currently selects demo when configuration is absent; changing this is a product behavior decision, outside a behavior-preserving refactor |
| Shared policies preserved characterized behavior | RUNTIME, SCOPED | 21 public-client cases passed before and after moving request logic; all 129 original test declarations retained; 157 final frontend tests pass. See results for intentional bug repairs and limits. |

## Initial verification and execution record

Executed on 2026-10-24, all exit 0:

```sh
npm test
npm run typecheck
npm run lint
npm run contracts:check
.venv/bin/python scripts/check_boundaries.py
```

The initial frontend suite reported **129 passed**, no failures or skips. Those
read-only baseline checks preceded implementation. The owner's clarification authorized
the bounded execution recorded in [the plan](refactor-plan.md),
[manifest](refactor-manifest.json) and [append-only log](refactor-log.md).
Characterization preceded the request-policy move; shared test loading preserved the
original declarations. Later production build and browser checks are in [results](results.md).
No model evaluation or hosted test was executed as part of this pass.

The archived execution workflow's blanket clean-checkout/commit/revert examples cannot
be applied literally to this uncommitted workspace. Preserve owner work, inventory and
back up the exact selected files before edits, and restore only the agent's own changes.
Do not use `git checkout .`, broad staging, stash, force push or an implicit initial
repository-wide commit as a cleanup mechanism. No publication is authorized.

## Reading route

1. Root instructions (`../../../AGENTS.md`; local record): authorization and immutable-artifact rules.
2. Current status (`../../../PROJECT_STATUS.md`; local record): implemented versus experimental paths.
3. [Wire/geometry ADR](../../decisions/0003-canonical-contract-boundary.md): explicit mappings.
4. [Persistence ADR](../../decisions/0006-phase-02-cloud-projects.md): conditional writes and recovery.
5. [Shared client](../../../packages/contracts/src/client.ts): origin and auth transport conventions.
6. [Workspace client](../../../src/lib/account-workspace.ts): mapping and domain response behavior.
7. [Document client](../../../src/lib/account-documents.ts): document mapping and distinct save lifecycle.
8. [Account project module](../../../src/lib/account-project-service.ts): save/conflict owner.
9. [Persistence regressions](../../../tests/account-persistence.test.mjs): save/recovery fixtures and current substitutes.
10. [Verified font loader](../../../src/lib/export-client.ts): a distinct binary interface to preserve.

## Requested workflows

Consulted directly, without installing or executing their setup scripts:

- [Investigate codebase](https://github.com/Data-System-School/agent-skills/blob/main/investigate-codebase/SKILL.md)
  and its orientation guide.
- [Improve codebase architecture](https://github.com/mattpocock/skills/blob/main/skills/engineering/improve-codebase-architecture/SKILL.md),
  its report guide and shared codebase-design vocabulary. Its requested parallel
  exploration was superseded by the owner's explicit sequential/no-subagent rule.
- [Architectural refactor](https://github.com/petekp/agent-skills/blob/main/skills/architectural-refactor/SKILL.md):
  reviewed as an archived reference; plan approval precedes execution.
- [Code simplifier definition](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/code-simplifier/agents/code-simplifier.md):
  behavior-preserving, touched-scope guidance; not installed as a Codex agent.
- [Verification before completion](https://github.com/obra/superpowers/blob/main/skills/verification-before-completion/SKILL.md):
  fresh command output required before claims.

The initial assessment stopped at candidate selection. The owner subsequently clarified
the request to execute the refactor; that instruction governs the completed plan.
No external workflow adds another approval gate to already authorized local work.
