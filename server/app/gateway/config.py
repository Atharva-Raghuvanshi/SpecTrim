"""models.yaml loader (FR-24): stage -> model, temperature, max_tokens, fallbacks."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

DEFAULT_PATH = "config/models.yaml"


class ConfigError(RuntimeError):
    pass


class StageModelCfg(BaseModel):
    model: str
    temperature: float = 0.2
    max_tokens: int = 4096
    fallback_model: str | None = None


class GatewayConfig(BaseModel):
    default: StageModelCfg
    stages: dict[str, StageModelCfg] = Field(default_factory=dict)

    def for_stage(self, stage: str) -> StageModelCfg:
        return self.stages.get(stage) or self.default


def load_models_config(
    path: str | Path | None = None, active: str | None = None
) -> GatewayConfig:
    p = Path(path or os.environ.get("MODELS_CONFIG", DEFAULT_PATH))
    if not p.is_file():
        raise ConfigError(f"models config not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    configs = data.get("configs") or {}
    name = active or os.environ.get("ACTIVE_CONFIG") or data.get("active")
    if not name or name not in configs:
        raise ConfigError(f"active config {name!r} not in {sorted(configs)}")
    try:
        return GatewayConfig.model_validate(configs[name])
    except ValidationError as exc:
        raise ConfigError(f"invalid config {name!r}: {exc}") from exc
