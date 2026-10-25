"""Phase 07 real PostgreSQL consent, crash, retention and erasure acceptance."""

import asyncio
import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.adapters.storage.base import StorageError
from app.core.errors import ApiError
from app.db import models as m
from app.modules.privacy.schemas import ConsentPut, ContributionCreate, DeletionCreate
from app.modules.privacy.service import STEPS, PrivacyService
from test_uploads_database import configure, execute, principal
from test_uploads_database import create_project as setup
from test_uploads_database import uploads_database as uploads_database

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def prepare_evidence_directory():
    # Evidence is generated on each clean checkout; no prior local run is required.
    Path("artifacts/phase07").mkdir(parents=True, exist_ok=True)


ARTIFACTS = Path("artifacts/phase07")


class Admin:
    def __init__(self, engine, owners):
        self.engine, self.owners, self.calls = engine, owners, []

    async def delete_identity(self, uid):
        async with self.engine.begin() as con:
            assert not await con.scalar(
                sa.select(sa.func.count()).select_from(m.users).where(m.users.c.id.in_(self.owners))
            )
            assert not await con.scalar(
                sa.select(sa.func.count())
                .select_from(m.assets)
                .where(m.assets.c.owner_user_id.in_(self.owners))
            )
        self.calls.append(uid)


def privacy(engine, identity, uploads, storage, admin=None, crash=None):
    return PrivacyService(
        engine,
        identity,
        uploads,
        storage,
        b"privacy-fixture-key-v1" * 2,
        identity_admin=admin,
        crash_hook=crash,
    )


async def drain(service, request, *, limit=30):
    for _ in range(limit):
        status = await service.get(None, request["id"], request["progress_token"])
        if status["state"] == "completed":
            return status
        job = await service.claim("privacy-test")
        if not job:
            async with service.engine.begin() as con:
                await con.execute(
                    m.jobs.update()
                    .where(m.jobs.c.job_type == "privacy_maintenance", m.jobs.c.state == "queued")
                    .values(run_after=sa.func.clock_timestamp())
                )
            job = await service.claim("privacy-test")
        assert job
        await service.advance(job, "privacy-test")
    raise AssertionError("saga failed to complete")


async def seed_asset(
    engine, storage, owner, project, *, kind="original_upload", state="ready", key=None, age=0
):
    aid = uuid.uuid4()
    data = b"private synthetic fixture bytes"
    key = key or f"u/historical-guest/p/{project}/orig/{aid}"
    storage.put_immutable(key, data)
    async with engine.begin() as con:
        await con.execute(
            m.assets.insert().values(
                id=aid,
                owner_user_id=owner,
                project_id=project,
                kind=kind,
                storage_backend="local_fs",
                storage_key=key,
                mime_type="image/png",
                size_bytes=len(data),
                sha256=hashlib.sha256(data).digest(),
                state=state,
                verified_at=datetime.now(UTC) if state == "ready" else None,
                retention_class="derived" if kind == "thumbnail" else "original",
                created_at=datetime.now(UTC) - timedelta(days=age),
                last_used_at=datetime.now(UTC) - timedelta(days=age),
                expires_at=datetime.now(UTC) - timedelta(days=age),
            )
        )
    return aid, key


def test_consent_default_guest_policy_history_and_recent_auth(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person = principal()
        guest = principal(True)
        service = privacy(engine, identity, uploads, storage)
        initial = await service.consent(person)
        assert not initial["granted"] and initial["eligible"]
        grant = ConsentPut(
            purpose="model_training", granted=True, policy_version="model-training-v1"
        )
        with pytest.raises(ApiError) as error:
            await service.put_consent(guest, grant)
        assert error.value.code == "consent_ineligible"
        with pytest.raises(ApiError) as error:
            await service.put_consent(replace(person, auth_time=1), grant)
        assert error.value.code == "recent_auth_required"
        with pytest.raises(ApiError) as error:
            await service.put_consent(person, grant.model_copy(update={"policy_version": "old"}))
        assert error.value.code == "consent_policy_conflict"
        granted = await service.put_consent(person, grant)
        same = await service.put_consent(person, grant)
        assert granted["consent_id"] == same["consent_id"]
        withdrawn = await service.put_consent(person, grant.model_copy(update={"granted": False}))
        assert withdrawn["withdrawn_at"] and not withdrawn["granted"]
        async with engine.begin() as con:
            assert (
                await con.scalar(sa.select(sa.func.count()).select_from(m.training_consents)) == 1
            )
        newer = await service.put_consent(person, grant)
        assert newer["consent_id"] != granted["consent_id"]
        await service.restore_reconcile()
        assert (await service.consent(person))["granted"] is True

    execute(uploads_database, tmp_path, scenario)


@pytest.mark.parametrize("moment", ["before", "after"])
@pytest.mark.parametrize("step", STEPS)
def test_every_saga_step_crash_resumes_and_firebase_last(uploads_database, tmp_path, moment, step):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person = principal()
        owner, project = await setup(identity, projects, person)
        await configure(engine, owner)
        aid, key = await seed_asset(engine, storage, owner, project)
        admin = Admin(engine, [owner])
        tripped = []

        def crash(at, where):
            if at == moment and where == step and not tripped:
                tripped.append(True)
                raise RuntimeError("injected_process_crash")

        service = privacy(engine, identity, uploads, storage, admin, crash)
        request = await service.create(
            person, DeletionCreate(scope="account", confirmed=True), "erase"
        )
        for _ in range(12):
            job = await service.claim("crashing")
            assert job
            try:
                await service.advance(job, "crashing")
            except RuntimeError:
                break
        assert tripped
        async with engine.begin() as con:
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.job_type == "privacy_maintenance", m.jobs.c.state == "running")
                .values(lease_expires_at=sa.func.clock_timestamp() - sa.text("interval '1 second'"))
            )
        await service.reap()
        service.crash_hook = None
        done = await drain(service, request)
        assert done["state"] == "completed" and admin.calls
        assert all(value == "done" for value in done["progress"].values())
        assert not [o for o in storage.list_objects() if not o.key.startswith("privacy/")]
        async with engine.begin() as con:
            row = (
                (
                    await con.execute(
                        sa.select(m.deletion_requests).where(
                            m.deletion_requests.c.id == request["id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert (
                row["user_id"] is None
                and row["project_id"] is None
                and row["identity_uids"] == []
                and row["target_users"] == []
            )
            audit = list((await con.execute(sa.select(m.audit_events))).mappings())
            encoded = json.dumps([dict(r) for r in audit], default=str)
            assert (
                str(owner) not in encoded
                and person.firebase_uid not in encoded
                and key not in encoded
            )
        ARTIFACTS.mkdir(exist_ok=True)
        path = ARTIFACTS / f"crash-{moment}-{step}.json"
        path.write_text(
            json.dumps(
                {
                    "moment": moment,
                    "step": step,
                    "completed": True,
                    "firebase_last": True,
                    "working_objects_remaining": 0,
                }
            )
        )

    execute(uploads_database, tmp_path, scenario)


def test_project_storage_failure_retries_and_receipts_are_owner_only(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person, other = principal(), principal()
        owner, project = await setup(identity, projects, person)
        aid, key = await seed_asset(engine, storage, owner, project)
        service = privacy(engine, identity, uploads, storage)
        body = DeletionCreate(
            scope="project", project_id=project, confirmed=True, expected_project_version=1
        )
        request = await service.create(person, body, "one")
        replay = await service.create(person, body, "one")
        assert replay == request
        with pytest.raises(ApiError) as error:
            await service.get(other, request["id"])
        assert error.value.code == "not_found"
        with pytest.raises(ApiError) as error:
            await service.create(other, body, "other")
        assert error.value.code == "not_found"
        with pytest.raises(ApiError):
            await service.create(person, DeletionCreate(scope="account", confirmed=True), "one")
        old = storage.delete_object

        def fail(key):
            raise StorageError("injected_private_storage_failure")

        storage.delete_object = fail
        for _ in range(4):
            job = await service.claim("failing")
            assert job
            await service.advance(job, "failing")
        state = await service.get(person, request["id"])
        assert state["state"] == "failed" and state["retryable"]
        assert not state["progress"].get("database") and storage.get_metadata(key)
        storage.delete_object = old
        done = await drain(service, request)
        assert done["state"] == "completed"
        async with engine.begin() as con:
            assert (
                await con.scalar(
                    sa.select(sa.func.count()).select_from(m.users).where(m.users.c.id == owner)
                )
                == 1
            )

    execute(uploads_database, tmp_path, scenario)


def test_contribution_withdrawal_dataset_race_and_working_assets_preserved(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        from app.modules.uploads.schemas import StrokeSampleCreate
        from penforge_contracts import StrokeDocument

        def drawing():
            return StrokeDocument.model_validate_json(
                Path("packages/contracts/fixtures/strokes.v1.json").read_text()
            )

        person = principal()
        owner, project = await setup(identity, projects, person)
        await configure(engine, owner)
        sample = await uploads.register_sample(
            person,
            project,
            idempotency_key="strokes",
            body=StrokeSampleCreate(source_kind="strokes", stroke_document=drawing()),
        )
        sample = sample[1] if isinstance(sample, tuple) else sample
        sid = uuid.UUID(str(sample["sample_id"]))
        service = privacy(engine, identity, uploads, storage)
        with pytest.raises(ApiError) as error:
            await service.contribute(
                person,
                ContributionCreate(
                    sample_id=sid,
                    consent_id=uuid.uuid4(),
                    policy_version="model-training-v1",
                    confirmed=True,
                ),
                "copy",
            )
        assert error.value.code == "consent_required"
        grant = await service.put_consent(
            person,
            ConsentPut(purpose="model_training", granted=True, policy_version="model-training-v1"),
        )
        await service.contribute(
            person,
            ContributionCreate(
                sample_id=sid,
                consent_id=grant["consent_id"],
                policy_version="model-training-v1",
                confirmed=True,
            ),
            "copy",
        )
        before = await service.build_dataset("dataset-before")
        assert len(before["entries"]) == 1
        withdrawn, built = await asyncio.gather(
            service.put_consent(
                person,
                ConsentPut(
                    purpose="model_training", granted=False, policy_version="model-training-v1"
                ),
            ),
            service.build_dataset("racing"),
        )
        assert len((await service.build_dataset("after-withdrawal"))["entries"]) == 0
        # The racing build may serialize wholly before withdrawal, never after it.
        assert len(built["entries"]) in (0, 1)
        request = await service.get(person, withdrawn["withdrawal_deletion_id"])
        request["progress_token"] = service.journal.progress_token(
            {
                "id": request["id"],
                "subject_ref": service.journal.reference("subject", person.firebase_uid),
            }
        )
        await drain(service, request)
        assert not storage.list_objects("dataset/")
        async with engine.begin() as con:
            assert (
                await con.scalar(
                    sa.select(sa.func.count()).select_from(m.samples).where(m.samples.c.id == sid)
                )
                == 1
            )
            assert (
                await con.scalar(
                    sa.select(sa.func.count())
                    .select_from(m.assets)
                    .where(m.assets.c.project_id == project)
                )
                > 0
            )

    execute(uploads_database, tmp_path, scenario)


def test_retention_dry_run_preserves_referenced_original_and_deletes_unused_derived(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person = principal()
        owner, project = await setup(identity, projects, person)
        original, original_key = await seed_asset(engine, storage, owner, project, age=100)
        derived, derived_key = await seed_asset(
            engine, storage, owner, project, kind="thumbnail", key="derived/unused", age=100
        )
        service = privacy(engine, identity, uploads, storage)
        report = await service.retention_report()
        assert str(derived) in report["asset_ids"] and str(original) not in report["asset_ids"]
        assert storage.get_metadata(derived_key)
        result = await service.retention_apply(report)
        assert result["objects_removed"] == 1 and storage.get_metadata(original_key)
        assert (await service.retention_apply(report))["objects_removed"] == 0
        ARTIFACTS.mkdir(exist_ok=True)
        (ARTIFACTS / "retention-dry-run.json").write_text(
            json.dumps(
                {
                    "policy": report["policy"],
                    "eligible_assets": len(report["asset_ids"]),
                    "original_preserved": True,
                    "apply": result,
                }
            )
        )

    execute(uploads_database, tmp_path, scenario)


def test_restore_journal_reconciles_old_database_without_resurrection(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person = principal()
        owner, project = await setup(identity, projects, person)
        admin = Admin(engine, [owner])
        service = privacy(engine, identity, uploads, storage, admin)
        request = await service.create(
            person, DeletionCreate(scope="account", confirmed=True), "erase"
        )
        await drain(service, request)
        # Simulate restoring the previous DB identity/project rows without restoring the independent journal.
        async with engine.begin() as con:
            await con.execute(
                m.users.insert().values(
                    id=owner, firebase_uid=person.firebase_uid, is_anonymous=False, tier="free_user"
                )
            )
            await con.execute(
                m.projects.insert().values(
                    id=project,
                    owner_user_id=owner,
                    name="Restored private name",
                    script_code="Latn",
                )
            )
        result = await service.restore_reconcile()
        assert result["reconciled_scopes"] == 1
        assert (await service.restore_reconcile())["reconciled_scopes"] == 0
        async with engine.begin() as con:
            assert (
                await con.scalar(sa.select(m.users.c.account_state).where(m.users.c.id == owner))
                == "deleting"
            )
        assert service.journal.account_blocked(person.firebase_uid)
        ARTIFACTS.mkdir(exist_ok=True)
        (ARTIFACTS / "restore-reconciliation.json").write_text(
            json.dumps(dict(result, access_revoked=True, independent_journal_required=True))
        )

    execute(uploads_database, tmp_path, scenario)


def test_claimed_guest_account_erasure_and_alias_preservation(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        guest, target = principal(True), principal()
        source, old_project = await setup(identity, projects, guest)
        destination, new_project = await setup(identity, projects, target)
        old, key = await seed_asset(engine, storage, source, old_project)
        await identity.claim(target, guest)
        # The claimed original retains its historical guest key and creator scope.
        alias, alias_key = uuid.uuid4(), "alias/independent"
        data = storage.read_object(key, max_bytes=1024)
        storage.put_immutable(alias_key, data)
        async with engine.begin() as con:
            await con.execute(
                m.assets.insert().values(
                    id=alias,
                    owner_user_id=destination,
                    project_id=new_project,
                    kind="original_upload",
                    storage_backend="local_fs",
                    storage_key=alias_key,
                    mime_type="image/png",
                    size_bytes=len(data),
                    sha256=hashlib.sha256(data).digest(),
                    state="ready",
                    duplicate_of=old,
                    verified_at=datetime.now(UTC),
                    retention_class="original",
                )
            )
        service = privacy(engine, identity, uploads, storage, Admin(engine, [source, destination]))
        request = await service.create(
            target,
            DeletionCreate(scope="project", project_id=old_project, confirmed=True),
            "project",
        )
        await drain(service, request)
        assert storage.get_metadata(alias_key)
        async with engine.begin() as con:
            assert (
                await con.scalar(sa.select(m.assets.c.duplicate_of).where(m.assets.c.id == alias))
                is None
            )
        account = await service.create(
            target, DeletionCreate(scope="account", confirmed=True), "account"
        )
        await drain(service, account)
        assert service.journal.account_blocked(guest.firebase_uid)
        async with engine.begin() as con:
            assert await con.scalar(sa.select(sa.func.count()).select_from(m.identity_claims)) == 0
            assert await con.scalar(sa.select(sa.func.count()).select_from(m.users)) == 0
        assert not [o for o in storage.list_objects() if not o.key.startswith("privacy/")]
        (ARTIFACTS / "claimed-storage-inventory.json").write_text(
            json.dumps(
                dict(
                    claimed_guest_keys_removed=True,
                    surviving_alias_preserved=True,
                    account_working_objects_remaining=0,
                )
            )
        )

    execute(uploads_database, tmp_path, scenario)


def test_live_deletion_revokes_capabilities_polling_and_stale_worker(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        from app.modules.jobs.service import JobsService
        from test_uploads_database import init_uploaded

        person = principal()
        owner, project = await setup(identity, projects, person)
        await configure(engine, owner)
        created_asset, created = await init_uploaded(uploads, person, project)
        job = await queue.claim("old-worker")
        service = privacy(engine, identity, uploads, storage)
        request = await service.create(
            person, DeletionCreate(scope="project", project_id=project, confirmed=True), "delete"
        )
        assert not await queue.heartbeat(
            job["id"], "old-worker", job["attempts"], owner, stage="decode", completed_units=1
        )
        with pytest.raises(ApiError):
            await JobsService(uploads).get(person, job["id"])
        with pytest.raises(ApiError):
            await uploads.renew_upload(person, created_asset)
        cleanup = await service.claim("cleanup")
        assert cleanup
        await service.advance(cleanup, "cleanup")
        state = await service.get(person, request["id"])
        assert state["progress"]["jobs"] == "waiting" and "storage" not in state["progress"]
        async with engine.begin() as con:
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job["id"])
                .values(lease_expires_at=sa.func.clock_timestamp() - sa.text("interval '1 second'"))
            )
        await drain(service, request)

    execute(uploads_database, tmp_path, scenario)


def test_rejected_cross_project_alias_keeps_canonical_bytes(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person = principal()
        owner, project = await setup(identity, projects, person)
        _, other = await setup(identity, projects, person)
        original, key = await seed_asset(engine, storage, owner, project)
        alias = uuid.uuid4()
        data = storage.read_object(key, max_bytes=1024)
        async with engine.begin() as con:
            await con.execute(
                m.assets.insert().values(
                    id=alias,
                    owner_user_id=owner,
                    project_id=other,
                    kind="original_upload",
                    storage_backend="local_fs",
                    storage_key="alias/reaped",
                    mime_type="image/png",
                    size_bytes=len(data),
                    sha256=hashlib.sha256(data).digest(),
                    state="rejected",
                    reject_reason="duplicate",
                    duplicate_of=original,
                    retention_class="original",
                )
            )
        service = privacy(engine, identity, uploads, storage)
        request = await service.create(
            person, DeletionCreate(scope="project", project_id=project, confirmed=True), "delete"
        )
        await drain(service, request)
        async with engine.begin() as con:
            canonical = await con.scalar(
                sa.select(m.assets.c.duplicate_of).where(m.assets.c.id == alias)
            )
            key = await con.scalar(
                sa.select(m.assets.c.storage_key).where(m.assets.c.id == canonical)
            )
        assert storage.read_object(key, max_bytes=1024) == data

    execute(uploads_database, tmp_path, scenario)


def test_real_worker_stops_when_deletion_revokes_its_scope(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        import os
        import subprocess
        import sys
        import time

        from test_uploads_database import init_uploaded
        from test_worker_runtime import png

        person = principal()
        owner, project = await setup(identity, projects, person)
        await configure(engine, owner)
        await init_uploaded(uploads, person, project, png())
        marker, release = tmp_path / "hash-complete", tmp_path / "release"
        child = tmp_path / "privacy_worker.py"
        child.write_text("""import asyncio,sys
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from app.adapters.storage.local_fs import LocalObjectStorage
from app.modules.identity.service import IdentityService
from app.modules.uploads.service import UploadsService,QueueService
from services.worker.runtime import VerificationWorker
class PausedQueue(QueueService):
 async def heartbeat(self,*args,**kwargs):
  valid=await super().heartbeat(*args,**kwargs)
  if valid and kwargs.get("stage")=="decode" and not Path(sys.argv[4]).exists():
   Path(sys.argv[4]).write_text("ready")
   while not Path(sys.argv[5]).exists(): await asyncio.sleep(.02)
  return valid
async def main():
 engine=create_async_engine(sys.argv[1],connect_args={"options":"-csearch_path="+sys.argv[2]})
 storage=LocalObjectStorage(Path(sys.argv[3]),secret=b"s"*32)
 uploads=UploadsService(engine,IdentityService(engine),storage,derivative_max_bytes=1024)
 queue=PausedQueue(engine,uploads,lease_seconds=2,retry_backoff_seconds=0)
 worker=VerificationWorker(queue,uploads,storage,worker_id="privacy-real-worker",heartbeat_seconds=.1,temp_root=Path(sys.argv[6]))
 await worker.run_once()
 storage.close();await engine.dispose()
asyncio.run(main())
""")
        process = subprocess.Popen(
            [
                sys.executable,
                str(child),
                sync.url.render_as_string(hide_password=False),
                uploads_database[1],
                str(storage.root),
                str(marker),
                str(release),
                str(tmp_path / "worker-temp"),
            ],
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    str(Path.cwd() / p) for p in ("apps/api", "packages/contracts", ".")
                ),
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            while not marker.exists():
                assert process.poll() is None, (
                    process.stderr.read().decode() if process.stderr else "worker exited"
                )
                assert time.monotonic() < deadline
                await asyncio.sleep(0.02)
            service = privacy(engine, identity, uploads, storage)
            request = await service.create(
                person,
                DeletionCreate(scope="project", project_id=project, confirmed=True),
                "delete",
            )
            release.write_text("continue")
            await asyncio.to_thread(process.wait, 10)
            assert process.returncode == 0, process.stderr.read().decode()
            async with engine.begin() as con:
                assert not await con.scalar(
                    sa.select(sa.func.count())
                    .select_from(m.assets)
                    .where(m.assets.c.project_id == project, m.assets.c.state == "ready")
                )
                await con.execute(
                    m.jobs.update()
                    .where(m.jobs.c.project_id == project, m.jobs.c.state == "running")
                    .values(
                        lease_expires_at=sa.func.clock_timestamp() - sa.text("interval '1 second'")
                    )
                )
            await drain(service, request)
            assert not [o for o in storage.list_objects() if not o.key.startswith("privacy/")]
            (ARTIFACTS / "real-worker-cancellation.json").write_text(
                json.dumps(
                    dict(
                        actual_worker_process=True,
                        deletion_revoked_scope=True,
                        stale_publication_blocked=True,
                        working_objects_remaining=0,
                    )
                )
            )
        finally:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait)

    execute(uploads_database, tmp_path, scenario)
