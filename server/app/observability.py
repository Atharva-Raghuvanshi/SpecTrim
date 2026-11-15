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
COST_UNPRICED = Counter("qo_cost_unpriced_total", "Calls with unpriced models", ["stage", "model"])
STAGE_LATENCY = Histogram(
    "qo_stage_latency_seconds", "Stage latency", ["stage", "model"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
SESSIONS_CREATED = Counter("qo_sessions_created_total", "Sessions created")
ACTIVE_LEASES = Gauge("qo_jobs_leased", "Jobs currently leased")
DAILY_SPEND = Gauge("qo_daily_spend_usd", "Spend today in USD")

# Proxy/optimizer metrics (PRD_v2 §9)
PROXY_OVERHEAD = Histogram(
    "qo_proxy_overhead_seconds",
    "Added non-LLM proxy overhead (policy+chain+cache lookup)",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0),
)
PROXY_CACHE_HITS = Counter("qo_proxy_cache_hits_total", "P9 exact-cache hits served")
PROXY_PASSTHROUGH = Counter(
    "qo_proxy_passthrough_total", "Requests forwarded unoptimized", ["reason"]
)
PROXY_INVALID_AFTER_PASS = Counter(
    "qo_proxy_invalid_after_pass_total", "Passes that broke the request (reverted)", ["pass"]
)
PROXY_INVALID_ORIGINAL = Counter(
    "qo_proxy_invalid_original_total", "Original requests already invalid at intake"
)
PROXY_REQUESTS = Counter("qo_proxy_requests_total", "Proxy requests handled", ["endpoint", "mode"])
PASS_SAVINGS_EST = Counter(
    "qo_pass_savings_tokens_est_total", "Estimated tokens removed per pass", ["pass"]
)


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
    else:
        COST_UNPRICED.labels(stage=stage, model=model).inc()
    STAGE_LATENCY.labels(stage=stage, model=model).observe(latency_ms / 1000)


def init_metrics() -> None:
    """Pre-initialize Prometheus series with zero values so dashboards never show empty/no-data."""
    stages = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "proxy")
    for s in stages:
        STAGE_CALLS.labels(stage=s, model="default")
        TOKENS_IN.labels(stage=s, model="default")
        TOKENS_OUT.labels(stage=s, model="default")
        COST_USD.labels(stage=s, model="default")
        COST_UNPRICED.labels(stage=s, model="default")
        STAGE_LATENCY.labels(stage=s, model="default")

    passes = ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9")
    for p in passes:
        PASS_SAVINGS_EST.labels(**{"pass": p})


init_metrics()
