from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kalshi_crypto.tickers import build_ticker, parse_ticker


def test_parses_the_spec_example():
    t = parse_ticker("KXBTC15M-26APR221830-T105000")
    assert t.series == "KXBTC15M"
    assert t.asset == "BTC"
    assert t.duration == "15M"
    assert t.close_time == datetime(2026, 4, 22, 18, 30, tzinfo=UTC)
    assert t.strike == 105_000.0
    assert t.strike_kind == "T"


def test_window_bounds():
    t = parse_ticker("KXBTC15M-26APR221830-T105000")
    assert t.duration_seconds == 900
    assert t.open_time == datetime(2026, 4, 22, 18, 15, tzinfo=UTC)


@pytest.mark.parametrize("asset", ["BTC", "ETH", "XRP", "SOL", "DOGE"])
def test_all_parallel_series_parse(asset):
    t = parse_ticker(f"KX{asset}15M-26AUG061230-T1000")
    assert t.asset == asset


def test_decimal_strikes_for_low_priced_assets():
    t = parse_ticker("KXXRP15M-26AUG061230-T1.05")
    assert t.strike == pytest.approx(1.05)


def test_round_trip():
    raw = "KXETH15M-26AUG061245-T1900"
    assert build_ticker("ETH", parse_ticker(raw).close_time, 1900.0) == raw


def test_build_requires_timezone():
    with pytest.raises(ValueError):
        build_ticker("BTC", datetime(2026, 8, 6, 12, 30), 64_000.0)


@pytest.mark.parametrize(
    "bad",
    [
        "KXBTC15M-26APR221830",            # missing strike
        "KXBTC15M-26XXX221830-T105000",    # bad month
        "NOTASERIES-26APR221830-T105000",  # bad series
        "KXBTC15M-26APR221830-105000",     # strike without a kind letter
        "",
    ],
)
def test_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_ticker(bad)
