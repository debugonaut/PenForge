"""Finite R5 budgets: one aggregate run limit plus per-phase wall/memory/output checks.

The R4 audit found each phase re-applied ``RLIMIT_CPU`` to the same value. Because that
limit counts cumulative process CPU time, later phases silently received less than their
nominal ceiling and could never be raised again. R5 installs one aggregate CPU ceiling
once and checks per-phase wall time, memory and output cooperatively, writing an explicit
incomplete marker rather than presenting a truncated run as a completed candidate.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.english.r5_model.recipe import R5ModelRecipe

_CPU_INSTALLED: list[float] = []


class BudgetExceeded(RuntimeError):
    """Raised when a bounded run exceeds a declared ceiling."""


@dataclass(frozen=True)
class BudgetPlan:
    pass_wall_seconds: float
    validation_wall_seconds: float
    run_wall_seconds: float
    cpu_seconds: float
    rss_mb: float
    output_bytes: int

    @classmethod
    def from_recipe(cls, recipe: R5ModelRecipe) -> "BudgetPlan":
        return cls(
            pass_wall_seconds=float(recipe.max_pass_wall_seconds),
            validation_wall_seconds=float(recipe.max_validation_wall_seconds),
            run_wall_seconds=float(recipe.max_run_wall_seconds),
            cpu_seconds=float(recipe.max_cpu_seconds),
            rss_mb=float(recipe.max_rss_mb),
            output_bytes=int(recipe.max_output_bytes),
        )


def cpu_limit_to_install(requested: float, hard: float | None) -> float:
    if requested <= 0:
        raise ValueError("r5_cpu_limit_must_be_positive")
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
            continue
    return total


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
        rss_mb: Callable[[], float] = process_peak_rss_mb,
        disk_bytes: Callable[[Path], int] = directory_bytes,
        abort: Callable[[str, Path], None] = _hard_abort,
    ) -> None:
        self.plan = plan
        self.output = output
        self.clock = clock
        self.rss_mb = rss_mb
        self.disk_bytes = disk_bytes
        self.abort = abort
        self.started = clock()
        self.phase_name: str | None = None
        self.phase_started = self.started
        self.phase_wall = 0.0
        self.phase_seconds: dict[str, float] = {}
        self._stop = threading.Event()
        self._watchdog: threading.Thread | None = None

    def install_cpu_limit(self) -> float:
        _, hard = resource.getrlimit(resource.RLIMIT_CPU)
        ceiling = cpu_limit_to_install(self.plan.cpu_seconds, float(hard))
        if _CPU_INSTALLED and ceiling > _CPU_INSTALLED[0]:
            raise ValueError("r5_aggregate_cpu_limit_was_already_installed_lower")
        if not _CPU_INSTALLED:
            limit = int(ceiling)
            resource.setrlimit(resource.RLIMIT_CPU, (limit, limit))
            _CPU_INSTALLED.append(float(limit))
        return ceiling

    def start(self) -> None:
        if self._watchdog is not None:
            raise ValueError("r5_budget_watchdog_already_started")

        def watch() -> None:
            while not self._stop.wait(0.5):
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
            raise ValueError("r5_phase_wall_must_be_positive")
        if self.phase_name is not None:
            raise ValueError("r5_phases_must_not_nest")
        self.phase_name, self.phase_started, self.phase_wall = name, self.clock(), wall_seconds
        tick = self.clock()
        try:
            self.checkpoint()
            yield
        finally:
            self.phase_seconds[name] = self.phase_seconds.get(name, 0.0) + (self.clock() - tick)
            self.phase_name, self.phase_started, self.phase_wall = None, self.clock(), 0.0

    def checkpoint(self) -> None:
        elapsed = self.clock() - self.started
        if elapsed > self.plan.run_wall_seconds:
            raise BudgetExceeded("aggregate_wall_ceiling_exceeded")
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

    def report(self) -> dict[str, Any]:
        return {
            "limits": {
                "pass_wall_seconds": self.plan.pass_wall_seconds,
                "validation_wall_seconds": self.plan.validation_wall_seconds,
                "run_wall_seconds": self.plan.run_wall_seconds,
                "cpu_seconds": self.plan.cpu_seconds,
                "rss_mb": self.plan.rss_mb,
                "output_bytes": self.plan.output_bytes,
            },
            "observed_wall_seconds": self.clock() - self.started,
            "phase_wall_seconds": dict(sorted(self.phase_seconds.items())),
            "peak_rss_mb": self.rss_mb(),
            "output_bytes": self.disk_bytes(self.output),
        }
