"""E4 token benchmark + E5 ablation (WP6).

A multi-turn tool-using builder loop driven through the LIVE proxy
(http://127.0.0.1:8001) so history and tool output actually grow and the
passes have something to compress. Resumable per (task, rep, mode/config);
persist after every cell; QuotaTracker-aware.

E4: 8 tasks x 2 reps x modes {off, safe, balanced} on ONE builder model.
E5: 4 tasks x 2 reps, balanced with one pass disabled each (QO_PASS_* is
    per-process, so the proxy runs in-process via FastAPI TestClient).

Usage:
  python -m evals.run_e4 --model groq/qwen/qwen3.8-27b --experiment e4
  python -m evals.run_e4 --model groq/openai/gpt-oss-20b --experiment e5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import winloop  # noqa: E402,F401
from app.optimizer.passes import PASS_FUNCTIONS  # noqa: E402
from checks import check_generated  # noqa: E402

from evals.quota import QuotaTracker  # noqa: E402

TASKS = Path(__file__).resolve().parent / "tasks" / "tasks_e3.json"
RESULTS = Path(__file__).resolve().parent / "results"
E4_TASKS = [f"T{i}" for i in (1, 2, 3, 5, 6, 7, 8, 4)]
E5_TASKS = ["T1", "T3", "T6", "T7"]

SYSTEM = (
    "You are a coding agent completing a task across multiple steps. "
    "At each step you may call `run_tests` (returns the test output) or "
    "`read_file` (returns a file listing). When ready, call `submit_code` with "
    "the complete Python source. Output JSON: "
    '{"tool": "run_tests|read_file|submit_code", "args": {...}}'
)

TOOLS_SPEC = [
    {"type": "function",
     "function": {"name": "run_tests",
                  "description": "run the task tests; returns PASS or FAIL with output",
                  "parameters": {"type": "object", "properties": {},
                                 "additionalProperties": False}}},
    {"type": "function",
     "function": {"name": "read_file",
                  "description": "list files in the project",
                  "parameters": {"type": "object", "properties": {},
                                 "additionalProperties": False}}},
    {"type": "function",
     "function": {"name": "submit_code",
                  "description": "submit the final Python source",
                  "parameters": {"type": "object",
                                 "properties": {"code": {"type": "string"}},
                                 "additionalProperties": False}}},
]


def make_call(client, model: str, mode: str, disabled: list[str] | None = None):  # noqa: ANN001,ANN201
    """Completion callable posting to the in-process proxy (lifespan already up).

    `disabled` pass ids emulate E5 by temporarily removing them from the chain.
    """
    removed: dict = {}

    def call(messages: list[dict]) -> tuple[dict, float]:
        if disabled:
            for pid in disabled:
                if pid in PASS_FUNCTIONS:
                    removed[pid] = PASS_FUNCTIONS.pop(pid)
        try:
            t0 = time.perf_counter()
            r = client.post(
                "/v1/chat/completions",
                json={"model": model, "messages": messages, "tools": TOOLS_SPEC},
                headers={"x-optimizer-mode": mode},
            )
            elapsed = time.perf_counter() - t0
            if r.status_code != 200:
                raise RuntimeError(f"proxy {r.status_code}: {r.text[:120]}")
            return r.json(), elapsed
        finally:
            for pid, fn in removed.items():
                PASS_FUNCTIONS[pid] = fn
                removed.pop(pid, None)

    return call


def tool_result(task: dict, name: str, attempt: int) -> dict:
    if name == "run_tests":
        if attempt < 2:
            out = f"collected 3 items\nFAILED {task['id']}::check (assertion)\n2 passed"
            return {"status": "FAIL", "output": out}
        return {"status": "PASS", "output": "3 passed"}
    if name == "read_file":
        return {"files": [f"{task['id']}.py", "tests.py", "README.md",
                          f"docs/notes_{attempt}.md"]}
    return {}


def run_cell(call, task: dict, max_turns: int = 8) -> dict:
    """One multi-turn tool loop until submit_code or turn cap."""
    import json as _json

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": task["prompt"]},
    ]
    submitted: str | None = None
    tokens_in = tokens_out = 0
    latencies: list[float] = []
    turns = 0
    for attempt in range(max_turns):
        resp, elapsed = call(messages)
        usage = resp.get("usage") or {}
        tokens_in += usage.get("prompt_tokens", 0)
        tokens_out += usage.get("completion_tokens", 0)
        latencies.append(elapsed)
        turns += 1
        msg = (resp.get("choices") or [{}])[0].get("message", {})
        tool_calls = msg.get("tool_calls") or []
        if tool_calls:
            fn = tool_calls[0]["function"]
            name = fn["name"]
            try:
                args_ = _json.loads(fn.get("arguments") or "{}")
            except _json.JSONDecodeError:
                args_ = {}
            if name == "submit_code":
                submitted = args_.get("code") or (msg.get("content") or "")
                break
            tr = tool_result(task, name, attempt)
            messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
            messages.append({"role": "tool",
                             "content": _json.dumps(tr) + "\n" + ("x" * 400),
                             "tool_call_id": tool_calls[0].get("id", f"t{attempt}")})
            continue
        submitted = msg.get("content") or ""
        break
    if submitted is None:
        return {"pass": None, "infra_aborted": True, "turns": turns,
                "tokens_in": tokens_in, "tokens_out": tokens_out}
    chk = check_generated(task["id"], submitted.strip())
    return {"pass": chk["pass"], "stage": chk["stage"], "turns": turns,
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "latency_avg_s": round(sum(latencies) / len(latencies), 2)}


def summarize(results: dict) -> None:
    by_mode: dict[str, list] = {}
    for cell, rec in results["cells"].items():
        if rec.get("pass") is None:
            continue
        cfg = cell.split(":")[2]
        by_mode.setdefault(cfg, []).append(rec)
    summary = {}
    for cfg, recs in sorted(by_mode.items()):
        n = len(recs)
        summary[cfg] = {
            "n": n,
            "pass_rate": sum(r["pass"] for r in recs) / n,
            "tokens_in_avg": sum(r["tokens_in"] for r in recs) / n,
            "tokens_out_avg": sum(r["tokens_out"] for r in recs) / n,
            "iterations_avg": sum(r["turns"] for r in recs) / n,
            "latency_avg_s": round(sum(r["latency_avg_s"] for r in recs) / n, 2),
        }
    results["summary"] = summary

    # PAIRED analysis: only bases where every config of the run completed
    configs = sorted(by_mode.keys())
    bases: dict[str, dict[str, dict]] = {}
    for cell, rec in results["cells"].items():
        if rec.get("pass") is None:
            continue
        base, cfg = cell.rsplit(":", 1)
        bases.setdefault(base, {})[cfg] = rec
    paired_bases = {b: m for b, m in bases.items() if len(m) == len(configs)}
    paired = {}
    diff_outcomes = 0
    for cfg in configs:
        recs = [m[cfg] for m in paired_bases.values()]
        paired[cfg] = {
            "n": len(recs),
            "pass_rate": sum(r["pass"] for r in recs) / len(recs) if recs else None,
            "tokens_in_avg": round(sum(r["tokens_in"] for r in recs) / len(recs)) if recs else None,
            "tokens_out_avg": round(sum(r["tokens_out"] for r in recs) / len(recs)) if recs else None,
        }
    for m in paired_bases.values():
        outs = {m[c]["pass"] for c in configs}
        if len(outs) > 1:
            diff_outcomes += 1
    base_cfg = configs[0] if "off" not in configs else "off"
    if paired.get(base_cfg, {}).get("tokens_in_avg"):
        ref = paired[base_cfg]["tokens_in_avg"]
        for cfg in configs:
            if paired[cfg]["tokens_in_avg"]:
                paired[cfg]["tokens_in_delta_pct"] = round(
                    100 * (paired[cfg]["tokens_in_avg"] - ref) / ref, 1)
    results["paired"] = {
        "bases": sorted(paired_bases), "paired_n": len(paired_bases),
        "tasks_with_different_outcomes": diff_outcomes,
        "per_config": paired,
    }
    print(json.dumps({"summary": summary, "paired": results["paired"]}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="groq/qwen/qwen3.8-27b")
    parser.add_argument("--experiment", choices=["e4", "e5"], default="e4")
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--max-tokens", type=float, default=170000, dest="max_tokens")
    parser.add_argument("--pace-s", type=float, default=2.0, dest="pace_s")
    parser.add_argument("--tasks-file", default=None, dest="tasks_file")
    args = parser.parse_args()

    quota = QuotaTracker()
    json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    tasks_src = args.tasks_file or str(TASKS)
    all_tasks = json.loads(Path(tasks_src).read_text(encoding="utf-8"))["tasks"]
    by_id_all = {t["id"]: t for t in all_tasks}
    task_ids = E5_TASKS if args.experiment == "e5" else E4_TASKS

    if args.experiment == "e4":
        configs: list[tuple[str, str | None]] = [("off", None), ("safe", None), ("balanced", None)]
    else:  # E5 ablation: balanced minus one pass each
        configs = [("balanced", None)] + [
            (f"balanced_no_{pid}", [pid]) for pid in ("P9", "P4", "P5", "P1")
        ]

    out_path = RESULTS / f"{args.experiment}_{args.model.replace('/', '_')}.json"
    results: dict = (
        json.loads(out_path.read_text(encoding="utf-8"))
        if out_path.is_file() else {"builder_model": args.model, "experiment": args.experiment,
                                    "tasks_file": tasks_src, "cells": {}}
    )
    # infra-aborted cells are retryable: drop them so resume re-runs them
    dropped = [k for k, v in results["cells"].items() if v.get("infra_aborted")]
    for k in dropped:
        results["cells"].pop(k, None)
    if dropped:
        print(f"retrying {len(dropped)} infra-aborted cells")
    results["builder_model"] = args.model

    n_cells = len(task_ids) * args.reps * len(configs)
    print(f"{args.experiment.upper()} estimate: {n_cells} cells x ~9K tokens ~= "
          f"{n_cells * 9 // 1}K total; budget 170K/day/model -> ~{max(1, n_cells * 9 // 170)} day(s). "
          "Runs until today's quota; resumable.")

    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:  # lifespan: settings, gateway, pool
        run_all(client, quota, args, task_ids, by_id_all, configs, results, out_path)
    summarize(results)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


def run_all(client, quota, args, task_ids, by_id_all, configs, results, out_path) -> None:  # noqa: ANN001,ANN201
    for tid in task_ids:
        task = by_id_all[tid]
        for rep in range(args.reps):
            for cfg_name, disabled in configs:
                cell = f"{tid}:rep{rep}:{cfg_name}"
                if cell in results["cells"]:
                    continue
                if not quota.check_tpd(args.model, 9000):
                    print("quota reached, resume tomorrow")
                    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
                    return 0
                call = make_call(client, args.model, cfg_name.split("_no_")[0], disabled)
                try:
                    rec = run_cell(call, task)
                except Exception as exc:  # noqa: BLE001
                    name = type(exc).__name__
                    rec = {"pass": None, "infra_aborted": "RateLimit" in name or "429" in str(exc),
                           "error": f"{name}: {exc}"[:200]}
                results["cells"][cell] = rec
                quota.record_usage(args.model, rec.get("tokens_in", 0) + rec.get("tokens_out", 0))
                out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
                print(f"{cell}: pass={rec.get('pass')} tok_in={rec.get('tokens_in')}", flush=True)
                time.sleep(args.pace_s)


if __name__ == "__main__":
    raise SystemExit(main())
