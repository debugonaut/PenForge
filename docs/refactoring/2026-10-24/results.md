# PenForge simplification results — 2026-10-24

The focused refactor makes shared policies and task entry points easier to locate.
The [codebase map](../../CODEBASE_MAP.md) is the current navigation guide. This was
a bounded architecture/persistence audit, not an exhaustive audit of every module,
a model qualification, or a production-readiness review.

## What changed for the next agent

| Before | Now | Where to read |
| --- | --- | --- |
| Repeated workspace/document request mechanics | One shared request policy; domain mappings and response behavior remain explicit | [account-request](../../../src/lib/account-request.ts), [workspace](../../../src/lib/account-workspace.ts), [documents](../../../src/lib/account-documents.ts) |
| Seven copies of owner checks around token acquisition | One check-before/after-await implementation; caller lifecycle fences remain | [owner-token](../../../src/lib/owner-token.ts) |
| Repeated compiler/data-URL setup in 11 suites and the Unicode helper | One test loader with explicit ordered substitutions | [test loader](../../../tests/helpers/typescript.mjs) |
| Import guard missed repository/relative/parent-package forms | Resolved import forms plus positive/negative executable fixtures | [checker](../../../scripts/check_boundaries.py), [regressions](../../../tests/backend/test_boundary_checker.py) |
| A 1,222-line current handoff mixed live and superseded instructions | A 121-line handoff at initial closeout, dated history retained separately, task-to-file map linked from root instructions/README; later owner instructions append to the current handoff | `PROJECT_STATUS.md`, [map](../../CODEBASE_MAP.md) |
| Assessment still requested candidate selection; README/phase index had stale implementation summaries | Current guidance points to implementation/results and existing account/Phase 11 evidence | [assessment](assessment.md), [README](../../../README.md), [phase index](../../backend-ml-infra/plan.md) |

The handoff archive preserves all original content except rebased relative links;
the transformation round-trips to the exact preimage. It remains ignored like the
original local handoff. No broad folder moves, dependencies, framework changes or
public publication were introduced. Agent reading time/token savings were not measured;
reduced duplication and the smaller starting document are observable structural changes.

## Browser audit repairs

Three concrete studio defects were corrected in the existing create route/CSS:

1. An unconfigured account screen claimed “Saved to your account” before a project
   existed. Header/footer now use the same current-owner/session/save-state message.
2. Account header controls forced a 421px page width at 390/320px. Scoped compact
   styling permits status wrapping and treats the mode tag like the existing demo tag.
3. “Try the local demo” changed the URL while retaining the account screen. Mode now
   derives from the URL/session, and a previous-mode project is hidden during loading.
   The previous default for a missing mode is retained as a separate policy finding.

The rest of the refactor preserves characterized behavior: domain error types and
allowlists, exact request headers, abort/network distinctions, workspace 404/null,
document DELETE/204, owner races, conditional saves and recovery semantics.

## Executed verification

Evidence is retained locally in ignored `artifacts/refactoring-20261024/`. Log names
below identify reproducible checks; their absence from a public checkout is expected.

| Check | Observed result | Local evidence |
| --- | --- | --- |
| Client characterization before/after extraction | 21 public-client cases passed on both implementations | `chunk-02-*.txt` |
| Original test preservation | All 129 original test declarations unchanged after AST printing, across 14 suites | `test-declarations-final.json` |
| Final frontend suite | 157 passed; zero failures, skips or cancellations | `final-tests.txt` |
| TypeScript, ESLint, generated-contract drift | All exit 0; generated contracts unchanged | `final-typecheck.txt`, `final-lint.txt`, `final-contracts.txt` |
| Final production build/start | Next.js build succeeded with existing CSS/webpack cache warnings; rebuilt app served on loopback port 3025 | `final-build.txt`; browser captures |
| Guard regression before repair | 15 failures in 34 cases exposed actual old bypasses | `chunk-04-regression-before.txt` |
| Guard/foundation after repair | 36 guard cases; 54 combined tests passed, one existing Starlette/httpx deprecation warning | `chunk-04-final-tests.txt` |
| Python static/architecture checks | Ruff check/format, focused mypy and actual boundary scan passed | `chunk-04-final-*.txt`, `chunk-04-guard.txt` |
| Production browser navigation | Keyboard link, mobile click, Back, Forward and demo refresh passed; no captured console errors/warnings | `navigation-final-browser.json`, `mode-*-after.txt` |
| Account and demo layout | No horizontal overflow at 1440×900, 768×1000, 390×844, 320×844 or 1280×600; no false account-save claim | `navigation-final-browser.json`, `account-final-*.png`, `demo-final-*.png` |
| Real Writing Desk path | Existing W03 WOFF2 imported, rendered at five widths, keyboard Note→Letter preserved words, downloaded SVG embedded identical font bytes | `browser-responsive.json`, `keyboard-format-proof.json`, `export-proof.json`, `production-letter.svg` |

Documentation checking passed **996 local links across 128 files** and both preserved
specification hashes. The source inventory checked **397 existing files**: 23 changed
code files, six added code files, zero missing files; all five protected private font
files are unchanged. API application, worker, contracts and font-engine code remain
unchanged. **269 Python source files parsed without execution.** The archive receipt
verifies exact content restoration. Private font binaries/verification remain frozen; importing them for the
browser proof did not rebuild or reevaluate a model. The original port 3024 Writing
Desk session was preserved; checks used a separate origin/tab and local demo records.

## Remaining findings and limits

- **TODO next session, deferred by the owner:** audit and remove non-essential code,
  including dead/unused code, obsolete components/implementations, unused dependencies,
  unnecessary abstractions and excessive fragmentation. The owner clarified that this
  broader implementation cleanup was intended. The focused pass is complete; the
  broader codebase cleanup remains incomplete. No further cleanup is being started now.
- A missing create-mode parameter still chooses demo when configuration is absent.
  Aligning that default with the explicit-demo production direction requires a scoped
  product-policy change; explicit account mode now reports unavailable truthfully.
- A supported reviewed-photo-box account import remains unimplemented. The completed
  private font/file-import proof does not establish that workflow or automatic extraction.
- This pass did not exercise a configured live account/database, hosted providers,
  physical touch hardware or the OS-gentle browser variant. Browser tooling exposed
  viewport controls but no media emulation; no OS preference was changed. Earlier
  Phase 03–09 browser/hosted/research gates remain exactly as recorded in phase evidence.
- No training, fitting, fine-tuning, real model inference/evaluation, font recompilation,
  account-data mutation, provider execution, commit, push or deployment occurred.
- The checkout has no commits; scoped preimages/hashes supply the comparison base.
  They are not a release diff. Private artifacts and publication/licensing decisions
  still require the existing separate authorization and audit.

The [plan](refactor-plan.md), [manifest](refactor-manifest.json) and
[append-only log](refactor-log.md) retain execution order and evidence. Future agents
should start with current instructions/status and the map, then follow only the
relevant phase/evidence path. Preserve completed research and fonts; take the next
owner-requested package without reconstructing or repeating the old runs.
