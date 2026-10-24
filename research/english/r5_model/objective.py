"""Aligned teacher-forced training loss for R5.

The R2 audit found that Transformers 4.57.6's ``VisionEncoderDecoderModel`` shifts
decoder inputs and then falls back to a causal loss that shifts targets a second time.
The shared repair in :mod:`research.pretrained.runtime` builds decoder inputs once and
scores unchanged labels. R5 reuses that repair rather than reimplementing it, so both
experiments share one audited objective.
"""

from __future__ import annotations

import math


def teacher_forced_loss(torch, model, pixel_values, labels):
    """Cross entropy at the decoder position that predicts each label token."""
    if getattr(labels, "ndim", None) != 2:
        raise ValueError("r5_labels_must_be_a_2d_batch")
    if getattr(pixel_values, "shape", (0,))[0] != labels.shape[0]:
        raise ValueError("r5_pixel_and_label_batch_sizes_differ")
    from research.pretrained.runtime import teacher_forced_loss as shared_loss

    loss = shared_loss(torch, model, pixel_values, labels)
    value = float(loss.detach())
    if not math.isfinite(value):
        raise ValueError("r5_nonfinite_training_loss")
    return loss


def assert_loss_decreased_below_chance(loss_value: float, classes: int) -> None:
    """Cheap mechanical guard: an aligned objective on real labels must beat chance.

    This is not a training-quality gate; it catches grossly misaligned targets, which
    would sit near ``ln(classes)`` from the first batch onward.
    """
    if classes < 2:
        raise ValueError("r5_loss_probe_requires_multiple_classes")
    if not math.isfinite(loss_value):
        raise ValueError("r5_nonfinite_loss_probe")
    if loss_value >= math.log(classes) + 1.0:
        raise ValueError("r5_loss_far_above_chance_suggests_misaligned_targets")
