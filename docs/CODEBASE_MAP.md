# PenForge codebase map

Use this map to choose a small reading path before searching the whole workspace.
It describes source ownership; dated results belong in the linked evidence files.
Local agents must first follow the supplied root instructions and current handoff.

## Find the right starting point

| Task | Read first | Then follow |
| --- | --- | --- |
| Landing or motion | [LandingPage](../src/components/landing/LandingPage.tsx), [design language](design-language.md), [motion contract](motion-design.md) | Component CSS, [film](../src/components/landing/HandwritingFilm.tsx) and [process demo](../src/components/landing/HowItWorksDemo.tsx) own their scoped timelines. Unused earlier landing/motion modules were removed during v1 preparation. |
| FAQs, footer or site links | [LandingPage](../src/components/landing/LandingPage.tsx), [SiteFooter](../src/components/shared/SiteFooter.tsx) | The [shadcn/Radix accordion](../src/components/ui/accordion.tsx) owns keyboard/ARIA composition and [content motion](../src/components/ui/accordion.module.css); landing CSS supplies its paper theme. The shared wordmark/link/arrow layout lives under `.pf-footer` in [globals.css](../src/app/globals.css). Keep real hash links and landing/regular-page focus recovery. |
| Studio navigation or demo flow | [create route](../src/app/create/page.tsx), [creation state](../src/components/creation/creation-state.ts) | The matching panel in `src/components/creation/`; [mock services](../src/lib/mock-services.ts) only for demo work. |
| Account project saves or recovery | [account project service](../src/lib/account-project-service.ts) | [workspace mapping/client](../src/lib/account-workspace.ts), then the server's [workspace service](../apps/api/app/modules/workspaces/service.py). |
| Identity or switching accounts | [session hook](../src/lib/use-account-session.ts), [auth service](../src/lib/auth-service.ts) | [owner token provider](../src/lib/owner-token.ts), [backend hook](../src/lib/use-account-backend.ts); keep caller lifecycle fences. |
| Writing Desk or document export | [WritingDesk](../src/components/writing/WritingDesk.tsx), [writing renderer](../src/lib/writing.ts) | [desk helpers](../src/lib/writing-desk.ts) (templates, sign-offs, coverage, page filters), [document persistence](../src/lib/account-documents.ts), [generated-font client](../src/lib/export-client.ts). |
| Capture, extraction or glyph approval | [AccountStudio](../src/components/creation/AccountStudio.tsx) | Capture/review panels → domain clients → matching API module → [analysis worker](../services/worker/analysis.py). |
| Live-canvas shape fidelity | [canvas evidence](canvas-font/evidence.md), [DrawingCanvas](../src/components/creation/DrawingCanvas.tsx) | [Shared brush/capture](../src/lib/stroke-capture.ts) → explicit analysis v2 → [round-stroke geometry](../packages/extraction/round_strokes.py). Keep v1 raster conversion and approved snapshots immutable. |
| Freeform words/sentences | [gap and sequence](freeform/plan.md), [evidence](freeform/evidence.md) | [Independent evidence contracts](../packages/contracts/penforge_contracts/freeform.py) → [source binding/adapter](../packages/ml_core/freeform.py). Preparation only; no model/default adapter, job version or production route. Existing analysis admission/publication still needs a versioned extension. |
| Font compilation or validation | [font-engine README](../packages/font-engine/README.md), [export worker](../services/worker/exports.py) | Independent [compiler](../packages/font-engine/penforge_font_engine/compiler.py) and [validators](../packages/font-engine/penforge_font_engine/validation.py). |
| FontBakery execution boundary | [Fixed-profile child](../packages/font-engine/penforge_font_engine/_fontbakery_child.py), [subprocess controls](../tests/backend/test_fontbakery_child.py) | Validators launch an isolated Python child with the fixed four-check profile; pip/pip_api imports fail closed. Declared dependencies remain installed. This is a call-path guard, not an OS sandbox or a vulnerability exemption. |
| API contract or wire field | [contract ADR](decisions/0003-canonical-contract-boundary.md), [contracts README](../packages/contracts/README.md) | Python contract/API schemas and exporters; regenerate derived TypeScript rather than editing it. |
| Queue, quotas, privacy or infrastructure | [phase index](backend-ml-infra/plan.md) | Relevant phase plan/evidence, then domain service and worker implementation. Read the complete v3 spec before backend/ML/infra changes. |
| Minimal Linux runtime packaging | [Builder-only assembler](../infrastructure/docker/assemble_runtime.py), [filesystem controls](../tests/backend/test_runtime_assembly.py) | API/worker Dockerfiles retain the locked builder and assemble a scratch runtime with native dependencies, full venv and license/package metadata. Actual image smoke, inventory and scans remain independent acceptance gates. |
| Image security evidence | [Exact-image gate](../scripts/image_security_gate.py), [reviewed dispositions](security/image-security-dispositions.md) | Preserve raw scans; match image, copied-file manifest, complete inventory and offline smoke. Unknown findings, ambiguous origins or stale/incomplete evidence remain blocking. |
| Public release inventory | [v1 preparation](releases/v1-preparation.md) | [Read-only auditor](../scripts/audit_publication.py), `.gitignore`, MIT/third-party notices and public-check targets in `Makefile`. Never infer approval from an automatically passing inventory. |
| Existing research or private font evidence | Current local handoff | Follow its specific research document; never start a completed run again to reconstruct context. |
| Experimental source-edge polish | polish evidence (`../research/PAGE_TO_FONT_POLISH.md`; local record), [proposal module](../research/font_polish.py) | [Focused tests](../tests/backend/test_font_polish.py); separate from production extraction, approval and compilation. |

Before any frontend change, read the complete [original specification](../document.md),
[design language](design-language.md) and [motion contract](motion-design.md).
For backend/ML/infra changes, read the complete [v3 specification](specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md)
and the relevant phase plan. The map does not replace those requirements.

## Runtime boundaries

```text
src/app + src/components            Next.js UI, root frontend (no apps/web)
  ├─ explicit demo → mock-services  browser-local, simulated font project flow
  └─ account service/client         explicit wire mapping, owner-scoped recovery
       ↓ HTTP
apps/api/app/main.py                FastAPI composition root
  → modules/<domain>/router.py      authentication and request boundary
  → modules/<domain>/service.py     ownership, transactions and domain policy
  → modules/<domain>/repository.py  PostgreSQL access
       ↓ durable job + private asset references
services/worker/runtime.py          claims, leases and processing orchestration
  → capture_safety / extraction     bounded isolated processing
  → penforge_font_engine           independent compilation and validation
```

Cross-domain API calls use the other module's public service. Routers do not import
repositories directly. [The boundary checker](../scripts/check_boundaries.py)
enforces these import rules, including relative imports; it is an AST guard, not a
complete runtime/security proof. [Its regressions](../tests/backend/test_boundary_checker.py)
run on synthetic source trees without importing models or the application.

| Shared seam | Owns | Caller still owns |
| --- | --- | --- |
| [account-request.ts](../src/lib/account-request.ts) | Strict origin/token validation, private JSON fetch options and bounded error parsing for workspace/documents | Domain mappings, error class/allowlist, successful response parsing and save/recovery policy |
| [owner-token.ts](../src/lib/owner-token.ts) | Captured-owner check before and after awaiting a token | Session lifecycle, cleanup, generation fences and domain error choice |
| [test loader](../tests/helpers/typescript.mjs) | Existing TypeScript compilation, data URLs and ordered import substitutions | Fixtures, explicit inert adapters and assertions; special JSX/CommonJS harnesses stay local |

Binary capture/export transports and job event streams have different policies;
they intentionally retain their dedicated clients.

## Trace one operation

**Account edit:** create route → `account-project-service` saver → owner/operation
recovery record → `account-workspace` mapping → `account-request` transport → workspace
router/service → repository compare-and-set. Acknowledged versions determine saved
status; local recovery is not account persistence.

**Real font:** reviewed canonical revisions → owner approval → immutable snapshot/build
manifest → export job → isolated compiler/validators → private bytes → `export-client`
origin/size/hash verification → Writing Desk FontFace → shared SVG/PNG renderer.
Workers propose artifacts; they never silently approve or activate a glyph.

## Sources of truth and common traps

- [Phase evidence](backend-ml-infra/plan.md) and [account integration evidence](backend-ml-infra/integration/account-persistence.md)
  record what actually ran. Plans describe intent; older dated evidence is not a fresh check.
- [Decision records](decisions/) explain settled boundaries. The later FINAL decisions
  in the v3 specification refine its earlier baseline; proposed phase reconciliations
  remain proposals until implemented/resolved.
- `src/lib/contracts.ts` contains frontend view models; `packages/contracts` contains
  canonical/wire contracts. Do not cast server DTOs into demo project types.
- Account integration exists locally. Identity alone never uploads local handwriting;
  importing existing work requires explicit action. Missing account configuration must
  never be described as a successful account save.
- Demo project previews use Caveat. Device font import, verified generated font bytes
  and Writing Desk PNG/SVG exports are real, separate paths.
- Local structured capture and manually reviewed private fonts do not establish automatic
  ordinary-photo extraction, model qualification, cursive joins or hosted readiness.
- The no-mode create route now chooses account mode. Demo is explicit.
- [Service accountability](accountability/evidence.md) owns versioned policy/access receipts,
  verified-account admission and private reports. Creation and raw file transfers use
  its API guard; identity recovery and deletion retain their original dependencies.
  `use-service-access` and `ServiceAccessPanel` present account setup; receipts never
  come from browser storage. Policy text lives in `packages/contracts/policies/service-use.v1.json`.
- Historical research scripts are not startup dependencies. Agents must not launch
  training/inference/evaluation to inspect existing results. Preserve frozen artifacts.

## Search and verification

Start inside the owning directory, then widen only when the dependency trace requires it:

```sh
rg --files src/lib src/components/creation
rg -n 'createWorkspaceClient|WorkspaceSaver' src tests
rg -n 'class WorkspacesService' apps/api/app/modules/workspaces
```

Skip `node_modules`, `.next*`, `.venv`, caches, `artifacts` and `research/runs` during
initial architecture reading. Evidence directories contain results/preimages, not a
second source tree. Do not execute files there. Use exact evidence paths when a task
requires them. Keep private data, checkpoints and local environment files out of publication.

| Changed area | Relevant checks |
| --- | --- |
| Frontend logic | `npm test`, `npm run typecheck`, `npm run lint`, `npm run contracts:check` |
| Running application | `npm run build`, then `npm start`; inspect affected interactions at 1440/768/390/320px and a short viewport, including keyboard and applicable motion modes |
| Architecture guard | `.venv/bin/python -m pytest tests/backend/test_boundary_checker.py -q` and `.venv/bin/python scripts/check_boundaries.py` |
| Python module | Focused tests, Ruff check/format and mypy; use the phase's environment/setup instructions |
| Documentation | `.venv/bin/python scripts/check_docs.py`; inspect source consistency and preserve specification hashes |

Use existing lockfiles/runtimes. Database/emulator suites need their documented local
services; a skipped hosted/browser test is not a pass. Training and real model evaluation
remain owner-run. Broader `make` targets include research tooling; choose checks by scope.

The [2026-10-24 refactor results](refactoring/2026-10-24/results.md) record this map's
initial verification, implementation changes and unresolved findings. Update this map
when ownership or entry points change; keep run counts and chronological logs there.
