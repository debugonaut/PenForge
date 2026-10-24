import pytest
from penforge_contracts.recognition import DEVA_CAPABILITIES, RecognitionHypothesis
from pydantic import ValidationError

from research.evaluation.deva_recognition import recognition_metrics


def test_recognition_does_not_approve_identity_or_shaping():
    with pytest.raises(ValidationError):
        RecognitionHypothesis(
            source_hash="sha256:" + "a" * 64,
            style="print",
            codepoints=[0x915],
            activates_identity=True,
        )
    r = recognition_metrics([("कि", "कु")])
    assert r["scalar_cer"] == 0.5 and r["cluster_cer"] == 1.0
    assert not r["shaping_gate_satisfied"] and r["writer_fidelity"] is None
    assert recognition_metrics([])["cluster_cer"] is None
    assert DEVA_CAPABILITIES["recognition"] == "research_only"
    assert not DEVA_CAPABILITIES["production_validated"]
