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

from clean_air.settings import POLLUTANTS, load_settings, months_before, utc_today


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--source", choices=("eea", "openaq", "combined"), default="eea")
    parser.add_argument("--pollutants", choices=list(POLLUTANTS), nargs="+", default=["PM2.5", "PM10"])
    parser.add_argument("--include-current", action="store_true")
    args = parser.parse_args()
    if not (1 <= args.requests <= 10000 and 1 <= args.concurrency <= 32):
        parser.error("Use 1..10000 requests and 1..32 concurrent clients.")
    base = f"http://127.0.0.1:{args.port}"
    with httpx.Client(base_url=base, trust_env=False, timeout=30) as client:
        response = client.get("/api/status", params={"source": args.source})
        response.raise_for_status()
        status = response.json()
        if not status["ready"]:
            raise RuntimeError("The real-data backfill must finish before benchmarking.")
        selected = (
            [status["provider_statuses"][source] for source in ("eea", "openaq")]
            if args.source == "combined" else [status]
        )
        if any(
            not source["ready"] or (
                source["data_source"] == "openaq" and not source.get("backfill_complete")
            ) for source in selected
        ):
            raise RuntimeError("Every benchmarked source must have completed its historical backfill.")
        earliest = max(date.fromisoformat(source["available_start"]) for source in selected)
        end = min(
            utc_today() - timedelta(days=1),
            *(date.fromisoformat(source["available_end"]) for source in selected),
        )
        periods = {
            "one_day": end,
            "thirty_days": max(earliest, end - timedelta(days=29)),
            "twelve_months": max(earliest, months_before(end + timedelta(days=1), 12)),
        }
        if args.include_current:
            periods["current"] = None
        results = []
        for pollutant in args.pollutants:
            for name, start in periods.items():
                params = {"pollutant": pollutant, "source": args.source}
                if start is not None:
                    params.update(start=start.isoformat(), end=end.isoformat())
                if args.source == "combined":
                    params["sources"] = ["eea", "openaq"]

                def request(_, query=params):
                    began = time.perf_counter()
                    result = client.get("/api/cleanest", params=query)
                    body = result.json()
                    if result.status_code == 422:
                        code = body.get("error", {}).get("code")
                        if code not in ("NO_ELIGIBLE_CITY", "NO_FRESH_COMPARABLE_HOUR"):
                            result.raise_for_status()
                        return (time.perf_counter() - began) * 1000, code
                    result.raise_for_status()
                    panels = body["panels"] if args.source == "combined" else [{"result": body}]
                    if args.source == "combined" and {panel["id"] for panel in panels} != {"eea", "openaq"}:
                        raise RuntimeError("The combined API used an unselected source or omitted a selected one.")
                    for panel in panels:
                        if panel.get("error"):
                            if panel["error"]["code"] not in ("NO_ELIGIBLE_CITY", "NO_FRESH_COMPARABLE_HOUR"):
                                raise RuntimeError(f"Unexpected source-panel error: {panel['error']['code']}")
                            continue
                        answer = panel["result"]
                        if not answer.get("winners") or not answer.get("methodology") or not answer.get("policy_id"):
                            raise RuntimeError("The API did not return the required successful comparison shape.")
                    return (time.perf_counter() - began) * 1000, "comparison"

                with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                    outcomes = list(pool.map(request, range(args.requests)))
                timings = sorted(item[0] for item in outcomes)
                p95 = timings[math.ceil(len(timings) * 0.95) - 1]
                results.append({
                    "source": args.source, "pollutant": pollutant, "period": name,
                    "start": start.isoformat() if start else None,
                    "end": end.isoformat() if start else None,
                    "requests": args.requests, "concurrency": args.concurrency,
                    "outcomes": {outcome: sum(item[1] == outcome for item in outcomes)
                                 for outcome in sorted({item[1] for item in outcomes})},
                    "p50_ms": round(median(timings), 3), "p95_ms": round(p95, 3),
                    "maximum_ms": round(max(timings), 3), "target_met": p95 < 300,
                })
    report = {
        "kind": "complete HTTP requests with response-shape verification",
        "platform": platform.platform(), "python": platform.python_version(),
        "target_p95_ms": 300, "all_targets_met": all(item["target_met"] for item in results),
        "results": results,
    }
    path = load_settings().var / "reports" / f"http-benchmark-{args.source}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["all_targets_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
