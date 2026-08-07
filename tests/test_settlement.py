"""Settlement-window model tests.

The headline assertion is :func:`test_settlement_sd_is_one_over_sqrt_three`:
settlement uncertainty is 1/sqrt(3) of the naive endpoint calculation. If that
ever breaks, the model has silently reverted to standard (wrong) practice.
"""

from __future__ import annotations

import math
import random
import statistics

import pytest

from kalshi_crypto.settlement import (
    PartialAverage,
    RollingVolEstimator,
    SettlementWindow,
    continuous_mean_variance_factor,
    discrete_mean_variance_factor,
    edge_vs_market,
    estimate_settlement_probability,
    normal_cdf,
)


def test_normal_cdf_basics():
    assert normal_cdf(0.0) == pytest.approx(0.5)
    assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert normal_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)


def test_discrete_factor_converges_to_T_over_3():
    """The discrete formula must reproduce sigma^2*T/3 in the continuous limit."""
    for n in (600, 6_000, 60_000):
        dt = 1.0
        discrete = discrete_mean_variance_factor(n, t0=0.0, dt=dt)
        continuous = continuous_mean_variance_factor(n * dt)
        assert discrete == pytest.approx(continuous, rel=1e-2)


def test_settlement_sd_is_one_over_sqrt_three():
    """sigma_settlement / sigma_endpoint -> 1/sqrt(3) ~= 0.577, i.e. 42% less."""
    n = 100_000
    sd_settlement = math.sqrt(discrete_mean_variance_factor(n, 0.0, 1.0))
    sd_endpoint = math.sqrt(n * 1.0)
    ratio = sd_settlement / sd_endpoint
    assert ratio == pytest.approx(1 / math.sqrt(3), rel=1e-3)
    assert ratio == pytest.approx(0.577, abs=1e-3)


def test_discrete_formula_matches_monte_carlo():
    """Validate the closed form against a simulated random walk."""
    rng = random.Random(4)
    n_left, dt, t0, sigma = 60, 1.0, 0.0, 2.0
    trials = 20_000
    means = []
    for _ in range(trials):
        price = 0.0
        elapsed = 0.0
        samples = []
        for i in range(1, n_left + 1):
            target = t0 + i * dt
            step = target - elapsed
            price += rng.gauss(0.0, sigma * math.sqrt(step))
            elapsed = target
            samples.append(price)
        means.append(statistics.fmean(samples))

    empirical_var = statistics.pvariance(means)
    predicted_var = sigma**2 * discrete_mean_variance_factor(n_left, t0, dt)
    assert empirical_var == pytest.approx(predicted_var, rel=0.05)


def test_discrete_beats_continuous_for_small_n():
    """Near the close the continuous limit is visibly wrong -- that's why we don't use it."""
    n = 3
    discrete = discrete_mean_variance_factor(n, t0=0.0, dt=1.0)
    continuous = continuous_mean_variance_factor(n * 1.0)
    assert discrete > continuous
    assert discrete / continuous > 1.4


def test_model_is_more_confident_than_naive():
    """Near-certain contracts: the settlement model assigns higher probability."""
    estimate = estimate_settlement_probability(
        current_price=64_500.0,
        strike=64_400.0,
        sigma_per_second=3.0,
        seconds_to_window_start=0.0,
    )
    assert estimate.probability > estimate.naive_probability
    assert estimate.probability > 0.9


def test_model_is_less_confident_than_naive_below_strike():
    """Symmetric: on the losing side, the model is *more* certain of the loss."""
    estimate = estimate_settlement_probability(
        current_price=64_300.0,
        strike=64_400.0,
        sigma_per_second=3.0,
        seconds_to_window_start=0.0,
    )
    assert estimate.probability < estimate.naive_probability
    assert estimate.probability < 0.1


def test_at_the_money_is_a_coin_flip():
    estimate = estimate_settlement_probability(
        current_price=64_400.0,
        strike=64_400.0,
        sigma_per_second=3.0,
        seconds_to_window_start=0.0,
    )
    assert estimate.probability == pytest.approx(0.5, abs=1e-6)


def test_partial_samples_reduce_uncertainty():
    """Observed samples are known, not random -- sd must fall as they accumulate."""
    window = SettlementWindow()
    sds = []
    for n_done in (0, 15, 30, 45, 59):
        partial = PartialAverage(n_done=n_done, sum_done=n_done * 64_400.0)
        estimate = estimate_settlement_probability(
            current_price=64_400.0,
            strike=64_400.0,
            sigma_per_second=3.0,
            seconds_to_window_start=0.0,
            partial=partial,
            window=window,
        )
        sds.append(estimate.sigma_settlement)
        assert estimate.n_left == window.n_samples - n_done
    assert sds == sorted(sds, reverse=True)
    # One sample left carries a factor of 1.0 against ~20.5 for a full window,
    # so sd falls by about 4.5x as the window fills.
    full = discrete_mean_variance_factor(60, 0.0, 1.0)
    last = discrete_mean_variance_factor(1, 0.0, 1.0)
    assert sds[-1] == pytest.approx(sds[0] * math.sqrt(last / full), rel=1e-9)
    assert sds[0] / sds[-1] == pytest.approx(4.5, abs=0.1)


def test_partial_average_locks_in_the_outcome():
    """A big enough partial average makes a late flip arithmetically impossible."""
    window = SettlementWindow()
    # 59 samples far above the strike; one sample left cannot drag the mean down.
    partial = PartialAverage(n_done=59, sum_done=59 * 65_000.0)
    estimate = estimate_settlement_probability(
        current_price=65_000.0,
        strike=64_400.0,
        sigma_per_second=3.0,
        seconds_to_window_start=0.0,
        partial=partial,
        window=window,
    )
    # Required mean for the last sample would be far below any reachable price.
    assert estimate.required_remaining_mean < 30_000.0
    assert estimate.probability > 0.999


def test_fully_observed_window_is_resolved():
    window = SettlementWindow()
    partial = PartialAverage(n_done=60, sum_done=60 * 64_500.0)
    estimate = estimate_settlement_probability(
        current_price=64_500.0, strike=64_400.0, sigma_per_second=3.0,
        seconds_to_window_start=0.0, partial=partial, window=window,
    )
    assert estimate.resolved
    assert estimate.probability == 1.0
    assert estimate.sigma_settlement == 0.0


def test_one_second_wick_does_not_flip_the_outcome():
    """A single-sample spike is one of sixty -- the 'it flipped at the last second' theory."""
    window = SettlementWindow()
    normal = [64_500.0] * 59
    wick = 60_000.0  # a violent one-second dip
    partial = PartialAverage(n_done=60, sum_done=sum(normal) + wick)
    estimate = estimate_settlement_probability(
        current_price=wick, strike=64_400.0, sigma_per_second=3.0,
        seconds_to_window_start=0.0, partial=partial, window=window,
    )
    settled_average = (sum(normal) + wick) / 60
    assert settled_average > 64_400.0
    assert estimate.probability == 1.0  # still settles YES despite the wick


def test_confidence_interval_widens_with_vol_uncertainty():
    # Near the money, where volatility uncertainty actually moves the answer.
    kwargs = dict(
        current_price=64_405.0, strike=64_400.0, sigma_per_second=3.0,
        seconds_to_window_start=0.0,
    )
    tight = estimate_settlement_probability(**kwargs, sigma_rel_se=0.0)
    loose = estimate_settlement_probability(**kwargs, sigma_rel_se=0.30)
    assert tight.prob_low == tight.prob_high
    assert loose.prob_low < loose.probability < loose.prob_high
    assert (loose.prob_high - loose.prob_low) > 0.02


def test_rejects_impossible_inputs():
    with pytest.raises(ValueError):
        estimate_settlement_probability(
            current_price=1.0, strike=1.0, sigma_per_second=-1.0,
            seconds_to_window_start=0.0,
        )
    with pytest.raises(ValueError):
        estimate_settlement_probability(
            current_price=1.0, strike=1.0, sigma_per_second=1.0,
            seconds_to_window_start=0.0,
            partial=PartialAverage(n_done=61, sum_done=61.0),
        )


class TestRollingVol:
    def test_returns_none_before_min_samples(self):
        vol = RollingVolEstimator(min_samples=30)
        for i in range(10):
            vol.update(float(i), 64_000.0)
        assert vol.sigma_per_second() is None

    def test_recovers_known_volatility(self):
        """Feed a walk with known sigma; the estimator should find it."""
        rng = random.Random(11)
        true_sigma_log = 0.00005  # per sqrt(second)
        price = 64_000.0
        vol = RollingVolEstimator(window_s=1e9, min_samples=30)
        vol.update(0.0, price)
        for t in range(1, 5000):
            price *= math.exp(rng.gauss(0.0, true_sigma_log))
            vol.update(float(t), price)
        sigma_dollars = vol.sigma_per_second()
        assert sigma_dollars is not None
        implied_log = sigma_dollars / price
        assert implied_log == pytest.approx(true_sigma_log, rel=0.10)

    def test_window_evicts_old_samples(self):
        vol = RollingVolEstimator(window_s=10.0, min_samples=2)
        for t in range(100):
            vol.update(float(t), 64_000.0 + t)
        assert vol.n_returns <= 12

    def test_standard_error_shrinks_with_samples(self):
        vol = RollingVolEstimator(window_s=1e9, min_samples=2)
        for t in range(50):
            vol.update(float(t), 64_000.0 + t)
        few = vol.relative_standard_error()
        for t in range(50, 500):
            vol.update(float(t), 64_000.0 + t)
        assert vol.relative_standard_error() < few

    def test_rejects_out_of_order_samples(self):
        vol = RollingVolEstimator()
        vol.update(10.0, 64_000.0)
        with pytest.raises(ValueError):
            vol.update(5.0, 64_000.0)


class TestEdge:
    STRIKE = 64_400.0
    SIGMA = 3.0

    def _estimate(self, p: float):
        """Build an estimate with a target probability by solving for the price.

        Bisection on the model's own output, so the helper cannot drift away
        from the implementation the way a hand-computed offset would.
        """
        low, high = self.STRIKE - 500.0, self.STRIKE + 500.0
        for _ in range(200):
            mid = (low + high) / 2.0
            estimate = estimate_settlement_probability(
                current_price=mid, strike=self.STRIKE, sigma_per_second=self.SIGMA,
                seconds_to_window_start=0.0,
            )
            if estimate.probability < p:
                low = mid
            else:
                high = mid
        estimate = estimate_settlement_probability(
            current_price=(low + high) / 2.0, strike=self.STRIKE,
            sigma_per_second=self.SIGMA, seconds_to_window_start=0.0,
        )
        assert estimate.probability == pytest.approx(p, abs=1e-6)
        return estimate

    def test_small_divergence_is_not_tradable(self):
        estimate = self._estimate(0.52)
        assessment = edge_vs_market(estimate, market_bid=0.50, market_ask=0.52)
        assert not assessment.tradable
        assert assessment.side is None

    def test_large_divergence_clears_the_hurdle(self):
        estimate = estimate_settlement_probability(
            current_price=64_600.0, strike=64_400.0, sigma_per_second=3.0,
            seconds_to_window_start=0.0,
        )
        assessment = edge_vs_market(estimate, market_bid=0.50, market_ask=0.52)
        assert assessment.model_p > 0.9
        assert assessment.tradable
        assert assessment.side == "yes"

    def test_hurdle_includes_fee_and_half_spread(self):
        estimate = self._estimate(0.60)
        wide = edge_vs_market(estimate, market_bid=0.40, market_ask=0.60)
        tight = edge_vs_market(estimate, market_bid=0.49, market_ask=0.51)
        assert wide.hurdle > tight.hurdle

    def test_maker_hurdle_is_lower_than_taker(self):
        estimate = self._estimate(0.60)
        taker = edge_vs_market(estimate, market_bid=0.50, market_ask=0.52, is_taker=True)
        maker = edge_vs_market(estimate, market_bid=0.50, market_ask=0.52, is_taker=False)
        assert maker.hurdle < taker.hurdle
