# Settlement source — OPEN QUESTION, resolve before trading

> **Status: UNRESOLVED.** Nobody has read the primary document yet. Until
> someone does, every probability this system produces rests on an assumption.

## The conflict

The build spec records two readings that have not been reconciled:

| Reading | Claim | Source |
|---|---|---|
| **BRTI average** | All Kalshi crypto contracts settle on a 60-second average of the CF Benchmarks Real-Time Index, sampled once per second over the final minute — same index from 15-minute through yearly contracts. | Two independent sources; Kalshi's CRYPTO15M terms as quoted on Robinhood's listing |
| **Captured value** | The 15-minute family settles against Kalshi's own `expiration_value` recorded on the market record. | kalshibacktest.com |

These are plausibly the *same mechanism at two layers* — Kalshi computing the
BRTI average and stamping it onto the market record. That is the outcome we
expect. But "expect" is not "verified."

## Why this is the first thing to resolve

The entire model in `kalshi_crypto/settlement.py` exists because settlement is
an **average**. For a driftless random walk, the variance of the mean of the
path over a window `T` is `σ²T/3`, not `σ²T`:

```
σ_settlement = σ√(T/3) ≈ 0.577 × σ√T
```

If settlement were instead a **snapshot** at the close, the correct factor is
`σ√T` and every probability this system produces is wrong — overconfident by
about 42% in the standard deviation, in the direction that makes near-certain
contracts look even more certain than they are. That is precisely the error
that turns a small edge into a large loss.

Not in dispute either way: settlement is an average, not a snapshot, in *both*
readings. A one-second wick through the strike is one sample out of sixty. That
kills the "it flipped in the last second, it's rigged" theory, and it is the
source of the opportunity the model tries to price.

## How to resolve it

### 1. Read the primary document

```bash
kxc terms                     # downloads and hashes CRYPTO15M.pdf
open contract_terms/CRYPTO15M.pdf
```

The fetcher stores a SHA-256 alongside the PDF so a later silent change to the
terms is detectable. It fails loudly on a network error rather than falling
back to a default — a missing document must never be read as "the defaults are
fine."

> The sandbox this package was developed in has no network route to Kalshi, so
> this step has **not** been run. It must be run on a networked machine.

Look for: the index name, the number of samples, the sampling interval, the
window's position relative to the close, and whether the exchange reserves the
right to substitute a value.

### 2. Settle it empirically as well

Reading the document tells you what Kalshi *says*. Recording expiries tells you
what Kalshi *does*. The `settlements` table stores both side by side:

```sql
SELECT ticker, expiration_value, mirror_average,
       expiration_value - mirror_average AS basis
FROM settlements
ORDER BY close_time DESC
LIMIT 100;
```

`TimescaleRecorder.record_settlement()` writes both columns. If they agree
across hundreds of expiries, the mirror is tracking whatever Kalshi actually
settles on, and the two readings above were the same thing all along.

### 3. Record the answer here

Replace this section with the finding, the date, and the document hash.

```
RESOLVED: <date>
Document: CRYPTO15M.pdf, sha256 <hash>
Settlement source: <what it actually says>
Samples: <n> at <interval> over <window>
Action taken: <e.g. SettlementWindow defaults confirmed / changed to ...>
```

## The related trap: mirror the index, not the convenient feed

BRTI is computed from a basket of qualifying exchanges. **Binance is not one of
them.** If the model predicts Binance spot while the contract settles on BRTI,
the basis error between them can exceed the entire edge — silently, because
everything looks correct right up until settlement disagrees.

`marketdata/brti.py` therefore restricts the composite to constituent
exchanges and `build_source("binance", ...)` raises rather than quietly
accepting a non-constituent venue.

The mirror is an **approximation** of the published CF methodology, not a
reimplementation. Use `BasisTracker` to measure mirror-vs-official error before
believing it:

```python
tracker.edge_is_safe(edge_bps=your_edge)   # demands edge > 3σ of basis error
```

If that returns `False`, the mirror is the binding constraint and no amount of
model work fixes it. The honest response at that point is to license the real
index feed.
