import pytest
from penforge_contracts.capabilities import CapabilityKey, Qualification
from penforge_contracts.planning import Component, plan, structured_route

KEY = CapabilityKey(task="recognition", script="Latn", style="print", input_class="scan")


def component(name, quality, memory):
    q = Qualification(
        key=KEY,
        state="production_validated",
        required_dimensions=["recognition", "writer_fidelity"],
        evidence={"recognition": "sha256:" + "1" * 64, "writer_fidelity": "sha256:" + "2" * 64},
        heldout_real_writers=True,
        independent_approval="sha256:" + "3" * 64,
    )
    return Component(
        id=name,
        version_hash="sha256:" + "4" * 64,
        qualification=q,
        tier="T1",
        worker_versions=["0.1.0"],
        quality_order=quality,
        memory_mb=memory,
    )


@pytest.mark.parametrize(
    "degradation,quota,memory", [(0, True, 128), (1, True, 2048), (0, False, 2048), (4, True, 2048)]
)
def test_resources_never_select_weaker_method(degradation, quota, memory):
    out = plan(
        KEY,
        [component("fast", 1, 16), component("fidelity", 2, 1024)],
        worker_version="0.1.0",
        memory_mb=memory,
        hardware_id="test",
        quota_available=quota,
        degradation_level=degradation,
    )
    assert out.state == "queued" and out.component_id == "fidelity"
    assert out.estimated_seconds is None and out.quality_policy == "accuracy_first"


def test_regression_and_unmeasured_model_do_not_qualify():
    body = component("fast", 1, 16).qualification.model_dump()
    with pytest.raises(ValueError):
        Qualification(**(body | {"regressed_dimensions": ["writer_fidelity"]}))
    out = plan(
        KEY, [], worker_version="0.1.0", memory_mb=1000, hardware_id="test", quota_available=True
    )
    assert out.state == "unsupported"
    assert structured_route("freeform_photo")["method"] == "isolated_recapture"
