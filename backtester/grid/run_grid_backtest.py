"""
Grid Controller Backtest Runner

Entry point script for running grid backtests.
Configure your MultiGridStrike controller and backtest parameters, then run.

Example usage:
    python scripts/backtester/grid/run_grid_backtest.py
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.strategy_v2.executors.position_executor.data_types import TripleBarrierConfig

from controllers.generic.multi_grid_strike import GridConfig, MultiGridStrikeConfig
from backtester.grid.data_types import GridBacktestConfig
from backtester.grid.grid_backtester import GridControllerBacktester

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_datetime(date_str: str) -> int:
    """
    Parse datetime string to Unix timestamp in milliseconds.

    Args:
        date_str: Date string in format "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS"

    Returns:
        Unix timestamp in milliseconds
    """
    try:
        if len(date_str) == 10:  # YYYY-MM-DD
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:  # YYYY-MM-DD HH:MM:SS
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

        # Make timezone aware (UTC)
        dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError as e:
        raise ValueError(f"Invalid date format '{date_str}': {e}")


def create_sample_controller_config() -> MultiGridStrikeConfig:
    """
    Create a sample MultiGridStrike configuration.

    Modify this function to customize your grid strategy.
    """
    return MultiGridStrikeConfig(
        connector_name="orderly_perpetual",
        trading_pair="BTC-USD",
        total_amount_quote=Decimal("10000"),  # Total capital allocation
        leverage=100,

        # Define your grids
        grids=[
            GridConfig(
                grid_id="buy_1",
                start_price=Decimal("87000"),
                end_price=Decimal("90000"),
                limit_price=Decimal("84500"),
                side=TradeType.BUY,
                amount_quote_pct=Decimal("1.0"),  # 100% of total capital
                enabled=True,
            ),
        ],

        # Grid parameters
        min_spread_between_orders=Decimal("0.0005"),  # 0.05%
        min_order_amount_quote=Decimal("200"),
        max_open_orders=10,
        max_orders_per_batch=4,
        order_frequency=1,  # seconds
        activation_bounds=Decimal("0.002"),  # 2%
        keep_position=True,

        # Risk management
        triple_barrier_config=TripleBarrierConfig(
            take_profit=Decimal("0.0005"),  # 2%
            stop_loss=Decimal("0.05"),  # 5%
            time_limit=432000,  # 1 hour
            open_order_type=OrderType.LIMIT_MAKER,
            take_profit_order_type=OrderType.LIMIT,
            stop_loss_order_type=OrderType.MARKET,
            time_limit_order_type=OrderType.MARKET,
        ),
    )


def create_backtest_config() -> GridBacktestConfig:
    """
    Create backtest configuration.

    Modify this function to customize your backtest parameters.
    """
    return GridBacktestConfig(
        connector_name="binance",  # Use spot connector for data
        trading_pair="BTC-USDC",
        candle_interval="1s",
        backtest_resolution=1,  # 1 second resolution

        # Time range
        start_timestamp=parse_datetime("2026-01-27 18:00:00"),
        end_timestamp=parse_datetime("2026-01-27 20:00:00"),

        # Market simulation
        spread_bps=Decimal("4"),  # 0.05% spread
        trade_fee_bps=Decimal("4"),  # 0.04% trading fee

        # Order book simulation
        orderbook_levels=10,
        level_spacing_bps=Decimal("2"),
        level_size=Decimal("100"),
    )


def print_results(result):
    """Print backtest results"""
    print("\n" + "=" * 80)
    print("BACKTEST RESULTS")
    print("=" * 80)

    print(f"\nTotal PnL: {result.total_pnl:.2f} USDT")
    print(f"Total Fees: {result.total_fees:.2f} USDT")
    print(f"Total Volume: {result.total_volume:.2f} USDT")
    print(f"Total Trades: {result.total_trades}")
    print(f"Final Capital: {result.final_capital:.2f} USDT")

    print("\n" + "-" * 80)
    print("EXECUTOR RESULTS")
    print("-" * 80)

    for executor_result in result.executor_results:
        print(f"\nExecutor: {executor_result.executor_id}")
        print(f"  Side: {executor_result.config.side.name}")
        print(f"  Price Range: {executor_result.config.start_price} - {executor_result.config.end_price}")
        print(f"  Net PnL: {executor_result.net_pnl_quote:.2f} USDT ({executor_result.net_pnl_pct:.2%})")
        print(f"  Realized PnL: {executor_result.realized_pnl_quote:.2f} USDT")
        print(f"  Fees: {executor_result.realized_fees_quote:.2f} USDT")
        print(f"  Volume: {executor_result.total_volume:.2f} USDT")
        print(f"  Levels: {executor_result.levels_completed}/{executor_result.total_levels} completed")
        print(f"  Fills: {len(executor_result.fills)}")
        print(f"  Close Type: {executor_result.close_type.name if executor_result.close_type else 'N/A'}")

    print("\n" + "=" * 80)

    # Save equity curve
    equity_file = "grid_backtest_equity_curve.csv"
    result.equity_curve.to_csv(equity_file, index=False)
    print(f"\nEquity curve saved to: {equity_file}")

    # Save fills
    if result.executor_results:
        all_fills = []
        for executor_result in result.executor_results:
            for fill in executor_result.fills:
                all_fills.append({
                    'timestamp': fill.timestamp,
                    'executor_id': fill.executor_id,
                    'order_id': fill.order_id,
                    'side': fill.side.name,
                    'order_type': fill.order_type.name,
                    'price': float(fill.price),
                    'amount': float(fill.amount),
                    'fee': float(fill.fee),
                    'realized_pnl': float(fill.realized_pnl),
                    'cumulative_pnl': float(fill.cumulative_pnl),
                })

        if all_fills:
            import pandas as pd
            fills_df = pd.DataFrame(all_fills)
            fills_file = "grid_backtest_fills.csv"
            fills_df.to_csv(fills_file, index=False)
            print(f"Fills saved to: {fills_file}")


async def main():
    """Main entry point"""
    logger.info("Starting grid backtest...")

    # Create configurations
    controller_config = create_sample_controller_config()
    backtest_config = create_backtest_config()

    # Create backtester
    backtester = GridControllerBacktester(controller_config, backtest_config)

    # Initialize data
    logger.info("Initializing data...")
    await backtester.initialize_data()

    # Run backtest
    logger.info("Running backtest...")
    result = await backtester.run()

    # Print results
    print_results(result)

    logger.info("Backtest complete!")


if __name__ == "__main__":
    asyncio.run(main())
