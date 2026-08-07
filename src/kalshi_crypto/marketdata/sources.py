"""Constituent-exchange feeds and a replay source for offline work.

Two live adapters are provided (Coinbase and Kraken, both BRTI constituents).
They share a small protocol so the recorder, the mirror, and the backtester all
consume the same shapes. :class:`ReplaySource` re-emits recorded ticks so the
entire pipeline -- mirror, model, backtester -- runs deterministically with no
network, which is how the test suite exercises it.

Adding a constituent means implementing :class:`PriceSource` and registering it;
nothing downstream changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import websockets

from .types import BookTop, TradePrint

__all__ = ["PriceSource", "ReplaySource", "CoinbaseSource", "KrakenSource", "build_source"]

log = logging.getLogger(__name__)

Event = BookTop | TradePrint


class PriceSource(Protocol):
    """A constituent feed."""

    exchange: str

    def stream(self) -> AsyncIterator[Event]:
        ...


@dataclass
class ReplaySource:
    """Emits pre-recorded events, optionally in wall-clock time.

    With ``realtime=False`` (the default) it emits as fast as possible, which is
    what backtests and tests want.
    """

    events: Sequence[Event]
    exchange: str = "replay"
    realtime: bool = False
    speed: float = 1.0

    async def stream(self) -> AsyncIterator[Event]:
        previous: float | None = None
        for event in self.events:
            if self.realtime and previous is not None:
                delay = (event.timestamp - previous) / self.speed
                if delay > 0:
                    await asyncio.sleep(delay)
            previous = event.timestamp
            yield event


@dataclass
class _WebsocketSource:
    """Shared reconnect loop for the live adapters."""

    symbols: Sequence[str]
    url: str = ""
    exchange: str = ""
    max_backoff: float = 30.0
    _seen_error: bool = field(default=False, init=False)

    def subscribe_message(self) -> dict:  # pragma: no cover - overridden
        raise NotImplementedError

    def parse(self, data: dict) -> Iterable[Event]:  # pragma: no cover - overridden
        raise NotImplementedError

    async def stream(self) -> AsyncIterator[Event]:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self.url) as conn:
                    backoff = 1.0
                    await conn.send(json.dumps(self.subscribe_message()))
                    async for raw in conn:
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        for event in self.parse(data):
                            yield event
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("%s feed dropped (%s); retrying in %.1fs", self.exchange, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff)


@dataclass
class CoinbaseSource(_WebsocketSource):
    """Coinbase Advanced Trade ticker channel. Symbols look like ``BTC-USD``."""

    url: str = "wss://advanced-trade-ws.coinbase.com"
    exchange: str = "coinbase"

    def subscribe_message(self) -> dict:
        return {"type": "subscribe", "product_ids": list(self.symbols), "channel": "ticker"}

    def parse(self, data: dict) -> Iterable[Event]:
        if data.get("channel") != "ticker":
            return
        for event in data.get("events", []):
            for tick in event.get("tickers", []):
                try:
                    bid, ask = float(tick["best_bid"]), float(tick["best_ask"])
                    yield BookTop(
                        exchange=self.exchange,
                        symbol=tick["product_id"],
                        bid=bid,
                        ask=ask,
                        bid_size=float(tick.get("best_bid_quantity", 0.0)),
                        ask_size=float(tick.get("best_ask_quantity", 0.0)),
                        timestamp=_now(),
                    )
                except (KeyError, ValueError, TypeError):
                    continue


@dataclass
class KrakenSource(_WebsocketSource):
    """Kraken v2 ticker channel. Symbols look like ``BTC/USD``."""

    url: str = "wss://ws.kraken.com/v2"
    exchange: str = "kraken"

    def subscribe_message(self) -> dict:
        return {
            "method": "subscribe",
            "params": {"channel": "ticker", "symbol": list(self.symbols)},
        }

    def parse(self, data: dict) -> Iterable[Event]:
        if data.get("channel") != "ticker":
            return
        for tick in data.get("data", []):
            try:
                yield BookTop(
                    exchange=self.exchange,
                    symbol=tick["symbol"],
                    bid=float(tick["bid"]),
                    ask=float(tick["ask"]),
                    bid_size=float(tick.get("bid_qty", 0.0)),
                    ask_size=float(tick.get("ask_qty", 0.0)),
                    timestamp=_now(),
                )
            except (KeyError, ValueError, TypeError):
                continue


_SOURCES = {"coinbase": CoinbaseSource, "kraken": KrakenSource}


def build_source(exchange: str, symbols: Sequence[str]) -> PriceSource:
    """Construct a live adapter by name.

    Only constituents with an implemented adapter are available. Deliberately
    raises for e.g. ``binance``: it is not a BRTI constituent, and quietly
    accepting it would reintroduce exactly the basis error this package warns
    about.
    """
    key = exchange.lower()
    if key not in _SOURCES:
        raise KeyError(
            f"no adapter for {exchange!r}. Implemented: {sorted(_SOURCES)}. "
            "Note that non-constituent venues (Binance among them) must not be "
            "fed into the index mirror."
        )
    return _SOURCES[key](symbols=list(symbols))


def _now() -> float:
    import time

    return time.time()
