-- selflearn-core storage schema — Postgres/Supabase flavor.
-- Mirrors schema.sql (SQLite, source of truth for local/backtest use); this
-- variant is applied to the shared Supabase project that the liddar-terminal
-- Vercel functions write to (api/log-prediction) and read from
-- (api/predictions, api/scores).
--
-- Security model: RLS is ENABLED with no anon policies on every table, so the
-- browser can never touch the store; the Vercel functions use the service-role
-- key server-side, which bypasses RLS by design.

CREATE TABLE IF NOT EXISTS predictions (
    id            TEXT PRIMARY KEY,        -- uuid
    ts            BIGINT NOT NULL,         -- unix seconds, prediction time
    model_version TEXT NOT NULL,
    task          TEXT NOT NULL,           -- 'scanner' | 'power_h1' | ...
    features      JSONB NOT NULL,          -- only data known at ts (no lookahead);
                                           -- optional breadth regime stamp lives here
    prediction    JSONB NOT NULL,          -- direction/price/idea payload
    confidence    DOUBLE PRECISION,        -- nullable, [0,1]
    horizon_s     BIGINT NOT NULL,         -- seconds until resolvable
    cohort        TEXT,                    -- ticker / ISO / regime bucket
    meta          JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_pred_task_ts ON predictions(task, ts);

CREATE TABLE IF NOT EXISTS outcomes (
    prediction_id TEXT PRIMARY KEY REFERENCES predictions(id),
    resolved_ts   BIGINT NOT NULL,
    realized      JSONB NOT NULL,          -- realized value / signed_return / hit
    meta          JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS registry (
    version    TEXT PRIMARY KEY,
    task       TEXT NOT NULL,
    config     JSONB NOT NULL,
    created_ts BIGINT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'candidate',  -- candidate|live|retired
    autonomy   INTEGER NOT NULL DEFAULT 0          -- L0..L4
);

CREATE TABLE IF NOT EXISTS registry_scores (
    version     TEXT NOT NULL REFERENCES registry(version),
    recorded_ts BIGINT NOT NULL,
    score       JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_regscores_version ON registry_scores(version, recorded_ts);

-- Lock the tables down: RLS on, no policies -> anon/authenticated get nothing;
-- only the service-role key (server-side) can read/write.
ALTER TABLE predictions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE outcomes        ENABLE ROW LEVEL SECURITY;
ALTER TABLE registry        ENABLE ROW LEVEL SECURITY;
ALTER TABLE registry_scores ENABLE ROW LEVEL SECURITY;
