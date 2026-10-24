import json
from pathlib import Path

import pytest

from packages.extraction.freeform import experiment


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"experimental_opt_in": True},
        {"experimental_opt_in": True, "fixture_domain": "private_handwriting"},
    ],
)
def test_freeform_is_unavailable_without_synthetic_optin(kwargs):
    with pytest.raises(ValueError):
        experiment([[True]], "e", **kwargs)


def test_connected_region_is_preserved_and_prompt_mismatch_cannot_assign_identity():
    mask = [[False] * 12 for _ in range(6)]
    for y in (1, 2, 3):
        for x in range(1, 10):
            mask[y][x] = True
    report = experiment(
        mask,
        "hello",
        experimental_opt_in=True,
        fixture_domain="synthetic_control",
        observed_fixture_words=["other"],
    )
    assert report["alignment_status"] == "mismatch"
    assert len(report["hypotheses"]) == 1
    assert report["hypotheses"][0]["character_boundaries"] == []
    assert report["hypotheses"][0]["approved_identity"] is None
    assert report["requires_isolated_recapture"]
    folder = Path("artifacts/phase08")
    folder.mkdir(exist_ok=True)
    (folder / "freeform-synthetic-control.json").write_text(json.dumps(report, indent=2))
