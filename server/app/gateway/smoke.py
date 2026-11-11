"""One real gateway call — M1.2 exit check. Manual only; CI must never run this.

Usage: make smoke-real   (requires GROQ_API_KEY in env or .env)
"""
from __future__ import annotations

import os
import sys

from .config import load_models_config
from .litellm_gateway import LiteLLMGateway


def main() -> int:
    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY not set; no call made.", file=sys.stderr)
        return 2
    gateway = LiteLLMGateway(load_models_config())
    res = gateway.complete(
        "classifier", [{"role": "user", "content": "Reply with the single word: pong"}]
    )
    print(f"model={res.model} latency_ms={res.latency_ms}")
    print(f"usage={res.usage.model_dump()} cost_usd={res.cost_usd}")
    print(f"text={res.text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
