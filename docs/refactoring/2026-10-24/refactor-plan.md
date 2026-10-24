# PenForge simplification plan — 2026-10-24

Authorization: the owner clarified that the supplied workflows are instructions to
“refactor and audit our codebase to simplify it all.” Proceed sequentially with
bounded, behavior-preserving changes rather than stopping at candidate selection.
The [assessment](assessment.md) supplies the source map and invariants. This plan
records the first verified pass; it does not authorize product changes or research.

## Design and scope

Keep the existing routes, wire contracts, owner-scoped persistence, domain errors,
generated-font verification and accepted UI. Reduce repeated policy at three existing
seams: test module loading, account JSON requests and owner-fenced token acquisition.
Audit the dependency guard while mapping imports; repair a concrete enforcement gap
with a negative regression. Avoid new dependencies, broad folder moves or new frameworks.

The checkout has no commits. Preserve scoped preimages and a source-hash inventory in
ignored `artifacts/refactoring-20261024/`; never reset/stash the owner's work. Record
each chunk in the manifest/log. No Git commit or push is part of this pass.

## Chunk 1 — consolidate test loading

Entry: baseline 129 frontend tests, TypeScript, ESLint and contract drift pass.

1. Add `tests/helpers/typescript.mjs` for fixed ES2022/ESM compilation, data URLs and
   explicit ordered import substitutions, using the existing TypeScript dependency.
2. Replace duplicated loaders in account-persistence, api-boundary, auth-service,
   capture-service, export-client, extraction-client, password-policy, stroke-capture,
   mock-services, job-client and writing tests, plus the Unicode helper.
3. Retain special JSX/CommonJS compilation, inert substitutes and test assertions.

Exit: unchanged 129-test discovery/results; typecheck/lint; inspect scoped diff.

## Chunk 2 — share account JSON request policy

Depends on Chunk 1. Entry: Chunk 1 verified.

1. Add public-client characterization tests for workspace and document factories;
   run against the original implementations before restructuring.
2. Add `src/lib/account-request.ts:createAccountRequest`: strict origin validation,
   token validation, private fetch options, serialization and bounded error-code
   interpretation behind one request function. Domain error factories/allowlists
   remain explicit; no retry, identity migration or recovery policy moves here.
3. Replace duplicate implementations in `account-workspace.ts` and
   `account-documents.ts`; preserve public methods/mappings, workspace 404/null,
   document DELETE/204 and malformed-success behavior.
4. Add the new test file to the explicit `npm test` list.

Exit: characterization and full frontend suite pass; typecheck/lint/contracts;
no export/capture binary or job-event transport migration.

## Chunk 3 — share owner-fenced token acquisition

Depends on Chunk 2. Entry: Chunk 2 verified.

1. Add `src/lib/owner-token.ts:createOwnerTokenProvider` using only the identity
   snapshot/token methods and an explicit domain-error factory. Check the captured
   UID before and after awaiting token acquisition; never subscribe or reconnect here.
2. Use it in `createCloudProjectService`, `createAccountProjectService`,
   `useAccountBackend`, and the matching WritingDesk/CaptureCloudPanel callbacks.
3. Preserve generation fences, cleanup, current error types and the late-result guards.
4. Add focused race tests and retain the existing identity/capture recovery regressions.

Exit: focused races and full frontend suite, typecheck/lint/contracts pass;
inspect all changed token producers and affected running-browser surfaces.

## Chunk 4 — verify the architecture guard itself

Entry: prior chunks verified. This addresses a discovered audit defect, not a new
application rule: routers already must not reach repositories directly.

1. Add `tests/backend/test_boundary_checker.py` using isolated synthetic source trees
   and the actual checker subprocess. Demonstrate that a same-module singular
   `repository` import currently slips past the guard.
2. Correct `scripts/check_boundaries.py` so both singular and plural repository module
   names are checked. Include relative import forms if the reproduction exposes them.
3. Keep the lightweight HTTP/font-engine restrictions and public cross-module service
   rule; check legitimate interfaces still pass.

Exit: negative fixture fails before repair, meaningful positive/negative regressions
pass afterward, actual source passes dependency guard; Ruff/format and relevant backend
tests pass. No application/backend schema or research implementation changes.

## Chunk 5 — repair observed studio state and layout

Added during final browser verification under the owner's audit/repair instruction.
The real unconfigured `/create?mode=account` showed “Saved to your account” in the
header and footer before any project was opened. This contradicts the existing
requirement that saved status follows server acknowledgement.

1. In `src/app/create/page.tsx`, compute one account-save message for both locations.
   Distinguish restoration, unavailable configuration, signed-out, loading/error and
   the current owner's loaded project/save state. Keep persistence and layout intact.
2. Hide stale saved/error chip decoration when there is no current-owner account project.
3. Correct the observed 421px account-header overflow at 390/320px in
   `src/app/create/create.module.css`: let status copy wrap and give the account mode
   tag the existing demo tag's compact-screen treatment; keep navigation/actions usable.
4. Remove the one-time React copy of URL mode: derive mode from current parameters
   and session availability, and hide the previous mode's loaded project while loading.
   The real “Try the local demo” link previously changed the URL while leaving the
   account screen visible. Preserve the existing default policy for a missing mode.
5. Rebuild and reproduce the unconfigured state in the running production browser;
   verify neither location claims a successful save. Repeat affected desktop/mobile
   checks, keyboard activation, Back/Forward and refresh; run final frontend checks.

Exit: false-success status, narrow-header overflow and stale navigation mode are gone;
current-owner save/recovery semantics and earlier refactors still pass their checks.
These are the intentional product behavior corrections in this pass.

## Chunk 6 — make agent continuation concise

The owner explicitly requested this closeout after confirming that agent navigation
and context reduction are the primary aim.

1. Add `docs/CODEBASE_MAP.md` with task-to-file entry points, data flow, shared seam
   ownership, sources of truth, search scope and appropriate checks. Link from root
   instructions and README without duplicating phase/run history.
2. Replace the 1,222-line current status with a concise checkpoint. Preserve every
   historical byte except rebased relative links in a dated, private archive; verify
   the transformation round-trips to the original. Keep authorizations and pending
   browser/hosted/research gates in the current handoff.
3. Correct stale candidate-selection and “not started” guidance in current navigation
   documents; retain dated investigation and phase evidence as historical records.
4. Publish local results and finish manifest/log with actual checks and unresolved
   findings. Validate Markdown links, source integrity and the archive receipt.

Exit: current navigation points to the map/results, no pending selection/closeout claim
remains, local links and protected source/artifact hashes pass. No broad documentation
rewrite, source relocation or public publication is included.

## Closeout

- Run fresh frontend tests, typecheck, lint, contract drift, production build and startup.
- Run checker regressions, dependency guard, documentation links and source integrity.
- Inspect affected browser surfaces at 1440, 768, 390 and 320 pixels, short viewport,
  keyboard and ordinary/gentle modes where the tools permit; record actual limits.
- Compare before/after inventory, touched-file diffs and protected private-font hashes.
- Update assessment, manifest, append-only log and `PROJECT_STATUS.md`. Distinguish
  completed refactors from further audit findings; do not claim the whole product is
  qualified or all previous acceptance gates are closed.
