"""
Data types for the Grid Controller backtester.

This backtester runs actual GridExecutor instances with mocked connector/strategy,
simulating fills tick-by-tick using OHLCV data.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional

import pandas as pd

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.strategy_v2.executors.grid_executor.data_types import GridExecutorConfig
from hummingbot.strategy_v2.models.executors import CloseType


class OrderStatus(Enum):
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    PARTIALLY_FILLED = "partially_filled"


@dataclass
class SimulatedOrder:
    """Order tracked by the backtesting mock connector"""
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
class SimulatedFill:
    """Record of a simulated order fill"""
    timestamp: int
    order_id: str
    trading_pair: str
    side: TradeType
    order_type: OrderType
    price: Decimal
    amount: Decimal
    fee_percent: Decimal


@dataclass
class Fill:
    """Record of an order fill with PnL tracking"""
    timestamp: int
    order_id: str
    executor_id: str
    trading_pair: str
    side: TradeType
    order_type: OrderType
    price: Decimal
    amount: Decimal
    fee: Decimal
    position_after: Decimal
    realized_pnl: Decimal  # PnL after fees
    cumulative_pnl: Decimal  # Cumulative PnL after fees


@dataclass
class GridBacktestConfig:
    """Configuration for grid backtesting"""
    # Required fields (no defaults)
    connector_name: str
    trading_pair: str
    start_timestamp: int
    end_timestamp: int

    # Optional fields (with defaults)
    candle_interval: str = "1s"
    backtest_resolution: int = 1  # seconds

    # Market simulation
    spread_bps: Decimal = Decimal("5")
    trade_fee_bps: Decimal = Decimal("4")

    # Order book simulation
    orderbook_levels: int = 10
    level_spacing_bps: Decimal = Decimal("2")
    level_size: Decimal = Decimal("100")


@dataclass
class GridExecutorResult:
    """Results from a single GridExecutor instance"""
    executor_id: str
    config: GridExecutorConfig
    fills: List[Fill]
    start_timestamp: int
    end_timestamp: int
    close_type: Optional[CloseType]

    # PnL metrics
    realized_pnl_quote: Decimal
    realized_fees_quote: Decimal
    position_pnl_quote: Decimal
    net_pnl_quote: Decimal
    net_pnl_pct: Decimal

    # Volume metrics
    realized_buy_size_quote: Decimal
    realized_sell_size_quote: Decimal
    total_volume: Decimal

    # Grid stats
    levels_completed: int
    total_levels: int


@dataclass
class BacktestResult:
    """Results from a complete backtest run"""
    executor_results: List[GridExecutorResult]
    equity_curve: pd.DataFrame
    total_pnl: Decimal
    total_fees: Decimal
    total_volume: Decimal
    total_trades: int
    final_capital: Decimal
