"""WP17 STEP 5: markdown link check — verifies relative file links in README.md,
docs/RESUME_CLAIMS.md and docs/DEPLOY.md resolve to files on disk. Exit 1 on any
broken link. Does not fetch external URLs."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ["README.md", "docs/RESUME_CLAIMS.md", "docs/DEPLOY.md", "evals/REPORT.md"]
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)#\s]+)(?:#[^)]*)?\)")


def main() -> int:
    broken: list[str] = []
    checked = 0
    for name in FILES:
        p = ROOT / name
        if not p.is_file():
            broken.append(f"missing file: {name}")
            continue
        for m in LINK_RE.finditer(p.read_text(encoding="utf-8")):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            checked += 1
            resolved = (p.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{name}: broken link -> {target}")
    print(f"link check: {checked} relative links checked")
    for b in broken:
        print(f"  BROKEN: {b}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
