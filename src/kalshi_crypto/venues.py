"""Venue routing: the signal must come from where you actually trade.

The failure this module exists to prevent
----------------------------------------
You predict on one venue's price and trade on another's instrument. Everything
looks fine until the two disagree at exactly the moment that decides the trade.
Two concrete ways that happens here:

1. **Kalshi 15-minute binaries settle on an index basket (BRTI), not on any one
   exchange.** Binance is not even in the basket. Predicting Coinbase spot and
   trading a Kalshi binary is a basis bet you did not intend to make.
2. **A Kalshi binary settles on a 60-second average; a perp or spot up/down
   resolves on the endpoint price.** Those need different formulas --
   ``sigma*sqrt(T/3)`` versus ``sigma*sqrt(T)``. Using the average formula on a
   perp overstates your confidence by ~42% in the standard deviation.

So a venue is not just a place to send an order. It determines the price feed,
the settlement rule, and therefore the pricing model. :func:`resolve` returns
all three together, and raises rather than letting them be mixed.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Instrument",
    "VenueSpec",
    "TradingContext",
    "VENUES",
    "resolve",
    "supported_pairs",
]

# How the payoff is determined -- this selects the pricing formula.
PRICING_AVERAGE = "settlement_average"  # 60s average of the index -> sigma*sqrt(T/3)
PRICING_ENDPOINT = "endpoint"           # price at the close       -> sigma*sqrt(T)


@dataclass(frozen=True)
class Instrument:
    """A specific tradable thing on a specific venue."""

    venue: str
    kind: str      # "binary_15m" | "perp" | "spot"
    asset: str     # BTC | ETH | XRP
    symbol: str    # venue-native symbol or ticker

    def __str__(self) -> str:
        return f"{self.venue}:{self.symbol}"


@dataclass(frozen=True)
class VenueSpec:
    """What a venue offers and, critically, what its prices settle against."""

    name: str
    kinds: tuple[str, ...]
    assets: tuple[str, ...]
    # Which price feed defines truth for this venue's contracts.
    signal_source: str
    pricing_mode: str
    settles_on: str
    symbol_template: str
    notes: str = ""


VENUES: dict[str, VenueSpec] = {
    "kalshi": VenueSpec(
        name="kalshi",
        kinds=("binary_15m",),
        assets=("BTC", "ETH", "XRP"),
        # NOT a single exchange -- the constituent basket mirror.
        signal_source="brti_mirror",
        pricing_mode=PRICING_AVERAGE,
        settles_on=(
            "60-second average of the CF Benchmarks Real-Time Index over the "
            "final minute (UNVERIFIED -- see docs/SETTLEMENT.md)"
        ),
        symbol_template="KX{asset}15M",
        notes=(
            "Binary, pays $1 or $0. Maker fees default to zero; taker ~1.75c at "
            "the money. Do NOT drive this from a single exchange's spot price."
        ),
    ),
    "coinbase": VenueSpec(
        name="coinbase",
        kinds=("spot", "perp"),
        assets=("BTC", "ETH", "XRP"),
        signal_source="coinbase",
        pricing_mode=PRICING_ENDPOINT,
        settles_on="Coinbase's own order book at the close of the window",
        symbol_template="{asset}-USD",
        notes="Predict Coinbase's book because that is what you transact against.",
    ),
    "crypto_com": VenueSpec(
        name="crypto_com",
        kinds=("spot", "perp"),
        assets=("BTC", "ETH", "XRP"),
        signal_source="crypto_com",
        pricing_mode=PRICING_ENDPOINT,
        settles_on="Crypto.com's own order book / mark price at the close",
        symbol_template="{asset}_USD",
        notes="Perp mark price can differ from spot; funding accrues on holds.",
    ),
}


@dataclass(frozen=True)
class TradingContext:
    """Feed, pricing rule, and instrument -- resolved together so they agree."""

    instrument: Instrument
    signal_source: str
    pricing_mode: str
    settles_on: str
    notes: str

    @property
    def uses_average_settlement(self) -> bool:
        return self.pricing_mode == PRICING_AVERAGE

    def describe(self) -> str:
        return (
            f"{self.instrument} ({self.instrument.kind})\n"
            f"  signal feed : {self.signal_source}\n"
            f"  pricing     : {self.pricing_mode}\n"
            f"  settles on  : {self.settles_on}"
        )


def resolve(asset: str, venue: str, kind: str) -> TradingContext:
    """Resolve a tradable context, or raise if the combination is invalid.

    Raising is the point. A silent fallback here is how you end up predicting
    one thing and trading another.
    """
    asset = asset.upper()
    venue = venue.lower()
    spec = VENUES.get(venue)
    if spec is None:
        raise KeyError(f"unknown venue {venue!r}; known: {sorted(VENUES)}")
    if kind not in spec.kinds:
        raise ValueError(
            f"{venue} does not offer {kind!r}; it offers {list(spec.kinds)}"
        )
    if asset not in spec.assets:
        raise ValueError(
            f"{venue} has no {asset} in this system; configured: {list(spec.assets)}"
        )
    return TradingContext(
        instrument=Instrument(
            venue=venue, kind=kind, asset=asset,
            symbol=spec.symbol_template.format(asset=asset),
        ),
        signal_source=spec.signal_source,
        pricing_mode=spec.pricing_mode,
        settles_on=spec.settles_on,
        notes=spec.notes,
    )


def assert_feed_matches(context: TradingContext, feed_name: str) -> None:
    """Guard: refuse to price a contract from the wrong feed."""
    if feed_name != context.signal_source:
        raise ValueError(
            f"feed mismatch: {context.instrument} must be priced from "
            f"{context.signal_source!r}, got {feed_name!r}. Trading one venue "
            "on another's price is an unintended basis bet."
        )


def supported_pairs() -> list[tuple[str, str, str]]:
    """Every (asset, venue, kind) this system can route."""
    return [
        (asset, spec.name, kind)
        for spec in VENUES.values()
        for kind in spec.kinds
        for asset in spec.assets
    ]
