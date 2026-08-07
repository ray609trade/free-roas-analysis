"""Tests for venue routing, the up/down model, paper trading, and the live engine."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from kalshi_crypto.config import LiveTradingNotEnabled, load_settings
from kalshi_crypto.live import LiveEngine, SyntheticFeed, render_table
from kalshi_crypto.paper import PaperBroker
from kalshi_crypto.signals import CalibrationTracker, DirectionalModel, FeatureEngine
from kalshi_crypto.signals.features import FeatureSet
from kalshi_crypto.venues import (
    PRICING_AVERAGE,
    PRICING_ENDPOINT,
    assert_feed_matches,
    resolve,
    supported_pairs,
)
from kalshi_crypto.windows import WindowClock


class TestPaperOnlySafety:
    def test_orders_are_blocked_by_default(self):
        """The default configuration cannot place an exchange order at all."""
        settings = load_settings({})
        assert settings.paper_only
        with pytest.raises(LiveTradingNotEnabled, match="PAPER_ONLY"):
            settings.require_order_permission()

    def test_even_demo_orders_are_blocked_while_paper_only(self):
        settings = load_settings({"KALSHI_ENV": "demo"})
        with pytest.raises(LiveTradingNotEnabled):
            settings.require_order_permission()

    def test_disabling_paper_only_still_requires_the_prod_opt_ins(self):
        settings = load_settings({"KALSHI_PAPER_ONLY": "0", "KALSHI_ENV": "prod"})
        with pytest.raises(LiveTradingNotEnabled, match="production"):
            settings.require_order_permission()

    def test_broker_holds_no_credentials_or_network_client(self):
        broker = PaperBroker()
        forbidden = {"api_key", "api_key_id", "private_key", "session", "client", "rest"}
        assert not (set(vars(broker)) & forbidden)


class TestVenueRouting:
    def test_kalshi_uses_the_index_basket_not_one_exchange(self):
        ctx = resolve("BTC", "kalshi", "binary_15m")
        assert ctx.signal_source == "brti_mirror"
        assert ctx.pricing_mode == PRICING_AVERAGE
        assert ctx.uses_average_settlement

    def test_coinbase_uses_its_own_book_and_endpoint_pricing(self):
        ctx = resolve("BTC", "coinbase", "perp")
        assert ctx.signal_source == "coinbase"
        assert ctx.pricing_mode == PRICING_ENDPOINT
        assert not ctx.uses_average_settlement

    def test_instrument_symbol_matches_the_venue_convention(self):
        assert resolve("ETH", "coinbase", "spot").instrument.symbol == "ETH-USD"
        assert resolve("ETH", "crypto_com", "perp").instrument.symbol == "ETH_USD"
        assert resolve("ETH", "kalshi", "binary_15m").instrument.symbol == "KXETH15M"

    def test_invalid_combinations_raise_rather_than_fall_back(self):
        with pytest.raises(ValueError):
            resolve("BTC", "kalshi", "perp")       # Kalshi has no perps here
        with pytest.raises(KeyError):
            resolve("BTC", "binance", "perp")      # not a configured venue
        with pytest.raises(ValueError):
            resolve("DOGE", "coinbase", "spot")    # not configured for this asset

    def test_feed_mismatch_is_rejected(self):
        """Predicting one venue and trading another must not be possible."""
        ctx = resolve("BTC", "kalshi", "binary_15m")
        assert_feed_matches(ctx, "brti_mirror")
        with pytest.raises(ValueError, match="basis bet"):
            assert_feed_matches(ctx, "coinbase")

    def test_all_three_assets_route_on_every_venue(self):
        pairs = supported_pairs()
        for asset in ("BTC", "ETH", "XRP"):
            assert any(p[0] == asset and p[1] == "kalshi" for p in pairs)
            assert any(p[0] == asset and p[1] == "coinbase" for p in pairs)


class TestWindowClock:
    def test_windows_align_to_quarter_hours(self):
        clock = WindowClock()
        now = datetime(2026, 8, 7, 12, 7, 30, tzinfo=UTC)
        window, closed = clock.update(now, 64_000.0)
        assert window.open_time == datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        assert window.close_time == datetime(2026, 8, 7, 12, 15, tzinfo=UTC)
        assert closed is None

    def test_strike_is_frozen_at_the_open(self):
        """Revising the strike from a later price would leak the future."""
        clock = WindowClock()
        base = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        clock.update(base, 64_000.0)
        clock.update(base + timedelta(seconds=300), 65_000.0)
        assert clock.current.strike == 64_000.0

    def test_rollover_reports_the_closed_window(self):
        clock = WindowClock()
        base = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        clock.update(base, 64_000.0)
        window, closed = clock.update(base + timedelta(minutes=16), 64_500.0)
        assert closed is not None
        assert closed.strike == 64_000.0
        assert window.open_time == datetime(2026, 8, 7, 12, 15, tzinfo=UTC)


class TestDirectionalModel:
    def _features(self, **kw) -> FeatureSet:
        base = dict(n_samples=100, realized_vol_per_s=5.0, vol_ratio=1.0)
        base.update(kw)
        return FeatureSet(**base)

    def test_at_the_money_with_no_signal_is_a_coin_flip(self):
        model = DirectionalModel()
        q = model.quote(
            context=resolve("BTC", "kalshi", "binary_15m"),
            price=64_000.0, strike=64_000.0, seconds_to_close=600.0,
            sigma_per_second=3.0, features=self._features(),
            now=datetime.now(UTC),
        )
        assert q.prob_up == pytest.approx(0.5, abs=1e-9)
        assert q.prob_down == pytest.approx(0.5, abs=1e-9)
        assert not q.tradable  # correctly refuses to call a coin flip

    def test_average_settlement_is_more_confident_than_endpoint(self):
        """Same inputs, different venue rule -> the ~1/sqrt(3) difference."""
        model = DirectionalModel()
        common = dict(
            price=64_100.0, strike=64_000.0, seconds_to_close=60.0,
            sigma_per_second=3.0, features=self._features(), now=datetime.now(UTC),
        )
        kalshi = model.quote(context=resolve("BTC", "kalshi", "binary_15m"), **common)
        coinbase = model.quote(context=resolve("BTC", "coinbase", "perp"), **common)
        assert kalshi.sigma_effective < coinbase.sigma_effective
        assert kalshi.prob_up > coinbase.prob_up
        ratio = kalshi.sigma_effective / coinbase.sigma_effective
        assert ratio == pytest.approx(1 / math.sqrt(3), abs=0.05)

    def test_bullish_flow_raises_the_up_probability(self):
        model = DirectionalModel()
        common = dict(
            context=resolve("BTC", "coinbase", "perp"),
            price=64_000.0, strike=64_000.0, seconds_to_close=300.0,
            sigma_per_second=3.0, now=datetime.now(UTC),
        )
        flat = model.quote(features=self._features(), **common)
        bull = model.quote(
            features=self._features(flow_imbalance_30s=1.0, book_imbalance=1.0), **common
        )
        bear = model.quote(
            features=self._features(flow_imbalance_30s=-1.0, book_imbalance=-1.0), **common
        )
        assert bear.prob_up < flat.prob_up < bull.prob_up

    def test_drift_is_hard_capped_relative_to_uncertainty(self):
        """A bounded disagreement with the market bounds how wrong we can be."""
        model = DirectionalModel(max_drift_sigmas=0.35)
        q = model.quote(
            context=resolve("BTC", "coinbase", "perp"),
            price=64_000.0, strike=64_000.0, seconds_to_close=600.0,
            sigma_per_second=3.0,
            features=self._features(
                flow_imbalance_30s=1.0, flow_imbalance_2m=1.0,
                book_imbalance=1.0, momentum=1.0,
            ),
            now=datetime.now(UTC),
        )
        assert abs(q.drift) <= 0.35 * q.sigma_effective + 1e-9
        assert q.prob_up < 0.65  # cannot run away from the coin flip

    def test_rsi_ships_at_zero_weight(self):
        """Included as a control so it can be measured, not trusted."""
        model = DirectionalModel()
        assert model.weights.rsi == 0.0
        common = dict(
            context=resolve("BTC", "coinbase", "perp"),
            price=64_000.0, strike=64_000.0, seconds_to_close=300.0,
            sigma_per_second=3.0, now=datetime.now(UTC),
        )
        neutral = model.quote(features=self._features(rsi=50.0), **common)
        overbought = model.quote(features=self._features(rsi=90.0), **common)
        assert neutral.prob_up == pytest.approx(overbought.prob_up)

    def test_high_vol_regime_blocks_trading(self):
        model = DirectionalModel()
        q = model.quote(
            context=resolve("BTC", "coinbase", "perp"),
            price=64_500.0, strike=64_000.0, seconds_to_close=300.0,
            sigma_per_second=3.0, features=self._features(vol_ratio=3.0),
            now=datetime.now(UTC),
        )
        assert not q.tradable
        assert "vol" in q.reason

    def test_macro_blackout_blocks_trading(self):
        now = datetime.now(UTC)
        model = DirectionalModel(blackout_until=now + timedelta(minutes=20))
        q = model.quote(
            context=resolve("BTC", "coinbase", "perp"),
            price=64_500.0, strike=64_000.0, seconds_to_close=300.0,
            sigma_per_second=3.0, features=self._features(), now=now,
        )
        assert not q.tradable
        assert "blackout" in q.reason

    def test_cold_start_refuses_to_quote_tradably(self):
        model = DirectionalModel()
        q = model.quote(
            context=resolve("BTC", "coinbase", "perp"),
            price=64_500.0, strike=64_000.0, seconds_to_close=300.0,
            sigma_per_second=3.0, features=self._features(n_samples=3),
            now=datetime.now(UTC),
        )
        assert not q.tradable
        assert "warming up" in q.reason

    def test_probabilities_are_complementary_and_bounded(self):
        model = DirectionalModel()
        for offset in (-500.0, 0.0, 500.0):
            q = model.quote(
                context=resolve("BTC", "kalshi", "binary_15m"),
                price=64_000.0 + offset, strike=64_000.0, seconds_to_close=300.0,
                sigma_per_second=3.0, features=self._features(),
                now=datetime.now(UTC),
            )
            assert 0.0 <= q.prob_up <= 1.0
            assert q.prob_up + q.prob_down == pytest.approx(1.0)
            assert q.prob_low <= q.prob_up <= q.prob_high
            assert 0 <= q.cents_up() <= 100


class TestPaperBroker:
    def test_winning_up_position_pays_out(self):
        broker = PaperBroker(starting_bankroll=1000.0)
        now = datetime.now(UTC)
        broker.buy(instrument="kalshi:KXBTC15M", window_label="12:00-12:15Z",
                   side="up", contracts=100, price=0.60, timestamp=now)
        pnl = broker.settle("kalshi:KXBTC15M", "12:00-12:15Z", outcome_up=True)
        # Paid $60 + $1.68 fee, received $100.
        assert float(pnl) == pytest.approx(100 - 60 - 1.68, abs=0.01)
        assert broker.bankroll > 1000.0

    def test_losing_position_loses_the_stake(self):
        broker = PaperBroker()
        broker.buy(instrument="i", window_label="w", side="up", contracts=100,
                   price=0.60, timestamp=datetime.now(UTC))
        pnl = broker.settle("i", "w", outcome_up=False)
        assert float(pnl) < -60.0

    def test_down_side_pays_when_price_falls(self):
        broker = PaperBroker()
        broker.buy(instrument="i", window_label="w", side="down", contracts=100,
                   price=0.40, timestamp=datetime.now(UTC))
        assert float(broker.settle("i", "w", outcome_up=False)) > 0

    def test_fees_are_charged_on_every_fill(self):
        broker = PaperBroker()
        broker.buy(instrument="i", window_label="w", side="up", contracts=100,
                   price=0.50, timestamp=datetime.now(UTC))
        assert broker.total_fees > 0

    def test_position_cap_rejects_oversized_orders(self):
        broker = PaperBroker(max_contracts_per_window=100)
        now = datetime.now(UTC)
        assert broker.buy(instrument="i", window_label="w", side="up",
                          contracts=100, price=0.5, timestamp=now) is not None
        assert broker.buy(instrument="i", window_label="w", side="up",
                          contracts=100, price=0.5, timestamp=now) is None

    def test_rejects_nonsense_prices(self):
        broker = PaperBroker()
        now = datetime.now(UTC)
        assert broker.buy(instrument="i", window_label="w", side="up",
                          contracts=100, price=0.0, timestamp=now) is None
        assert broker.buy(instrument="i", window_label="w", side="up",
                          contracts=100, price=1.0, timestamp=now) is None


class TestCalibration:
    def test_perfect_predictions_score_zero_brier(self):
        t = CalibrationTracker()
        for i in range(20):
            up = i % 2 == 0
            t.record("BTC", "kalshi", f"w{i}", 1.0 if up else 0.0)
            t.resolve(f"w{i}", up)
        assert t.brier() == pytest.approx(0.0)
        assert t.brier_skill() == pytest.approx(1.0)
        assert t.accuracy() == pytest.approx(1.0)

    def test_always_fifty_percent_scores_the_baseline(self):
        t = CalibrationTracker()
        for i in range(50):
            t.record("BTC", "kalshi", f"w{i}", 0.5)
            t.resolve(f"w{i}", i % 2 == 0)
        assert t.brier() == pytest.approx(0.25)
        assert t.brier_skill() == pytest.approx(0.0)

    def test_backwards_model_scores_worse_than_a_coin(self):
        t = CalibrationTracker()
        for i in range(20):
            up = i % 2 == 0
            t.record("BTC", "kalshi", f"w{i}", 0.0 if up else 1.0)
            t.resolve(f"w{i}", up)
        assert t.brier() > 0.25
        assert t.brier_skill() < 0

    def test_comparison_against_the_market_is_what_matters(self):
        t = CalibrationTracker()
        for i in range(40):
            up = i % 2 == 0
            # We say 0.9/0.1 correctly; the market says a flat 0.5.
            t.record("BTC", "kalshi", f"w{i}", 0.9 if up else 0.1, market_prob=0.5)
            t.resolve(f"w{i}", up)
        assert t.beats_market() > 0

    def test_reliability_buckets_predicted_against_actual(self):
        t = CalibrationTracker()
        for i in range(100):
            t.record("BTC", "kalshi", f"w{i}", 0.7)
            t.resolve(f"w{i}", i % 10 < 7)  # actually happens 70% of the time
        rows = t.reliability()
        row = next(r for r in rows if r["n"] > 0)
        assert row["predicted"] == pytest.approx(0.7)
        assert row["actual"] == pytest.approx(0.7, abs=0.01)

    def test_round_trip_through_disk(self, tmp_path):
        t = CalibrationTracker()
        t.record("BTC", "kalshi", "w1", 0.6, market_prob=0.55)
        t.resolve("w1", True)
        path = tmp_path / "calib.json"
        t.save(path)
        loaded = CalibrationTracker.load(path)
        assert loaded.brier() == pytest.approx(t.brier())


class TestFeatureEngine:
    def test_flow_imbalance_tracks_aggressor_side(self):
        fe = FeatureEngine()
        for i in range(50):
            fe.on_price(float(i), 64_000.0 + i)
        for i in range(10):
            fe.on_trade(50.0 + i, 1000.0, is_buy_aggressor=True)
        assert fe.snapshot(60.0).flow_imbalance_30s == pytest.approx(1.0)

    def test_flow_imbalance_decays_out_of_the_window(self):
        fe = FeatureEngine()
        for i in range(50):
            fe.on_price(float(i), 64_000.0)
        fe.on_trade(10.0, 1000.0, is_buy_aggressor=True)
        assert fe.snapshot(200.0).flow_imbalance_30s == 0.0

    def test_book_imbalance_is_clamped(self):
        fe = FeatureEngine()
        fe.on_book_imbalance(5.0)
        assert fe.snapshot(1.0).book_imbalance == 1.0

    def test_rsi_rises_on_a_monotonic_advance(self):
        fe = FeatureEngine()
        for i in range(60):
            fe.on_price(float(i), 64_000.0 + i * 10)
        assert fe.snapshot(60.0).rsi > 90

    def test_not_ready_before_enough_samples(self):
        fe = FeatureEngine()
        fe.on_price(1.0, 64_000.0)
        assert not fe.snapshot(1.0).is_ready


class TestLiveEngine:
    def _engine(self, venue="kalshi", kind="binary_15m") -> LiveEngine:
        return LiveEngine(
            contexts=[resolve(a, venue, kind) for a in ("BTC", "ETH", "XRP")],
            broker=PaperBroker(starting_bankroll=1000.0),
        )

    def test_quotes_all_three_assets_after_warmup(self):
        engine = self._engine()
        base = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        prices = {"BTC": 64_000.0, "ETH": 1_900.0, "XRP": 1.05}
        for i in range(120):
            now = base + timedelta(seconds=i)
            for asset, p in prices.items():
                engine.on_price(asset, p * (1 + 0.00002 * i), now)
        quotes = engine.current_quotes()
        assert {q.asset for q in quotes} == {"BTC", "ETH", "XRP"}
        for q in quotes:
            assert 0.0 <= q.prob_up <= 1.0
            assert q.venue == "kalshi"

    def test_window_rollover_settles_and_scores(self):
        engine = self._engine()
        base = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        # Rising through the first window, then into the next.
        for i in range(0, 1100, 5):
            now = base + timedelta(seconds=i)
            engine.on_price("BTC", 64_000.0 * (1 + 0.000004 * i), now)
        assert engine.results, "a window should have closed and resolved"
        result = engine.results[0]
        assert result.asset == "BTC"
        assert result.outcome_up is True  # price rose across the window

    def test_endpoint_venue_settles_on_last_price_not_average(self):
        engine = self._engine(venue="coinbase", kind="perp")
        base = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        for i in range(0, 1000, 5):
            engine.on_price("BTC", 64_000.0 + i, base + timedelta(seconds=i))
        assert engine.results
        assert engine.results[0].outcome_up is True

    def test_dashboard_renders_and_flags_paper_mode(self):
        engine = self._engine()
        base = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        for i in range(120):
            engine.on_price("BTC", 64_000.0, base + timedelta(seconds=i))
        out = render_table(engine, base + timedelta(seconds=120))
        assert "PAPER MODE" in out
        assert "no real funds" in out
        assert "BTC" in out

    def test_no_trade_mode_places_nothing(self):
        engine = self._engine()
        engine.auto_trade = False
        base = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        for i in range(0, 900, 5):
            engine.on_price("BTC", 64_000.0 + i * 2, base + timedelta(seconds=i))
        assert engine.broker.fills == []

    def test_at_most_one_paper_position_per_window(self):
        engine = self._engine(venue="coinbase", kind="perp")
        base = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        for i in range(0, 850, 5):
            engine.on_price("BTC", 64_000.0 + i * 3, base + timedelta(seconds=i))
        windows = [f.window_label for f in engine.broker.fills]
        assert len(windows) == len(set(windows))


async def test_synthetic_feed_drives_the_engine_offline():
    """The whole pipeline runs with no network: this is the `kxc replay` path."""
    engine = LiveEngine(contexts=[resolve("BTC", "kalshi", "binary_15m")])
    feed = SyntheticFeed(
        assets=("BTC",), seed=1, speed=0.0, duration_s=2000.0, tick_seconds=5.0,
        start=datetime(2026, 8, 7, 12, 0, tzinfo=UTC),
    )
    ticks = 0
    async for tick in feed.stream():
        engine.on_price(tick.asset, tick.price, tick.timestamp)
        ticks += 1
    assert ticks > 300
    assert engine.current_quotes()
    assert engine.results, "windows should have closed and been scored"


class TestLockedCall:
    """The call must be committed early, while the outcome is still open."""

    def _engine(self, **kw) -> LiveEngine:
        from kalshi_crypto.live import LiveEngine as LE

        return LE(contexts=[resolve("BTC", "coinbase", "spot")], auto_trade=False, **kw)

    def _feed(self, engine, *, minutes: float, drift_per_s: float = 0.0) -> datetime:
        base = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        last = base
        for i in range(0, int(minutes * 60), 2):
            last = base + timedelta(seconds=i)
            engine.on_price("BTC", 64_000.0 + drift_per_s * i, last)
        return last

    def test_no_call_before_the_band_opens(self):
        engine = self._engine(call_after_s=240.0, call_deadline_s=540.0)
        self._feed(engine, minutes=3.0, drift_per_s=2.0)
        assert engine.current_calls() == []

    def test_call_locks_inside_the_band(self):
        engine = self._engine(call_after_s=240.0, call_deadline_s=540.0)
        self._feed(engine, minutes=8.0, drift_per_s=2.0)
        calls = engine.current_calls()
        assert len(calls) == 1
        call = calls[0]
        assert call.side == "UP"                     # price rose all window
        assert 4.0 <= call.minutes_into_window <= 9.0

    def test_call_is_forced_by_the_deadline_even_when_uncertain(self):
        """A flat market never clears the confidence gate; commit anyway."""
        engine = self._engine(call_after_s=240.0, call_deadline_s=540.0)
        self._feed(engine, minutes=10.0, drift_per_s=0.0)
        calls = engine.current_calls()
        assert len(calls) == 1
        assert calls[0].minutes_into_window <= 9.5
        assert calls[0].side in ("UP", "DOWN")

    def test_call_does_not_change_once_locked(self):
        """Revising until the bell would make hindsight look like foresight."""
        engine = self._engine(call_after_s=240.0, call_deadline_s=540.0)
        self._feed(engine, minutes=6.0, drift_per_s=3.0)
        locked = engine.current_calls()[0]
        # Now slam the price the other way for the rest of the window.
        base = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
        for i in range(360, 880, 2):
            engine.on_price("BTC", 64_000.0 - i * 5.0, base + timedelta(seconds=i))
        still = engine.current_calls()[0]
        assert still.side == locked.side
        assert still.prob_up == locked.prob_up

    def test_completed_calls_are_scored_against_the_outcome(self):
        engine = self._engine(call_after_s=240.0, call_deadline_s=540.0)
        self._feed(engine, minutes=17.0, drift_per_s=2.0)
        assert engine.calls, "the window should have closed and scored the call"
        call, outcome_up = engine.calls[0]
        assert outcome_up is True
        assert call.was_right(outcome_up)
        assert engine.call_accuracy == 1.0

    def test_a_fresh_call_is_made_each_window(self):
        engine = self._engine(call_after_s=240.0, call_deadline_s=540.0)
        self._feed(engine, minutes=32.0, drift_per_s=1.0)
        assert len(engine.calls) >= 1
        windows = [c.window_label for c, _ in engine.calls]
        assert len(windows) == len(set(windows))

    def test_prices_view_renders(self):
        from kalshi_crypto.live import render_prices

        engine = self._engine()
        last = self._feed(engine, minutes=8.0, drift_per_s=2.0)
        out = render_prices(engine, last)
        assert "BTC" in out and "UP/DOWN CALL" in out
