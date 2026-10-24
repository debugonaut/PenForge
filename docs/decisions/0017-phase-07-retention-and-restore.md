# 0017 — Conservative retention and independent erasure reconciliation

Status: accepted for local Phase 07. Date: 2026-10-20.

`retention_policy.v1` records explicit local defaults from v3 Part B §7.7: pending
uploads/staged publications/checkpoints 24 hours, unused derived assets 30 days,
idempotency receipts 24 hours, terminal job events 90 days, audit 365 days. These are
configurable retention choices, not provider backup or legal-compliance claims.
`assets.last_used_at` records actual capability issuance. Existing assets backfill their
creation time. Immutable source, result, candidate, outline, snapshot and export lineage
is conservatively preserved while referenced. Unreadable referenced manifests preserve
that scope instead of guessing that children are unused.

The operator first saves a hash-checked dry-run report. Apply rechecks current policy,
owner/project locks and every reference; only the intersection remains eligible. Known
objects are individually deleted and checked missing; metadata stays as deleted lineage.
Storage accounting settles once. Unknown orphan objects are reported, never purged.
Expired audit records use a dedicated SECURITY DEFINER routine; ordinary app audit
UPDATE/DELETE privileges remain revoked and the append-only trigger remains active.

Consent/identity/claim/deletion admission share ownership advisory lock 70707001 before
user locks, retaining stable UUID row ordering. The existing queue claim cap lock remains
70606001 and is acquired before user/job rows. This deliberately conservative local
coordination avoids ancestry/claim races; its hosted contention is unmeasured.

Erasure journal entries contain only HMAC targets, related identity HMACs, scope, opaque
operation ID and acceptance timestamp, with an integrity MAC. They precede database
acceptance completion so a crash cannot resurrect access. A failed database response may
therefore leave a durably authorized erasure journal entry; restore reconciliation resumes
that accepted intent. The journal must survive database restore and its HMAC key must be
stable. A missing journal or restored older journal is an unresolved operational restore
gate; no provider backup guarantee follows from a local fixture.

Contribution-only restore applies only to grants that existed at acceptance. It must not
withdraw later, explicitly granted consent or remove later contributions. Dataset version
selection is transactional and immutable; withdrawal excludes new versions, while past
version lineage/training influence is disclosed honestly. No Phase 07 training exists.

A scoped owner-issued status receipt permits content-free progress after Firebase is
removed. The receipt is a 256-bit HMAC capability, sent in a header, never a URL, log or
retained evidence body. Account/project/raw Firebase targets and consent IDs are cleared
from completed requests. The independent HMAC journal is retained for restore safety.
