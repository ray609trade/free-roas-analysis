"""Fee-accurate backtesting with a pessimistic fill model."""

from .book import Fill, FillEngine, OrderBook, RestingOrder, Side
from .engine import (
    Action,
    Backtester,
    BacktestResult,
    MarketEvent,
    MarketState,
    Strategy,
    leakage_check,
)
from .strategies import MakerStrategy, RandomStrategy, SettlementModelStrategy
from .synthetic import generate_markets

__all__ = [
    "Action",
    "BacktestResult",
    "Backtester",
    "Fill",
    "FillEngine",
    "MakerStrategy",
    "MarketEvent",
    "MarketState",
    "OrderBook",
    "RandomStrategy",
    "RestingOrder",
    "SettlementModelStrategy",
    "Side",
    "Strategy",
    "generate_markets",
    "leakage_check",
]
