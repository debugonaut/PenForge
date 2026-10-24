import pytest
from penforge_contracts.capabilities import CapabilityKey, Qualification, SyntheticProposal
from pydantic import ValidationError


def test_production_requires_independent_real_matching_evidence():
    key = CapabilityKey(task="recognition", script="Latn", style="print", input_class="scan")
    q = Qualification(key=key, required_dimensions=["recognition", "writer_fidelity"])
    assert q.state == "research_only"
    with pytest.raises(ValidationError):
        Qualification(**(q.model_dump() | {"state": "production_validated"}))
    with pytest.raises(ValidationError):
        Qualification(**(q.model_dump() | {"required_dimensions": ["overall_accuracy"]}))


def test_synthetic_boundary_cannot_activate():
    with pytest.raises(ValidationError):
        SyntheticProposal(origin="style_conditioned_generation", approval="approved")
    assert SyntheticProposal(origin="geometric_reconstruction").authentic_coverage is False
