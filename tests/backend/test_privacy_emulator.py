"""Actual local Firebase deletion is last; no hosted Firebase behavior is claimed."""

import pytest
import sqlalchemy as sa
from app.adapters.storage.local_fs import LocalObjectStorage
from app.db import models as m
from app.modules.privacy.service import PrivacyService
from app.modules.uploads.service import UploadsService
from test_identity_emulator import auth, headers
from test_identity_emulator import emulator_app as emulator_app

pytestmark = [pytest.mark.integration, pytest.mark.emulator]


def test_account_erasure_removes_real_emulator_identity_last(emulator_app, tmp_path):
    client, base = emulator_app
    user = auth(base, "signUp", returnSecureToken=True)
    token = user["idToken"]
    identity = client.app.state.identity_service
    assert client.get("/api/v1/me", headers=headers(token)).status_code == 200
    storage = LocalObjectStorage(tmp_path / "objects", secret=b"s" * 32)
    uploads = UploadsService(identity.engine, identity, storage)
    verifier = client.app.state.auth_verifier
    called = []

    class Admin:
        async def delete_identity(self, uid):
            async with identity.engine.begin() as con:
                assert not await con.scalar(
                    sa.select(sa.func.count())
                    .select_from(m.users)
                    .where(m.users.c.firebase_uid == uid)
                )
                assert not await con.scalar(sa.select(sa.func.count()).select_from(m.assets))
            await verifier.delete_identity(uid)
            called.append(True)

    privacy = PrivacyService(
        identity.engine,
        identity,
        uploads,
        storage,
        b"privacy-emulator-test-secret" * 2,
        identity_admin=Admin(),
    )
    client.app.state.privacy_service = privacy
    created = client.post(
        "/api/v1/privacy/deletion-requests",
        headers=headers(token) | {"Idempotency-Key": "delete-account"},
        json={"scope": "account", "confirmed": True},
    )
    assert created.status_code == 202, created.text
    record = created.json()

    async def drain():
        for _ in range(7):
            job = await privacy.claim("emulator-worker")
            assert job
            assert await privacy.advance(job, "emulator-worker")

    try:
        client.portal.call(drain)
        assert called == [True]
        assert client.get("/api/v1/me", headers=headers(token)).status_code == 401
        result = client.get(
            "/api/v1/privacy/deletion-requests/" + record["id"],
            headers={"X-Deletion-Receipt": record["progress_token"]},
        )
        assert result.status_code == 200 and result.json()["state"] == "completed"
    finally:
        storage.close()
