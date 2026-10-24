# Resource budget — measured inputs and unresolved hosting

Corrected 2026-10-21 (Asia/Kolkata). [Pinned benchmark results](benchmarks.md) supersede
the original compile-only time and parent-only memory figures. No hosted cost was measured.

## Measured local units

- Max upload: 20 MiB; derivative: 4 MiB / 1000 px; worker admission: 768 MiB (code caps).
- Synthetic verify p50: 101.417 ms; extraction p50: 617.204 ms.
- Compile plus all five validators, p50 per format: ttf 364.43 ms, otf 346.385 ms, woff2 390.43 ms.
- Sampled full process tree: 398.3 MiB peak; sampling is not a hard cgroup measurement.
- These inputs are authored synthetic geometry/template ink. They do not establish
  real-photo worst cases, handwriting quality, steady-state cloud throughput or cost.

## Proposed storage shape

Ten 2 MiB originals + ten 0.2 MiB derivatives + 0.5 MiB glyph artifacts + 0.03 MiB
exports is **22.53 MiB of assumed object data**. This is not database size. At a nominal
1,000,000,000-byte object allowance it is roughly 42 such footprints before safety margin,
version history, retries and backup copies. PostgreSQL metadata, revisions, events and
indexes require a separate measured footprint; no database-project capacity is inferred
from image sizes. Worst-case capture caps can exceed 4 GiB per project before quotas.

[Validated quota example](../../../infrastructure/deployment/quota-tiers.example.json):
guest 25 MiB lifetime / 600 compute seconds daily; registered user 50 MiB / 1200 daily;
global 700,000,000 bytes / 3600 seconds daily. **Proposals only**, not auto-seeded, not a
hosted safe-capacity claim. Provision via [runbook](runbook.md), then measure and review.
An app compute-second allowance is not a database CU-hour allowance.

## Full topology still needs a budget

| Component | Known input | Unmeasured or undecided |
|---|---|---|
| Frontend | Existing Next.js build | Hosting, image/build minutes, bandwidth |
| API | FastAPI + persistent pool + SSE/polling | Cold starts, region, concurrency, shared rate limiting |
| Worker | Local verify/extract/compile/validate above | Container startup, temporary disk, scheduler retries, hosted memory |
| PostgreSQL | Real locks/quotas/logical restore tested locally | Hosted pooler, wake tails, metadata growth, backups |
| Objects | Assumed footprint and typed protocol | Selected adapter, actual inventory, egress/operations, backup copies |
| Identity/App Check | [Provider comparison](providers.md) | Guest churn, attestation assessments and quota behavior |
| Scheduler | Bounded worker is possible in principle | Delivery reliability, minimum billed duration, scheduler charges |
| CI/registry | Workflow and images exist | Hosted minutes, vulnerability results, image sizes/storage |
| Monitoring | Private metrics + portable alert rules | Scraper, alert delivery, retention, provider egress |
| Recovery | Small logical DB restore measured | Off-site DB/object/journal storage, tested production RPO/RTO |

Neon Free's 100 CU-hours / 0.25 CU = 400 active hours (published arithmetic). A continuously
polling worker exceeds it. A ten-minute daily worker is five nominal execution-hours
monthly, but database wake tails, requests and monitoring also keep compute active.
This arithmetic is not a complete deployment feasibility result.

Cloud Run Jobs publish a free allowance with billing prerequisites, a minimum billed
minute per started instance and account-wide aggregation. Scheduler/build/registry/egress
can cost separately. [Spend caps](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps)
can enforce late and permit billed overages. No exact zero-spend guarantee is established.

**₹0 for a complete low-traffic deployment remains a hypothesis.** Compare selected
regional services and measure the full topology before stating otherwise. Logical backups
are allowed if off-site, scheduled, retained and successfully drilled; provider PITR is
optional. The owner still selects providers and approves provisioning/promotion.
