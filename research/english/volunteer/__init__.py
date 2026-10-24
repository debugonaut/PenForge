"""Private five-writer volunteer pilot for the frozen English R6 winner.

This package is separate from the frozen R6 study. It evaluates the selected R6
pass-2 continuation winner on isolated uppercase and lowercase letter crops cut
from five provisional volunteer pages. Nothing here trains, fine-tunes or
self-selects a model, and nothing here touches the frozen R6 run.

Two owner-facing stages are deliberately split:

* :mod:`research.english.volunteer.prepare` hashes the original pages, cuts the
  reviewed letters, writes a private manifest and renders contact sheets. It
  imports no model framework and no framework is importable while it runs.
* :mod:`research.english.volunteer.evaluate` verifies the frozen winner's
  hashes, reconstructs the R6 composition (the pinned R5 canonical base plus the
  selected R5 delta plus the selected R6 pass-2 delta), runs one greedy pass and
  scores it.

Importing this package is inert: it does not import Torch, Transformers or
Safetensors, read any image, or create a run directory.
"""

from research.english.volunteer.manifest import (
    EXPECTED_SAMPLES,
    EXPECTED_SAMPLES_PER_WRITER,
    LOWER,
    UPPER,
    WRITERS,
    Manifest,
    ManifestSample,
    ReviewedWriter,
    load_manifest,
    validate_coordinates,
)

__all__ = [
    "EXPECTED_SAMPLES",
    "EXPECTED_SAMPLES_PER_WRITER",
    "LOWER",
    "UPPER",
    "WRITERS",
    "Manifest",
    "ManifestSample",
    "ReviewedWriter",
    "load_manifest",
    "validate_coordinates",
]
