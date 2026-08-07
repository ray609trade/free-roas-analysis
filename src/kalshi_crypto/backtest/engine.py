"""Backtest engine (build-order item 5).

Design rule, stated in the build spec and enforced by the test suite: **a random
strategy must backtest to exactly minus its costs.** If randomness looks
profitable, the harness has lookahead leakage and every result it has ever
produced is void. :func:`leakage_check` is that assertion, runnable on demand.

The engine settles positions at expiry against recorded ground truth and never
lets a strategy see a quantity it could not have seen at that instant --
strategies receive a :class:`MarketState` snapshot, not the event list.

Metrics deliberately include **fill-conditional P&L** separately from
unconditional P&L. That is the adverse-selection diagnostic for the maker
strategy (spec section 4.2): if your fills are systematically on the wrong side,
the spread math can look fine while the strategy loses money.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from ..fees import DEFAULT_SCHEDULE, FeeSchedule
from .book import Fill, FillEngine, OrderBook, RestingOrder, Side

__all__ = [
    "MarketEvent",
    "MarketState",
    "Strategy",
    "Action",
    "BacktestResult",
    "Backtester",
    "leakage_check",
]


@dataclass(frozen=True)
class MarketEvent:
    """One thing that happened, in time order."""

    timestamp: float
    kind: str  # "book" | "trade" | "index" | "settle"
    ticker: str
    # book
    side: Side | None = None
    price_cents: int | None = None
    delta: int | None = None
    # trade
    trade_size: int | None = None
    taker_is_buy: bool | None = None
    # index
    index_price: float | None = None
    # settle
    settled_yes: bool | None = None


@dataclass(frozen=True)
class MarketState:
    """What a strategy is allowed to see at one instant."""

    timestamp: float
    ticker: str
    book: OrderBook
    index_price: float | None
    seconds_to_close: float
    position: int
    resting: tuple[RestingOrder, ...]


@dataclass(frozen=True)
class Action:
    """A strategy's instruction for this tick.

    ``taker_size`` is signed: positive buys YES, negative sells YES.
    ``quotes`` replaces all resting orders with the given (is_buy, price, size)
    triples -- strategies re-quote rather than amend, matching how the live
    quoting engine behaves.
    """

    taker_size: int = 0
    quotes: tuple[tuple[bool, int, int], ...] = ()
    cancel_all: bool = False


class Strategy(Protocol):
    name: str

    def on_state(self, state: MarketState) -> Action: ...


@dataclass
class BacktestResult:
    fills: list[Fill] = field(default_factory=list)
    realised_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    contracts_traded: int = 0
    settled_markets: int = 0
    per_market_pnl: list[float] = field(default_factory=list)
    fill_conditional_pnl: list[float] = field(default_factory=list)

    @property
    def pnl_per_contract(self) -> float:
        if self.contracts_traded == 0:
            return 0.0
        return float(self.realised_pnl) / self.contracts_traded

    @property
    def fees_per_contract(self) -> float:
        if self.contracts_traded == 0:
            return 0.0
        return float(self.total_fees) / self.contracts_traded

    @property
    def gross_pnl_per_contract(self) -> float:
        """P&L before fees -- the number that should be ~0 for a random strategy."""
        if self.contracts_traded == 0:
            return 0.0
        return float(self.realised_pnl + self.total_fees) / self.contracts_traded

    def standard_error_per_contract(self) -> float:
        """S.e. of mean P&L per market, for judging whether a result is noise."""
        if len(self.per_market_pnl) < 2:
            return float("inf")
        return statistics.stdev(self.per_market_pnl) / math.sqrt(len(self.per_market_pnl))

    def summary(self) -> dict[str, float]:
        return {
            "markets": float(self.settled_markets),
            "contracts": float(self.contracts_traded),
            "net_pnl": float(self.realised_pnl),
            "total_fees": float(self.total_fees),
            "pnl_per_contract": self.pnl_per_contract,
            "gross_pnl_per_contract": self.gross_pnl_per_contract,
            "fees_per_contract": self.fees_per_contract,
            "stderr_per_market": self.standard_error_per_contract(),
        }


@dataclass
class Backtester:
    """Replays events through a strategy with a full fee and fill model."""

    schedule: FeeSchedule = field(default_factory=lambda: DEFAULT_SCHEDULE)
    series: str | None = None

    def run(
        self,
        events: Iterable[MarketEvent],
        strategy: Strategy,
        *,
        close_times: dict[str, float] | None = None,
    ) -> BacktestResult:
        close_times = close_times or {}
        engine = FillEngine(schedule=self.schedule, series=self.series)
        result = BacktestResult()

        books: dict[str, OrderBook] = {}
        positions: dict[str, int] = {}
        cash: dict[str, Decimal] = {}
        resting: dict[str, list[RestingOrder]] = {}
        index_price: float | None = None
        order_seq = 0

        for event in events:
            book = books.setdefault(event.ticker, OrderBook(event.ticker))
            positions.setdefault(event.ticker, 0)
            cash.setdefault(event.ticker, Decimal("0"))
            orders = resting.setdefault(event.ticker, [])

            if event.kind == "index":
                index_price = event.index_price

            elif event.kind == "book":
                assert event.side is not None and event.price_cents is not None
                book.apply_delta(event.side, event.price_cents, event.delta or 0)

            elif event.kind == "trade":
                # Resting orders may fill against this print. This happens
                # before the strategy is consulted: it could not have reacted
                # to a trade that had not yet printed.
                for order in list(orders):
                    fill = engine.try_maker_fill(
                        order,
                        trade_price_cents=event.price_cents or 0,
                        trade_size=event.trade_size or 0,
                        taker_is_buy=bool(event.taker_is_buy),
                        timestamp=event.timestamp,
                    )
                    if fill is not None:
                        self._apply_fill(fill, event.ticker, positions, cash, result)
                orders[:] = [o for o in orders if o.remaining > 0]

            elif event.kind == "settle":
                pnl = self._settle(
                    event, positions, cash, result, orders
                )
                result.per_market_pnl.append(pnl)
                continue

            if event.kind == "settle":
                continue

            close_time = close_times.get(event.ticker, event.timestamp)
            state = MarketState(
                timestamp=event.timestamp,
                ticker=event.ticker,
                book=book,
                index_price=index_price,
                seconds_to_close=close_time - event.timestamp,
                position=positions[event.ticker],
                resting=tuple(orders),
            )
            action = strategy.on_state(state)

            if action.cancel_all or action.quotes:
                orders.clear()
            for is_buy, price_cents, size in action.quotes:
                order_seq += 1
                orders.append(
                    RestingOrder(
                        order_id=f"{event.ticker}-{order_seq}",
                        is_buy=is_buy,
                        price_cents=price_cents,
                        size=size,
                        placed_at=event.timestamp,
                    )
                )

            if action.taker_size:
                order_seq += 1
                fills = engine.execute_taker(
                    book,
                    is_buy=action.taker_size > 0,
                    size=abs(action.taker_size),
                    order_id=f"{event.ticker}-t{order_seq}",
                    timestamp=event.timestamp,
                )
                for fill in fills:
                    self._apply_fill(fill, event.ticker, positions, cash, result)

        return result

    @staticmethod
    def _apply_fill(
        fill: Fill,
        ticker: str,
        positions: dict[str, int],
        cash: dict[str, Decimal],
        result: BacktestResult,
    ) -> None:
        positions[ticker] += fill.size if fill.is_buy else -fill.size
        cash[ticker] += fill.cash_flow
        result.fills.append(fill)
        result.total_fees += fill.fee
        result.contracts_traded += fill.size

    @staticmethod
    def _settle(
        event: MarketEvent,
        positions: dict[str, int],
        cash: dict[str, Decimal],
        result: BacktestResult,
        orders: list[RestingOrder],
    ) -> float:
        """Settle a market: each YES contract pays $1 if YES, else $0. No fee."""
        orders.clear()
        position = positions.get(event.ticker, 0)
        payout = Decimal(position) if event.settled_yes else Decimal(0)
        pnl = cash.get(event.ticker, Decimal("0")) + payout
        result.realised_pnl += pnl
        result.settled_markets += 1
        positions[event.ticker] = 0
        cash[event.ticker] = Decimal("0")
        if position != 0:
            result.fill_conditional_pnl.append(float(pnl) / abs(position))
        return float(pnl)


def leakage_check(
    build_random_run: Callable[[], BacktestResult],
    *,
    tolerance_sigma: float = 3.0,
) -> tuple[bool, str]:
    """Assert a random strategy earns zero gross and loses exactly its fees.

    Returns ``(passed, message)``. A random strategy on a fair market has zero
    expected gross P&L; the only systematic drag is fees. If gross P&L is
    positive beyond ``tolerance_sigma`` standard errors, the harness is leaking
    future information and nothing it reports can be trusted.
    """
    result = build_random_run()
    if result.contracts_traded == 0:
        return False, "random strategy traded nothing; the harness is not wired up"

    gross = result.gross_pnl_per_contract
    stderr = result.standard_error_per_contract()
    if not math.isfinite(stderr):
        return False, "not enough markets to judge; run more"

    # Standard error is per market; convert to a per-contract scale.
    contracts_per_market = result.contracts_traded / max(result.settled_markets, 1)
    stderr_per_contract = stderr / max(contracts_per_market, 1.0)

    if stderr_per_contract <= 0:
        # Zero variance across markets is not "inconclusive" -- a strategy whose
        # every market returns the identical result, with a positive gross edge,
        # is the strongest leakage signature there is.
        if gross > 1e-9:
            return False, (
                f"LEAKAGE: zero variance across markets with gross edge "
                f"{gross:+.5f}/contract. Every market returned the same result, "
                "which only happens when the strategy can see the outcome."
            )
        return True, f"OK: gross {gross:+.5f}/contract with zero variance"

    z = gross / stderr_per_contract

    if z > tolerance_sigma:
        return False, (
            f"LEAKAGE: random strategy shows gross edge of {gross:+.5f}/contract "
            f"({z:.1f} sigma). Expected ~0. Fix the harness before trusting any result."
        )
    if result.total_fees <= 0 and any(f.is_taker for f in result.fills):
        return False, "taker fills incurred no fees; the fee model is not wired in"
    return True, (
        f"OK: gross {gross:+.5f}/contract ({z:+.1f} sigma), "
        f"fees {result.fees_per_contract:.5f}/contract, net {result.pnl_per_contract:+.5f}"
    )
