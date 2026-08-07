# Architecture

*Educational and informational only. Not trading, investment, legal, or tax
advice.*

## The finding that shapes everything

Kalshi's event-contract fee schedule:

```
taker fee = roundup(M × 0.07   × C × P × (1−P))    M defaults to 1
maker fee = roundup(M × 0.0175 × C × P × (1−P))    M defaults to 0
```

**The maker multiplier defaults to zero.** The 15-minute crypto series do not
appear in the non-standard multiplier table, so posting liquidity is free and
crossing the spread costs ~1.75c near the money.

| Approach | Entry | Fee | Total cost | Win rate needed |
|---|---|---|---|---|
| Taker, crosses at 52c | $0.52 | $0.0175 | $0.5375 | **53.75%** |
| Taker, fills at 50c | $0.50 | $0.0175 | $0.5175 | **51.75%** |
| Maker, rests at 49c | $0.49 | $0.0000 | $0.4900 | **49.00%** |

A 4.75-point swing. The entire realistic edge from a good model is 2–3 points.
**The fee structure is a larger lever than the model**, which is why the system
is built to post liquidity rather than fire market orders at signals.

Reproduce the table yourself: `kxc fees`.

Two consequences that are easy to miss:

- Fees round **up per order**, so 1-lots pay ~14% more per contract than
  100-lots purely from rounding. Trade in 100-lots.
- There is **no settlement fee**. Holding to expiry costs nothing extra.

### Perpetuals are structurally worse on this horizon

Tier 0 is 12.0bp taker / 5.0bp maker — a 24bp round trip as a taker. BTC's
typical 15-minute move is roughly 0.2% (daily vol ÷ √96), so a taker round trip
consumes an entire average move. Unless you are a maker, perps lose to event
contracts here. Tiers only improve past $100K of 30-day volume.

## Module map

| Module | Build-order item | What it does |
|---|---|---|
| `auth.py`, `config.py` | 1 | RSA-PSS signing; demo/prod gating |
| `rest.py`, `ws.py` | 1 | REST and WebSocket clients |
| `contract_terms.py` | 2 | Downloads + hashes CRYPTO15M.pdf |
| `marketdata/` | 3 | Constituent feeds, BRTI mirror, basis tracking |
| `storage/` | 3 | TimescaleDB schema, batched recorders |
| `settlement.py` | 4 | The σ√(T/3) settlement-window model |
| `backtest/` | 5 | Fill + fee model, leakage detection |
| `quoting/` | 6 | Maker quoting, inventory, adverse-selection tracking |

## The settlement-window model (§4.1)

Settlement is the average of the final 60 seconds, not the price at the close.
For a driftless random walk the variance of the mean of the path over a window
of length `T` is `σ²T/3`:

```
σ_settlement = σ√(T/3) ≈ 0.577 × σ√T
```

Settlement is ~42% less variable than the standard endpoint calculation
implies. Anyone pricing with `σ√T` systematically overstates the chance of a
late flip: near-certain contracts get underpriced, coin-flips overpriced.

**We do not use the continuous `T/3` form for live pricing.** Settlement is a
discrete average of a small, known number of samples, and near the close the
continuous limit is visibly wrong. For samples at `t_i = t0 + i·dt`:

```
Var(mean of remaining) = σ² · [ t0 + dt · (n+1)(2n+1) / (6n) ]
```

which converges to `σ²T/3` for large `n` and `t0 = 0` — pinned by
`test_discrete_factor_converges_to_T_over_3` and validated against a Monte
Carlo simulation in `test_discrete_formula_matches_monte_carlo`.

The estimate is arithmetic on partially-revealed information: samples already
collected are **known**, not random. Only the remainder is uncertain, which is
why uncertainty falls ~4.5x across the window and the edge decays sharply.

**Caveat, stated plainly:** the `T/3` result assumes a driftless random walk
with constant volatility. Real crypto has volatility clustering and jumps.
Treat the output as a well-founded prior to calibrate empirically, not as
truth. Every estimate carries a confidence interval derived from the sampling
error of the volatility estimate, and every estimate is written to
`model_estimates` **before** the outcome is known so calibration can be audited
rather than remembered.

Output is always a calibrated probability with an interval — never a "BUY"
label. Trade only when `|model_p − market_p| > fee + half_spread + margin`.

## Maker quoting (§4.2)

Given zero maker fees, quote both sides around fair value and collect the
spread. You do not need directional accuracy; you need fair value to be roughly
**unbiased** and inventory controlled.

- bid at `fair − k`, ask at `fair + k`, with `k ≥ half the current spread`
- skew quotes against accumulated inventory
- widen `k` as time-to-close shrinks — adverse selection rises sharply near the
  end
- pull quotes entirely inside the settlement window unless §4.1 gives a firm read
- hard flat before close once inventory exceeds the limit
- all orders are `post_only`, so a quote can never accidentally pay taker fees

**The risk is adverse selection.** You get filled fastest precisely when
informed flow is running you over. `InventoryState.mean_fill_edge` tracks
fill-conditional edge separately from unconditional P&L: if it is negative,
fills are systematically arriving on the wrong side and the strategy is losing
even when the spread math looks fine.

`QuotingEngine.plan()` is pure — it computes desired quotes and sends nothing.
Sending is a separate step behind the production gate.

## Backtesting (§5) — the leakage gate

**A random strategy must backtest to exactly minus its costs.** If randomness
looks profitable, the harness is leaking future information and every result it
has produced is void.

`test_random_taker_converges_to_exactly_minus_its_costs` pins this precisely:
on synthetic coin-flip markets with a 2c spread, a random taker converges to
−0.01 gross (the half-spread) and −0.0275 net (plus the 1.75c taker fee) per
contract. `leakage_check()` runs the same test on demand and treats a
zero-variance run with positive edge as the strongest leakage signature there
is.

The fill model is deliberately pessimistic:

- **Taker** orders walk the book level by level; size beyond available depth
  does not fill.
- **Maker** orders fill only when a trade prints *through* the resting price,
  never merely *at* it — modelling the back of the queue at every level. Real
  queue position is usually better, so live fills should exceed backtested
  ones. An error in the safe direction.

Synthetic markets exist because you cannot test the harness against real data:
with real data you never know the true edge, so "profitable" and "leaking" look
identical. In the synthetic set the truth is known by construction.

## Safety model

Two independent opt-ins are required before a single order can reach production:

1. `KALSHI_ENV=prod` (default is `demo`)
2. `KALSHI_ALLOW_LIVE_ORDERS=1`

`Settings.require_order_permission()` enforces this and every order path calls
it. Reading market data from production is unrestricted; sending orders is not.

## Known limits

- **The BRTI mirror is an approximation.** Measure the basis before trusting
  it — see [SETTLEMENT.md](SETTLEMENT.md).
- **The settlement source is unverified.** Nobody has read the contract terms
  PDF yet. This is the highest-priority open item.
- **No live data has ever flowed through this code.** The development sandbox
  had no route to Kalshi or to the constituent exchanges. Every network adapter
  is written from the documented protocol and unit-tested against replayed
  events, but none has been exercised against a live endpoint.
- **`schema.sql` has never been applied to a running database.** No Docker
  daemon was available in the development sandbox, so the TimescaleDB
  integration tests are skipped by default. Run them with
  `KALSHI_TEST_DATABASE_URL=... pytest` before relying on the recorder.
- **True HFT is not viable here.** REST latency runs 50–200ms. Build for
  event-driven medium frequency.
- Macro is a **no-trade filter**, not a direction input. Hard-block trading ±30
  minutes around CPI, FOMC, NFP, and PPI, and widen quotes in elevated-vol
  regimes. This is not yet implemented — it belongs with §4.3.

## Not built (items 7–9)

Deliberately out of scope for this pass:

- **§4.3 directional model** — the weakest of the three approaches. Order-flow
  microstructure features have a mechanical story; RSI/MACD/Bollinger do not,
  and should be included only as controls so you can *measure* that they add
  nothing.
- **Dashboard + calibration tracker** — the `model_estimates` table is the
  substrate; the reporting layer is not written.
- **60 days paper trading** — non-negotiable gate before any live capital.
