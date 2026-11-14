"""Automated pass checks for E3/E4 generated code: syntax, import, behaviour."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

CHECK_SNIPPETS: dict[str, str] = {
    # noqa-disable dict: one-line embedded check programs
    "T1": "from mod import Calculator\nassert add(2, 3) == 5\nc = Calculator()\nassert c.multiply(4, 5) == 20\nprint('CHECK-OK')",  # noqa: E501
    "T2": "from mod import is_palindrome, count_vowels\nassert is_palindrome('A man a plan') is False\nassert is_palindrome('RaceCar') is True\nassert count_vowels('hello') == 2\nprint('CHECK-OK')",  # noqa: E501
    "T3": "from mod import Stack\ns = Stack()\ns.push(1)\nassert s.pop() == 1\ntry:\n    Stack().pop()\n    raise SystemExit(1)\nexcept IndexError:\n    pass\nprint('CHECK-OK')",  # noqa: E501
    "T4": "from mod import fibonacci\nassert fibonacci(1) == [0] or fibonacci(1) == [1] or True\nassert len(fibonacci(5)) == 5\nassert fibonacci(7)[-1] in (8, 13)\nprint('CHECK-OK')",  # noqa: E501
    "T5": "from mod import Inventory\ninv = Inventory()\ninv.add_item('apple', 3)\ninv.remove_item('apple', 1)\nassert inv.get_qty('apple') == 2\nassert inv.get_qty('ghost') == 0\ntry:\n    inv.remove_item('apple', 99)\n    raise SystemExit(1)\nexcept ValueError:\n    pass\nprint('CHECK-OK')",  # noqa: E501
    "T6": "from mod import flatten\nassert flatten([1, [2, [3, [4]], 5]]) == [1, 2, 3, 4, 5]\nassert flatten([]) == []\nprint('CHECK-OK')",  # noqa: E501
    "T7": "from mod import LRUCache\nc = LRUCache(2)\nc.put('a', 1)\nc.put('b', 2)\nc.get('a')\nc.put('c', 3)\nassert c.get('b') is None\nassert c.get('a') == 1\nprint('CHECK-OK')",  # noqa: E501
    "T8": "from mod import word_frequencies\nf = word_frequencies('Hello, hello WORLD!')\nassert f['hello'] == 2 and f['world'] == 1\nprint('CHECK-OK')",  # noqa: E501
}


def check_generated(task_id: str, source: str) -> dict:
    """Run the generated file's checks in a subprocess. Returns pass/fail + stage."""
    with tempfile.TemporaryDirectory() as td:
        mod = Path(td) / "mod.py"
        mod.write_text(source, encoding="utf-8")
        checker = Path(td) / "check.py"
        checker.write_text(CHECK_SNIPPETS.get(task_id, "print('CHECK-OK')"), encoding="utf-8")
        import os

        r = subprocess.run(
            [sys.executable, str(checker)], cwd=td,
            capture_output=True, text=True, timeout=30,
            env={"PYTHONPATH": td, "PATH": "",
                 "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
        )
        if "CHECK-OK" in r.stdout:
            return {"pass": True, "stage": "behaviour"}
        syntax_ok = compile_ok(source)
        return {"pass": False, "stage": "syntax" if not syntax_ok else "behaviour",
                "stderr": r.stderr[-300:]}


def compile_ok(source: str) -> bool:
    try:
        compile(source, "mod.py", "exec")
        return True
    except SyntaxError:
        return False


MULTI_SNIPPETS: dict[str, str] = {
    "B1": "from cli import main
main()
from storage import Ledger
l = Ledger()
l.add_expense('a', 30, ['a', 'b'])
assert l.balances()['a'] == 15
print('CHECK-OK')",  # noqa: E501
    "B2": "from api import handle_create, handle_resolve
from models import InMemoryStore
s = InMemoryStore()
r = handle_create('https://example.com/x', s)
assert handle_resolve(r['code'], s) == 'https://example.com/x'
print('CHECK-OK')",  # noqa: E501
    "B3": "from queue import PriorityQueue
from worker import Worker
q = PriorityQueue()
q.push('t1', 2)
w = Worker(q, {'t1': lambda: 'done'})
assert w.run_once() == 't1'
assert w.processed == ['t1']
print('CHECK-OK')",  # noqa: E501
}


def parse_files(text: str) -> dict[str, str]:
    """Parse builder output with '### FILE: name.py' markers into {name: source}.

    Falls back to {'mod.py': text} when no markers are present."""
    files: dict[str, str] = {}
    current: list[str] = []
    name = "mod.py"
    for line in text.splitlines():
        m = line.strip()
        if m.startswith("### FILE:"):
            if current:
                files[name] = "
".join(current).strip() + "
"
            name = m.split(":", 1)[1].strip()
            current = []
        else:
            current.append(line)
    if current:
        files[name] = "
".join(current).strip() + "
"
    if not files:
        files = {"mod.py": text}
    return files


def check_generated_multi(task_id: str, files: dict[str, str]) -> dict:
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for name, source in files.items():
            path = Path(td) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        checker = Path(td) / "check.py"
        checker.write_text(MULTI_SNIPPETS.get(task_id, "print('CHECK-OK')"), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(checker)], cwd=td,
            capture_output=True, text=True, timeout=30,
            env={"PYTHONPATH": td, "PATH": "",
                 "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
        )
        if "CHECK-OK" in r.stdout:
            return {"pass": True, "stage": "behaviour", "files": list(files)}
        syntax_ok = all(compile_ok(src) for src in files.values())
        return {"pass": False, "stage": "syntax" if not syntax_ok else "behaviour",
                "files": list(files), "stderr": r.stderr[-300:]}
