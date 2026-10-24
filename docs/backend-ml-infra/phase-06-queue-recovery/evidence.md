# Phase 06 implementation and acceptance evidence

Date: 2026-10-20. Status: implemented locally; automated verification recorded below.
Responsive/browser acceptance is **unexecuted** because local browser use is disabled
in Work settings. Full phase acceptance is therefore still pending. Work was sequential;
no subtask agents, provider provisioning, deployment or Phase 07 work was performed.

## Implemented behavior

- All three real workers share the existing PostgreSQL jobs table and advisory-locked
  strict global/owner caps. Bounded server priority, aging and owner dispatch rotation
  prevent the measured backlog monopolization. Type/schema/script/profile filters keep
  unsupported work queued; v1 payload IDs and terminal records are preserved.
- Every worker heartbeat, progress/checkpoint, asset/result/candidate publication and
  export finalization uses job/holder/attempt/unexpired lease plus active scope. Added
  database live and deferred guards retain the earlier commit-time publication guards.
  A rejected verification commit now returns an unaccepted publication, stopping work.
- Immutable checkpoint outputs → manifest → fenced reference support verified sample/
  format reuse. Hash identity covers all source/template/model/config/algorithm/code/
  dependency inputs. Corruption fails closed; dependency changes miss the old identity.
  Pending crash output is reconciled without deleting accepted historical artifacts.
- Configurable retry jitter/backoff, poison detection, queued/running cancellation and
  graceful monotonic requeue settle quotas once. SIGKILL use is marked unmeasured.
  Measured overage fails success publication, retains actual usage, and terminal cleanup
  cannot silently exceed the reserved hard cap. Automatic top-up is disabled.
- Versioned settings/tier provisioning preserve explicit local limits. Accuracy-first
  degradation can reduce concurrency or reject admission, never substitute an inferior
  extractor/compiler/validator. Reads/review/approved downloads remain available.
- Polling and server-polled SSE share immutable ordered status snapshots. ETag,
  Retry-After, Last-Event-ID, bounded stream slots/lifetime and ongoing authorization
  are implemented. Private panels add measured progress, unknown ETA, reconnect/fallback
  and accessible cancellation. Source writing and credentials are excluded from events.
- Scheduled drain and persistent idle use the same protocol and existing isolated,
  bounded stage children. No ML model cache is introduced because these three handlers
  load no trained model. No alternate queue, broker or in-process processing runner exists.

Decisions: [queue ordering](../../decisions/0012-phase-06-queue-ordering.md),
[checkpoint identity](../../decisions/0013-phase-06-checkpoint-identity.md),
[retry/cancellation](../../decisions/0014-phase-06-retries-and-cancellation.md),
[quota/degradation](../../decisions/0015-phase-06-quota-and-degradation.md).
Configuration, endpoints and rollback are in [development](development.md).

## Verification record

Fresh complete verification: **466 backend tests passed** (4 emulator scenarios deselected),
**4 separate Auth-emulator scenarios passed**, and **101 frontend tests passed**. Ruff
and formatting passed across 158 Python files; mypy passed 103 source files. Import
boundaries, OpenAPI/TypeScript generation, retained-v1 compatibility, frontend TypeScript,
ESLint, production build, seven-route frontend startup and API startup checks passed.
A focused 14-test recovery run and four capacity/mode/migration checks are focused
subsets, not added to these totals.

Final counts and command evidence are recorded in
the acceptance summary (`../../../artifacts/phase06/summary.json`; local record). Focused package
runs are subsets of the complete suites, not additional unique test counts.
Earlier failed runs are retained for diagnosis and are superseded by the final runs.
The complete backend run uses real PostgreSQL 16.2 over a local Unix socket; the separate
identity run uses only the local `demo-penforge-identity` Auth emulator. No SQLite,
hosted identity or real writing is substituted.

Relevant retained reports:

| Check | Evidence |
| --- | --- |
| Full backend, including Phase 03–05 regression suite | backend-final.txt (`../../../artifacts/phase06/backend-final.txt`; local record) |
| Separate Auth emulator | emulator-final.txt (`../../../artifacts/phase06/emulator-final.txt`; local record) |
| Frontend transport/recovery/client tests | frontend-final.txt (`../../../artifacts/phase06/frontend-final.txt`; local record) |
| Legacy review fixtures, current fencing and deferred verification expiry | legacy-fence-regressions.txt (`../../../artifacts/phase06/legacy-fence-regressions.txt`; local record) |
| Actual SIGKILL after sample two, corrupt/dependency-mismatched checkpoints | `test_queue_recovery.py` in the full backend run; chaos report (`../../../artifacts/phase06/chaos.json`; local record) |
| Actual SIGTERM, checkpoint retained, replacement succeeds | sigterm.json (`../../../artifacts/phase06/sigterm.json`; local record) |
| Owner-only SSE/poll equivalence, resumption, account/ownership revocation | event report (`../../../artifacts/phase06/events.json`; local record) and `test_job_events.py` |
| 1/5/20 claimers, fairness, caps, heartbeat latency, EXPLAIN | 1 (`../../../artifacts/phase06/concurrency-1.json`; local record), 5 (`../../../artifacts/phase06/concurrency-5.json`; local record), 20 (`../../../artifacts/phase06/concurrency-20.json`; local record) |
| Actual three-handler drain, three validated formats, persistent idle stop | resources.json (`../../../artifacts/phase06/resources.json`; local record) |
| Previous-release queued v1/terminal records survive up/down/up | previous-release-migration.json (`../../../artifacts/phase06/previous-release-migration.json`; local record) |
| Ruff/format/mypy/import boundaries/source integrity | quality-final.txt (`../../../artifacts/phase06/quality-final.txt`; local record) |
| Generated OpenAPI/TypeScript/retained-v1 compatibility | contracts-final.txt (`../../../artifacts/phase06/contracts-final.txt`; local record) |
| TypeScript/ESLint/production build and startup | frontend-checks.txt (`../../../artifacts/phase06/frontend-checks.txt`; local record), startup.json (`../../../artifacts/phase06/startup.json`; local record) |

Migration `0009_queue_recovery` advances to 26 application tables. The complete suite
checks empty up/down/up, current ORM/schema drift, previous-release seeded preservation
and downgrade refusal once checkpoint lineage must be retained. Older migration tests
exercise their historical SQL with frozen v1 data, then restore head before invoking
current repositories; new service binaries are not advertised as compatible with old DB
schemas. Review fixture inserts now supply the same live fence as real publication.

## Measurement interpretation

Hardware (`../../../artifacts/phase06/hardware.txt`; local record): Apple M4 (`Mac16,12`), 16 GiB RAM,
macOS 15.7.4 arm64, Python 3.12.15, local PostgreSQL 16.2. Queue tests use five owners
and fourteen synthetic jobs, with explicit fixture caps global 4/owner 1, retry base 0
and 1, 5 or 20 concurrent database claimers. They are database contention tests, not
twenty simultaneously compiling child processes. First five dispatches reach five
distinct owners, no attempt is claimed twice, and final reservations are zero.

Each concurrency JSON contains claim and heartbeat p50/p95, dispatch order and exact
EXPLAIN output/query identity. `enable_seqscan=off` explicitly proves availability of
`jobs_claim_idx` and `jobs_lease_idx` on this tiny fixture; plans are collected after the
drain and may return zero rows. This does not prove optimizer choice or latency on a
production backlog. Re-test with representative data before sizing a provider/pooler.

The resource report measures one sequential worker draining three synthetic jobs:
strict decode, one digital analysis sample and all three compiled/validated font formats.
It records queue wait, active wall time, per-child RSS and watchdog ceilings. Parent
RSS is explicitly the pytest process's lifetime peak and includes prior tests; it is
not a standalone worker peak. Child RSS is sampled and may miss a short peak. Actual
stage CPU is only reported where measured; a CPU limit is not a CPU measurement.
No matching ETA or handwriting accuracy benchmark is qualified. Numeric values are
local measurements/fixtures, not production quotas, throughput or provider cost.

## Browser and remaining acceptance

A disposable schema, local Auth emulator and dedicated frontend/API ports were prepared.
Only guest account connection was observed before automation became unavailable. On
continuation the browser tool returned: “Local browser use is disabled in Work settings.”
No settings were changed or browser restrictions bypassed.

Phase 06 private-panel UI at 1440/768/390/320 px, short viewports, keyboard interaction,
ordinary/OS-gentle motion, visual cancellation, reload recovery and browser-level SSE
disconnect/reconnect/polling fallback are **unexecuted**. Automated HTTP/client tests cover
the protocol behavior and do not close these interaction checks. Phase 03/04 current-
wizard browser checks remain pending at the user's choice; Phase 05 OS-gentle remains
unexecuted. Prior Phase 05 responsive/CoreText evidence is preserved, not rerun here.

Other unresolved gates: hosted service identity and scoped runtime access, provider
pooler/SSE limits and capacity, App Check, shared public rate limiting, cloud storage,
Linux/container enforcement and hosted CI, consent/retention/deletion, real-handwriting
accuracy and public readiness. Accepted checkpoint history remains inventoried/charged;
TTL only expires reuse. The Git directory has untracked workspace files, no established
commit history/remote; no PR, backup or deployment is claimed. Stop before Phase 07;
privacy/deletion is a separately authorized next phase.
