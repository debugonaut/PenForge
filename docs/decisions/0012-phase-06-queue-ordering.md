# 0012 — One queue, atomic caps and owner rotation

Status: accepted for local Phase 06. Date: 2026-10-20.

The existing PostgreSQL jobs table and v1 payloads remain authoritative. All three
handlers take advisory transaction lock 70606001 before user/project/job row locks.
The count check and actual claim share that transaction, including across job types.
Expired leases still consume a slot until reaped. No broker or replacement runner exists.

Order is bounded server priority with a saturating age boost, fewest running owner jobs,
least recently dispatched owner, eligibility time, creation time and ID. The dispatch tie
break prevents a single worker from draining one owner's old backlog before serving
others. Last heartbeat records dispatch history without another queue. Callers cannot
supply priority. Claim filters type, v1 schema, Latin script, memory and recognized quality/
resource/result profiles. Unsupported payloads stay queued. The dispatcher chooses across
all three types, then rechecks eligibility/caps in the handler's claim transaction.

Local defaults of four global/two owner slots are configurable safety bounds, not a
production quota or measured safe hosting capacity. The reports use explicit fixtures
with four global/one owner slots and 1, 5, 20 simultaneous database claimers. Hosted
pooler behavior, capacity and service identity remain Phase 11 gates.
