"""Price feeds for the live engine.

``SyntheticFeed`` exists so the whole system is testable with no network and no
account: it generates a driftless random walk at whatever speed you ask for.
Run ``kxc replay`` and you get hours of windows in seconds. It is a test
harness, not a market simulator -- it has no order book, no news, and no
volatility clustering, so a strategy that looks good on it has proved only that
the plumbing works.

``ExchangeFeed`` wraps the real constituent adapters. For Kalshi it must be
driven by the index basket mirror, never a single exchange -- see
:mod:`kalshi_crypto.venues` for why that distinction is enforced rather than
documented.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

__all__ = ["PriceTick", "SyntheticFeed", "ExchangeFeed", "DEFAULT_START_PRICES"]

# Starting levels for offline replay only. These are placeholders for shaping a
# random walk -- they are not quotes and are not current.
DEFAULT_START_PRICES = {"BTC": 64_000.0, "ETH": 1_900.0, "XRP": 1.05}

# Rough per-second volatility as a fraction of price, implying ~0.2% over 15
# minutes for BTC, which matches the typical move cited in the build spec.
DEFAULT_VOL_PER_SEC = {"BTC": 0.000082, "ETH": 0.000105, "XRP": 0.000125}


@dataclass(frozen=True)
class PriceTick:
    asset: str
    price: float
    timestamp: datetime


@dataclass
class SyntheticFeed:
    """Deterministic random walk, for offline testing of the full pipeline."""

    assets: tuple[str, ...] = ("BTC", "ETH", "XRP")
    seed: int = 42
    tick_seconds: float = 1.0
    speed: float = 60.0          # simulated seconds per real second
    duration_s: float = 3600.0
    start: datetime | None = None
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    async def stream(self) -> AsyncIterator[PriceTick]:
        import asyncio

        now = self.start or datetime.now(UTC).replace(microsecond=0)
        prices = {a: DEFAULT_START_PRICES.get(a, 100.0) for a in self.assets}
        elapsed = 0.0

        while elapsed < self.duration_s:
            for asset in self.assets:
                vol = DEFAULT_VOL_PER_SEC.get(asset, 0.0001)
                shock = self._rng.gauss(0.0, vol * (self.tick_seconds ** 0.5))
                prices[asset] *= (1.0 + shock)
                yield PriceTick(asset, prices[asset], now)
            now += timedelta(seconds=self.tick_seconds)
            elapsed += self.tick_seconds
            if self.speed > 0:
                delay = self.tick_seconds / self.speed
                if delay > 0.001:
                    await asyncio.sleep(delay)


@dataclass
class ExchangeFeed:
    """Real market data, routed so the feed matches the venue being traded.

    For Kalshi this drives a :class:`~kalshi_crypto.marketdata.BRTIMirror` over
    constituent exchanges and emits the composite at 1Hz. For Coinbase or
    Crypto.com it emits that venue's own top-of-book mid, because that is what
    the contract resolves against.
    """

    assets: tuple[str, ...] = ("BTC", "ETH", "XRP")
    signal_source: str = "brti_mirror"
    sample_interval_s: float = 1.0

    async def stream(self) -> AsyncIterator[PriceTick]:
        import asyncio

        from ..marketdata import BRTIMirror, BookTop, build_source

        if self.signal_source == "brti_mirror":
            mirrors = {a: BRTIMirror(a) for a in self.assets}
            exchanges = ["coinbase", "kraken"]
        else:
            mirrors = {}
            exchanges = [self.signal_source]

        symbol_for = {
            "coinbase": lambda a: f"{a}-USD",
            "kraken": lambda a: f"{a}/USD",
            "crypto_com": lambda a: f"{a}_USD",
        }

        queue: asyncio.Queue[BookTop] = asyncio.Queue()

        async def pump(exchange: str) -> None:
            symbols = [symbol_for.get(exchange, lambda a: a)(a) for a in self.assets]
            source = build_source(exchange, symbols)
            async for event in source.stream():
                if isinstance(event, BookTop):
                    await queue.put(event)

        tasks = [asyncio.create_task(pump(ex)) for ex in exchanges]
        latest: dict[str, float] = {}
        try:
            while True:
                top = await queue.get()
                asset = top.symbol.replace("-", "/").replace("_", "/").split("/")[0].upper()
                if asset not in self.assets:
                    continue
                if mirrors:
                    mirror = mirrors[asset]
                    mirror.on_book(top)
                    sample = mirror.sample(now=top.timestamp)
                    if sample is None:
                        continue
                    price = sample.price
                else:
                    price = top.size_weighted_mid
                latest[asset] = price
                yield PriceTick(asset, price, datetime.now(UTC))
        finally:
            for task in tasks:
                task.cancel()
