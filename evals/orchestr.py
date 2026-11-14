"""WP13 PART 5: unattended daily orchestrator (`make eval-daily`).

Strictly sequential stages (one experiment on one model at a time):
  S1  E1/E2 v2 remaining prompts (spec pipeline, gpt-oss-120b)
  S2  E4b primary: off vs balanced first, then safe (groq/qwen/qwen3.8-27b)
  S3  E5 ablation on the E4b tasks only (gpt-oss-20b); one-cell pass-firing
      trial first — passes that never fire are skipped
  S4  E3b spec generation (gpt-oss-120b) then builder cells (gpt-oss-20b),
      paired analysis only
  S5  E4 tiny fixtures as dev/regression set, max 2 reps (groq/qwen/qwen3.8-27b)
  S6  finalization (no LLM calls): REPORT.md, pareto, static HTML, README
      table data, RESUME_CLAIMS.md

Safety: single instance (lockfile with PID liveness), STOP file exits cleanly
after the current cell, QuotaTracker gates with hourly probes on daily-limit
429s, 3 consecutive non-429 failures or a stamp mismatch write a blocker into
docs/STATUS.md and stop. The orchestrator never edits code/prompts/labels.
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
LOCK = RESULTS / "eval-daily.lock"
STOP = RESULTS / "STOP"
STATUS_MD = ROOT / "docs" / "STATUS.md"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
ENV = {**os.environ, "DATABASE_URL": "postgresql://app:app@localhost:5433/qo"}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _used(model: str) -> int:
    qf = RESULTS / "quota.json"
    if not qf.is_file():
        return 0
    data = json.loads(qf.read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT / "evals"))
    from evals.quota import normalize_key

    key = normalize_key(model)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if data.get("date_utc") != today:
        return 0
    return data.get("models", {}).get(key, {}).get("tokens_today", 0)


def quota_free(model: str) -> int:
    sys.path.insert(0, str(ROOT / "evals"))
    from evals.quota import QuotaTracker

    if QuotaTracker().is_exhausted(model):
        return 0
    return max(0, 170000 - _used(model))


def write_status(blocker: str | None = None, stage: str = "") -> None:
    lines = [f"# STATUS (updated {now()})", ""]
    lines.append(f"- current stage: {stage or 'none'}")
    if blocker:
        lines.append(f"- **BLOCKER: {blocker}**")
    else:
        lines.append("- no blockers")
    STATUS_MD.parent.mkdir(parents=True, exist_ok=True)
    STATUS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(cmd: list[str], label: str) -> tuple[int, str]:
    print(f"[{now()}] STAGE {label}: start", flush=True)
    write_status(stage=label)
    r = subprocess.run([PY, *cmd], cwd=ROOT, env=ENV, capture_output=True, text=True)
    tail = (r.stdout or "")[-1500:] + (r.stderr or "")[-800:]
    print(f"[{now()}] STAGE {label}: exit={r.returncode}\n{tail}", flush=True)
    return r.returncode, tail


def probe_until_reset(model: str, deadline: float) -> bool:
    """Hourly tiny probe until the model's daily quota resets or deadline."""
    sys.path.insert(0, str(ROOT / "evals"))
    from evals.quota import QuotaTracker

    t = QuotaTracker()
    while time.time() < deadline:
        print(f"[{now()}] probing {model} hourly for quota reset…", flush=True)
        if t.probe(model):
            print(f"[{now()}] {model} quota reset detected", flush=True)
            return True
        time.sleep(3600)
    return False


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock() -> bool:
    RESULTS.mkdir(parents=True, exist_ok=True)
    if LOCK.is_file():
        try:
            pid = int(LOCK.read_text(encoding="utf-8").strip())
            if pid_alive(pid):
                print(f"another orchestrator is running (PID {pid}); exiting")
                return False
            print(f"removing stale lock from dead PID {pid}")
        except (ValueError, OSError):
            pass
        LOCK.unlink()
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


def cells_done(prefix: str) -> int:
    files = sorted(RESULTS.glob(f"{prefix}_*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return 0
    d = json.loads(files[-1].read_text(encoding="utf-8"))
    return sum(1 for c in d.get("cells", {}).values() if c.get("pass") is not None)


def run_cell_budget(client, quota_t, args_ns, task_ids, by_id_all, configs,
                    results, out_path, cells_budget: int) -> int:  # noqa: ANN001
    """Run at most cells_budget cells; return the count of consecutive
    non-429 failures observed (for the safety stop)."""
    from evals.run_e4 import make_call, run_cell

    consecutive_bad = 0
    for tid in task_ids:
        task = by_id_all[tid]
        for rep in range(args_ns.reps):
            for cfg_name, disabled in configs:
                cell = f"{tid}:rep{rep}:{cfg_name}"
                if cell in results["cells"]:
                    continue
                if STOP.is_file():
                    print(f"[{now()}] STOP file seen; exiting after this cell", flush=True)
                    return consecutive_bad
                model = args_ns.model
                if not quota_t.check_tpd(model, 9000):
                    print(f"[{now()}] {model} exhausted; stage will re-probe", flush=True)
                    return consecutive_bad
                call = make_call(client, model, cfg_name.split("_no_")[0], disabled)
                try:
                    rec = run_cell(call, task)
                    if rec.get("pass") is None and not rec.get("infra_aborted"):
                        consecutive_bad += 1
                    else:
                        consecutive_bad = 0
                except Exception as exc:  # noqa: BLE001
                    name = type(exc).__name__
                    rec = {"pass": None, "infra_aborted": "RateLimit" in name,
                           "error": f"{name}: {exc}"[:200]}
                    consecutive_bad = 0 if rec["infra_aborted"] else consecutive_bad + 1
                results["cells"][cell] = rec
                quota_t.record_usage(model, rec.get("tokens_in", 0) + rec.get("tokens_out", 0))
                out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
                print(f"[{now()}] {cell}: {json.dumps(rec)[:160]}", flush=True)
                if consecutive_bad >= 3:
                    write_status(
                        f"3 consecutive non-429 failures in {label_of(configs)}; "
                        f"stage halted — needs investigation", stage="halted")
                    raise SystemExit(2)
                time.sleep(args_ns.pace_s)
    return consecutive_bad


def label_of(configs):  # noqa: ANN201
    return "+".join(c for c, _ in configs)


def main() -> int:
    if not acquire_lock():
        return 3
    deadline = time.time() + 30 * 24 * 3600
    try:
        stages = build_stages()
        for stage in stages:
            name = stage["name"]
            if STOP.is_file():
                print(f"[{now()}] STOP file present; exiting cleanly", flush=True)
                break
            if stage["done"]():
                print(f"[{now()}] {name}: already complete", flush=True)
                continue
            free = quota_free(stage["model"])
            print(f"[{now()}] STAGE PLAN {name}: est {stage['est_tokens']} tok, "
                  f"{free} tok free on {stage['model']}", flush=True)
            if stage["est_tokens"] > 0 and free < stage["est_tokens"]:
                print(f"[{now()}] {name}: model exhausted; probing hourly", flush=True)
                if not probe_until_reset(stage["model"], deadline):
                    write_status(f"{name}: model quota exhausted past deadline",
                                 stage=name)
                    return 1
            code, tail = run(stage["cmd"](), name)
            if code not in (0,):
                if "stamp mismatch" in tail.lower():
                    write_status(f"{name}: stamp mismatch detected", stage=name)
                    return 2
                print(f"[{now()}] {name}: non-zero exit {code}; continuing", flush=True)
        write_status(stage="finalization done")
        print(f"[{now()}] finalize: REPORT/pareto/static report/claims", flush=True)
        run(["-m", "evals.run_e6"], "E6")
        run(["-m", "evals.make_report"], "REPORT")
        run([str(ROOT / "scripts" / "build_static_report.py")], "STATIC-REPORT")
        run(["-m", "evals.build_claims"], "RESUME-CLAIMS")
        print(f"[{now()}] eval-daily complete", flush=True)
        return 0
    finally:
        if LOCK.is_file():
            LOCK.unlink()


def build_stages() -> list[dict]:  # noqa: ANN201
    e4b = str(ROOT / "evals" / "tasks_e4b.json")
    tiny = str(ROOT / "evals" / "tasks" / "tasks_e3.json")
    return [
        {
            "name": "S1-E1E2-v2",
            "model": "openai/gpt-oss-120b",
            "est_tokens": 45000,
            "cmd": ["-m", "evals.runner", "--version", "v2", "--max-tokens", "400000",
                    "--pace-s", "45", "--output", "evals/results/e1_e2_v2_full.json"],
            "done": lambda: _wp3_done(),
        },
        {
            "name": "S2-E4b-off-balanced",
            "model": "groq/qwen/qwen3.8-27b",
            "est_tokens": 35000,
            "cmd": ["-m", "evals.run_e4", "--model", "groq/qwen/qwen3.8-27b",
                    "--experiment", "e4", "--tasks-file", e4b,
                    "--configs", "off,balanced", "--pace-s", "2"],
            "done": lambda: cells_done("e4_tasks_e4b") >= 16,
        },
        {
            "name": "S3-E4b-safe",
            "model": "groq/qwen/qwen3.8-27b",
            "est_tokens": 18000,
            "cmd": ["-m", "evals.run_e4", "--model", "groq/qwen/qwen3.8-27b",
                    "--experiment", "e4", "--tasks-file", e4b,
                    "--configs", "safe", "--pace-s", "2"],
            "done": lambda: cells_done("e4_tasks_e4b") >= 24,
        },
        {
            "name": "S3b-E5-trial",
            "model": "groq/openai/gpt-oss-20b",
            "est_tokens": 9000,
            "cmd": ["-m", "evals.e5_trial"],
            "done": lambda: (RESULTS / "e5_trial.json").is_file(),
        },
        {
            "name": "S3c-E5-ablation",
            "model": "groq/openai/gpt-oss-20b",
            "est_tokens": 28000,
            "cmd": ["-m", "evals.run_e4", "--model", "groq/openai/gpt-oss-20b",
                    "--experiment", "e5", "--tasks-file", e4b,
                    "--configs", "balanced", "--passes-from-trial", "--pace-s", "2"],
            "done": lambda: _e5_done(),
        },
        {
            "name": "S4a-E3b-specs",
            "model": "openai/gpt-oss-120b",
            "est_tokens": 40000,
            "cmd": ["-m", "evals.run_e3b", "--specs-only", "--max-tokens", "170000",
                    "--pace-s", "45"],
            "done": lambda: (RESULTS / "e3b_specs.json").is_file(),
        },
        {
            "name": "S4b-E3b-builder",
            "model": "groq/openai/gpt-oss-20b",
            "est_tokens": 28000,
            "cmd": ["-m", "evals.run_e3b", "--builder", "groq/openai/gpt-oss-20b",
                    "--specs-ready", "--max-tokens", "170000", "--pace-s", "2"],
            "done": lambda: cells_done("e3b") >= 12,
        },
        {
            "name": "S5-E4-dev-regression",
            "model": "groq/qwen/qwen3.8-27b",
            "est_tokens": 18000,
            "cmd": ["-m", "evals.run_e4", "--model", "groq/qwen/qwen3.8-27b",
                    "--experiment", "e4", "--tasks-file", tiny,
                    "--reps", "2", "--configs", "off,safe,balanced", "--pace-s", "2"],
            "done": lambda: cells_done("e4_tasks_e3") >= 48,
        },
    ]


def _wp3_done() -> bool:
    f = RESULTS / "e1_e2_v2_full.json"
    if not f.is_file():
        return False
    d = json.loads(f.read_text(encoding="utf-8"))
    return d.get("ran", 0) >= 30 and d.get("e2_micro_precision") is not None


def _e5_done() -> bool:
    files = sorted(RESULTS.glob("e5_tasks_e4b_*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return False
    d = json.loads(files[-1].read_text(encoding="utf-8"))
    cells = d.get("cells", {})
    n_cfgs = len({k.rsplit(":", 1)[1] for k in cells})
    return len(cells) >= 8 * n_cfgs and n_cfgs >= 2


if __name__ == "__main__":
    sys.exit(main())
