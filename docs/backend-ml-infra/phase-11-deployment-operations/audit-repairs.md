# Phase 10–11 audit repairs

Date: 2026-10-21 (Asia/Kolkata). Authorized by the owner's “fix it” follow-up. Work was
sequential, without subtask agents. The original audit (`../../../artifacts/phase10-11-audit/findings.md`; local record)
is preserved as historical evidence. This is a repair record, not deployment approval.

## Findings and resolution

| Audit finding | Repair and verification |
|---|---|
| F01, spacing plus alternates | Compile `kern` and `calt` once. N=2/N=3 tests pass in TTF/CFF OTF/WOFF2; GPOS and GSUB survive and every required validator passes. Base-derived kerning never transfers to an unmeasured alternate. |
| F02, partial fonts | Use actual shaped glyphs and exercise every emitted pair; no fixed-corpus minimum is indexed when there are no samples. B/C partial fonts pass all three formats with and without space. |
| F03, invalid quota runbook | Strict rule-object validation, correct identity tiers, reviewed example and explicit idempotent provisioning command. Live PostgreSQL proves guest/user uploads and compute admission, exhaustion, and rejecting a changed configuration under the same version. No quotas auto-seeded. |
| F04, privacy-key rotation | Stable `PRIVACY_HMAC_SECRET` with compatibility fallback. Pin the existing privacy key before rotating the general key. Journal signatures, references and receipts remain stable. PostgreSQL tests cover API startup and worker reconciliation for account/project/contribution receipts. No general dual-key window or privacy-key migration is claimed. |
| F05, scanner self-failure | Exclude only the exact `CHANGE_ME` URL placeholder. Positive AWS/password detections remain tested, with values absent from scanner output. Final tree scans clean. |
| F06, missing CI dependency | Benchmark job installs the locked `local-postgres` group. Hosted clean-runner execution is still unexecuted. |
| F07, omitted runtime data | Both images copy pinned Unicode and fixture data; worker also includes ML core. Reconstructed COPY payloads pass packaged smoke checks. CI runs the actual images networkless under 256/768 MiB caps; Docker is unavailable locally. |
| F08, false benchmark pass | Missing/failed required validators or formats, capture failure, child errors and resource breaches produce a failed report and nonzero exit. Failure injection tests pass. |
| F09, incomplete measurement | Separate compile/validation/total raw samples and p50/p95; actual isolated verify/extract runs; simultaneous process-tree RSS sampling and supervisor limits. Varied fixture emits 944 corrected pairs. [Pinned outputs](benchmarks.md) replace the earlier parent-only resource claim. |
| F10, operational omissions | Added live-window quotas, leases/retries, ready-asset byte metadata, API body transfer, retained validation/font resources, job wall time and privacy backlog. Typed errors produce rate/quota/consent rejection counters. Real protected HTTP scrape tested against PostgreSQL. Portable provisional alert rules and blocking vulnerability scans added. Hosted alert delivery, per-worker live CPU/RSS sampling, promotion and complete deployment/recovery rehearsals remain explicitly incomplete. |
| F11, overclaimed feasibility | Full ₹0 topology is unproven. Cloud Run Jobs allowance and Firebase/App Check documented from primary sources; logical off-site backups remain an eligible solution. Database capacity is separated from object footprints; DB wake tails and other services are included as unmeasured budget inputs. |

Additional repairs: native PostgreSQL URL normalization; UUID schema isolation and cleanup;
two consecutive benchmark invocations; successful versus empty claim distributions;
fenced lease renewal; real quota reservation ledger; provider-neutral second storage
credential factory; repeated glyph-pair measurement; real font hashes and corrected `gpos`
summary key. Fixed-corpus statistics stay separate from the emitted-pair verification set.

## Immutable build identity

The algorithm is `penforge-spacing-v2`. Compiler **1.0.1** is pinned in the existing
`tool_versions` mapping in both API policy and actual engine. Repaired builds have new IDs.
Old manifests still parse without serialization changes; compiling them with the wrong
engine fails `toolchain_mismatch`. Drain old jobs with their compatible worker or create
fresh export requests; never rewrite their manifests or existing ready exports. Zero
spacing omits its feature, without a promise of pre-repair name/build bytes.

## Executed acceptance

| Check | Result |
|---|---|
| Backend, `pytest -m 'not integration' -q` | **447 passed**, 220 deselected |
| Real disposable PostgreSQL, `make test-integration` | **215 passed**, 452 deselected |
| Final operations target including actual protected HTTP scrape | **6 passed** after the final HTTP assertion |
| Spacing target | **25 passed**; partial coverage, combination and identity cases |
| Frontend `npm test` | **118 passed** |
| Lint / format / mypy / boundaries / documentation | Passed; final logs copied below |
| Retained v1 compatibility, schema generation, TypeScript and research registry | Passed |
| Frontend typecheck / ESLint / production build | Passed; existing CSS autoprefixer warnings remain |
| Secret scan | Clean; positive and placeholder tests passed |
| Synthetic font evaluation and five-layer report | Regenerated with current policy/version and real hashes |
| PostgreSQL benchmark + logical restore | Passed; 60 jobs restored; real quotas held at cap |
| Worker benchmark | Passed; verify/extract + three formats + all five validators under sampled 768 MiB limit |
| Image COPY payload reconstruction | API and worker passed with host dependencies; **not a Docker run** |
| Auth emulator | Not rerun; the prior five-scenario evidence remains historical |

Raw repair artifacts (`../../../artifacts/phase10-11-repairs/verification.json`; local record),
PostgreSQL benchmark (`../../../artifacts/phase10-11-repairs/pg-local-20261021.json`; local record),
worker benchmark (`../../../artifacts/phase10-11-repairs/worker-local-20261021.json`; local record),
image payload checks (`../../../artifacts/phase10-11-repairs/image-payload.json`; local record), and
spacing summary (`../../../artifacts/phase10/summary.json`; local record) retain the actual outputs.
The artifact index records source hashes and command-log hashes. No Git commit is
claimed: this workspace has no committed HEAD and its sources are untracked.

## Remaining work

Phase 10's accepted measured-spacing scope is repaired locally. Phase 11's public outcome
is **not complete**. Provider/Q7 decisions, shared rate limiting, hosted identity/App Check,
selected object adapter, Docker/hosted CI, region/scheduler/pooler measurements, monitoring
contacts/delivery, manual promotion and complete database/object/journal recovery must
still be implemented or rehearsed in the selected environment. Provisional alert rules
have not been loaded into a deployed monitoring system.

Preserve earlier pending Phase 03/04 wizard, Phase 05 gentle motion, Phase 06 browser,
Phase 07 gentle/final account deletion, Phase 08 gentle/physical touch, Phase 09 responsive
visual/keyboard and Phase 10 spacing-control browser checks. No UI was redesigned here.
No authentic-handwriting, human typography, recognition, fidelity or training result is
established by synthetic engineering tests.

Both Indic-HW-UC downloads were subsequently found in `~/Downloads/` at the expected
published sizes (Hindi 3,576,032,718 bytes; Marathi 3,009,421,257 bytes). They have not been
moved or admitted. Download observation (`../../../artifacts/phase10-11-repairs/download-observation.json`; local record)
and archive/hash check (`../../../artifacts/phase10-11-repairs/download-index-check.json`; local record)
record the read-only check; a readable ZIP index is not CRC, annotation or writer validation.

For the user's next ML step: validate archive contents and parse the authorized corpora,
resolve writer identities and IIIT cross-corpus duplicates,
freeze writer-disjoint train/validation/test sets, and run pinned recognition baselines.
Then select a bounded first training/fine-tuning experiment with held-out metrics and a
recorded resource budget. Recognition and personal-font generation need separate tests;
a recognizer training result alone does not validate writer fidelity. Hosting need not
be completed before local data preparation and research. No training was started here.
