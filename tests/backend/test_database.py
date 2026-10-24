"""Real PostgreSQL baseline acceptance. No SQLite stand-in is supported.

TEST_DATABASE_URL must address a disposable local/CI database with schema and
role creation rights. Tests use a fresh isolated schema and remove it afterwards.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.db import models as m
from app.db.revision import EXPECTED_MIGRATION_HEAD
from app.db.session import normalize_database_url
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration
HASH = bytes(range(32))


def migrate(engine: Engine, direction: str, revision: str) -> None:
    config = Config(str(ROOT / "infrastructure/migrations/alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        getattr(command, direction)(config, revision)


@pytest.fixture(scope="module")
def database() -> Iterator[Engine]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is required for real PostgreSQL acceptance")
    admin = sa.create_engine(normalize_database_url(database_url))
    schema = "phase00_test_" + uuid.uuid4().hex
    with admin.begin() as connection:
        version = int(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
        assert version >= 150000, "PostgreSQL 15+ is required for NULLS NOT DISTINCT"
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = sa.create_engine(
        normalize_database_url(database_url), connect_args={"options": f"-csearch_path={schema}"}
    )
    try:
        migrate(engine, "upgrade", "head")
        migrate(engine, "downgrade", "base")
        with engine.connect() as connection:
            assert sa.inspect(connection).get_table_names() == ["alembic_version"]
        migrate(engine, "upgrade", "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


@pytest.fixture
def db(database: Engine) -> Iterator[Connection]:
    with database.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()


def insert(db: Connection, table: sa.Table, **values: Any) -> uuid.UUID:
    identity = values.setdefault("id", uuid.uuid4())
    db.execute(table.insert().values(**values))
    return identity


def owner(db: Connection) -> uuid.UUID:
    return insert(db, m.users, firebase_uid=uuid.uuid4().hex)


def project(db: Connection, user: uuid.UUID | None = None) -> uuid.UUID:
    return insert(
        db, m.projects, owner_user_id=user or owner(db), name="Foundation", script_code="Latn"
    )


def glyph(db: Connection, project_id: uuid.UUID, codepoints: list[int] | None = None) -> uuid.UUID:
    return insert(db, m.glyphs, project_id=project_id, codepoints=codepoints or [97])


def revision(
    db: Connection, glyph_id: uuid.UUID, user: uuid.UUID | None = None, **extra: Any
) -> uuid.UUID:
    values: dict[str, Any] = {
        "glyph_id": glyph_id,
        "seq": 1,
        "origin": "user_capture",
        "approval": "pending",
        "outline_inline": {"contours": []},
        "content_hash": HASH,
    }
    if user is not None:
        values.update(approval="approved", approved_by_user_id=user, approved_at=datetime.now(UTC))
    values.update(extra)
    return insert(db, m.glyph_revisions, **values)


def asset(db: Connection, project_id: uuid.UUID, user: uuid.UUID, **extra: Any) -> uuid.UUID:
    values: dict[str, Any] = {
        "project_id": project_id,
        "owner_user_id": user,
        "kind": "original_upload",
        "storage_backend": "local_fs",
        "storage_key": uuid.uuid4().hex,
        "mime_type": "image/png",
        "size_bytes": 4,
        "retention_class": "original",
    }
    values.update(extra)
    return insert(db, m.assets, **values)


def ready_asset(db: Connection, project_id: uuid.UUID, user: uuid.UUID, **extra: Any) -> uuid.UUID:
    return asset(
        db, project_id, user, state="ready", sha256=HASH, verified_at=datetime.now(UTC), **extra
    )


def snapshot(db: Connection, project_id: uuid.UUID, **extra: Any) -> uuid.UUID:
    values = {
        "project_id": project_id,
        "kind": "export",
        "manifest": {"glyphs": {}},
        "manifest_sha256": HASH,
    }
    values.update(extra)
    return insert(db, m.project_snapshots, **values)


def flush(db: Connection) -> None:
    db.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    db.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")


def rejects(db: Connection, operation: Callable[[], Any], code: str | None = None) -> None:
    with pytest.raises(DBAPIError) as failure, db.begin_nested():
        operation()
        flush(db)
    if code:
        assert code in str(failure.value.orig)


def test_migration_cycle_and_metadata_drift(database: Engine) -> None:
    # The fixture executed actual upgrade -> downgrade -> upgrade before this check.
    with database.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
            == EXPECTED_MIGRATION_HEAD
        )
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        assert compare_metadata(context, m.metadata) == []
        # 27 lineage tables plus account project workspaces and Writing Desk pages.
        tables = set(sa.inspect(connection).get_table_names()) - {"alembic_version"}
        assert len(tables) == 29
        assert {"project_workspaces", "writing_documents"} <= tables


@pytest.mark.parametrize(
    "codepoints", [[-1], [1114112], [55296], [57343], [None], [], [[97]], [97] * 17]
)
def test_unicode_arrays_reject_non_scalar_or_non_vector_values(
    db: Connection, codepoints: list[Any]
) -> None:
    pid = project(db)
    rejects(db, lambda: insert(db, m.glyphs, project_id=pid, codepoints=codepoints))


def test_unicode_arrays_accept_devanagari_and_non_bmp(db: Connection) -> None:
    pid = project(db)
    glyph(db, pid, [0x915, 0x94D, 0x937])
    glyph(db, pid, [0x1F642])
    flush(db)


def test_generated_pending_cannot_activate_and_owner_approval_can(db: Connection) -> None:
    user = owner(db)
    gid = glyph(db, project(db, user))
    rid = revision(db, gid, origin="ml_generated")
    rejects(
        db,
        lambda: db.execute(
            m.glyphs.update().where(m.glyphs.c.id == gid).values(active_revision_id=rid)
        ),
        "active_revision",
    )
    db.execute(
        m.glyph_revisions.update()
        .where(m.glyph_revisions.c.id == rid)
        .values(approval="approved", approved_by_user_id=user, approved_at=datetime.now(UTC))
    )
    db.execute(m.glyphs.update().where(m.glyphs.c.id == gid).values(active_revision_id=rid))
    flush(db)
    assert (
        db.execute(
            sa.select(m.glyphs.c.active_revision_id).where(m.glyphs.c.id == gid)
        ).scalar_one()
        == rid
    )


def test_generated_revision_cannot_skip_pending(db: Connection) -> None:
    user = owner(db)
    gid = glyph(db, project(db, user))
    rejects(db, lambda: revision(db, gid, user, origin="ml_generated"), "generated_revision")


def test_cross_glyph_active_revision_and_wrong_approver_fail(db: Connection) -> None:
    user = owner(db)
    pid = project(db, user)
    first, second = glyph(db, pid), glyph(db, pid, [98])
    rid = revision(db, first, user)
    rejects(
        db,
        lambda: db.execute(
            m.glyphs.update().where(m.glyphs.c.id == second).values(active_revision_id=rid)
        ),
        "active_revision",
    )
    rejects(db, lambda: revision(db, second, owner(db)), "owner_approval")


@pytest.mark.parametrize(
    "changes",
    [
        {"outline_inline": {"contours": ["changed"]}},
        {"metrics": {"advance": 44}},
        {"content_hash": b"x" * 32},
        {"origin": "ml_generated"},
    ],
)
def test_revision_geometry_and_provenance_immutable_while_pending(
    db: Connection, changes: dict[str, Any]
) -> None:
    rid = revision(db, glyph(db, project(db)))
    rejects(
        db,
        lambda: db.execute(
            m.glyph_revisions.update().where(m.glyph_revisions.c.id == rid).values(**changes)
        ),
        "immutable",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"approval": "rejected", "approved_by_user_id": None, "approved_at": None},
        {"approved_at": datetime(2020, 1, 1, tzinfo=UTC)},
    ],
)
def test_approved_review_metadata_is_terminal(db: Connection, changes: dict[str, Any]) -> None:
    user = owner(db)
    rid = revision(db, glyph(db, project(db, user)), user)
    rejects(
        db,
        lambda: db.execute(
            m.glyph_revisions.update().where(m.glyph_revisions.c.id == rid).values(**changes)
        ),
        "review_final",
    )


def test_revision_parent_same_glyph_and_increasing_sequence(db: Connection) -> None:
    pid = project(db)
    g1, g2 = glyph(db, pid), glyph(db, pid, [98])
    r1 = revision(db, g1)
    rejects(db, lambda: revision(db, g2, parent_revision_id=r1), "parent_lineage")
    r2 = revision(db, g1, seq=2, parent_revision_id=r1)
    flush(db)
    rejects(db, lambda: revision(db, g1, seq=3, parent_revision_id=r2, id=r2))


@pytest.mark.parametrize("field", ["content_hash", "manifest_sha256", "sha256", "request_hash"])
def test_checksum_lengths(db: Connection, field: str) -> None:
    user = owner(db)
    pid = project(db, user)
    if field == "content_hash":
        rejects(db, lambda: revision(db, glyph(db, pid), content_hash=b"short"))
    elif field == "manifest_sha256":
        rejects(db, lambda: snapshot(db, pid, manifest_sha256=b"short"))
    elif field == "sha256":
        rejects(db, lambda: asset(db, pid, user, sha256=b"short"))
    else:
        rejects(
            db,
            lambda: db.execute(
                m.idempotency_keys.insert().values(
                    user_id=user,
                    key="k",
                    request_hash=b"short",
                    state="in_progress",
                    expires_at=datetime.now(UTC),
                )
            ),
        )


def test_inline_geometry_budget_and_exclusive_source(db: Connection) -> None:
    gid = glyph(db, project(db))
    rejects(db, lambda: revision(db, gid, outline_inline={"data": "x" * 32768}))
    rejects(db, lambda: revision(db, gid, outline_inline=None))


def test_snapshots_validate_lineage_and_are_immutable(db: Connection) -> None:
    user = owner(db)
    pid = project(db, user)
    gid = glyph(db, pid)
    rid = revision(db, gid, user)
    sid = snapshot(db, pid, manifest={"glyphs": {str(gid): str(rid)}})
    flush(db)
    rejects(
        db,
        lambda: db.execute(
            m.project_snapshots.update()
            .where(m.project_snapshots.c.id == sid)
            .values(label="changed")
        ),
        "snapshot_immutable",
    )
    rejects(
        db,
        lambda: db.execute(m.project_snapshots.delete().where(m.project_snapshots.c.id == sid)),
        "snapshot_immutable",
    )
    rejects(
        db,
        lambda: db.execute(m.glyph_revisions.delete().where(m.glyph_revisions.c.id == rid)),
        "snapshot_referenced",
    )
    rejects(
        db,
        lambda: snapshot(db, project(db, user), manifest={"glyphs": {str(gid): str(rid)}}),
        "snapshot_revision",
    )


def test_pending_revision_cannot_enter_snapshot(db: Connection) -> None:
    pid = project(db)
    gid = glyph(db, pid)
    rid = revision(db, gid)
    rejects(
        db,
        lambda: snapshot(db, pid, manifest={"glyphs": {str(gid): str(rid)}}),
        "snapshot_revision",
    )


def test_active_snapshot_belongs_to_same_project(db: Connection) -> None:
    first, second = project(db), project(db)
    sid = snapshot(db, first)
    rejects(
        db,
        lambda: db.execute(
            m.projects.update().where(m.projects.c.id == second).values(active_snapshot_id=sid)
        ),
        "snapshot_project",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"storage_key": "replacement"},
        {"sha256": b"x" * 32},
        {"size_bytes": 100},
        {"mime_type": "image/jpeg"},
        {"is_immutable": False},
        {"verified_at": None},
    ],
)
def test_ready_asset_content_cannot_change(db: Connection, changes: dict[str, Any]) -> None:
    user = owner(db)
    aid = ready_asset(db, project(db, user), user)
    rejects(
        db,
        lambda: db.execute(m.assets.update().where(m.assets.c.id == aid).values(**changes)),
        "immutable",
    )
    db.execute(m.assets.update().where(m.assets.c.id == aid).values(state="deleting"))
    rejects(
        db,
        lambda: db.execute(m.assets.update().where(m.assets.c.id == aid).values(**changes)),
        "immutable",
    )


def test_owner_consistency_and_atomic_claim(db: Connection) -> None:
    first, second = owner(db), owner(db)
    pid = project(db, first)
    rejects(db, lambda: asset(db, pid, second), "owner_mismatch")
    aid = asset(db, pid, first)
    jid = insert(
        db,
        m.jobs,
        project_id=pid,
        owner_user_id=first,
        job_type="asset_verify",
        payload={},
        payload_schema_version=1,
    )
    flush(db)
    rejects(
        db,
        lambda: db.execute(
            m.projects.update().where(m.projects.c.id == pid).values(owner_user_id=second)
        ),
        "owner_mismatch",
    )
    db.execute(m.projects.update().where(m.projects.c.id == pid).values(owner_user_id=second))
    db.execute(m.assets.update().where(m.assets.c.id == aid).values(owner_user_id=second))
    db.execute(m.jobs.update().where(m.jobs.c.id == jid).values(owner_user_id=second))
    flush(db)


def test_samples_cannot_reference_another_projects_asset(db: Connection) -> None:
    user = owner(db)
    first, second = project(db, user), project(db, user)
    aid = asset(db, first, user)
    rejects(
        db,
        lambda: insert(
            db, m.samples, project_id=second, asset_id=aid, source_type="canvas_drawing"
        ),
        "sample_asset",
    )


def test_user_merge_cycles_rejected(db: Connection) -> None:
    first, second = owner(db), owner(db)
    db.execute(m.users.update().where(m.users.c.id == first).values(merged_into_user_id=second))
    rejects(
        db,
        lambda: db.execute(
            m.users.update().where(m.users.c.id == second).values(merged_into_user_id=first)
        ),
        "merge_cycle",
    )


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "canceled"])
def test_terminal_jobs_cannot_restart(db: Connection, terminal: str) -> None:
    jid = insert(db, m.jobs, job_type="maintenance_reconcile", payload={}, payload_schema_version=1)
    if terminal == "canceled":
        db.execute(m.jobs.update().where(m.jobs.c.id == jid).values(state=terminal))
    else:
        db.execute(m.jobs.update().where(m.jobs.c.id == jid).values(state="running"))
        db.execute(m.jobs.update().where(m.jobs.c.id == jid).values(state=terminal))
    rejects(
        db,
        lambda: db.execute(m.jobs.update().where(m.jobs.c.id == jid).values(state="queued")),
        "job_state_transition",
    )


@pytest.mark.parametrize("final", ["ready", "failed"])
def test_final_exports_immutable_and_cross_project_refs_rejected(
    db: Connection, final: str
) -> None:
    user = owner(db)
    pid, other = project(db, user), project(db, user)
    sid = snapshot(db, pid)
    values: dict[str, Any] = {
        "project_id": pid,
        "snapshot_id": sid,
        "requested_by_user_id": user,
        "format": "ttf",
        "family_name": "Foundation",
        "version_string": "1.0",
        "status": final,
        "finalized_at": datetime.now(UTC),
    }
    if final == "ready":
        values.update(
            asset_id=ready_asset(db, pid, user, kind="export"),
            validation_status="passed_with_warnings",
        )
    eid = insert(db, m.font_exports, **values)
    flush(db)
    rejects(
        db,
        lambda: db.execute(
            m.font_exports.update().where(m.font_exports.c.id == eid).values(family_name="Changed")
        ),
        "final_export",
    )
    rejects(
        db,
        lambda: db.execute(m.font_exports.delete().where(m.font_exports.c.id == eid)),
        "final_export",
    )
    rejects(
        db,
        lambda: insert(
            db,
            m.font_exports,
            **(values | {"project_id": other, "asset_id": None, "status": "pending"}),
        ),
        "snapshot_mismatch",
    )


def test_project_cascade_handles_revision_snapshot_export_cycles(db: Connection) -> None:
    user = owner(db)
    pid = project(db, user)
    aid = ready_asset(db, pid, user, kind="export")
    gid = glyph(db, pid)
    rid = revision(db, gid, user)
    sid = snapshot(db, pid, manifest={"glyphs": {str(gid): str(rid)}})
    db.execute(m.glyphs.update().where(m.glyphs.c.id == gid).values(active_revision_id=rid))
    db.execute(m.projects.update().where(m.projects.c.id == pid).values(active_snapshot_id=sid))
    insert(
        db,
        m.font_exports,
        project_id=pid,
        snapshot_id=sid,
        requested_by_user_id=user,
        format="ttf",
        family_name="Foundation",
        version_string="1",
        status="ready",
        asset_id=aid,
        validation_status="passed",
        finalized_at=datetime.now(UTC),
    )
    flush(db)
    rejects(db, lambda: db.execute(m.users.delete().where(m.users.c.id == user)))
    db.execute(m.projects.delete().where(m.projects.c.id == pid))
    flush(db)
    for table in (
        m.projects,
        m.assets,
        m.glyphs,
        m.glyph_revisions,
        m.project_snapshots,
        m.font_exports,
    ):
        assert db.execute(sa.select(sa.func.count()).select_from(table)).scalar_one() == 0
    db.execute(m.users.delete().where(m.users.c.id == user))
    flush(db)


def test_active_contribution_requires_separate_owner_consent(db: Connection) -> None:
    user = owner(db)
    pid = project(db, user)
    aid = asset(db, pid, user)
    sample = insert(db, m.samples, project_id=pid, asset_id=aid, source_type="canvas_drawing")
    rejects(
        db,
        lambda: insert(db, m.dataset_contributions, sample_id=sample, contributor_ref=HASH),
        "owner_consent",
    )
    consent = insert(
        db, m.training_consents, user_id=user, purpose="model_training", policy_version="1"
    )
    contribution = insert(
        db, m.dataset_contributions, sample_id=sample, contributor_ref=HASH, consent_id=consent
    )
    flush(db)
    rejects(
        db,
        lambda: db.execute(
            m.training_consents.update()
            .where(m.training_consents.c.id == consent)
            .values(withdrawn_at=datetime.now(UTC))
        ),
        "owner_consent",
    )
    # Withdrawal must remove active dataset eligibility atomically; no model-unlearning claim.
    db.execute(
        m.training_consents.update()
        .where(m.training_consents.c.id == consent)
        .values(withdrawn_at=datetime.now(UTC))
    )
    db.execute(
        m.dataset_contributions.update()
        .where(m.dataset_contributions.c.id == contribution)
        .values(status="withdrawn", withdrawn_at=datetime.now(UTC))
    )
    flush(db)


def test_global_quota_null_identity_is_unique(db: Connection) -> None:
    values = {
        "scope": "global",
        "resource": "exports",
        "window_kind": "lifetime",
        "window_start": datetime.now(UTC).date(),
        "limit_amount": 0,
    }
    insert(db, m.quota_balances, **values)
    rejects(db, lambda: insert(db, m.quota_balances, **values))


def test_audit_role_is_append_only(database: Engine) -> None:
    role = "phase00_app_" + uuid.uuid4().hex
    with database.begin() as connection:
        schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
        connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        connection.exec_driver_sql(
            f'GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON audit_events TO "{role}"'
        )
        connection.exec_driver_sql(f'REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM "{role}"')
        connection.exec_driver_sql(f'GRANT USAGE ON SEQUENCE audit_events_id_seq TO "{role}"')
        connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
        connection.execute(
            m.audit_events.insert().values(
                actor_ref="system",
                action="foundation.test",
                resource_type="foundation",
                outcome="success",
            )
        )
        assert connection.exec_driver_sql("SELECT count(*) FROM audit_events").scalar_one() == 1
        for operation in (
            "UPDATE audit_events SET action='changed'",
            "DELETE FROM audit_events",
            "TRUNCATE audit_events",
        ):
            rejects(
                connection,
                lambda sql=operation: connection.exec_driver_sql(sql),
                "permission denied",
            )
        connection.exec_driver_sql("RESET ROLE")
        for permission in ("UPDATE", "DELETE", "TRUNCATE"):
            assert not connection.execute(
                sa.text("SELECT has_table_privilege(:role,'audit_events',:permission)"),
                {"role": role, "permission": permission},
            ).scalar_one()
        connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
        connection.exec_driver_sql(f'DROP ROLE "{role}"')
        # Trigger also prevents accidental owner/migration-role mutation.
        rejects(
            connection,
            lambda: connection.exec_driver_sql("DELETE FROM audit_events"),
            "audit_append_only",
        )


@pytest.mark.parametrize(
    ("query", "index"),
    [
        (
            "SELECT id FROM jobs WHERE state='queued' ORDER BY priority DESC,run_after,created_at LIMIT 1 FOR UPDATE SKIP LOCKED",
            "jobs_claim_idx",
        ),
        (
            "SELECT id FROM jobs WHERE state IN ('running','cancel_requested') AND lease_expires_at < now()",
            "jobs_lease_idx",
        ),
        (
            "SELECT id FROM projects WHERE owner_user_id='00000000-0000-0000-0000-000000000001' ORDER BY updated_at DESC, id DESC",
            "projects_owner_cursor_idx",
        ),
    ],
)
def test_operational_query_indexes_are_reachable(db: Connection, query: str, index: str) -> None:
    # Small-table planner choices are not throughput evidence; demonstrate usable indexes only.
    db.exec_driver_sql("SET LOCAL enable_seqscan=off")
    plan = "\n".join(db.exec_driver_sql("EXPLAIN " + query).scalars())
    assert index in plan
