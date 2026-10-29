"""Optional wake signal between the API and the co-located scheduled worker.

Hosted free tiers suspend idle database compute after a few minutes of silence, so
the worker must not poll while nobody is using the product. The API touches a file
in the shared container filesystem on real requests; the supervisor keeps full-rate
worker passes while that signal is fresh and rests the worker (and the database)
otherwise. Without configuration nothing is written.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

TOUCH_INTERVAL_SECONDS = 5.0


def wake_file() -> Path | None:
    value = os.environ.get("WORKER_WAKE_FILE")
    return Path(value) if value else None


def signal_activity(path: Path | None) -> None:
    """Mark the shared container as busy; failures are never worth failing a request."""
    if path is None:
        return
    now = time.time()
    try:
        if now - path.stat().st_mtime < TOUCH_INTERVAL_SECONDS:
            return
        path.touch()
    except FileNotFoundError:
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.touch()
        except OSError:
            return
    except OSError:
        return
