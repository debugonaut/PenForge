"""Bounded process metrics and a restricted Prometheus scrape.

Labels come from fixed vocabularies; a request path, identity, project id or error
message never becomes a label. Metrics describe volume, latency, queue health and
quota pressure only — no handwriting, font, token or URL content can enter.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable

ROUTE_CLASSES = frozenset(
    {
        "health",
        "contracts",
        "identity",
        "projects",
        "uploads",
        "storage",
        "jobs",
        "analysis",
        "glyphs",
        "exports",
        "privacy",
        "metrics",
        "other",
    }
)
STATUS_CLASSES = ("2xx", "3xx", "4xx", "5xx")
LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def route_class(path: str) -> str:
    """Fixed-vocabulary bucket; unknown segments collapse to ``other``."""
    parts = [part for part in path.split("/") if part]
    if parts[:2] != ["api", "v1"]:
        return "metrics" if parts[:1] == ["internal"] else "other"
    segment = parts[2] if len(parts) > 2 else "other"
    aliases = {"objects": "storage", "samples": "uploads", "sessions": "identity"}
    segment = aliases.get(segment, segment)
    return segment if segment in ROUTE_CLASSES else "other"


def status_class(status: int) -> str:
    return f"{status // 100}xx" if 200 <= status <= 599 else "5xx"


class Metrics:
    """Thread-safe counters and fixed-bucket latency histograms."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str], int] = {}
        self._latency: dict[tuple[str, float], int] = {}
        self._latency_sum: dict[str, float] = {}
        self._latency_count: dict[str, int] = {}
        self._rejections: dict[str, int] = {}
        self._transfer = {"received": 0, "sent": 0}

    def request(self, route: str, status: int, seconds: float) -> None:
        if route not in ROUTE_CLASSES:
            route = "other"
        if not math.isfinite(seconds) or seconds < 0:
            return
        klass = status_class(status)
        with self._lock:
            self._requests[(route, klass)] = self._requests.get((route, klass), 0) + 1
            self._latency_sum[route] = self._latency_sum.get(route, 0.0) + seconds
            self._latency_count[route] = self._latency_count.get(route, 0) + 1
            for bound in LATENCY_BUCKETS:
                if seconds <= bound:
                    key = (route, bound)
                    self._latency[key] = self._latency.get(key, 0) + 1

    def transfer(self, received: int, sent: int) -> None:
        with self._lock:
            self._transfer["received"] += max(0, received)
            self._transfer["sent"] += max(0, sent)

    def rejection(self, reason: str) -> None:
        if reason not in {
            "body_too_large",
            "unhandled_exception",
            "rate_limited",
            "quota_exceeded",
            "quota_unconfigured",
            "consent_required",
            "consent_policy_conflict",
            "consent_ineligible",
        }:
            return
        with self._lock:
            self._rejections[reason] = self._rejections.get(reason, 0) + 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "transfer": dict(self._transfer),
                "requests": dict(self._requests),
                "latency_buckets": dict(self._latency),
                "latency_sum": dict(self._latency_sum),
                "latency_count": dict(self._latency_count),
                "rejections": dict(self._rejections),
            }

    def render(self, extra: Iterable[str] = ()) -> str:
        snap = self.snapshot()
        lines = [
            "# HELP penforge_requests_total API requests by route class and status class.",
            "# TYPE penforge_requests_total counter",
        ]
        for (route, klass), value in sorted(snap["requests"].items()):
            lines.append(f'penforge_requests_total{{route="{route}",status="{klass}"}} {value}')
        lines += [
            "# HELP penforge_request_duration_seconds API latency by route class.",
            "# TYPE penforge_request_duration_seconds histogram",
        ]
        for route in sorted(snap["latency_count"]):
            cumulative = 0
            for bound in LATENCY_BUCKETS:
                cumulative = snap["latency_buckets"].get((route, bound), cumulative)
                lines.append(
                    "penforge_request_duration_seconds_bucket"
                    f'{{route="{route}",le="{bound:g}"}} {cumulative}'
                )
            count = snap["latency_count"][route]
            lines.append(
                f'penforge_request_duration_seconds_bucket{{route="{route}",le="+Inf"}} {count}'
            )
            lines.append(
                f'penforge_request_duration_seconds_sum{{route="{route}"}} '
                f"{snap['latency_sum'][route]:.6f}"
            )
            lines.append(f'penforge_request_duration_seconds_count{{route="{route}"}} {count}')
        lines += [
            "# HELP penforge_rejections_total Requests rejected before or at the boundary.",
            "# TYPE penforge_rejections_total counter",
        ]
        for reason, value in sorted(snap["rejections"].items()):
            lines.append(f'penforge_rejections_total{{reason="{reason}"}} {value}')
        lines.append("# TYPE penforge_api_transfer_bytes_total counter")
        for direction, count in snap["transfer"].items():
            lines.append(f'penforge_api_transfer_bytes_total{{direction="{direction}"}} {count}')
        lines.extend(line for line in extra if line)
        return "\n".join(lines) + "\n"
