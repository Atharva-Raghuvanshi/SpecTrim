"""Fixed builder agent for E3/E4: a single prompted coding agent through the
gateway. Produces one small self-contained Python file per task.

- E3 arm "raw": the task prompt only.
- E3 arm "optimized": the task prompt plus the spec package context.
- E4 modes: the same agent loop driven through the proxy with x-optimizer-mode.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))

from app import winloop  # noqa: E402,F401
from app.gateway.config import load_models_config  # noqa: E402
from app.gateway.litellm_gateway import LiteLLMGateway  # noqa: E402
from app.settings import get_settings  # noqa: E402

BUILD_INSTRUCTION = (
    "You are a coding agent. Write ONE complete, self-contained Python file that "
    "implements the task. No explanations, no markdown fences — output pure Python only. "
    "The file must define the required classes/functions and include a small "
    "`if __name__ == '__main__':` demo guard where sensible."
)


def build_once(
    gateway: LiteLLMGateway, task_prompt: str, spec_context: str | None
) -> tuple[str, object]:
    """One builder call. Returns (generated Python source, CompletionResult)."""
    content = (
        task_prompt if not spec_context
        else f"{task_prompt}\n\nSPECIFICATION TO FOLLOW:\n{spec_context}"
    )
    res = gateway.complete("spec_writer", [
        {"role": "system", "content": BUILD_INSTRUCTION},
        {"role": "user", "content": content},
    ])
    text = res.text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("python"):
            text = text[6:]
    return text, res


def make_gateway_from_env() -> LiteLLMGateway:
    settings = get_settings()
    return LiteLLMGateway(
        load_models_config(settings.models_config, settings.active_config),
        max_retries=6, base_delay=2.0,
    )
