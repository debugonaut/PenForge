"""Flushed structured progress that cannot leak handwriting text.

The owner's R5 final-test interruption showed why silent evaluation is unusable. R6 emits
one JSON object per line, immediately flushed, with stage, candidate/pass and completed
counts. Field names that would carry a label, truth string or generated text are rejected
outright, so a careless caller cannot print private content.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

FORBIDDEN_FIELDS = frozenset(
    {"label", "labels", "truth", "prediction", "predictions", "text", "handwriting"}
)
MAX_LINE_CHARS = 600


class Progress:
    """Structured, flushed progress with an injectable writer for tests."""

    def __init__(
        self,
        *,
        writer: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.writer = writer if writer is not None else _stdout_writer
        self.clock = clock
        self.started = clock()
        self.events: list[dict[str, Any]] = []

    def event(self, stage: str, *, status: str | None = None, **fields: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"stage": stage}
        if status is not None:
            payload["status"] = status
        payload.update(fields)
        payload["elapsed_seconds"] = round(self.clock() - self.started, 3)
        if set(payload) & FORBIDDEN_FIELDS:
            raise ValueError("r6_progress_must_not_carry_private_content")
        for value in payload.values():
            if isinstance(value, str) and len(value) > 200:
                raise ValueError("r6_progress_string_too_long")
        line = json.dumps(payload, sort_keys=True, allow_nan=False)
        if len(line) > MAX_LINE_CHARS:
            raise ValueError("r6_progress_line_too_long")
        self.writer(line)
        self.events.append(payload)
        return payload


def _stdout_writer(line: str) -> None:
    print(line, flush=True)


def merge_lineage_fields(lineage: Mapping[str, Any]) -> dict[str, Any]:
    """Small, non-private identifiers safe to print from a lineage mapping."""
    keep = ("recipe_hash", "lineage_hash", "candidate")
    return {key: lineage[key] for key in keep if key in lineage}
