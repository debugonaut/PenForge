"""Bounded R5 English-letter model package (Part B).

Importing this package is side-effect free: no Torch import, no model load, no random
seeding, no environment mutation. The owner-run entry point is
:func:`research.english.r5_model.experiment.run_bounded_training`; Part A owns the
shell wrapper, the final-test invocation and the authoritative metric.

Frozen boundary with Part A
---------------------------
Rows keep exactly these fields: ``id``, ``hash``, ``path``, ``pixel_hash``, ``label``,
``writer``, ``source_group``, ``source_partition``, ``split`` and ``source_member``.
``path`` is a staging-relative content address (``images/<sha256>.png``). Only
``train`` and ``validation`` rows are accepted; any ``test`` row is a hard error.

Prediction records are ``{"sample_id", "truth", "prediction"}``. The injected scorer is
called as ``scorer(records, rows)`` (or ``scorer(records)``) and must return a mapping
with ``accuracy``, ``correct``, ``n``, ``scalar_cer`` and ``exact_case_sensitive: true``;
optional ``per_crop_ms_p50`` breaks ties. Part A's ID join remains authoritative.
"""

from research.english.r5_model.evaluate import PredictionRecord
from research.english.r5_model.experiment import (
    Preflight,
    TrainingPlan,
    load_plan,
    preflight,
    run_bounded_training,
)
from research.english.r5_model.recipe import R5ModelRecipe, load_recipe, recipe_hash
from research.english.r5_model.rows import CLASSES, SAMPLE_FIELDS, StudyRows, load_study
from research.english.r5_model.selection import PassResult, select_best

__all__ = [
    "CLASSES",
    "PassResult",
    "PredictionRecord",
    "Preflight",
    "R5ModelRecipe",
    "SAMPLE_FIELDS",
    "StudyRows",
    "TrainingPlan",
    "load_plan",
    "load_recipe",
    "load_study",
    "preflight",
    "recipe_hash",
    "run_bounded_training",
    "select_best",
]
