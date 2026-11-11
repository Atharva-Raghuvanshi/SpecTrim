from pathlib import Path

import pytest
from app.gateway.config import ConfigError, GatewayConfig, StageModelCfg, load_models_config

REPO = Path(__file__).resolve().parents[2]
REAL_CONFIG = REPO / "config" / "models.yaml"

EXPECTED_STAGES = {
    "analyzer",
    "classifier",
    "question_gate",
    "spec_writer",
    "decomposer",
    "contract",
    "revision",
}


def test_real_models_yaml_loads_with_all_stages_pinned() -> None:
    cfg = load_models_config(REAL_CONFIG, active="single_model")
    assert EXPECTED_STAGES <= set(cfg.stages)
    for stage_cfg in cfg.stages.values():
        assert stage_cfg.model.startswith("groq/")


def test_for_stage_known_and_unknown() -> None:
    cfg = GatewayConfig(
        default=StageModelCfg(model="groq/default"),
        stages={"classifier": StageModelCfg(model="groq/small", temperature=0.0, max_tokens=16)},
    )
    assert cfg.for_stage("classifier").model == "groq/small"
    assert cfg.for_stage("classifier").temperature == 0.0
    assert cfg.for_stage("nope").model == "groq/default"


def test_unknown_active_config_raises(tmp_path: Path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text(
        "active: single_model\n"
        "configs:\n"
        "  single_model:\n"
        "    default: {model: groq/x}\n"
        "    stages: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="nope"):
        load_models_config(path, active="nope")


def test_missing_file_raises() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_models_config(tmp_path_abs() / "absent.yaml")


def tmp_path_abs() -> Path:
    return Path(__file__).resolve().parent


def test_active_from_env_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "models.yaml"
    path.write_text(
        "active: single_model\n"
        "configs:\n"
        "  single_model:\n"
        "    default: {model: groq/one}\n"
        "    stages: {}\n"
        "  dual_model:\n"
        "    default: {model: groq/two}\n"
        "    stages: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ACTIVE_CONFIG", "dual_model")
    cfg = load_models_config(path)
    assert cfg.default.model == "groq/two"
