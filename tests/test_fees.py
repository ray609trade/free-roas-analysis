"""Fee model tests. These pin the numbers the whole architecture rests on."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from kalshi_crypto.fees import (
    DEFAULT_SCHEDULE,
    FeeSchedule,
    perp_fee,
    perp_tier_for_volume,
)


def test_maker_fee_is_zero_by_default():
    """The single most important finding in the spec: M_maker defaults to 0."""
    for price in (0.01, 0.25, 0.49, 0.50, 0.52, 0.75, 0.99):
        assert DEFAULT_SCHEDULE.maker_fee(price, 100) == Decimal("0.00")


def test_taker_fee_matches_published_formula():
    # 0.07 * 100 * 0.50 * 0.50 = 1.75 exactly, no rounding needed.
    assert DEFAULT_SCHEDULE.taker_fee(0.50, 100) == Decimal("1.75")
    # 0.07 * 100 * 0.52 * 0.48 = 1.7472 -> rounds up to 1.75
    assert DEFAULT_SCHEDULE.taker_fee(0.52, 100) == Decimal("1.75")
    # 0.07 * 100 * 0.10 * 0.90 = 0.63 exactly
    assert DEFAULT_SCHEDULE.taker_fee(0.10, 100) == Decimal("0.63")


def test_taker_fee_peaks_at_the_money():
    fees = [float(DEFAULT_SCHEDULE.taker_fee(c / 100, 1000)) for c in range(1, 100)]
    assert max(fees) == pytest.approx(float(DEFAULT_SCHEDULE.taker_fee(0.50, 1000)))


def test_rounding_is_up_and_per_order():
    """A 1-lot pays the whole cent; a 100-lot amortises it."""
    one = DEFAULT_SCHEDULE.effective_fee_per_contract(0.50, 1, is_taker=True)
    hundred = DEFAULT_SCHEDULE.effective_fee_per_contract(0.50, 100, is_taker=True)
    assert one == Decimal("0.02")  # 0.0175 raw, rounded up to a whole cent
    assert hundred == Decimal("0.0175")
    assert one > hundred  # ~14% more per contract, purely from rounding


def test_one_lot_rounds_up_to_two_cents():
    # 0.07 * 1 * 0.5 * 0.5 = 0.0175 -> ceiling to the cent is 0.02
    assert DEFAULT_SCHEDULE.taker_fee(0.50, 1) == Decimal("0.02")


def test_breakeven_swing_between_taker_and_maker():
    """The spec's headline: ~4.75 points of break-even between taking and making."""
    taker_52 = DEFAULT_SCHEDULE.breakeven_win_rate(0.52, 100, is_taker=True)
    maker_49 = DEFAULT_SCHEDULE.breakeven_win_rate(0.49, 100, is_taker=False)
    assert taker_52 == pytest.approx(0.5375, abs=1e-4)
    assert maker_49 == pytest.approx(0.4900, abs=1e-9)
    assert (taker_52 - maker_49) == pytest.approx(0.0475, abs=1e-3)


def test_breakeven_taker_at_fifty():
    assert DEFAULT_SCHEDULE.breakeven_win_rate(0.50, 100, is_taker=True) == pytest.approx(
        0.5175, abs=1e-9
    )


def test_non_standard_multiplier_override():
    """If Kalshi ever prices maker fees on a series, one dict entry covers it."""
    schedule = FeeSchedule(overrides={"KXBTC15M": (Decimal("1"), Decimal("1"))})
    assert schedule.maker_fee(0.50, 100, "KXBTC15M") > 0
    assert schedule.maker_fee(0.50, 100, "KXETH15M") == Decimal("0.00")


def test_zero_contracts_and_bounds():
    assert DEFAULT_SCHEDULE.taker_fee(0.5, 0) == Decimal("0.00")
    with pytest.raises(ValueError):
        DEFAULT_SCHEDULE.taker_fee(1.5, 10)
    with pytest.raises(ValueError):
        DEFAULT_SCHEDULE.taker_fee(0.5, -1)


def test_perp_round_trip_consumes_an_average_fifteen_minute_move():
    """24bp round trip vs a ~0.2% typical 15-minute BTC move."""
    tier = perp_tier_for_volume(0.0)
    round_trip_bps = 2 * tier.taker_bps
    assert round_trip_bps == 24.0

    notional = 100_000.0
    cost = 2 * perp_fee(notional, is_taker=True)
    typical_move_pct = 0.002  # daily vol / sqrt(96)
    assert cost / notional >= typical_move_pct * 0.9

    assert perp_tier_for_volume(500_000.0).taker_bps < tier.taker_bps


def test_perp_maker_is_not_free_unlike_events():
    """Perps charge makers; event contracts do not. That asymmetry is the point."""
    assert perp_fee(10_000.0, is_taker=False) > 0
    assert DEFAULT_SCHEDULE.maker_fee(0.5, 10_000) == Decimal("0.00")


def test_round_trip_cost_adds_both_legs():
    cost = DEFAULT_SCHEDULE.round_trip_cost(
        0.50, 0.55, 100, entry_taker=True, exit_taker=True
    )
    expected = DEFAULT_SCHEDULE.taker_fee(0.50, 100) + DEFAULT_SCHEDULE.taker_fee(0.55, 100)
    assert cost == expected
    assert math.isclose(float(cost), 3.48, abs_tol=0.02)
