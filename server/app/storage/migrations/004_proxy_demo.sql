-- Demo ledger (WP9): rows are clearly flagged demo data, never in /stats
CREATE TABLE IF NOT EXISTS proxy_demo_requests (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id         TEXT,
    mode               TEXT NOT NULL,
    model              TEXT NOT NULL,
    cache_hit          BOOLEAN NOT NULL DEFAULT FALSE,
    passthrough        BOOLEAN NOT NULL DEFAULT FALSE,
    tokens_baseline_est INT NOT NULL DEFAULT 0,
    tokens_actual      INT,
    cost_baseline_est  NUMERIC(10, 6) DEFAULT 0,
    cost_actual        NUMERIC(10, 6),
    latency_ms         INT,
    pass_reports       JSONB NOT NULL DEFAULT '[]',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
