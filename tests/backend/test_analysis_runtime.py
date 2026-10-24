"""Actual process loss after inventoried immutable extraction output."""

import asyncio
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.db import models as m
from app.modules.analysis.service import AnalysisService
from test_analysis_database import drawing
from test_uploads_database import execute
from test_uploads_database import uploads_database as uploads_database

from services.worker.analysis import AnalysisWorker

pytestmark = pytest.mark.integration


def test_sigkill_after_staged_output_releases_quota_then_new_attempt_succeeds(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, sample, body = await drawing(engine, identity, projects, uploads)
        api = AnalysisService(engine, identity, uploads, storage, lease_seconds=1)
        _, response = await api.create(owner, project, uuid.uuid4().hex, body)
        marker = tmp_path / "written"
        script = tmp_path / "kill_analysis.py"
        script.write_text("""
import asyncio,sys,time
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from app.adapters.storage.local_fs import LocalObjectStorage
from app.modules.identity.service import IdentityService
from app.modules.uploads.service import UploadsService
from app.modules.analysis.service import AnalysisService
from services.worker.analysis import AnalysisWorker
class PausedStorage(LocalObjectStorage):
 def put_immutable(self,*args,**kwargs):
  result=super().put_immutable(*args,**kwargs)
  Path(sys.argv[4]).write_text('written')
  time.sleep(30)
  return result
async def main():
 engine=create_async_engine(sys.argv[1],connect_args={'options':'-csearch_path='+sys.argv[2]})
 storage=PausedStorage(Path(sys.argv[3]),secret=b's'*32)
 identity=IdentityService(engine)
 uploads=UploadsService(engine,identity,storage)
 service=AnalysisService(engine,identity,uploads,storage,lease_seconds=1)
 await AnalysisWorker(service,worker_id='killed',temp_root=Path(sys.argv[5]),heartbeat_seconds=.1).run_once()
asyncio.run(main())
""")
        process = subprocess.Popen(
            [
                sys.executable,
                str(script),
                sync.url.render_as_string(hide_password=False),
                uploads_database[1],
                str(storage.root),
                str(marker),
                str(tmp_path / "attempts"),
            ],
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    [
                        str(Path.cwd() / "apps/api"),
                        str(Path.cwd() / "packages/contracts"),
                        str(Path.cwd()),
                    ]
                ),
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            while not marker.exists():
                if process.poll() is not None:
                    raise AssertionError(
                        (process.stderr.read() if process.stderr else b"").decode()
                    )
                assert time.monotonic() < deadline
                await asyncio.sleep(0.02)
            process.send_signal(signal.SIGKILL)
            await asyncio.to_thread(process.wait, 5)
            assert process.returncode == -signal.SIGKILL
            job_id = uuid.UUID(response["job_id"])
            deadline = time.monotonic() + 4
            while await api.attempt_active(job_id, 1):
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            worker = AnalysisWorker(
                api, worker_id="recovered", temp_root=tmp_path / "attempts", heartbeat_seconds=0.1
            )
            assert await worker.run_once()
            final = await api.get(owner, project, job_id)
            assert final["state"] == "succeeded" and final["attempts"] == 2
            with sync.connect() as con:
                assert con.scalar(sa.select(sa.func.count()).select_from(m.glyph_candidates)) == 1
                balances = (
                    con.execute(
                        sa.select(m.quota_balances).where(
                            m.quota_balances.c.resource == "storage_bytes"
                        )
                    )
                    .mappings()
                    .all()
                )
            physical = sum(obj.size_bytes for obj in storage.list_objects())
            assert all(b["reserved"] == 0 and b["committed"] == physical for b in balances)
            assert (await uploads.reconcile())["orphan_keys"] == []
            assert not list((tmp_path / "attempts").iterdir())
        finally:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait, 5)
            if process.stderr:
                process.stderr.close()

    execute(uploads_database, tmp_path, scenario)
