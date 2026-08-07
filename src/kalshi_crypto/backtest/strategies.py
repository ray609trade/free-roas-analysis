"""Reference strategies for the backtester.

:class:`RandomStrategy` is not a trading idea. It is the control: it must lose
exactly its fees and nothing more. Keep it, and run it whenever the harness
changes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..settlement import (
    PartialAverage,
    RollingVolEstimator,
    SettlementWindow,
    estimate_settlement_probability,
)
from .engine import Action, MarketState

__all__ = ["RandomStrategy", "SettlementModelStrategy", "MakerStrategy"]


@dataclass
class RandomStrategy:
    """Coin-flip taker. The leakage canary.

    Trades a fixed size in a random direction with fixed probability per tick,
    ignoring every piece of market information.
    """

    name: str = "random"
    size: int = 100
    trade_probability: float = 0.02
    seed: int = 12345
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def on_state(self, state: MarketState) -> Action:
        if state.position != 0:
            return Action()
        if self._rng.random() > self.trade_probability:
            return Action()
        if state.book.best_bid is None or state.book.best_ask is None:
            return Action()
        direction = 1 if self._rng.random() < 0.5 else -1
        return Action(taker_size=direction * self.size)


@dataclass
class SettlementModelStrategy:
    """Section 4.1 in strategy form: cross only on a fee-clearing divergence.

    Deliberately conservative -- it takes liquidity, so it must overcome the
    full taker fee plus half spread plus a margin. In practice this fires
    rarely, which is the honest outcome for a taker strategy on this product.
    """

    strike: float
    name: str = "settlement-model"
    size: int = 100
    margin: float = 0.02
    window: SettlementWindow = field(default_factory=SettlementWindow)
    vol: RollingVolEstimator = field(default_factory=RollingVolEstimator)
    _partial: PartialAverage = field(default_factory=PartialAverage, init=False)
    _last_sample_t: float | None = field(default=None, init=False)

    def on_state(self, state: MarketState) -> Action:
        if state.index_price is None:
            return Action()
        self.vol.update(state.timestamp, state.index_price)

        in_window = state.seconds_to_close <= self.window.duration_s
        if in_window:
            # Accumulate one sample per interval once the window has opened.
            due = (
                self._last_sample_t is None
                or state.timestamp - self._last_sample_t >= self.window.sample_interval_s
            )
            if due and self._partial.n_done < self.window.n_samples:
                self._partial = self._partial.observe(state.index_price)
                self._last_sample_t = state.timestamp

        sigma = self.vol.sigma_per_second()
        if sigma is None or state.position != 0:
            return Action()

        seconds_to_window_start = max(
            state.seconds_to_close - self.window.duration_s, 0.0
        )
        estimate = estimate_settlement_probability(
            current_price=state.index_price,
            strike=self.strike,
            sigma_per_second=sigma,
            seconds_to_window_start=seconds_to_window_start,
            partial=self._partial,
            window=self.window,
            sigma_rel_se=self.vol.relative_standard_error(),
        )
        if estimate.resolved:
            return Action()

        bid, ask = state.book.best_bid, state.book.best_ask
        if bid is None or ask is None:
            return Action()
        mid = (bid + ask) / 200.0
        half_spread = (ask - bid) / 200.0
        edge = estimate.probability - mid
        # Taker fee at the money is ~1.75c; require it plus spread plus margin.
        hurdle = 0.0175 + half_spread + self.margin
        if abs(edge) <= hurdle:
            return Action()
        return Action(taker_size=self.size if edge > 0 else -self.size)


@dataclass
class MakerStrategy:
    """Section 4.2 as a backtestable strategy -- see quoting.engine for live use."""

    fair_value: float = 0.5
    name: str = "maker"
    size: int = 100
    half_width_cents: int = 2
    max_position: int = 300
    skew_per_contract: float = 0.01
    pull_inside_s: float = 60.0

    def on_state(self, state: MarketState) -> Action:
        if state.seconds_to_close <= self.pull_inside_s:
            return Action(cancel_all=True)
        bid, ask = state.book.best_bid, state.book.best_ask
        if bid is None or ask is None:
            return Action(cancel_all=True)

        # Skew against inventory so quotes lean toward flattening.
        skew = self.skew_per_contract * state.position / max(self.size, 1)
        fair_cents = (self.fair_value - skew) * 100.0
        k = max(self.half_width_cents, (ask - bid) / 2.0)

        bid_price = int(max(1, min(99, round(fair_cents - k))))
        ask_price = int(max(1, min(99, round(fair_cents + k))))
        if bid_price >= ask_price:
            return Action(cancel_all=True)

        quotes: list[tuple[bool, int, int]] = []
        if state.position < self.max_position:
            quotes.append((True, bid_price, self.size))
        if state.position > -self.max_position:
            quotes.append((False, ask_price, self.size))
        return Action(quotes=tuple(quotes))
