"""Purpose-specific consent and one durable privacy handler on the existing queue."""

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from penforge_contracts import PrivacyMaintenanceJob, StrokeDocument, canonical_bytes

from app.adapters.storage.base import ObjectMissing, StorageError
from app.core.errors import ApiError
from app.db import models as m
from app.modules.jobs.service import JobsService, queue_candidates
from app.modules.privacy.journal import ErasureJournal
from app.modules.privacy.repository import PrivacyRepository
from app.modules.privacy.schemas import DeletionCreate
from app.modules.privacy.schemas import DeletionView as DeletionView

STEPS = ("jobs", "aliases", "contributions", "storage", "database", "firebase", "complete")


class PrivacyService:
    def __init__(
        self,
        engine,
        identity,
        uploads,
        storage,
        secret,
        *,
        identity_admin=None,
        lease_seconds=60,
        crash_hook=None,
    ):
        self.engine, self.identity, self.uploads, self.storage = engine, identity, uploads, storage
        self.journal = ErasureJournal(storage, secret)
        self.identity_admin, self.lease_seconds, self.crash_hook = (
            identity_admin,
            lease_seconds,
            crash_hook,
        )
        self.coordinator = JobsService(uploads)

        async def erased(uid):
            return await asyncio.to_thread(self.journal.account_blocked, uid)

        self.identity.erasure_check = erased

    async def _owner(self, con, principal):
        return await self.identity.resolve_in_transaction(
            con, principal, await PrivacyRepository(con).now()
        )

    async def _recent(self, principal, now):
        if not 0 <= now.timestamp() - principal.auth_time <= self.identity.claim_max_age_seconds:
            raise ApiError("recent_auth_required", 401)

    async def consent(self, principal):
        async with self.engine.begin() as con:
            owner = await self._owner(con, principal)
            repo = PrivacyRepository(con)
            policy, grant = await repo.policy(), await repo.consent(owner["id"])
            return self._consent_view(owner, policy, grant)

    @staticmethod
    def _consent_view(owner, policy, grant, deletion=None):
        return dict(
            policy_version=policy.policy_version,
            granted=bool(grant and not grant["withdrawn_at"]),
            eligible=not owner["is_anonymous"],
            consent_id=grant["id"] if grant else None,
            granted_at=grant["granted_at"] if grant else None,
            withdrawn_at=grant["withdrawn_at"] if grant else None,
            withdrawal_deletion_id=deletion,
        )

    async def put_consent(self, principal, body):
        async with self.engine.begin() as con:
            owner = await self._owner(con, principal)
            repo = PrivacyRepository(con)
            now, policy = await repo.now(), await repo.policy()
            await self._recent(principal, now)
            if body.policy_version != policy.policy_version:
                raise ApiError("consent_policy_conflict", 409)
            if body.granted and owner["is_anonymous"]:
                raise ApiError("consent_ineligible", 403)
            if body.granted and not policy.contributions_enabled:
                raise ApiError("capability_unavailable", 503)
            grant = await repo.consent(owner["id"], active=True)
            deletion = None
            if body.granted and not grant:
                grant = (
                    (
                        await con.execute(
                            m.training_consents.insert()
                            .values(
                                id=uuid4(),
                                user_id=owner["id"],
                                purpose=body.purpose,
                                policy_version=policy.policy_version,
                                granted_at=now,
                            )
                            .returning(m.training_consents)
                        )
                    )
                    .mappings()
                    .one()
                )
            elif not body.granted:
                await repo.withdraw(owner["id"], now)
                # Withdrawal cleanup cannot be disabled with new deletion admission.
                deletion = await self._accept(
                    con,
                    owner,
                    DeletionCreate(scope="contributions_only", confirmed=True),
                    self.journal.reference(
                        "withdrawal", str(grant["id"]) if grant else str(owner["id"])
                    ).hex(),
                )
                grant = await repo.consent(owner["id"])
            await con.execute(
                m.audit_events.insert().values(
                    actor_ref=self.journal.reference("subject", principal.firebase_uid).hex(),
                    action="consent.grant" if body.granted else "consent.withdraw",
                    resource_type="consent",
                    outcome="success",
                    metadata={},
                )
            )
            return self._consent_view(owner, policy, grant, deletion["id"] if deletion else None)

    @staticmethod
    def view(row, token=None):
        return dict(
            id=row["id"],
            scope=row["scope"],
            state=row["state"],
            progress=row["progress"],
            evidence=row["evidence"] or {},
            error_code=row["error_code"],
            retryable=row["state"] == "failed",
            requested_at=row["requested_at"],
            completed_at=row["completed_at"],
            backup_expiry=None,
            progress_token=token,
        )

    async def get(self, principal, request_id, receipt=None):
        async with self.engine.begin() as con:
            row = await PrivacyRepository(con).request(request_id)
            authorized = row and (
                principal
                and bytes(row["subject_ref"])
                == self.journal.reference("subject", principal.firebase_uid)
                or receipt
                and hmac.compare_digest(receipt, self.journal.progress_token(row))
            )
            if not authorized:
                raise ApiError("not_found", 404)
            return self.view(row, self.journal.progress_token(row) if principal else None)

    async def create(self, principal, body, key):
        if not key or len(key) > 128 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ApiError("validation_error", 400)
        async with self.engine.begin() as con:
            # Resolve existing deleting identity for exact replay; never recreate it.
            await con.execute(sa.select(sa.func.pg_advisory_xact_lock(70707001)))
            repo = PrivacyRepository(con)
            await repo.user(principal.firebase_uid)
            subject = self.journal.reference("subject", principal.firebase_uid)
            idem = self.journal.reference("idempotency", key)
            replay = (
                (
                    await con.execute(
                        sa.select(m.deletion_requests).where(
                            m.deletion_requests.c.subject_ref == subject,
                            m.deletion_requests.c.idempotency_hash == idem,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            digest = hashlib.sha256(canonical_bytes(body.model_dump(mode="json"))).digest()
            if replay:
                if bytes(replay["request_hash"]) != digest:
                    raise ApiError("idempotency_key_reuse", 422)
                return self.view(replay, self.journal.progress_token(replay))
            owner = await self._owner(con, principal)
            await self._recent(principal, await repo.now())
            if not (await repo.policy()).new_requests_enabled:
                raise ApiError("capability_unavailable", 503)
            row = await self._accept(con, owner, body, key)
            return self.view(row, self.journal.progress_token(row))

    async def _accept(
        self, con, owner, body, key, *, request_id=None, restoring=False, consent_ids=None
    ):
        repo = PrivacyRepository(con)
        subject, idem = (
            self.journal.reference("subject", owner["firebase_uid"]),
            self.journal.reference("idempotency", key),
        )
        digest = hashlib.sha256(canonical_bytes(body.model_dump(mode="json"))).digest()
        replay = (
            (
                await con.execute(
                    sa.select(m.deletion_requests).where(
                        m.deletion_requests.c.subject_ref == subject,
                        m.deletion_requests.c.idempotency_hash == idem,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if replay:
            return replay
        now = await repo.now()
        targets = [owner]
        if body.scope == "project":
            project = (
                (
                    await con.execute(
                        sa.select(m.projects)
                        .where(
                            m.projects.c.id == body.project_id,
                            m.projects.c.owner_user_id == owner["id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if not project:
                raise ApiError("not_found", 404)
            if (
                body.expected_project_version is not None
                and project["row_version"] != body.expected_project_version
            ):
                raise ApiError("revision_conflict", 412)
            await con.execute(
                m.projects.update()
                .where(m.projects.c.id == body.project_id)
                .values(status="deleting", row_version=m.projects.c.row_version + 1)
            )
            await repo.withdraw(owner["id"], now, body.project_id, close_grant=False)
        else:
            if body.scope == "account":
                # Ownership coordination serializes this ancestry inventory with claims.
                targets = list(
                    (
                        await con.execute(
                            sa.text(
                                "WITH RECURSIVE ancestry AS (SELECT * FROM users WHERE id=:owner UNION ALL SELECT u.* FROM users u JOIN ancestry a ON u.merged_into_user_id=a.id) SELECT * FROM ancestry ORDER BY id"
                            ),
                            {"owner": owner["id"]},
                        )
                    ).mappings()
                )
                await con.execute(
                    sa.select(m.users)
                    .where(m.users.c.id.in_([u["id"] for u in targets]))
                    .order_by(m.users.c.id)
                    .with_for_update()
                )
                await con.execute(
                    m.users.update()
                    .where(m.users.c.id == owner["id"])
                    .values(account_state="deleting")
                )
                await con.execute(
                    m.projects.update()
                    .where(m.projects.c.owner_user_id == owner["id"])
                    .values(status="deleting", row_version=m.projects.c.row_version + 1)
                )
            if consent_ids is None:
                for user in targets:
                    await repo.withdraw(user["id"], now)
            else:
                await con.execute(
                    m.dataset_contributions.update()
                    .where(
                        m.dataset_contributions.c.consent_id.in_(consent_ids),
                        m.dataset_contributions.c.status == "active",
                    )
                    .values(status="withdrawn", withdrawn_at=now)
                )
                await con.execute(
                    m.training_consents.update()
                    .where(
                        m.training_consents.c.id.in_(consent_ids),
                        m.training_consents.c.withdrawn_at.is_(None),
                    )
                    .values(withdrawn_at=now)
                )
        if consent_ids is None:
            consent_ids = list(
                (
                    await con.execute(
                        sa.select(m.training_consents.c.id).where(
                            m.training_consents.c.user_id.in_([u["id"] for u in targets])
                        )
                    )
                ).scalars()
            )
        row = (
            (
                await con.execute(
                    m.deletion_requests.insert()
                    .values(
                        id=request_id or uuid4(),
                        subject_ref=subject,
                        user_id=owner["id"],
                        project_id=body.project_id,
                        scope=body.scope,
                        state="requested",
                        progress={},
                        evidence={},
                        project_ref=self.journal.reference("project", body.project_id)
                        if body.project_id
                        else None,
                        consent_ids=[str(c) for c in consent_ids],
                        target_users=[str(u["id"]) for u in targets],
                        identity_uids=[u["firebase_uid"] for u in targets]
                        if body.scope == "account"
                        else [],
                        idempotency_hash=idem,
                        request_hash=digest,
                        requested_at=now,
                        updated_at=now,
                    )
                    .returning(m.deletion_requests)
                )
            )
            .mappings()
            .one()
        )
        # No writing, UID, filename or storage key enters this independent journal.
        if not restoring:
            await asyncio.to_thread(self.journal.write, row)
        await repo.create_job(row["id"], now)
        return row

    async def contribute(self, principal, body, key):
        async with self.engine.begin() as con:
            owner = await self._owner(con, principal)
            repo = PrivacyRepository(con)
            policy = await repo.policy()
            if owner["is_anonymous"] or not policy.contributions_enabled:
                raise ApiError("consent_ineligible", 403)
            grant = await repo.consent(owner["id"], active=True)
            if (
                not grant
                or grant["id"] != body.consent_id
                or grant["policy_version"] != body.policy_version
                or body.policy_version != policy.policy_version
            ):
                raise ApiError("consent_required", 403)
            sample = (
                (
                    await con.execute(
                        sa.select(m.samples)
                        .join(m.projects, m.projects.c.id == m.samples.c.project_id)
                        .where(
                            m.samples.c.id == body.sample_id,
                            m.projects.c.owner_user_id == owner["id"],
                            m.projects.c.status != "deleting",
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if not sample:
                raise ApiError("not_found", 404)
            source = (
                (
                    await con.execute(
                        sa.select(m.assets).where(
                            m.assets.c.id == sample["asset_id"],
                            m.assets.c.owner_user_id == owner["id"],
                        )
                    )
                )
                .mappings()
                .one()
            )
            if source["kind"] == "raw_strokes" and source["state"] == "ready":
                raw = await asyncio.to_thread(
                    self.storage.read_object, source["storage_key"], max_bytes=20 * 1024 * 1024
                )
                doc = StrokeDocument.model_validate_json(raw)
                # Dataset copies need geometry, not pen hardware/timing or capture lineage.
                data = canonical_bytes(
                    dict(
                        schema_version=1,
                        artifact_type="penforge.contribution_strokes",
                        strokes=[
                            [{"x": p.x, "y": p.y} for p in stroke.points] for stroke in doc.strokes
                        ],
                    )
                )
                transform = "metadata-minimized-stroke-geometry-v1"
            else:
                derived = (
                    (
                        await con.execute(
                            sa.select(m.assets)
                            .where(
                                m.assets.c.parent_asset_id == source["id"],
                                m.assets.c.owner_user_id == owner["id"],
                                m.assets.c.kind == "thumbnail",
                                m.assets.c.state == "ready",
                            )
                            .order_by(m.assets.c.id)
                            .limit(1)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if not derived:
                    raise ApiError("capability_unavailable", 503)
                raw = await asyncio.to_thread(
                    self.storage.read_object, derived["storage_key"], max_bytes=4 * 1024 * 1024
                )
                # The existing isolated verifier created this metadata-free PNG.
                if hashlib.sha256(raw).digest() != bytes(derived["sha256"]):
                    raise ApiError("upload_mismatch", 400)
                data, transform = raw, "strict-decoder-metadata-stripped-png-v1"
            if hashlib.sha256(raw).digest() != bytes(
                (source if source["kind"] == "raw_strokes" else derived)["sha256"]
            ):
                raise ApiError("upload_mismatch", 400)
            if not key or len(key) > 128:
                raise ApiError("validation_error", 400)
            cid = UUID(
                bytes=self.journal.reference("contribution-key", str(owner["id"]) + ":" + key)[:16]
            )
            previous = (
                (
                    await con.execute(
                        sa.select(m.dataset_contributions).where(
                            m.dataset_contributions.c.id == cid
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if previous:
                if previous["sample_id"] != sample["id"] or previous["consent_id"] != grant["id"]:
                    raise ApiError("idempotency_key_reuse", 422)
                if previous["copy_completed_at"]:
                    return self.contribution_view(previous)
            digest = hashlib.sha256(data).digest()
            if not previous:
                await con.execute(
                    m.dataset_contributions.insert().values(
                        id=cid,
                        sample_id=sample["id"],
                        consent_id=grant["id"],
                        contributor_ref=self.journal.reference(
                            "contributor", principal.firebase_uid
                        ),
                        storage_key="dataset/contributions/" + str(cid),
                        source_asset_id=source["id"],
                        content_sha256=digest,
                        transformation=transform,
                        source_lineage={
                            "source_sha256": bytes(source["sha256"]).hex(),
                            "policy_version": policy.policy_version,
                        },
                    )
                )
        # The ledger commits before copying. Re-lock the owner and grant so withdrawal
        # cannot race publication. A failed copy is recoverable by the same explicit key.
        async with self.engine.begin() as con:
            owner = await self._owner(con, principal)
            repo = PrivacyRepository(con)
            row = (
                (
                    await con.execute(
                        sa.select(m.dataset_contributions)
                        .where(m.dataset_contributions.c.id == cid)
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            grant = await repo.consent(owner["id"], active=True)
            if not grant or grant["id"] != row["consent_id"] or row["status"] != "active":
                raise ApiError("consent_required", 403)
            await asyncio.to_thread(self.storage.put_immutable, row["storage_key"], data)
            await con.execute(
                m.dataset_contributions.update()
                .where(m.dataset_contributions.c.id == cid)
                .values(copy_completed_at=await repo.now())
            )
            row = dict(row) | {"copy_completed_at": await repo.now()}
            return self.contribution_view(row)

    @staticmethod
    def contribution_view(row):
        return dict(
            id=row["id"],
            status=row["status"],
            content_sha256=bytes(row["content_sha256"]).hex() if row["content_sha256"] else None,
            consent_id=row["consent_id"],
            transformation=row["transformation"],
            removed_from_dataset_at=row["removed_from_dataset_at"],
        )

    async def build_dataset(self, version):
        """Offline selection only, never training. Every build rechecks live grants."""
        if not __import__("re").fullmatch(r"[a-zA-Z0-9_.-]{1,80}", version):
            raise ValueError("invalid_dataset_version")
        async with self.engine.begin() as con:
            await con.execute(sa.select(sa.func.pg_advisory_xact_lock(70707001)))
            list(
                (
                    await con.execute(sa.select(m.users).order_by(m.users.c.id).with_for_update())
                ).mappings()
            )
            prior = await con.scalar(
                sa.select(m.dataset_versions.c.manifest).where(
                    m.dataset_versions.c.version == version
                )
            )
            if prior is not None:
                return prior
            rows = list(
                (
                    await con.execute(
                        sa.select(m.dataset_contributions)
                        .join(
                            m.training_consents,
                            m.training_consents.c.id == m.dataset_contributions.c.consent_id,
                        )
                        .join(m.users, m.users.c.id == m.training_consents.c.user_id)
                        .where(
                            m.training_consents.c.withdrawn_at.is_(None),
                            m.training_consents.c.policy_version
                            == (await PrivacyRepository(con).policy()).policy_version,
                            m.users.c.account_state == "active",
                            m.users.c.merged_into_user_id.is_(None),
                            m.dataset_contributions.c.status == "active",
                            m.dataset_contributions.c.copy_completed_at.is_not(None),
                        )
                        .order_by(m.dataset_contributions.c.id)
                    )
                ).mappings()
            )
            entries = []
            for row in rows:
                metadata = await asyncio.to_thread(self.storage.get_metadata, row["storage_key"])
                if metadata.sha256 != bytes(row["content_sha256"]).hex():
                    raise StorageError("dataset_integrity_failed")
                entries.append(
                    dict(
                        contribution_id=str(row["id"]),
                        sha256=metadata.sha256,
                        contributor_ref=bytes(row["contributor_ref"]).hex(),
                        consent_id=str(row["consent_id"]),
                        transformation=row["transformation"],
                    )
                )
            manifest = dict(
                version=version, purpose="model_training", entries=entries, training_run_ids=[]
            )
            await con.execute(
                m.dataset_versions.insert().values(
                    version=version,
                    manifest=manifest,
                    manifest_sha256=hashlib.sha256(canonical_bytes(manifest)).digest(),
                )
            )
            return manifest

    async def _aliases(self, con, row):
        assets = await PrivacyRepository(con).assets(row)
        doomed = {a["id"] for a in assets}
        for asset in assets:
            if asset["kind"] != "original_upload" or asset["duplicate_of"]:
                continue
            survivors = list(
                (
                    await con.execute(
                        sa.select(m.assets)
                        .where(
                            m.assets.c.duplicate_of == asset["id"],
                            m.assets.c.id.not_in(doomed),
                            m.assets.c.owner_user_id == asset["owner_user_id"],
                        )
                        .order_by(m.assets.c.id)
                        .with_for_update()
                    )
                ).mappings()
            )
            if not survivors:
                continue
            # Prefer an existing ready claimed alias, preserving its exact identity/bytes.
            ready = next((a for a in survivors if a["state"] == "ready"), None)
            if asset["state"] != "deleting":
                await con.execute(
                    m.assets.update().where(m.assets.c.id == asset["id"]).values(state="deleting")
                )
            if ready:
                canonical = ready["id"]
                await con.execute(
                    m.assets.update().where(m.assets.c.id == canonical).values(duplicate_of=None)
                )
            else:
                # A rejected alias may have had redundant bytes reaped. Commit a
                # deterministic pending ledger on the first pass, then copy/promote
                # on a later lease; a killed write always remains inventoried.
                canonical = UUID(
                    bytes=self.journal.reference("alias", str(row["id"]) + str(asset["id"]))[:16]
                )
                key = "preserved/" + str(canonical)
                pending = (
                    (
                        await con.execute(
                            sa.select(m.assets).where(m.assets.c.id == canonical).with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if pending is None:
                    await con.execute(
                        m.assets.insert().values(
                            id=canonical,
                            owner_user_id=asset["owner_user_id"],
                            project_id=survivors[0]["project_id"],
                            kind="original_upload",
                            storage_backend=asset["storage_backend"],
                            storage_key=key,
                            mime_type=asset["mime_type"],
                            size_bytes=asset["size_bytes"],
                            declared_sha256=asset["sha256"],
                            state="pending_upload",
                            retention_class="original",
                            expires_at=None,
                        )
                    )
                    return False
                if (
                    pending["owner_user_id"] != asset["owner_user_id"]
                    or pending["project_id"] != survivors[0]["project_id"]
                    or pending["state"] != "pending_upload"
                ):
                    raise StorageError("alias_preservation_conflict")
                data = await asyncio.to_thread(
                    self.storage.read_object, asset["storage_key"], max_bytes=20 * 1024 * 1024
                )
                if hashlib.sha256(data).digest() != bytes(asset["sha256"]):
                    raise StorageError("alias_integrity_failed")
                await asyncio.to_thread(self.storage.put_immutable, key, data)
                await asyncio.to_thread(
                    self.storage.verify_upload,
                    key,
                    size_bytes=len(data),
                    sha256=bytes(asset["sha256"]).hex(),
                )
                for state in ("uploaded", "verifying"):
                    await con.execute(
                        m.assets.update().where(m.assets.c.id == canonical).values(state=state)
                    )
                await con.execute(
                    m.assets.update()
                    .where(m.assets.c.id == canonical)
                    .values(
                        state="ready",
                        sha256=asset["sha256"],
                        verified_at=await PrivacyRepository(con).now(),
                    )
                )
                await con.execute(
                    m.quota_reservations.update()
                    .where(m.quota_reservations.c.asset_id == asset["id"])
                    .values(asset_id=canonical)
                )
            await con.execute(
                m.assets.update()
                .where(m.assets.c.id.in_([a["id"] for a in survivors if a["id"] != canonical]))
                .values(duplicate_of=canonical)
            )

        return True

    async def _delete_object(self, key):
        await asyncio.to_thread(self.storage.delete_object, key)
        try:
            await asyncio.to_thread(self.storage.get_metadata, key)
        except ObjectMissing:
            return
        raise StorageError("privacy_object_still_present")

    async def _storage(self, con, row):
        repo = PrivacyRepository(con)
        assets = await repo.assets(row)
        keys = {a["storage_key"] for a in assets}
        # Phase 03 verifier can die before derivative metadata publication. Enumerate
        # only its deterministic, persisted attempt budget; no broad prefix purge.
        for asset in assets:
            if asset["kind"] == "original_upload" and "/orig/" in asset["storage_key"]:
                job = (
                    (
                        await con.execute(
                            sa.select(m.jobs).where(m.jobs.c.id == asset["verify_job_id"])
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if job:
                    prefix = asset["storage_key"].rsplit("/orig/", 1)[0]
                    keys.update(
                        f"{prefix}/thumb/{asset['id']}/a{attempt}.png"
                        for attempt in range(1, job["max_attempts"] + 1)
                    )
        for key in sorted(keys):
            await self._delete_object(key)
        return len(keys)

    async def _remove_contributions(self, con, row):
        repo = PrivacyRepository(con)
        count = 0
        for contribution in await repo.contributions(row):
            if contribution["status"] == "active":
                await con.execute(
                    m.dataset_contributions.update()
                    .where(m.dataset_contributions.c.id == contribution["id"])
                    .values(status="withdrawn", withdrawn_at=await repo.now())
                )
            if contribution["storage_key"]:
                await self._delete_object(contribution["storage_key"])
                count += 1
            await con.execute(
                m.dataset_contributions.update()
                .where(m.dataset_contributions.c.id == contribution["id"])
                .values(
                    status="removed_from_dataset",
                    removed_from_dataset_at=await repo.now(),
                    source_lineage={},
                    storage_key=None,
                    source_asset_id=None,
                    sample_id=None,
                    consent_id=None,
                )
            )
        return count

    async def claim(self, holder, *, worker_mem_mb=768, job_id=None):
        async with self.engine.begin() as con:
            candidates = await queue_candidates(
                con, ("privacy_maintenance",), (1,), worker_mem_mb, job_id
            )
            for candidate in candidates:
                job = (
                    (
                        await con.execute(
                            sa.select(m.jobs)
                            .where(m.jobs.c.id == candidate["id"], m.jobs.c.state == "queued")
                            .with_for_update(skip_locked=True)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if not job:
                    continue
                now = await PrivacyRepository(con).now()
                result = (
                    (
                        await con.execute(
                            m.jobs.update()
                            .where(m.jobs.c.id == job["id"])
                            .values(
                                state="running",
                                attempts=m.jobs.c.attempts + 1,
                                lease_holder=holder,
                                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                                last_heartbeat_at=now,
                                started_at=job["started_at"] or now,
                            )
                            .returning(m.jobs)
                        )
                    )
                    .mappings()
                    .one()
                )
                return dict(result)
        return None

    async def reap(self):
        async with self.engine.begin() as con:
            jobs = list(
                (
                    await con.execute(
                        sa.select(m.jobs)
                        .where(
                            m.jobs.c.job_type == "privacy_maintenance",
                            m.jobs.c.state == "running",
                            m.jobs.c.lease_expires_at <= sa.func.clock_timestamp(),
                        )
                        .with_for_update(skip_locked=True)
                    )
                ).mappings()
            )
            for job in jobs:
                await con.execute(
                    m.jobs.update()
                    .where(m.jobs.c.id == job["id"])
                    .values(
                        state="queued",
                        lease_holder=None,
                        lease_expires_at=None,
                        run_after=await PrivacyRepository(con).now(),
                        error_code="lease_expired",
                    )
                )
        return len(jobs)

    async def advance(self, job, holder):
        """One persisted step per lease. Effects repeat safely after a crash."""
        request_id = PrivacyMaintenanceJob.model_validate(job["payload"]).deletion_request_id
        next_step = None
        try:
            async with self.engine.begin() as con:
                repo = PrivacyRepository(con)
                # Ownership lock is acquired before user/project/job locks, as in claims.
                await con.execute(sa.select(sa.func.pg_advisory_xact_lock(70707001)))
                row = await repo.request(request_id)
                if row is None:
                    raise ValueError("privacy_request_missing")
                await repo.lock_targets(row)
                current = (
                    (
                        await con.execute(
                            sa.select(m.jobs).where(m.jobs.c.id == job["id"]).with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                now = await repo.now()
                if (
                    current["state"] != "running"
                    or current["lease_holder"] != holder
                    or current["attempts"] != job["attempts"]
                    or current["lease_expires_at"] <= now
                ):
                    return False
                await con.execute(
                    sa.select(
                        sa.func.set_config(
                            "penforge.privacy_fence",
                            json.dumps(
                                dict(
                                    job_id=str(job["id"]),
                                    attempt=job["attempts"],
                                    holder=holder,
                                    expires_at=current["lease_expires_at"].isoformat(),
                                )
                            ),
                            True,
                        )
                    )
                )
                progress, evidence = dict(row["progress"]), dict(row["evidence"] or {})
                next_step = next((s for s in STEPS if progress.get(s) != "done"), None)
                if self.crash_hook and next_step:
                    self.crash_hook("before", next_step)
                if next_step == "jobs":
                    done = await repo.cancel_jobs(row, self.coordinator)
                    progress["jobs"] = "done" if done else "waiting"
                elif next_step == "aliases":
                    progress[next_step] = "done" if await self._aliases(con, row) else "waiting"
                elif next_step == "contributions":
                    evidence["dataset_objects_removed"] = await self._remove_contributions(con, row)
                    progress[next_step] = "done"
                elif next_step == "storage":
                    evidence["objects_removed"] = await self._storage(con, row)
                    progress[next_step] = "done"
                elif next_step == "database":
                    if row["scope"] != "contributions_only":
                        evidence["records_removed"] = await repo.purge(row)
                    progress[next_step] = "done"
                elif next_step == "firebase":
                    if row["scope"] == "account":
                        if self.identity_admin is None:
                            raise StorageError("privacy_identity_unavailable")
                        for uid in row["identity_uids"]:
                            await self.identity_admin.delete_identity(uid)
                        evidence["identities_removed"] = len(row["identity_uids"])
                    progress[next_step] = "done"
                elif next_step == "complete":
                    progress[next_step] = "done"
                completed = progress.get("complete") == "done"
                await con.execute(
                    m.deletion_requests.update()
                    .where(m.deletion_requests.c.id == request_id)
                    .values(
                        progress=progress,
                        evidence=evidence,
                        state="completed" if completed else "in_progress",
                        error_code=None,
                        updated_at=now,
                        completed_at=now if completed else None,
                        **(
                            dict(
                                user_id=None,
                                project_id=None,
                                identity_uids=[],
                                target_users=[],
                                consent_ids=[],
                            )
                            if completed
                            else {}
                        ),
                    )
                )
                if completed:
                    await con.execute(
                        m.audit_events.insert().values(
                            actor_ref=bytes(row["subject_ref"]).hex(),
                            action="privacy.complete",
                            resource_type="privacy",
                            outcome="success",
                            metadata=evidence,
                        )
                    )
                # Requeue a safe step without changing the fencing counter backwards.
                wait = progress.get("jobs") == "waiting"
                await con.execute(
                    m.jobs.update()
                    .where(m.jobs.c.id == job["id"])
                    .values(
                        state="succeeded" if completed else "queued",
                        stage=next_step,
                        lease_holder=None,
                        lease_expires_at=None,
                        run_after=now + timedelta(seconds=1 if wait else 0),
                        finished_at=now if completed else None,
                    )
                )
            if self.crash_hook and next_step:
                self.crash_hook("after", next_step)
            return True
        except (StorageError, OSError):
            await self._failed(job, holder, request_id)
            return False

    async def _failed(self, job, holder, request_id):
        async with self.engine.begin() as con:
            repo = PrivacyRepository(con)
            current = (
                (
                    await con.execute(
                        sa.select(m.jobs).where(m.jobs.c.id == job["id"]).with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            now, policy = await repo.now(), await repo.policy()
            if (
                current["state"] != "running"
                or current["lease_holder"] != holder
                or current["attempts"] != job["attempts"]
                or current["lease_expires_at"] <= now
            ):
                return
            await con.execute(
                m.deletion_requests.update()
                .where(m.deletion_requests.c.id == request_id)
                .values(state="failed", error_code="privacy_dependency_unavailable", updated_at=now)
            )
            delay = min(
                policy.retry_cap_seconds,
                policy.retry_base_seconds * 2 ** min(12, job["attempts"] - 1),
            )
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job["id"])
                .values(
                    state="queued",
                    lease_holder=None,
                    lease_expires_at=None,
                    run_after=now + timedelta(seconds=delay),
                    error_code="privacy_dependency_unavailable",
                )
            )

    async def restore_reconcile(self):
        """Must finish before API/worker startup; independent journal survives DB restore."""
        entries = await asyncio.to_thread(self.journal.entries)
        count = 0
        async with self.engine.begin() as con:
            await con.execute(sa.select(sa.func.pg_advisory_xact_lock(70707001)))
            users = list(
                (
                    await con.execute(sa.select(m.users).order_by(m.users.c.id).with_for_update())
                ).mappings()
            )
            for entry in entries:
                owner = next(
                    (
                        u
                        for u in users
                        if self.journal.reference("subject", u["firebase_uid"]).hex()
                        == entry["subject_ref"]
                    ),
                    None,
                )
                if not owner:
                    continue
                stale_consents = None
                if entry["scope"] == "contributions_only":
                    stale_consents = list(
                        (
                            await con.execute(
                                sa.select(m.training_consents.c.id).where(
                                    m.training_consents.c.user_id == owner["id"],
                                    m.training_consents.c.granted_at
                                    <= datetime.fromisoformat(entry["accepted_at"]),
                                )
                            )
                        ).scalars()
                    )
                    active_copies = await con.scalar(
                        sa.select(sa.func.count())
                        .select_from(m.dataset_contributions)
                        .where(
                            m.dataset_contributions.c.consent_id.in_(stale_consents),
                            m.dataset_contributions.c.status != "removed_from_dataset",
                        )
                    )
                    if not active_copies:
                        await con.execute(
                            m.training_consents.update()
                            .where(
                                m.training_consents.c.id.in_(stale_consents),
                                m.training_consents.c.withdrawn_at.is_(None),
                            )
                            .values(withdrawn_at=await PrivacyRepository(con).now())
                        )
                        continue
                project = None
                if entry["scope"] == "project":
                    project = next(
                        (
                            p
                            for p in (
                                await con.execute(
                                    sa.select(m.projects).where(
                                        m.projects.c.owner_user_id == owner["id"]
                                    )
                                )
                            ).mappings()
                            if self.journal.reference("project", p["id"]).hex()
                            == entry["project_ref"]
                        ),
                        None,
                    )
                    if not project:
                        continue
                target_ref = self.journal.reference("project", project["id"]) if project else None
                pending = await con.scalar(
                    sa.select(m.deletion_requests.c.id).where(
                        m.deletion_requests.c.subject_ref == bytes.fromhex(entry["subject_ref"]),
                        m.deletion_requests.c.scope == entry["scope"],
                        m.deletion_requests.c.project_ref.is_not_distinct_from(target_ref),
                        m.deletion_requests.c.state != "completed",
                    )
                )
                if pending is not None:
                    continue
                rid = UUID(entry["request_id"])
                existing = await PrivacyRepository(con).request(rid)
                if existing and existing["state"] != "completed":
                    continue
                if existing:
                    # Retain the completed receipt. A new reconciliation request uses the
                    # same HMAC target and the normal saga, with a new opaque operation ID.
                    rid = uuid4()
                await self._accept(
                    con,
                    owner,
                    DeletionCreate(
                        scope=entry["scope"],
                        project_id=project["id"] if project else None,
                        confirmed=True,
                    ),
                    "restore:" + str(rid),
                    request_id=rid,
                    restoring=True,
                    consent_ids=stale_consents,
                )
                count += 1
        return {"reconciled_scopes": count}

    async def retention_report(self):
        from app.modules.privacy.retention import Retention

        return await Retention(self).report()

    async def retention_apply(self, report):
        from app.modules.privacy.retention import Retention

        return await Retention(self).apply(report)

    async def signals(self):
        async with self.engine.begin() as con:
            repo = PrivacyRepository(con)
            policy, now = await repo.policy(), await repo.now()
            count = await con.scalar(
                sa.select(sa.func.count())
                .select_from(m.deletion_requests)
                .where(
                    m.deletion_requests.c.state != "completed",
                    m.deletion_requests.c.updated_at
                    < now - timedelta(seconds=policy.stuck_seconds),
                )
            )
            return {"stuck_requests": count, "threshold_seconds": policy.stuck_seconds}


async def project_deletion(service, principal, project_id, if_match):
    """Legacy DELETE adapter retains its strong ETag and natural retry identity."""
    import re

    if if_match is None:
        raise ApiError("precondition_required", 428)
    match = re.fullmatch(r'"v([1-9][0-9]{0,8})"', if_match)
    if not match:
        raise ApiError("revision_conflict", 412)
    return await service.create(
        principal,
        DeletionCreate(
            scope="project",
            project_id=project_id,
            expected_project_version=int(match[1]),
            confirmed=True,
        ),
        "project-delete:" + str(project_id) + ":" + match[1],
    )
