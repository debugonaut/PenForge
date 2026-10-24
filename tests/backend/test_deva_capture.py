import uuid

import pytest
import sqlalchemy as sa
from app.db import models as m
from app.modules.analysis.schemas import AnalysisCreate
from app.modules.analysis.service import AnalysisService
from app.modules.glyphs.service import GlyphsService
from test_analysis_database import drawing
from test_uploads_database import execute
from test_uploads_database import uploads_database as uploads_database

from services.worker.analysis import AnalysisWorker

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("cps", [[0x915, 0x93C], [0x958], [0x915, 0x94D, 0x937]])
def test_deva_digital_preserves_sequence_and_never_auto_approves(uploads_database, tmp_path, cps):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, sample, body = await drawing(engine, identity, projects, uploads)
        async with engine.begin() as con:
            await con.execute(
                m.projects.update().where(m.projects.c.id == project).values(script_code="Deva")
            )
        value = body.model_dump(mode="json")
        value["assignments"][0]["codepoints"] = cps
        api = AnalysisService(engine, identity, uploads, storage)
        _, created = await api.create(
            owner, project, uuid.uuid4().hex, AnalysisCreate.model_validate(value)
        )
        assert await AnalysisWorker(
            api, worker_id="deva", temp_root=tmp_path / "attempts"
        ).run_once()
        state = await api.get(owner, project, uuid.UUID(created["job_id"]))
        assert state["state"] == "succeeded"
        with sync.connect() as con:
            glyph = con.execute(sa.select(m.glyphs)).mappings().one()
            assert glyph["codepoints"] == cps and glyph["active_revision_id"] is None
            candidate = con.execute(sa.select(m.glyph_candidates)).mappings().one()
            assert candidate["review_status"] == "pending"
        assert (await GlyphsService(engine, identity, storage).get(owner, project, glyph["id"]))[
            "codepoints"
        ] == cps

    execute(uploads_database, tmp_path, scenario)
