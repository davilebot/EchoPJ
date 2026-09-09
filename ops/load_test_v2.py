#!/usr/bin/env python3
"""Run a bounded, read-only load probe against the EchoPJs v2 HTTP API."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookies import SimpleCookie
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


SCENARIOS = {
    "health": ("GET", "/health/ready", None, False),
    "dashboard": ("GET", "/api/dashboard", None, True),
    "search": (
        "POST",
        "/api/search",
        {"ufs": ["SP"], "registration_statuses": ["ATIVA"], "limit": 20},
        True,
    ),
}


def request_once(
    base_url: str,
    scenario: str,
    *,
    cookie: str | None,
    organization_id: int | None,
    timeout: float,
) -> dict:
    method, path, payload, authenticated = SCENARIOS[scenario]
    headers = {"Accept": "application/json", "User-Agent": "EchoPJs-load-probe/1.0"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if authenticated:
        if not cookie or not organization_id:
            raise RuntimeError("O cenário autenticado precisa de sessão e organização.")
        headers["Cookie"] = cookie
        headers["X-Organization-Id"] = str(organization_id)
    started = time.perf_counter()
    status = 0
    response_time_header = None
    try:
        with urlopen(Request(urljoin(base_url, path), data=body, headers=headers, method=method), timeout=timeout) as response:
            status = response.status
            response_time_header = response.headers.get("X-Response-Time-Ms")
            response.read()
    except HTTPError as error:
        status = error.code
        response_time_header = error.headers.get("X-Response-Time-Ms")
        error.read()
    except (URLError, TimeoutError, OSError):
        status = 0
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "status": status,
        "elapsed_ms": round(elapsed_ms, 2),
        "server_ms": float(response_time_header) if response_time_header else None,
    }


def login(base_url: str, username: str, password: str, timeout: float) -> str:
    payload = json.dumps({"identifier": username, "password": password}).encode("utf-8")
    request = Request(
        urljoin(base_url, "/api/auth/login"),
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"O login respondeu HTTP {response.status}.")
            cookie_header = response.headers.get("Set-Cookie", "")
            response.read()
    except HTTPError as error:
        error.read()
        raise RuntimeError(f"O login respondeu HTTP {error.code}.") from error
    cookies = SimpleCookie()
    cookies.load(cookie_header)
    session = cookies.get("echopjs_session")
    if not session:
        raise RuntimeError("O login não devolveu uma sessão.")
    return f"echopjs_session={session.value}"


def percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * value + 0.999999)))
    return round(ordered[index], 2)


def summarize(results: list[dict], duration_seconds: float) -> dict:
    latencies = [item["elapsed_ms"] for item in results]
    server_latencies = [item["server_ms"] for item in results if item["server_ms"] is not None]
    statuses = Counter(str(item["status"]) if item["status"] else "network_error" for item in results)
    failed = sum(1 for item in results if not 200 <= item["status"] < 300)
    return {
        "requests": len(results),
        "duration_seconds": round(duration_seconds, 3),
        "throughput_rps": round(len(results) / duration_seconds, 2) if duration_seconds else 0.0,
        "status_counts": dict(sorted(statuses.items())),
        "error_rate_percent": round(100 * failed / len(results), 2) if results else 100.0,
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else 0.0,
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0.0,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "server_latency_ms": {
            "p50": percentile(server_latencies, 0.50),
            "p95": percentile(server_latencies, 0.95),
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Teste de carga limitado da API EchoPJs v2.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--username")
    parser.add_argument("--password-env", default="ECHOPJS_LOAD_PASSWORD")
    parser.add_argument("--organization-id", type=int)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--max-p95-ms", type=float, default=2000.0)
    parser.add_argument("--max-error-rate", type=float, default=1.0)
    args = parser.parse_args(argv)
    if not 1 <= args.requests <= 500:
        parser.error("--requests precisa ficar entre 1 e 500")
    if not 1 <= args.concurrency <= 20:
        parser.error("--concurrency precisa ficar entre 1 e 20")
    if args.scenario == "search" and args.requests > 20:
        parser.error("o cenário search aceita no máximo 20 requisições por execução")
    if args.timeout <= 0 or args.max_p95_ms <= 0 or not 0 <= args.max_error_rate <= 100:
        parser.error("revise timeout e limites de aprovação")
    parsed_url = urlsplit(args.base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        parser.error("--base-url precisa ser uma URL HTTP válida")
    if SCENARIOS[args.scenario][3] and (not args.username or not args.organization_id):
        parser.error("cenários autenticados exigem --username e --organization-id")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    base_url = args.base_url.rstrip("/") + "/"
    cookie = None
    if SCENARIOS[args.scenario][3]:
        password = os.environ.get(args.password_env, "")
        if not password:
            print(f"Defina a variável {args.password_env} sem passar a senha na linha de comando.", file=sys.stderr)
            return 2
        cookie = login(base_url, args.username, password, args.timeout)

    # One warm-up request is deliberately excluded from the report.
    warmup = request_once(
        base_url, args.scenario, cookie=cookie,
        organization_id=args.organization_id, timeout=args.timeout,
    )
    if not 200 <= warmup["status"] < 300:
        print(json.dumps({"scenario": args.scenario, "warmup": warmup, "passed": False}, ensure_ascii=False, indent=2))
        return 1

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(
                request_once, base_url, args.scenario, cookie=cookie,
                organization_id=args.organization_id, timeout=args.timeout,
            )
            for _ in range(args.requests)
        ]
        results = [future.result() for future in as_completed(futures)]
    report = summarize(results, time.perf_counter() - started)
    report.update({
        "scenario": args.scenario,
        "target": urlsplit(base_url).netloc,
        "concurrency": args.concurrency,
        "limits": {"max_p95_ms": args.max_p95_ms, "max_error_rate_percent": args.max_error_rate},
    })
    report["passed"] = (
        report["error_rate_percent"] <= args.max_error_rate
        and report["latency_ms"]["p95"] <= args.max_p95_ms
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
