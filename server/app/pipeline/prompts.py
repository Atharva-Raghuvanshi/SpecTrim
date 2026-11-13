"""Prompt loading: prompts/<stage>/vN.md, version part of the input hash (FR-24/5.8)."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
VERSION_RE = re.compile(r"^v(\d+)\.md$")


@lru_cache(maxsize=None)
def latest_prompt(stage: str) -> tuple[str, str]:
    """Return (prompt_version, template) for a stage.

    Honors PROMPT_VERSION env var (e.g. 'v1' or 'v2') if set and file exists;
    otherwise defaults to the highest vN.
    """
    stage_dir = PROMPTS_DIR / stage
    if not stage_dir.is_dir():
        raise FileNotFoundError(f"no prompts for stage {stage!r}")

    forced_ver = os.environ.get("PROMPT_VERSION")
    if forced_ver:
        target = stage_dir / f"{forced_ver}.md"
        if target.is_file():
            return forced_ver, target.read_text(encoding="utf-8")

    versions = sorted(
        int(m.group(1)) for p in stage_dir.iterdir() if (m := VERSION_RE.match(p.name))
    )
    if not versions:
        raise FileNotFoundError(f"no vN.md prompts in {stage_dir}")
    version = versions[-1]
    template = (stage_dir / f"v{version}.md").read_text(encoding="utf-8")
    return f"v{version}", template


def render(stage: str, **fields: str) -> tuple[str, str]:
    """Return (prompt_version, rendered_prompt)."""
    version, template = latest_prompt(stage)
    out = template
    for key, value in fields.items():
        out = out.replace("{" + key + "}", value)
    return version, out
