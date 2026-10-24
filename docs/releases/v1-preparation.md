# Version 1 preparation

2026-10-24. Original project code uses the owner-selected [MIT License](../../LICENSE).
The visible product version label is simply **Version 1** in the footer. Publication
is held until the owner confirms the concurrent workspace changes are finished.

## Implemented application

| Area | Actual boundary |
| --- | --- |
| Account projects and documents | Typed owner-scoped persistence, explicit imports, recovery and conflict handling. Missing configuration reports unavailable. |
| Capture and review | Verified immutable captures, assigned drawing/template extraction, canonical pending letters, reversible revisions and explicit owner approval. |
| Font export | Approved snapshots compile to validated TTF, CFF OTF and WOFF2; verified bytes can load in Writing Desk. |
| Writing Desk | Device font import and actual PNG/SVG document exports. User words and stationery choices survive format changes. |
| Local demonstration | Explicitly selected browser-local flow; simulated extraction/project previews/export remain separate from real generated font bytes. |
| Natural writing | Evidence contracts/source binding exist. Automatic word/sentence recognition, character alignment and connected-letter separation are not established. |

An initial source release does not establish hosted operation, public-account/legal
readiness, writer fidelity or a qualified handwriting model. Devanagari, joins and
unmeasured generation retain their existing gates. No provider provisioning or model
execution is part of this preparation.

## Cleanup and checks

The active import graph identified three unreachable UI modules: the earlier landing
story, its motion helper and the former cloud workbench. Their four implementation/
stylesheet files were removed (1,913 lines), while the current landing, projects
service and recovery regressions remain. The footer links the official repository
and displays the requested version. Its keyboard and responsive layout were checked
at 1440, 768, 390 and 320 pixels, including a 568-pixel-tall viewport.

The initial footer/FAQ update introduced nine native disclosures
and four groups of real anchors, enlarges the original wordmark and places a circular
52px back-to-top arrow at the lower left. The base retains copyright and Version 1.
Answers now reflect the implemented account-export path and distinguish its actual
capabilities from the local demo and unproven automatic cursive joins.
This update passed typecheck, lint, 179 frontend tests and a separate production
build. Browser checks covered 1440/768/390/320px layouts, all nine FAQs expanded on
mobile/narrow screens, Enter/Space, natural scrolling, section re-entry and real
section/Privacy links. Keyboard back-to-top restored scroll and focus on both the
landing and Privacy page; the incoming FAQ hash also worked. OS-gentle browser checks
were not repeated. No backend or model checks were needed for this UI-only update.

The subsequent component request replaces those disclosures with the official
shadcn/Radix accordion, adapted to CSS modules with its MIT notice retained. It uses
one collapsible answer at a time, keyboard navigation and measured content heights.
The owner's speed refinement sets the height, chevron and open-item feedback to
100ms ease-out with no normal answer fade or delay. The final chapter has no extra
entrance animation. Typecheck, lint, 179 frontend tests and the refined production
build passed; scoped responsive, keyboard, switching and exit checks passed. Gentle
CSS is implemented; its prepared fixture does not close the unexecuted OS/browser
acceptance gate.

The [publication auditor](../../scripts/audit_publication.py) hashes an explicit
candidate inventory, rejects private outputs/writer or sample records and checks
links against the public set. It does not stage, commit, push or grant license rights.
Manual source/asset, credential and exact-diff review remains required. The local
candidate inventory is not a release approval, especially while another agent edits.

Private preparation/results, admitted writer/sample manifests and local research
histories are excluded. Historical evidence paths shown as inline code refer to
private local records; these records are not needed to build the application.
Original specification hashes remain unchanged.

The owner identified the former parchment as third-party material, then requested
its removal. It is replaced by an original procedural SVG with fixed noise seeds,
covered by the project MIT License. The removed image is absent from the public
inventory; its earlier unresolved source/license is no longer a dependency.
The replacement passed a fresh production build, ESLint and 179 frontend tests.
Browser checks covered both paper scenes and section re-entry at desktop, tablet,
mobile and narrow widths (1440/768/390/320 px); no horizontal overflow was observed.

Application checks use `make test-api` and `make test-integration`. These targets
exclude the separate owner-run research suites; running the application gate must
not train, fit or evaluate a handwriting model. `make contracts-check` verifies the
public schemas/client. `make research-metadata-check` is a separate local metadata
audit that requires the private research records and executes no models.

**549 application backend tests and 179 frontend tests passed.** The first backend
invocation passed 521 but hit 27 nested macOS sandbox denials; the reviewed local
retry passed all 548 with each decoder's own network-denial boundary intact. A
public-file-only copy then exposed two test dependencies on ignored evidence paths.
The printability report now uses temporary test storage; the image-extraction
regression reads byte-identical historical synthetic-only metrics from a public
fixture. No model evaluation was rerun. With one additional inventory regression,
the current public-file-only copy passed all 549 application tests and its full
Ruff/mypy, boundary, documentation, contracts/client and credential-pattern checks.
Its 850-file inventory has no automatic audit problems; manual review remains
required.

A separate production-build snapshot passed. Typecheck/lint and the publication
auditor's 26 inert regressions passed. The 2026-10-24 candidate audit and credential
pattern scan passed, and all five pages across the Latin and Devanagari A4 print
templates were rendered and visually inspected. This checks digital layout only;
physical-printer scaling remains unverified. Database, live-account and OS-gentle
acceptance were not rerun by this preparation.

## Before publication

- Reconcile the other agent's completed changes and rerun the exact combined diff/
  inventory checks. No commit or push is currently authorized.
- Inspect generated files, source, templates and binary assets, and confirm asset
  redistribution provenance. Credentials and private writing remain excluded.
- Recheck the final selected files after any further changes; preserve missing
  hosted, account, OS-gentle, physical-device and full-phase browser gates.
- Recheck the official remote history and branch settings immediately before any later
  authorized push. This checkout has no configured remote or local commits, and the
  official remote could not be reached during this audit; inspect upstream history
  before the first publication.

The newly supplied cursive page differs from the frozen W03 source. It requires its
own private source-bound work; the older font must not be relabeled as this sample.
Real model execution remains owner-run, and creating a private font is not permission
to redistribute handwriting or train on it.
