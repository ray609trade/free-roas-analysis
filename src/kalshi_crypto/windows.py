"""15-minute window clock.

An up/down question needs three things pinned down before it means anything:
when the window opened, what the price was then (the strike), and when it
closes. This module owns that so every component agrees.

Windows align to :00, :15, :30, :45 UTC, matching Kalshi's 15-minute series.
The strike is the index level captured at the window open and is then frozen --
recomputing it from a later price is a subtle way to leak the future into a
backtest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

__all__ = ["Window", "WindowClock"]

WINDOW_SECONDS = 900


def _floor_to_window(ts: datetime, seconds: int) -> datetime:
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=UTC)


@dataclass
class Window:
    """One 15-minute up/down question."""

    open_time: datetime
    close_time: datetime
    strike: float | None = None

    @property
    def duration_s(self) -> float:
        return (self.close_time - self.open_time).total_seconds()

    def seconds_to_close(self, now: datetime) -> float:
        return (self.close_time - now).total_seconds()

    def seconds_elapsed(self, now: datetime) -> float:
        return (now - self.open_time).total_seconds()

    @property
    def is_priced(self) -> bool:
        return self.strike is not None

    def label(self) -> str:
        return f"{self.open_time:%H:%M}-{self.close_time:%H:%M}Z"


@dataclass
class WindowClock:
    """Tracks the current window and freezes its strike at the open."""

    window_seconds: int = WINDOW_SECONDS
    current: Window | None = field(default=None, init=False)
    _history: list[Window] = field(default_factory=list, init=False)

    def update(self, now: datetime, price: float | None) -> tuple[Window, Window | None]:
        """Advance the clock. Returns (current window, window that just closed).

        The strike is set once, from the first price seen at or after the open,
        and never revised.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        now = now.astimezone(UTC)
        open_time = _floor_to_window(now, self.window_seconds)

        closed: Window | None = None
        if self.current is None or self.current.open_time != open_time:
            if self.current is not None:
                closed = self.current
                self._history.append(closed)
            self.current = Window(
                open_time=open_time,
                close_time=open_time + timedelta(seconds=self.window_seconds),
            )

        if self.current.strike is None and price is not None:
            self.current.strike = price

        return self.current, closed

    @property
    def history(self) -> list[Window]:
        return list(self._history)

    def next_close(self, now: datetime) -> datetime:
        open_time = _floor_to_window(now.astimezone(UTC), self.window_seconds)
        return open_time + timedelta(seconds=self.window_seconds)


def annualised_to_per_second(daily_vol_pct: float, price: float) -> float:
    """Convenience: convert a daily percentage vol into per-second dollars.

    BTC's ~0.2% typical 15-minute move corresponds to daily vol / sqrt(96).
    """
    per_second_pct = daily_vol_pct / math.sqrt(86_400.0)
    return per_second_pct * price
