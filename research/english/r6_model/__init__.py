"""Bounded R6 English-letter continuation package (Part B).

Importing this package is side-effect free: no Torch import, no model load, no random
seeding, no environment mutation. The owner-run entry points are
:func:`research.english.r6_model.experiment.run_bounded_training` and
:func:`research.english.r6_model.experiment.evaluate_control`; Part A owns data
preparation, the authoritative scorer, the source freeze and the final test.

Frozen boundary with Part A
---------------------------
Rows are Part A's ten-field ``train``/``validation`` rows
(:mod:`research.english.r6_contract`); any ``test`` row is a hard error. Prediction
records are ``{"sample_id", "truth", "prediction"}`` and the injected scorer is called as
``scorer(records)`` or ``scorer(records, rows)`` and must return ``accuracy``, ``correct``,
``n``, ``scalar_cer`` and ``exact_case_sensitive: true``; optional ``per_crop_ms_p50``
breaks ties.

Initialization is the frozen R5 composition: the exact R4 full checkpoint plus the
selected 53-tensor R5 trainable delta. There is no fallback to pretrained or R4-only
weights, and the delta is rejected if it does not cover exactly the R5 trainable scope.
"""

from research.english.r6_model.checkpoint import PassCommit
from research.english.r6_model.evaluate import PredictionRecord
from research.english.r6_model.experiment import (
    CONTROL_CANDIDATE,
    Preflight,
    TrainingPlan,
    candidate_name,
    evaluate_control,
    load_plan,
    preflight,
    run_bounded_training,
)
from research.english.r6_model.initialization import (
    EXPECTED_TOKEN_IDS,
    ExpectedHashes,
    Initialization,
    apply_delta,
    verify_initialization,
)
from research.english.r6_model.progress import Progress
from research.english.r6_model.recipe import (
    CANDIDATE_ID,
    R6ModelRecipe,
    data_epoch,
    load_recipe,
    recipe_hash,
)
from research.english.r6_model.rows import StudyRows, load_study, study_from_rows

__all__ = [
    "CANDIDATE_ID",
    "CONTROL_CANDIDATE",
    "EXPECTED_TOKEN_IDS",
    "ExpectedHashes",
    "Initialization",
    "PassCommit",
    "PredictionRecord",
    "Preflight",
    "Progress",
    "R6ModelRecipe",
    "StudyRows",
    "TrainingPlan",
    "apply_delta",
    "candidate_name",
    "data_epoch",
    "evaluate_control",
    "load_plan",
    "load_recipe",
    "load_study",
    "preflight",
    "recipe_hash",
    "run_bounded_training",
    "study_from_rows",
    "verify_initialization",
]
