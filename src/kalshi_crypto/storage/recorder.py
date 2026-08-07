"""Tick recorders (build-order item 3).

Two implementations behind one protocol:

``MemoryRecorder``     -- in-process, used by tests and by the backtester.
``TimescaleRecorder``  -- batched COPY-style inserts into TimescaleDB.

Batching matters. At 1Hz per asset plus every constituent top-of-book update,
row-at-a-time inserts become the bottleneck long before the exchange does.
Writes are flushed on a size threshold or a time threshold, whichever comes
first, so a quiet market still gets its data persisted promptly.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..marketdata.types import BookTop, IndexSample, TradePrint

__all__ = ["Recorder", "MemoryRecorder", "TimescaleRecorder", "schema_path"]

log = logging.getLogger(__name__)


def schema_path() -> Path:
    """Path to schema.sql, for ``psql -f``."""
    return Path(__file__).with_name("schema.sql")


def _ts(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


class Recorder(Protocol):
    def record_index(self, sample: IndexSample) -> None: ...
    def record_top(self, top: BookTop) -> None: ...
    def record_trade(self, trade: TradePrint) -> None: ...
    def record_estimate(self, ticker: str, model: str, fields: dict[str, Any]) -> None: ...
    def flush(self) -> None: ...


@dataclass
class MemoryRecorder:
    """Keeps everything in lists. Fine for tests and short replays."""

    index_samples: list[IndexSample] = field(default_factory=list)
    tops: list[BookTop] = field(default_factory=list)
    trades: list[TradePrint] = field(default_factory=list)
    estimates: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def record_index(self, sample: IndexSample) -> None:
        self.index_samples.append(sample)

    def record_top(self, top: BookTop) -> None:
        self.tops.append(top)

    def record_trade(self, trade: TradePrint) -> None:
        self.trades.append(trade)

    def record_estimate(self, ticker: str, model: str, fields: dict[str, Any]) -> None:
        self.estimates.append((ticker, model, dict(fields)))

    def flush(self) -> None:
        return None


@dataclass
class TimescaleRecorder:
    """Batched writer. Requires the ``db`` extra (``psycopg``).

    Use as a context manager so the final partial batch is always flushed --
    dropping the tail of a recording session is a silent, permanent data loss.
    """

    dsn: str
    batch_size: int = 500
    flush_interval_s: float = 5.0
    _conn: Any = field(default=None, init=False)
    _buffers: dict[str, list[tuple]] = field(default_factory=dict, init=False)
    _last_flush: float = field(default_factory=time.monotonic, init=False)

    _SQL = {
        "index_samples": (
            "INSERT INTO index_samples (time, asset, price, n_constituents, is_stale) "
            "VALUES (%s, %s, %s, %s, %s)"
        ),
        "constituent_tops": (
            "INSERT INTO constituent_tops (time, exchange, symbol, bid, ask, bid_size, ask_size) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)"
        ),
        "constituent_trades": (
            "INSERT INTO constituent_trades (time, exchange, symbol, price, size, aggressor) "
            "VALUES (%s, %s, %s, %s, %s, %s)"
        ),
        "model_estimates": (
            "INSERT INTO model_estimates (time, ticker, model, probability, prob_low, "
            "prob_high, market_mid, sigma_settlement, n_left, seconds_to_close) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        ),
    }

    def __enter__(self) -> TimescaleRecorder:
        import psycopg

        self._conn = psycopg.connect(self.dsn, autocommit=False)
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self.flush()
        finally:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _append(self, table: str, row: tuple) -> None:
        buf = self._buffers.setdefault(table, [])
        buf.append(row)
        due = time.monotonic() - self._last_flush >= self.flush_interval_s
        if len(buf) >= self.batch_size or due:
            self.flush()

    def record_index(self, sample: IndexSample) -> None:
        self._append(
            "index_samples",
            (_ts(sample.timestamp), sample.asset, sample.price,
             sample.n_constituents, sample.is_stale),
        )

    def record_top(self, top: BookTop) -> None:
        self._append(
            "constituent_tops",
            (_ts(top.timestamp), top.exchange, top.symbol, top.bid, top.ask,
             top.bid_size, top.ask_size),
        )

    def record_trade(self, trade: TradePrint) -> None:
        self._append(
            "constituent_trades",
            (_ts(trade.timestamp), trade.exchange, trade.symbol, trade.price,
             trade.size, trade.aggressor),
        )

    def record_estimate(self, ticker: str, model: str, fields: dict[str, Any]) -> None:
        self._append(
            "model_estimates",
            (
                _ts(fields.get("timestamp", time.time())), ticker, model,
                fields.get("probability"), fields.get("prob_low"), fields.get("prob_high"),
                fields.get("market_mid"), fields.get("sigma_settlement"),
                fields.get("n_left"), fields.get("seconds_to_close"),
            ),
        )

    def flush(self) -> None:
        if self._conn is None or not any(self._buffers.values()):
            self._last_flush = time.monotonic()
            return
        try:
            with self._conn.cursor() as cur:
                for table, rows in self._buffers.items():
                    if rows:
                        cur.executemany(self._SQL[table], rows)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._buffers.clear()
            self._last_flush = time.monotonic()

    def record_settlement(
        self,
        ticker: str,
        close_time: datetime,
        strike: float,
        settled_yes: bool,
        expiration_value: float | None,
        mirror_average: float | None,
    ) -> None:
        """Write settlement ground truth. Committed immediately, not batched.

        ``expiration_value`` and ``mirror_average`` sitting side by side is what
        makes the section-3 settlement question empirically answerable: if they
        agree over hundreds of expiries, the mirror is tracking whatever Kalshi
        actually settles on.
        """
        if self._conn is None:
            raise RuntimeError("use TimescaleRecorder as a context manager")
        self.flush()
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settlements (ticker, close_time, strike, settled_yes, "
                "expiration_value, mirror_average) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (ticker) DO UPDATE SET "
                "expiration_value = EXCLUDED.expiration_value, "
                "mirror_average = EXCLUDED.mirror_average",
                (ticker, close_time, strike, settled_yes, expiration_value, mirror_average),
            )
        self._conn.commit()


def replay_index_samples(rows: Sequence[tuple[float, float]], asset: str) -> list[IndexSample]:
    """Build :class:`IndexSample` objects from ``(timestamp, price)`` rows."""
    return [
        IndexSample(asset=asset, price=price, timestamp=ts, n_constituents=1)
        for ts, price in rows
    ]
