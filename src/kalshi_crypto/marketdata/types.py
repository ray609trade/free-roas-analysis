"""Shared market-data value types."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["BookTop", "TradePrint", "IndexSample"]


@dataclass(frozen=True)
class BookTop:
    """Top of book on one constituent exchange."""

    exchange: str
    symbol: str
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    timestamp: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def is_crossed(self) -> bool:
        """Strictly crossed: bid above ask, which means bad or stale data.

        A *locked* book (bid == ask) is unusual but legitimate and has a
        well-defined mid, so it is not treated as an error.
        """
        return self.bid > self.ask

    @property
    def is_locked(self) -> bool:
        return self.bid == self.ask

    @property
    def size_weighted_mid(self) -> float:
        """Mid weighted toward the thinner side -- a better microprice estimate."""
        total = self.bid_size + self.ask_size
        if total <= 0:
            return self.mid
        return (self.bid * self.ask_size + self.ask * self.bid_size) / total


@dataclass(frozen=True)
class TradePrint:
    exchange: str
    symbol: str
    price: float
    size: float
    aggressor: str  # "buy" | "sell" | "unknown"
    timestamp: float


@dataclass(frozen=True)
class IndexSample:
    """One 1Hz sample of the mirrored index."""

    asset: str
    price: float
    timestamp: float
    n_constituents: int
    is_stale: bool = False
