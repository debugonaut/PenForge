# Phase 01 acceptance evidence

Date: 2026-10-19. Status: **implemented and locally verified; hosted gates remain open**.
Authorization covers Phase 01 only. Phase 02 has not started. No cloud provisioning,
public deployment, training, model selection or later processing was performed.

## Implemented behavior

| Requirement               | Actual implementation and evidence                                                                                                                                                                                                                                                                                                                                                                  |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Verified Bearer identity  | Admin Python 7.7.0 behind `AuthVerifier`, strict one-header Bearer parsing, verified UID/provider/email state, project/issuer/signature/time checks, revocation/disabled lookup on every request. Invalid/expired/wrong-project/revoked returns safe typed 401; dependencies return 503. Real SDK RSA fixture tests are separate from unsigned emulator tests.                                      |
| Transactional persistence | Internal user mapping by verified Firebase UID, concurrent first insert, guest counter in the same transaction, injectable clock/IDs, monotonic linking and preserved nondefault tiers/history. Suspended/deleting/merged users rejected.                                                                                                                                                           |
| Truthful `/me`            | Actual identity/tier/creation history and current configured quota rows; unconfigured budgets are explicit, never unlimited. No token/email, storage key or invented budget in response.                                                                                                                                                                                                            |
| Secure claiming           | Both proofs verified, registered destination, anonymous source, bounded recent `auth_time`; same UID preserves ID. Stable user locks, exact immutable replay receipt, competing-claim protection and stale-guest rejection.                                                                                                                                                                         |
| Lossless transfer         | Projects, project/standalone assets/jobs, reservations and balance usage move atomically. Ready hash collisions become aliases without byte/key/state loss; colliding job keys retain original immutable scope. Creation, approval, revision, snapshot and export provenance retained. Over-limit balances preserve work; no project/job creation API exists yet to bypass the later quota service. |
| Races and ownership       | Real PG tests cover concurrent first requests, competing destinations, eight identical claims, new job before claim, stale guest insert, deletion-state lock race, running-job ownership with unchanged lease, foreign-owner 404 service lookups, merged guest rejection, reservation/global usage conservation. No worker runtime/deletion saga is claimed.                                        |
| Storage and consent       | Indexed owner-scoped purge inventory includes historical keys/aliases. Prefixes never authorize access. Claims do not grant consent; legacy active guest contributions are withdrawn atomically, retaining provenance.                                                                                                                                                                              |
| Abuse/log safety          | Bounded concurrent injected limiter, first-identity/IP/global and claim windows, rate headers/Retry-After, HMAC peer IP only. SDK/body/validation failures never echo proofs or credentials. Real Uvicorn header-spoof test verifies changing X-Forwarded-For cannot change the peer limit.                                                                                                         |
| Frontend                  | Isolated `/account`, lazy Firebase JS 13.0.0 through generated client/service boundary, explicit configuration/actions, unavailable/errors, tab-scoped auth, memory-only source proof, explicit transfer approval, navigation/reload warning. Existing local mock services stay selected.                                                                                                           |
| Migration/contracts       | Additive `0002_identity`, seeded 0001 upgrade, pre-claim up/down/up, zero metadata drift, guarded post-claim downgrade, retained original v1 baseline plus identity wire fixtures, generated TS/OpenAPI Bearer routes, additive compatibility regression.                                                                                                                                           |

## Executed checks

Backend output (`../../../artifacts/phase-01/backend-tests.txt`; local record) and the final combined
check output (`../../../artifacts/phase-01/final-check.txt`; local record) record actual commands/results:

- Python unit/contracts/research: **177 passed**.
- Real PostgreSQL: **74 passed**, including all **49 preserved Phase 00 tests** and
  **25 Phase 01 tests**. No SQLite substitute. Focused DB output (`../../../artifacts/phase-01/identity-postgresql.txt`; local record).
- Real Auth emulator + PostgreSQL/public identity HTTP: **3 scenario tests passed**,
  exercising anonymous/password/Google linking, existing-account transfer/replay/merged
  rejection, disabled users, rate limits, CORS and secret redaction.
- Frontend: **48 passed** (38 preserved + 10 identity tests).
- Ruff check/format, mypy, layer checks, local links and original specification hashes,
  deterministic contract generation, retained-v1 compatibility, independent TS and research
  metadata audit passed. Frontend typecheck, ESLint and production build passed.
- `npm run dev`, successful `npm run build` followed by `npm start`, actual local API and
  Auth emulator execution passed. Native private PostgreSQL is 16.2; CLI is 15.34.0,
  Python 3.12.15 and Node 26.9.0. Temporary CLI tooling warned that its unused superstatic
  package lists Node 20/22/24; the Auth emulator executed. CI remains configured for Node22.

Offline cryptographic fixtures use the real Admin SDK with local RSA keys/certificates and
controlled user-record lookups. They prove verifier logic and SDK signature/revocation
behavior, not Google's live certificate endpoint, ADC access, hosted account disablement
propagation or real OAuth. The emulator deliberately uses unsigned tokens and mock Google
accounts. [Firebase review](firebase-compatibility.md) cites current official documentation.

One upstream Starlette/httpx test-client deprecation warning remains. It is not an auth
failure. No upstream warning was hidden by modifying unrelated framework dependencies.

## Browser evidence

Layout data (`../../../artifacts/phase-01/browser-layout.json`; local record) records full and actual
browser-emulated OS gentle preference at 1440×900, 768×1000, 390×844, 320×844 and 1280×600.
All ten account layouts fit the viewport width and have no page errors. First keyboard
Tab reaches the visible skip link/focus outline; form controls have labels and tab access.
Screenshots include desktop (`../../../artifacts/phase-01/account-1440x900-full.png`; local record),
narrow (`../../../artifacts/phase-01/account-320x844-full.png`; local record) and
gentle (`../../../artifacts/phase-01/account-390x844-gentle.png`; local record).

Interaction evidence (`../../../artifacts/phase-01/browser-interactions.json`; local record) records a real
browser against the local API/emulator: no request before explicit action, guest sign-in,
unchanged internal ID on email link, existing-account login, disabled claim button before
approval, approved transfer, and Google mocked-popup linking. Only `/me` and claim reached
the API; existing local draft bytes remained unchanged. The source proof/credentials are
not in screenshots or JSON. Claim review (`../../../artifacts/phase-01/account-claim-review.png`; local record)
and linked Google identity (`../../../artifacts/phase-01/account-google-linked.png`; local record) show UI states.

The optimized production build, with no Firebase configuration, shows an explicit
unavailable state (`../../../artifacts/phase-01/account-default-unavailable.png`; local record) and makes no
external auth request. Local create, Workbench/account navigation, landing and Writing Desk
remain reachable. Production data (`../../../artifacts/phase-01/browser-production.json`; local record),
preserved hero (`../../../artifacts/phase-01/hero-preserved-1440.png`; local record).
Existing real font-import/stationery tests remain green. Landing/motion/hand/renderer/local
service sources were not edited. Fresh manual import and every stationery export format
are not newly claimed by this identity pass.

Forwarded-header HTTP evidence (`../../../artifacts/phase-01/forwarded-header-check.json`; local record)
records two invalid proofs followed by a 429 despite three different supplied forwarded
addresses. Public launch commands disable proxy headers; trusted ingress remains a gate.

## Remaining gates and acceptance limits

All locally executable Phase 01 identity criteria pass. There are no new public project,
asset, upload, job or deletion routes: foreign-resource 404 is verified at the owner-scoped
service boundary; route-level IDOR coverage must accompany those routes in their phases.
Job race evidence uses real DB transitions/leases, not a running later-phase worker.

The following checks/decisions remain incomplete and are not asserted by emulator success:

- Hosted Firebase project/provider enablement, email/Google configuration, domain checks,
  ADC/service identity and real Google certificate/revocation behavior.
- App Check attestation/enforcement, Auth's Preview/Identity Platform eligibility, provider
  billing/quota terms and actual deployment checks. No debug token bypass is deployed.
- Shared multi-instance PostgreSQL rate limiter, stable HMAC rotation/overlap, trusted
  ingress and measured global resource ceilings. Hosted identity fails closed until this
  deployment gate is implemented; local limits are safety defaults, not throughput claims.
- Existing Docker/Compose image execution and hosted CI remain unexecuted: no Docker or
  Git remote/runner here. Auth emulator CI step is authored, not hosted verified.
- Current npm audit (`../../../artifacts/phase-01/npm-audit.json`; local record): **11 findings (10 high,
  1 moderate)**. The prior seven Next/PostCSS/ESLint/glob findings remain; Firebase adds
  four package findings through the unused Firestore → gRPC chain. Only `firebase/app`
  and `firebase/auth` are imported; Firestore/Storage/Analytics are not initialized.
  This narrows current runtime relevance but does not erase dependency risk. Reported
  automatic remedies include an old major Firebase downgrade; no forced upgrade/downgrade
  or unverified transitive override was applied. Resolve before public release.
- Anonymous sessions older than the claim window cannot renew `auth_time` by token refresh.
  They fail securely and retain server work. No insecure proof recovery shortcut is added.
- Phase 07 must explicitly enable authorized receipt/origin-history erasure and safe
  cross-project canonical/alias deletion order. Current immutability prevents ad hoc purge.
- Provider decisions, Infra Q7/Q8–Q10, unmeasured ML and governed research gates carry forward:
  0 approved datasets, 0 measured benchmarks, 0 completed experiments. No training occurred.

## Decisions and rollback

[ADR 0005](../../decisions/0005-phase-01-identity-and-claims.md) records reconciliations.
[Development](development.md) gives explicit configuration and rollback commands. Disable
frontend auth and rebuild to remove account integration while preserving all drafts/users.
Do not delete Firebase users or attempt a lossy reverse claim. Schema downgrade is tested
before receipts only; afterwards use forward repair. The next recommended phase is
**Phase 02 — Projects**, requiring new explicit authorization. Stop before it.
