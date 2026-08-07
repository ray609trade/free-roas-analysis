"""Backtest harness tests.

:func:`test_random_strategy_loses_exactly_its_fees` is the gate the build spec
demands: if a random strategy backtests profitable, the harness has leakage and
every result it has produced is void.
"""

from __future__ import annotations

import pytest

from kalshi_crypto.backtest import (
    Backtester,
    MakerStrategy,
    MarketEvent,
    OrderBook,
    RandomStrategy,
    RestingOrder,
    Side,
    generate_markets,
    leakage_check,
)
from kalshi_crypto.backtest.book import FillEngine


class TestOrderBook:
    def test_deltas_and_best_prices(self):
        book = OrderBook("T")
        book.apply_delta(Side.YES, 48, 100)
        book.apply_delta(Side.YES, 47, 200)
        book.apply_delta(Side.NO, 52, 150)
        assert book.best_bid == 48
        assert book.best_ask == 52
        assert book.mid == 50.0
        assert book.spread == 4

    def test_level_removed_when_size_hits_zero(self):
        book = OrderBook("T")
        book.apply_delta(Side.YES, 48, 100)
        book.apply_delta(Side.YES, 48, -100)
        assert book.best_bid is None

    def test_no_side_delta_converts_to_yes_frame(self):
        """A NO bid at 45c is a YES ask at 55c."""
        book = OrderBook("T")
        book.apply_no_side_delta(45, 100)
        assert book.best_ask == 55

    def test_imbalance_sign(self):
        book = OrderBook("T")
        book.apply_delta(Side.YES, 49, 900)
        book.apply_delta(Side.NO, 51, 100)
        imbalance = book.imbalance()
        assert imbalance is not None and imbalance > 0.7

    def test_empty_book_has_no_mid(self):
        assert OrderBook("T").mid is None
        assert OrderBook("T").imbalance() is None


class TestFills:
    def test_taker_walks_multiple_levels(self):
        book = OrderBook("T")
        book.apply_delta(Side.NO, 51, 50)
        book.apply_delta(Side.NO, 52, 50)
        fills = FillEngine().execute_taker(
            book, is_buy=True, size=80, order_id="o1", timestamp=0.0
        )
        assert [f.price_cents for f in fills] == [51, 52]
        assert sum(f.size for f in fills) == 80
        assert all(f.is_taker for f in fills)
        assert all(f.fee > 0 for f in fills)

    def test_taker_cannot_fill_beyond_available_depth(self):
        book = OrderBook("T")
        book.apply_delta(Side.NO, 51, 10)
        fills = FillEngine().execute_taker(
            book, is_buy=True, size=1000, order_id="o1", timestamp=0.0
        )
        assert sum(f.size for f in fills) == 10

    def test_maker_fills_only_on_a_print_through_the_price(self):
        engine = FillEngine()
        order = RestingOrder("o1", is_buy=True, price_cents=49, size=100)
        # A sell print at 49 hits queue ahead of us, not us.
        assert engine.try_maker_fill(
            order, trade_price_cents=49, trade_size=100, taker_is_buy=False, timestamp=0.0
        ) is None
        # A sell print at 48 goes through our price.
        fill = engine.try_maker_fill(
            order, trade_price_cents=48, trade_size=40, taker_is_buy=False, timestamp=0.0
        )
        assert fill is not None
        assert fill.size == 40 and not fill.is_taker
        assert fill.fee == 0  # maker multiplier defaults to zero

    def test_maker_buy_is_not_filled_by_a_buy_aggressor(self):
        engine = FillEngine()
        order = RestingOrder("o1", is_buy=True, price_cents=49, size=100)
        assert engine.try_maker_fill(
            order, trade_price_cents=40, trade_size=100, taker_is_buy=True, timestamp=0.0
        ) is None

    def test_cash_flow_signs(self):
        engine = FillEngine()
        book = OrderBook("T")
        book.apply_delta(Side.NO, 50, 100)
        buy = engine.execute_taker(book, is_buy=True, size=100, order_id="o", timestamp=0.0)[0]
        assert buy.cash_flow < 0  # buying costs cash
        book2 = OrderBook("T")
        book2.apply_delta(Side.YES, 50, 100)
        sell = engine.execute_taker(book2, is_buy=False, size=100, order_id="o", timestamp=0.0)[0]
        assert sell.cash_flow > 0


class TestLeakage:
    def test_random_strategy_loses_exactly_its_fees(self):
        """The non-negotiable gate. Gross P&L ~ 0; net P&L ~ -fees."""
        events, close_times, _ = generate_markets(n_markets=400, seed=3)
        result = Backtester().run(events, RandomStrategy(seed=3), close_times=close_times)

        assert result.contracts_traded > 0
        assert result.settled_markets == 400

        gross = result.gross_pnl_per_contract
        stderr = result.standard_error_per_contract()
        contracts_per_market = result.contracts_traded / result.settled_markets
        stderr_per_contract = stderr / contracts_per_market

        # Gross edge indistinguishable from zero.
        assert abs(gross) < 3 * stderr_per_contract, (
            f"random strategy shows gross edge {gross:+.5f}/contract -- leakage"
        )
        # And the only systematic drag is the fee.
        assert result.fees_per_contract > 0
        assert result.pnl_per_contract < 0
        assert result.pnl_per_contract == pytest.approx(
            gross - result.fees_per_contract, abs=1e-9
        )

    @pytest.mark.slow
    def test_random_taker_converges_to_exactly_minus_its_costs(self):
        """The spec's acceptance criterion, stated precisely.

        A random taker on a true coin flip pays the half-spread on entry and the
        taker fee, and earns nothing. With a 2c spread that is -0.01 gross and
        -0.0275 net per contract. Anything materially better is leakage;
        anything materially worse means the fill or fee model is overcharging.
        """
        events, close_times, _ = generate_markets(n_markets=6000, seed=21)
        result = Backtester().run(events, RandomStrategy(seed=21), close_times=close_times)

        contracts_per_market = result.contracts_traded / result.settled_markets
        se = result.standard_error_per_contract() / contracts_per_market

        expected_gross = -0.01      # half of the synthetic 2c spread
        expected_net = expected_gross - 0.0175  # plus the at-the-money taker fee

        assert result.gross_pnl_per_contract == pytest.approx(
            expected_gross, abs=3 * se
        ), f"gross {result.gross_pnl_per_contract:+.5f} vs expected {expected_gross:+.5f}"
        assert result.pnl_per_contract == pytest.approx(expected_net, abs=3 * se)
        assert result.fees_per_contract == pytest.approx(0.0175, abs=1e-4)

    def test_leakage_check_helper_passes_on_a_clean_harness(self):
        events, close_times, _ = generate_markets(n_markets=300, seed=5)

        def run():
            return Backtester().run(events, RandomStrategy(seed=5), close_times=close_times)

        passed, message = leakage_check(run)
        assert passed, message

    def test_leakage_check_catches_a_cheating_strategy(self):
        """A strategy given the answer must trip the detector."""
        events, close_times, markets = generate_markets(n_markets=300, seed=9)
        outcomes = {m.ticker: m.settled_yes for m in markets}

        class Oracle:
            name = "oracle"

            def on_state(self, state):
                from kalshi_crypto.backtest import Action

                if state.position != 0 or state.book.best_ask is None:
                    return Action()
                if state.book.best_bid is None:
                    return Action()
                return Action(taker_size=100 if outcomes[state.ticker] else -100)

        def run():
            return Backtester().run(events, Oracle(), close_times=close_times)

        passed, message = leakage_check(run)
        assert not passed
        assert "LEAKAGE" in message

    def test_leakage_check_catches_a_partial_cheat(self):
        """A strategy that peeks only sometimes still has variance -- exercise the z path."""
        import random as _random

        events, close_times, markets = generate_markets(n_markets=400, seed=11)
        outcomes = {m.ticker: m.settled_yes for m in markets}
        rng = _random.Random(11)

        class NoisyOracle:
            name = "noisy-oracle"

            def on_state(self, state):
                from kalshi_crypto.backtest import Action

                if state.position != 0 or state.book.best_bid is None:
                    return Action()
                if state.book.best_ask is None:
                    return Action()
                # Right 65% of the time: a real but imperfect edge.
                correct = rng.random() < 0.65
                want_yes = outcomes[state.ticker] if correct else not outcomes[state.ticker]
                return Action(taker_size=100 if want_yes else -100)

        def run():
            return Backtester().run(events, NoisyOracle(), close_times=close_times)

        result = run()
        assert result.standard_error_per_contract() > 0  # genuine variance
        passed, message = leakage_check(run)
        assert not passed
        assert "LEAKAGE" in message and "sigma" in message

    def test_fees_are_actually_charged(self):
        events, close_times, _ = generate_markets(n_markets=50, seed=1)
        result = Backtester().run(events, RandomStrategy(seed=1), close_times=close_times)
        assert result.total_fees > 0
        assert all(f.fee > 0 for f in result.fills if f.is_taker)


class TestSettlement:
    def test_yes_position_pays_a_dollar_on_yes(self):
        ticker = "T"
        events = [
            MarketEvent(0.0, "book", ticker, side=Side.NO, price_cents=50, delta=100),
            MarketEvent(1.0, "book", ticker, side=Side.YES, price_cents=49, delta=100),
            MarketEvent(2.0, "settle", ticker, settled_yes=True),
        ]

        class BuyOnce:
            name = "buy-once"

            def __init__(self):
                self.done = False

            def on_state(self, state):
                from kalshi_crypto.backtest import Action

                if self.done or state.book.best_ask is None:
                    return Action()
                self.done = True
                return Action(taker_size=100)

        result = Backtester().run(events, BuyOnce(), close_times={ticker: 2.0})
        # Bought 100 at 50c = -$50, paid $1.75 fee, received $100 at settlement.
        assert float(result.realised_pnl) == pytest.approx(100 - 50 - 1.75, abs=1e-6)

    def test_maker_strategy_pays_no_fees(self):
        events, close_times, _ = generate_markets(n_markets=100, seed=2)
        result = Backtester().run(events, MakerStrategy(), close_times=close_times)
        if result.contracts_traded:
            assert all(not f.is_taker for f in result.fills)
            assert result.total_fees == 0
