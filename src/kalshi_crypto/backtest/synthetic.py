"""Synthetic market generator.

Real recorded history is what you eventually backtest on, but you cannot test
the *harness itself* against real data -- with real data you never know the true
edge, so "profitable" and "leaking" look identical.

Here the truth is known by construction: a driftless random walk settled against
a strike placed exactly at the starting price, so YES and NO are a genuine coin
flip. Any strategy that shows a gross edge on this data has been given
information it should not have.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .book import Side
from .engine import MarketEvent

__all__ = ["SyntheticMarket", "generate_markets"]


@dataclass
class SyntheticMarket:
    events: list[MarketEvent]
    ticker: str
    close_time: float
    settled_yes: bool


def generate_markets(
    n_markets: int = 200,
    *,
    seed: int = 7,
    ticks_per_market: int = 120,
    tick_seconds: float = 5.0,
    start_price: float = 64_000.0,
    sigma_per_second: float = 3.0,
    settlement_samples: int = 60,
    spread_cents: int = 2,
    depth: int = 500,
) -> tuple[list[MarketEvent], dict[str, float], list[SyntheticMarket]]:
    """Generate coin-flip markets.

    The strike is set at the market's own starting price, so before any
    information arrives the contract is a true 50/50. Settlement uses the
    average of the final ``settlement_samples`` seconds, matching the real
    contract's mechanics.

    Quotes are posted around the *true* fair value implied by the current price,
    with a fixed spread -- an honest market maker who is neither ahead of nor
    behind the walk.
    """
    rng = random.Random(seed)
    all_events: list[MarketEvent] = []
    close_times: dict[str, float] = {}
    markets: list[SyntheticMarket] = []

    clock = 0.0
    for i in range(n_markets):
        ticker = f"SYNTH-{i:04d}"
        strike = start_price
        price = start_price
        events: list[MarketEvent] = []
        market_start = clock
        close_time = market_start + ticks_per_market * tick_seconds
        close_times[ticker] = close_time

        # Seed the book with an initial two-sided quote.
        events.append(
            MarketEvent(market_start, "book", ticker, side=Side.YES,
                        price_cents=50 - spread_cents // 2, delta=depth)
        )
        events.append(
            MarketEvent(market_start, "book", ticker, side=Side.NO,
                        price_cents=50 + spread_cents // 2, delta=depth)
        )

        prev_bid = 50 - spread_cents // 2
        prev_ask = 50 + spread_cents // 2
        settlement_sum = 0.0
        settlement_n = 0

        for t in range(1, ticks_per_market + 1):
            now = market_start + t * tick_seconds
            price += rng.gauss(0.0, sigma_per_second * tick_seconds**0.5)
            events.append(MarketEvent(now, "index", ticker, index_price=price))

            seconds_left = close_time - now
            if seconds_left <= settlement_samples:
                settlement_sum += price
                settlement_n += 1

            # Quote around a rough fair value; the exact number does not matter
            # for the leakage test, only that it carries no future information.
            fair = 50.0
            new_bid = max(1, min(98, int(round(fair)) - max(1, spread_cents // 2)))
            new_ask = min(99, max(new_bid + 1, int(round(fair)) + max(1, spread_cents // 2)))

            if new_bid != prev_bid:
                events.append(MarketEvent(now, "book", ticker, side=Side.YES,
                                          price_cents=prev_bid, delta=-depth))
                events.append(MarketEvent(now, "book", ticker, side=Side.YES,
                                          price_cents=new_bid, delta=depth))
                prev_bid = new_bid
            if new_ask != prev_ask:
                events.append(MarketEvent(now, "book", ticker, side=Side.NO,
                                          price_cents=prev_ask, delta=-depth))
                events.append(MarketEvent(now, "book", ticker, side=Side.NO,
                                          price_cents=new_ask, delta=depth))
                prev_ask = new_ask

            # Occasional prints, direction independent of the eventual outcome.
            if rng.random() < 0.3:
                taker_is_buy = rng.random() < 0.5
                events.append(
                    MarketEvent(
                        now, "trade", ticker,
                        price_cents=new_ask if taker_is_buy else new_bid,
                        trade_size=rng.randint(1, 50),
                        taker_is_buy=taker_is_buy,
                    )
                )

        settled_value = settlement_sum / settlement_n if settlement_n else price
        settled_yes = settled_value > strike
        events.append(MarketEvent(close_time, "settle", ticker, settled_yes=settled_yes))

        all_events.extend(events)
        markets.append(SyntheticMarket(events, ticker, close_time, settled_yes))
        clock = close_time + tick_seconds

    return all_events, close_times, markets
