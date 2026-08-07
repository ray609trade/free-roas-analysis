"""Live engine: turns a price stream into up/down prices and paper trades.

One engine drives both modes. ``kxc live`` feeds it real exchange data; ``kxc
replay`` feeds it a synthetic walk. Identical code path, so what you test
offline is what runs online.

Per asset it maintains: a window clock (strike frozen at the open), a feature
engine, a volatility estimator, and -- for average-settled venues -- the partial
settlement average as samples arrive in the final minute.

Predictions are recorded to the calibration tracker **when they are made**, and
resolved when the window closes. That ordering is the entire value of the
tracker; scoring after the fact against remembered predictions is worthless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..paper.broker import PaperBroker
from ..settlement import PartialAverage, RollingVolEstimator, SettlementWindow
from ..signals.calibration import CalibrationTracker
from ..signals.directional import DirectionalModel, UpDownQuote
from ..signals.features import FeatureEngine
from ..venues import PRICING_AVERAGE, TradingContext
from ..windows import WindowClock

__all__ = ["LiveEngine", "WindowResult", "render_table"]


@dataclass(frozen=True)
class WindowResult:
    asset: str
    venue: str
    window_label: str
    strike: float
    final_price: float
    outcome_up: bool
    pnl: float


@dataclass
class _AssetState:
    context: TradingContext
    clock: WindowClock = field(default_factory=WindowClock)
    features: FeatureEngine = field(default_factory=FeatureEngine)
    vol: RollingVolEstimator = field(default_factory=RollingVolEstimator)
    partial: PartialAverage = field(default_factory=PartialAverage)
    last_price: float | None = None
    last_sample_t: float | None = None
    quote: UpDownQuote | None = None
    traded_window: str | None = None


@dataclass
class LiveEngine:
    """Drives quoting and paper trading for a set of (asset, venue) contexts."""

    contexts: list[TradingContext]
    model: DirectionalModel = field(default_factory=DirectionalModel)
    broker: PaperBroker = field(default_factory=PaperBroker)
    tracker: CalibrationTracker = field(default_factory=CalibrationTracker)
    settlement_window: SettlementWindow = field(default_factory=SettlementWindow)
    auto_trade: bool = True
    trade_size: int = 100
    results: list[WindowResult] = field(default_factory=list)
    _state: dict[str, _AssetState] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        for ctx in self.contexts:
            self._state[ctx.instrument.asset] = _AssetState(context=ctx)

    @property
    def assets(self) -> list[str]:
        return list(self._state)

    def on_price(self, asset: str, price: float, now: datetime) -> UpDownQuote | None:
        """Feed one price tick. Returns the current quote once warmed up."""
        state = self._state.get(asset.upper())
        if state is None or price <= 0:
            return None
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        ts = now.timestamp()

        window, closed = state.clock.update(now, price)
        if closed is not None:
            self._close_window(state, closed, price)

        state.last_price = price
        state.vol.update(ts, price)
        state.features.on_price(ts, price)

        seconds_to_close = window.seconds_to_close(now)

        # Accumulate the settlement average for venues that settle on one.
        if state.context.pricing_mode == PRICING_AVERAGE:
            if seconds_to_close <= self.settlement_window.duration_s:
                due = (
                    state.last_sample_t is None
                    or ts - state.last_sample_t >= self.settlement_window.sample_interval_s
                )
                if due and state.partial.n_done < self.settlement_window.n_samples:
                    state.partial = state.partial.observe(price)
                    state.last_sample_t = ts

        sigma = state.vol.sigma_per_second()
        if sigma is None or window.strike is None:
            return None

        quote = self.model.quote(
            context=state.context,
            price=price,
            strike=window.strike,
            seconds_to_close=seconds_to_close,
            sigma_per_second=sigma,
            features=state.features.snapshot(ts),
            now=now,
            window_label=window.label(),
            sigma_rel_se=state.vol.relative_standard_error(),
            partial=state.partial if state.context.pricing_mode == PRICING_AVERAGE else None,
        )
        state.quote = quote

        if self.auto_trade:
            self._maybe_trade(state, quote, now)
        return quote

    def _maybe_trade(self, state: _AssetState, quote: UpDownQuote, now: datetime) -> None:
        """One paper position per window, taken only when the gate opens."""
        if not quote.tradable or state.traded_window == quote.window_label:
            return
        side = "up" if quote.prob_up >= 0.5 else "down"
        # Pay the model's own probability as the entry price. With no live
        # order book this is the neutral assumption: no assumed edge from
        # buying below fair, and no free lunch from the spread.
        price = quote.prob_up if side == "up" else quote.prob_down
        fill = self.broker.buy(
            instrument=quote.instrument,
            window_label=quote.window_label,
            side=side,
            contracts=self.trade_size,
            price=round(price, 2),
            timestamp=now,
        )
        if fill is not None:
            state.traded_window = quote.window_label
            self.tracker.record(
                asset=quote.asset, venue=quote.venue,
                window_label=quote.window_label, prob_up=quote.prob_up,
            )

    def _close_window(self, state: _AssetState, window, final_price: float) -> None:
        """Resolve a finished window: settle paper, score the prediction."""
        if window.strike is None:
            state.partial = PartialAverage()
            state.last_sample_t = None
            return

        # Average-settled venues resolve on the collected average; endpoint
        # venues on the last price. Using the wrong one here would quietly
        # mis-score every prediction.
        if state.context.pricing_mode == PRICING_AVERAGE and state.partial.n_done > 0:
            settle_value = state.partial.mean
        else:
            settle_value = final_price
        outcome_up = settle_value > window.strike

        instrument = str(state.context.instrument)
        pnl = float(self.broker.settle(instrument, window.label(), outcome_up))
        self.tracker.resolve(window.label(), outcome_up, asset=state.context.instrument.asset)
        self.results.append(WindowResult(
            asset=state.context.instrument.asset,
            venue=state.context.instrument.venue,
            window_label=window.label(),
            strike=window.strike,
            final_price=settle_value,
            outcome_up=outcome_up,
            pnl=pnl,
        ))

        state.partial = PartialAverage()
        state.last_sample_t = None

    def current_quotes(self) -> list[UpDownQuote]:
        return [s.quote for s in self._state.values() if s.quote is not None]


def render_table(engine: LiveEngine, now: datetime | None = None) -> str:
    """Text dashboard: the up/down price per asset, plus the paper account."""
    now = now or datetime.now(UTC)
    quotes = engine.current_quotes()
    lines = [
        f"  {now:%Y-%m-%d %H:%M:%S}Z   PAPER MODE - no real funds at risk",
        "",
        f"  {'ASSET':<5} {'VENUE':<11} {'WINDOW':<13} {'T-':>7}  "
        f"{'UP':>7} {'DOWN':>7} {'RANGE':>15}  STATUS",
        "  " + "-" * 88,
    ]
    if not quotes:
        lines.append("  warming up -- need ~30 price samples before quoting")
    for q in sorted(quotes, key=lambda x: x.asset):
        status = q.reason if not q.tradable else f"** {q.side} **"
        lines.append(
            f"  {q.asset:<5} {q.venue:<11} {q.window_label:<13} "
            f"{q.seconds_to_close:6.0f}s  "
            f"{q.prob_up:6.1%} {q.prob_down:6.1%} "
            f"{q.prob_low:6.1%}-{q.prob_high:6.1%}  {status}"
        )
    lines.append("")
    lines.append("  " + engine.broker.format_summary().replace("\n", "\n  "))
    if engine.tracker.resolved:
        lines.append("")
        lines.append("  CALIBRATION")
        lines.append("  " + engine.tracker.format_report().replace("\n", "\n  "))
    return "\n".join(lines)
