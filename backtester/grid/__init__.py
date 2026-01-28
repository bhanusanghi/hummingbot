"""
Grid Controller Backtester

Custom tick-by-tick backtester for GridExecutor and MultiGridStrike controller.
Refactored to use ExecutorOrchestrator for proper executor lifecycle management.
"""

from backtester.grid.backtesting_executor_orchestrator import BacktestingExecutorOrchestrator
from backtester.grid.backtesting_market_data_provider import BacktestingMarketDataProvider
from backtester.grid.data_types import (
    BacktestResult,
    Fill,
    GridBacktestConfig,
    GridExecutorResult,
    OrderStatus,
    SimulatedFill,
    SimulatedOrder,
)
from backtester.grid.grid_backtester import GridControllerBacktester
from backtester.grid.mock_connector import BacktestingMockConnector, MockBudgetChecker, MockOrderTracker
from backtester.grid.mock_strategy import BacktestingMockStrategy

__all__ = [
    # Main backtester
    "GridControllerBacktester",
    # Backtesting infrastructure
    "BacktestingExecutorOrchestrator",
    "BacktestingMarketDataProvider",
    # Mock components
    "BacktestingMockConnector",
    "BacktestingMockStrategy",
    "MockOrderTracker",
    "MockBudgetChecker",
    # Data types
    "GridBacktestConfig",
    "GridExecutorResult",
    "BacktestResult",
    "SimulatedOrder",
    "SimulatedFill",
    "Fill",
    "OrderStatus",
]
