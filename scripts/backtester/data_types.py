"""
Data types for the MMGrid backtester.

TickData contains all pre-compiled data needed by create_proposal.
The strategy should NOT make any connector calls - all data comes from TickData.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd

from hummingbot.core.data_type.common import OrderType, TradeType


class OrderStatus(Enum):
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    PARTIALLY_FILLED = "partially_filled"


@dataclass
class SimulatedOrder:
    """Order tracked by the backtesting engine"""
    id: str
    trading_pair: str
    side: TradeType
    price: Decimal
    amount: Decimal
    order_type: OrderType
    created_at: int
    filled_amount: Decimal = Decimal("0")
    status: OrderStatus = OrderStatus.OPEN

    @property
    def remaining_amount(self) -> Decimal:
        return self.amount - self.filled_amount

    @property
    def is_buy(self) -> bool:
        return self.side == TradeType.BUY


@dataclass
class Fill:
    """Record of an order fill"""
    timestamp: int
    order_id: str
    trading_pair: str
    side: TradeType
    price: Decimal
    amount: Decimal
    fee: Decimal
    position_after: Decimal
    realized_pnl: Decimal
    cumulative_pnl: Decimal


@dataclass
class TickData:
    """
    Pre-compiled data for one tick.

    The strategy's create_proposal should ONLY use data from this object.
    No connector calls allowed in create_proposal.

    create_proposal is a PURE FUNCTION of TickData + config.
    """
    # Time
    timestamp: int

    # Order book - best levels
    best_bid_price: Decimal
    best_bid_size: Decimal
    best_ask_price: Decimal
    best_ask_size: Decimal

    # Order book depth (for maker price adjustment)
    bids_df: pd.DataFrame  # columns: price, amount, update_id
    asks_df: pd.DataFrame  # columns: price, amount, update_id

    # Prices
    mid_price: Decimal
    mark_price: Decimal  # In backtest, same as mid_price
    ema_mid: Decimal     # EMA of mid price (pre-calculated by caller)

    # Position
    position: Decimal  # Current inventory (signed: + long, - short)

    # Timing state (create_proposal checks these to decide if it should act)
    last_order_created_timestamp: int  # When orders were last placed
    last_fill_timestamp: int           # When last fill occurred
    last_fill_direction: int           # -1 (sell), 0 (none), 1 (buy)
    is_last_fill_in_same_direction: bool

    # Config values (passed through for timing checks in create_proposal)
    order_refresh_time: int  # Seconds between order refreshes
    order_cooldown: int      # Seconds to wait after a fill


@dataclass
class BacktestConfig:
    """Configuration for the backtester"""
    # Data source - fetch from exchange via MarketDataProvider
    connector_name: str  # e.g., "orderly_perpetual"
    trading_pair: str  # e.g., "BTC-USDC"
    candle_interval: str  # e.g., "1m", "5m", "1h"
    backtest_resolution: int  # e.g., 1, 5, 15, 30, 60 (seconds)

    # Time range for historical data
    start_timestamp: int  # Unix timestamp in seconds
    end_timestamp: int  # Unix timestamp in seconds

    # Market simulation
    spread_bps: Decimal = Decimal("5")  # Simulated spread for order book
    trade_fee_bps: Decimal = Decimal("4")  # Fee per trade in bps

    # Order book depth simulation
    orderbook_levels: int = 10
    level_spacing_bps: Decimal = Decimal("2")  # Spacing between levels
    level_size: Decimal = Decimal("100")  # Size at each level

    # Initial state
    initial_position: Decimal = Decimal("0")
    initial_capital: Decimal = Decimal("10000")

@dataclass
class BacktestResult:
    """Results from a backtest run"""
    # Summary metrics
    total_pnl: Decimal
    total_pnl_pct: Decimal
    total_trades: int
    total_volume: Decimal
    win_rate: Decimal
    profit_factor: Decimal
    max_drawdown: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: float

    # Time series
    equity_curve: pd.DataFrame  # timestamp, equity, position, pnl

    # Trade history
    fills: List[Fill]

    # Final state
    final_position: Decimal
    final_equity: Decimal
