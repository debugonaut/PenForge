"""Finite, cumulative R6 budgets.

R5 installed one aggregate ``RLIMIT_CPU`` correctly but kept its wall/CPU clocks per
invocation: a resumed run restarted the aggregate clock, so repeated resumes could exceed
the declared envelope. R6 persists consumed wall/CPU time in every state and charges a
discarded (interrupted) pass conservatively, so the ceiling covers every invocation of the
same experiment. The R5 single-install CPU rule and cooperative wall/RSS/output checks are
preserved.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research.english.r6_model.recipe import R6ModelRecipe

_CPU_INSTALLED: list[float] = []
WATCHDOG_INTERVAL_SECONDS = 0.5


class BudgetExceeded(RuntimeError):
    """Raised when a bounded run exceeds a declared ceiling."""


@dataclass(frozen=True)
class Consumption:
    """Wall/CPU already charged to this experiment by earlier invocations."""

    wall_seconds: float = 0.0
    cpu_seconds: float = 0.0

    def __post_init__(self) -> None:
        for value in (self.wall_seconds, self.cpu_seconds):
            if value < 0:
                raise ValueError("r6_prior_consumption_must_not_be_negative")


@dataclass(frozen=True)
class BudgetPlan:
    pass_wall_seconds: float
    validation_wall_seconds: float
    run_wall_seconds: float
    cpu_seconds: float
    rss_mb: float
    output_bytes: int
    prior: Consumption = field(default_factory=Consumption)

    @classmethod
    def from_recipe(
        cls, recipe: R6ModelRecipe, *, prior: Consumption | None = None
    ) -> "BudgetPlan":
        return cls(
            pass_wall_seconds=float(recipe.max_pass_wall_seconds),
            validation_wall_seconds=float(recipe.max_validation_wall_seconds),
            run_wall_seconds=float(recipe.max_run_wall_seconds),
            cpu_seconds=float(recipe.max_cpu_seconds),
            rss_mb=float(recipe.max_rss_mb),
            output_bytes=int(recipe.max_output_bytes),
            prior=Consumption() if prior is None else prior,
        )

    @property
    def remaining_wall_seconds(self) -> float:
        return self.run_wall_seconds - self.prior.wall_seconds

    @property
    def remaining_cpu_seconds(self) -> float:
        return self.cpu_seconds - self.prior.cpu_seconds


def cpu_limit_to_install(requested: float, hard: float | None) -> float:
    if requested <= 0:
        raise ValueError("r6_cpu_limit_must_be_positive")
    if hard is None or hard == resource.RLIM_INFINITY or hard <= 0:
        return requested
    return min(float(requested), float(hard))


def process_peak_rss_mb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value / (1_000_000 if sys.platform == "darwin" else 1000)


def directory_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except FileNotFoundError:
            # Atomic publication may rename a file between discovery and stat.
            continue
    return total


def publication_peak_bytes(*, full_state_bytes: int, selected_state_bytes: int) -> int:
    """Worst case during publication and rotation.

    Two full states and two model-only states can coexist: a completed pass writes its
    ``selected-candidate`` while the previous winner is still in ``best/`` (neither may be
    deleted before the new one is proven), and the same pass then commits a new full state
    (``latest.new``) beside the previous ``latest`` before the rename swap. Rotation
    renames, it never copies, so the swap itself adds no bytes. A stale
    ``latest.old``/``best.old`` left by a crash is reclaimed before the next write, which
    is why it is not counted again.
    """
    for value in (full_state_bytes, selected_state_bytes):
        if value <= 0:
            raise ValueError("r6_publication_sizes_must_be_positive")
    return 2 * full_state_bytes + 2 * selected_state_bytes


def assert_publication_fits(needed_bytes: int, plan: BudgetPlan) -> None:
    if needed_bytes > plan.output_bytes:
        raise ValueError("r6_publication_peak_exceeds_output_ceiling")


def _hard_abort(reason: str, output: Path) -> None:
    try:
        (output / "budget-failure.json").write_text(
            json.dumps({"status": "incomplete", "reason": reason}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    finally:
        os._exit(124)


class RunBudget:
    """Cooperative budget guard; the watchdog is a backstop, not the primary check."""

    def __init__(
        self,
        plan: BudgetPlan,
        output: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
        cpu_clock: Callable[[], float] = time.process_time,
        rss_mb: Callable[[], float] = process_peak_rss_mb,
        disk_bytes: Callable[[Path], int] = directory_bytes,
        abort: Callable[[str, Path], None] = _hard_abort,
    ) -> None:
        self.plan = plan
        self.output = output
        self.clock = clock
        self.cpu_clock = cpu_clock
        self.rss_mb = rss_mb
        self.disk_bytes = disk_bytes
        self.abort = abort
        self.started = clock()
        self.started_cpu = cpu_clock()
        self.phase_name: str | None = None
        self.phase_started = self.started
        self.phase_wall = 0.0
        self.phase_seconds: dict[str, float] = {}
        self._stop = threading.Event()
        self._watchdog: threading.Thread | None = None

    def install_cpu_limit(self) -> float:
        _, hard = resource.getrlimit(resource.RLIMIT_CPU)
        ceiling = cpu_limit_to_install(self.plan.remaining_cpu_seconds, float(hard))
        if _CPU_INSTALLED and ceiling > _CPU_INSTALLED[0]:
            raise ValueError("r6_aggregate_cpu_limit_was_already_installed_lower")
        if not _CPU_INSTALLED:
            limit = max(1, int(ceiling))
            resource.setrlimit(resource.RLIMIT_CPU, (limit, limit))
            _CPU_INSTALLED.append(float(limit))
        return ceiling

    def start(self) -> None:
        if self._watchdog is not None:
            raise ValueError("r6_budget_watchdog_already_started")

        def watch() -> None:
            while not self._stop.wait(WATCHDOG_INTERVAL_SECONDS):
                try:
                    self.checkpoint()
                except BudgetExceeded as error:
                    self.abort(str(error), self.output)
                    return

        self._watchdog = threading.Thread(target=watch, daemon=True)
        self._watchdog.start()

    @contextmanager
    def phase(self, name: str, wall_seconds: float) -> Iterator[None]:
        if wall_seconds <= 0:
            raise ValueError("r6_phase_wall_must_be_positive")
        if self.phase_name is not None:
            raise ValueError("r6_phases_must_not_nest")
        self.phase_name, self.phase_started, self.phase_wall = name, self.clock(), wall_seconds
        tick = self.clock()
        try:
            self.checkpoint()
            yield
        finally:
            self.phase_seconds[name] = self.phase_seconds.get(name, 0.0) + (self.clock() - tick)
            self.phase_name, self.phase_started, self.phase_wall = None, self.clock(), 0.0

    def require_wall(self, seconds: float, *, reason: str) -> None:
        """Refuse to start work the aggregate ceiling cannot finish."""
        if seconds <= 0:
            raise ValueError("r6_required_wall_must_be_positive")
        if self.plan.remaining_wall_seconds - (self.clock() - self.started) < seconds:
            raise BudgetExceeded(f"aggregate_wall_ceiling_insufficient:{reason}")

    def checkpoint(self) -> None:
        wall = self.plan.prior.wall_seconds + (self.clock() - self.started)
        if wall > self.plan.run_wall_seconds:
            raise BudgetExceeded("aggregate_wall_ceiling_exceeded")
        cpu = self.plan.prior.cpu_seconds + (self.cpu_clock() - self.started_cpu)
        if cpu > self.plan.cpu_seconds:
            raise BudgetExceeded("aggregate_cpu_ceiling_exceeded")
        if self.phase_name is not None and self.clock() - self.phase_started > self.phase_wall:
            raise BudgetExceeded(f"phase_wall_ceiling_exceeded:{self.phase_name}")
        if self.rss_mb() > self.plan.rss_mb:
            raise BudgetExceeded("rss_ceiling_exceeded")
        if self.disk_bytes(self.output) > self.plan.output_bytes:
            raise BudgetExceeded("output_ceiling_exceeded")

    def close(self) -> None:
        self._stop.set()
        if self._watchdog is not None:
            self._watchdog.join(timeout=2)

    def consumption(self) -> Consumption:
        return Consumption(
            wall_seconds=self.plan.prior.wall_seconds + (self.clock() - self.started),
            cpu_seconds=self.plan.prior.cpu_seconds + (self.cpu_clock() - self.started_cpu),
        )

    def report(self) -> dict[str, Any]:
        consumed = self.consumption()
        return {
            "limits": {
                "pass_wall_seconds": self.plan.pass_wall_seconds,
                "validation_wall_seconds": self.plan.validation_wall_seconds,
                "run_wall_seconds": self.plan.run_wall_seconds,
                "cpu_seconds": self.plan.cpu_seconds,
                "rss_mb": self.plan.rss_mb,
                "output_bytes": self.plan.output_bytes,
            },
            "prior_consumption": {
                "wall_seconds": self.plan.prior.wall_seconds,
                "cpu_seconds": self.plan.prior.cpu_seconds,
            },
            "consumed": {
                "wall_seconds": consumed.wall_seconds,
                "cpu_seconds": consumed.cpu_seconds,
            },
            "observed_wall_seconds": self.clock() - self.started,
            "phase_wall_seconds": dict(sorted(self.phase_seconds.items())),
            "peak_rss_mb": self.rss_mb(),
            "output_bytes": self.disk_bytes(self.output),
        }
