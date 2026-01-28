"""
Grid Controller Backtester

Refactored to work at the controller level using ExecutorOrchestrator.
The orchestrator manages executor lifecycle, and we manually tick executors each candle.
"""

import asyncio
import logging
from decimal import Decimal
from typing import Dict, List, Optional

import pandas as pd

from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.data_feed.market_data_provider import MarketDataProvider

from controllers.generic.multi_grid_strike import MultiGridStrike, MultiGridStrikeConfig
from backtester.candle_cache_manager import CandleCacheManager
from backtester.grid.backtesting_executor_orchestrator import BacktestingExecutorOrchestrator
from backtester.grid.backtesting_market_data_provider import BacktestingMarketDataProvider
from backtester.grid.data_types import (
    BacktestResult,
    Fill,
    GridBacktestConfig,
    GridExecutorResult,
)
from backtester.grid.mock_connector import BacktestingMockConnector
from backtester.grid.mock_strategy import BacktestingMockStrategy

logger = logging.getLogger(__name__)


class GridControllerBacktester:
    """
    Backtester for MultiGridStrike controller using ExecutorOrchestrator.

    Runs tick-by-tick simulation:
    1. Update market state from candles
    2. Simulate fills and emit events (executors receive via PubSub)
    3. Tick all executors (manual control_task)
    4. Push reports to controller
    5. Controller determines actions
    6. Orchestrator executes actions (creates/stops executors)
    7. Record equity
    """

    def __init__(
        self,
        controller_config: MultiGridStrikeConfig,
        backtest_config: GridBacktestConfig,
    ):
        self.controller_config = controller_config
        self.backtest_config = backtest_config

        # Market data provider (for fetching rules and candles)
        self.market_data_provider = MarketDataProvider(connectors={})

        # Candle cache manager
        self.candle_cache_manager = CandleCacheManager(cache_dir="backtest_cache")

        # Trading rules
        self.trading_rules: Optional[Dict[str, TradingRule]] = None

        # Candle data
        self.candles: Optional[pd.DataFrame] = None

        # Backtesting components (initialized in _initialize_mock_components)
        self.mock_connector: Optional[BacktestingMockConnector] = None
        self.bt_market_data_provider: Optional[BacktestingMarketDataProvider] = None
        self.controller: Optional[MultiGridStrike] = None
        self.mock_strategy: Optional[BacktestingMockStrategy] = None
        self.orchestrator: Optional[BacktestingExecutorOrchestrator] = None

        # Tracking
        self.fills: List[Fill] = []
        self.equity_curve: List[Dict] = []
        self.current_timestamp = 0

        # Capital tracking
        self.initial_capital = controller_config.total_amount_quote
        self.total_fees = Decimal("0")

    async def initialize_data(self):
        """
        Fetch trading rules and candle data from the exchange.
        """
        logger.info("Fetching trading rules...")
        self.trading_rules = await self._fetch_trading_rules()

        logger.info("Fetching candle data...")
        self.candles = await self._fetch_candles()
        logger.info(f"Loaded {len(self.candles)} candles")

    async def _fetch_trading_rules(self) -> Dict[str, TradingRule]:
        """Fetch trading rules for the trading pair"""
        trading_rule = await self.market_data_provider.get_trading_rules_async(
            self.backtest_config.connector_name,
            self.controller_config.trading_pair
        )
        return {self.controller_config.trading_pair: trading_rule}

    async def _fetch_candles(self) -> pd.DataFrame:
        """Fetch historical candle data with caching"""
        candles_df = await self.candle_cache_manager.get_candles(
            market_data_provider=self.market_data_provider,
            connector_name=self.backtest_config.connector_name,
            trading_pair=self.backtest_config.trading_pair,
            interval=self.backtest_config.candle_interval,
            start_timestamp=self.backtest_config.start_timestamp,
            end_timestamp=self.backtest_config.end_timestamp,
        )

        return candles_df

    async def run(self) -> BacktestResult:
        """
        Run the backtest simulation.

        Returns:
            BacktestResult with executor results, equity curve, and summary metrics
        """
        logger.info("Initializing mock components...")
        self._initialize_mock_components()

        logger.info("Starting backtest simulation...")
        await self._run_simulation()

        logger.info("Finalizing results...")
        return self._build_result()

    def _initialize_mock_components(self):
        """
        Initialize all backtesting components in the correct order.

        Order matters:
        1. Mock connector (simulates exchange)
        2. Backtesting market data provider (simulated time + price delegation)
        3. Controller (uses backtesting market data provider)
        4. Mock strategy (wraps connector + references controller)
        5. Orchestrator (manages executor lifecycle)
        """
        # 1. Mock connector (with event system)
        self.mock_connector = BacktestingMockConnector(
            trading_rules=self.trading_rules,
            backtest_config=self.backtest_config,
        )

        # 2. Backtesting market data provider (simulated time)
        self.bt_market_data_provider = BacktestingMarketDataProvider(
            mock_connector=self.mock_connector,
            connector_name=self.controller_config.connector_name,
        )

        # 3. Controller (using backtesting market data provider)
        self.actions_queue = asyncio.Queue()
        self.controller = MultiGridStrike(
            config=self.controller_config,
            market_data_provider=self.bt_market_data_provider,
            actions_queue=self.actions_queue,
        )

        # 4. Mock strategy (wraps connector + references controller + market data provider)
        self.mock_strategy = BacktestingMockStrategy(
            connector=self.mock_connector,
            connector_name=self.controller_config.connector_name,
            trading_pair=self.controller_config.trading_pair,
            market_data_provider=self.bt_market_data_provider,
        )
        self.mock_strategy.controllers = {self.controller_config.id: self.controller}

        # 5. Orchestrator (subclass — no DB, no async loop)
        self.orchestrator = BacktestingExecutorOrchestrator(strategy=self.mock_strategy)

    async def _run_simulation(self):
        """
        Run tick-by-tick simulation through all candles.

        For each candle:
        1. Update market state (connector + strategy + market data provider timestamps)
        2. Simulate fills and emit events (executors receive automatically via PubSub)
        3. Tick all executors (manual control_task)
        4. Push reports to controller
        5. Controller determines actions
        6. Orchestrator executes actions (creates/stops executors)
        7. Record equity
        """
        for idx, row in self.candles.iterrows():
            timestamp = int(row['timestamp'])
            self.current_timestamp = timestamp

            candle = {
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
            }

            # 1. Update market state
            self.mock_connector.update_market_state(candle, timestamp)
            self.mock_strategy.current_timestamp = timestamp / 1000.0
            self.bt_market_data_provider._current_time = timestamp / 1000.0

            # 2. Simulate fills and emit events (executors receive via PubSub)
            fills = self.mock_connector.simulate_fills_and_emit_events(candle)
            self._record_fills(fills)

            # 3. Tick all executors (manual control_task)
            await self.orchestrator.tick_all_executors()

            # 4. Push reports to controller (mirrors StrategyV2Base.update_executors_info)
            reports = self.orchestrator.get_all_reports()
            controller_id = self.controller_config.id
            report = reports.get(controller_id, {})
            self.controller.executors_info = report.get("executors", [])
            self.controller.positions_held = report.get("positions", [])

            # 5. Controller decides actions
            await self.controller.update_processed_data()
            actions = self.controller.determine_executor_actions()

            # 6. Orchestrator executes actions (creates/stops executors)
            self.orchestrator.execute_actions(actions)

            # 7. Record equity using orchestrator's performance report
            self._record_equity(timestamp, controller_id)

            # Log progress every 1000 candles
            if idx % 1000 == 0:
                logger.info(f"Processed {idx}/{len(self.candles)} candles")

    def _record_fills(self, fills: List):
        """
        Record fills for reporting.

        Since fills now happen via events to executors, we just need to track
        them for the fills CSV output.
        """
        for fill in fills:
            # Calculate fee
            fee = fill.amount * fill.price * fill.fee_percent
            self.total_fees += fee

            # Find executor ID (look through orchestrator's active executors)
            executor_id = None
            for controller_id, executors_list in self.orchestrator.active_executors.items():
                for executor in executors_list:
                    # Check if this executor has this order
                    for level in executor.grid_levels:
                        if level.active_open_order and level.active_open_order.order_id == fill.order_id:
                            executor_id = executor.config.id
                            break
                        if level.active_close_order and level.active_close_order.order_id == fill.order_id:
                            executor_id = executor.config.id
                            break
                    if executor_id:
                        break
                if executor_id:
                    break

            if not executor_id:
                logger.warning(f"Could not find executor for order {fill.order_id}")
                continue

            # Record fill
            fill_record = Fill(
                timestamp=fill.timestamp,
                order_id=fill.order_id,
                executor_id=executor_id,
                trading_pair=fill.trading_pair,
                side=fill.side,
                order_type=fill.order_type,
                price=fill.price,
                amount=fill.amount,
                fee=fee,
                position_after=Decimal("0"),  # Grid doesn't track single position
                realized_pnl=Decimal("0"),  # Will be calculated by executor
                cumulative_pnl=Decimal("0"),  # Will be updated below
            )
            self.fills.append(fill_record)

    def _record_equity(self, timestamp: int, controller_id: str):
        """
        Record equity at current timestamp using orchestrator's performance report.

        Args:
            timestamp: Current timestamp in milliseconds
            controller_id: ID of the controller
        """
        # Get performance report from orchestrator
        performance_report = self.orchestrator.generate_performance_report(controller_id)

        # Total PnL = realized + unrealized
        total_pnl = performance_report.global_pnl_quote

        # Equity = initial capital + total PnL
        equity = self.initial_capital + total_pnl

        # Count active executors
        active_executors = len(self.orchestrator.active_executors.get(controller_id, []))

        self.equity_curve.append({
            'timestamp': timestamp,
            'equity': float(equity),
            'pnl': float(total_pnl),
            'fees': float(self.total_fees),
            'active_executors': active_executors,
        })

    def _build_result(self) -> BacktestResult:
        """
        Build final BacktestResult using orchestrator's executor reports.

        Returns:
            BacktestResult with executor results, equity curve, and summary metrics
        """
        # Get executor reports from orchestrator
        executor_reports = self.orchestrator.get_executors_report()
        controller_id = self.controller_config.id
        executor_infos = executor_reports.get(controller_id, [])

        # Build GridExecutorResult for each executor
        executor_results = []
        for executor_info in executor_infos:
            # Get fills for this executor
            executor_fills = [f for f in self.fills if f.executor_id == executor_info.id]

            # Create result (simplified - we don't have direct access to grid levels anymore)
            result = GridExecutorResult(
                executor_id=executor_info.id,
                config=executor_info.config,
                fills=executor_fills,
                start_timestamp=int(executor_info.timestamp * 1000),
                end_timestamp=self.current_timestamp,
                close_type=executor_info.close_type,
                realized_pnl_quote=executor_info.net_pnl_quote,
                realized_fees_quote=executor_info.cum_fees_quote,
                position_pnl_quote=Decimal("0"),  # Not tracked separately in ExecutorInfo
                net_pnl_quote=executor_info.net_pnl_quote,
                net_pnl_pct=executor_info.net_pnl_pct,
                realized_buy_size_quote=Decimal("0"),  # Not tracked in ExecutorInfo
                realized_sell_size_quote=Decimal("0"),  # Not tracked in ExecutorInfo
                total_volume=executor_info.filled_amount_quote,
                levels_completed=0,  # Not available in ExecutorInfo
                total_levels=0,  # Not available in ExecutorInfo
            )
            executor_results.append(result)

        # Convert equity curve to DataFrame
        equity_df = pd.DataFrame(self.equity_curve)

        # Calculate summary metrics from performance report
        performance_report = self.orchestrator.generate_performance_report(controller_id)
        total_pnl = performance_report.global_pnl_quote
        total_volume = performance_report.volume_traded

        return BacktestResult(
            executor_results=executor_results,
            equity_curve=equity_df,
            total_pnl=total_pnl,
            total_fees=self.total_fees,
            total_volume=total_volume,
            total_trades=len(self.fills),
            final_capital=self.initial_capital + total_pnl,
        )
