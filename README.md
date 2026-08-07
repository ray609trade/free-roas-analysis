# Kalshi 15-Minute Crypto — Research Stack

Infrastructure for Kalshi's 15-minute crypto up/down event contracts
(BTC · ETH · XRP): a settlement-window probability model, a fee-accurate
backtester with leakage detection, and a maker quoting engine.

> **Educational and informational only. Not trading, investment, legal, or tax
> advice.** Short-duration crypto contracts carry substantial risk of total
> loss. Verify every constant in this repository against Kalshi's own documents
> before risking capital — fee schedules and contract terms change. Consult a
> licensed financial adviser, attorney, and accountant before committing
> capital.

## What this is, and what it deliberately is not

It does **not** tell you which side will win the next 15 minutes. Nothing here
claims to. The market's own price is the best available probability estimate on
this horizon — if a contract trades at 53c, the aggregate of everyone with live
feeds and capital at risk says 53%.

What it does instead: compute a probability from live data, compare it to the
market price, and keep an auditable calibration record of whether it was ever
actually right.

## Paper only — real orders are structurally impossible

`KALSHI_PAPER_ONLY` defaults to **on**, and it blocks order placement on *every*
environment including demo. The live engine trades through `PaperBroker`, which
holds no credentials and opens no sockets — there is no code path from the
running app to a real exchange order. Turning it off takes a deliberate
`KALSHI_PAPER_ONLY=0`, and even then production needs two further opt-ins.

## Quick start

```bash
pip install -e ".[dev,db]"
pytest                          # 178 tests, no network required
```

**Run it right now, offline** — the full engine on synthetic prices:

```bash
kxc replay --speed 120          # 2 simulated hours in ~1 minute
```

**Run it on live market data** (paper trading, needs network):

```bash
kxc live --venue kalshi   --kind binary_15m --assets BTC,ETH,XRP
kxc live --venue coinbase --kind perp       --assets BTC,ETH
```

Both print a live table: the up/down price per asset per 15-minute window, a
confidence range, whether the model thinks it's worth acting on, your paper P&L,
and a running calibration score.

```
  ASSET VENUE       WINDOW             T-       UP    DOWN           RANGE  STATUS
  BTC   kalshi      03:45-04:00Z     183s    0.7%  99.3%   0.4%-  1.1%  ** DOWN **
  ETH   kalshi      03:45-04:00Z     183s   97.5%   2.5%  96.5%- 98.3%  ** UP **
  XRP   kalshi      03:45-04:00Z     183s   99.7%   0.3%  99.5%- 99.9%  ** UP **
```

Other commands:

```bash
kxc venues                      # the routing table (see below)
kxc fees                        # the fee table that drives the architecture
kxc settle --price 64420 --strike 64400 --sigma 3
kxc backtest --markets 400      # runs the leakage check first
```

## The signal always matches the venue you trade on

This is enforced, not documented. Pick a venue and the feed, settlement rule,
and pricing formula are chosen for you — `kxc venues` shows the table.

| Venue | Instrument | Price feed | Resolves on | Formula |
|---|---|---|---|---|
| Kalshi | `binary_15m` | **index basket mirror** | 60-second average | `σ√(T/3)` |
| Coinbase | `spot`, `perp` | Coinbase's own book | endpoint at close | `σ√T` |
| Crypto.com | `spot`, `perp` | Crypto.com's own book | endpoint at close | `σ√T` |

Two mistakes this prevents:

- **Predicting Coinbase and trading Kalshi.** Kalshi settles on an index basket
  that doesn't even include Binance. That mismatch is an unintended basis bet,
  and it's silent until settlement disagrees with you.
- **Using the average formula on a perp.** A binary settles on a 60-second
  average; a perp resolves on the endpoint. Same inputs, ~42% different standard
  deviation. Using the wrong one makes you overconfident exactly when it costs.

`resolve()` raises on an invalid combination rather than falling back, and
`assert_feed_matches()` refuses to price a contract from the wrong feed.

## The two findings that matter most

**1. The maker fee multiplier defaults to zero.** Posting liquidity is free;
crossing costs ~1.75c near the money. That is a 4.75-point swing in break-even
win rate — larger than the entire realistic edge from a good model. A mediocre
model that only posts beats a good model that always crosses. The system is
built to quote, not to fire market orders at signals.

**2. Settlement is an average, not a snapshot.** For a driftless random walk,
the variance of the mean of the path over a window `T` is `σ²T/3`, so
`σ_settlement = σ√(T/3) ≈ 0.577 × σ√T`. Settlement is ~42% less variable than
the standard endpoint formula implies, and anyone pricing with `σ√T`
systematically overstates the chance of a late flip.

The second one is not a rounding detail. With the index $20 above the strike and
one minute to go:

```console
$ kxc settle --price 64420 --strike 64400 --sigma 3
{
  "probability": 0.929533,
  "interval": [0.890846, 0.966468],
  "naive_endpoint_probability": 0.805288,
  "sigma_settlement": 13.583998,
  "n_left": 60
}

The naive endpoint formula would say 80.5%; the settlement-average
model says 93.0% (+12.4%).
```

A 12-point disagreement with standard practice, on a product where 2–3 points
is a good edge. Both are explained in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## ⚠️ Before you trade: two unverified assumptions

This code was developed in a sandbox with **no network route to Kalshi or to
any constituent exchange**. Two things must be checked on a networked machine:

1. **The settlement source is unresolved.** Sources conflict on whether the
   15-minute family settles on the 60-second BRTI average or on a captured
   `expiration_value`. They are plausibly the same thing at two layers, but
   nobody has read the primary document. Run `kxc terms`, read the PDF, and
   record the finding. See [docs/SETTLEMENT.md](docs/SETTLEMENT.md) — every
   downstream number depends on it.
2. **No live data has ever flowed through this code.** The REST, WebSocket, and
   exchange adapters are written from documented protocols and unit-tested
   against replayed events. None has been exercised against a live endpoint.

## Build order

| # | Component | Status |
|---|---|---|
| 1 | API auth + WebSocket client (demo env) | built, not exercised live |
| 2 | Contract terms; confirm settlement source | fetcher built, **PDF unread** |
| 3 | BRTI mirror + tick recorder → TimescaleDB | built, not exercised live |
| 4 | Settlement-window calculator (§4.1) | built and tested |
| 5 | Backtest harness with full fee model | built and tested |
| 6 | Maker quoting engine (§4.2), demo only | built, not exercised live |
| 7 | Directional model (§4.3) | not built |
| 8 | Dashboard + calibration tracker | not built (schema exists) |
| 9 | 60 days paper trading | not started — the gate before live capital |

## Configuration

```bash
export KALSHI_ENV=demo                          # default; 'prod' for real money
export KALSHI_API_KEY_ID=...
export KALSHI_PRIVATE_KEY_PATH=~/.kalshi/key.pem
export KALSHI_DATABASE_URL=postgresql://...
```

Sending an order to production requires **two** independent opt-ins:
`KALSHI_ENV=prod` *and* `KALSHI_ALLOW_LIVE_ORDERS=1`. Reading production market
data is unrestricted. Secrets are read from the environment or a key file;
`*.pem`, `*.key`, and `.env` are gitignored.

### Database

```bash
docker compose up -d timescaledb
psql "$KALSHI_DATABASE_URL" -f "$(kxc schema)"
```

Start recording early. You need months of history before a backtest means
anything, and history you did not capture is gone forever.

## Gotchas worth knowing up front

- **The WebSocket requires API-key auth even for public channels**, and signs
  `GET /trade-api/ws/v2` — the WebSocket path, not the REST prefix, with no
  query string. This is the day-one failure that looks like a network problem.
  Pinned by `test_websocket_signs_its_own_path_not_the_rest_prefix`.
- **Query strings are excluded from REST signatures.** Sign
  `/trade-api/v2/markets`, never `/trade-api/v2/markets?limit=100`.
- **Binance is not a BRTI constituent.** Mirroring it introduces a basis error
  that can exceed your entire edge, silently. `build_source("binance", ...)`
  raises on purpose.
- **Fees round up per order.** 1-lots pay ~14% more per contract than 100-lots.
- **Rate limits are token-based and tiered** — check `GET /account/api-limits`.
  REST latency is 50–200ms, so this is medium-frequency infrastructure, not HFT.

## Tax note

Kalshi event contracts settled through a CFTC-regulated DCM are treated as
Section 1256 contracts under current IRS guidance — 60/40 blended treatment
regardless of holding period, with an automatic 1099 at year end. Perpetual
futures may be treated differently. Get your accountant's read before the first
fill, not at filing time.
