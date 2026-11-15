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
from evals.stamp import (  # noqa: E402  # noqa: E402
    get_stamp,
    models_hash,
    prompts_hash,
    proxy_registry_hash,
    result_stamp,
    stamp_matches,
    tasks_hash,
)

TASKS = Path(__file__).resolve().parent / "tasks" / "tasks_e3.json"
RESULTS = Path(__file__).resolve().parent / "results"
ROOT = Path(__file__).resolve().parents[1]
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


def by_id_from(tasks_file: Path, task_id: str) -> dict:
    tasks = json.loads(Path(tasks_file).read_text(encoding="utf-8"))["tasks"]
    return next(t for t in tasks if t["id"] == task_id)


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


def run_cell(call, task: dict, max_turns: int = 8, ledger_pool=None) -> dict:
    """One multi-turn tool loop until submit_code or turn cap.

    With ledger_pool, each request's ledger row is captured per turn:
    tokens_baseline_est (input est), tokens_actual and passes fired."""
    import json as _json


    messages: list[dict] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": task["prompt"]},
    ]
    submitted: str | None = None
    tokens_in = tokens_out = 0
    latencies: list[float] = []
    requests: list[dict] = []
    turns = 0
    for attempt in range(max_turns):
        resp, elapsed = call(messages)
        usage = resp.get("usage") or {}
        tokens_in += usage.get("prompt_tokens", 0)
        tokens_out += usage.get("completion_tokens", 0)
        latencies.append(elapsed)
        turns += 1
        if ledger_pool is not None:
            try:
                with ledger_pool.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "SELECT tokens_baseline_est, pass_reports FROM proxy_requests "
                        "ORDER BY created_at DESC LIMIT 1")
                    row = cur.fetchone()
                if row:
                    fired = [pr["pass"] for pr in (row[1] or [])
                             if not pr.get("skipped")]
                    requests.append({
                        "tokens_baseline_est": int(row[0] or 0),
                        "passes_fired": fired,
                    })
            except Exception:
                pass  # ledger capture is best-effort; never break the cell
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
                "tokens_in": tokens_in, "tokens_out": tokens_out,
                "requests": requests}
    chk = check_generated(task["id"], submitted.strip())
    return {"pass": chk["pass"], "stage": chk["stage"], "turns": turns,
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "requests": requests,
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
    from evals.stats import paired_summary

    results["paired"] = paired_summary(results["cells"], configs)
    print(json.dumps({"summary": summary, "paired": results["paired"]}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="groq/qwen/qwen3.8-27b")
    parser.add_argument("--experiment", choices=["e4", "e5"], default="e4")
    parser.add_argument("--reps", type=int, default=2)
    parser.add_argument("--max-tokens", type=float, default=170000, dest="max_tokens")
    parser.add_argument("--pace-s", type=float, default=2.0, dest="pace_s")
    parser.add_argument("--tasks-file", default=None, dest="tasks_file")
    parser.add_argument("--configs", default="off,safe,balanced",
                        help="comma-separated mode order (quota priority order)")
    parser.add_argument("--max-cells", type=int, default=0, dest="max_cells")
    parser.add_argument("--passes", default="P9,P4,P5,P1",
                        help="passes to ablate in E5 (only ones that fired)")
    parser.add_argument("--passes-from-trial", action="store_true",
                        dest="passes_from_trial",
                        help="read the fired pass list from e5_trial.json")
    parser.add_argument("--task-ids", default=None, dest="task_ids",
                        help="comma-separated task ids (e.g. 'L1,L3' for the pilot)")
    parser.add_argument("--pilot", action="store_true",
                        help="pilot mode: 1 rep, per-request ledger capture, pairs summary")
    args = parser.parse_args()

    quota = QuotaTracker()
    json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    tasks_src = args.tasks_file or str(TASKS)
    all_tasks = json.loads(Path(tasks_src).read_text(encoding="utf-8"))["tasks"]
    by_id_all = {t["id"]: t for t in all_tasks}
    task_ids = E5_TASKS if args.experiment == "e5" else E4_TASKS
    if args.task_ids:
        wanted = [t.strip() for t in args.task_ids.split(",") if t.strip()]
        task_ids = [t for t in task_ids if t in wanted]

    if args.experiment == "e4":
        order = [c.strip() for c in args.configs.split(",") if c.strip()]
        configs: list[tuple[str, str | None]] = [(m, None) for m in order]
    else:  # E5 ablation: balanced minus one pass each; pass list from trial
        fired = [p.strip() for p in (args.passes or "").split(",") if p.strip()]
        if args.passes_from_trial:
            trial = RESULTS / "e5_trial.json"
            if trial.is_file():
                fired = json.loads(trial.read_text(encoding="utf-8")).get("fired", fired)
            print(f"E5 ablation arms from trial: {fired}")
        configs = [("balanced", None)] + [
            (f"balanced_no_{pid}", [pid]) for pid in fired
        ]

    tasks_version = json.loads(
        Path(tasks_src).read_text(encoding="utf-8")).get("version", "dev")
    out_path = RESULTS / f"{args.experiment}_{tasks_version}_{args.model.replace('/', '_')}.json"
    results: dict = (
        json.loads(out_path.read_text(encoding="utf-8"))
        if out_path.is_file() else {"builder_model": args.model, "experiment": args.experiment,
                                    "tasks_file": tasks_src, "cells": {}}
    )
    # infra-aborted cells are retryable; cells from an older code stamp are
    # stale and re-run (WP12: never pair/average across stamps)
    stamp = result_stamp("e4", {
        "registry": proxy_registry_hash(), "models": models_hash(),
        "prompts": prompts_hash(), "tasks": tasks_hash(tasks_src),
    })
    results["stamp"] = stamp
    results["tasks_version"] = tasks_version
    dropped = [
        k for k, v in results["cells"].items()
        if v.get("infra_aborted") or not stamp_matches(v.get("stamp"), stamp)
    ]
    for k in dropped:
        results["cells"].pop(k, None)
    kept = len(results["cells"])
    kept_tok = sum(c.get("tokens_in", 0) + c.get("tokens_out", 0)
                   for c in results["cells"].values())
    rerun_est = len(dropped) * 9000
    print(f"stamp: kept {kept} cells (~{kept_tok // 1000}K tok), "
          f"re-run {len(dropped)} cells (est ~{rerun_est // 1000}K tok) "
          f"[registry {stamp['inputs']['registry'][:8]}]")
    results["builder_model"] = args.model

    n_cells = len(task_ids) * args.reps * len(configs)
    print(f"{args.experiment.upper()} estimate: {n_cells} cells x ~9K tokens ~= "
          f"{n_cells * 9 // 1}K total; budget 170K/day/model -> ~{max(1, n_cells * 9 // 170)} day(s). "
          "Runs until today's quota; resumable.")

    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:  # lifespan: settings, gateway, pool
        run_all(client, quota, args, task_ids, by_id_all, configs, results, out_path,
                max_cells=args.max_cells)
    summarize(results)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


def run_pilot_cells(client, quota, args, results, out_path) -> None:  # noqa: ANN001
    """WP17 S2p pilot: tasks L1+L3, 1 rep, off+balanced = 4 cells, sequential.
    Per-request ledger capture; shown as pairs; labelled 'pilot, n=2 pairs'."""

    from app.optimizer.validate import validate_internal as _unused_guard  # noqa: F401

    model = args.model
    stamp = get_stamp()
    results["stamp"] = stamp
    results["pilot"] = True
    results["pilot_note"] = "pilot, n=2 pairs"
    pool = client.app.state.pool
    ledger_pool = pool  # run_cell captures per-request rows via this pool
    consecutive_bad = 0
    configs = ["off", "balanced"]
    wanted = [t.strip() for t in (args.task_ids or "L1,L3").split(",") if t.strip()]
    for tid in wanted:
        task = by_id_from(Path(args.tasks_file or
                               str(ROOT / "evals" / "tasks_e4b.json")), tid)
        for mode in configs:
            cell = f"{tid}:{mode}"
            if cell in results["cells"]:
                continue
            if not quota.check_tpd(model, 9000):
                print(f"[quota] {model} exhausted; pilot stops here", flush=True)
                out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
                return
            call = make_call(client, model, mode)
            try:
                rec = run_cell(call, task, ledger_pool=ledger_pool)
                rec["mode"] = mode
                rec["task"] = tid
                rec["stamp"] = stamp
            except Exception as exc:  # noqa: BLE001
                name = type(exc).__name__
                rec = {"pass": None, "infra_aborted": "RateLimit" in name,
                       "error": f"{name}: {exc}"[:200], "mode": mode, "task": tid}
            results["cells"][cell] = rec
            quota.record_usage(model, rec.get("tokens_in", 0) + rec.get("tokens_out", 0))
            out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
            first_est = max((r["tokens_baseline_est"]
                             for r in rec.get("requests", [])), default=0)
            fired = sorted({p for r in rec.get("requests", [])
                            for p in r["passes_fired"]})
            print(f"pilot {cell}: pass={rec.get('pass')} "
                  f"tok_in={rec.get('tokens_in')} first_call_est={first_est} "
                  f"passes_fired={fired}", flush=True)
            if rec.get("pass") is None and not rec.get("infra_aborted"):
                consecutive_bad += 1
            else:
                consecutive_bad = 0
            if consecutive_bad >= 3:
                raise SystemExit(2)
            time.sleep(args.pace_s)

    # pairs summary
    pairs = []
    for tid in wanted:
        off = results["cells"].get(f"{tid}:off")
        bal = results["cells"].get(f"{tid}:balanced")
        if off and bal and off.get("pass") is not None and bal.get("pass") is not None:
            pairs.append({
                "task": tid,
                "off": {"pass": off["pass"], "tokens_in": off["tokens_in"],
                        "first_call_est": max(
                            (r["tokens_baseline_est"] for r in off.get("requests", [])),
                            default=0)},
                "balanced": {"pass": bal["pass"], "tokens_in": bal["tokens_in"],
                             "first_call_est": max(
                                 (r["tokens_baseline_est"] for r in bal.get("requests", [])),
                                 default=0)},
            })
    results["paired"] = {"pairs": pairs, "n_pairs": len(pairs),
                         "label": "pilot, n=2 pairs"}
    print(json.dumps(results["paired"], indent=2), flush=True)


def run_all(client, quota, args, task_ids, by_id_all, configs, results, out_path,
            max_cells: int = 0) -> None:  # noqa: ANN001,ANN201
    budget = max_cells
    stop_file = Path(__file__).resolve().parents[1] / "results" / "STOP"
    consecutive_bad = 0
    for tid in task_ids:
        task = by_id_all[tid]
        for rep in range(args.reps):
            for cfg_name, disabled in configs:
                cell = f"{tid}:rep{rep}:{cfg_name}"
                if cell in results["cells"]:
                    continue
                if stop_file.is_file():
                    print(f"[{time.strftime('%H:%M:%S')}] STOP file seen; "
                          f"exiting cleanly after persisted state", flush=True)
                    return
                if budget <= 0:
                    print("cell budget reached for this invocation; resume later")
                    return
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
                budget -= 1
                if rec.get("pass") is None and not rec.get("infra_aborted"):
                    consecutive_bad += 1
                else:
                    consecutive_bad = 0
                if consecutive_bad >= 3:
                    print("SAFETY STOP: 3 consecutive non-429 failures; "
                          "write the blocker and investigate", flush=True)
                    raise SystemExit(2)
                time.sleep(args.pace_s)


if __name__ == "__main__":
    raise SystemExit(main())
