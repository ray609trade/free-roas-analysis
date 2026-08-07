"""Kalshi fee model.

Event-contract schedule (effective 2026-07-07)::

    taker fee = roundup(M_taker * 0.07   * C * P * (1-P))    M_taker default 1
    maker fee = roundup(M_maker * 0.0175 * C * P * (1-P))    M_maker default 0

Two properties drive the whole architecture and are easy to get wrong:

1. **The maker multiplier defaults to zero.** Resting liquidity in a series with
   no non-standard multiplier is free. The 15-minute crypto series
   (KXBTC15M / KXETH15M / KXXRP15M) are not in the non-standard table as of the
   verification date, so posting costs nothing and crossing costs ~1.75c near
   the money. That gap is wider than any plausible model edge.
2. **Rounding is up, to the cent, per order** -- not per contract. A 1-lot pays
   the same rounding penalty as a 100-lot pays across the whole order, so small
   orders are disproportionately expensive. :func:`effective_fee_per_contract`
   makes that visible.

``NON_STANDARD_MULTIPLIERS`` is the single place to patch when Kalshi publishes
a multiplier for a series we trade. Always re-verify against the live schedule:
https://kalshi.com/docs/kalshi-fee-schedule.pdf
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal

__all__ = [
    "FeeSchedule",
    "PerpFeeTier",
    "DEFAULT_SCHEDULE",
    "PERP_TIERS",
    "perp_fee",
    "perp_tier_for_volume",
]

_CENT = Decimal("0.01")

# Series that Kalshi lists with a non-standard multiplier: series ticker ->
# (taker multiplier, maker multiplier). Empty means every series uses the
# standard defaults. Patch here, not at call sites.
NON_STANDARD_MULTIPLIERS: dict[str, tuple[Decimal, Decimal]] = {}


def _roundup_cents(amount: Decimal) -> Decimal:
    """Round *up* to the next whole cent, per Kalshi's schedule."""
    return amount.quantize(_CENT, rounding=ROUND_CEILING)


@dataclass(frozen=True)
class FeeSchedule:
    """Event-contract fees.

    ``taker_rate``/``maker_rate`` are the 0.07 and 0.0175 base coefficients;
    ``taker_multiplier``/``maker_multiplier`` are Kalshi's ``M``.
    """

    taker_rate: Decimal = Decimal("0.07")
    maker_rate: Decimal = Decimal("0.0175")
    taker_multiplier: Decimal = Decimal("1")
    maker_multiplier: Decimal = Decimal("0")
    overrides: dict[str, tuple[Decimal, Decimal]] = field(
        default_factory=lambda: dict(NON_STANDARD_MULTIPLIERS)
    )

    def multipliers(self, series: str | None) -> tuple[Decimal, Decimal]:
        if series is not None and series in self.overrides:
            return self.overrides[series]
        return self.taker_multiplier, self.maker_multiplier

    def _fee(self, rate: Decimal, mult: Decimal, price: float, contracts: int) -> Decimal:
        if contracts < 0:
            raise ValueError("contracts must be non-negative")
        p = Decimal(str(price))
        if not (Decimal("0") <= p <= Decimal("1")):
            raise ValueError(f"price must be a probability in dollars (0..1), got {price}")
        if mult == 0 or contracts == 0:
            return Decimal("0.00")
        raw = mult * rate * Decimal(contracts) * p * (Decimal("1") - p)
        return _roundup_cents(raw)

    def taker_fee(self, price: float, contracts: int, series: str | None = None) -> Decimal:
        """Fee in dollars for an order of ``contracts`` that crosses the spread."""
        taker_m, _ = self.multipliers(series)
        return self._fee(self.taker_rate, taker_m, price, contracts)

    def maker_fee(self, price: float, contracts: int, series: str | None = None) -> Decimal:
        """Fee in dollars for an order of ``contracts`` that rested and was hit."""
        _, maker_m = self.multipliers(series)
        return self._fee(self.maker_rate, maker_m, price, contracts)

    def fee(
        self, price: float, contracts: int, *, is_taker: bool, series: str | None = None
    ) -> Decimal:
        if is_taker:
            return self.taker_fee(price, contracts, series)
        return self.maker_fee(price, contracts, series)

    def effective_fee_per_contract(
        self, price: float, contracts: int, *, is_taker: bool, series: str | None = None
    ) -> Decimal:
        """Per-contract cost after the per-order round-up.

        Shows the small-order penalty: at 50c a 1-lot taker pays $0.02/contract
        while a 100-lot pays $0.0175/contract.
        """
        if contracts == 0:
            return Decimal("0")
        return self.fee(price, contracts, is_taker=is_taker, series=series) / Decimal(contracts)

    def breakeven_win_rate(
        self, price: float, contracts: int = 100, *, is_taker: bool, series: str | None = None
    ) -> float:
        """Win rate a long position at ``price`` needs just to break even.

        A YES contract pays $1 on a win and $0 on a loss, so total cost per
        contract *is* the breakeven probability.
        """
        per_contract = self.effective_fee_per_contract(
            price, contracts, is_taker=is_taker, series=series
        )
        return float(Decimal(str(price)) + per_contract)

    def round_trip_cost(
        self,
        entry_price: float,
        exit_price: float,
        contracts: int,
        *,
        entry_taker: bool,
        exit_taker: bool,
        series: str | None = None,
    ) -> Decimal:
        """Total fees to open at ``entry_price`` and close at ``exit_price``.

        Settlement itself is free -- an exit that is really "hold to expiry"
        should pass ``contracts=0`` for the exit leg rather than calling this.
        """
        return self.fee(
            entry_price, contracts, is_taker=entry_taker, series=series
        ) + self.fee(exit_price, contracts, is_taker=exit_taker, series=series)


DEFAULT_SCHEDULE = FeeSchedule()


# --------------------------------------------------------------------------
# Perpetual futures: volume-tiered basis-point fees, a different product with a
# different schedule. Tier 0 round trip as taker is 24bp, which is roughly an
# entire average 15-minute BTC move -- see docs/ARCHITECTURE.md.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PerpFeeTier:
    name: str
    min_30d_volume_usd: float
    taker_bps: float
    maker_bps: float


PERP_TIERS: tuple[PerpFeeTier, ...] = (
    PerpFeeTier("tier0", 0.0, 12.0, 5.0),
    PerpFeeTier("tier1", 100_000.0, 10.0, 4.0),
)


def perp_tier_for_volume(volume_30d_usd: float) -> PerpFeeTier:
    """Highest tier whose volume threshold is met.

    Only tier 0 is verified; higher tiers are placeholders. Confirm the live
    ladder before sizing anything on the improvement.
    """
    tier = PERP_TIERS[0]
    for candidate in PERP_TIERS:
        if volume_30d_usd >= candidate.min_30d_volume_usd:
            tier = candidate
    return tier


def perp_fee(notional_usd: float, *, is_taker: bool, volume_30d_usd: float = 0.0) -> float:
    """Perp fee in dollars for a given notional."""
    tier = perp_tier_for_volume(volume_30d_usd)
    bps = tier.taker_bps if is_taker else tier.maker_bps
    return notional_usd * bps / 10_000.0
