-- Initial schema (PRD 5.7)
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'queued',
    trace_id        TEXT NOT NULL,
    current_version INT,
    spend_usd       NUMERIC(10, 6) NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL UNIQUE,
    idea            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    kind         TEXT NOT NULL DEFAULT 'pipeline',
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | leased | done | dead
    attempts     INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 5,
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
    leased_until TIMESTAMPTZ,
    locked_by    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_claim_idx ON jobs (status, run_after);

CREATE TABLE IF NOT EXISTS stages (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL REFERENCES sessions(id),
    stage_key  TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed
    input_hash TEXT NOT NULL,
    output     JSONB,
    model      TEXT,
    tokens_in  INT NOT NULL DEFAULT 0,
    tokens_out INT NOT NULL DEFAULT 0,
    cost_usd   NUMERIC(10, 6),
    latency_ms INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, stage_key)
);

CREATE TABLE IF NOT EXISTS versions (
    session_id    TEXT NOT NULL REFERENCES sessions(id),
    version       INT NOT NULL,
    package       JSONB NOT NULL,
    created_from  TEXT NOT NULL DEFAULT 'initial',  -- initial | change | failure
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, version)
);

CREATE TABLE IF NOT EXISTS questions (
    id          TEXT NOT NULL,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    round       INT NOT NULL DEFAULT 1,
    text        TEXT NOT NULL,
    category    TEXT NOT NULL,
    answer      TEXT,
    answered_at TIMESTAMPTZ,
    PRIMARY KEY (session_id, id)
);

CREATE TABLE IF NOT EXISTS events (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    seq        INT NOT NULL,
    type       TEXT NOT NULL,
    payload    JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, seq)
);

CREATE TABLE IF NOT EXISTS daily_spend (
    day DATE PRIMARY KEY,
    usd NUMERIC(10, 4) NOT NULL DEFAULT 0
);
