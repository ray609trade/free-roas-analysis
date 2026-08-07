"""BRTI mirror tests.

The load-bearing one is :func:`test_binance_is_not_a_constituent`: mirroring the
wrong price source is the silent failure mode from spec section 3.
"""

from __future__ import annotations

import pytest

from kalshi_crypto.marketdata import (
    BRTI_CONSTITUENTS,
    BasisTracker,
    BookTop,
    BRTIMirror,
    ReplaySource,
    TradePrint,
    build_source,
)


def top(exchange: str, bid: float, ask: float, ts: float = 100.0, size: float = 1.0) -> BookTop:
    return BookTop(exchange, "BTC-USD", bid, ask, size, size, ts)


class TestConstituents:
    def test_binance_is_not_a_constituent(self):
        """BRTI does not include Binance. Feeding it in would inject basis error."""
        for asset, exchanges in BRTI_CONSTITUENTS.items():
            assert "binance" not in exchanges, asset

    def test_build_source_refuses_binance_with_an_explanation(self):
        with pytest.raises(KeyError, match="Binance"):
            build_source("binance", ["BTCUSDT"])

    def test_non_constituent_quotes_are_ignored(self):
        mirror = BRTIMirror("BTC")
        mirror.on_book(top("binance", 64_000.0, 64_001.0))
        assert mirror.sample(now=100.0) is None

    def test_unknown_asset_requires_explicit_constituents(self):
        with pytest.raises(KeyError):
            BRTIMirror("PEPE")
        assert BRTIMirror("PEPE", constituents=("coinbase",)) is not None


class TestComposite:
    def test_equal_weight_before_any_volume(self):
        mirror = BRTIMirror("BTC")
        mirror.on_book(top("coinbase", 64_000.0, 64_002.0))
        mirror.on_book(top("kraken", 64_010.0, 64_012.0))
        sample = mirror.sample(now=100.0)
        assert sample is not None
        assert sample.price == pytest.approx(64_006.0)
        assert sample.n_constituents == 2

    def test_volume_weighting_favours_the_active_venue(self):
        mirror = BRTIMirror("BTC")
        mirror.on_book(top("coinbase", 64_000.0, 64_000.0))
        mirror.on_book(top("kraken", 65_000.0, 65_000.0))
        mirror.on_trade(TradePrint("coinbase", "BTC-USD", 64_000.0, 9.0, "buy", 100.0))
        mirror.on_trade(TradePrint("kraken", "BTC-USD", 65_000.0, 1.0, "buy", 100.0))
        sample = mirror.sample(now=100.0)
        assert sample is not None
        # Coinbase carries ~90% of notional, so the composite sits near it.
        assert 64_000.0 < sample.price < 64_200.0

    def test_stale_quotes_are_dropped(self):
        mirror = BRTIMirror("BTC", stale_after_s=5.0)
        mirror.on_book(top("coinbase", 64_000.0, 64_002.0, ts=100.0))
        assert mirror.sample(now=104.0) is not None
        assert mirror.sample(now=110.0) is None

    def test_crossed_books_are_rejected(self):
        mirror = BRTIMirror("BTC")
        mirror.on_book(top("coinbase", 64_005.0, 64_000.0))  # crossed
        assert mirror.sample(now=100.0) is None

    def test_thin_coverage_is_flagged_stale(self):
        mirror = BRTIMirror("BTC")
        mirror.on_book(top("coinbase", 64_000.0, 64_002.0))
        sample = mirror.sample(now=100.0)
        assert sample is not None and sample.is_stale

    def test_size_weighted_mid_leans_toward_the_thin_side(self):
        heavy_bid = BookTop("coinbase", "BTC-USD", 64_000.0, 64_010.0, 100.0, 1.0, 0.0)
        assert heavy_bid.size_weighted_mid > heavy_bid.mid

    def test_volume_window_evicts_old_trades(self):
        mirror = BRTIMirror("BTC", volume_window_s=10.0)
        mirror.on_book(top("coinbase", 64_000.0, 64_000.0, ts=200.0))
        mirror.on_book(top("kraken", 65_000.0, 65_000.0, ts=200.0))
        mirror.on_trade(TradePrint("coinbase", "BTC-USD", 64_000.0, 100.0, "buy", 100.0))
        # A much later kraken trade evicts the stale coinbase volume.
        mirror.on_trade(TradePrint("kraken", "BTC-USD", 65_000.0, 1.0, "buy", 200.0))
        sample = mirror.sample(now=200.0)
        assert sample is not None
        assert sample.price == pytest.approx(65_000.0)


class TestBasisTracker:
    def test_reports_error_distribution(self):
        tracker = BasisTracker()
        for i in range(100):
            offset = 1.0 if i % 2 else -1.0
            tracker.add(64_000.0 + offset, 64_000.0)
        report = tracker.report()
        assert report["n"] == 100
        assert abs(report["mean_bps"]) < 1e-6
        assert report["stdev_bps"] > 0

    def test_edge_is_safe_requires_enough_samples(self):
        tracker = BasisTracker()
        tracker.add(64_000.0, 64_000.0)
        assert not tracker.edge_is_safe(edge_bps=100.0)

    def test_large_basis_noise_invalidates_a_small_edge(self):
        tracker = BasisTracker()
        for i in range(200):
            tracker.add(64_000.0 + (10.0 if i % 2 else -10.0), 64_000.0)
        # ~1.6bp of noise: a 1bp edge is not safe, a 20bp edge is.
        assert not tracker.edge_is_safe(edge_bps=1.0)
        assert tracker.edge_is_safe(edge_bps=20.0)


async def test_replay_source_emits_in_order():
    events = [top("coinbase", 64_000.0 + i, 64_001.0 + i, ts=float(i)) for i in range(5)]
    seen = [event async for event in ReplaySource(events).stream()]
    assert [e.timestamp for e in seen] == [0.0, 1.0, 2.0, 3.0, 4.0]


async def test_mirror_consumes_a_replay_end_to_end():
    """The pipeline runs with no network at all -- feed, mirror, sample."""
    events = [
        top("coinbase", 64_000.0 + i, 64_002.0 + i, ts=float(i)) for i in range(10)
    ] + [top("kraken", 64_001.0 + i, 64_003.0 + i, ts=float(i)) for i in range(10)]
    events.sort(key=lambda e: e.timestamp)

    mirror = BRTIMirror("BTC")
    samples = []
    async for event in ReplaySource(events).stream():
        mirror.on_book(event)
        sample = mirror.sample(now=event.timestamp)
        if sample is not None:
            samples.append(sample)

    assert len(samples) >= 10
    assert samples[-1].price > samples[0].price
    assert samples[-1].n_constituents == 2
