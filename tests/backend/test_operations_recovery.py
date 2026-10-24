"""Operational procedures must work with persistent privacy and quota state."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.adapters.storage.local_fs import LocalObjectStorage
from app.core.config import Settings
from app.modules.jobs.quota_config import QuotaConfiguration
from app.modules.privacy.journal import ErasureJournal
from pydantic import SecretStr, ValidationError


def test_general_key_rotation_keeps_all_privacy_scopes_and_receipts(tmp_path):
    before = Settings(hmac_secret=SecretStr("a" * 40))
    pinned = Settings(hmac_secret=SecretStr("b" * 40), privacy_hmac_secret=SecretStr("a" * 40))
    storage = LocalObjectStorage(tmp_path / "objects", secret=b"s" * 32)
    try:
        old = ErasureJournal(storage, before.privacy_key)
        rows = []
        for scope in ("account", "project", "contributions_only"):
            row = dict(
                id=uuid4(),
                requested_at=datetime.now(UTC),
                identity_uids=["synthetic-owner"],
                scope=scope,
                subject_ref=old.reference("subject", "synthetic-owner"),
                project_ref=old.reference("project", uuid4()) if scope == "project" else None,
            )
            old.write(row)
            rows.append(row)
        new = ErasureJournal(storage, pinned.privacy_key)
        assert new.entries() == old.entries()
        assert new.account_blocked("synthetic-owner")
        for row in rows:
            assert new.progress_token(row) == old.progress_token(row)
    finally:
        storage.close()


def test_privacy_secret_remains_redacted_and_has_minimum_strength():
    with pytest.raises(ValidationError):
        Settings(privacy_hmac_secret=SecretStr("short"))
    assert "private-audit-key" not in repr(
        Settings(privacy_hmac_secret=SecretStr("private-audit-key" * 3))
    )


def test_operator_quota_example_has_real_identity_tiers_and_windows():
    config = QuotaConfiguration.model_validate(
        json.loads(Path("infrastructure/deployment/quota-tiers.example.json").read_text())
    )
    assert set(config.tiers) == {"free_guest", "free_user"}
    assert config.global_limits["compute_seconds"].window_kind == "day"
    value = config.model_dump(mode="json", by_alias=True)
    value["global"]["storage_bytes"] = 700000000
    with pytest.raises(ValidationError):
        QuotaConfiguration.model_validate(value)
