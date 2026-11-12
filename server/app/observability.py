"""Structured JSON logs with trace_id (M7.1) and Prometheus metrics (M7.2).

Metric labels never include session_id (hard rule).
"""
from __future__ import annotations

import contextvars
import logging
import sys

import structlog
from prometheus_client import Counter, Gauge, Histogram

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")

REQUESTS = Counter(
    "qo_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
STAGE_CALLS = Counter(
    "qo_stage_calls_total", "LLM stage calls", ["stage", "model"]
)
TOKENS_IN = Counter("qo_tokens_in_total", "Prompt tokens", ["stage", "model"])
TOKENS_OUT = Counter("qo_tokens_out_total", "Completion tokens", ["stage", "model"])
COST_USD = Counter("qo_cost_usd_total", "Cost in USD", ["stage", "model"])
STAGE_LATENCY = Histogram(
    "qo_stage_latency_seconds", "Stage latency", ["stage", "model"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
SESSIONS_CREATED = Counter("qo_sessions_created_total", "Sessions created")
ACTIVE_LEASES = Gauge("qo_jobs_leased", "Jobs currently leased")
DAILY_SPEND = Gauge("qo_daily_spend_usd", "Spend today in USD")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _inject_trace_id,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer()]
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def _inject_trace_id(logger, method, event_dict):  # type: ignore[no-untyped-def]
    event_dict["trace_id"] = trace_id_var.get()
    return event_dict


def get_logger(name: str = "qo"):  # type: ignore[no-untyped-def]
    return structlog.get_logger(name)


def record_stage_call(stage: str, model: str, tokens_in: int, tokens_out: int,
                      cost_usd: float | None, latency_ms: int) -> None:
    STAGE_CALLS.labels(stage=stage, model=model).inc()
    TOKENS_IN.labels(stage=stage, model=model).inc(tokens_in)
    TOKENS_OUT.labels(stage=stage, model=model).inc(tokens_out)
    if cost_usd is not None:
        COST_USD.labels(stage=stage, model=model).inc(cost_usd)
    STAGE_LATENCY.labels(stage=stage, model=model).observe(latency_ms / 1000)
