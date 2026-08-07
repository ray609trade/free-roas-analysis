-- TimescaleDB schema for the Kalshi 15-minute crypto stack.
--
-- Start recording before building any model: you need months of history before
-- a backtest means anything, and history you did not capture is gone forever.
--
-- Apply with:  psql "$KALSHI_DATABASE_URL" -f schema.sql

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------
-- Index data: the mirrored BRTI composite, plus the raw constituent tops it
-- was built from. Keeping constituents lets the composite be recomputed later
-- when the weighting scheme changes -- without them, a methodology fix means
-- throwing away the history.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS index_samples (
    time            TIMESTAMPTZ      NOT NULL,
    asset           TEXT             NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    n_constituents  SMALLINT         NOT NULL,
    is_stale        BOOLEAN          NOT NULL DEFAULT FALSE
);
SELECT create_hypertable('index_samples', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_index_samples_asset_time
    ON index_samples (asset, time DESC);

CREATE TABLE IF NOT EXISTS constituent_tops (
    time        TIMESTAMPTZ      NOT NULL,
    exchange    TEXT             NOT NULL,
    symbol      TEXT             NOT NULL,
    bid         DOUBLE PRECISION NOT NULL,
    ask         DOUBLE PRECISION NOT NULL,
    bid_size    DOUBLE PRECISION NOT NULL,
    ask_size    DOUBLE PRECISION NOT NULL
);
SELECT create_hypertable('constituent_tops', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_constituent_tops_ex_time
    ON constituent_tops (exchange, symbol, time DESC);

CREATE TABLE IF NOT EXISTS constituent_trades (
    time      TIMESTAMPTZ      NOT NULL,
    exchange  TEXT             NOT NULL,
    symbol    TEXT             NOT NULL,
    price     DOUBLE PRECISION NOT NULL,
    size      DOUBLE PRECISION NOT NULL,
    aggressor TEXT             NOT NULL
);
SELECT create_hypertable('constituent_trades', 'time', if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- Kalshi contract data. The order book is recorded, not just consumed:
-- informed flow in the contract is itself a signal about the underlying.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS kalshi_quotes (
    time        TIMESTAMPTZ NOT NULL,
    ticker      TEXT        NOT NULL,
    yes_bid     INTEGER,        -- cents
    yes_ask     INTEGER,
    last_price  INTEGER,
    volume      BIGINT,
    open_interest BIGINT
);
SELECT create_hypertable('kalshi_quotes', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_kalshi_quotes_ticker_time
    ON kalshi_quotes (ticker, time DESC);

CREATE TABLE IF NOT EXISTS kalshi_book_deltas (
    time    TIMESTAMPTZ NOT NULL,
    ticker  TEXT        NOT NULL,
    seq     BIGINT,
    side    TEXT        NOT NULL,   -- 'yes' | 'no'
    price   INTEGER     NOT NULL,   -- cents
    delta   INTEGER     NOT NULL,   -- contracts added/removed
    is_snapshot BOOLEAN  NOT NULL DEFAULT FALSE
);
SELECT create_hypertable('kalshi_book_deltas', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_kalshi_book_deltas_ticker_time
    ON kalshi_book_deltas (ticker, time DESC);

CREATE TABLE IF NOT EXISTS kalshi_trades (
    time      TIMESTAMPTZ NOT NULL,
    ticker    TEXT        NOT NULL,
    price     INTEGER     NOT NULL,  -- cents
    count     INTEGER     NOT NULL,
    taker_side TEXT
);
SELECT create_hypertable('kalshi_trades', 'time', if_not_exists => TRUE);

-- Settlements: the ground truth every backtest resolves against, and the only
-- place to measure mirror-vs-official basis after the fact.
CREATE TABLE IF NOT EXISTS settlements (
    ticker            TEXT PRIMARY KEY,
    close_time        TIMESTAMPTZ      NOT NULL,
    strike            DOUBLE PRECISION NOT NULL,
    settled_yes       BOOLEAN          NOT NULL,
    expiration_value  DOUBLE PRECISION,
    mirror_average    DOUBLE PRECISION,   -- our 60s average over the same window
    recorded_at       TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- The accountability layer: every model estimate is written down *before* the
-- outcome is known, so calibration can be audited rather than remembered.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS model_estimates (
    time          TIMESTAMPTZ      NOT NULL,
    ticker        TEXT             NOT NULL,
    model         TEXT             NOT NULL,
    probability   DOUBLE PRECISION NOT NULL,
    prob_low      DOUBLE PRECISION,
    prob_high     DOUBLE PRECISION,
    market_mid    DOUBLE PRECISION,
    sigma_settlement DOUBLE PRECISION,
    n_left        SMALLINT,
    seconds_to_close DOUBLE PRECISION
);
SELECT create_hypertable('model_estimates', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_model_estimates_ticker_time
    ON model_estimates (ticker, time DESC);

-- Compression: tick data is repetitive and gets large fast.
ALTER TABLE index_samples SET (
    timescaledb.compress, timescaledb.compress_segmentby = 'asset'
);
SELECT add_compression_policy('index_samples', INTERVAL '7 days', if_not_exists => TRUE);

ALTER TABLE constituent_tops SET (
    timescaledb.compress, timescaledb.compress_segmentby = 'exchange, symbol'
);
SELECT add_compression_policy('constituent_tops', INTERVAL '7 days', if_not_exists => TRUE);
