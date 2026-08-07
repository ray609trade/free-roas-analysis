"""Kalshi 15-minute crypto up/down research stack.

Educational and informational only. Not trading, investment, legal, or tax
advice. Verify every constant in this package against Kalshi's own documents
before risking capital -- fee schedules and contract terms change.

Build order (see README):

1. API auth + WebSocket client        :mod:`kalshi_crypto.auth`, :mod:`kalshi_crypto.ws`
2. Contract terms / settlement source :mod:`kalshi_crypto.contract_terms`
3. BRTI mirror + tick recorder        :mod:`kalshi_crypto.marketdata`, :mod:`kalshi_crypto.storage`
4. Settlement-window calculator       :mod:`kalshi_crypto.settlement`
5. Backtest harness with fee model    :mod:`kalshi_crypto.backtest`
6. Maker quoting engine (demo only)   :mod:`kalshi_crypto.quoting`
"""

from .fees import DEFAULT_SCHEDULE, FeeSchedule
from .settlement import (
    PartialAverage,
    RollingVolEstimator,
    SettlementEstimate,
    SettlementWindow,
    estimate_settlement_probability,
)
from .tickers import MarketTicker, build_ticker, parse_ticker

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_SCHEDULE",
    "FeeSchedule",
    "MarketTicker",
    "PartialAverage",
    "RollingVolEstimator",
    "SettlementEstimate",
    "SettlementWindow",
    "build_ticker",
    "estimate_settlement_probability",
    "parse_ticker",
]
