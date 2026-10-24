"""Deterministic warmup then linear-decay schedule for the bounded R5 passes.

R4 used a constant learning rate with no warmup and no decay for its single pass. R5
warm-starts a fine-tuned decoder, so the schedule starts gently, decays toward a floor
and is fixed in advance: no schedule is chosen from validation results.
"""

from __future__ import annotations


def learning_rate_multiplier(
    step: int,
    *,
    total_steps: int,
    warmup_steps: int,
    final_ratio: float,
) -> float:
    if total_steps < 1:
        raise ValueError("r5_schedule_requires_positive_total_steps")
    if not 0 <= warmup_steps <= total_steps:
        raise ValueError("r5_schedule_warmup_outside_step_budget")
    if not 0 < final_ratio <= 1:
        raise ValueError("r5_schedule_final_ratio_must_be_in_(0,1]")
    if not 1 <= step <= total_steps:
        raise ValueError("r5_schedule_step_outside_step_budget")
    if warmup_steps and step <= warmup_steps:
        return step / warmup_steps
    decay_steps = total_steps - warmup_steps
    if decay_steps <= 1:
        return 1.0
    progress = (step - warmup_steps - 1) / (decay_steps - 1)
    return 1.0 - (1.0 - final_ratio) * progress


def apply_multiplier(optimizer, base_learning_rate: float, multiplier: float) -> float:
    if not 0 < base_learning_rate:
        raise ValueError("r5_base_learning_rate_must_be_positive")
    if not 0 < multiplier <= 1:
        raise ValueError("r5_schedule_multiplier_must_be_in_(0,1]")
    rate = base_learning_rate * multiplier
    for group in optimizer.param_groups:
        group["lr"] = rate
    return rate
