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
class LockedCall:
    """A committed up/down call, frozen partway through the window.

    Locking matters for honesty. A model that keeps revising until the bell
    always looks prescient in hindsight, because by then most of the answer is
    already in the price. Freezing the call early -- while the outcome is still
    genuinely uncertain -- is the only version worth scoring.
    """

    asset: str
    window_label: str
    side: str            # "UP" | "DOWN"
    prob_up: float
    price_at_call: float
    strike: float
    seconds_into_window: float
    confidence: float

    @property
    def minutes_into_window(self) -> float:
        return self.seconds_into_window / 60.0

    def was_right(self, outcome_up: bool) -> bool:
        return (self.side == "UP") == outcome_up


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
    call: LockedCall | None = None


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
    # Commit a call between these two marks into the 15-minute window. The
    # default 4-9 minute band leaves 6-11 minutes of unresolved outcome, so the
    # call is a real prediction rather than a readout of what already happened.
    call_after_s: float = 240.0
    call_deadline_s: float = 540.0
    results: list[WindowResult] = field(default_factory=list)
    calls: list[tuple[LockedCall, bool]] = field(default_factory=list)
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
        self._maybe_lock_call(state, quote, window.seconds_elapsed(now))

        if self.auto_trade:
            self._maybe_trade(state, quote, now)
        return quote

    def _maybe_lock_call(
        self, state: _AssetState, quote: UpDownQuote, elapsed_s: float
    ) -> None:
        """Freeze the call once inside the decision band.

        Before ``call_after_s`` there is not enough of the window resolved to
        say much. After ``call_deadline_s`` we commit regardless, because a call
        that waits for certainty is not a prediction. Between the two we take
        the first moment the model clears its own confidence gate.
        """
        if state.call is not None and state.call.window_label == quote.window_label:
            return
        if elapsed_s < self.call_after_s:
            return

        past_deadline = elapsed_s >= self.call_deadline_s
        if not past_deadline and not quote.tradable:
            return  # still inside the band; wait for a firmer read

        state.call = LockedCall(
            asset=quote.asset,
            window_label=quote.window_label,
            side=quote.side,
            prob_up=quote.prob_up,
            price_at_call=quote.price,
            strike=quote.strike,
            seconds_into_window=elapsed_s,
            confidence=quote.confidence,
        )
        self.tracker.record(
            asset=quote.asset, venue=quote.venue,
            window_label=quote.window_label, prob_up=quote.prob_up,
        )

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

        if state.call is not None and state.call.window_label == window.label():
            self.calls.append((state.call, outcome_up))
        state.call = None
        state.partial = PartialAverage()
        state.last_sample_t = None

    @property
    def call_accuracy(self) -> float | None:
        """Hit rate of locked calls -- the number that actually matters."""
        if not self.calls:
            return None
        return sum(1 for call, up in self.calls if call.was_right(up)) / len(self.calls)

    def current_calls(self) -> list[LockedCall]:
        return [s.call for s in self._state.values() if s.call is not None]

    def current_quotes(self) -> list[UpDownQuote]:
        return [s.quote for s in self._state.values() if s.quote is not None]


def render_prices(engine: LiveEngine, now: datetime | None = None) -> str:
    """Minimal view: live price, the window, and the committed up/down call."""
    now = now or datetime.now(UTC)
    quotes = sorted(engine.current_quotes(), key=lambda q: q.asset)
    calls = {c.asset: c for c in engine.current_calls()}

    lines = [
        f"  {now:%H:%M:%S}Z   LIVE PRICES + 15-MIN UP/DOWN CALL",
        "",
        f"  {'':<5} {'PRICE':>12} {'STRIKE':>12} {'MOVE':>9} "
        f"{'CLOSES':>8}  {'LIVE':>13}  CALL",
        "  " + "-" * 86,
    ]
    if not quotes:
        lines.append("  connecting... need ~30 samples (about 30s) before the first read")

    for q in quotes:
        move = (q.price - q.strike) / q.strike * 100 if q.strike else 0.0
        call = calls.get(q.asset)
        if call is None:
            mins = (900 - q.seconds_to_close) / 60.0
            call_text = f"pending (locks at {engine.call_after_s/60:.0f}m, now {mins:.1f}m)"
        else:
            mark = "UP  " if call.side == "UP" else "DOWN"
            call_text = (
                f"{mark} {call.prob_up:5.1%} @ {call.minutes_into_window:.1f}m"
            )
        lines.append(
            f"  {q.asset:<5} {q.price:>12,.4f} {q.strike:>12,.4f} {move:>+8.3f}% "
            f"{q.seconds_to_close:>7.0f}s  {q.prob_up:>6.1%} up   {call_text}"
        )

    if engine.calls:
        acc = engine.call_accuracy or 0.0
        recent = engine.calls[-6:]
        lines.append("")
        lines.append(f"  COMPLETED CALLS: {len(engine.calls)}   hit rate {acc:.0%}")
        for call, outcome_up in recent:
            verdict = "HIT " if call.was_right(outcome_up) else "MISS"
            actual = "up" if outcome_up else "down"
            lines.append(
                f"    {verdict}  {call.asset:<4} {call.window_label:<14} "
                f"called {call.side:<4} at {call.minutes_into_window:.1f}m "
                f"({call.prob_up:.0%} up)  ->  actually {actual}"
            )
        if len(engine.calls) < 30:
            lines.append(
                f"    ({len(engine.calls)} calls is far too few to judge -- "
                "expect ~50% early, and give it days before drawing conclusions)"
            )
    return "\n".join(lines)


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
