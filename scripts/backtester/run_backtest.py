#!/usr/bin/env python
"""
Entry point script for running backtests on MMGrid strategy.

Configure all backtest parameters in the main() function.

Example:
    python scripts/backtester/run_backtest.py
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal

import pandas as pd

from scripts.backtester.mm_grid_backtester import MMGridBacktester
from scripts.backtester.data_types import BacktestConfig
from scripts.mm_grid_kodiak_target import MMGrid, MMGridConfig


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_datetime(dt_str: str) -> int:
    """Parse datetime string (YYYY-MM-DD HH:MM:SS or YYYY-MM-DD) to Unix timestamp"""
    try:
        if len(dt_str) == 10:  # YYYY-MM-DD
            dt = datetime.strptime(dt_str, "%Y-%m-%d")
        else:  # YYYY-MM-DD HH:MM:SS
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        return int(dt.timestamp())
    except ValueError:
        raise ValueError(f"Invalid datetime format: {dt_str}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")


async def main():
    """Run backtest"""

    # Configure strategy parameters
    strategy_config = MMGridConfig(
        exchange="orderly_perpetual",
        trading_pair="ZEC-USD",
        order_size=[Decimal("0.15")],
        bid_spread_levels=[Decimal("0.0005")],
        ask_spread_levels=[Decimal("0.0005")],
        order_refresh_time=6,
        order_cooldown=20,
        max_inventory=Decimal("0.5"),
        min_inventory_pct_for_adjustment=Decimal("0.25"),
        max_price_adjustment=Decimal("0.001"),
        max_spread_mult=Decimal("1.5"),
        randomization=Decimal("0"),
        leverage=100,
        ema_window=10,
        target_inventory=Decimal("0.0"),
    )

    # Configure backtest parameters
    backtest_config = BacktestConfig(
        # Data source
        connector_name="binance",
        trading_pair="ZEC-USDC",
        candle_pair="ZEC-USDC",
        candle_interval="1s",
        backtest_resolution=1,  # Process strategy every N seconds.

        # Time range (Unix timestamps)
        start_timestamp=1769062392,
        end_timestamp=1769069592,
        # Or use: start_timestamp=parse_datetime("2024-01-01 00:00:00"),

        # Market simulation
        spread_bps=Decimal("5"),
        trade_fee_bps=Decimal("4"),

        # Order book simulation
        orderbook_levels=10,
        level_spacing_bps=Decimal("2"),
        level_size=Decimal("100"),

        # Initial state
        initial_position=Decimal("0"),
        initial_capital=Decimal("10000"),
    )

    # Print configuration
    logger.info("=" * 60)
    logger.info("BACKTEST CONFIGURATION")
    logger.info("=" * 60)
    logger.info(f"Trading pair: {strategy_config.trading_pair}")
    logger.info(f"Connector: {backtest_config.connector_name}")
    logger.info(f"Time range: {datetime.fromtimestamp(backtest_config.start_timestamp)} to {datetime.fromtimestamp(backtest_config.end_timestamp)}")
    logger.info(f"Candle interval: {backtest_config.candle_interval}")
    logger.info(f"Backtest resolution: {backtest_config.backtest_resolution}s")
    logger.info(f"Order size: {strategy_config.order_size[0]}")
    logger.info(f"Spreads: bid={strategy_config.bid_spread_levels[0]}, ask={strategy_config.ask_spread_levels[0]}")
    logger.info(f"Refresh time: {strategy_config.order_refresh_time}s, Cooldown: {strategy_config.order_cooldown}s")
    logger.info(f"Initial capital: {backtest_config.initial_capital}")
    logger.info("=" * 60)

    # Create strategy instance for backtest (bypasses connector initialization)
    strategy = MMGrid.create_for_backtest(strategy_config)

    # Create backtester
    backtester = MMGridBacktester(
        strategy=strategy,
        strategy_config=strategy_config,
        backtest_config=backtest_config
    )

    # Initialize data (fetch candles and trading rules)
    logger.info("\nInitializing data...")
    await backtester.initialize_data()

    # Run backtest
    logger.info("\nRunning backtest...")
    result = backtester.run()

    # Print results
    logger.info("\n" + "=" * 60)
    logger.info("BACKTEST RESULTS")
    logger.info("=" * 60)
    logger.info(f"Total PnL: {result.total_pnl:.4f} ({result.total_pnl_pct:.2f}%)")
    logger.info(f"Total Trades: {result.total_trades}")
    logger.info(f"Total Volume: {result.total_volume:.2f}")
    logger.info(f"Profit Factor: {result.profit_factor:.2f}")
    logger.info(f"Final Position: {result.final_position}")
    logger.info(f"Final Equity: {result.final_equity:.2f}")
    logger.info("=" * 60)

    # Save results (optional)
    # result.equity_curve.to_csv("backtest_equity_curve.csv", index=False)
    # logger.info("\nEquity curve saved to backtest_equity_curve.csv")

    # return result

    # Create CSV of fills
    if result.fills:
        fills_data = []
        for fill in result.fills:
            fills_data.append({
                "timestamp": fill.timestamp,
                "datetime": datetime.fromtimestamp(fill.timestamp).strftime("%Y-%m-%d %H:%M:%S"),
                "order_id": fill.order_id,
                "trading_pair": fill.trading_pair,
                "side": fill.side.name,  # Convert TradeType enum to string
                "price": float(fill.price),
                "amount": float(fill.amount),
                "fee": float(fill.fee),
                "position_after": float(fill.position_after),
                "realized_pnl": float(fill.realized_pnl),
                "cumulative_pnl": float(fill.cumulative_pnl),
            })
        fills_df = pd.DataFrame(fills_data)
        fills_df.to_csv("backtest_fills.csv", index=False)
        logger.info("\nFills saved to backtest_fills.csv")
    else:
        logger.info("\nNo fills to save")
    
    


if __name__ == "__main__":
    asyncio.run(main())
