"""WP13 PART 2: QuotaTracker key normalization, merged-total stop, 429-daily handling."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.quota import (  # noqa: E402
    QuotaTracker,
    _merge_split_keys,
    is_daily_limit_error,
    normalize_key,
)


def tracker(tmp_path: Path, seed: dict | None = None) -> QuotaTracker:
    t = QuotaTracker(quota_file=tmp_path / "quota.json")
    if seed:
        t.data = seed
        t._save()
        t.data = t._load()
    return t


def test_normalize_key_strips_provider_prefixes() -> None:
    assert normalize_key("groq/openai/gpt-oss-20b") == "openai/gpt-oss-20b"
    assert normalize_key("openai/gpt-oss-20b") == "openai/gpt-oss-20b"
    assert normalize_key("groq/qwen/qwen3.8-27b") == "qwen/qwen3.8-27b"
    assert normalize_key("qwen/qwen3.8-27b") == "qwen/qwen3.8-27b"


def test_normalized_keys_share_one_counter(tmp_path: Path) -> None:
    t = tracker(tmp_path)
    t.record_usage("groq/openai/gpt-oss-20b", 1000)
    t.record_usage("openai/gpt-oss-20b", 500)
    d = json.loads((tmp_path / "quota.json").read_text(encoding="utf-8"))
    assert d["models"]["openai/gpt-oss-20b"]["tokens_today"] == 1500
    assert "groq/openai/gpt-oss-20b" not in d["models"]


def test_merge_split_keys_takes_max_and_dedupes() -> None:
    data = {"date_utc": "2026-11-14", "models": {
        "groq/openai/gpt-oss-20b": {"tokens_today": 162261, "requests_today": 40},
        "openai/gpt-oss-20b": {"tokens_today": 40771, "requests_today": 34},
        "openai/gpt-oss-120b": {"tokens_today": 98125, "requests_today": 20},
    }}
    _merge_split_keys(data)
    merged = data["models"]
    assert "groq/openai/gpt-oss-20b" not in merged
    assert merged["openai/gpt-oss-20b"]["tokens_today"] == 162261  # max of split
    assert merged["openai/gpt-oss-20b"]["requests_today"] == 40
    assert merged["openai/gpt-oss-120b"]["tokens_today"] == 98125


def test_check_tpd_stops_on_merged_total(tmp_path: Path) -> None:
    t = tracker(tmp_path)
    t.record_usage("groq/openai/gpt-oss-20b", 162000)
    t.record_usage("openai/gpt-oss-20b", 10000)  # same model: merged total 172K
    # 172K >= 170K threshold: any further cell is rejected
    assert t.check_tpd("openai/gpt-oss-20b", 1000) is False


def test_check_tpd_allows_below_threshold(tmp_path: Path) -> None:
    t = tracker(tmp_path)
    t.record_usage("openai/gpt-oss-20b", 10000)
    assert t.check_tpd("groq/openai/gpt-oss-20b", 5000) is True


def test_daily_limit_429_marks_exhausted(tmp_path: Path) -> None:
    t = tracker(tmp_path)
    t.mark_daily_exhausted("groq/qwen/qwen3.8-27b")
    assert t.is_exhausted("qwen/qwen3.8-27b") is True  # normalized
    assert t.check_tpd("qwen/qwen3.8-27b", 1) is False


def test_daily_limit_message_detection() -> None:
    assert is_daily_limit_error("Rate limit reached for model on tokens per day (TPD)")
    assert is_daily_limit_error("Limit 1000 requests per day (RPD) exceeded")
    assert not is_daily_limit_error("Rate limit reached: tokens per minute (TPM)")
    assert not is_daily_limit_error("server overloaded")


def test_headers_preferred_over_local_count(tmp_path: Path) -> None:
    t = tracker(tmp_path)
    t.record_usage("openai/gpt-oss-120b", 1000)
    # provider says only 5000 tokens remain of 200K → 195K used, over threshold
    t.read_headers("groq/openai/gpt-oss-120b", {
        "x-ratelimit-remaining-tokens": "5000",
        "x-ratelimit-limit-tokens": "200000",
    })
    assert t.check_tpd("openai/gpt-oss-120b", 0) is False


def test_probe_clears_exhaustion_on_success(tmp_path: Path, monkeypatch) -> None:
    t = tracker(tmp_path)
    t.mark_daily_exhausted("qwen/qwen3.8-27b")

    import httpx as _httpx

    class FakeResp:
        status_code = 200
        headers = {}
        text = "{}"

    monkeypatch.setattr(_httpx, "post", lambda *a, **k: FakeResp())
    monkeypatch.setattr(t, "exhausted_until", {})  # allow immediate probe
    # bypass the hourly throttle by pre-dating
    import time

    t.exhausted_until["qwen/qwen3.8-27b"] = time.time() - 7200
    assert t.probe("qwen/qwen3.8-27b") is True
    assert not t.is_exhausted("qwen/qwen3.8-27b")


def test_probe_hourly_throttle(tmp_path: Path, monkeypatch) -> None:
    t = tracker(tmp_path)
    t.mark_daily_exhausted("qwen/qwen3.8-27b")
    calls = {"n": 0}

    import httpx as _httpx

    def fake_post(*a, **k):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise AssertionError("probe should be throttled")

    monkeypatch.setattr(_httpx, "post", fake_post)
    assert t.probe("qwen/qwen3.8-27b") is False  # throttled: no HTTP call
    assert calls["n"] == 0
