# Instrument mechanics and strategy notes

Reference for the four instrument types this touches, and what actually drives
each one. *Educational only. Not trading advice.*

---

## 1. Binary up/down (Kalshi 15-minute)

**Payoff:** $1 if the settlement value is above the strike, $0 if not. Buying
UP at 60c risks 60c to make 40c.

**The price is a probability.** 53c means the market says 53%. This is the
single most important thing to internalise: to beat it you must be better
calibrated than everyone with live feeds and money at risk. That is a high bar
on a 15-minute horizon.

**Settles on a 60-second average**, not the price at the bell. Consequences:

- A one-second wick through the strike is one sample out of sixty. The "it
  flipped at the last second" theory is mostly wrong.
- Uncertainty is `σ√(T/3)`, not `σ√T` — about 42% smaller. Pricing with the
  endpoint formula chronically overestimates late flips.
- Once samples start landing, they are *known*. Uncertainty drops ~4.5x across
  the final minute, and the outcome can become arithmetically locked before the
  close.

**Fees dominate.** Maker fee multiplier defaults to zero; taker runs ~1.75c at
the money. That is a 4.75-point swing in break-even win rate, larger than any
realistic model edge. Posting beats crossing, full stop.

**Max loss** is your stake. No margin, no liquidation.

---

## 2. Perpetual futures

**No expiry.** Position tracks the underlying with leverage until you close or
get liquidated.

**Funding rate** is the mechanism that pegs the perp to spot: longs pay shorts
(or the reverse) at intervals, typically 8-hourly. On a 15-minute horizon
funding is nearly irrelevant as a *cost* but useful as a *signal* — persistently
high positive funding means crowded longs, which is fuel for a downside cascade.

**Liquidation is the real risk.** With 10x leverage, a 10% adverse move is a
total loss, and it happens before any thesis has time to be right. Liquidations
cluster: forced selling triggers more forced selling. The liquidation feed is
the most predictive short-horizon event you can observe, precisely because it is
mechanical rather than discretionary.

**Fees:** tier 0 is 12bp taker / 5bp maker. A taker round trip is 24bp against a
typical 15-minute BTC move of ~20bp — the fee eats the whole move. **On this
horizon perps are structurally worse than binaries unless you are a maker.**

**Resolves on the endpoint price**, so `σ√T` is correct here — different from
the binary above. This system enforces that distinction per venue.

---

## 3. Futures (dated)

Expire on a set date, settle to an index. Basis (futures minus spot) converges
to zero at expiry, which is the one genuinely reliable relationship in this
document. Contango/backwardation tells you about positioning and carry.

Not directly useful at 15 minutes — the basis barely moves — but the term
structure is a decent regime indicator.

---

## 4. Options

**Not a direction bet.** Price is driven by strike, time, and *implied
volatility*. You can be right on direction and still lose money to IV crush or
theta.

Greeks that matter at short horizon:
- **Delta** — sensitivity to price. Near-expiry ATM options have delta whipping
  between 0 and 1.
- **Gamma** — how fast delta moves. Explodes near expiry; dealer gamma hedging
  can pin price to a strike or accelerate moves through it.
- **Theta** — time decay, brutal in the final hours.
- **Vega** — IV sensitivity.

Structurally similar to a binary: a tight call spread is nearly the same payoff
as an up contract. The binary is usually cleaner and cheaper for this horizon.

---

## What actually predicts, at 15 minutes

**Real mechanical story:**
- Order book imbalance (top 5/10/20) — visible pressure, decays in seconds
- Trade flow imbalance — aggressor-side volume, 30s and 2min
- Cross-exchange lead-lag — Binance typically leads by tens of milliseconds
- Liquidation cascades — the most predictive observable short-horizon event
- The contract's own order book — informed flow in the contract is a signal
  about the underlying

**Regime, not direction:**
- Realized vol over 1/5/15 min, and vol-of-vol
- Funding rate and its 1-hour delta; open interest delta
- BTC returns as a feature for ETH and XRP — BTC leads alts

**Probably nothing:**
- RSI, MACD, Bollinger, EMA crossovers. Every retail participant has these on
  the same 1-minute bars. If they predicted, the price would already reflect it.
  They ship at **weight zero** here, as controls, so you can *measure* that they
  add nothing rather than assume it. That measurement is worth having.

**Macro is a filter, not an input.** CPI, FOMC, NFP, PPI move the daily chart,
not the 15-minute chart in any way you can trade. Correct use: hard-block
trading ±30 minutes around releases and widen in elevated-vol regimes. The
system blocks rather than predicts through these.

---

## The uncomfortable summary

The market price is the best available estimate. A model that disagrees with it
by a lot is usually wrong, not insightful — which is why the drift term here is
hard-capped at 0.35 standard deviations.

The measurable edges, in order of how much they are actually worth:

1. **Fee structure** — 4.75 points from posting instead of crossing. Certain.
2. **Correct settlement math** — `σ√(T/3)` versus `σ√T` on average-settled
   binaries. Certain, and most participants get it wrong.
3. **Partial-average information** — arithmetic on samples already observed
   inside the settlement window. Certain, and it decays within the minute.
4. **Microstructure signals** — real but small, and they decay in seconds.
5. **Classic technical indicators** — no evidence of anything.

Notice that the top three are arithmetic, not forecasting. That ordering is why
this system is built the way it is.
