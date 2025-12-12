"""
Strategy base utilities for backtesting.

Provides data structures for preprocessing market data that work identically
for both live trading and backtesting.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Optional


@dataclass
class MarketState:
    """
    Standardized preprocessed market data.

    Strategy populates this during preprocessing, and it works identically
    whether using a real connector or MockConnector.
    """
    timestamp: float = 0
    mid_price: Decimal = Decimal("0")
    mark_price: Decimal = Decimal("0")
    best_bid: Decimal = Decimal("0")
    best_ask: Decimal = Decimal("0")
    best_bid_size: Decimal = Decimal("0")
    best_ask_size: Decimal = Decimal("0")
    spread: Decimal = Decimal("0")
    spread_bps: Decimal = Decimal("0")
    ema_price: Optional[Decimal] = None
    custom: Dict[str, Any] = field(default_factory=dict)
