"""WP1d: measure added non-LLM proxy latency (p95) for P1/P4/P5/P7/P9 and
streaming first-token overhead. Pure in-process measurement, no network.

Usage: .venv/Scripts/python.exe scripts/bench_latency.py
Writes evals/results/latency_bench.json and prints a table.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app.optimizer.chain import run_chain  # noqa: E402
from app.optimizer.passes import cache_key  # noqa: E402
from app.optimizer.passes_c import p5_history_compaction  # noqa: E402
from app.optimizer.policy import resolve_policy  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "evals" / "results"


def big_request(tool_lines: int = 3000, turns: int = 60) -> dict:
    msgs = [{"role": "system", "content": "You are a coding agent. " * 20}]
    for i in range(turns):
        msgs.append({"role": "user", "content": f"turn {i}: " + "x" * 200})
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "run_tests", "arguments": "{}"}}]})
        tool_log = "\n".join(f"line {j}: log" for j in range(tool_lines))
        msgs.append({"role": "tool", "content": tool_log + "\nERROR: something",
                     "tool_call_id": f"c{i}"})
    msgs.append({"role": "user", "content": "continue"})
    return {"model": "m", "messages": msgs}


def bench(name: str, fn, reps: int = 200) -> dict:
    samples_ms = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples_ms.append((time.perf_counter() - t0) * 1000)
    samples_ms.sort()
    p = lambda q: samples_ms[min(len(samples_ms) - 1, int(q * len(samples_ms)))]  # noqa: E731
    return {
        "name": name,
        "reps": reps,
        "median_ms": round(statistics.median(samples_ms), 3),
        "p95_ms": round(p(0.95), 3),
        "max_ms": round(samples_ms[-1], 3),
    }


def main() -> int:
    req = big_request()
    pol_safe = resolve_policy("safe", None)
    pol_balanced = resolve_policy("balanced", None)

    rows = [
        bench("chain_safe (P9miss,P4,P1,P7)", lambda: run_chain(req, pol_safe)),
        bench("chain_balanced (+P5,P2,P3,P6)", lambda: run_chain(req, pol_balanced)),
        bench("P4 alone", lambda: p4_only(req)),
        bench("P5 alone", lambda: p5_history_compaction(req, pol_balanced)),
        bench("P1 alone", lambda: p1_only(req)),
        bench("P7 alone", lambda: p7_only(req)),
        bench("P9 cache-key compute", lambda: cache_key("m", req, 1)),
    ]
    out = {"target_p95_ms": 150, "rows": rows,
           "input_tokens_est_before": sum(len(str(m.get("content", ""))) // 4 for m in req["messages"]),
           "note": "in-process pure-pass latency; excludes HTTP + provider time"}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "latency_bench.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"{'bench':40s} {'median':>9s} {'p95':>9s} {'max':>9s}")
    for r in rows:
        flag = " OK" if r["p95_ms"] < 150 else " MISS"
        print(f"{r['name']:40s} {r['median_ms']:>7.2f}ms {r['p95_ms']:>7.2f}ms {r['max_ms']:>7.2f}ms{flag}")
    print(f"saved -> {RESULTS / 'latency_bench.json'}")
    return 0


def p4_only(req):  # noqa: ANN201
    from app.optimizer.passes import p4_tool_output_compaction

    return p4_tool_output_compaction(req, resolve_policy("safe", None))


def p1_only(req):  # noqa: ANN201
    from app.optimizer.passes import p1_cache_aware_assembly

    return p1_cache_aware_assembly(req, resolve_policy("safe", None))


def p7_only(req):  # noqa: ANN201
    from app.optimizer.passes import p7_output_controls

    return p7_output_controls(req, resolve_policy("safe", None))


if __name__ == "__main__":
    raise SystemExit(main())
