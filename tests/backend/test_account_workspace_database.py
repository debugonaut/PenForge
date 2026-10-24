"""Real-PostgreSQL acceptance for durable working state and Writing Desk documents."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.adapters.auth.base import Principal
from app.core.errors import ApiError
from app.db import models as m
from app.db.session import normalize_database_url
from app.modules.documents.schemas import WritingDocumentCreate
from app.modules.documents.service import DocumentsService
from app.modules.identity.service import IdentityService
from app.modules.projects.service import ProjectsService
from app.modules.workspaces.schemas import WorkspaceDocument
from app.modules.workspaces.service import WorkspacesService
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def account_database() -> Iterator[tuple[Engine, str]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url is None:
        pytest.skip("TEST_DATABASE_URL is required for real PostgreSQL acceptance")
    normalized = normalize_database_url(url)
    admin = sa.create_engine(normalized)
    schema = "integration_workspace_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = sa.create_engine(normalized, connect_args={"options": f"-csearch_path={schema}"})
    with engine.begin() as connection:
        config = Config(str(ROOT / "infrastructure/migrations/alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    try:
        yield engine, schema
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def principal(uid: str | None = None) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        firebase_uid=uid or uuid.uuid4().hex,
        is_anonymous=True,
        sign_in_provider="anonymous",
        email_verified=False,
        auth_time=int(NOW.timestamp()),
        token_issued_at=int(NOW.timestamp()),
    )


def run(
    database: tuple[Engine, str],
    scenario: Callable[
        [Engine, AsyncEngine, ProjectsService, WorkspacesService, DocumentsService],
        Awaitable[None],
    ],
) -> None:
    def execute() -> None:
        sync_engine, schema = database
        engine = create_async_engine(
            sync_engine.url,
            connect_args={"options": f"-csearch_path={schema}"},
            pool_size=30,
        )
        identity = IdentityService(engine, clock=lambda: NOW)
        projects = ProjectsService(engine, identity, cursor_secret=b"w" * 32, clock=lambda: NOW)
        workspaces = WorkspacesService(engine, identity, clock=lambda: NOW)
        documents = DocumentsService(
            engine,
            identity,
            cursor_secret=b"w" * 32,
            clock=lambda: NOW,
        )

        async def work() -> None:
            try:
                await scenario(sync_engine, engine, projects, workspaces, documents)
            finally:
                await engine.dispose()

        asyncio.run(work())

    execute()


async def make_project(service: ProjectsService, owner: Principal, name: str) -> str:
    _, _, project = await service.create(
        owner,
        idempotency_key=f"create-{uuid.uuid4()}",
        name=name,
        script_code="Latn",
        style_controls={"thickness": 52.0},
    )
    return project["id"]


def test_migration_adds_only_bounded_draft_tables(account_database: tuple[Engine, str]) -> None:
    sync, _ = account_database
    with sync.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema() "
                    "AND table_name IN ('project_workspaces','writing_documents')"
                )
            )
        }
        assert tables == {"project_workspaces", "writing_documents"}
        # Draft payloads stay bounded in PostgreSQL, not only in Python.
        constraints = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conname IN "
                    "('ck_project_workspaces_size','ck_writing_documents_size')"
                )
            )
        }
        assert constraints == {
            "ck_project_workspaces_size",
            "ck_writing_documents_size",
        }


def test_workspace_etag_owner_scope_and_conflict(account_database: tuple[Engine, str]) -> None:
    owner_a, owner_b = principal(), principal()

    async def scenario(
        _sync: Engine,
        _async: AsyncEngine,
        projects: ProjectsService,
        workspaces: WorkspacesService,
        _documents: DocumentsService,
    ) -> None:
        project = await make_project(projects, owner_a, "Real account project")
        foreign = await make_project(projects, owner_b, "Someone else's")

        with pytest.raises(ApiError) as missing:
            await workspaces.get(owner_a, uuid.UUID(project))
        assert (missing.value.code, missing.value.status) == ("not_found", 404)

        with pytest.raises(ApiError) as unguarded:
            await workspaces.put(
                owner_a,
                uuid.UUID(project),
                if_match=None,
                workspace=WorkspaceDocument(),
            )
        assert (unguarded.value.code, unguarded.value.status) == (
            "precondition_required",
            428,
        )

        created = await workspaces.put(
            owner_a,
            uuid.UUID(project),
            if_match='"v0"',
            workspace=WorkspaceDocument(
                step="review",
                preview_text="A quiet thought.",
                review={"approved_glyph_ids": ["glyph-a"]},
                sample={
                    "kind": "draw",
                    "strokes": [[{"x": 0.1, "y": 0.2}, {"x": 0.3, "y": 0.4}]],
                },
            ),
        )
        assert created["row_version"] == 1
        assert created["workspace"]["step"] == "review"
        assert created["workspace"]["sample"]["strokes"][0][1]["y"] == 0.4

        with pytest.raises(ApiError) as duplicate:
            await workspaces.put(
                owner_a,
                uuid.UUID(project),
                if_match='"v0"',
                workspace=WorkspaceDocument(),
            )
        assert duplicate.value.code == "revision_conflict"

        updated = await workspaces.put(
            owner_a,
            uuid.UUID(project),
            if_match='"v1"',
            workspace=WorkspaceDocument(
                step="style",
                style={
                    "thickness": 61,
                    "slant": 4,
                    "spacing": 48,
                    "smoothness": 66,
                    "proportion": 52,
                },
                preview_text="Saved after a refresh.",
            ),
        )
        assert updated["row_version"] == 2

        with pytest.raises(ApiError) as stale:
            await workspaces.put(
                owner_a,
                uuid.UUID(project),
                if_match='"v1"',
                workspace=WorkspaceDocument(preview_text="Stale tab"),
            )
        assert stale.value.code == "revision_conflict"
        assert stale.value.context["current_row_version"] == 2

        with pytest.raises(ApiError) as hidden:
            await workspaces.get(owner_b, uuid.UUID(project))
        assert hidden.value.code == "not_found"
        with pytest.raises(ApiError) as hidden_write:
            await workspaces.put(
                owner_b,
                uuid.UUID(project),
                if_match='"v2"',
                workspace=WorkspaceDocument(),
            )
        assert hidden_write.value.code == "not_found"

        with pytest.raises(ApiError) as foreign_missing:
            await workspaces.get(owner_a, uuid.UUID(foreign))
        assert foreign_missing.value.code == "not_found"

        reloaded = await workspaces.get(owner_a, uuid.UUID(project))
        assert reloaded["workspace"]["preview_text"] == "Saved after a refresh."
        assert reloaded["workspace"]["style"]["thickness"] == 61

    run(account_database, scenario)


def test_documents_are_idempotent_owner_scoped_and_deletable(
    account_database: tuple[Engine, str],
) -> None:
    owner_a, owner_b = principal(), principal()
    body = WritingDocumentCreate(
        title="A note to Priya",
        format="note",
        document={
            "body": "Saved words survive a refresh.",
            "ink": "#192e35",
            "accent": "#d7836f",
        },
    )

    async def scenario(
        _sync: Engine,
        _async: AsyncEngine,
        _projects: ProjectsService,
        _workspaces: WorkspacesService,
        documents: DocumentsService,
    ) -> None:
        key = "document-create-1"
        replayed, created = await documents.create(owner_a, idempotency_key=key, body=body)
        assert replayed is False
        assert created["row_version"] == 1
        assert created["document"]["body"] == "Saved words survive a refresh."

        again, replayed_body = await documents.create(owner_a, idempotency_key=key, body=body)
        assert again is True
        assert replayed_body["id"] == created["id"]

        with pytest.raises(ApiError) as reuse:
            await documents.create(
                owner_a,
                idempotency_key=key,
                body=WritingDocumentCreate(
                    title="Different words",
                    format="note",
                    document={"body": "Different"},
                ),
            )
        assert reuse.value.code == "idempotency_key_reuse"

        page = await documents.list(owner_a, limit=10, cursor=None)
        assert [item["id"] for item in page["items"]] == [created["id"]]
        assert page["next_cursor"] is None

        with pytest.raises(ApiError) as hidden:
            await documents.get(owner_b, uuid.UUID(created["id"]))
        assert hidden.value.code == "not_found"

        with pytest.raises(ApiError) as precondition:
            await documents.update(
                owner_a, uuid.UUID(created["id"]), if_match=None, changes={"title": "x"}
            )
        assert precondition.value.code == "precondition_required"

        updated = await documents.update(
            owner_a,
            uuid.UUID(created["id"]),
            if_match='"v1"',
            changes={
                "title": "A longer letter",
                "format": "letter",
                "document": {**body.document.model_dump(mode="json"), "body": "New words."},
            },
        )
        assert updated["row_version"] == 2
        assert updated["format"] == "letter"

        with pytest.raises(ApiError) as stale:
            await documents.update(
                owner_a,
                uuid.UUID(created["id"]),
                if_match='"v1"',
                changes={"title": "Stale"},
            )
        assert stale.value.code == "revision_conflict"

        with pytest.raises(ApiError) as foreign_delete:
            await documents.delete(owner_b, uuid.UUID(created["id"]), if_match='"v2"')
        assert foreign_delete.value.code == "not_found"

        await documents.delete(owner_a, uuid.UUID(created["id"]), if_match='"v2"')
        with pytest.raises(ApiError) as gone:
            await documents.get(owner_a, uuid.UUID(created["id"]))
        assert gone.value.code == "not_found"

    run(account_database, scenario)


def test_account_removal_cascades_drafts_and_documents(
    account_database: tuple[Engine, str],
) -> None:
    owner = principal()

    async def scenario(
        sync: Engine,
        _async: AsyncEngine,
        projects: ProjectsService,
        workspaces: WorkspacesService,
        documents: DocumentsService,
    ) -> None:
        project = await make_project(projects, owner, "Deletion inventory")
        await workspaces.put(
            owner,
            uuid.UUID(project),
            if_match='"v0"',
            workspace=WorkspaceDocument(preview_text="Draft to erase"),
        )
        _, document = await documents.create(
            owner,
            idempotency_key="erase-document",
            body=WritingDocumentCreate(
                title="Erased note", format="note", document={"body": "Erased"}
            ),
        )

        def erase() -> None:
            with sync.begin() as connection:
                # The privacy saga removes documents explicitly and projects cascade
                # their workspaces; the owner FK cascade is the final safety net.
                connection.execute(
                    m.writing_documents.delete().where(
                        m.writing_documents.c.owner_user_id
                        == sa.select(m.users.c.id)
                        .where(m.users.c.firebase_uid == owner.firebase_uid)
                        .scalar_subquery()
                    )
                )
                # The saga removes owned projects before the identity (projects keep
                # an explicit RESTRICT owner reference).
                connection.execute(
                    m.projects.delete().where(
                        m.projects.c.owner_user_id
                        == sa.select(m.users.c.id)
                        .where(m.users.c.firebase_uid == owner.firebase_uid)
                        .scalar_subquery()
                    )
                )
                connection.execute(
                    m.users.delete().where(m.users.c.firebase_uid == owner.firebase_uid)
                )

        await asyncio.to_thread(erase)
        with sync.connect() as connection:
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM project_workspaces w "
                        "LEFT JOIN projects p ON p.id = w.project_id WHERE p.id IS NULL"
                    )
                ).scalar_one()
                == 0
            )
        with pytest.raises(ApiError) as gone:
            await documents.get(owner, uuid.UUID(document["id"]))
        assert gone.value.code == "not_found"

    run(account_database, scenario)
