"""Auto-finish orchestrator v2 (WP10).

Single-instance (lockfile). Runs ONE runner at a time, in the WP10 order:
E1/E2 v2 remaining -> E4 -> E5 -> E3b (non-trivial multi-file) -> E6 -> REPORT.
Every phase is QuotaTracker-gated and prints a token estimate first.
All runners are resumable per cell; progress persists across days.

Usage: .venv/Scripts/python.exe -m evals.auto_finish
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
LOCK = RESULTS / "autofinish.lock"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
ENV = {**os.environ, "DATABASE_URL": "postgresql://app:app@localhost:5433/qo"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def quota(model: str) -> int:
    """Remaining usable daily tokens for a model (85 percent stop rule)."""
    qf = RESULTS / "quota.json"
    used = 0
    if qf.is_file():
        data = json.loads(qf.read_text(encoding="utf-8"))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for k in (model, model.split("/", 1)[-1]):
            if k in data.get("models", {}):
                if data.get("date_utc") == today:
                    used = data["models"][k].get("tokens_today", 0)
                break
    return max(0, 170000 - used)


def run(cmd: list[str], label: str) -> None:
    print(f"[{now()}] PHASE {label}: start", flush=True)
    r = subprocess.run([PY, *cmd], cwd=ROOT, env=ENV, capture_output=True, text=True)
    tail = (r.stdout or "")[-1200:] + (r.stderr or "")[-600:]
    print(f"[{now()}] PHASE {label}: exit={r.returncode}", flush=True)
    print(tail, flush=True)


def cells_done(name: str) -> int:
    files = list(RESULTS.glob(f"{name}_*.json"))
    if not files:
        return 0
    d = json.loads(files[0].read_text(encoding="utf-8"))
    return sum(1 for c in d.get("cells", {}).values() if c.get("pass") is not None)


def e4b_enabled() -> bool:
    """E4b runs by default once the sanity check passes (WP13 step 4)."""
    f = RESULTS / "e4b_sanity.json"
    if not f.is_file():
        return False
    return json.loads(f.read_text(encoding="utf-8")).get("ok") is True


def e5_passes_fired() -> list:
    """Passes that actually fired in the one-cell trial (WP13 order 3)."""
    f = RESULTS / "e5_trial.json"
    if not f.is_file():
        return []
    fired = json.loads(f.read_text(encoding="utf-8")).get("fired", [])
    return [p for p in fired if p != "P9" or fired.count("P9") > 1]  # P9 fires on 2nd identical request


def e5_e4b_done() -> bool:
    files = list(RESULTS.glob("e5_tasks_e4b_*.json"))
    if not files:
        return False
    d = json.loads(files[0].read_text(encoding="utf-8"))
    cells = d.get("cells", {})
    n_cfgs = len({k.rsplit(":", 1)[1] for k in cells})
    return len(cells) >= 8 * n_cfgs and n_cfgs >= 2  # balanced + >=1 fired ablation


def wp3_done() -> bool:
    f = RESULTS / "e1_e2_v2_full.json"
    if not f.is_file():
        return False
    d = json.loads(f.read_text(encoding="utf-8"))
    return d.get("ran", 0) >= 30 and d.get("e2_micro_precision") is not None


PHASES = [
    {
        # WP13 order (1): E1/E2 v2 remainder (P25-P30 + any stale)
        "name": "E1E2-v2",
        "est_tokens": 45000,
        "gate": lambda: quota("openai/gpt-oss-120b") >= 45000,
        "cmd": lambda: ["-m", "evals.runner", "--version", "v2", "--max-tokens", "400000",
                        "--pace-s", "45", "--output", "evals/results/e1_e2_v2_full.json"],
        "done": wp3_done,
        "model": "openai/gpt-oss-120b",
    },
    {
        # WP13 order (2): E4b primary — off vs balanced first, then safe
        "name": "E4b-primary",
        "est_tokens": 45000,
        "gate": lambda: e4b_enabled() and quota("groq/qwen/qwen3.8-27b") >= 9000,
        "cmd": lambda: ["-m", "evals.run_e4", "--model", "groq/qwen/qwen3.8-27b",
                        "--experiment", "e4", "--tasks-file",
                        str(ROOT / "evals" / "tasks" / "tasks_e4b.json"),
                        "--configs", "off,balanced,safe", "--pace-s", "2"],
        "done": lambda: cells_done("e4_tasks_e4b") >= 24,
        "model": "groq/qwen/qwen3.8-27b",
    },
    {
        # WP13 order (3): E5 on the E4b tasks ONLY — one-cell trial first; passes
        # that never fire are skipped from the ablation arms.
        "name": "E5-on-e4b",
        "est_tokens": 3500,
        "gate": lambda: e4b_enabled() and quota("groq/openai/gpt-oss-20b") >= 3500
        and e5_passes_fired() != [],
        "cmd": lambda: ["-m", "evals.run_e4", "--model", "groq/openai/gpt-oss-20b",
                        "--experiment", "e5", "--tasks-file",
                        str(ROOT / "evals" / "tasks" / "tasks_e4b.json"),
                        "--passes", ",".join(e5_passes_fired()), "--pace-s", "2"],
        "done": lambda: e5_e4b_done(),
        "model": "groq/openai/gpt-oss-20b",
    },
    {
        # WP13 order (4): E3b non-trivial multi-file
        "name": "E3b",
        "est_tokens": 40000,
        "gate": lambda: quota("openai/gpt-oss-120b") >= 20000
        and quota("groq/openai/gpt-oss-20b") >= 30000,
        "cmd": lambda: ["-m", "evals.run_e3b", "--max-tokens", "170000", "--pace-s", "45"],
        "done": lambda: cells_done("e3b") >= 12,
        "model": "openai/gpt-oss-120b",
    },
    {
        # WP13 order (5): tiny-task E4 as dev/regression set, max 2 reps.
        # Runs LAST and only if quota remains after the primary work.
        "name": "E4-dev-regression",
        "est_tokens": 9000,
        "gate": lambda: quota("groq/qwen/qwen3.8-27b") >= 9000,
        "cmd": lambda: ["-m", "evals.run_e4", "--model", "groq/qwen/qwen3.8-27b",
                        "--experiment", "e4", "--tasks-file",
                        str(ROOT / "evals" / "tasks" / "tasks_e3.json"),
                        "--reps", "2", "--pace-s", "2"],
        "done": lambda: cells_done("e4_tasks_e3") >= 48,
        "model": "groq/qwen/qwen3.8-27b",
    },
    {
        "name": "E6",
        "est_tokens": 0,
        "gate": lambda: True,
        "cmd": lambda: ["-m", "evals.run_e6"],
        "done": lambda: (RESULTS / "pareto.png").is_file(),
        "model": "none",
    },
    {
        "name": "REPORT",
        "est_tokens": 0,
        "gate": lambda: True,
        "cmd": lambda: ["-m", "evals.make_report"],
        "done": lambda: False,  # always regenerate last
        "model": "none",
    },
]


def all_done() -> bool:
    return all(p["done"]() for p in PHASES if p["name"] not in ("REPORT",))


def acquire_lock() -> bool:
    RESULTS.mkdir(parents=True, exist_ok=True)
    if LOCK.is_file():
        print(f"another auto_finish instance holds {LOCK}; exiting")
        return False
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


def main() -> int:
    if not acquire_lock():
        return 3
    deadline = time.time() + 48 * 3600
    try:
        while time.time() < deadline:
            acted = False
            for p in PHASES:
                if p["name"] == "REPORT" or p["done"]():
                    continue
                est = p["est_tokens"]
                free = quota(p["model"])
                decision = "RUN" if free >= est else "SKIP (quota)"
                print(f"[{now()}] {p['name']}: est {est} tok, {free} tok free on "
                      f"{p['model']} -> {decision}", flush=True)
                if free < est and est > 0:
                    continue
                run(p["cmd"](), p["name"])
                acted = True
            if all_done():
                (RESULTS / "AUTOFINISH_DONE").write_text(
                    datetime.now(timezone.utc).isoformat(), encoding="utf-8")
                print(f"[{now()}] ALL PHASES COMPLETE", flush=True)
                return 0
            if not acted:
                print(f"[{now()}] remaining phases quota-blocked; sleeping 10 min "
                      f"for UTC reset", flush=True)
                time.sleep(600)
            else:
                time.sleep(15)
        print("autofinish deadline reached; progress saved and resumable", flush=True)
        return 1
    finally:
        if LOCK.is_file():
            LOCK.unlink()


if __name__ == "__main__":
    sys.exit(main())
