"""WP13 sanity check for tasks_e4b: enables the primary benchmark by default.

Checks (all must pass):
1. Known-good solutions PASS check_generated_multi for every task.
2. Known-bad solutions FAIL.
3. Worst-case request size stays under 6K tokens in every mode (the runner
   keeps only the latest tool result full-sized; P4 may shrink it further).

Writes evals/results/e4b_sanity.json. auto_finish gates the E4b phase on it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from checks import check_generated_multi  # noqa: E402

TASKS = Path(__file__).resolve().parent / "tasks" / "tasks_e4b.json"
RESULTS = Path(__file__).resolve().parent / "results"

# Hand-written known-good solutions (small, correct implementations).
GOOD: dict[str, dict[str, str]] = {
    "L1": {
        "storage.py": (
            "class Ledger:\n"
            "    def __init__(self):\n"
            "        self.expenses = []\n"
            "    def add_expense(self, payer, amount, participants):\n"
            "        self.expenses.append((payer, amount, participants))\n"
            "    def balances(self):\n"
            "        b = {}\n"
            "        for payer, amount, participants in self.expenses:\n"
            "            share = amount / len(participants)\n"
            "            for p in participants:\n"
            "                b[p] = b.get(p, 0.0) - share\n"
            "            b[payer] = b.get(payer, 0.0) + amount\n"
            "        return {k: round(v, 2) for k, v in b.items()}\n"
        ),
        "cli.py": (
            "from storage import Ledger\n"
            "def main():\n"
            "    l = Ledger()\n"
            "    l.add_expense('a', 30, ['a', 'b'])\n"
            "    print(l.balances())\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
    },
    "L2": {
        "models.py": (
            "from dataclasses import dataclass\n"
            "@dataclass\nclass ShortLink:\n"
            "    code: str\n"
            "    url: str\n"
            "class InMemoryStore:\n"
            "    def __init__(self):\n"
            "        self.links = {}\n"
            "    def create(self, url):\n"
            "        code = 'c' + str(len(self.links) + 1)\n"
            "        self.links[code] = ShortLink(code, url)\n"
            "        return code\n"
            "    def resolve(self, code):\n"
            "        link = self.links.get(code)\n"
            "        return link.url if link else None\n"
        ),
        "api.py": (
            "from models import InMemoryStore\n"
            "def handle_create(url, store):\n"
            "    if not isinstance(url, str) or not url.startswith(('http://', 'https://')):\n"
            "        raise ValueError('bad url')\n"
            "    return {'code': store.create(url)}\n"
            "def handle_resolve(code, store):\n"
            "    return store.resolve(code)\n"
        ),
    },
    "L3": {
        "queue.py": (
            "class PriorityQueue:\n"
            "    def __init__(self):\n"
            "        self.items = []\n"
            "    def push(self, task_id, priority):\n"
            "        self.items.append((priority, task_id))\n"
            "    def pop(self):\n"
            "        if not self.items:\n"
            "            raise KeyError('empty')\n"
            "        self.items.sort()\n"
            "        return self.items.pop(0)[1]\n"
        ),
        "worker.py": (
            "from queue import PriorityQueue\n"
            "class Worker:\n"
            "    def __init__(self, q, handlers):\n"
            "        self.q = q\n"
            "        self.handlers = handlers\n"
            "        self.processed = []\n"
            "    def run_once(self):\n"
            "        task_id = self.q.pop()\n"
            "        self.processed.append(task_id)\n"
            "        return task_id\n"
        ),
    },
    "L4": {
        "deploy.py": (
            "def run_deploy(env):\n"
            "    return 'deployed ' + env\n"
        ),
        "main.py": (
            "from deploy import run_deploy\n"
        ),
    },
}

BAD = {  # known-bad: syntax error (L1), wrong behaviour (L2, L3)
    "L1": {"storage.py": "class Ledger:\n    def balances(self):\n    return {}\n",
           "cli.py": "from storage import Ledger\n"},
    "L2": {"models.py": "class InMemoryStore:\n    def resolve(self, code):\n        return 'wrong'\n",
           "api.py": "from models import InMemoryStore\n"},
    "L3": {"queue.py": "class PriorityQueue:\n    def pop(self):\n        return None\n",
           "worker.py": "from queue import PriorityQueue\n"},\
    "L4": {"deploy.py": "def run_deploy(env):\n    raise RuntimeError('boom')\n",\
           "main.py": "from deploy import run_deploy\n"},
}

# tasks_e4b has 4 tasks; B-task ids map L1-L3 to checks B1-B3 style snippets.
CHECK_IDS = {"L1": "B1", "L2": "B2", "L3": "B3", "L4": "L4"}


def token_est(text: str) -> int:
    return max(1, len(text) // 4)


def main() -> int:
    tasks = json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    checks: list[str] = []
    ok = True
    for t in tasks:
        tid = t["id"]
        check_id = CHECK_IDS.get(tid, "B1")
        good_files = GOOD.get(tid)
        bad_files = BAD.get(tid)
        if good_files is None or bad_files is None:
            checks.append(f"{tid}: MISSING fixture")
            ok = False
            continue
        good = check_generated_multi(check_id, good_files)
        bad = check_generated_multi(check_id, bad_files)
        checks.append(f"{tid}: known-good pass={good['pass']} (files={good.get('files')}), "
                      f"known-bad pass={bad['pass']}")
        if not good["pass"]:
            ok = False
        if bad["pass"]:
            checks.append(f"{tid}: known-bad unexpectedly PASSED")
            ok = False

        # token budget: worst case request = system + prompt + assistant turns
        # (~150 tok/turn) + the latest full tool log + P7 style line.
        log_tokens = token_est("x" * (t.get("tool_log_lines", 800) * 15))
        prompt_tokens = token_est(t["prompt"])
        turns = 8
        assistant_tokens = 150 * turns
        off_request = 100 + prompt_tokens + assistant_tokens + log_tokens + 30
        checks.append(f"{tid}: worst-case off-mode request ~= {off_request} tokens "
                      f"({'OK' if off_request < 6000 else 'OVER 6K'})")
        if off_request >= 6000:
            ok = False

    out = {"ok": ok, "checks": checks, "version": "e4b-v1"}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "e4b_sanity.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
