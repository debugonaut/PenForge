"""Content-free operational logging: only explicitly typed fields can leave the process."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

EVENTS = {
    "request_completed",
    "request_failed",
    "dependency_failed",
    "asset_verification_finished",
    "privacy_saga_stuck",
}
STRING_FIELDS = {"request_id", "job_id", "project_id", "user_id"}
NUMBER_FIELDS = {"duration_ms", "status_code", "bytes_in", "bytes_out", "rss_mb", "request_count"}


def safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    from math import isfinite
    from uuid import UUID

    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if (
            key == "stage"
            and isinstance(value, str)
            and value in {"hash", "decode", "derivative", "publish", "verified", "lease_lost"}
        ):
            clean[key] = value
        elif key in STRING_FIELDS and isinstance(value, str):
            try:
                clean[key] = str(UUID(value))
            except ValueError:
                pass
        elif key in NUMBER_FIELDS and type(value) in (int, float) and isfinite(value):
            clean[key] = value
    return clean


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Do not call getMessage(), formatException(), or serialize arbitrary extras.
        event = record.msg if isinstance(record.msg, str) and record.msg in EVENTS else "redacted"
        return json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "event": event,
                **safe_fields(getattr(record, "fields", {})),
            },
            allow_nan=False,
        )


logger = logging.getLogger("penforge")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    logger.addHandler(handler)


def log_event(event: str, **fields: Any) -> None:
    logger.info(event, extra={"fields": safe_fields(fields)})
