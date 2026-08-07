"""Settlement-window variance model (spec section 4.1).

The contract does not settle on the price at the close. It settles on the
**average** of the index over the final 60 seconds, sampled once per second.
That single fact changes the pricing formula.

Naive approach
--------------
Treat settlement as a point T seconds away and use ``sigma * sqrt(T)``.

Why it is wrong
---------------
For a driftless random walk, the variance of the *time-average* of the path
over a window of length T is ``sigma^2 * T / 3``, not ``sigma^2 * T``. So::

    sigma_settlement = sigma * sqrt(T / 3) ~= 0.577 * sigma * sqrt(T)

Settlement is about 42% less variable than the endpoint calculation implies.
Anyone pricing with the endpoint formula systematically overstates the chance
of a late flip: near-certain contracts get underpriced, coin-flips overpriced.

What this module actually computes
----------------------------------
We do not use the continuous ``T/3`` approximation for live pricing. Settlement
is a discrete average of a known, small number of samples, and near the close
``n_left`` gets small enough that the continuous limit is visibly wrong. For
samples at times ``t_i = t0 + i*dt`` (i = 1..n) ahead of now::

    Var(mean of remaining samples)
        = sigma^2 / n^2 * sum_{i,j} min(t_i, t_j)
        = sigma^2 * [ t0 + dt * (n+1)(2n+1) / (6n) ]

which converges to ``sigma^2 * T / 3`` for large n and ``t0 = 0``.
:func:`continuous_mean_variance_factor` is kept alongside it so the two can be
compared directly, and the test suite pins the ratio at ``1/sqrt(3)``.

The estimate is then arithmetic on partially-revealed information: samples
already collected are *known*, not random. Only the remainder is uncertain,
which is why the edge decays sharply through the window.

Honest caveat
-------------
The ``T/3`` result assumes a driftless random walk with constant volatility.
Real crypto has volatility clustering and jumps. Treat the output as a
well-founded prior to be calibrated against realised outcomes, not as truth.
Every estimate carries a confidence interval derived from the sampling error of
the volatility estimate; use :mod:`kalshi_crypto.backtest` to check calibration.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .fees import DEFAULT_SCHEDULE, FeeSchedule

__all__ = [
    "SettlementWindow",
    "PartialAverage",
    "SettlementEstimate",
    "RollingVolEstimator",
    "discrete_mean_variance_factor",
    "continuous_mean_variance_factor",
    "normal_cdf",
    "estimate_settlement_probability",
    "edge_vs_market",
]


def normal_cdf(z: float) -> float:
    """Standard normal CDF via ``math.erf`` (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass(frozen=True)
class SettlementWindow:
    """Shape of the settlement average.

    Defaults match the 60-second, 1Hz average of the CF Benchmarks Real-Time
    Index described in Kalshi's crypto contract terms. **Confirm against the
    contract terms PDF before trading** -- see docs/SETTLEMENT.md, this is the
    one input every downstream number depends on.
    """

    n_samples: int = 60
    sample_interval_s: float = 1.0

    @property
    def duration_s(self) -> float:
        return self.n_samples * self.sample_interval_s


@dataclass(frozen=True)
class PartialAverage:
    """Samples already observed inside the settlement window."""

    n_done: int = 0
    sum_done: float = 0.0

    def observe(self, price: float) -> PartialAverage:
        return PartialAverage(self.n_done + 1, self.sum_done + price)

    @property
    def mean(self) -> float:
        if self.n_done == 0:
            raise ValueError("no samples observed yet")
        return self.sum_done / self.n_done


def discrete_mean_variance_factor(n_left: int, t0: float, dt: float) -> float:
    """Variance of the mean of ``n_left`` future samples, in units of sigma^2.

    Samples land at ``t0 + dt, t0 + 2*dt, ..., t0 + n_left*dt`` seconds from
    now, where ``t0`` is the lead time before the first remaining sample.

    Derivation: for Brownian motion ``Cov(W_s, W_t) = min(s, t)``, so
    ``Var(mean) = sigma^2/n^2 * sum_{i,j} min(t_i, t_j)`` and
    ``sum_{i,j=1..n} min(i, j) = n(n+1)(2n+1)/6``.
    """
    if n_left <= 0:
        return 0.0
    if t0 < 0 or dt <= 0:
        raise ValueError("t0 must be >= 0 and dt must be > 0")
    n = float(n_left)
    return t0 + dt * (n + 1.0) * (2.0 * n + 1.0) / (6.0 * n)


def continuous_mean_variance_factor(duration_s: float) -> float:
    """The ``T/3`` continuous limit, for comparison against the discrete form."""
    if duration_s < 0:
        raise ValueError("duration must be non-negative")
    return duration_s / 3.0


class RollingVolEstimator:
    """Realised per-second volatility from a trailing window of index samples.

    Returns volatility in *price* units (dollars), scaled from log returns by
    the current level, so it can be compared directly against a strike.
    """

    def __init__(self, window_s: float = 300.0, min_samples: int = 30) -> None:
        self.window_s = window_s
        self.min_samples = min_samples
        self._samples: deque[tuple[float, float]] = deque()  # (timestamp, price)

    def update(self, timestamp: float, price: float) -> None:
        if price <= 0:
            raise ValueError("price must be positive")
        if self._samples and timestamp < self._samples[-1][0]:
            raise ValueError("samples must arrive in non-decreasing time order")
        self._samples.append((timestamp, price))
        cutoff = timestamp - self.window_s
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.popleft()

    @property
    def n_returns(self) -> int:
        return max(len(self._samples) - 1, 0)

    def sigma_per_second(self) -> float | None:
        """Per-second sigma in dollars, or ``None`` if not enough data yet."""
        if self.n_returns < self.min_samples:
            return None
        sq_sum = 0.0
        total_dt = 0.0
        for (t0, p0), (t1, p1) in zip(self._samples, list(self._samples)[1:]):
            dt = t1 - t0
            if dt <= 0:
                continue
            r = math.log(p1 / p0)
            sq_sum += r * r
            total_dt += dt
        if total_dt <= 0:
            return None
        sigma_log = math.sqrt(sq_sum / total_dt)  # per sqrt(second), log units
        return sigma_log * self._samples[-1][1]

    def relative_standard_error(self) -> float:
        """Approximate relative s.e. of the sigma estimate: ``1/sqrt(2n)``."""
        n = self.n_returns
        if n <= 0:
            return float("inf")
        return 1.0 / math.sqrt(2.0 * n)


@dataclass(frozen=True)
class SettlementEstimate:
    """Model output: a calibrated probability, never a BUY label."""

    probability: float
    prob_low: float
    prob_high: float
    sigma_settlement: float
    required_remaining_mean: float | None
    n_left: int
    naive_probability: float
    resolved: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


def estimate_settlement_probability(
    *,
    current_price: float,
    strike: float,
    sigma_per_second: float,
    seconds_to_window_start: float,
    partial: PartialAverage | None = None,
    window: SettlementWindow | None = None,
    sigma_rel_se: float = 0.0,
    z: float = 1.96,
) -> SettlementEstimate:
    """Probability that the settlement average finishes strictly above ``strike``.

    Parameters
    ----------
    current_price:
        Latest index level (the BRTI mirror, *not* Binance spot).
    strike:
        Contract threshold.
    sigma_per_second:
        Per-second volatility in dollars, from :class:`RollingVolEstimator`.
    seconds_to_window_start:
        Seconds until the first *remaining* sample. Zero or negative once the
        settlement window has opened.
    partial:
        Samples already collected inside the window. These are known values and
        reduce the remaining uncertainty accordingly.
    sigma_rel_se:
        Relative standard error of ``sigma_per_second``; widens the interval.

    Returns
    -------
    SettlementEstimate
        ``resolved=True`` means every sample is in and the outcome is
        arithmetic, not probability.
    """
    window = window or SettlementWindow()
    partial = partial or PartialAverage()
    if partial.n_done > window.n_samples:
        raise ValueError("more samples observed than the window contains")
    if sigma_per_second < 0:
        raise ValueError("sigma must be non-negative")

    n_left = window.n_samples - partial.n_done
    notes: list[str] = []

    if n_left == 0:
        settled = partial.sum_done / window.n_samples
        p = 1.0 if settled > strike else 0.0
        return SettlementEstimate(
            probability=p, prob_low=p, prob_high=p, sigma_settlement=0.0,
            required_remaining_mean=None, n_left=0, naive_probability=p,
            resolved=True, notes=("all samples observed; outcome is arithmetic",),
        )

    # Mean the remaining samples must average for the contract to settle above.
    required = (window.n_samples * strike - partial.sum_done) / n_left

    # Lead time to the first remaining sample. Once inside the window the next
    # sample is at most one interval away.
    t0 = max(seconds_to_window_start, 0.0)
    if partial.n_done > 0:
        t0 = min(t0, window.sample_interval_s)

    factor = discrete_mean_variance_factor(n_left, t0, window.sample_interval_s)
    sd = sigma_per_second * math.sqrt(factor)

    def _prob(sd_: float) -> float:
        if sd_ <= 0:
            return 1.0 if current_price > required else 0.0
        return normal_cdf((current_price - required) / sd_)

    p = _prob(sd)

    # Interval from volatility estimation error. Higher sigma pushes the
    # probability toward 0.5 from either side, so the mapping is not monotone;
    # take the envelope.
    if sigma_rel_se > 0:
        lo_sigma = max(sigma_per_second * (1.0 - z * sigma_rel_se), 0.0)
        hi_sigma = sigma_per_second * (1.0 + z * sigma_rel_se)
        candidates = [
            p,
            _prob(lo_sigma * math.sqrt(factor)),
            _prob(hi_sigma * math.sqrt(factor)),
        ]
        prob_low, prob_high = min(candidates), max(candidates)
    else:
        prob_low = prob_high = p
        notes.append("no volatility uncertainty supplied; interval is degenerate")

    # The naive endpoint calculation, for comparison only. Uses the full time to
    # the close of the window rather than the average-of-path variance.
    naive_T = t0 + n_left * window.sample_interval_s
    naive_sd = sigma_per_second * math.sqrt(naive_T)
    naive_p = (
        normal_cdf((current_price - required) / naive_sd)
        if naive_sd > 0
        else (1.0 if current_price > required else 0.0)
    )

    if partial.n_done > 0:
        notes.append(f"{partial.n_done}/{window.n_samples} samples already fixed")

    return SettlementEstimate(
        probability=p, prob_low=prob_low, prob_high=prob_high, sigma_settlement=sd,
        required_remaining_mean=required, n_left=n_left, naive_probability=naive_p,
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class EdgeAssessment:
    """Whether a divergence is big enough to be worth crossing for."""

    model_p: float
    market_p: float
    raw_edge: float
    hurdle: float
    tradable: bool
    side: str | None


def edge_vs_market(
    estimate: SettlementEstimate,
    *,
    market_bid: float,
    market_ask: float,
    contracts: int = 100,
    margin: float = 0.01,
    schedule: FeeSchedule | None = None,
    series: str | None = None,
    is_taker: bool = True,
) -> EdgeAssessment:
    """Compare the model against the book, net of fees, spread, and a margin.

    Trades only when ``|model_p - market_p| > fee + half_spread + margin``. The
    hurdle is deliberately computed against the price you would actually pay,
    not the mid.
    """
    schedule = schedule or DEFAULT_SCHEDULE
    if not 0 <= market_bid <= market_ask <= 1:
        raise ValueError("require 0 <= bid <= ask <= 1")

    mid = (market_bid + market_ask) / 2.0
    half_spread = (market_ask - market_bid) / 2.0
    model_p = estimate.probability
    raw_edge = model_p - mid

    side = "yes" if raw_edge > 0 else "no"
    entry = market_ask if side == "yes" else 1.0 - market_bid
    fee = float(
        schedule.effective_fee_per_contract(
            entry, contracts, is_taker=is_taker, series=series
        )
    )
    hurdle = fee + half_spread + margin
    return EdgeAssessment(
        model_p=model_p,
        market_p=mid,
        raw_edge=raw_edge,
        hurdle=hurdle,
        tradable=abs(raw_edge) > hurdle,
        side=side if abs(raw_edge) > hurdle else None,
    )
