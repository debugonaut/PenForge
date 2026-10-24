# 0013 — Verified immutable recovery units

Status: accepted for local Phase 06. Date: 2026-10-20.

A tagged canonical hash covers stage, source hashes, pinned template, model (explicitly
null when absent), complete configuration, algorithm and actual dependency/code/lock
hashes. Analysis checkpoints one sample; verification checkpoints its strict decoder
output; export checkpoints each fully compiled/validated format. A changed upstream hash
changes the unit identity. No attempt-specific result manifest is reused as a new attempt.
The worker reconstructs a new fenced result manifest from verified stage units.

Reserve/register pending object ledgers first, write immutable outputs, write the manifest,
verify bytes, then publish a job/owner/project/holder/attempt/lease-fenced checkpoint row.
The immutable reference has a deferred commit guard. Reads validate ownership, complete
manifest schema, media, sizes, hashes and dependency identity. Hashes are integrity evidence,
not authorization or worker attestation. A corrupted accepted checkpoint fails closed;
operators retain evidence and must investigate instead of silently trusting its key.

Recovery units have a configurable reuse TTL. Accepted historical objects remain inventoried
and charged; this phase does not erase accepted history. Pending crash output is reconciled
and cleaned under its job fence; unknown orphan storage is dry-run only. General historical
retention/deletion remains Phase 07. Rollback retains checkpoints and terminal records.
