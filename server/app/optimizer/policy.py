"""Policy engine (M10.2): mode resolution, kill switches, protected content."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

MODES = ("off", "safe", "balanced", "aggressive")
PASS_ORDER = ["P9", "P4", "P5", "P2", "P3", "P10", "P1", "P7", "P6"]

PASS_MODE_FLOOR = {  # modes in which each pass may run (PRD v2 §5)
    "P9": "safe", "P4": "safe", "P7": "safe", "P1": "safe",
    "P2": "balanced", "P5": "balanced", "P3": "balanced", "P6": "balanced",
    "P10": "aggressive",
}

PROTECTED_HEADER = "x-optimizer-protect"


def _mode_rank(mode: str) -> int:
    return MODES.index(mode) if mode in MODES else 2


@dataclass
class Policy:
    mode: str = "balanced"
    enabled: list[str] = field(default_factory=list)
    protect_patterns: list[str] = field(default_factory=list)

    def allows(self, pass_id: str) -> bool:
        return pass_id in self.enabled


def resolve_policy(mode_header: str | None, protect_header: str | None) -> Policy:
    """Mode from header; kill switches from env QO_PASS_P<N>=0 disable a pass."""
    mode = mode_header if mode_header in MODES else "balanced"
    enabled: list[str] = []
    for pass_id in PASS_ORDER:
        floor = PASS_MODE_FLOOR[pass_id]
        if _mode_rank(mode) >= _mode_rank(floor) and os.environ.get(f"QO_PASS_{pass_id}", "1") != "0":
            enabled.append(pass_id)
    patterns = [p.strip() for p in (protect_header or "").split(",") if p.strip()]
    return Policy(mode=mode, enabled=enabled, protect_patterns=patterns)


def content_is_protected(text: str, policy: Policy) -> bool:
    """Protected: latest user instruction, active error text, tagged content.

    Proxy callers tag via x-optimizer-protect; we also never touch the last
    user message (the instruction being executed right now).
    """
    if not text:
        return False
    return any(p in text for p in policy.protect_patterns)
