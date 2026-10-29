"""Load smoke test for the Shwe Min Thar KPI system — LOCAL / QA stacks only.

Drives the main read paths concurrently with plain HTTP (httpx, already a
project dependency — no k6, locust or other new infra) and reports
per-endpoint p50/p95 latency plus error counts:

  login -> SA dashboard summary -> reports summary -> users list ->
  departed (yearly, company-wide) download -> period list

Credentials NEVER come from this file: the Super Admin email/password come
from the environment (TEST_SMOKE_SA_EMAIL / TEST_SMOKE_SA_PASSWORD) and are
never printed, logged, or written anywhere. The script refuses to run
without them, and refuses base URLs whose host looks like production
(``prod`` / ``production`` as a whole token).

PASS BAR (stated here, adjustable with --p95-budget-ms):
  * 0 errors across every step, every virtual user, every round.
  * p95 latency <= budget (default 2000 ms) on every step, local stack.
  A failing bar exits non-zero but still prints the full report.

USAGE (from the repo root, with the repo venv active):
  export TEST_SMOKE_SA_EMAIL='sa@example.com'
  export TEST_SMOKE_SA_PASSWORD='the-sa-password'
  python scripts/load_smoke.py --base-url http://127.0.0.1:8001
  python scripts/load_smoke.py --base-url http://127.0.0.1:8001 \\
      --users 50 --rounds 2 --json-out /tmp/load-smoke.json

  --users is the concurrent virtual-user count (recommended band 20-50;
  accepted 1-100). --rounds repeats the whole chain that many times.
  Each round logs in fresh, so login latency is measured too.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field

import httpx

STEPS: tuple[tuple[str, str, str], ...] = (
    # (name, method, path-template)
    ("login", "POST", "/api/v1/auth/login"),
    ("dashboard_summary", "GET", "/api/v1/superadmin/dashboard/summary"),
    ("reports_summary", "GET", "/api/v1/kpi/reports/summary"),
    ("users_list", "GET", "/api/v1/users/?limit=100"),
    ("departed_download", "GET", "/api/v1/system/report-download"),
    ("period_list", "GET", "/api/v1/kpi/periods/"),
)
PROD_TOKENS = ("prod", "production")


@dataclass
class StepStats:
    latencies_ms: list[float] = field(default_factory=list)  # successes only
    errors: int = 0
    error_samples: list[str] = field(default_factory=list)
    statuses: dict[int, int] = field(default_factory=dict)

    def percentile(self, pct: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        if len(ordered) == 1:
            return ordered[0]
        rank = (pct / 100) * (len(ordered) - 1)
        low, high = int(rank), min(int(rank) + 1, len(ordered) - 1)
        frac = rank - low
        return ordered[low] * (1 - frac) + ordered[high] * frac


def _prod_host_hit(base_url: str) -> str | None:
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return None
    parts = re.split(r"[-_.]", host)
    for token in PROD_TOKENS:
        if token in parts:
            return token
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True,
                        help="API base URL, e.g. http://127.0.0.1:8001")
    parser.add_argument("--users", type=int, default=30,
                        help="Concurrent virtual users (default 30; 1-100).")
    parser.add_argument("--rounds", type=int, default=3,
                        help="How many times each VU runs the chain (default 3).")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="Per-request timeout seconds (default 30).")
    parser.add_argument("--p95-budget-ms", type=float, default=2000.0,
                        help="p95 latency budget ms per step (default 2000).")
    parser.add_argument("--json-out", default="",
                        help="Optional path to write the JSON report to.")
    args = parser.parse_args(argv)
    if not 1 <= args.users <= 100:
        parser.error("--users must be between 1 and 100.")
    if not 1 <= args.rounds <= 10:
        parser.error("--rounds must be between 1 and 10.")
    if args.timeout <= 0:
        parser.error("--timeout must be positive.")
    return args


async def _call(client: httpx.AsyncClient, method: str, url: str,
                stats: StepStats, **kwargs) -> httpx.Response | None:
    started = time.perf_counter()
    try:
        response = await client.request(method, url, **kwargs)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        stats.errors += 1
        if len(stats.error_samples) < 3:
            stats.error_samples.append(f"{type(exc).__name__}: {exc}"[:300])
        return None
    elapsed_ms = (time.perf_counter() - started) * 1000
    stats.statuses[response.status_code] = stats.statuses.get(
        response.status_code, 0) + 1
    if 200 <= response.status_code < 300:
        stats.latencies_ms.append(elapsed_ms)
    else:
        stats.errors += 1
        if len(stats.error_samples) < 3:
            body = (response.text or "")[:200].replace("\n", " ")
            stats.error_samples.append(f"HTTP {response.status_code}: {body}")
    return response


async def _run_chain(client: httpx.AsyncClient, base_url: str, email: str,
                     password: str, year: int, stats: dict[str, StepStats],
                     timeout: float) -> None:
    token: str | None = None
    for name, method, path in STEPS:
        step = stats[name]
        if name == "login":
            response = await _call(
                client, method, base_url + path, step,
                json={"email": email, "password": password},
                timeout=timeout,
            )
            if response is not None and response.status_code == 200:
                try:
                    token = response.json().get("access_token")
                except ValueError:
                    token = None
                if token is None:
                    step.errors += 1
                    step.error_samples.append("login 200 without access_token")
            continue
        if token is None:
            step.errors += 1
            if len(step.error_samples) < 3:
                step.error_samples.append("skipped: no login token")
            continue
        headers = {"Authorization": f"Bearer {token}"}
        if name == "departed_download":
            params = {"report_type": "YEARLY", "year": str(year),
                      "scope": "company"}
            await _call(client, method, base_url + path, step,
                        headers=headers, params=params, timeout=timeout)
        else:
            await _call(client, method, base_url + path, step,
                        headers=headers, timeout=timeout)


async def _run(args: argparse.Namespace, email: str, password: str,
               year: int) -> dict[str, StepStats]:
    stats: dict[str, StepStats] = {name: StepStats() for name, _, _ in STEPS}
    semaphore = asyncio.Semaphore(args.users)
    # trust_env=False: proxy environment variables must not redirect a
    # local/QA-stack smoke run through some unrelated proxy.
    async with httpx.AsyncClient(trust_env=False) as client:
        async def _vu() -> None:
            async with semaphore:
                for _ in range(args.rounds):
                    await _run_chain(client, args.base_url, email, password,
                                     year, stats, args.timeout)

        await asyncio.gather(*(_vu() for _ in range(args.users)))
    return stats


def _report(stats: dict[str, StepStats], users: int, rounds: int,
            budget_ms: float) -> tuple[dict, bool]:
    attempts = users * rounds
    report: dict = {"attempts_per_step": attempts, "steps": {}, "verdict": "PASS"}
    all_ok = True
    for name, _, _ in STEPS:
        step = stats[name]
        successes = len(step.latencies_ms)
        p50 = step.percentile(50)
        p95 = step.percentile(95)
        ok = step.errors == 0 and successes > 0 and p95 <= budget_ms
        all_ok = all_ok and ok
        report["steps"][name] = {
            "attempts": attempts,
            "successes": successes,
            "errors": step.errors,
            "statuses": step.statuses,
            "p50_ms": round(p50, 1),
            "p95_ms": round(p95, 1),
            "pass": ok,
            "error_samples": step.error_samples,
        }
    report["verdict"] = "PASS" if all_ok else "FAIL"
    return report, all_ok


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    email = os.environ.get("TEST_SMOKE_SA_EMAIL", "").strip()
    password = os.environ.get("TEST_SMOKE_SA_PASSWORD", "")
    if not email or not password:
        print("Refusing: set TEST_SMOKE_SA_EMAIL and TEST_SMOKE_SA_PASSWORD "
              "(Super Admin credentials are never hardcoded).", file=sys.stderr)
        return 2
    if (hit := _prod_host_hit(args.base_url)) is not None:
        print(f"Refusing: base URL host looks like production (token '{hit}').",
              file=sys.stderr)
        return 2
    year = time.localtime().tm_year - 1  # previous year: a full-year window
    stats = asyncio.run(_run(args, email, password, year))
    report, ok = _report(stats, args.users, args.rounds, args.p95_budget_ms)
    print(f"LOAD SMOKE — {args.users} users x {args.rounds} rounds "
          f"(base {args.base_url}, p95 budget {args.p95_budget_ms:.0f} ms)")
    print(f"  {'step':<20}{'ok':<8}{'errors':<8}{'p50 ms':<10}{'p95 ms':<10}"
          f"{'statuses'}")
    for name, _, _ in STEPS:
        step = report["steps"][name]
        print(f"  {name:<20}{'PASS' if step['pass'] else 'FAIL':<8}"
              f"{step['errors']:<8}{step['p50_ms']:<10}{step['p95_ms']:<10}"
              f"{step['statuses']}")
        for sample in step["error_samples"]:
            print(f"    ! {sample}")
    print(f"  VERDICT: {report['verdict']} "
          f"(bar: 0 errors, p95 <= {args.p95_budget_ms:.0f} ms per step)")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print(f"  JSON report: {args.json_out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
