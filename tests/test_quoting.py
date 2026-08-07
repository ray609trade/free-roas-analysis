"""Maker quoting engine tests (spec section 4.2)."""

from __future__ import annotations

import pytest

from kalshi_crypto.config import LiveTradingNotEnabled, Settings, load_settings
from kalshi_crypto.quoting import InventoryPolicy, InventoryState, QuotingEngine
from kalshi_crypto.settlement import estimate_settlement_probability


def engine(**kwargs) -> QuotingEngine:
    return QuotingEngine(settings=load_settings({}), **kwargs)


class TestQuotePlacement:
    def test_posts_both_sides_around_fair_value(self):
        plan = engine().plan(
            fair_value=0.50, seconds_to_close=600.0,
            market_bid_cents=48, market_ask_cents=52,
        )
        assert len(plan.quotes) == 2
        bid = next(q for q in plan.quotes if q.is_buy)
        ask = next(q for q in plan.quotes if not q.is_buy)
        assert bid.price_cents < 50 < ask.price_cents

    def test_never_quotes_inside_half_the_market_spread(self):
        """k >= half the current spread, so we do not cross by accident."""
        plan = engine(base_half_width_cents=1.0).plan(
            fair_value=0.50, seconds_to_close=600.0,
            market_bid_cents=40, market_ask_cents=60,
        )
        bid = next(q for q in plan.quotes if q.is_buy)
        ask = next(q for q in plan.quotes if not q.is_buy)
        assert ask.price_cents - bid.price_cents >= 20

    def test_widens_as_the_close_approaches(self):
        """Adverse selection rises sharply near the close, so k must grow."""
        eng = engine()
        far = eng.half_width(600.0, market_spread_cents=2)
        near = eng.half_width(60.0, market_spread_cents=2)
        closer = eng.half_width(5.0, market_spread_cents=2)
        assert far < near < closer

    def test_half_width_is_capped(self):
        eng = engine(max_half_width_cents=6.0)
        assert eng.half_width(1.0, market_spread_cents=40) == 6.0

    def test_quotes_stay_inside_one_to_ninety_nine(self):
        for fair in (0.005, 0.995):
            plan = engine().plan(
                fair_value=fair, seconds_to_close=600.0,
                market_bid_cents=None, market_ask_cents=None,
            )
            for quote in plan.quotes:
                assert 1 <= quote.price_cents <= 99

    def test_rejects_a_fair_value_outside_zero_to_one(self):
        with pytest.raises(ValueError):
            engine().plan(
                fair_value=1.4, seconds_to_close=600.0,
                market_bid_cents=None, market_ask_cents=None,
            )


class TestInventory:
    def test_long_inventory_skews_quotes_down(self):
        flat = engine().plan(
            fair_value=0.50, seconds_to_close=600.0,
            market_bid_cents=48, market_ask_cents=52,
        )
        eng = engine()
        eng.inventory.position = 250
        long = eng.plan(
            fair_value=0.50, seconds_to_close=600.0,
            market_bid_cents=48, market_ask_cents=52,
        )
        flat_bid = next(q.price_cents for q in flat.quotes if q.is_buy)
        long_bid = next(q.price_cents for q in long.quotes if q.is_buy)
        assert long_bid < flat_bid  # less eager to buy more

    def test_stops_adding_on_the_long_side_at_the_limit(self):
        eng = engine(policy=InventoryPolicy(max_position=100, flatten_threshold=1_000))
        eng.inventory.position = 100
        plan = eng.plan(
            fair_value=0.50, seconds_to_close=600.0,
            market_bid_cents=48, market_ask_cents=52,
        )
        assert all(not q.is_buy for q in plan.quotes)

    def test_hard_flat_past_the_threshold(self):
        eng = engine(policy=InventoryPolicy(max_position=500, flatten_threshold=400))
        eng.inventory.position = 450
        plan = eng.plan(
            fair_value=0.50, seconds_to_close=600.0,
            market_bid_cents=48, market_ask_cents=52,
        )
        assert plan.is_pulled
        assert plan.flatten_size == -450
        assert "flatten" in plan.reason

    def test_adverse_selection_shows_up_as_negative_fill_edge(self):
        """Fills arriving after the market moved against us must be visible."""
        state = InventoryState()
        # Bought at 50c when fair had already dropped to 45c, repeatedly.
        for _ in range(10):
            state.on_fill(is_buy=True, size=100, price=0.50, fair_at_fill=0.45)
        assert state.mean_fill_edge is not None
        assert state.mean_fill_edge < 0

    def test_good_fills_show_positive_edge(self):
        state = InventoryState()
        for _ in range(10):
            state.on_fill(is_buy=True, size=100, price=0.48, fair_at_fill=0.50)
        assert state.mean_fill_edge == pytest.approx(0.02)

    def test_position_tracks_fills(self):
        state = InventoryState()
        state.on_fill(is_buy=True, size=100, price=0.5, fair_at_fill=0.5)
        state.on_fill(is_buy=False, size=30, price=0.5, fair_at_fill=0.5)
        assert state.position == 70


class TestSettlementWindowBehaviour:
    def test_pulls_quotes_inside_the_settlement_window(self):
        plan = engine().plan(
            fair_value=0.50, seconds_to_close=30.0,
            market_bid_cents=48, market_ask_cents=52,
        )
        assert plan.is_pulled
        assert "settlement window" in plan.reason

    def test_keeps_quoting_inside_the_window_on_a_firm_model_read(self):
        estimate = estimate_settlement_probability(
            current_price=64_600.0, strike=64_400.0, sigma_per_second=3.0,
            seconds_to_window_start=0.0,
        )
        assert estimate.probability > 0.9  # firm read, far from a 50c market
        plan = engine().plan(
            fair_value=estimate.probability, seconds_to_close=30.0,
            market_bid_cents=48, market_ask_cents=52, estimate=estimate,
        )
        assert not plan.is_pulled

    def test_a_weak_read_does_not_justify_quoting_in_the_window(self):
        estimate = estimate_settlement_probability(
            current_price=64_401.0, strike=64_400.0, sigma_per_second=3.0,
            seconds_to_window_start=0.0,
        )
        plan = engine().plan(
            fair_value=estimate.probability, seconds_to_close=30.0,
            market_bid_cents=48, market_ask_cents=52, estimate=estimate,
        )
        assert plan.is_pulled


class TestProductionGate:
    async def test_apply_refuses_prod_without_opt_in(self):
        eng = QuotingEngine(settings=Settings(environment="prod"))
        plan = eng.plan(
            fair_value=0.5, seconds_to_close=600.0,
            market_bid_cents=48, market_ask_cents=52,
        )
        with pytest.raises(LiveTradingNotEnabled):
            await eng.apply(plan, rest=None, ticker="KXBTC15M-26AUG061230-T64000")

    async def test_apply_posts_limit_orders_with_post_only_on_demo(self):
        """post_only means a quote can never accidentally pay the taker fee."""
        sent: list[dict] = []

        class FakeREST:
            async def create_order(self, **order):
                sent.append(order)
                return {"order": order}

        eng = QuotingEngine(settings=Settings(environment="demo", paper_only=False))
        plan = eng.plan(
            fair_value=0.5, seconds_to_close=600.0,
            market_bid_cents=48, market_ask_cents=52,
        )
        await eng.apply(plan, rest=FakeREST(), ticker="T")
        assert len(sent) == 2
        assert all(o["type"] == "limit" for o in sent)
        assert all(o["post_only"] for o in sent)

    async def test_flatten_sends_a_single_market_order(self):
        sent: list[dict] = []

        class FakeREST:
            async def create_order(self, **order):
                sent.append(order)
                return {}

        eng = QuotingEngine(
            settings=Settings(environment="demo", paper_only=False),
            policy=InventoryPolicy(max_position=500, flatten_threshold=400),
        )
        eng.inventory.position = 450
        plan = eng.plan(
            fair_value=0.5, seconds_to_close=600.0,
            market_bid_cents=48, market_ask_cents=52,
        )
        await eng.apply(plan, rest=FakeREST(), ticker="T")
        assert len(sent) == 1
        assert sent[0]["type"] == "market"
        assert sent[0]["action"] == "sell"
        assert sent[0]["count"] == 450
