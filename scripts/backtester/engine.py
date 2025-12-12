"""
Backtest engine - orchestrates the backtest loop and order management.
"""

from decimal import Decimal
from typing import Dict, Optional
import pandas as pd
import asyncio

from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.core.utils.estimate_fee import build_perpetual_trade_fee
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig, HistoricalCandlesConfig

from scripts.backtester.mock_connector import MockConnector
from scripts.backtester.report import BacktestReport, Fill
from scripts.mm_grid_kodiak_target import MMGrid, MMGridConfig


# TODO-> add an optional lag to create orders after a tick is processed

class BacktestEngine:
    """Main backtest engine that simulates strategy execution on historical data"""

    def __init__(self, config: MMGridConfig, kline_path: Optional[str] = None, 
                 start_time: Optional[int] = None, end_time: Optional[int] = None,
                 tick_interval: int = 1, fill_mode: str = "high_low"):
        """
        Initialize backtest engine.

        Args:
            config: MMGridConfig with strategy parameters
            kline_path: Optional path to CSV file with kline data (timestamp, open, high, low, close, volume).
                       If None, data will be fetched on demand from Binance spot.
            start_time: Start timestamp in seconds (required if kline_path is None)
            end_time: End timestamp in seconds (required if kline_path is None)
            tick_interval: Seconds between ticks (default 1 second)
            fill_mode: "close_only" or "high_low" - determines fill detection logic
        """
        self.backtesting_resolution = tick_interval  # Default 1 second
        self.fill_mode = fill_mode  # "close_only" or "high_low"
        self.report = BacktestReport()
        self.config = config
        self.kline_path = kline_path
        self.start_time = start_time
        self.end_time = end_time
        self.klines = None  # Will be set by _load_data()
        self._candle_interval = 1  # Default to 1 second for Binance 1s data

        # Create mock connector and strategy
        self.mock_connector = MockConnector(config.trading_pair)
        self.strategy = MMGrid(
            connectors={config.exchange: self.mock_connector},
            config=config
        )
        self.strategy.reset_state()

    async def _fetch_binance_candles(self, trading_pair: str, start_time: int, end_time: int) -> pd.DataFrame:
        """
        Fetch Binance spot 1s candles for the given time range.
        
        Args:
            trading_pair: Trading pair (e.g., "BTC-USDC")
            start_time: Start timestamp in seconds
            end_time: End timestamp in seconds
            
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume, ...
        """
        # Create candle feed for Binance spot with 1s interval
        candle_feed = CandlesFactory.get_candle(
            CandlesConfig(
                connector="binance",
                trading_pair=trading_pair,
                interval="1s",
                max_records=1000  # Max records per request
            )
        )
        
        # Fetch historical candles
        candles_df = await candle_feed.get_historical_candles(
            config=HistoricalCandlesConfig(
                connector_name="binance",
                trading_pair=trading_pair,
                interval="1s",
                start_time=start_time,
                end_time=end_time,
            )
        )
        
        return candles_df

    def _load_data(self):
        """Load kline data from CSV file or prepare for on-demand fetching"""
        if self.kline_path:
            # Load from CSV file
            self.klines = pd.read_csv(self.kline_path)
            # Detect candle interval from data (assume uniform spacing)
            if len(self.klines) >= 2:
                self._candle_interval = int(self.klines.iloc[1]['timestamp'] - self.klines.iloc[0]['timestamp'])
            else:
                self._candle_interval = 60  # Default to 1 minute
        else:
            # Validate required parameters for on-demand fetching
            if self.start_time is None or self.end_time is None:
                raise ValueError("start_time and end_time are required when kline_path is not provided")
            if self.start_time >= self.end_time:
                raise ValueError("start_time must be less than end_time")
            # Data will be fetched on demand in run()
            self._candle_interval = 1  # 1 second for Binance 1s data

    async def _initialize_data(self):
        """Initialize data - fetch from Binance if kline_path is not provided"""
        if self.kline_path is None:
            print(f"Fetching Binance spot 1s candles for {self.config.trading_pair}...")
            print(f"Time range: {self.start_time} to {self.end_time}")
            # TODO -> take candlestick config in backtesting config
            self.klines = await self._fetch_binance_candles(
                self.config.trading_pair,
                self.start_time,
                self.end_time
            )
            print(f"Fetched {len(self.klines)} candles")
        else:
            self._load_data()
    
    async def run_async(self) -> BacktestReport:
        """Run the backtest at 1-second resolution (async version)"""
        # Initialize data if not already loaded
        if self.klines is None:
            await self._initialize_data()
        
        return self._run_backtest()

    def run(self) -> BacktestReport:
        """Run the backtest at 1-second resolution (synchronous wrapper)"""
        # Initialize data if not already loaded
        if self.klines is None:
            if self.kline_path:
                self._load_data()
            else:
                # Run async initialization - use asyncio.run() which handles event loop creation
                asyncio.run(self._initialize_data())
        
        return self._run_backtest()

    def _run_backtest(self) -> BacktestReport:
        """Internal method to run the backtest loop"""
        start_time = int(self.klines['timestamp'].min())
        end_time = int(self.klines['timestamp'].max())
        current_time = start_time

        while current_time <= end_time:
            # 1. Get candle for current timestamp (constant close price within candle)
            candle = self._get_candle_for_timestamp(current_time)
            if candle is None:
                current_time += self.backtesting_resolution
                continue

            # 2. Update mock connector with current candle and timestamp
            self.mock_connector.set_candle(candle)
            self.mock_connector.set_timestamp(current_time)

            # 3. Check fills against tracked orders (uses real ClientOrderTracker)
            active_orders = self.mock_connector._order_tracker.active_orders.copy()
            for order_id, order in active_orders.items():
                if self._should_fill(order, candle):
                    # Remove from ClientOrderTracker
                    self.mock_connector._order_tracker.stop_tracking_order(order_id)

                    # Create fill data
                    fill = Fill(
                        timestamp=current_time,
                        side=order.trade_type,
                        order_price=order.price,
                        fill_price=order.price,  # Fill at order price
                        amount=order.amount,
                        order=order,
                        candle_open=candle.get('open', 0),
                        candle_high=candle.get('high', 0),
                        candle_low=candle.get('low', 0),
                        candle_close=candle.get('close', 0),
                        candle_volume=candle.get('volume', 0),
                        inventory_after=Decimal("0")  # Will be set later
                    )

                    # Create fill event (same format as live trading)
                    fill_event = self._create_fill_event(fill, current_time)

                    # DIRECT method call to strategy (bypasses Cython PubSub)
                    # This works because:
                    # - did_fill_order() is a Python method on StrategyPyBase
                    # - Strategy code is identical - it uses the same hook
                    # - Only difference is HOW the method gets called (direct vs event)
                    self.strategy.did_fill_order(fill_event)

                    # Update position and record
                    self.mock_connector.update_position(fill.amount, fill.side)

                    # Get current inventory after fill
                    inventory = self.strategy._get_current_inventory()
                    fill.inventory_after = inventory
                    self.report.record_fill(fill, candle, inventory)

            # 4. Strategy tick - calls connector methods normally
            self.strategy.current_timestamp = current_time
            self.strategy.tick(current_time)
            # Strategy calls batch_order_create → MockConnector.buy()/sell() → ClientOrderTracker tracks orders
            # Strategy calls batch_order_cancel → MockConnector.cancel() → ClientOrderTracker removes orders

            current_time += self.backtesting_resolution

        return self.report

    def _get_candle_for_timestamp(self, timestamp: int) -> Optional[dict]:
        """Get candle containing this timestamp (constant close within candle)"""
        candle_start = (timestamp // self._candle_interval) * self._candle_interval
        match = self.klines[self.klines['timestamp'] == candle_start]
        return match.iloc[0].to_dict() if not match.empty else None

    def _should_fill(self, order: InFlightOrder, candle: dict) -> bool:
        """Determine if an order should fill based on candle data"""
        order_price = float(order.price)

        if self.fill_mode == "high_low":
            # Buy fills if candle low <= order price
            if order.trade_type == TradeType.BUY and candle['low'] <= order_price:
                return True
            # Sell fills if candle high >= order price
            elif order.trade_type == TradeType.SELL and candle['high'] >= order_price:
                return True
        # Note: close_only mode removed - use high_low which is more realistic

        return False
    
    def _create_fill_event(self, fill: Fill, timestamp: float) -> OrderFilledEvent:
        """Create OrderFilledEvent from Fill for strategy hook"""
        # Use order's client_order_id as order_id
        order_id = fill.order.client_order_id

        # Create trade fee (maker fee since these are limit orders)
        base, quote = fill.order.trading_pair.split("-")
        trade_fee = build_perpetual_trade_fee(
            exchange=self.config.exchange,
            is_maker=True,
            position_action=PositionAction.OPEN,
            base_currency=base,
            quote_currency=quote,
            order_type=OrderType.LIMIT_MAKER,
            order_side=fill.side,
            amount=fill.amount,
            price=fill.fill_price
        )

        return OrderFilledEvent(
            timestamp=timestamp,
            order_id=order_id,
            trading_pair=fill.order.trading_pair,
            trade_type=fill.side,
            order_type=OrderType.LIMIT_MAKER,
            price=fill.fill_price,
            amount=fill.amount,
            trade_fee=trade_fee,
            exchange_trade_id=fill.order.exchange_order_id,
            exchange_order_id=fill.order.exchange_order_id,
            leverage=self.config.leverage,
            position=PositionAction.OPEN.value
        )

