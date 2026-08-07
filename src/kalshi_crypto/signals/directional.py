"""Up/down probability for a 15-minute window.

How the number is built
-----------------------
Start from the honest baseline: a driftless random walk. That already gives a
probability, and on this horizon it is most of the answer.

    P(up) = Phi( (price - strike + drift) / sigma_effective )

Two pieces are venue-dependent and must not be mixed up:

* ``sigma_effective`` -- for a Kalshi binary that settles on a 60-second
  average, remaining uncertainty is ``sigma*sqrt(T/3)``-ish (exact discrete form
  in :mod:`kalshi_crypto.settlement`). For a perp or spot bet that resolves on
  the endpoint, it is ``sigma*sqrt(T)``. Using the wrong one misstates
  confidence by ~42% in the standard deviation.
* ``drift`` -- the only place features enter. It is deliberately small and hard
  capped.

Why the drift is capped
-----------------------
The market price is the best available estimate on a 15-minute horizon. A model
claiming a large edge over it is almost always wrong. The cap
(``max_drift_sigmas``, default 0.35 standard deviations) bounds how far this
model is allowed to disagree, which bounds how wrong it can be. Raise it only
after the calibration tracker earns it.

Weights are a prior, not a result
---------------------------------
The default weights encode "microstructure has a mechanical story, classic
indicators do not" -- RSI ships at weight **zero**. None of them are fitted.
Run :mod:`kalshi_crypto.signals.calibration` over real outcomes before believing
any of it. Until then the output is a well-founded guess with an error bar, and
it says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from ..settlement import (
    PartialAverage,
    SettlementWindow,
    discrete_mean_variance_factor,
    normal_cdf,
)
from ..venues import PRICING_AVERAGE, TradingContext
from .features import FeatureSet

__all__ = ["DirectionalWeights", "UpDownQuote", "DirectionalModel", "MACRO_BLACKOUTS"]


@dataclass(frozen=True)
class DirectionalWeights:
    """Contribution of each feature to the drift estimate.

    Units: multiples of one-second sigma per second of remaining time.
    """

    book_imbalance: float = 0.020
    flow_imbalance_30s: float = 0.030
    flow_imbalance_2m: float = 0.015
    momentum: float = 0.010
    # Ships at zero on purpose. Turn it on only to measure that it adds nothing.
    rsi: float = 0.000


# Windows where direction is not predictable and the correct action is to stand
# aside, not to guess. Macro moves the daily chart, not the 15-minute chart.
MACRO_BLACKOUTS = (
    "CPI", "FOMC", "NFP", "PPI", "GDP", "PCE",
)
BLACKOUT_MINUTES = 30


@dataclass(frozen=True)
class UpDownQuote:
    """The deliverable: a price on up or down for one window on one venue."""

    asset: str
    venue: str
    instrument: str
    window_label: str
    seconds_to_close: float
    strike: float
    price: float
    prob_up: float
    prob_low: float
    prob_high: float
    sigma_effective: float
    baseline_prob: float
    drift: float
    pricing_mode: str
    tradable: bool
    reason: str

    @property
    def prob_down(self) -> float:
        return 1.0 - self.prob_up

    @property
    def side(self) -> str:
        return "UP" if self.prob_up >= 0.5 else "DOWN"

    @property
    def confidence(self) -> float:
        """Distance from a coin flip, in [0, 1]."""
        return abs(self.prob_up - 0.5) * 2.0

    def cents_up(self) -> int:
        """Fair value of the UP contract in cents, comparable to a Kalshi quote."""
        return int(round(self.prob_up * 100))

    def format_line(self) -> str:
        return (
            f"{self.asset:<4} {self.venue:<10} {self.window_label:<14} "
            f"T-{self.seconds_to_close:6.0f}s  "
            f"UP {self.prob_up:6.1%} [{self.prob_low:5.1%}-{self.prob_high:5.1%}]  "
            f"DOWN {self.prob_down:6.1%}  "
            f"{'TRADABLE' if self.tradable else 'stand aside'}"
        )


@dataclass
class DirectionalModel:
    """Produces a calibrated up/down probability for a window."""

    weights: DirectionalWeights = field(default_factory=DirectionalWeights)
    max_drift_sigmas: float = 0.35
    min_confidence_to_trade: float = 0.10
    settlement_window: SettlementWindow = field(default_factory=SettlementWindow)
    blackout_until: datetime | None = None

    def drift_sigmas(self, features: FeatureSet) -> float:
        """Drift in units of one-second sigma per second, before scaling."""
        w = self.weights
        raw = (
            w.book_imbalance * features.book_imbalance
            + w.flow_imbalance_30s * features.flow_imbalance_30s
            + w.flow_imbalance_2m * features.flow_imbalance_2m
            + w.momentum * features.momentum
            + w.rsi * features.rsi_signal
        )
        return raw

    def effective_sigma(
        self,
        *,
        sigma_per_second: float,
        seconds_to_close: float,
        context: TradingContext,
        partial: PartialAverage | None = None,
    ) -> tuple[float, int]:
        """Remaining uncertainty, using the venue's own settlement rule."""
        if context.pricing_mode == PRICING_AVERAGE:
            window = self.settlement_window
            partial = partial or PartialAverage()
            n_left = max(window.n_samples - partial.n_done, 0)
            if n_left == 0:
                return 0.0, 0
            t0 = max(seconds_to_close - window.duration_s, 0.0)
            if partial.n_done > 0:
                t0 = min(t0, window.sample_interval_s)
            factor = discrete_mean_variance_factor(n_left, t0, window.sample_interval_s)
            return sigma_per_second * math.sqrt(factor), n_left
        # Endpoint settlement: plain sqrt of remaining time.
        t = max(seconds_to_close, 0.0)
        return sigma_per_second * math.sqrt(t), 0

    def in_blackout(self, now: datetime) -> bool:
        return self.blackout_until is not None and now < self.blackout_until

    def quote(
        self,
        *,
        context: TradingContext,
        price: float,
        strike: float,
        seconds_to_close: float,
        sigma_per_second: float,
        features: FeatureSet,
        now: datetime,
        window_label: str = "",
        sigma_rel_se: float = 0.15,
        partial: PartialAverage | None = None,
    ) -> UpDownQuote:
        """Price up-or-down for this window on this venue."""
        sigma_eff, _ = self.effective_sigma(
            sigma_per_second=sigma_per_second,
            seconds_to_close=seconds_to_close,
            context=context,
            partial=partial,
        )

        # Drift, expressed in dollars over the remaining time, then capped
        # relative to the uncertainty it is competing against.
        drift_raw = self.drift_sigmas(features) * sigma_per_second * max(seconds_to_close, 0.0)
        cap = self.max_drift_sigmas * sigma_eff
        drift = max(-cap, min(cap, drift_raw))

        def _p(sd: float, d: float) -> float:
            if sd <= 0:
                return 1.0 if (price + d) > strike else 0.0
            return normal_cdf((price - strike + d) / sd)

        prob = _p(sigma_eff, drift)
        baseline = _p(sigma_eff, 0.0)

        # Interval from volatility estimation error, enveloped because the map
        # from sigma to probability is not monotone across the strike.
        lo_sd = max(sigma_eff * (1 - 1.96 * sigma_rel_se), 0.0)
        hi_sd = sigma_eff * (1 + 1.96 * sigma_rel_se)
        candidates = [prob, _p(lo_sd, drift), _p(hi_sd, drift)]
        prob_low, prob_high = min(candidates), max(candidates)

        tradable, reason = self._gate(features, prob, now, seconds_to_close)

        return UpDownQuote(
            asset=context.instrument.asset,
            venue=context.instrument.venue,
            instrument=str(context.instrument),
            window_label=window_label,
            seconds_to_close=seconds_to_close,
            strike=strike,
            price=price,
            prob_up=prob,
            prob_low=prob_low,
            prob_high=prob_high,
            sigma_effective=sigma_eff,
            baseline_prob=baseline,
            drift=drift,
            pricing_mode=context.pricing_mode,
            tradable=tradable,
            reason=reason,
        )

    def _gate(
        self, features: FeatureSet, prob: float, now: datetime, seconds_to_close: float
    ) -> tuple[bool, str]:
        """Decide whether this is even worth acting on."""
        if not features.is_ready:
            return False, "warming up: not enough price history yet"
        if self.in_blackout(now):
            return False, "macro blackout: direction is not predictable here"
        if features.is_high_vol:
            return False, (
                f"elevated vol regime (x{features.vol_ratio:.1f}): widen, do not predict"
            )
        if seconds_to_close < 5:
            return False, "too close to the bell to act on"
        confidence = abs(prob - 0.5) * 2.0
        if confidence < self.min_confidence_to_trade:
            return False, f"too close to a coin flip ({prob:.1%})"
        return True, f"{'UP' if prob >= 0.5 else 'DOWN'} with {confidence:.0%} confidence"
