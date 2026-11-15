"""WP17 orchestrator: unattended, staged, quota-aware, deadline-bounded.

Stages (EVAL_STAGES env selects; default 'S1,S2p,S6,S7'):
  S1   E1/E2 v2 remaining prompts (gpt-oss-120b), while under 85% TPD
  S2   full E4b off/balanced (qwen)          - disabled by default
  S2b  E4b safe arm (qwen)                   - disabled by default
  S2p  E4b PILOT: L1+L3, 1 rep, off+balanced = 4 cells, hard cap 150K tok
  S3b  E5 ablation (20b)                     - disabled by default
  S4a  E3b spec generation (120b)            - disabled by default
  S4b  E3b builder cells (20b)               - disabled by default
  S5   E4 tiny dev/regression, max 2 reps    - disabled by default
  S6   finalization (REPORT, pareto, static, claims; no LLM calls)
  S7   verify (test/lint/coverage), secret scan, commit generated files,
       tag v1.0-portfolio, push if origin exists, COMPLETE in STATUS.md

EVAL_DEADLINE_HOURS (default 3): at the deadline, or when S1+S2p are done,
the orchestrator jumps to S6 -> S7. One experiment on one model at a time;
never edits code, prompts, labels or tasks. Survives shell exit (nohup).
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

DEADLINE_HOURS = float(os.environ.get("EVAL_DEADLINE_HOURS", "3"))


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def selected_stages() -> list[str]:
    raw = os.environ.get("EVAL_STAGES", "S1,S2p,S6,S7")
    return [t.strip() for t in raw.split(",") if t.strip()]


def normalize_key(model: str) -> str:
    m = model.strip()
    return m[len("groq/"):] if m.startswith("groq/") else m


def used_today(model: str) -> int:
    qf = RESULTS / "quota.json"
    if not qf.is_file():
        return 0
    data = json.loads(qf.read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if data.get("date_utc") != today:
        return 0
    return data.get("models", {}).get(normalize_key(model), {}).get("tokens_today", 0)


def headroom(model: str) -> int:
    return max(0, 170000 - used_today(model))


def write_status(blocker: str | None = None, stage: str = "",
                 complete: bool = False) -> None:
    lines = [f"# STATUS (updated {now()})", "", f"- current stage: {stage or 'none'}"]
    if complete:
        lines.append("- COMPLETE")
    lines.append(f"- **BLOCKER: {blocker}**" if blocker else "- no blockers")
    STATUS_MD.parent.mkdir(parents=True, exist_ok=True)
    STATUS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(cmd: list[str], label: str) -> tuple[int, str]:
    print(f"[{now()}] STAGE {label}: start", flush=True)
    r = subprocess.run([PY, *cmd], cwd=ROOT, env=ENV, capture_output=True, text=True)
    tail = (r.stdout or "")[-1500:] + (r.stderr or "")[-800:]
    print(f"[{now()}] STAGE {label}: exit={r.returncode}\n{tail}", flush=True)
    return r.returncode, tail


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
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
    files = sorted(
        (f for f in RESULTS.glob(f"{prefix}*.json") if "diagnosis" not in f.name),
        key=lambda p: p.stat().st_mtime,
    )
    if not files:
        return 0
    d = json.loads(files[-1].read_text(encoding="utf-8"))
    if "per_prompt" in d:
        return sum(1 for p in d["per_prompt"]
                   if p.get("valid") is not None and not p.get("infra_aborted"))
    return sum(1 for c in d.get("cells", {}).values() if c.get("pass") is not None)


def wp3_done() -> bool:
    f = RESULTS / "e1_e2_v2_full.json"
    if not f.is_file():
        return False
    d = json.loads(f.read_text(encoding="utf-8"))
    return d.get("ran", 0) >= 30 and d.get("e2_micro_precision") is not None


def pilot_done() -> bool:
    files = sorted(RESULTS.glob("e4_pilot_*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return False
    d = json.loads(files[0].read_text(encoding="utf-8"))
    cells = d.get("cells", {})
    return len(cells) >= 4 and all(c.get("pass") is not None for c in cells.values())


def pick_pilot_model() -> str | None:
    candidates = ["groq/qwen/qwen3.8-27b", "groq/openai/gpt-oss-20b",
                  "groq/openai/gpt-oss-120b"]
    best, best_free = None, -1
    for m in candidates:
        free = headroom(m)
        if free >= 20000 and free > best_free:
            best, best_free = m, free
    return best


def run_s2p() -> None:
    model = pick_pilot_model()
    if model is None:
        print(f"[{now()}] S2p: no candidate model has 20K tok headroom; skipping",
              flush=True)
        write_status(stage="S2p",
                     blocker="no model with 20K headroom for pilot; will retry")
        return
    print(f"[{now()}] S2p pilot model: {model} "
          f"(headroom {headroom(model)} tok, hard cap {150000})", flush=True)
    r = subprocess.run(
        [PY, "-m", "evals.run_e4", "--model", model, "--experiment", "e4",
         "--pilot", "--task-ids", "L1,L3", "--reps", "1",
         "--configs", "off,balanced", "--pace-s", "2",
         "--output", f"evals/results/e4_pilot_{model.replace('/', '_')}.json"],
        cwd=ROOT, env=ENV, capture_output=True, text=True)
    print(f"[{now()}] S2p exit={r.returncode}\n"
          f"{(r.stdout or '')[-1200:]}{(r.stderr or '')[-600:]}", flush=True)


def run_s6() -> None:
    print(f"[{now()}] S6 finalization", flush=True)
    for cmd, label in (
        (["-m", "evals.run_e6"], "E6-pareto"),
        (["-m", "evals.make_report"], "REPORT"),
        (["-m", "evals.build_claims"], "RESUME-CLAIMS"),
        ([str(ROOT / "scripts" / "build_static_report.py")], "STATIC-REPORT"),
    ):
        run(cmd, label)


def run_s7() -> None:
    print(f"[{now()}] S7 verify + tag", flush=True)
    ok = True
    for label, args in (("make test", ["test"]), ("make lint", ["lint"]),
                        ("make coverage", ["coverage"])):
        r = subprocess.run(["make", *args], cwd=ROOT, env=ENV,
                           capture_output=True, text=True)
        print(f"[{now()}] S7 {label}: exit={r.returncode} "
              f"{(r.stdout or '')[-300:]}", flush=True)
        if r.returncode != 0:
            ok = False
    scan = subprocess.run(
        [PY, str(ROOT / "scripts" / "secret_scan.py")], cwd=ROOT, env=ENV,
        capture_output=True, text=True)
    print(f"[{now()}] S7 secret scan: exit={scan.returncode}\n"
          f"{scan.stdout[-600:]}", flush=True)
    if scan.returncode != 0:
        write_status(stage="S7",
                     blocker="SECRET SCAN FAILED - do not push; revoke the key in "
                             "the provider console and rewrite history")
        return
    if not ok:
        write_status(stage="S7", blocker="make test/lint/coverage failed; no tag")
        return
    gen_files: list[str] = []
    for pat in ("evals/results/*.json", "evals/REPORT.md", "docs/RESUME_CLAIMS.md",
                "docs/report.html", "docs/log.md", "docs/STATUS.md"):
        gen_files += [str(p) for p in ROOT.glob(pat)]
    subprocess.run(["git", "add", *gen_files], cwd=ROOT, env=ENV, capture_output=True)
    c = subprocess.run(["git", "commit", "-q", "-m", "eval: final results (WP17)"],
                       cwd=ROOT, env=ENV, capture_output=True, text=True)
    print(f"[{now()}] S7 commit: exit={c.returncode}", flush=True)
    tag = subprocess.run(["git", "tag", "v1.0-portfolio"], cwd=ROOT, env=ENV,
                         capture_output=True, text=True)
    if tag.returncode != 0:
        print(f"[{now()}] tag v1.0-portfolio already exists (ok)", flush=True)
    rem = subprocess.run(["git", "remote"], cwd=ROOT, env=ENV,
                         capture_output=True, text=True)
    if "origin" in rem.stdout.split():
        push = subprocess.run(["git", "push", "origin", "HEAD", "--tags"],
                              cwd=ROOT, env=ENV, capture_output=True, text=True)
        print(f"[{now()}] push: exit={push.returncode}", flush=True)
    else:
        print(f"[{now()}] no git remote 'origin'; push manually with "
              f"`git push origin HEAD --tags`", flush=True)
        write_status(stage="S7", complete=True)
        STATUS_MD.write_text(
            STATUS_MD.read_text(encoding="utf-8") +
            "\n- no git remote 'origin': push manually with `git push origin HEAD --tags`\n",
            encoding="utf-8")
        return
    write_status(stage="S7", complete=True)
    print(f"[{now()}] COMPLETE", flush=True)


STAGES = [
    {"sid": "S1", "name": "S1-E1E2-v2", "model": "openai/gpt-oss-120b",
     "est_tokens": 45000,
     "cmd": lambda: ["-m", "evals.runner", "--version", "v2", "--max-tokens", "400000",
                     "--pace-s", "45", "--output", "evals/results/e1_e2_v2_full.json"],
     "done": wp3_done},
    {"sid": "S2p", "name": "S2p-E4b-pilot", "model": None, "est_tokens": 20000,
     "cmd": None, "done": pilot_done},
    {"sid": "S2", "name": "S2-E4b-full-off-balanced", "model": "groq/qwen/qwen3.8-27b",
     "est_tokens": 35000,
     "cmd": lambda: ["-m", "evals.run_e4", "--model", "groq/qwen/qwen3.8-27b",
                     "--experiment", "e4", "--tasks-file",
                     str(ROOT / "evals" / "tasks_e4b.json"),
                     "--configs", "off,balanced", "--pace-s", "2"],
     "done": lambda: cells_done("e4_tasks_e4b") >= 16},
    {"sid": "S2b", "name": "S2b-E4b-safe", "model": "groq/qwen/qwen3.8-27b",
     "est_tokens": 18000,
     "cmd": lambda: ["-m", "evals.run_e4", "--model", "groq/qwen/qwen3.8-27b",
                     "--experiment", "e4", "--tasks-file",
                     str(ROOT / "evals" / "tasks_e4b.json"),
                     "--configs", "safe", "--pace-s", "2"],
     "done": lambda: cells_done("e4_tasks_e4b") >= 24},
    {"sid": "S3b", "name": "S3b-E5-ablation", "model": "groq/openai/gpt-oss-20b",
     "est_tokens": 28000,
     "cmd": lambda: ["-m", "evals.run_e4", "--model", "groq/openai/gpt-oss-20b",
                     "--experiment", "e5", "--tasks-file",
                     str(ROOT / "evals" / "tasks_e4b.json"),
                     "--configs", "balanced", "--passes-from-trial", "--pace-s", "2"],
     "done": lambda: cells_done("e5_tasks_e4b") >= 16},
    {"sid": "S4a", "name": "S4a-E3b-specs", "model": "openai/gpt-oss-120b",
     "est_tokens": 40000,
     "cmd": lambda: ["-m", "evals.run_e3b", "--specs-only", "--max-tokens", "170000",
                     "--pace-s", "45"],
     "done": lambda: (RESULTS / "e3b_specs.json").is_file()},
    {"sid": "S4b", "name": "S4b-E3b-builder", "model": "groq/openai/gpt-oss-20b",
     "est_tokens": 28000,
     "cmd": lambda: ["-m", "evals.run_e3b", "--builder", "groq/openai/gpt-oss-20b",
                     "--specs-ready", "--max-tokens", "170000", "--pace-s", "2"],
     "done": lambda: cells_done("e3b") >= 12},
    {"sid": "S5", "name": "S5-E4-tiny-dev-regression", "model": "groq/qwen/qwen3.8-27b",
     "est_tokens": 18000,
     "cmd": lambda: ["-m", "evals.run_e4", "--model", "groq/qwen/qwen3.8-27b",
                     "--experiment", "e4", "--tasks-file",
                     str(ROOT / "evals" / "tasks" / "tasks_e3.json"),
                     "--reps", "2", "--configs", "off,safe,balanced", "--pace-s", "2"],
     "done": lambda: cells_done("e4_tasks_e3") >= 48},
    {"sid": "S6", "name": "S6-finalize", "model": "none", "est_tokens": 0,
     "cmd": None, "done": lambda: False},
    {"sid": "S7", "name": "S7-verify-tag", "model": "none", "est_tokens": 0,
     "cmd": None, "done": lambda: False},
]


def all_done(selected: list[str]) -> bool:
    checks = {s["sid"]: s["done"] for s in STAGES}
    return all(checks[sid]() for sid in selected if sid not in ("S6", "S7"))


def main() -> int:
    if not acquire_lock():
        return 3
    deadline = time.time() + DEADLINE_HOURS * 3600
    selected = selected_stages()
    try:
        while True:
            acted = False
            deadline_hit = time.time() >= deadline
            for stage in STAGES:
                sid, name = stage["sid"], stage["name"]
                if sid not in selected:
                    print(f"[{now()}] {name}: DISABLED (not in EVAL_STAGES)", flush=True)
                    continue
                if sid == "S6":
                    run_s6()
                    continue
                if sid == "S7":
                    run_s7()
                    if STOP.is_file():
                        STOP.unlink(missing_ok=True)
                    return 0
                if STOP.is_file():
                    print(f"[{now()}] STOP file present; exiting cleanly", flush=True)
                    return 0
                if stage["done"]():
                    print(f"[{now()}] {name}: already complete", flush=True)
                    continue
                if deadline_hit:
                    print(f"[{now()}] deadline hit; skipping {name} to finalization",
                          flush=True)
                    continue
                if sid == "S2p":
                    run_s2p()
                    acted = True
                    continue
                model = stage["model"]
                free = headroom(model)
                print(f"[{now()}] STAGE PLAN {name}: est {stage['est_tokens']} tok, "
                      f"{free} tok free on {model}", flush=True)
                if free < stage["est_tokens"]:
                    print(f"[{now()}] {model} lacks headroom; probing hourly", flush=True)
                    sys.path.insert(0, str(ROOT / "evals"))
                    from evals.quota import QuotaTracker

                    qt = QuotaTracker()
                    qt.mark_daily_exhausted(model)
                    probe_until = min(time.time() + 3600, deadline)
                    while time.time() < probe_until:
                        if STOP.is_file():
                            print(f"[{now()}] STOP file seen; exiting cleanly", flush=True)
                            return 0
                        if qt.probe(model):
                            print(f"[{now()}] {model} quota reset detected", flush=True)
                            break
                        time.sleep(3600)
                    continue
                code, _ = run(stage["cmd"](), name)
                acted = True
                if code == 2:
                    write_status(stage=name,
                                 blocker="safety stop: 3 consecutive non-429 failures")
                    return 2
            if all_done(selected):
                run_s6()
                run_s7()
                return 0
            if not acted:
                print(f"[{now()}] nothing actionable; sleeping 10 min", flush=True)
                time.sleep(600)
    finally:
        if LOCK.is_file():
            LOCK.unlink()


if __name__ == "__main__":
    sys.exit(main())
