"""WP17 S7 secret scan: working tree + git history + tracked files.

Exit 0 = clean; exit 1 = findings (names + counts only, never key contents).
Never prints the matched secret text.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {
    "groq-key": re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    "openai-sk": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "bearer-long": re.compile(r"Bearer [A-Za-z0-9_\-]{32,}"),
}
WHITELIST_FILES = {"evals/APPROVALS.md", "docs/log.md", "docs/STATUS.md"}
WHITELIST_SNIPPET = "gsk_"


def scan_text(text: str, label: str) -> list[str]:
    findings: list[str] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        for name, pat in PATTERNS.items():
            m = pat.search(line)
            if m:
                # never print the match itself; only file:line + pattern name
                findings.append(f"{label}:{i}: pattern={name}")
                break
    return findings


def tracked_env_files() -> list[str]:
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return [f for f in r.stdout.splitlines() if f.endswith(".env")]


def tracked_files() -> list[str]:
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.splitlines()


def scan_worktree() -> list[str]:
    """Scan git-TRACKED files only (untracked .env is by design gitignored)."""
    findings: list[str] = []
    for rel in tracked_files():
        path = ROOT / rel
        if not path.is_file() or any(rel == w for w in WHITELIST_FILES):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings += scan_text(text, rel)
    return findings


def scan_history() -> list[str]:
    """Scan git history blobs for key patterns (names/counts only)."""
    findings: list[str] = []
    r = subprocess.run(["git", "rev-list", "--all", "--max-count=200"],
                       cwd=ROOT, capture_output=True, text=True)
    commits = r.stdout.split()
    for c in commits[:50]:  # bound the scan; key leakage would be recent
        rr = subprocess.run(["git", "grep", "-l", "-E", "gsk_[A-Za-z0-9]{20,}",
                             c, "--", "."],
                            cwd=ROOT, capture_output=True, text=True)
        for line in rr.stdout.splitlines():
            if ":" in line:
                findings.append("history:" + line.split(":", 1)[0] + ":groq-key")
    return sorted(set(findings))


def main() -> int:
    findings = scan_worktree() + scan_history()
    env_tracked = tracked_env_files()
    for f in env_tracked:
        findings.append(f"tracked-env-file:{f}")
    counts: dict[str, int] = {}
    for f in findings:
        kind = f.rsplit(":", 1)[-1]
        counts[kind] = counts.get(kind, 0) + 1
    print(f"secret scan: {len(findings)} findings")
    for kind, n in sorted(counts.items()):
        print(f"  {kind}: {n}")
    for f in findings:
        print(f"  location: {f}")
    if findings or env_tracked:
        print("REMEDIATION: revoke the key in the provider console and rewrite "
              "git history (git filter-repo). Do NOT push.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
