"""Bounded in-process telemetry and safe operational status helpers."""

from __future__ import annotations

import json
import math
import threading
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any


@dataclass(frozen=True)
class RequestObservation:
    recorded_at: float
    route: str
    method: str
    status: int
    duration_ms: float


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return round(ordered[index], 1)


class OperationsMonitor:
    """Keep recent request telemetry without external dependencies or unbounded labels."""

    def __init__(self, *, max_observations: int = 20_000):
        self.started_at = datetime.now(timezone.utc)
        self._started_monotonic = monotonic()
        self._lock = threading.RLock()
        self._observations: deque[RequestObservation] = deque(maxlen=max(100, max_observations))
        self._requests_total = 0
        self._in_flight = 0

    def begin(self) -> None:
        with self._lock:
            self._in_flight += 1

    def finish(self, route: str, method: str, status: int, duration_ms: float) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self._requests_total += 1
            self._observations.append(RequestObservation(
                recorded_at=monotonic(), route=route, method=method,
                status=status, duration_ms=max(0.0, duration_ms),
            ))

    def snapshot(self, *, window_seconds: int = 300) -> dict[str, Any]:
        cutoff = monotonic() - max(60, window_seconds)
        with self._lock:
            recent = [item for item in self._observations if item.recorded_at >= cutoff]
            total = self._requests_total
            in_flight = self._in_flight
        durations = [item.duration_ms for item in recent]
        errors = sum(item.status >= 500 for item in recent)
        client_errors = sum(400 <= item.status < 500 for item in recent)
        routes = Counter(f"{item.method} {item.route}" for item in recent)
        return {
            "started_at": self.started_at.isoformat(),
            "uptime_seconds": round(monotonic() - self._started_monotonic),
            "requests_total": total,
            "in_flight": in_flight,
            "window_seconds": max(60, window_seconds),
            "window": {
                "requests": len(recent),
                "server_errors": errors,
                "client_errors": client_errors,
                "error_rate_percent": round(errors / len(recent) * 100, 2) if recent else 0.0,
                "latency_ms": {
                    "p50": percentile(durations, 0.50),
                    "p95": percentile(durations, 0.95),
                    "p99": percentile(durations, 0.99),
                    "max": round(max(durations), 1) if durations else 0.0,
                },
                "top_routes": [{"route": route, "requests": count} for route, count in routes.most_common(8)],
            },
        }


def backup_status(path: str, *, stale_after_hours: int = 8) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        return {"status": "unknown", "detail": "O serviço ainda não publicou o estado do backup."}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        checked_at = datetime.fromisoformat(str(payload["checked_at"]))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        age_seconds = max(0, (datetime.now(timezone.utc) - checked_at.astimezone(timezone.utc)).total_seconds())
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"status": "invalid", "detail": "O arquivo de estado do backup é inválido."}
    status = str(payload.get("status", "unknown"))
    if status == "ok" and age_seconds > stale_after_hours * 3600:
        status = "stale"
    return {
        "status": status,
        "checked_at": checked_at.isoformat(),
        "age_seconds": round(age_seconds),
        "retained": int(payload.get("retained", 0) or 0),
        "detail": str(payload.get("detail", ""))[:240] or None,
    }
