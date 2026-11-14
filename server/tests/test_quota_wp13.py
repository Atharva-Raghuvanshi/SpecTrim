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


def test_merge_sums_split_counters_not_max(tmp_path: Path) -> None:
    """WP14: the split keys recorded DIFFERENT calls, so the merge must SUM.
    (40,771 + 162,261 = 203,032 for openai/gpt-oss-20b on 2026-11-14.)"""
    data = {"date_utc": "2026-11-14", "models": {
        "openai/gpt-oss-20b": {"tokens_today": 40771, "requests_today": 34},
        "groq/openai/gpt-oss-20b": {"tokens_today": 162261, "requests_today": 66},
    }}
    _merge_split_keys(data)
    merged = data["models"]["openai/gpt-oss-20b"]
    assert merged["tokens_today"] == 40771 + 162261
    assert merged["requests_today"] == 34 + 66


def test_merged_sum_exceeds_tpd_stops(tmp_path: Path) -> None:
    t = tracker(tmp_path)
    t.record_usage("groq/openai/gpt-oss-20b", 162261)
    t.record_usage("openai/gpt-oss-20b", 40771)  # same model, other code path
    assert t.check_tpd("openai/gpt-oss-20b", 0) is False  # 203K > 170K stop


def test_no_double_recording_for_one_call(tmp_path: Path) -> None:
    """A call is recorded exactly once by its own code path: recording the same
    call under both key spellings is a caller bug the tracker cannot dedupe —
    document via this guard test (two records = two calls counted)."""
    t = tracker(tmp_path)
    t.record_usage("groq/openai/gpt-oss-20b", 100)
    t.record_usage("openai/gpt-oss-20b", 100)
    assert t.check_tpd("openai/gpt-oss-20b", 0) is True  # 200 tok: two calls counted
    d = json.loads((tmp_path / "quota.json").read_text(encoding="utf-8"))
    assert d["models"]["openai/gpt-oss-20b"]["tokens_today"] == 200


def test_headers_are_tpm_only_never_used_for_tpd(tmp_path: Path) -> None:
    """Groq x-ratelimit-* headers expose the 8K per-minute window: feeding them
    into the DAILY check would undercount (7927 remaining == 73 used)."""
    t = tracker(tmp_path)
    t.record_usage("openai/gpt-oss-20b", 190000)
    t.read_headers("openai/gpt-oss-20b", {
        "x-ratelimit-remaining-tokens": "7927",
        "x-ratelimit-limit-tokens": "8000",
    })
    # despite the header saying "7927 remaining", the local daily sum rules
    assert t.check_tpd("openai/gpt-oss-20b", 0) is False
