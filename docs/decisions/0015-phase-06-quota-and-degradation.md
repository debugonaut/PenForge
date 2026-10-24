# 0015 — Versioned quotas and accuracy-preserving admission

Status: accepted for local Phase 06. Date: 2026-10-20.

Queue policy lives in system_settings queue_policy.v1 with a configuration version.
Optional quota_tiers.vN records provision explicit user/global resource balances for new
windows. Existing explicitly provisioned balances remain valid. Missing budgets fail closed;
no production limits are seeded. Test values are fixtures, not product quotas.

Verification/analysis reserve bounded compute in the job-creation transaction in addition
to their storage reservations. Export retains compute, format and storage reservations.
All balance rows for the owner/global scope are locked in stable ID order before resource
operations. Settlement commits measured usage and releases unused amounts once. Usage above
the reservation prevents success publication; automatic top-ups are not enabled. Terminal
cleanup commits no more than the reserved cap and records actual usage and the uncommitted
overage explicitly, so reaping can finish without violating a hard cap. Killed attempt usage
is unmeasured and must not be fabricated. This is not proof of provider billing accuracy.

Levels 1–2 can disable optional capabilities (none newly implemented here); level 3 reduces
owner concurrency, and level 4 rejects new job admission with Retry-After. Existing jobs,
reads, corrections, approvals and validated downloads continue. No level switches the
extractor/compiler or skips validation. A cheaper algorithm still requires independent
qualification for the same input class. ETA remains insufficient_data without a matching
script/style/model/hardware/input benchmark. Public limits/providers remain unresolved.
