"""Auto-finish orchestrator (WP3-6 closure).

Runs the remaining quota-gated evals in priority order, re-checking limits
automatically: when a model's daily quota resets (UTC date change), the blocked
phases re-trigger. Every phase is resumable (result files persist per cell).
When everything measurable is done, writes evals/results/AUTOFINISH_DONE.

Usage (long-running):
  .venv/Scripts/python.exe -m evals.auto_finish
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
ENV = {**__import__("os").environ,
       "DATABASE_URL": "postgresql://app:app@localhost:5433/qo"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def quota(model: str) -> int:
    qf = RESULTS / "quota.json"
    if not qf.is_file():
        return 200000
    data = json.loads(qf.read_text(encoding="utf-8"))
    for k in (model, model.split("/", 1)[-1]):
        if k in data.get("models", {}):
            return max(0, 170000 - data["models"][k].get("tokens_today", 0))
    return 170000  # unused model: full usable budget


def run(cmd: list[str], label: str) -> None:
    print(f"[{now()}] PHASE {label}: start", flush=True)
    r = subprocess.run([PY, *cmd], cwd=ROOT, env=ENV, capture_output=True, text=True)
    tail = (r.stdout or "")[-1500:] + (r.stderr or "")[-800:]
    print(f"[{now()}] PHASE {label}: exit={r.returncode}\n{tail}", flush=True)


def wp3_done() -> bool:
    f = RESULTS / "e1_e2_v2_full.json"
    if not f.is_file():
        return False
    d = json.loads(f.read_text(encoding="utf-8"))
    # complete only when all 30 ran AND precision/recall were actually computed
    # (quota-aborted prompts leave them None -> retryable)
    return d.get("ran", 0) >= 30 and d.get("e2_micro_precision") is not None


def e3_done() -> bool:
    files = list(RESULTS.glob("e3_groq_*.json"))
    if not files:
        return False
    d = json.loads(files[0].read_text(encoding="utf-8"))
    cells = d.get("cells", {})
    return sum(1 for c in cells.values() if c.get("pass") is not None) >= 32


def e4_done() -> bool:
    files = list(RESULTS.glob("e4_*.json"))
    if not files:
        return False
    d = json.loads(files[0].read_text(encoding="utf-8"))
    cells = d.get("cells", {})
    completed = sum(1 for c in cells.values() if c.get("pass") is not None)
    return completed >= 48


def e5_done() -> bool:
    files = list(RESULTS.glob("e5_*.json"))
    if not files:
        return False
    d = json.loads(files[0].read_text(encoding="utf-8"))
    cells = d.get("cells", {})
    completed = sum(1 for c in cells.values() if c.get("pass") is not None)
    return completed >= 40


PHASES = [
    ("WP3-resume", wp3_done,
     lambda: ["-m", "evals.runner", "--version", "v2", "--max-tokens", "400000",
              "--pace-s", "5", "--output", "evals/results/e1_e2_v2_full.json"],
     lambda: quota("openai/gpt-oss-120b") >= 100000),
    ("E3", e3_done,
     lambda: ["-m", "evals.run_e3", "--builder", "groq/openai/gpt-oss-20b",
              "--max-tokens", "170000"],
     lambda: quota("openai/gpt-oss-120b") >= 80000
     and quota("openai/gpt-oss-20b") >= 100000),
    ("E4", e4_done,
     lambda: ["-m", "evals.run_e4", "--model", "groq/qwen/qwen3.8-27b",
              "--experiment", "e4", "--pace-s", "2"],
     lambda: quota("groq/qwen/qwen3.8-27b") >= 9000),
    ("E5", e5_done,
     lambda: ["-m", "evals.run_e4", "--model", "groq/openai/gpt-oss-20b",
              "--experiment", "e5", "--pace-s", "2"],
     lambda: quota("openai/gpt-oss-20b") >= 9000),
    ("E6", lambda: (RESULTS / "pareto.png").is_file(),
     lambda: ["-m", "evals.run_e6"], lambda: True),
    ("REPORT", lambda: False,  # always regenerate last
     lambda: ["-m", "evals.make_report"], lambda: True),
]


def all_done() -> bool:
    return all(check() for _, check, _, _ in PHASES if _ != "REPORT")


def main() -> int:
    deadline = time.time() + 48 * 3600  # hard stop: 48h
    while time.time() < deadline:
        acted = False
        for name, check, cmd_fn, gate in PHASES:
            if name == "REPORT" or check():
                continue
            if gate():
                run(cmd_fn(), name)
                acted = True
        if all_done():
            (RESULTS / "AUTOFINISH_DONE").write_text(
                datetime.now(timezone.utc).isoformat(), encoding="utf-8")
            print(f"[{now()}] ALL PHASES COMPLETE", flush=True)
            return 0
        if not acted:
            print(f"[{now()}] all remaining phases quota-blocked; "
                  f"sleeping 10 min for UTC reset", flush=True)
            time.sleep(600)
        else:
            time.sleep(15)
    print("autofinish deadline reached; progress saved and resumable", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
