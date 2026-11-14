"""QuotaTracker (FR-26, PRD §10): tracks Groq free-tier limits per model.

Persists daily tokens and requests per model in evals/results/quota.json (reset by UTC date).
Free-tier limits per model:
  - 30 RPM, 1K RPD
  - 8K TPM (sleeps when projected TPM > 80% = 6,400 tokens/min)
  - 200K TPD (stops cleanly at 85% = 170,000 tokens/day per model with 'quota reached, resume tomorrow')
"""
from __future__ import annotations

import collections
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_QUOTA_FILE = RESULTS_DIR / "quota.json"

# Per-model limits & thresholds
MAX_TPM = 8000
MAX_TPD = 200000
TPM_THRESHOLD = int(MAX_TPM * 0.80)  # 6,400 tokens / min
TPD_THRESHOLD = int(MAX_TPD * 0.85)  # 170,000 tokens / day


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def normalize_key(model: str) -> str:
    """One model = one counter: strip provider prefixes.

    'groq/openai/gpt-oss-20b' and 'openai/gpt-oss-20b' are the same model."""
    m = model.strip()
    if m.startswith("groq/"):
        m = m[len("groq/"):]
    return m


def is_daily_limit_error(exc_or_message: object) -> bool:
    """True when a 429 message indicates the DAILY limit (not TPM/RPM)."""
    text = str(exc_or_message).lower()
    return ("daily" in text or "rpd" in text or "tokens per day" in text
            or "requests per day" in text)


PROBE_INTERVAL_S = 3600  # hourly probe when a model is exhausted


class QuotaTracker:
    def __init__(self, quota_file: Path | str | None = None) -> None:
        self.path = Path(quota_file or DEFAULT_QUOTA_FILE)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # In-memory sliding window of (timestamp, tokens) per model for TPM
        self._minute_windows: dict[str, collections.deque[tuple[float, int]]] = (
            collections.defaultdict(collections.deque)
        )
        # model (normalized) -> timestamp when a daily-limit 429 was seen
        self.exhausted_until: dict[str, float] = {}
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        cur_date = today_utc()
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("date_utc") == cur_date:
                    _merge_split_keys(data)
                    return data
                # New UTC day: reset daily counters
                data["date_utc"] = cur_date
                for mcfg in data.get("models", {}).values():
                    mcfg["tokens_today"] = 0
                    mcfg["requests_today"] = 0
                self._save(data)
                return data
            except Exception:
                pass
        init_data: dict[str, Any] = {
            "date_utc": cur_date,
            "models": {},
        }
        self._save(init_data)
        return init_data

    def _save(self, data: dict[str, Any] | None = None) -> None:
        save_data = data if data is not None else self.data
        self.path.write_text(json.dumps(save_data, indent=2), encoding="utf-8")

    def _ensure_model(self, model: str) -> dict[str, Any]:
        cur_date = today_utc()
        if self.data.get("date_utc") != cur_date:
            self.data = self._load()

        model = normalize_key(model)
        models = self.data.setdefault("models", {})
        if model not in models:
            models[model] = {
                "tokens_today": 0,
                "requests_today": 0,
                "last_updated_utc": datetime.now(timezone.utc).isoformat(),
                "ratelimit_remaining_tokens": None,
                "ratelimit_limit_tokens": None,
                "ratelimit_reset_tokens_s": None,
            }
        return models[model]

    def read_headers(self, model: str, headers: dict | None) -> None:
        """Parse standard x-ratelimit headers if present in API response."""
        if not headers:
            return
        model = normalize_key(model)
        mcfg = self._ensure_model(model)
        # Normalize header keys to lowercase
        norm = {str(k).lower(): v for k, v in headers.items()}

        if "x-ratelimit-remaining-tokens" in norm:
            try:
                mcfg["ratelimit_remaining_tokens"] = int(norm["x-ratelimit-remaining-tokens"])
            except (ValueError, TypeError):
                pass

        if "x-ratelimit-limit-tokens" in norm:
            try:
                mcfg["ratelimit_limit_tokens"] = int(norm["x-ratelimit-limit-tokens"])
            except (ValueError, TypeError):
                pass

        if "x-ratelimit-reset-tokens" in norm:
            try:
                val = norm["x-ratelimit-reset-tokens"]
                # Could be float seconds or duration string (e.g. '1.5s' or '2m30s')
                if isinstance(val, (int, float)):
                    mcfg["ratelimit_reset_tokens_s"] = float(val)
                elif isinstance(val, str) and val.endswith("s"):
                    mcfg["ratelimit_reset_tokens_s"] = float(val[:-1])
            except (ValueError, TypeError):
                pass
        self._save()

    def check_tpd(self, model: str, estimated_tokens: int = 0) -> bool:
        """Return True if daily token usage is below 85% of 200K TPD.

        Prefers the provider's x-ratelimit-remaining-tokens header when fresher
        than the local count. If a daily-limit 429 was seen, the model is
        exhausted until a successful hourly probe says otherwise."""
        key = normalize_key(model)
        if self.is_exhausted(key):
            print(f"model {key} exhausted until reset (429 daily); "
                  f"resume tomorrow or probe hourly", file=sys.stderr)
            return False
        mcfg = self._ensure_model(key)
        current = mcfg["tokens_today"]
        remaining = mcfg.get("ratelimit_remaining_tokens")
        limit = mcfg.get("ratelimit_limit_tokens") or MAX_TPD
        if remaining is not None and limit:
            used_by_provider = max(0, int(limit) - int(remaining))
            current = max(current, used_by_provider)  # prefer provider count
        if current + estimated_tokens >= TPD_THRESHOLD:
            print("quota reached, resume tomorrow", file=sys.stderr)
            return False
        return True

    def mark_daily_exhausted(self, model: str) -> None:
        """Record a daily-limit 429: the model is exhausted until reset."""
        self.exhausted_until[normalize_key(model)] = time.time()

    def is_exhausted(self, model: str) -> bool:
        """True while a daily-limit 429 is unresolved. Clears only via probe()."""
        return normalize_key(model) in self.exhausted_until

    def probe(self, model: str, api_key: str | None = None) -> bool:
        """One tiny direct call to detect whether the daily reset happened.

        On success (200): clears exhaustion and returns True. On a daily-limit
        429: stays exhausted and returns False. Probe at most hourly."""
        key = normalize_key(model)
        last = self.exhausted_until.get(key, 0)
        if time.time() - last < PROBE_INTERVAL_S:
            return not self.is_exhausted(key)
        self.exhausted_until[key] = time.time()  # throttle probes to hourly
        api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        provider_model = model if "/" in model else f"groq/{model}"
        try:
            import httpx

            r = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": provider_model, "max_tokens": 1,
                      "messages": [{"role": "user", "content": "ping"}]},
                timeout=15,
            )
        except Exception:
            return False
        if r.status_code == 200:
            self.exhausted_until.pop(key, None)
            self.read_headers(key, dict(r.headers))
            self._save()
            return True
        if r.status_code == 429 and is_daily_limit_error(r.text):
            return False
        # non-daily 429 (TPM/RPM): recoverable after a short wait
        self.exhausted_until.pop(key, None)
        return not self.is_exhausted(key)

    def check_tpm_and_sleep(self, model: str, estimated_tokens: int = 0) -> float:
        """If projected TPM exceeds 80% of 8K (6,400), sleep until window clears."""
        now = time.time()
        window = self._minute_windows[model]

        # Evict records older than 60 seconds
        while window and window[0][0] < now - 60.0:
            window.popleft()

        current_window_tokens = sum(tok for _, tok in window)
        projected = current_window_tokens + estimated_tokens

        slept = 0.0
        if projected > TPM_THRESHOLD and window:
            # Sleep until the oldest request in the 60s window rolls off
            oldest_ts = window[0][0]
            sleep_duration = max(0.5, (oldest_ts + 60.0) - now + 0.1)
            time.sleep(sleep_duration)
            slept = sleep_duration

            # Clean up window again after sleeping
            now = time.time()
            while window and window[0][0] < now - 60.0:
                window.popleft()

        return slept

    def record_usage(
        self,
        model: str,
        tokens: int,
        headers: dict | None = None,
    ) -> None:
        """Record token usage and request count for a model."""
        model = normalize_key(model)
        mcfg = self._ensure_model(model)
        mcfg["tokens_today"] += tokens
        mcfg["requests_today"] += 1
        mcfg["last_updated_utc"] = datetime.now(timezone.utc).isoformat()

        # Update in-memory TPM window
        now = time.time()
        self._minute_windows[model].append((now, tokens))

        if headers:
            self.read_headers(model, headers)
        else:
            self._save()


def _merge_split_keys(data: dict[str, Any]) -> None:
    """One-time migration: merge counters for keys that normalize identically
    (e.g. 'groq/openai/gpt-oss-20b' and 'openai/gpt-oss-20b')."""
    models = data.get("models", {})
    merged: dict[str, dict[str, Any]] = {}
    for key, cfg in models.items():
        norm = normalize_key(key)
        tgt = merged.get(norm)
        if tgt is None:
            merged[norm] = dict(cfg)
            continue
        tgt["tokens_today"] = max(
            int(tgt.get("tokens_today", 0)), int(cfg.get("tokens_today", 0)))
        tgt["requests_today"] = max(
            int(tgt.get("requests_today", 0)), int(cfg.get("requests_today", 0)))
    data["models"] = merged


def main() -> int:
    tracker = QuotaTracker()
    print(json.dumps(tracker.data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
