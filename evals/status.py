"""`make eval-status`: current stage, cells done/total, tokens per model today,
last 5 result files, orchestrator PID liveness, docs/STATUS.md contents."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evals" / "results"
LOCK = RESULTS / "eval-daily.lock"
STOP = RESULTS / "STOP"
STATUS_MD = ROOT / "docs" / "STATUS.md"

STAGES = [
    ("S1", "E1/E2 v2 remainder", 30),
    ("S2", "E4b primary off+balanced", 16),
    ("S2b", "E4b primary safe", 24),
    ("S3b", "E5 trial + ablation on e4b", 0),
    ("S4a", "E3b specs", 3),
    ("S4b", "E3b builder cells", 12),
    ("S5", "E4 tiny dev/regression", 48),
]


def done_in(prefix: str) -> int:
    files = sorted(
        (f for f in RESULTS.glob(f"{prefix}_*.json") if "diagnosis" not in f.name),
        key=lambda p: p.stat().st_mtime,
    )
    if not files:
        return 0
    d = json.loads(files[-1].read_text(encoding="utf-8"))
    return sum(1 for c in d.get("cells", {}).values() if c.get("pass") is not None)


def main() -> int:
    print("== eval-daily status ==")
    pid = None
    if LOCK.is_file():
        try:
            pid = int(LOCK.read_text(encoding="utf-8").strip())
        except ValueError:
            pass
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    print(f"orchestrator: {'RUNNING pid=' + str(pid) if alive else 'not running'}")
    print(f"STOP file: {'present' if STOP.is_file() else 'absent'}")

    print("stages:")
    for sid, name, total in STAGES:
        prefix = {
            "S1": "e1_e2_v2_full", "S2": "e4_tasks_e4b", "S2b": "e4_tasks_e4b",
            "S3b": "e5_tasks_e4b", "S4a": "e3b_specs", "S4b": "e3b",
            "S5": "e4_tasks_e3",
        }[sid]
        n = done_in(prefix)
        print(f"  {sid} {name}: {n}/{total} cells" if total else
              f"  {sid} {name}: {n} cells")

    qf = RESULTS / "quota.json"
    if qf.is_file():
        q = json.loads(qf.read_text(encoding="utf-8"))
        print(f"tokens used today ({q.get('date_utc')}):")
        for m, v in sorted(q.get("models", {}).items()):
            print(f"  {m}: {v.get('tokens_today')} tok, {v.get('requests_today')} req")

    files = sorted(
        (f for f in RESULTS.glob("*.json") if "quota" not in f.name and "audit" not in f.name),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )[:5]
    print("last 5 result files:")
    for f in files:
        ts = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).strftime("%m-%d %H:%M")
        print(f"  {f.name} ({ts}Z)")

    if STATUS_MD.is_file():
        print("docs/STATUS.md:")
        for line in STATUS_MD.read_text(encoding="utf-8").splitlines():
            print(f"  {line}")
    else:
        print("docs/STATUS.md: absent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
