"""Maker quoting engine (build-order item 6, spec section 4.2).

The premise is the fee schedule, not a forecast. Maker fees default to zero and
taker fees run ~1.75c near the money, so posting rather than crossing is worth
about 4.75 percentage points of break-even win rate -- more than any plausible
model edge. A mediocre model that only ever posts beats a good model that always
crosses.

What that means concretely: you do not need directional accuracy. You need a
fair value that is roughly *unbiased* and inventory that stays under control.

Quote rules implemented here:

* bid at ``fair - k``, ask at ``fair + k``, with ``k >= half the current spread``
* skew both quotes against accumulated inventory
* widen ``k`` as time-to-close shrinks -- adverse selection rises sharply in the
  final minute
* pull quotes entirely inside the settlement window unless the section 4.1 model
  gives a firm read
* hard flat before close once inventory exceeds the limit

Safety: :meth:`QuotingEngine.plan` is pure -- it computes a desired quote set and
nothing else. Sending is a separate step that runs the production gate in
:mod:`kalshi_crypto.config`, so the engine defaults to the demo exchange and
refuses production without an explicit second opt-in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import Settings, load_settings
from ..settlement import SettlementEstimate, SettlementWindow
from .inventory import InventoryPolicy, InventoryState

__all__ = ["Quote", "QuotePlan", "QuotingEngine"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Quote:
    is_buy: bool
    price_cents: int
    size: int


@dataclass(frozen=True)
class QuotePlan:
    quotes: tuple[Quote, ...]
    reason: str
    flatten_size: int = 0

    @property
    def is_pulled(self) -> bool:
        return not self.quotes


@dataclass
class QuotingEngine:
    """Computes the desired resting quotes for one market."""

    settings: Settings = field(default_factory=load_settings)
    size: int = 100
    base_half_width_cents: float = 2.0
    policy: InventoryPolicy = field(default_factory=InventoryPolicy)
    inventory: InventoryState = field(default_factory=InventoryState)
    window: SettlementWindow = field(default_factory=SettlementWindow)
    # Below this many seconds to close, widen aggressively.
    widen_horizon_s: float = 300.0
    max_half_width_cents: float = 12.0
    # A model read this far from the market is "firm" enough to quote inside
    # the settlement window.
    firm_read_threshold: float = 0.10

    def half_width(self, seconds_to_close: float, market_spread_cents: int | None) -> float:
        """``k``: never inside half the market spread, widening toward the close."""
        k = self.base_half_width_cents
        if market_spread_cents is not None:
            k = max(k, market_spread_cents / 2.0)
        if seconds_to_close < self.widen_horizon_s:
            # Linear ramp from 1x at the horizon to 3x at the close.
            ramp = 1.0 + 2.0 * (1.0 - max(seconds_to_close, 0.0) / self.widen_horizon_s)
            k *= ramp
        return min(k, self.max_half_width_cents)

    def plan(
        self,
        *,
        fair_value: float,
        seconds_to_close: float,
        market_bid_cents: int | None,
        market_ask_cents: int | None,
        estimate: SettlementEstimate | None = None,
    ) -> QuotePlan:
        """Compute desired quotes. Pure -- sends nothing.

        ``fair_value`` is a probability in [0, 1].
        """
        if not 0.0 <= fair_value <= 1.0:
            raise ValueError("fair_value must be a probability in [0, 1]")

        position = self.inventory.position

        if self.policy.must_flatten(position):
            return QuotePlan(
                quotes=(),
                reason=f"inventory {position} at/over flatten threshold",
                flatten_size=-position,
            )

        in_settlement_window = seconds_to_close <= self.window.duration_s
        if in_settlement_window:
            firm = (
                estimate is not None
                and not estimate.resolved
                and market_bid_cents is not None
                and market_ask_cents is not None
                and abs(
                    estimate.probability
                    - (market_bid_cents + market_ask_cents) / 200.0
                )
                >= self.firm_read_threshold
            )
            if not firm:
                return QuotePlan(
                    quotes=(),
                    reason="inside settlement window without a firm model read",
                )

        spread = (
            market_ask_cents - market_bid_cents
            if market_bid_cents is not None and market_ask_cents is not None
            else None
        )
        k = self.half_width(seconds_to_close, spread)
        skew = self.policy.skew_cents(position)
        centre = fair_value * 100.0 + skew

        bid_price = int(round(centre - k))
        ask_price = int(round(centre + k))
        bid_price = max(1, min(98, bid_price))
        ask_price = max(bid_price + 1, min(99, ask_price))

        quotes: list[Quote] = []
        if self.policy.may_buy(position, self.size):
            quotes.append(Quote(True, bid_price, self.size))
        if self.policy.may_sell(position, self.size):
            quotes.append(Quote(False, ask_price, self.size))

        if not quotes:
            return QuotePlan((), reason=f"inventory {position} blocks both sides")
        return QuotePlan(
            tuple(quotes),
            reason=f"quoting k={k:.1f}c skew={skew:+.1f}c pos={position}",
        )

    # -- sending -------------------------------------------------------------

    async def apply(self, plan: QuotePlan, rest, ticker: str) -> list[dict]:
        """Send a plan to the exchange.

        Runs the production gate first: with ``KALSHI_ENV=prod`` and no
        ``KALSHI_ALLOW_LIVE_ORDERS=1`` this raises rather than trading. Cancel
        everything before re-quoting so the book never briefly holds both the
        old and the new quotes.
        """
        self.settings.require_order_permission()
        responses: list[dict] = []

        if plan.flatten_size:
            responses.append(
                await rest.create_order(
                    ticker=ticker,
                    action="buy" if plan.flatten_size > 0 else "sell",
                    side="yes",
                    count=abs(plan.flatten_size),
                    type="market",
                )
            )
            log.warning("flattening %s: %+d", ticker, plan.flatten_size)
            return responses

        for quote in plan.quotes:
            responses.append(
                await rest.create_order(
                    ticker=ticker,
                    action="buy" if quote.is_buy else "sell",
                    side="yes",
                    count=quote.size,
                    type="limit",
                    yes_price=quote.price_cents,
                    post_only=True,  # never pay the taker fee by accident
                )
            )
        return responses
