import re
from pathlib import Path

BANNED_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(litellm|openai|anthropic|cohere|mistralai|google\.generativeai)\b",
    re.MULTILINE,
)


def test_no_provider_sdk_imports_outside_gateway() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for py_file in app_dir.rglob("*.py"):
        if "gateway" in py_file.parts:
            continue
        if BANNED_IMPORT.search(py_file.read_text(encoding="utf-8")):
            offenders.append(str(py_file))
    assert offenders == []
