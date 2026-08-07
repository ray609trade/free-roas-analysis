"""Short-horizon features.

Ordered by how much mechanical story there is behind them, which is not the
same as how popular they are.

**Microstructure (real story).** Order-book imbalance and aggressor-side trade
flow are the observable footprint of actual pressure. They decay in seconds,
which is why they matter on a 15-minute horizon and nowhere else.

**Volatility regime (real, but not directional).** Tells you how wide to quote
and when to stand aside. It says nothing about direction and is never given a
directional weight here.

**Classic technicals (included as controls).** RSI, MACD, EMA crossovers ship
with weight **zero** by default. Every retail participant has them; if they
predicted, the price would already reflect it. They are here so you can
*measure* that they add nothing rather than assume it -- turn the weight on,
run the calibration tracker, and look at the Brier score. That measurement is
worth having; the indicator probably is not.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

__all__ = ["FeatureSet", "FeatureEngine"]


@dataclass(frozen=True)
class FeatureSet:
    """All features at one instant, standardised to roughly [-1, 1]."""

    book_imbalance: float = 0.0
    flow_imbalance_30s: float = 0.0
    flow_imbalance_2m: float = 0.0
    momentum: float = 0.0
    rsi: float = 50.0
    realized_vol_per_s: float = 0.0
    vol_ratio: float = 1.0
    n_samples: int = 0

    @property
    def rsi_signal(self) -> float:
        """RSI mapped to [-1, 1]. Carried as a control, not a conviction."""
        return (self.rsi - 50.0) / 50.0

    @property
    def is_high_vol(self) -> bool:
        return self.vol_ratio > 1.75

    @property
    def is_ready(self) -> bool:
        return self.n_samples >= 30


class _EMA:
    def __init__(self, span: float) -> None:
        self.alpha = 2.0 / (span + 1.0)
        self.value: float | None = None

    def update(self, x: float) -> float:
        self.value = x if self.value is None else self.alpha * x + (1 - self.alpha) * self.value
        return self.value


@dataclass
class FeatureEngine:
    """Maintains rolling features from price, book, and trade updates."""

    fast_span: float = 20.0
    slow_span: float = 60.0
    rsi_period: int = 14
    vol_window_s: float = 300.0
    long_vol_window_s: float = 3600.0

    _fast: _EMA = field(default_factory=lambda: _EMA(20.0), init=False)
    _slow: _EMA = field(default_factory=lambda: _EMA(60.0), init=False)
    _prices: deque[tuple[float, float]] = field(default_factory=deque, init=False)
    _gains: deque[float] = field(default_factory=deque, init=False)
    _losses: deque[float] = field(default_factory=deque, init=False)
    _flow: deque[tuple[float, float]] = field(default_factory=deque, init=False)
    _book_imbalance: float = field(default=0.0, init=False)
    _last_price: float | None = field(default=None, init=False)
    _long_vol: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._fast = _EMA(self.fast_span)
        self._slow = _EMA(self.slow_span)

    # -- inputs --------------------------------------------------------------

    def on_price(self, timestamp: float, price: float) -> None:
        if price <= 0:
            return
        self._prices.append((timestamp, price))
        cutoff = timestamp - self.long_vol_window_s
        while len(self._prices) > 2 and self._prices[0][0] < cutoff:
            self._prices.popleft()

        self._fast.update(price)
        self._slow.update(price)

        if self._last_price is not None:
            change = price - self._last_price
            self._gains.append(max(change, 0.0))
            self._losses.append(max(-change, 0.0))
            while len(self._gains) > self.rsi_period:
                self._gains.popleft()
                self._losses.popleft()
        self._last_price = price

    def on_book_imbalance(self, imbalance: float) -> None:
        """Top-of-book size imbalance in [-1, 1]; positive means bid-heavy."""
        self._book_imbalance = max(-1.0, min(1.0, imbalance))

    def on_trade(self, timestamp: float, notional: float, is_buy_aggressor: bool) -> None:
        self._flow.append((timestamp, notional if is_buy_aggressor else -notional))
        cutoff = timestamp - 120.0
        while self._flow and self._flow[0][0] < cutoff:
            self._flow.popleft()

    # -- derived -------------------------------------------------------------

    def _flow_imbalance(self, now: float, lookback_s: float) -> float:
        signed = 0.0
        gross = 0.0
        for ts, notional in self._flow:
            if now - ts <= lookback_s:
                signed += notional
                gross += abs(notional)
        if gross <= 0:
            return 0.0
        return max(-1.0, min(1.0, signed / gross))

    def _realized_vol(self, now: float, window_s: float) -> float:
        sq = 0.0
        total_dt = 0.0
        previous: tuple[float, float] | None = None
        for ts, price in self._prices:
            if now - ts > window_s:
                previous = (ts, price)
                continue
            if previous is not None:
                dt = ts - previous[0]
                if dt > 0:
                    r = math.log(price / previous[1])
                    sq += r * r
                    total_dt += dt
            previous = (ts, price)
        if total_dt <= 0:
            return 0.0
        return math.sqrt(sq / total_dt) * (self._last_price or 0.0)

    def _rsi(self) -> float:
        if len(self._gains) < self.rsi_period:
            return 50.0
        avg_gain = sum(self._gains) / len(self._gains)
        avg_loss = sum(self._losses) / len(self._losses)
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def snapshot(self, now: float) -> FeatureSet:
        short_vol = self._realized_vol(now, self.vol_window_s)
        long_vol = self._realized_vol(now, self.long_vol_window_s)
        vol_ratio = short_vol / long_vol if long_vol > 0 else 1.0

        momentum = 0.0
        if self._fast.value is not None and self._slow.value is not None and short_vol > 0:
            # Express the EMA gap in units of a 60-second move, then squash.
            gap = (self._fast.value - self._slow.value) / (short_vol * math.sqrt(60.0))
            momentum = math.tanh(gap)

        return FeatureSet(
            book_imbalance=self._book_imbalance,
            flow_imbalance_30s=self._flow_imbalance(now, 30.0),
            flow_imbalance_2m=self._flow_imbalance(now, 120.0),
            momentum=momentum,
            rsi=self._rsi(),
            realized_vol_per_s=short_vol,
            vol_ratio=vol_ratio,
            n_samples=len(self._prices),
        )
