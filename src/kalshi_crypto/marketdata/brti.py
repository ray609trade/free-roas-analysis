"""BRTI mirror (build-order item 3).

Why this exists
---------------
Kalshi's crypto contracts settle on the CF Benchmarks Real-Time Index, which is
computed from a basket of qualifying exchanges. **Binance is not in that
basket.** If a model predicts Binance spot and the contract settles on BRTI, the
basis between them can exceed the entire edge -- this is the failure mode called
out in section 3 of the build spec, and it is silent: everything looks fine
right up until settlement disagrees with the model.

So: mirror the index, not the most convenient feed.

What this is, honestly
----------------------
This is an *approximation* of BRTI, not a reimplementation. The published CF
methodology aggregates constituent order books into a synthetic book and takes
a volume- and time-weighted midpoint. What we compute is a volume-share-weighted
microprice across constituent tops of book, sampled at 1Hz.

That is close enough to trade against **only after you have measured the
basis**. :class:`BasisTracker` exists for exactly that: feed it the official
index whenever you can observe it (Kalshi publishes settlement values on the
market record after expiry) and it reports the error distribution. If the basis
standard deviation is a meaningful fraction of your edge, the mirror is not good
enough yet and the honest response is to license the real index feed.
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from .types import BookTop, IndexSample, TradePrint

__all__ = ["BRTI_CONSTITUENTS", "BRTIMirror", "BasisTracker"]

# CF Benchmarks' BRTI constituent exchanges for BTC. Note the absence of
# Binance. Re-verify against the current CF methodology -- constituents change.
BRTI_CONSTITUENTS: dict[str, tuple[str, ...]] = {
    "BTC": ("bitstamp", "coinbase", "gemini", "itbit", "kraken", "lmax"),
    "ETH": ("bitstamp", "coinbase", "gemini", "itbit", "kraken", "lmax"),
    "XRP": ("bitstamp", "coinbase", "kraken"),
}

# A constituent quote older than this is dropped from the composite.
STALE_AFTER_S = 5.0
# Volume share is measured over this trailing window.
VOLUME_WINDOW_S = 300.0


@dataclass
class BRTIMirror:
    """Composite index built from constituent tops of book.

    Weighting is by trailing traded volume share, so an exchange that stops
    trading loses influence gradually rather than at a cliff. Quotes that are
    stale or crossed are excluded outright.
    """

    asset: str
    constituents: tuple[str, ...] = ()
    stale_after_s: float = STALE_AFTER_S
    volume_window_s: float = VOLUME_WINDOW_S
    _tops: dict[str, BookTop] = field(default_factory=dict, init=False)
    _volumes: dict[str, deque[tuple[float, float]]] = field(
        default_factory=lambda: defaultdict(deque), init=False
    )

    def __post_init__(self) -> None:
        if not self.constituents:
            known = BRTI_CONSTITUENTS.get(self.asset.upper())
            if known is None:
                raise KeyError(
                    f"no constituent list for {self.asset!r}; pass constituents= "
                    "explicitly and verify against CF Benchmarks' methodology"
                )
            self.constituents = known

    def on_book(self, top: BookTop) -> None:
        if top.exchange not in self.constituents:
            return  # not an index constituent; ignore rather than contaminate
        if top.is_crossed or top.bid <= 0 or top.ask <= 0:
            return
        self._tops[top.exchange] = top

    def on_trade(self, trade: TradePrint) -> None:
        if trade.exchange not in self.constituents:
            return
        window = self._volumes[trade.exchange]
        window.append((trade.timestamp, trade.size * trade.price))
        cutoff = trade.timestamp - self.volume_window_s
        while window and window[0][0] < cutoff:
            window.popleft()

    def _prune_volumes(self, now: float) -> None:
        """Expire volume older than the window for *every* exchange.

        Pruning only on that exchange's next trade would let a venue that stops
        trading keep its weight indefinitely -- the opposite of the intended
        "loses influence gradually" behaviour.
        """
        cutoff = now - self.volume_window_s
        for window in self._volumes.values():
            while window and window[0][0] < cutoff:
                window.popleft()

    def _weight(self, exchange: str) -> float:
        return sum(notional for _, notional in self._volumes.get(exchange, ()))

    def sample(self, now: float | None = None) -> IndexSample | None:
        """Compute the current composite, or ``None`` if no usable constituents."""
        now = time.time() if now is None else now
        self._prune_volumes(now)
        fresh = [
            top
            for top in self._tops.values()
            if now - top.timestamp <= self.stale_after_s
        ]
        if not fresh:
            return None

        weights = [self._weight(top.exchange) for top in fresh]
        total = sum(weights)
        if total <= 0:
            # No volume history yet: fall back to an equal-weighted composite.
            price = statistics.fmean(top.size_weighted_mid for top in fresh)
        else:
            price = sum(
                top.size_weighted_mid * w for top, w in zip(fresh, weights)
            ) / total

        return IndexSample(
            asset=self.asset,
            price=price,
            timestamp=now,
            n_constituents=len(fresh),
            is_stale=len(fresh) < max(2, len(self.constituents) // 2),
        )


@dataclass
class BasisTracker:
    """Measures mirror-vs-official error. Run this before believing the mirror.

    Feed it pairs of (mirror value, official value) at the same instant. The
    thing that matters is not the mean -- a constant offset is harmless for a
    threshold contract only if it is truly constant -- but the standard
    deviation relative to your edge.
    """

    samples: list[tuple[float, float]] = field(default_factory=list)

    def add(self, mirror: float, official: float) -> None:
        self.samples.append((mirror, official))

    @property
    def errors_bps(self) -> list[float]:
        return [
            (mirror - official) / official * 10_000.0
            for mirror, official in self.samples
            if official > 0
        ]

    def report(self) -> dict[str, float]:
        errors = self.errors_bps
        if len(errors) < 2:
            return {"n": float(len(errors))}
        return {
            "n": float(len(errors)),
            "mean_bps": statistics.fmean(errors),
            "stdev_bps": statistics.stdev(errors),
            "max_abs_bps": max(abs(e) for e in errors),
        }

    def edge_is_safe(self, edge_bps: float, *, safety_factor: float = 3.0) -> bool:
        """True if basis noise is small relative to the edge being claimed.

        Default demands the edge be at least 3 standard deviations of basis
        error. If this returns False, the mirror is the binding constraint and
        no amount of model work fixes it.
        """
        report = self.report()
        if report.get("n", 0) < 30:
            return False
        return edge_bps > safety_factor * report["stdev_bps"]
