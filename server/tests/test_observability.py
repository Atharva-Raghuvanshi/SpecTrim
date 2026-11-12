"""M7.1/M7.2: JSON logs with trace_id; metrics increment without session_id."""
from __future__ import annotations

import io
import json
import logging

import structlog
from app.observability import (
    COST_USD,
    TOKENS_IN,
    TOKENS_OUT,
    get_logger,
    record_stage_call,
    setup_logging,
    trace_id_var,
)


def test_json_log_includes_trace_id() -> None:
    setup_logging("INFO")
    buf = io.StringIO()
    trace_id_var.set("trace-abc")
    root = logging.getLogger()
    old = root.handlers
    handler = logging.StreamHandler(buf)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        structlog.processors.JSONRenderer()]
        )
    )
    root.handlers = [handler]
    try:
        get_logger("test").info("hello", extra_field=1)
    finally:
        root.handlers = old
    line = buf.getvalue().strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["trace_id"] == "trace-abc"
    assert parsed["event"] == "hello"
    assert parsed["extra_field"] == 1


def test_metric_labels_never_include_session_id() -> None:
    record_stage_call("analyzer", "groq/openai/gpt-oss-20b", 10, 5, 0.0001, 120)
    for metric in (TOKENS_IN, TOKENS_OUT, COST_USD):
        for sample in metric.collect()[0].samples:
            assert "session_id" not in sample.labels
    TOKENS_IN.labels(stage="analyzer", model="groq/openai/gpt-oss-20b")._value.get()


def test_stage_metrics_increment(app_client) -> None:
    client, _ = app_client
    import uuid

    from tests.conftest import drain

    before = TOKENS_IN.labels(
        stage="analyzer", model="fake-model"
    )._value.get()
    r = client.post(
        "/sessions", json={"idea": "metric test"}, headers={"Idempotency-Key": f"k-{uuid.uuid4()}"}
    )
    drain(client, r.json()["id"])
    after = TOKENS_IN.labels(stage="analyzer", model="fake-model")._value.get()
    assert after > before
