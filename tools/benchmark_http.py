"""Measure complete HTTP response latency and output shape on real local data."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import platform
import time
from datetime import date, timedelta
from statistics import median

import httpx

from clean_air.settings import load_settings, months_before


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    if not (1 <= args.requests <= 10000 and 1 <= args.concurrency <= 32):
        parser.error("Use 1..10000 requests and 1..32 concurrent clients.")
    base = f"http://127.0.0.1:{args.port}"
    with httpx.Client(base_url=base, trust_env=False, timeout=30) as client:
        response = client.get("/api/status")
        response.raise_for_status()
        status = response.json()
        if not status["ready"]:
            raise RuntimeError("The real-data backfill must finish before benchmarking.")
        earliest = date.fromisoformat(status["available_start"])
        end = date.fromisoformat(status["available_end"])
        periods = {
            "one_day": end,
            "thirty_days": max(earliest, end - timedelta(days=29)),
            "twelve_months": max(earliest, months_before(end + timedelta(days=1), 12)),
        }
        results = []
        for pollutant in ("PM2.5", "PM10"):
            for name, start in periods.items():
                params = {"start": start.isoformat(), "end": end.isoformat(), "pollutant": pollutant}

                def request(_, query=params):
                    began = time.perf_counter()
                    result = client.get("/api/cleanest", params=query)
                    result.raise_for_status()
                    body = result.json()
                    if not body.get("winners") or not body.get("methodology") or not body.get("policy_id"):
                        raise RuntimeError("The API did not return the required successful comparison shape.")
                    return (time.perf_counter() - began) * 1000

                with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                    timings = sorted(pool.map(request, range(args.requests)))
                p95 = timings[math.ceil(len(timings) * 0.95) - 1]
                results.append({
                    "pollutant": pollutant, "period": name, "start": start.isoformat(),
                    "end": end.isoformat(), "requests": args.requests, "concurrency": args.concurrency,
                    "p50_ms": round(median(timings), 3), "p95_ms": round(p95, 3),
                    "maximum_ms": round(max(timings), 3), "target_met": p95 < 300,
                })
    report = {
        "kind": "complete HTTP requests with response-shape verification",
        "platform": platform.platform(), "python": platform.python_version(),
        "target_p95_ms": 300, "all_targets_met": all(item["target_met"] for item in results),
        "results": results,
    }
    path = load_settings().var / "reports" / "http-benchmark.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["all_targets_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
