"""
Backtesting Executor Orchestrator

Subclass of ExecutorOrchestrator that avoids DB access and async control loops.
Executors are ticked manually via tick_all_executors() instead of running continuously.
"""

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from hummingbot.core.data_type.common import PriceType
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StoreExecutorAction
from hummingbot.strategy_v2.models.executors_info import PerformanceReport

if TYPE_CHECKING:
    from backtester.grid.mock_strategy import BacktestingMockStrategy

logger = logging.getLogger(__name__)


class BacktestingExecutorOrchestrator(ExecutorOrchestrator):
    """
    Executor orchestrator for backtesting.

    Key differences from ExecutorOrchestrator:
    - __init__: Skip DB initialization, manually set up controller structures
    - create_executor: Don't call executor.start() (which spawns async control_loop)
    - store_executor: Skip DB writes
    - tick_all_executors: New method to manually call control_task() on all executors
    - generate_performance_report: Override to skip strategy.markets check
    """

    def __init__(
        self,
        strategy: "BacktestingMockStrategy",
        executors_update_interval: float = 1.0,
        executors_max_retries: int = 10,
    ):
        """
        Initialize the backtesting orchestrator.

        Unlike the parent class, we skip _initialize_cached_performance() which
        hits the database via MarketsRecorder.

        Args:
            strategy: The BacktestingMockStrategy instance
            executors_update_interval: Update interval for executors (not used in backtesting)
            executors_max_retries: Max retries for executors
        """
        self.strategy = strategy
        self.executors_update_interval = executors_update_interval
        self.executors_max_retries = executors_max_retries
        self.active_executors = {}
        self.positions_held = {}
        self.executors_ids_position_held = []
        self.cached_performance = {}

        # Initialize structures for each controller
        for controller_id in strategy.controllers.keys():
            self.active_executors[controller_id] = []
            self.positions_held[controller_id] = []
            self.cached_performance[controller_id] = PerformanceReport()

    def create_executor(self, action: CreateExecutorAction):
        """
        Create an executor without starting its async control_loop.

        Instead of calling executor.start(), we:
        1. Instantiate the executor
        2. Set its status to RUNNING
        3. Register its event listeners
        4. Call update_metrics() to initialize state

        The executor will be ticked manually via tick_all_executors().

        Args:
            action: CreateExecutorAction with executor config
        """
        controller_id = action.controller_id
        executor_config = action.executor_config

        # Set controller_id in config
        executor_config.controller_id = controller_id

        # Get executor class from mapping
        executor_class = self._executor_mapping.get(executor_config.type)
        if executor_class is None:
            raise ValueError(f"Unsupported executor config type: {executor_config.type}")

        # Instantiate executor
        executor = executor_class(
            strategy=self.strategy,
            config=executor_config,
            update_interval=self.executors_update_interval,
            max_retries=self.executors_max_retries,
        )

        # Manually set status to RUNNING (instead of calling start())
        executor._status = RunnableStatus.RUNNING

        # Register event listeners (this is what start() does internally)
        executor.register_events()

        # Initialize executor metrics
        executor.update_metrics()

        # Add to active executors
        self.active_executors[controller_id].append(executor)
        logger.debug(f"Created {type(executor).__name__} for controller {controller_id}")

    def store_executor(self, action: StoreExecutorAction):
        """
        Store executor data and update cached performance.

        Unlike the parent class, we skip MarketsRecorder.store_or_update_executor()
        and just update cached performance and remove the executor.

        Args:
            action: StoreExecutorAction with controller and executor IDs
        """
        controller_id = action.controller_id
        executor_id = action.executor_id

        executor = next(
            (executor for executor in self.active_executors[controller_id]
             if executor.config.id == executor_id),
            None
        )

        if not executor:
            logger.error(f"Executor ID {executor_id} not found for controller {controller_id}.")
            return

        if executor.is_active:
            logger.error(f"Executor ID {executor_id} is still active.")
            return

        try:
            # Skip DB write, just update cached performance
            self._update_cached_performance(controller_id, executor.executor_info)
        except Exception as e:
            logger.error(f"Error processing executor id {executor_id}: {str(e)}.")
            logger.error(f"Executor info: {executor.executor_info} | Config: {executor.config}")

        # Remove from active executors
        self.active_executors[controller_id].remove(executor)
        del executor

    async def tick_all_executors(self):
        """
        Manually tick all active executors by calling their control_task().

        This replaces the continuous async control_loop that runs in live trading.
        Each executor's control_task() will:
        - Update grid levels based on current prices
        - Place/cancel orders as needed
        - Check barriers and close conditions
        - Process any events that were fired

        Note: We patch executor._sleep to a no-op to avoid delays.
        """
        # Create a no-op async sleep function
        async def noop_sleep(delay):
            pass

        for controller_id, executors_list in self.active_executors.items():
            for executor in executors_list:
                if executor.is_active:
                    try:
                        # Patch _sleep to avoid delays
                        original_sleep = executor._sleep
                        executor._sleep = noop_sleep

                        # Call control_task (the main executor logic)
                        await executor.control_task()

                        # Restore original sleep
                        executor._sleep = original_sleep

                    except Exception as e:
                        logger.error(f"Error ticking executor {executor.config.id}: {e}", exc_info=True)

    def generate_performance_report(self, controller_id: str) -> PerformanceReport:
        """
        Generate performance report for a controller.

        Override to skip the strategy.markets check in the position loop,
        since BacktestingMockStrategy may not have a full markets dict.

        Args:
            controller_id: ID of the controller

        Returns:
            PerformanceReport with aggregated metrics
        """
        # Create a new report starting from cached base values
        report = PerformanceReport()
        cached_report = self.cached_performance.get(controller_id, PerformanceReport())

        # Start with cached values (from completed executors)
        report.realized_pnl_quote = cached_report.realized_pnl_quote
        report.volume_traded = cached_report.volume_traded
        report.close_type_counts = cached_report.close_type_counts.copy() if cached_report.close_type_counts else {}

        # Add data from active executors
        active_executors = self.active_executors.get(controller_id, [])
        positions = self.positions_held.get(controller_id, [])

        for executor in active_executors:
            executor_info = executor.executor_info
            if not executor_info.is_done:
                report.unrealized_pnl_quote += executor_info.net_pnl_quote
            else:
                report.realized_pnl_quote += executor_info.net_pnl_quote
                if executor_info.close_type:
                    report.close_type_counts[executor_info.close_type] = report.close_type_counts.get(
                        executor_info.close_type, 0) + 1

            report.volume_traded += executor_info.filled_amount_quote

        # Add data from positions held
        # Skip strategy.markets check since it may not be fully initialized in backtesting
        positions_summary = []
        for position in positions:
            try:
                mid_price = self.strategy.market_data_provider.get_price_by_type(
                    position.connector_name, position.trading_pair, PriceType.MidPrice)
                position_summary = position.get_position_summary(
                    mid_price if not mid_price.is_nan() else Decimal("0"))

                # Update report with position data
                report.realized_pnl_quote += position_summary.realized_pnl_quote - position_summary.cum_fees_quote
                report.volume_traded += position_summary.volume_traded_quote
                report.unrealized_pnl_quote += position_summary.unrealized_pnl_quote
                positions_summary.append(position_summary)
            except Exception as e:
                logger.warning(f"Error processing position {position.trading_pair}: {e}")

        # Set the positions summary
        report.positions_summary = positions_summary

        # Calculate global PNL values
        report.global_pnl_quote = report.unrealized_pnl_quote + report.realized_pnl_quote
        report.global_pnl_pct = (report.global_pnl_quote / report.volume_traded) * 100 if report.volume_traded != 0 else Decimal(0)

        # Calculate individual PNL percentages
        report.unrealized_pnl_pct = (report.unrealized_pnl_quote / report.volume_traded) * 100 if report.volume_traded != 0 else Decimal(0)
        report.realized_pnl_pct = (report.realized_pnl_quote / report.volume_traded) * 100 if report.volume_traded != 0 else Decimal(0)

        return report
