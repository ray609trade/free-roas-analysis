"""Order book and fill simulation.

Prices are integer cents throughout this module. Kalshi quotes in cents, and
carrying floats into fill logic invites off-by-a-hundredth bugs at exactly the
boundaries that decide whether a resting order filled.

Fill model
----------
Deliberately pessimistic, because an optimistic fill model is the most common
way a backtest manufactures profit that does not exist:

* **Taker orders** walk the book level by level and pay the taker fee. Size
  beyond available depth does not fill -- it is not assumed away.
* **Maker orders** rest, and only fill when a trade prints *through* the resting
  price (strictly better for the aggressor), never merely *at* it. This models
  standing at the back of the queue at every price level. Real queue position is
  usually better than that, so realised maker fills should exceed backtested
  ones -- an error in the safe direction.

Both rules exist so that a strategy which looks profitable here has some chance
of being profitable live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from ..fees import DEFAULT_SCHEDULE, FeeSchedule

__all__ = ["Side", "PriceLevel", "OrderBook", "RestingOrder", "Fill", "FillEngine"]


class Side(str, Enum):
    YES = "yes"
    NO = "no"

    @property
    def other(self) -> Side:
        return Side.NO if self is Side.YES else Side.YES


@dataclass
class PriceLevel:
    price_cents: int
    size: int


@dataclass
class OrderBook:
    """A Kalshi binary book, held as YES-side bids and asks in cents.

    Kalshi natively quotes both a YES book and a NO book; a NO bid at price p is
    economically a YES ask at 100-p. This class stores the unified YES view and
    converts on the way in, so the rest of the system reasons in one frame.
    """

    ticker: str
    bids: dict[int, int] = field(default_factory=dict)  # price -> size
    asks: dict[int, int] = field(default_factory=dict)

    def apply_delta(self, side: Side, price_cents: int, delta: int) -> None:
        """Apply an incremental update, in the YES frame."""
        book = self.bids if side is Side.YES else self.asks
        new_size = book.get(price_cents, 0) + delta
        if new_size <= 0:
            book.pop(price_cents, None)
        else:
            book[price_cents] = new_size

    def apply_no_side_delta(self, price_cents: int, delta: int) -> None:
        """Apply a NO-book update, converting to the YES frame."""
        self.apply_delta(Side.NO, 100 - price_cents, delta)

    @property
    def best_bid(self) -> int | None:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> int | None:
        return min(self.asks) if self.asks else None

    @property
    def mid(self) -> float | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    @property
    def spread(self) -> int | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return ask - bid

    def depth(self, side: Side) -> list[PriceLevel]:
        """Levels sorted best-first."""
        book = self.bids if side is Side.YES else self.asks
        ordered = sorted(book.items(), reverse=side is Side.YES)
        return [PriceLevel(p, s) for p, s in ordered]

    def imbalance(self, levels: int = 5) -> float | None:
        """Top-N size imbalance in [-1, 1]; positive means bid-heavy."""
        bid_size = sum(l.size for l in self.depth(Side.YES)[:levels])
        ask_size = sum(l.size for l in self.depth(Side.NO)[:levels])
        total = bid_size + ask_size
        if total == 0:
            return None
        return (bid_size - ask_size) / total


@dataclass
class RestingOrder:
    order_id: str
    is_buy: bool
    price_cents: int
    size: int
    filled: int = 0
    placed_at: float = 0.0

    @property
    def remaining(self) -> int:
        return self.size - self.filled


@dataclass(frozen=True)
class Fill:
    order_id: str
    is_buy: bool
    price_cents: int
    size: int
    fee: Decimal
    is_taker: bool
    timestamp: float

    @property
    def price(self) -> float:
        return self.price_cents / 100.0

    @property
    def cash_flow(self) -> Decimal:
        """Signed cash change: buying costs money, selling raises it. Fees always cost."""
        notional = Decimal(self.price_cents) * Decimal(self.size) / Decimal(100)
        return (-notional if self.is_buy else notional) - self.fee


@dataclass
class FillEngine:
    """Applies the fill rules against a book and a trade tape."""

    schedule: FeeSchedule = field(default_factory=lambda: DEFAULT_SCHEDULE)
    series: str | None = None

    def execute_taker(
        self, book: OrderBook, *, is_buy: bool, size: int, order_id: str, timestamp: float
    ) -> list[Fill]:
        """Walk the book. Returns one fill per price level consumed.

        Fees are charged per level because each level is a separate execution
        price, and Kalshi's fee is price-dependent. This slightly overstates
        rounding cost versus a single blended print -- again, erring expensive.
        """
        levels = book.depth(Side.NO if is_buy else Side.YES)
        remaining = size
        fills: list[Fill] = []
        for level in levels:
            if remaining <= 0:
                break
            take = min(remaining, level.size)
            if take <= 0:
                continue
            fee = self.schedule.taker_fee(level.price_cents / 100.0, take, self.series)
            fills.append(
                Fill(order_id, is_buy, level.price_cents, take, fee, True, timestamp)
            )
            book.apply_delta(Side.NO if is_buy else Side.YES, level.price_cents, -take)
            remaining -= take
        return fills

    def try_maker_fill(
        self,
        order: RestingOrder,
        *,
        trade_price_cents: int,
        trade_size: int,
        taker_is_buy: bool,
        timestamp: float,
    ) -> Fill | None:
        """Fill a resting order only if a trade printed *through* its price.

        A resting bid at 49 requires a sell print at 48 or lower; a print at 49
        is assumed to have hit queue ahead of us.
        """
        if order.remaining <= 0:
            return None
        if order.is_buy:
            if taker_is_buy or trade_price_cents >= order.price_cents:
                return None
        else:
            if not taker_is_buy or trade_price_cents <= order.price_cents:
                return None

        size = min(order.remaining, trade_size)
        if size <= 0:
            return None
        fee = self.schedule.maker_fee(order.price_cents / 100.0, size, self.series)
        order.filled += size
        return Fill(order.order_id, order.is_buy, order.price_cents, size, fee, False, timestamp)
