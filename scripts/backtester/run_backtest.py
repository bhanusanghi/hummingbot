#!/usr/bin/env python
"""
Entry point script for running backtests on market making strategies.

Configure all backtest parameters in the BacktestConfig instance within the main() function.

Supports two data sources:
    1. CSV file with kline data (set kline_path)
    2. On-demand fetching from Binance spot (set start_time and end_time)

Example configuration:
    config = BacktestConfig(
        kline_path="data/sample_btc_usdc_1m.csv",
        fill_mode="high_low",
        trading_pair="BTC-USDC",
        order_size=Decimal("0.1"),
        ...
    )

Or for on-demand data:
    config = BacktestConfig(
        start_time=1704067200,
        end_time=1704070800,
        ...
    )
"""

import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from datetime import datetime
from typing import Optional

from scripts.backtester.engine import BacktestEngine
from scripts.mm_grid_kodiak_target import MMGridConfig


@dataclass
class BacktestConfig:
    """Consolidated runtime configuration for backtesting"""
    # Data source configuration
    kline_path: Optional[str] = None
    start_time: Optional[int] = None
    end_time: Optional[int] = None

    # Backtest execution parameters
    fill_mode: str = "high_low"  # "close_only" or "high_low"
    backtest_resolution: int = 1  # Seconds between ticks
    output_path: Optional[str] = None

    # Strategy parameters (MMGridConfig)
    trading_pair: str = "BTC-USDC"
    exchange: str = "orderly_perpetual"
    order_size: Decimal = Decimal("0.1")
    bid_spread: Decimal = Decimal("0.001")
    ask_spread: Decimal = Decimal("0.001")
    order_refresh_time: int = 10
    order_cooldown: int = 30
    max_inventory: Decimal = Decimal("0.01")
    min_inventory_pct_for_adjustment: Decimal = Decimal("0.25")
    max_price_adjustment: Decimal = Decimal("0.001")
    max_spread_mult: Decimal = Decimal("1.5")
    randomization: Decimal = Decimal("0")
    leverage: int = 100
    ema_window: int = 10
    target_inventory: Decimal = Decimal("0.0")

    def to_strategy_config(self) -> MMGridConfig:
        """Convert BacktestConfig to MMGridConfig for strategy initialization"""
        return MMGridConfig(
            exchange=self.exchange,
            trading_pair=self.trading_pair,
            order_size=[self.order_size],
            bid_spread_levels=[self.bid_spread],
            ask_spread_levels=[self.ask_spread],
            order_refresh_time=self.order_refresh_time,
            order_cooldown=self.order_cooldown,
            max_inventory=self.max_inventory,
            min_inventory_pct_for_adjustment=self.min_inventory_pct_for_adjustment,
            max_price_adjustment=self.max_price_adjustment,
            max_spread_mult=self.max_spread_mult,
            randomization=self.randomization,
            leverage=self.leverage,
            ema_window=self.ema_window,
            target_inventory=self.target_inventory,
        )

    def get_output_path(self) -> str:
        """Get output path for results, using default if not specified"""
        if self.output_path:
            return self.output_path

        if self.kline_path:
            return str(Path(self.kline_path).parent / f"{Path(self.kline_path).stem}_results.csv")
        else:
            return f"backtest_results_{self.start_time}_{self.end_time}.csv"

    def validate(self) -> None:
        """Validate configuration"""
        if self.kline_path:
            path = Path(self.kline_path)
            if not path.exists():
                raise FileNotFoundError(f"Kline file not found: {self.kline_path}")
        else:
            if self.start_time is None or self.end_time is None:
                raise ValueError("start_time and end_time are required when kline_path is not provided")
            if self.start_time >= self.end_time:
                raise ValueError("start_time must be less than end_time")


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

def main():
    # Create BacktestConfig with all runtime parameters
    config = BacktestConfig(
        # Data source: use either kline_path OR (start_time and end_time)
        kline_path=None,  # e.g., "data/sample_btc_usdc_1m.csv"
        start_time=1704067200,  # Unix timestamp, or use parse_datetime("2024-01-01 00:00:00")
        end_time=1704070800,    # Unix timestamp, or use parse_datetime("2024-01-01 01:00:00")

        # Backtest execution parameters
        fill_mode="close_only",   # "close_only" or "high_low"
        backtest_resolution=1,        # Seconds between ticks
        output_path=None,       # Auto-generated if None

        # Strategy parameters
        trading_pair="ZEC-USDC",
        order_size=Decimal("0.15"),
        bid_spread=Decimal("0.0005"),
        ask_spread=Decimal("0.0005"),
        order_refresh_time=6,
        order_cooldown=20,
    )

    # Validate configuration
    try:
        config.validate()
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Print backtest configuration
    if config.kline_path:
        print(f"Running backtest on CSV file: {config.kline_path}")
    else:
        print(f"Running backtest with on-demand data fetching from Binance spot")
        print(f"Time range: {datetime.fromtimestamp(config.start_time)} to {datetime.fromtimestamp(config.end_time)}")
    print(f"Fill mode: {config.fill_mode}")
    print(f"Trading pair: {config.trading_pair}")
    print(f"Order size: {config.order_size}")
    print(f"Spreads: bid={config.bid_spread}, ask={config.ask_spread}")
    print(f"Refresh time: {config.order_refresh_time}s, Cooldown: {config.order_cooldown}s")
    print()

    # Run backtest with strategy config from BacktestConfig
    engine = BacktestEngine(
        config=config.to_strategy_config(),
        kline_path=config.kline_path,
        start_time=config.start_time,
        end_time=config.end_time,
        tick_interval=config.backtest_resolution,
        fill_mode=config.fill_mode
    )
    report = engine.run()

    # Print summary
    report.print_summary()

    # Save results
    output_path = config.get_output_path()
    report.save(output_path)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()

