"""Sequential real pipeline modes, measured synthetic resources and release compatibility."""

import asyncio
import json
import platform
import resource
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.db import models as m
from app.modules.analysis.service import AnalysisService
from app.modules.exports.service import ExportsService
from test_analysis_database import drawing
from test_database import migrate
from test_exports_database import create, prepared
from test_uploads_database import configure, create_project, execute, init_uploaded, principal
from test_uploads_database import uploads_database as uploads_database
from test_worker_runtime import png

from packages.capture_safety import DecodeLimits
from services.worker.analysis import AnalysisWorker
from services.worker.exports import ExportWorker
from services.worker.runtime import PipelineWorker

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("handler", ["analysis", "exports"])
def test_stage_resource_shortage_is_typed_and_never_weakens_quality(
    uploads_database, tmp_path, monkeypatch, handler
):
    from app.modules.jobs.service import JobsService

    from packages.capture_safety.decoder import ImageRejected
    from services.worker import analysis as analysis_runtime
    from services.worker import exports as export_runtime

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        def unavailable(*args, **kwargs):
            if handler == "analysis":
                raise ImageRejected("analysis_resource_limit")
            raise ValueError("font_resource_limit")

        if handler == "analysis":
            owner, _, project, _, body = await drawing(engine, identity, projects, uploads)
            service = AnalysisService(engine, identity, uploads, storage)
            await service.create(owner, project, uuid.uuid4().hex, body)
            worker = AnalysisWorker(service, worker_id="capacity", temp_root=tmp_path / "attempts")
            monkeypatch.setattr(analysis_runtime, "analyze_isolated", unavailable)
        else:
            owner, _, project, snapshot = await prepared(
                engine, identity, projects, storage, uploads
            )
            service = ExportsService(engine, identity, uploads, storage)
            await create(service, owner, project, snapshot)
            worker = ExportWorker(service, worker_id="capacity", temp_root=tmp_path / "attempts")
            monkeypatch.setattr(export_runtime, "compile_isolated", unavailable)
        assert await worker.run_once()
        async with engine.connect() as con:
            job = (await con.execute(sa.select(m.jobs))).mappings().one()
        status = await JobsService(uploads).get(owner, job["id"])
        assert status["state"] == "queued" and status["retrying"] is True
        assert status["error_code"] == "resource_unavailable"
        assert status["quality_policy"] == "accuracy_first"
        assert status["eta_seconds"] is None
        assert not list((tmp_path / "attempts").iterdir())

    execute(uploads_database, tmp_path, scenario)


def test_actual_pipeline_scheduled_drain_and_persistent_idle_resource_report(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        await init_uploaded(uploads, owner, project, png())
        analysis_owner, _, analysis_project, _, analysis_body = await drawing(
            engine, identity, projects, uploads
        )
        analysis = AnalysisService(engine, identity, uploads, storage)
        await analysis.create(analysis_owner, analysis_project, uuid.uuid4().hex, analysis_body)
        font_owner, _, font_project, snapshot = await prepared(
            engine, identity, projects, storage, uploads
        )
        exports = ExportsService(engine, identity, uploads, storage)
        receipt = await create(exports, font_owner, font_project, snapshot)
        worker = PipelineWorker(
            queue,
            uploads,
            storage,
            worker_id="profile-verification",
            limits=DecodeLimits(),
            analysis=AnalysisWorker(
                analysis,
                worker_id="profile-analysis",
                temp_root=tmp_path / "attempts",
                heartbeat_seconds=0.1,
            ),
            exports=ExportWorker(
                exports,
                worker_id="profile-exports",
                temp_root=tmp_path / "attempts",
                heartbeat_seconds=0.1,
            ),
            heartbeat_seconds=0.1,
            temp_root=tmp_path / "attempts",
        )
        began = time.perf_counter()
        await worker.run(mode="scheduled", max_seconds=30)
        elapsed = time.perf_counter() - began
        async with engine.connect() as con:
            jobs = list(
                (await con.execute(sa.select(m.jobs).order_by(m.jobs.c.started_at))).mappings()
            )
            assets = list(
                (
                    await con.execute(sa.select(m.assets).where(m.assets.c.kind == "checkpoint"))
                ).mappings()
            )
            balances = list((await con.execute(sa.select(m.quota_balances))).mappings())
        assert len(jobs) == 3 and {j["job_type"] for j in jobs} == {
            "asset_verify",
            "analyze_samples",
            "compile_export",
        }
        assert all(j["state"] == "succeeded" for j in jobs)
        assert all(b["reserved"] == 0 and b["committed"] <= b["limit_amount"] for b in balances)
        assert not list((tmp_path / "attempts").iterdir())
        reports = []
        for asset in assets:
            if asset["mime_type"] != "application/json":
                continue
            value = json.loads(
                storage.read_object(asset["storage_key"], max_bytes=32 * 1024 * 1024)
            )
            if "resources" in value:
                reports.append(
                    dict(
                        stage="analyze_sample" if "files" in value else "compile_validate_format",
                        **value["resources"],
                    )
                )
            elif "original_sha256" in value and "metrics" in value:
                reports.append(dict(stage="strict_decode", **value["metrics"]))
        assert Counter(r["stage"] for r in reports) == {
            "strict_decode": 1,
            "analyze_sample": 1,
            "compile_validate_format": 3,
        }
        assert all(r.get("wall_seconds", r.get("wall_ms", 0)) >= 0 for r in reports)
        output = dict(
            date="2026-10-20",
            synthetic=True,
            worker_processes=1,
            scheduled_job_types=3,
            formats=3,
            scheduled_elapsed_seconds=elapsed,
            hardware=dict(
                platform=platform.platform(),
                machine=platform.machine(),
                python=sys.version,
                parent_process_lifetime_peak_rss_bytes=resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss
                * (1 if sys.platform == "darwin" else 1024),
            ),
            configuration=dict(
                lease_seconds=queue.lease_seconds,
                control_poll_seconds=0.1,
                child_memory_limit_mb=768,
                capture_wall_limit_seconds=20,
                analysis_wall_limit_seconds=30,
                format_wall_limit_seconds=60,
            ),
            jobs=[
                dict(
                    type=j["job_type"],
                    queue_wait_ms=(j["started_at"] - j["created_at"]).total_seconds() * 1000,
                    active_measured_seconds=j["usage"].get("wall_seconds"),
                    attempts=j["attempts"],
                    state=j["state"],
                )
                for j in jobs
            ],
            stages=reports,
            production_capacity=False,
            eta_profile_qualified=False,
        )
        # Same runtime, same protocol; persistent idle is interruptible and does not claim phantom work.
        task = asyncio.create_task(worker.run(mode="persistent", max_seconds=10))
        await asyncio.sleep(0.1)
        worker.stop.set()
        await asyncio.wait_for(task, 2)
        output["persistent_idle_clean_stop"] = True
        output["accepted_exports"] = [
            (await exports.get(font_owner, uuid.UUID(e)))["status"] for e in receipt["export_ids"]
        ]
        Path("artifacts/phase06/resources.json").write_text(json.dumps(output, indent=2) + "\n")

    execute(uploads_database, tmp_path, scenario)


def test_previous_release_seeded_jobs_and_terminal_records_survive_up_down_up(uploads_database):
    sync, _ = uploads_database
    migrate(sync, "downgrade", "0008_font_exports")
    owner, project, queued, terminal = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with sync.begin() as con:
        con.execute(m.users.insert().values(id=owner, firebase_uid=uuid.uuid4().hex))
        con.execute(
            m.projects.insert().values(
                id=project, owner_user_id=owner, name="Previous release fixture", script_code="Latn"
            )
        )
        # Legacy job IDs and untouched v1 payload bytes, including a terminal unknown type.
        for jid, kind, payload in (
            (queued, "asset_verify", {"schema_version": 1, "asset_id": str(uuid.uuid4())}),
            (terminal, "previous_release_fixture", {"schema_version": 1}),
        ):
            con.execute(
                m.jobs.insert().values(
                    id=jid,
                    owner_user_id=owner,
                    project_id=project,
                    job_type=kind,
                    payload_schema_version=1,
                    payload=payload,
                )
            )
        con.execute(
            m.jobs.update()
            .where(m.jobs.c.id == terminal)
            .values(
                state="running",
                attempts=1,
                lease_holder="legacy",
                lease_expires_at=sa.func.now() + sa.text("interval '60 seconds'"),
            )
        )
        con.execute(
            m.jobs.update()
            .where(m.jobs.c.id == terminal)
            .values(state="succeeded", lease_holder=None, lease_expires_at=None)
        )
        original = list(con.execute(sa.select(m.jobs).order_by(m.jobs.c.id)).mappings())
    for direction, revision in (
        ("upgrade", "head"),
        ("downgrade", "0008_font_exports"),
        ("upgrade", "head"),
    ):
        migrate(sync, direction, revision)
        with sync.connect() as con:
            current = list(con.execute(sa.select(m.jobs).order_by(m.jobs.c.id)).mappings())
        assert [dict(j) for j in current] == [dict(j) for j in original]
    Path("artifacts/phase06/previous-release-migration.json").write_text(
        json.dumps(
            dict(
                previous="0008_font_exports",
                current="0009_queue_recovery",
                queued_v1_preserved=True,
                terminal_preserved=True,
                up_down_up=True,
            ),
            indent=2,
        )
        + "\n"
    )
