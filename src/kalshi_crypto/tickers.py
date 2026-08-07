"""Parsing and construction of Kalshi 15-minute crypto market tickers.

Format::

    KXBTC15M-26APR221830-T105000
    ^^^^^^^^ ^^^^^^^^^^^ ^^^^^^^
    series   close time  strike

* series      -- ``KX`` + asset + duration, e.g. ``KXBTC15M``
* close time  -- ``YYMONDDHHMM`` in UTC, e.g. 2026-04-22 18:30Z
* strike      -- a type letter then the level; ``T`` is a threshold ("above")

Markets open roughly 5 minutes before their window begins and trade through the
close, so a market's total lifecycle is about 20 minutes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = ["MarketTicker", "parse_ticker", "build_ticker", "SUPPORTED_ASSETS"]

SUPPORTED_ASSETS = ("BTC", "ETH", "XRP", "SOL", "DOGE")

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_MONTH_NAMES = {v: k for k, v in _MONTHS.items()}

_SERIES_RE = re.compile(r"^KX(?P<asset>[A-Z]+?)(?P<duration>\d+[MHD])$")
_DATE_RE = re.compile(r"^(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hh>\d{2})(?P<mi>\d{2})$")
_STRIKE_RE = re.compile(r"^(?P<kind>[A-Z])(?P<level>[0-9]+(?:\.[0-9]+)?)$")


@dataclass(frozen=True)
class MarketTicker:
    """A parsed market ticker."""

    raw: str
    series: str
    asset: str
    duration: str
    close_time: datetime
    strike_kind: str
    strike: float

    @property
    def duration_seconds(self) -> int:
        value, unit = int(self.duration[:-1]), self.duration[-1]
        return value * {"M": 60, "H": 3600, "D": 86400}[unit]

    @property
    def open_time(self) -> datetime:
        """Start of the contract's measurement window."""
        from datetime import timedelta

        return self.close_time - timedelta(seconds=self.duration_seconds)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.raw


def parse_ticker(ticker: str) -> MarketTicker:
    """Parse a Kalshi market ticker. Raises ``ValueError`` if malformed."""
    parts = ticker.strip().upper().split("-")
    if len(parts) != 3:
        raise ValueError(f"expected 3 dash-separated parts, got {len(parts)}: {ticker!r}")
    series, date_part, strike_part = parts

    m_series = _SERIES_RE.match(series)
    if not m_series:
        raise ValueError(f"unrecognised series {series!r}")

    m_date = _DATE_RE.match(date_part)
    if not m_date:
        raise ValueError(f"unrecognised close time {date_part!r}")
    month = _MONTHS.get(m_date["mon"])
    if month is None:
        raise ValueError(f"unrecognised month {m_date['mon']!r}")
    close = datetime(
        2000 + int(m_date["yy"]), month, int(m_date["dd"]),
        int(m_date["hh"]), int(m_date["mi"]), tzinfo=UTC,
    )

    m_strike = _STRIKE_RE.match(strike_part)
    if not m_strike:
        raise ValueError(f"unrecognised strike {strike_part!r}")

    return MarketTicker(
        raw=ticker.strip().upper(),
        series=series,
        asset=m_series["asset"],
        duration=m_series["duration"],
        close_time=close,
        strike_kind=m_strike["kind"],
        strike=float(m_strike["level"]),
    )


def build_ticker(asset: str, close_time: datetime, strike: float, *, duration: str = "15M") -> str:
    """Inverse of :func:`parse_ticker` for the threshold-style markets.

    ``close_time`` must be timezone-aware; it is converted to UTC. Integral
    strikes render without a decimal point, matching Kalshi's own formatting.
    """
    if close_time.tzinfo is None:
        raise ValueError("close_time must be timezone-aware")
    close = close_time.astimezone(UTC)
    level = f"{strike:g}" if strike != int(strike) else str(int(strike))
    return (
        f"KX{asset.upper()}{duration}-"
        f"{close.year % 100:02d}{_MONTH_NAMES[close.month]}{close.day:02d}"
        f"{close.hour:02d}{close.minute:02d}-"
        f"T{level}"
    )
