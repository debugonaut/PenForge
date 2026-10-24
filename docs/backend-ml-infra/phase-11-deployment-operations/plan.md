# Phase 11 — Deployment feasibility, operations and public release

Status: **portable implementation repaired locally 2026-10-21; full phase outcome and public launch remain incomplete**. No provider account, credential, hosted run or public launch. See [evidence](evidence.md) and [audit repairs](audit-repairs.md). Source: Part B infrastructure decisions, §§7.9, 10–12; Part A OPS-07–09.  
Starts with phase 00 local tooling/research. M1 public launch requires phases 00–07 plus this launch gate; M2/M3/M4 have additional capability-specific gates.

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md) and project handoff (`../../../PROJECT_STATUS.md`; local record) before implementation. Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) when integrating the frontend.

## Outcome

A publicly available, resource-capped English foundation whose hosting, backup and security claims are supported by current provider terms and measured workloads. Optional personal/self-hosted advanced workers never become a prerequisite for the public English flow.

## Work packages

1. Resolve Infra Q7 with the product owner before production configuration: local/ephemeral staging/production separation, secrets source and rotation, API↔worker↔DB/storage service identity, role boundaries, environments and promotion. Preserve Q8–Q10 as undefined until discussed. No account creation, resource purchase, public exposure or credential grant is implied by writing this plan.
2. Verify candidate provider terms from official primary sources at implementation time: free eligibility, billing/card prerequisites, inactivity sleep, region/availability, CPU/RAM/runtime, queue/scheduler options, database/storage/egress, signed URLs, backup/restore, usage-limit behavior and prevention of paid overages. Record date/plan/source. Supabase/Neon and Supabase Storage/R2 are candidates, not selections.
3. Benchmark PostgreSQL candidates with the same schema/data/client region: pooling/connection behavior, cold start/wake, claim latency at 1/5/20 workers, heartbeat writes, strict caps/advisory locks/SKIP LOCKED through the pooler, storage limits and restore. Record raw runs, p50/p95, hardware/config and sample count, not estimates presented as measurements.
4. Run storage conformance suite for selected candidates: scoped signing, size constraints, immutable/atomic writes, checksum/metadata, access expiry, listing/deletion, resumability if available, dedupe and deletion/claim inventory. Implement only the selected adapter after evidence review.
5. Benchmark representative verify/extract/compile jobs on available cloud runtimes: cold starts, duration window, peak RSS/CPU, temp disk, font/decode dependencies, persistent versus scheduled execution, checkpoint survival and scheduler reliability. Benchmark SSE limits while preserving polling. Model and quality gates remain unchanged by hosting limits.
6. Write a resource budget from measured user/storage/compute/egress requests plus global safety margin and configured ceilings. Verify the public English workload can run without an always-on personal machine. If actual free limits cannot support it, record a deployment feasibility blocker; do not claim ₹0 works or silently switch to paid/autoscaling billing.
7. Implement least-privilege service roles and secret injection after Q7 selection. Optional advanced self-hosted workers advertise capability and receive only scoped job assets via controlled proxy/capabilities where feasible. Direct broad cross-user DB/storage credentials are not an acceptable convenience. Test compromised-worker access to unrelated assets.
8. Add observability for API errors/latencies, queue age/depth, leases, retries, worker resource use, reserved/committed quotas, stored bytes, transfer, validation and deletion/consent failures. Derive alert thresholds from budgets; logs remain content/token/URL free. Low-risk health stays public; readiness/metrics/admin operations are restricted.
9. Finish CI/CD gates: frontend/API lint/type/unit, PostgreSQL+Firebase emulator integration, memory-capped ML smoke, OpenAPI/job-schema compatibility, migration compatibility/drift, image dependency/size budgets and security checks. Deploy compatible workers before API schema emission; use expand→migrate→contract and verified backups before destructive steps.
10. Rehearse staging smoke, migration failure, worker loss, provider quota exhaustion, deploy rollback, database/object restore and deletion-ledger reconciliation. Backups have documented expiry and restore objectives measured by drills. A restore must not resurrect access to deleted data or withdrawn contributions.
11. Produce release evidence, capability labels, operator runbooks and truthful product status. Manual production promotion follows the authorized release process. Enable cloud integration gradually; preserve local recovery and an explicit demo mode.

## Public M1 launch gate

- Firebase identity/claim, owner isolation, guest creation boundary and abuse/resource caps pass.
- A representative structured English sample reaches reviewed glyphs and valid TTF/OTF/WOFF2 in public cloud without a personal machine dependency.
- Partial coverage is accurate; advanced cursive/Devanagari/generated glyph claims match their actual independent gates.
- Chaos recovery, fencing, quota concurrency, upload safety and export immutability pass on the selected runtime.
- Consent/deletion/retention and restore reconciliation pass, including historical guest-owned assets and in-flight jobs.
- Current provider terms and hard-overage protections support the approved budget; unresolved ₹0 feasibility is stated explicitly if not proven.
- Polling works when SSE is unavailable. No fake progress/ETA; benchmark profile matches the runtime and input class.
- Secrets/roles/monitoring/backup ownership and incident contacts are documented; user data never enters operational logs.

## Evidence, rollout and rollback

Deliver provider comparison with dated primary-source links, raw benchmark JSON, resource budget, deployment/runbook files, restore drill and launch checklist with executed evidence. Plans do not substitute for any of these checks. Rollback uses compatible immutable images/model pointers and feature flags, drains jobs without losing checkpoints and preserves old exports. Database rollback follows the tested expand-contract/restore procedure; never automatically run destructive downgrade in production.

## Open gates

Q7–Q10, provider/region selection, current free terms, billing protections, measured throughput, backup retention and actual worker availability all remain pending. Resolve them through evidence and product-owner decisions before provisioning; no provider has been selected by this plan.
