import hashlib
from pathlib import Path

from penforge_contracts.devanagari import coverage_evidence, inventory, tier1


def test_tier_one_is_pinned_and_source_backed():
    value = inventory()
    assert value["required_set_id"] == "deva-tier1-ucd17-v1"
    root = Path(__file__).parents[2] / "packages/contracts/unicode/17.0.0"
    for name, digest in value["sources"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
    assert {
        0x915,
        0x958,
        0x93C,
        0x94D,
        0x901,
        0x902,
        0x903,
        0x964,
        0x965,
        *range(0x966, 0x970),
    } <= tier1()
    assert 0x200D not in tier1()
    assert value["tier2"]["status"] == "unavailable"


def test_counts_do_not_claim_cluster_readiness():
    rows = [
        dict(approval="approved", outline=dict(codepoints=[cp], mapping="cmap")) for cp in tier1()
    ]
    value = coverage_evidence(rows, requested_text="कि")
    assert not value["missing"]
    assert value["missing_clusters"] == [[0x915, 0x93F]]
    assert not value["complete_devanagari"]
    assert value["shaping_validation_state"] == "unverified"
