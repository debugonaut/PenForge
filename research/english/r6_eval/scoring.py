"""Authoritative raw scorer and paired validation comparison; imports no model."""

from __future__ import annotations

import random
from collections import defaultdict

from research.english.r5_eval.scoring import _percentile, score_predictions, validation_scorer


def paired_comparison(rows, control, candidate, *, seed=20261023, rounds=2000):
    before = score_predictions(rows, control, seed=seed, rounds=rounds)
    after = score_predictions(rows, candidate, seed=seed, rounds=rounds)
    old = {r["sample_id"]: r["prediction"] for r in control}
    new = {r["sample_id"]: r["prediction"] for r in candidate}
    counts = dict(fixed=0, regressed=0, persistent_error=0, correct_control=0)
    writers = defaultdict(list)
    for row in rows:
        a, b = old[row["id"]] == row["label"], new[row["id"]] == row["label"]
        group = (
            "fixed"
            if b and not a
            else "regressed"
            if a and not b
            else "correct_control"
            if a
            else "persistent_error"
        )
        counts[group] += 1
        writers[row["writer"]].append(int(b) - int(a))
    clusters = [writers[key] for key in sorted(writers)]
    rng = random.Random(seed)
    gains = []
    for _ in range(rounds):
        draw = rng.choices(clusters, k=len(clusters))
        gains.append(sum(sum(group) for group in draw) / sum(map(len, draw)))
    return {
        "control": before,
        "candidate": after,
        "paired": counts,
        "accuracy_gain": after["accuracy"] - before["accuracy"],
        "paired_writer_cluster_gain_95_ci": [_percentile(gains, q) for q in (0.025, 0.975)],
        "bootstrap": {"seed": seed, "rounds": rounds, "writers": len(clusters)},
        "limitation": "Validation selection is adaptive; intervals do not remove selection optimism.",
    }


__all__ = ["score_predictions", "validation_scorer", "paired_comparison"]
