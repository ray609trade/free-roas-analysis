"""Command line entry point (``kxc``).

Subcommands map onto the build order:

    kxc fees            fee and break-even table (section 2)
    kxc terms           download contract terms (item 2, needs network)
    kxc settle          settlement-window probability (item 4)
    kxc backtest        run the leakage check and a strategy (item 5)
    kxc quote           show a quote plan for given inputs (item 6)
    kxc schema          print the path to schema.sql (item 3)
"""

from __future__ import annotations

import argparse
import json
import sys

from .backtest import Backtester, RandomStrategy, generate_markets, leakage_check
from .backtest.strategies import MakerStrategy
from .fees import DEFAULT_SCHEDULE, perp_fee
from .quoting import QuotingEngine
from .settlement import (
    PartialAverage,
    SettlementWindow,
    estimate_settlement_probability,
)
from .storage import schema_path


def _cmd_fees(args: argparse.Namespace) -> int:
    contracts = args.contracts
    print(f"Fee table for a {contracts}-lot (event contracts)\n")
    print(f"{'price':>7} {'taker fee':>11} {'maker fee':>11} "
          f"{'taker BE':>9} {'maker BE':>9}")
    for cents in range(int(args.low * 100), int(args.high * 100) + 1, args.step):
        p = cents / 100.0
        taker = DEFAULT_SCHEDULE.effective_fee_per_contract(p, contracts, is_taker=True)
        maker = DEFAULT_SCHEDULE.effective_fee_per_contract(p, contracts, is_taker=False)
        be_t = DEFAULT_SCHEDULE.breakeven_win_rate(p, contracts, is_taker=True)
        be_m = DEFAULT_SCHEDULE.breakeven_win_rate(p, contracts, is_taker=False)
        print(f"{p:>7.2f} {float(taker):>11.5f} {float(maker):>11.5f} "
              f"{be_t:>8.2%} {be_m:>8.2%}")
    swing = (
        DEFAULT_SCHEDULE.breakeven_win_rate(0.52, contracts, is_taker=True)
        - DEFAULT_SCHEDULE.breakeven_win_rate(0.49, contracts, is_taker=False)
    )
    print(f"\nTaker at 52c vs maker at 49c: {swing:.2%} of break-even win rate.")
    print("Maker multiplier defaults to ZERO -- posting is free, crossing is not.")
    notional = 10_000.0
    print(f"\nPerps, ${notional:,.0f} notional, tier 0: "
          f"taker ${perp_fee(notional, is_taker=True):.2f} / "
          f"maker ${perp_fee(notional, is_taker=False):.2f} per side.")
    return 0


def _cmd_terms(args: argparse.Namespace) -> int:
    from .contract_terms import fetch_contract_terms

    try:
        record = fetch_contract_terms(args.name, args.dest)
    except RuntimeError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(record.to_json())
    print(
        "\nNow READ it. Confirm whether settlement is the 60-second BRTI average "
        "or a captured expiration_value, and record the answer in "
        "docs/SETTLEMENT.md. Every number downstream depends on it.",
        file=sys.stderr,
    )
    return 0


def _cmd_settle(args: argparse.Namespace) -> int:
    window = SettlementWindow(n_samples=args.samples)
    partial = PartialAverage(
        n_done=args.samples_done,
        sum_done=args.samples_done * args.partial_mean if args.samples_done else 0.0,
    )
    estimate = estimate_settlement_probability(
        current_price=args.price,
        strike=args.strike,
        sigma_per_second=args.sigma,
        seconds_to_window_start=args.to_window,
        partial=partial,
        window=window,
        sigma_rel_se=args.sigma_se,
    )
    out = {
        "probability": round(estimate.probability, 6),
        "interval": [round(estimate.prob_low, 6), round(estimate.prob_high, 6)],
        "naive_endpoint_probability": round(estimate.naive_probability, 6),
        "sigma_settlement": round(estimate.sigma_settlement, 6),
        "required_remaining_mean": estimate.required_remaining_mean,
        "n_left": estimate.n_left,
        "resolved": estimate.resolved,
        "notes": list(estimate.notes),
    }
    print(json.dumps(out, indent=2))
    if not estimate.resolved:
        delta = estimate.probability - estimate.naive_probability
        print(
            f"\nThe naive endpoint formula would say {estimate.naive_probability:.1%}; "
            f"the settlement-average model says {estimate.probability:.1%} "
            f"({delta:+.1%}).",
            file=sys.stderr,
        )
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    events, close_times, _ = generate_markets(n_markets=args.markets, seed=args.seed)

    def run_random():
        return Backtester().run(events, RandomStrategy(seed=args.seed), close_times=close_times)

    passed, message = leakage_check(run_random)
    print(f"leakage check: {message}")
    if not passed:
        return 1

    strategy = MakerStrategy() if args.strategy == "maker" else RandomStrategy(seed=args.seed)
    result = Backtester().run(events, strategy, close_times=close_times)
    print(f"\nstrategy: {strategy.name}")
    for key, value in result.summary().items():
        print(f"  {key:>24}: {value:,.5f}")
    return 0


def _cmd_quote(args: argparse.Namespace) -> int:
    engine = QuotingEngine()
    engine.inventory.position = args.position
    plan = engine.plan(
        fair_value=args.fair,
        seconds_to_close=args.to_close,
        market_bid_cents=args.bid,
        market_ask_cents=args.ask,
    )
    print(f"reason: {plan.reason}")
    if plan.flatten_size:
        print(f"FLATTEN {plan.flatten_size:+d}")
    for quote in plan.quotes:
        print(f"  {'BID' if quote.is_buy else 'ASK'} {quote.price_cents}c x {quote.size}")
    if plan.is_pulled and not plan.flatten_size:
        print("  (no quotes)")
    return 0


def _cmd_schema(_: argparse.Namespace) -> int:
    print(schema_path())
    return 0


def _cmd_venues(_: argparse.Namespace) -> int:
    from .venues import VENUES, resolve

    print("Routing table -- the feed is chosen by the venue, never by hand.\n")
    for spec in VENUES.values():
        print(f"{spec.name}")
        print(f"  offers      : {', '.join(spec.kinds)}  on  {', '.join(spec.assets)}")
        print(f"  signal feed : {spec.signal_source}")
        print(f"  pricing     : {spec.pricing_mode}")
        print(f"  settles on  : {spec.settles_on}")
        if spec.notes:
            print(f"  note        : {spec.notes}")
        print()
    ctx = resolve("BTC", "kalshi", "binary_15m")
    print("Example:")
    print("  " + ctx.describe().replace("\n", "\n  "))
    return 0


def _build_engine(args: argparse.Namespace):
    from .live import LiveEngine
    from .paper import PaperBroker
    from .signals import DirectionalModel
    from .venues import resolve

    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]
    contexts = [resolve(a, args.venue, args.kind) for a in assets]
    return LiveEngine(
        contexts=contexts,
        model=DirectionalModel(),
        broker=PaperBroker(starting_bankroll=args.bankroll),
        auto_trade=not args.no_trade,
        trade_size=args.size,
    )


async def _drive(engine, feed, refresh_s: float, quiet: bool) -> None:
    from .live import render_table

    last_render = 0.0
    async for tick in feed.stream():
        engine.on_price(tick.asset, tick.price, tick.timestamp)
        stamp = tick.timestamp.timestamp()
        if not quiet and stamp - last_render >= refresh_s:
            print("\033[2J\033[H" + render_table(engine, tick.timestamp), flush=True)
            last_render = stamp


def _cmd_replay(args: argparse.Namespace) -> int:
    """Offline simulation -- the full engine with no network and no account."""
    import asyncio

    from .live import SyntheticFeed, render_table

    engine = _build_engine(args)
    feed = SyntheticFeed(
        assets=tuple(q.instrument.asset for q in engine.contexts),
        seed=args.seed, speed=args.speed, duration_s=args.duration,
    )
    print(f"REPLAY -- synthetic prices, no network, no real funds. "
          f"{args.duration/60:.0f} simulated minutes at {args.speed:.0f}x.\n")
    try:
        asyncio.run(_drive(engine, feed, args.refresh, args.quiet))
    except KeyboardInterrupt:
        pass
    print(render_table(engine))
    print("\nNOTE: synthetic prices are a driftless random walk. This proves the "
          "pipeline runs; it proves nothing about edge.")
    return 0


def _cmd_live(args: argparse.Namespace) -> int:
    """Live market data, paper trading only."""
    import asyncio

    from .config import load_settings
    from .live import ExchangeFeed, render_table

    settings = load_settings()
    if not settings.paper_only:
        print("Refusing to start: KALSHI_PAPER_ONLY=0. This command is paper only.",
              file=sys.stderr)
        return 2

    engine = _build_engine(args)
    source = engine.contexts[0].signal_source
    feed = ExchangeFeed(
        assets=tuple(c.instrument.asset for c in engine.contexts),
        signal_source=source,
    )
    print(f"LIVE market data via {source} -- PAPER trading, no real funds.\n")
    try:
        asyncio.run(_drive(engine, feed, args.refresh, args.quiet))
    except KeyboardInterrupt:
        print("\nstopped.")
    except Exception as exc:  # noqa: BLE001
        print(f"\nfeed error: {exc}\n\nIf this is a connection failure, this "
              "machine has no route to the exchange. Run `kxc replay` to "
              "exercise the same engine offline.", file=sys.stderr)
        return 1
    print(render_table(engine))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kxc", description="Kalshi 15-minute crypto research stack"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_fees = sub.add_parser("fees", help="fee and break-even table")
    p_fees.add_argument("--contracts", type=int, default=100)
    p_fees.add_argument("--low", type=float, default=0.40)
    p_fees.add_argument("--high", type=float, default=0.60)
    p_fees.add_argument("--step", type=int, default=2)
    p_fees.set_defaults(func=_cmd_fees)

    p_terms = sub.add_parser("terms", help="download contract terms PDF")
    p_terms.add_argument("--name", default="CRYPTO15M")
    p_terms.add_argument("--dest", default="contract_terms")
    p_terms.set_defaults(func=_cmd_terms)

    p_settle = sub.add_parser("settle", help="settlement-window probability")
    p_settle.add_argument("--price", type=float, required=True, help="current index level")
    p_settle.add_argument("--strike", type=float, required=True)
    p_settle.add_argument("--sigma", type=float, required=True, help="per-second sigma in $")
    p_settle.add_argument("--to-window", type=float, default=0.0,
                          help="seconds until the settlement window opens")
    p_settle.add_argument("--samples", type=int, default=60)
    p_settle.add_argument("--samples-done", type=int, default=0)
    p_settle.add_argument("--partial-mean", type=float, default=0.0,
                          help="mean of samples already collected")
    p_settle.add_argument("--sigma-se", type=float, default=0.1,
                          help="relative standard error of sigma")
    p_settle.set_defaults(func=_cmd_settle)

    p_bt = sub.add_parser("backtest", help="leakage check + strategy run")
    p_bt.add_argument("--markets", type=int, default=300)
    p_bt.add_argument("--seed", type=int, default=7)
    p_bt.add_argument("--strategy", choices=["random", "maker"], default="maker")
    p_bt.set_defaults(func=_cmd_backtest)

    p_quote = sub.add_parser("quote", help="show a quote plan")
    p_quote.add_argument("--fair", type=float, required=True)
    p_quote.add_argument("--to-close", type=float, default=600.0)
    p_quote.add_argument("--bid", type=int, default=None)
    p_quote.add_argument("--ask", type=int, default=None)
    p_quote.add_argument("--position", type=int, default=0)
    p_quote.set_defaults(func=_cmd_quote)

    sub.add_parser("schema", help="path to schema.sql").set_defaults(func=_cmd_schema)
    sub.add_parser("venues", help="show venue routing").set_defaults(func=_cmd_venues)

    def _engine_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--assets", default="BTC,ETH,XRP")
        p.add_argument("--venue", default="kalshi",
                       choices=["kalshi", "coinbase", "crypto_com"])
        p.add_argument("--kind", default="binary_15m",
                       choices=["binary_15m", "perp", "spot"])
        p.add_argument("--bankroll", type=float, default=1000.0)
        p.add_argument("--size", type=int, default=100)
        p.add_argument("--no-trade", action="store_true",
                       help="quote only; place no paper trades")
        p.add_argument("--refresh", type=float, default=5.0)
        p.add_argument("--quiet", action="store_true")

    p_replay = sub.add_parser("replay", help="offline simulation, no network")
    _engine_args(p_replay)
    p_replay.add_argument("--seed", type=int, default=42)
    p_replay.add_argument("--speed", type=float, default=120.0,
                          help="simulated seconds per real second")
    p_replay.add_argument("--duration", type=float, default=7200.0,
                          help="simulated seconds to run")
    p_replay.set_defaults(func=_cmd_replay)

    p_live = sub.add_parser("live", help="live market data, paper trading only")
    _engine_args(p_live)
    p_live.set_defaults(func=_cmd_live)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
