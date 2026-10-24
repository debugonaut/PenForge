"""Real migrated PostgreSQL receipts, direct transfers and privacy boundaries."""

from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.db import models as m
from sqlalchemy.exc import DBAPIError
from test_uploads_database import uploads_database as uploads_database
from test_uploads_http import SOURCE, headers, initialize
from test_uploads_http import capture_http as capture_http

pytestmark = pytest.mark.integration


def agreement(client, policy=None, **changes):
    return {
        "policy_sha256": (policy or client.get("/api/v1/service-policy").json())["sha256"],
        "terms_accepted": True,
        "handwriting_rights_confirmed": True,
        **changes,
    }


def test_receipts_are_idempotent_hash_bound_private_and_immutable(capture_http):
    client, sync, users, _, _ = capture_http
    response = client.post(
        "/api/v1/me/service-agreements", headers=headers(), json=agreement(client)
    )
    assert response.status_code == 200 and response.json()["eligible"]
    same = client.post("/api/v1/me/service-agreements", headers=headers(), json=agreement(client))
    assert same.json() == response.json()
    with sync.connect() as connection:
        rows = (
            connection.execute(
                sa.select(m.service_agreements).where(m.service_agreements.c.user_id == users[0])
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        assert rows[0]["policy_document"] == client.get("/api/v1/service-policy").json()["document"]
    with pytest.raises(DBAPIError), sync.begin() as connection:
        connection.execute(m.service_agreements.update().values(policy_version="forged"))
    with pytest.raises(DBAPIError), sync.begin() as connection:
        connection.execute(m.service_agreements.delete())
    changed = client.app.state.settings
    changed.service_operator_name = "Synthetic updated operator"
    status = client.get("/api/v1/me/service-access", headers=headers()).json()
    assert not status["eligible"] and not status["accepted"]
    stale = client.post(
        "/api/v1/me/service-agreements",
        headers=headers(),
        json=agreement(client, {"sha256": response.json()["policy_sha256"]}),
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "policy_changed"
    # Reading existing work and identity remains possible without accepting again.
    assert client.get("/api/v1/projects", headers=headers()).status_code == 200
    assert client.get("/api/v1/me", headers=headers()).status_code == 200


def test_guest_unverified_and_new_accounts_cannot_bypass_creation_or_transfers(capture_http):
    client, _, _, projects, _ = capture_http
    verifier = client.app.state.auth_verifier
    base = verifier.principals["owner-token"]
    for token, principal, code in [
        ("guest", replace(base, firebase_uid="guest-new", is_anonymous=True), "account_required"),
        ("unverified", replace(base, email_verified=False), "email_verification_required"),
        ("new", replace(base, firebase_uid="registered-new"), "terms_required"),
    ]:
        verifier.principals[token] = principal
        response = client.post(
            f"/api/v1/projects/{projects[0]}/uploads",
            headers=headers(token, uuid4().hex),
            json={
                "filename": "private.png",
                "mime_type": "image/png",
                "size_bytes": len(SOURCE),
                "sha256": "sha256:" + "a" * 64,
            },
        )
        assert response.status_code == 403 and response.json()["error"]["code"] == code
        direct = client.put(
            "/api/v1/storage/objects/no-token", headers=headers(token), content=SOURCE
        )
        assert direct.status_code == 403 and direct.json()["error"]["code"] == code
    assert (
        client.post(
            "/api/v1/me/service-agreements", headers=headers("unverified"), json=agreement(client)
        ).status_code
        == 403
    )


def test_object_capability_also_requires_same_account_owner(capture_http):
    client, sync, users, projects, _ = capture_http
    created, _, _ = initialize(client, projects[0])
    url = created["upload"]["url"]
    assert client.put(url, content=SOURCE).status_code == 401
    assert client.put(url, headers=headers("other-token"), content=SOURCE).status_code == 404
    assert client.put(url, headers=headers(), content=SOURCE).status_code == 204
    with sync.connect() as connection:
        audit = list(connection.execute(sa.select(m.audit_events)).mappings())
        assert any(
            row["resource_type"] == "initialize_upload" and row["resource_id"] == projects[0]
            for row in audit
        )
        encoded = str(audit)
        assert all(str(user) not in encoded for user in users)
        assert "http-" not in encoded and url not in encoded and "owner-token" not in encoded


def test_public_reports_are_bounded_private_rate_limited_and_have_no_automatic_penalty(
    capture_http,
):
    client, sync, users, _, _ = capture_http
    body = {
        "category": "impersonation",
        "details": "Synthetic concern for transport testing only.",
        "contact": "test@example.invalid",
    }
    response = client.post("/api/v1/abuse-reports", json=body)
    assert response.status_code == 201
    assert set(response.json()) == {"report_id"}
    assert client.get("/api/v1/abuse-reports/" + response.json()["report_id"]).status_code == 404
    assert (
        client.post("/api/v1/abuse-reports", json={**body, "details": "x" * 4001}).status_code
        == 400
    )
    for _ in range(4):
        assert client.post("/api/v1/abuse-reports", json=body).status_code == 201
    assert client.post("/api/v1/abuse-reports", json=body).status_code == 429
    with sync.connect() as connection:
        assert (
            connection.execute(
                sa.select(m.users.c.account_state).where(m.users.c.id == users[0])
            ).scalar_one()
            == "active"
        )
        assert (
            connection.execute(
                sa.select(sa.func.count()).select_from(m.training_consents)
            ).scalar_one()
            == 0
        )


def test_service_receipts_are_erased_by_existing_account_deletion_saga(uploads_database, tmp_path):
    from app.core.config import Settings
    from app.modules.accountability.schemas import AgreementCreate
    from app.modules.accountability.service import AccountabilityService
    from app.modules.privacy.schemas import DeletionCreate
    from test_privacy_database import Admin, drain, privacy
    from test_uploads_database import execute, principal

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person = principal()
        owner = (await identity.me(person))["user_id"]
        access = AccountabilityService(identity, Settings(app_env="test", hmac_secret="s" * 32))
        policy = __import__(
            "app.modules.accountability.policy", fromlist=["current_policy"]
        ).current_policy(access.settings)
        await access.access(
            person,
            AgreementCreate(
                policy_sha256=policy["sha256"],
                terms_accepted=True,
                handwriting_rights_confirmed=True,
            ),
        )
        await access.access(person, operation="synthetic_test")
        other = principal()
        await access.access(
            other,
            AgreementCreate(
                policy_sha256=policy["sha256"],
                terms_accepted=True,
                handwriting_rights_confirmed=True,
            ),
        )
        admin = Admin(engine, [owner])
        erasure = privacy(engine, identity, uploads, storage, admin)
        request = await erasure.create(
            person, DeletionCreate(scope="account", confirmed=True), "erase-with-terms"
        )
        assert (await drain(erasure, request))["state"] == "completed"
        async with engine.begin() as connection:
            audit = list((await connection.execute(sa.select(m.audit_events))).mappings())
            assert str(owner) not in str(audit) and person.firebase_uid not in str(audit)
            assert not await connection.scalar(
                sa.select(sa.func.count())
                .select_from(m.service_agreements)
                .where(m.service_agreements.c.user_id == owner)
            )
            assert (
                await connection.scalar(
                    sa.select(sa.func.count()).select_from(m.service_agreements)
                )
                == 1
            )

    execute(uploads_database, tmp_path, scenario)


def test_report_retention_dry_run_and_apply_preserve_new_reports(capture_http):
    import asyncio
    from datetime import UTC, datetime, timedelta

    client, sync, _, _, _ = capture_http
    now = datetime.now(UTC)
    with sync.begin() as connection:
        for age in [10, 91]:
            connection.execute(
                m.abuse_reports.insert().values(
                    id=uuid4(),
                    category="other",
                    details="Synthetic retention fixture only.",
                    created_at=now - timedelta(days=age),
                )
            )
    service = client.app.state.accountability_service
    assert asyncio.run(service.prune_reports()) == 1
    with sync.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(m.abuse_reports)) == 2
    assert asyncio.run(service.prune_reports(apply=True)) == 1
    assert asyncio.run(service.prune_reports(apply=True)) == 0
