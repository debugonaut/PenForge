"""Independent scalar/cluster reports for future admitted observations only."""

from penforge_contracts.unicode import graphemes, scalars

from research.evaluation.harness import cer


def recognition_metrics(pairs):
    return dict(
        scalar_cer=cer([(scalars(a), scalars(b)) for a, b in pairs]),
        cluster_cer=cer([(graphemes(a), graphemes(b)) for a, b in pairs]),
        segmentation_split_errors=None,
        segmentation_merge_errors=None,
        boundary_fidelity=None,
        writer_fidelity=None,
        corrections=None,
        review_effort=None,
        resources=None,
        shaping_gate_satisfied=False,
    )
