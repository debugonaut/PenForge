# 0002 — Ratify and strengthen the PostgreSQL baseline

Status: accepted. Date: 2026-10-19. Scope: Phase 00 schema only.

## Context

[v3 Part B §§7.3–7.8](../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md)
proposes DDL. Part A IC-05 and §11 require preservation of immutable lineage and controlled
promotion. The illustrative foreign keys do not alone guarantee same-glyph activation,
owner consistency or explicit generated approval.

## Decision

Migration `0001_initial` retains all 21 baseline tables and PostgreSQL-native JSONB,
Unicode scalar arrays, deferred cyclic pointers and partial indexes. SQLAlchemy Core
metadata is the corresponding model definition. Frozen migration SQL does not import
live metadata; future changes require additive Alembic revisions.

Add `approved_by_user_id` and `approved_at` to revisions. Generated revisions must begin
pending. Activation requires an approved revision belonging to the same glyph. Approval
records the current project owner; the DB does not authenticate the caller. Future
services must derive that owner from verified identity, and worker credentials must lack
promotion privileges. These authorization services/roles are later-phase work.

Revision geometry, metrics, hashes and lineage are immutable from creation; pending review
may transition once to approved/rejected. Snapshot-referenced revisions cannot be pruned.
Snapshots reject updates and direct deletion; project cascade is permitted. Final exports
reject changes, except the source's `ON DELETE SET NULL` for a deleted job. Ready asset
content stays immutable through deletion states. Jobs cannot leave terminal states.
Scalar validity, array dimensions, checksum lengths and inline outline size are checked.
Deferred reference guards enforce project/child owner consistency while permitting atomic
account ownership reassignment. Withdrawing consent and contribution exclusion must occur
atomically; withdrawal cannot leave a contribution active under that consent.

Audit is append-only by trigger and a separately tested runtime-role REVOKE. Runtime
credentials must neither own tables nor inherit the migration owner. The grant helper
only grants to an already-created role; no deployment credential scheme is selected.

## Consequences and evidence

PostgreSQL 15+ is required (`NULLS NOT DISTINCT` index). Compose/CI use PostgreSQL 16.
Real local PostgreSQL 16.2 tests cover up/down/up, zero metadata drift, checks/guards,
ownership, deletion cycles, role permissions and planner indexes.
See database acceptance (`../../artifacts/phase-00/database-acceptance.md`; local record).
DB state transitions are a backstop; they do not implement the later queue, quota,
consent API, deletion saga or ownership-scoped resource services.

Downgrade removes the initial schema and data. It is for disposable local databases only;
never run automatically in production. Later migrations use expand → migrate → contract,
with verified backups before destructive changes. PostgreSQL is never replaced by SQLite
for acceptance testing.
