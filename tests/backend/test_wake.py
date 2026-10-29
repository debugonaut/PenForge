"""Wake-signal file behaviour used by the hosted supervisor."""

from __future__ import annotations

import os

from app.core.wake import signal_activity, wake_file


def test_unconfigured_wake_file_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.delenv("WORKER_WAKE_FILE", raising=False)
    assert wake_file() is None
    signal_activity(None)
    assert not any(tmp_path.iterdir())


def test_wake_signal_creates_the_file_and_throttles(monkeypatch, tmp_path):
    target = tmp_path / "nested" / "wake"
    monkeypatch.setenv("WORKER_WAKE_FILE", str(target))
    assert wake_file() == target

    signal_activity(wake_file())
    created = target.stat().st_mtime
    # Throttled: an immediate second signal must not rewrite the file.
    signal_activity(wake_file())
    assert target.stat().st_mtime == created

    # Once outside the touch interval the signal is forwarded again.
    stale = created - 60
    os.utime(target, (stale, stale))
    signal_activity(wake_file())
    assert target.stat().st_mtime > stale


def test_wake_signal_swallows_an_unwritable_path(monkeypatch, tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("WORKER_WAKE_FILE", str(blocked / "wake"))
    signal_activity(wake_file())  # never raises into a request
