# Penforge

Official repository: [debugonaut/PenForge](https://github.com/debugonaut/PenForge).
Contributions and pull requests belong in this repository. Public changes receive
code, privacy, licensing and verification review before publication.

Original project code is licensed under the [MIT License](LICENSE). Bundled font and
Unicode data retain their [own licenses and notices](THIRD_PARTY_NOTICES.md). Private
handwriting and research records are excluded from publication. See the
[v1 preparation and verification boundary](docs/releases/v1-preparation.md).

An illustrated stationery experience and handwriting-to-font studio with separate local demo and account-backed paths. Private approved snapshots compile into real fonts; automatic ordinary-photo handwriting generation and hosted readiness remain unproven. `document.md` is the original implementation specification; later accepted design and motion decisions are preserved below.

## Continue development

Start with the [codebase map](docs/CODEBASE_MAP.md) for task entry points, data flow, source ownership and scoped checks. Local agents must also follow the workspace's supplied root instructions and current handoff; those local files are excluded from the public checkout. Read the complete [frontend specification](document.md), [design language](docs/design-language.md) and [motion design contract](docs/motion-design.md) before changing the accepted UI.

Backend, ML and infrastructure work follows the complete [v3 engineering specification](docs/specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md) and [phase index](docs/backend-ml-infra/plan.md). [Phase 00 — Foundations](docs/backend-ml-infra/phase-00-foundations/plan.md), [Phase 01 — Identity](docs/backend-ml-infra/phase-01-identity/evidence.md), [Phase 02 — Projects](docs/backend-ml-infra/phase-02-projects/evidence.md) and [Phase 03 — Capture and uploads](docs/backend-ml-infra/phase-03-capture-uploads/evidence.md) are implemented locally. Cloud project metadata and private captures are explicit choices; local handwriting/drafts are never uploaded or migrated automatically. See the phase-specific development instructions and acceptance evidence for executed checks and remaining hosted gates.

## Run

```sh
npm ci
npm run dev
```

Open [localhost:3000](http://localhost:3000). To serve the optimized build, run `npm run build` followed by `npm start`.

## Routes and interactions

- `/`: five centered scenes with a shared oversized DM Sans and Caveat title scale: a native handwriting-to-typing SVG film, an interactive Write / Refine / Type grid, a Writing Desk, a style editor, and nine FAQs with the final create CTA. The process grid lets visitors choose an authored `g` shape, see both occurrences update in a typed phrase, and compare matching paper/typed samples with the same loop highlighted. The style editor uses visual slider endpoints; size and line spacing live under More controls. The film has pause and replay controls. Section navigation and system reduced-motion preferences are supported. The closing footer has the original PenForge wordmark, grouped live links and a circular back-to-top arrow at its lower left.
- `/create`: account mode loads owner-scoped working state, private capture/review, style settings and generated-font references through typed services. The separate local demo supports drawing, validated image previews, simulated extraction/export, glyph prototype editing and local recovery. Review/approval and real private font compilation keep their explicit boundaries.
- `/account`: a single paper sign-in card — email/password, Google and GitHub (GitHub needs its Firebase provider enabled), with verification and explicit service/handwriting-rights acceptance — plus existing guest claiming when a guest proof is pending. Auth does not upload or migrate local drafts.
- `/projects`: account projects support resume, rename and confirmed deletion, with owner-scoped metadata, versioned working state and conflict-safe changes. Local demo drafts remain in a separate disclosure with resume, duplicate, rename, recovery and raw backup. Explicit import creates a separate account project from supported local fields; uploaded images are not reuploaded, and the local copy remains intact.
- `/write`: Writing Desk offers distinct letter, memo and greeting-card stationery, four paper stocks, separate ink/accent choices and typography controls. Format changes preserve words. Device drafts remain local until an explicit account-save choice; saved account pages resume with generated-font references. Imported font files stay device-only. Actual font bytes render in the preview and real PNG/SVG downloads; SVG embeds the selected font. Sharing uses the device share flow; printing can save a PDF.

The hero uses oversized lettering above a large coral film stage with dark playback controls. Character cutouts peek from the stage edges without covering the title; the create CTA follows the demo. The film positions the pen nib against measured letter advances and tracks the typing caret to visible glyph edges, while the same handwriting continues through photographing, digitizing, and typing. The same hand illustration appears in the Write grid. An original procedural paper texture frames the opening and closing scenes; cream, yellow, coral, and mint keep the middle scenes consistent. The FAQs use an adapted shadcn/Radix accordion with one answer open at a time, 100ms height transitions and reduced-motion handling.

The hand/pen cutout was generated with the built-in image-generation tool and is delivered as a lossless WebP. Its PNG source, final prompt, generation provenance, and nib anchor are in `public/illustrations/`. The two mascots remain original repository-authored SVGs.

Local demo extraction, project-font previews and demo export remain demonstrations; those project previews use Caveat. Explicit private capture/review is opt-in. The separate private export panel compiles approved snapshots into validated real TTF, CFF OTF and WOFF2. Writing Desk can explicitly fetch verified generated bytes by owned export ID; imported fonts also use their actual bytes. No local handwriting is uploaded automatically. Writing Desk PNG and SVG exports are real document exports. SVG files embed the selected font; the bundled Caveat export also includes its OFL license. Original vector characters are authored in the repository. Fraunces, DM Sans, and Caveat are served locally from their Fontsource packages.

Local drafts use validated, versioned storage and survive refresh in the same browser and origin. They are never automatically sent to cloud projects. Cloud project metadata uses server-side row versions; local draft writes remain browser-local and concurrent writes across tabs are not transactional. Recovery protects malformed local data before resetting.

## Checks

```sh
npm run typecheck
npm run lint
npm test
npm run build
```

Development uses `.next-dev`; production builds use `.next`, so the two outputs do not collide. Scoped GSAP timelines animate the native SVG film and repeat scene entrances when revisited. The film loops after a short finished-result hold, pauses offscreen, and retains local pause/replay controls. There is no global motion toggle or saved off flag; the system reduced-motion setting selects a gentler animated variant. One deliberate wheel gesture advances one scene; nested scrollable content and sections taller than the viewport allow ordinary scrolling before advancing. Touch, keyboard, and scrollbar navigation remain native.

## Earlier frontend visual review

The accepted Writing Desk review covered distinct letter stationery, memo and greeting-card designs, separate ink/accent colors, preserved words and handoff fields, Personalize, PNG download feedback, and narrow mobile controls without horizontal overflow. Previews retain their size when disclosures open. Phase 02 check counts and browser captures are recorded in [Phase 02 evidence](docs/backend-ml-infra/phase-02-projects/evidence.md); Phase 01 emulator and hosted limits remain in [Phase 01 evidence](docs/backend-ml-infra/phase-01-identity/evidence.md).

The earlier frontend iteration was inspected at 1440×900, 1280×720, 768×1000, 390×844, and 320×844 with no horizontal page overflow. Review covered the shared display typography, revised writing grip, responsive character clearance, process grid, compact Writing Desk, expanded style controls, scene navigation, and keyboard focus after explicit navigation. The style proof grows to show wrapped text and scrolls only beyond its bounded height. Taller scenes and expanded FAQs remain accessible through ordinary scrolling. Reduced motion was also checked in the preceding iteration.

Creation and Workbench checks covered keyboard drawing and history, uploads and invalid-file recovery, refresh/resume, glyph approval, style presets and local text rules, multiline preview, export disclosure, confirmation cancellation, and back/forward navigation.

Landing captures and earlier editor, preview, and Workbench screenshots are kept in `artifacts/`.

Reference review included the live [RIFT](https://www.tryrift.fun/) site and Dennis Snellenberg’s [Breathtaking](https://dribbble.com/shots/13961411-Breathtaking), plus [Impeccable](https://github.com/pbakaus/impeccable), the official [GSAP guidance](https://github.com/greensock/gsap-skills), [LottieFiles motion design](https://github.com/LottieFiles/motion-design-skill), [Zero-Jank Scroll](https://github.com/harshavarma02/zero-jank-scroll-agent-skill), [mthines animations](https://github.com/mthines/agent-skills/blob/main/skills/design/animations/SKILL.md), and [motion-design-agent](https://github.com/soilmass/motion-design-agent). LottieFiles guidance informed coordinated entrances, quick control feedback, and restrained paper settling. The other supplied frontend/motion collections were reviewed as indexes. These were consulted as references; no external skill installer or setup script was run. The local brainstorming skill was used only for context review, following the explicit instruction to begin implementation without new design approvals.

## Remaining integration

Backend Phases 00–10 are implemented locally within their evidence gates. Phase 10 ships measured pair kerning; joins and advanced refinement remain gated. Phase 11 portable tooling and audit repairs exist, while hosted execution and its full public outcome remain incomplete. Use the [phase index](docs/backend-ml-infra/plan.md) and each phase's evidence for current scope and pending browser/OS checks. No handwriting model is qualified; Devanagari production, ordinary-photo automation and public deployment remain unproven.

Account workspace/document persistence is integrated with the existing backend. The [account integration evidence](docs/backend-ml-infra/integration/account-persistence.md) records a prior real loopback project → capture → approval → font → saved document scenario and its limits. The [2026-10-24 refactor results](docs/refactoring/2026-10-24/results.md) record shared policy extraction, architecture-guard repairs and scoped browser verification. Local demo data is never automatically migrated into an account.

Phase 00 established the local backend; identity and opt-in metadata cloud projects have separate [local setup](docs/backend-ml-infra/phase-01-identity/development.md) and [Phase 02 run instructions](docs/backend-ml-infra/phase-02-projects/development.md). Phase 03 adds [private capture storage and the durable verification worker](docs/backend-ml-infra/phase-03-capture-uploads/development.md); [Phase 04 extraction/review](docs/backend-ml-infra/phase-04-extraction-review/development.md) and [Phase 05 font export](docs/backend-ml-infra/phase-05-font-export/development.md) now have their own local run instructions. Current crop/rotation/drawing controls edit visual glyph prototypes. Uploaded images have validation, downsampling, preview, replacement and removal; a precision image-crop and orientation workspace remains a further refinement. Character artwork is original SVG, with no emoji or stock mascot placeholders.

### Service-accountability draft

Account creation/editing, capture processing and private font downloads require a
verified account and the current service agreement. `/create` defaults to account mode;
`/create?mode=demo` selects the separate local demonstration. `/terms`, `/privacy` and
`/report-abuse` describe the pre-release controls. Training consent is independent.

Migration 0014 adds agreement receipts and private report storage. Stable privacy-key
configuration is required for accountability. The operator name/contact and Indian
legal review remain unresolved, so public use stays gated. See the
[implementation evidence and operator procedure](docs/accountability/evidence.md).
No legal compliance, immunity or hosted readiness is implied.
