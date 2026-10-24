import pytest
from pydantic import ValidationError

from research.evaluation.production_deva_corpus import ProductionCorpus, ingest


def test_starter_cannot_become_production_corpus():
    with pytest.raises(ValidationError):
        ProductionCorpus(corpus="starter", version="v1", words=[])


def test_missing_or_synthetic_provenance_rejected_before_execution():
    value = dict(
        corpus="synthetic-contract-test-only",
        version="1",
        source_uri="fixture",
        license="CC0",
        license_status="independently_reviewed",
        license_evidence="sha256:" + "0" * 64,
        content_hash="sha256:" + "0" * 64,
        tokenization="unicode_whitespace_v1",
        normalization_policy="preserve_input",
        frequency_method="count_normalized_tokens_v1",
        sampling_method="top_n_frequency_scalar_tiebreak_v1",
        reviewer_evidence="sha256:" + "0" * 64,
        evidence_domain="synthetic",
        words=[
            dict(text="क" + str(i), codepoints=list(map(ord, "क" + str(i))), frequency=1)
            for i in range(200)
        ],
    )
    with pytest.raises(ValidationError):
        ProductionCorpus.model_validate(value)
    value["evidence_domain"] = "licensed_natural_text"
    manifest = ProductionCorpus.model_validate(value)
    with pytest.raises(ValueError, match="content_hash"):
        ingest(manifest, b"not the pinned source")
