"""Single-host beta supervisor: API process plus the durable scheduled worker.

Free hosting runs one container, so this replaces a second service. The API touches
a wake file on every request; while that file is recent the worker passes run
back-to-back so a queued job starts within seconds. When the site is idle the
supervisor lets the worker rest and the database compute suspends, which is what
keeps the free PostgreSQL allowance inside its monthly budget.

No migration or quota provisioning happens here. Those stay explicit operator steps
with their own database role.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

WORKER_PASS_SECONDS = int(os.environ.get("WORKER_MAX_RUN_SECONDS", "300"))
IDLE_PASS_SECONDS = int(os.environ.get("WORKER_IDLE_PASS_SECONDS", "30"))
IDLE_POLL_SECONDS = int(os.environ.get("WORKER_IDLE_POLL_SECONDS", "600"))
WAKE_GRACE_SECONDS = float(os.environ.get("WORKER_WAKE_GRACE_SECONDS", "30"))
API_RESTART_BACKOFF = 5.0


def wake_state(last_touch: float | None, now: float, grace: float = WAKE_GRACE_SECONDS):
    """Decide the next worker pass from the last API request.

    Returns ``(active, wait_seconds)``: active means keep passing immediately,
    otherwise rest until ``wait_seconds`` elapses and the state is re-read.
    """
    if last_touch is None:
        return False, IDLE_POLL_SECONDS
    if now - last_touch <= grace:
        return True, 0.0
    # Idle: wait out the remainder of the slow safety-poll interval.
    return False, max(0.0, IDLE_POLL_SECONDS - (now - last_touch))


def last_touch(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def child_env(pass_seconds: int) -> dict[str, str]:
    env = dict(os.environ)
    env["WORKER_MODE"] = "scheduled"
    env["WORKER_MAX_RUN_SECONDS"] = str(pass_seconds)
    # The image filesystem is not writable for uid 65532 outside /tmp.
    env.setdefault("WORKER_TEMP_ROOT", "/tmp/penforge-worker-temp")
    os.makedirs(env["WORKER_TEMP_ROOT"], mode=0o700, exist_ok=True)
    return env


def api_command(port: int) -> list[str]:
    return [
        sys.executable,
        "-B",
        "-m",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--no-access-log",
        "--no-proxy-headers",
    ]


def terminate(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def interruptible_sleep(seconds: float, stop) -> None:  # noqa: ANN001
    deadline = time.monotonic() + seconds
    while not stop() and time.monotonic() < deadline:
        time.sleep(min(0.5, deadline - time.monotonic()))


def wait_for_activity(wake_file: Path, stop) -> None:  # noqa: ANN001
    """Sleep while idle, waking as soon as real traffic touches the file."""
    while not stop():
        active, wait = wake_state(last_touch(wake_file), time.time())
        if active or wait <= 0:
            return
        interruptible_sleep(min(wait, 5.0), stop)


def apply_migrations() -> None:
    """Explicit, operator-requested schema step; never runs on its own."""
    if os.environ.get("BETA_MIGRATE") != "upgrade":
        return
    print("applying database migrations", flush=True)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "alembic",
            "-c",
            "infrastructure/migrations/alembic.ini",
            "upgrade",
            "head",
        ]
    )
    if result.returncode != 0:
        raise SystemExit(f"migration failed with exit code {result.returncode}")


def main() -> int:
    port = int(os.environ.get("PORT", "10000"))
    wake_file = Path(os.environ.get("WORKER_WAKE_FILE", "/tmp/penforge-worker-wake"))
    env = child_env(WORKER_PASS_SECONDS)
    api: subprocess.Popen | None = None
    api_failures = 0
    stopping = False
    first_pass = True

    def handle(_signum, _frame):  # noqa: ANN001 - signal handler
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    apply_migrations()

    while not stopping:
        if api is None or api.poll() is not None:
            if api is not None and api.returncode not in (0, -signal.SIGTERM):
                api_failures += 1
                delay = min(API_RESTART_BACKOFF * api_failures, 60)
                print(f"api exited ({api.returncode}); restarting in {delay:.0f}s", flush=True)
                interruptible_sleep(delay, lambda: stopping)
                if stopping:
                    break
            print("starting api", flush=True)
            api = subprocess.Popen(api_command(port), env=env)
            api_failures = 0

        active, wait = wake_state(last_touch(wake_file), time.time())
        if not first_pass and not active:
            print(f"idle for {wait:.0f}s before the next safety pass", flush=True)
            wait_for_activity(wake_file, lambda: stopping)
            if stopping:
                break
            active, wait = wake_state(last_touch(wake_file), time.time())
        first_pass = False
        pass_seconds = WORKER_PASS_SECONDS if active else IDLE_PASS_SECONDS
        print(f"starting worker pass ({pass_seconds}s)", flush=True)
        worker = subprocess.Popen(
            [sys.executable, "-B", "-m", "services.worker"],
            env=child_env(pass_seconds),
        )
        while not stopping and worker.poll() is None:
            try:
                worker.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            if api.poll() is not None:
                api = None
                break
            if wake_state(last_touch(wake_file), time.time())[0] and pass_seconds < WORKER_PASS_SECONDS:
                # Real traffic arrived during a short pass: cut it short and resume full passes.
                worker.terminate()
                break
        terminate(worker)
        if stopping:
            break
        interruptible_sleep(1.0 if active else 0.0, lambda: stopping)

    print("stopping beta services", flush=True)
    terminate(api)
    return 0
