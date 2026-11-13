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


class QuotaTracker:
    def __init__(self, quota_file: Path | str | None = None) -> None:
        self.path = Path(quota_file or DEFAULT_QUOTA_FILE)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # In-memory sliding window of (timestamp, tokens) per model for TPM
        self._minute_windows: dict[str, collections.deque[tuple[float, int]]] = (
            collections.defaultdict(collections.deque)
        )
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        cur_date = today_utc()
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("date_utc") == cur_date:
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

        If at or above threshold, prints 'quota reached, resume tomorrow' and returns False.
        """
        mcfg = self._ensure_model(model)
        current = mcfg["tokens_today"]
        if current + estimated_tokens >= TPD_THRESHOLD:
            print("quota reached, resume tomorrow", file=sys.stderr)
            return False
        return True

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


def main() -> int:
    tracker = QuotaTracker()
    print(json.dumps(tracker.data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
