# Provider comparison — October 2026

Original comparison checked **2026-10-20**. Corrections checked **2026-10-21 (Asia/Kolkata)**:
Neon plans, Supabase backups, Cloud Run pricing/spend caps, Firebase pricing/Auth limits
and App Check. Other original rows retain their earlier check date and require verification
again before selection. No account or hosted workload was used.

This file separates three things that are easy to blur together:

1. **Verified** — quoted from the linked official page.
2. **Measured** — produced by `scripts/phase11_pg_benchmark.py` on this machine
   (see [benchmarks](benchmarks.md)). Local numbers are _not_ hosted numbers.
3. **Required** — what PenForge needs, from the code and the measured workloads.

## PostgreSQL candidates

| | Neon Free | Supabase Free |
|---|---|---|
| Card required | **No** ("no credit card required") | **Not stated** on the Free page |
| Storage | **1 GB** per project, **20 GB** total | **500 MB** database, 1 GB disk |
| Compute | **100 CU-hours/project/month**, up to 2 CU (8 GB RAM) | Shared CPU Nano, up to 500 MB RAM; no stated CU-hours allowance |
| Idle behaviour | Compute **scales to zero after 5 min**; next query reactivates. Data retained | Project **pauses after ~1 week** of low activity; restorable up to 1 year |
| Connections | Pooler (PgBouncer **transaction mode**), 10,000 clients; `max_connections` 104 at 0.25 CU (97 for the app) | **60** direct connections, **200** pooler clients; shared pooler in **both session and transaction** mode |
| `SKIP LOCKED` through pooler | Not stated (row locks are transaction-scoped, so expected to work; unverified) | Not stated |
| Session advisory locks through pooler | **Not supported** in transaction mode | **Not supported** in transaction mode; session mode supports them |
| PITR / backups | Instant restore **6 h history, 1 GB change cap**; 1 manual snapshot; scheduled snapshots not on Free | **No automatic backups, no PITR** on Free; export recommended |
| At the limit | CU-hours or transfer exhausted → **compute suspended** until next period; storage writes fail. No pay-as-you-go overage documented | No charge; grace period, then Fair Use restrictions incl. read-only DB and **402** responses. Read-only above 500 MB |
| Sources | [plans](https://neon.com/docs/introduction/plans), [pricing](https://neon.com/pricing), [scale to zero](https://neon.com/docs/introduction/scale-to-zero), [pooling](https://neon.com/docs/connect/connection-pooling), [backup](https://neon.com/docs/guides/backup-restore) | [pricing](https://supabase.com/pricing), [pausing](https://supabase.com/docs/guides/platform/free-project-pausing), [connections](https://supabase.com/docs/guides/database/connecting-to-postgres), [cost control](https://supabase.com/docs/guides/platform/cost-control), [backups](https://supabase.com/docs/guides/platform/backups) |

**Derived check on Neon's 100 CU-hours.** The smallest compute is 0.25 CU, so the free
allowance covers **400 compute-hours**, i.e. ~16.7 days of continuous uptime in a 30-day
month. A worker that polls the queue every few seconds keeps the compute awake, so a
continuous deployment exceeds this allowance before month end. Scale-to-zero helps only
when nothing is polling. This is arithmetic from the published allowance, not a
measurement.

**Derived check on Supabase's 500 MB.** PenForge stores only metadata, pointers and
small JSON blobs in PostgreSQL; images and fonts belong in object storage. 500 MB is
therefore plausible for early use but is the tightest constraint of the two, and the
Free plan has **no managed PITR**. This is a recovery tradeoff, not an automatic
failure: the source phase permits scheduled off-site logical backups with expiry,
object backup and a successful restore/deletion-reconciliation drill.

**PenForge compatibility notes.** The queue depends on `SELECT … FOR UPDATE SKIP LOCKED`
and on `set_config('penforge.worker_fence', …)` with transaction-local scope, which
survives transaction pooling. It does **not** rely on session advisory locks. Session
mode is still required for any future feature that holds session state; that is a design
constraint, not a current need.

## Object storage candidates

| | Cloudflare R2 | Supabase Storage |
|---|---|---|
| Free storage | **10 GB-month** | **1 GB** |
| Operations | **1 M Class A**, **10 M Class B**/month | n/a (bandwidth-metered) |
| Egress | **Free** | **5 GB cached + 5 GB uncached**, shared org-wide |
| Max object | 5 TiB (4.995 TiB effective); 5 GiB single-part | **50 MB** on Free |
| Signed URLs | Presigned **PUT and GET**, expiry **1 s – 7 days** | Signed GET supported, max expiry **not stated**; signed upload **2 h**; resumable upload URL **24 h** |
| At the limit | **Billable overage**: $0.015/GB-month, $4.50/M Class A, $0.36/M Class B | No charge; grace period then Fair Use restrictions |
| Card | **Not stated** (checkout may preauthorise a card) | **Not stated** on the Free page |
| Sources | [pricing](https://developers.cloudflare.com/r2/pricing/), [limits](https://developers.cloudflare.com/r2/platform/limits/), [presigned URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/) | [storage pricing](https://supabase.com/docs/guides/storage/pricing), [file limits](https://supabase.com/docs/guides/storage/uploads/file-limits), [bandwidth](https://supabase.com/docs/guides/storage/serving/bandwidth) |

**Decisive difference.** R2 **bills overage** and documents no hard stop; Supabase Storage
on Free **fails closed** (no charge, restrictions instead). The owner's constraint is "no
uncontrolled paid overages", so a provider that can silently bill is only acceptable with
a verified spend cap. Supabase Storage's 50 MB object limit is compatible with PenForge's
20 MiB upload ceiling.

## API / worker runtimes

| | Free always-on HTTP? | Free worker? | Sleep / scale | Card | Hard spend cap |
|---|---|---|---|---|---|
| Render | 0.1 CPU / 512 MB free web service | **No free worker plan documented** | Web spins down after **15 min** idle, ~1 min cold start; 750 instance-hours/workspace/month | Not required for free usage | Build-minute limit only, **not** a compute cap |
| Koyeb | 512 MB / 0.1 vCPU, one free service | **No** — free instance cannot be a Worker Service | Scales to zero after **1 h** idle, not configurable | **Yes** (with a cancelled preauthorisation hold) | Not documented |
| Fly.io | **No free tier** for new organisations | No | Auto-stop optional on paid Machines | Yes (2 h / 7 day trial without) | **No hard cap**; overages billed |
| Cloud Run | Request-based service allowance exists; no always-on free guarantee | Jobs: 240,000 vCPU-s + 450,000 GiB-s monthly at Tier 1 pricing; minimum 1 minute billed per instance | Service scales to zero; Jobs run then exit; persistent worker pools bill while idle | Billing account required | Eligible service spend caps exist, but enforcement is delayed and overages can still be billed |

Sources: [Render free](https://render.com/docs/free), [Render plans](https://render.com/docs/compute-plans),
[Koyeb instances](https://www.koyeb.com/docs/reference/instances), [Koyeb pricing FAQ](https://www.koyeb.com/docs/faqs/pricing),
[Fly pricing](https://fly.io/docs/about/pricing/), [Fly cost management](https://fly.io/docs/about/cost-management/),
[Cloud Run pricing](https://cloud.google.com/run/pricing),
[Cloud Run timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout),
[Cloud Run worker pools](https://docs.cloud.google.com/run/docs/resource-model),
[Cloud Run spend caps](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps).

## What the product actually requires

From the code and the Phase 03–10 evidence, the public English path needs:

| Requirement | Value | Consequence |
|---|---|---|
| HTTP API | FastAPI, long-lived DB pool, SSE-capable | Any container host; serverless needs a DB pooler |
| Worker | `asset_verify` + `analyze_samples` + `compile_export`, 300 s window, 768 MiB admission | Scheduled/event-driven execution is a candidate; actual cold start and hosted resource limits remain untested |
| Database | PostgreSQL 16, `SKIP LOCKED`, transaction-local `set_config`, migrations | Neon or Supabase both work in principle |
| Object storage | Private, immutable, short-lived signed PUT/GET, checksums, inventory | **No cloud adapter exists yet** — the blocking code gap |
| Backups | Database + objects + erasure journal, expiry, RPO/RTO and reconciliation | Off-site logical dumps can satisfy the gate; Neon also offers bounded 6 h history |
| Quota tier | Validated rules for `free_guest` and `free_user` | Example and explicit provisioning command exist; a fresh database intentionally has no automatic limits |

## Identity and attestation compatibility

Firebase's [pricing plans](https://firebase.google.com/docs/projects/billing/firebase-pricing-plans)
include most Authentication methods and App Check at no charge on Spark, with no payment
information required for Spark. Anonymous sign-in remains subject to [Auth limits](https://firebase.google.com/docs/auth/limits),
including per-IP account creation limits and different quotas if upgraded to Identity
Platform. No phone/SMS plan is assumed. Guest identity does not bypass Penforge's own
creation and resource limits.

[App Check](https://firebase.google.com/docs/app-check) is separate from user identity.
Web reCAPTCHA Enterprise has 10,000 no-cost assessments monthly, then provider costs;
app-check token caching and refresh must be budgeted. Hosted Firebase, shared rate
limiting and App Check integration remain unverified in this application. Linking Cloud
Run billing can change the Firebase project's plan; evaluate the complete billing project.

## Decision state

Neon and Supabase PostgreSQL remain candidates. Neon's bounded history is useful;
Supabase can meet the backup requirement with separately scheduled off-site logical
exports and measured restoration. No provider is selected by this comparison.

Supabase Storage Free is a candidate with documented restrictions at exhaustion.
R2 has billable overages; do not infer an exact hard stop from notifications. No cloud
adapter has been implemented or admitted by conformance.

A complete ₹0 deployment is **unproven**. Neon's polling example exceeds its monthly
compute allowance; that arithmetic says nothing about an event-driven topology using a
different database. Cloud Run Jobs have a free allowance but require billing and may
incur scheduler, build, registry, network, monitoring and backup charges. Their allowance
is aggregated per billing account, not per app. Ten scheduled minutes daily is only
five nominal execution-hours; DB wake tails, API requests and monitoring must be added.

[Google's spend-cap documentation](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps)
explicitly permits delayed enforcement and overages. A configured cap is risk control,
not a ₹0 guarantee. The next hosting decision needs an entire topology, regional
measurements and a demonstrated overage policy; a missing always-on worker plan does
not prove that every possible free deployment is impossible.
