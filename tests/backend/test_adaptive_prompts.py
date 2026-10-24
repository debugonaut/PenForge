import pytest
from app.modules.uploads.schemas import PromptContext
from test_glyphs_database import action, seed, select
from test_uploads_database import execute
from test_uploads_database import uploads_database as uploads_database


@pytest.mark.integration
def test_prompts_follow_observed_review_evidence(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads
        )
        before = await service.prompts(person, project)
        assert "uncertain_assignment" in before["selection_reasons"]["65"]
        assert 65 not in before["observed_approved_codepoints"]
        current = await select(service, person, project, glyph, candidate)
        await action(service, person, project, glyph, current, "approve")
        after = await service.prompts(person, project)
        assert 65 in after["observed_approved_codepoints"]
        assert after["evidence_hash"] != before["evidence_hash"]
        assert after["prompt_is_ground_truth"] is False
        with pytest.raises(ValueError):
            PromptContext(
                prompt_policy_version="coverage-followup-v2",
                expected_codepoints=[65],
                selection_reasons={},
                evidence_hash=after["evidence_hash"],
                prompt_is_ground_truth=True,
            )

    execute(uploads_database, tmp_path, scenario)
