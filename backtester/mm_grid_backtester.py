"""
MMGrid Backtesting Engine

This backtester is specifically designed for mm_grid_kodiak_target strategy.
It pre-computes all data into TickData and calls the SAME strategy.create_proposal()
method used in live trading.

Key design:
- Uses MarketDataProvider to fetch historical candles and trading rules
- All market data pre-compiled into TickData
- Strategy's create_proposal() is a PURE FUNCTION of TickData + config
- Engine handles: fills, position tracking, PnL, timing state, quantization
- Same create_proposal() works for both live and backtest
"""

import json
import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np
import pandas as pd

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.core.data_type.order_candidate import PerpetualOrderCandidate
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.data_feed.market_data_provider import MarketDataProvider

from backtester.data_types import (
    BacktestConfig,
    BacktestResult,
    Fill,
    OrderStatus,
    SimulatedOrder,
    TickData,
)

if TYPE_CHECKING:
    from scripts.mm_grid_kodiak_target import MMGrid, MMGridConfig

logger = logging.getLogger(__name__)


def sign(x) -> int:
    """Return sign of number: -1, 0, or 1"""
    return (x > 0) - (x < 0)


class MMGridBacktester:
    """
    Backtesting engine for MMGrid strategy.

    Usage:
        config = BacktestConfig(candles_path="data/btc_1m.csv", ...)
        strategy = MMGrid.create_for_backtest(strategy_config)  # Bypasses connector init
        backtester = MMGridBacktester(strategy, strategy_config, config)
        result = backtester.run()
    """

    # Cache directory for storing candlestick data
    CACHE_DIR = Path("backtest_cache")

    def __init__(
        self,
        strategy: "MMGrid",
        strategy_config: "MMGridConfig",
        backtest_config: BacktestConfig,
    ):
        self.strategy = strategy
        self.strategy_config = strategy_config
        self.config = backtest_config

        # Initialize MarketDataProvider for fetching candles and trading rules
        # berizard - why is this not using backtesting data provider with correct connector?
        self.market_data_provider = MarketDataProvider(connectors={})

        # Fetch trading rules and candles (done in separate async method)
        self.trading_rule: Optional[TradingRule] = None
        self.candles: Optional[pd.DataFrame] = None

        # Engine state - position and PnL
        self.position: Decimal = backtest_config.initial_position
        self.entry_price: Optional[Decimal] = None # TODO - We will have muliple entries and exits. this is shit
        self.realized_pnl: Decimal = Decimal("0")
        self.initial_capital: Decimal = backtest_config.initial_capital

        # Engine state - orders
        self.orders: Dict[str, SimulatedOrder] = {}
        self._order_counter: int = 0

        # berizard - why is this outside tick data at all
        # Engine state - timing (passed to TickData for strategy to check)
        self._last_order_created_timestamp: int = 0
        self._last_fill_timestamp: int = 0
        self._last_fill_direction: int = 0  # -1 (sell), 0 (none), 1 (buy)
        self._is_last_fill_in_same_direction: bool = False
        
        # EMA state (engine manages this for backtest)
        self._mid_history: List[Decimal] = []
        self._ema_mid: Decimal = Decimal("0")


        # Results tracking
        self.fills: List[Fill] = []
        self.equity_curve: List[Dict] = []

        # PnL tracking without fees
        self.realized_pnl_before_fees: Decimal = Decimal("0")

    def _get_cache_filepath(self) -> Path:
        """
        Generate cache filepath based on connector, trading_pair, and interval.

        Format: connector_tradingpair_interval.json
        Example: binance_BTCUSDC_1s.json
        """
        # Create cache directory if it doesn't exist
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Remove hyphens and make lowercase for filename
        trading_pair_clean = self.config.candle_pair.replace("-", "")
        connector_clean = self.config.connector_name.replace("_", "")
        interval_clean = self.config.candle_interval

        filename = f"{connector_clean}_{trading_pair_clean}_{interval_clean}.json"
        return self.CACHE_DIR / filename

    def _load_cached_candles(self) -> Optional[Dict]:
        """
        Load cached candles from JSON file.

        Returns:
            Dict with 'metadata' and 'data' keys, or None if cache doesn't exist
        """
        cache_file = self._get_cache_filepath()

        if not cache_file.exists():
            logger.info(f"No cache file found at {cache_file}")
            return None

        try:
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)

            logger.info(f"Loaded cache from {cache_file}")
            logger.info(f"Cache range: {cache_data['metadata']['from']} to {cache_data['metadata']['to']}")

            return cache_data
        except Exception as e:
            logger.warning(f"Failed to load cache from {cache_file}: {e}")
            return None

    def _save_candles_to_cache(self, candles_df: pd.DataFrame) -> None:
        """
        Save or append candles to cache file.

        Args:
            candles_df: DataFrame with candle data including 'timestamp' column
        """
        if candles_df is None or len(candles_df) == 0:
            logger.warning("No candles to cache")
            return

        cache_file = self._get_cache_filepath()

        # Convert DataFrame to dict format for JSON storage
        # data structure: {timestamp: {candle_data}}
        new_data = {}
        for idx, row in candles_df.iterrows():
            timestamp = int(row['timestamp'])
            candle_data = {
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
            }
            new_data[str(timestamp)] = candle_data

        # Load existing cache or create new
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cache = json.load(f)

                # Merge new data with existing
                cache['data'].update(new_data)

                # Update metadata timestamps
                all_timestamps = [int(ts) for ts in cache['data'].keys()]
                cache['metadata']['from'] = min(all_timestamps)
                cache['metadata']['to'] = max(all_timestamps)

                logger.info(f"Appended {len(new_data)} candles to existing cache")
            except Exception as e:
                logger.warning(f"Failed to load existing cache, creating new: {e}")
                cache = {
                    'metadata': {
                        'from': int(candles_df['timestamp'].min()),
                        'to': int(candles_df['timestamp'].max()),
                    },
                    'data': new_data
                }
        else:
            # Create new cache
            cache = {
                'metadata': {
                    'from': int(candles_df['timestamp'].min()),
                    'to': int(candles_df['timestamp'].max()),
                },
                'data': new_data
            }
            logger.info(f"Created new cache with {len(new_data)} candles")

        # Save to file
        try:
            with open(cache_file, 'w') as f:
                json.dump(cache, f, indent=2)
            logger.info(f"Saved cache to {cache_file}")
            logger.info(f"Total cached candles: {len(cache['data'])}")
        except Exception as e:
            logger.error(f"Failed to save cache to {cache_file}: {e}")


    async def initialize_data(self):
        """
        Fetch historical candles and trading rules from exchange via MarketDataProvider.

        Uses caching to store candles and only fetches missing data.
        This must be called before run() since it fetches data asynchronously.
        """
        logger.info(f"Initializing data for {self.config.trading_pair} from {self.config.connector_name}...")

        # Fetch trading rules (async to ensure they're loaded)
        logger.info(f"Fetching trading rules for {self.config.trading_pair}...")
        self.trading_rule = await self.market_data_provider.get_trading_rules_async(
            self.strategy_config.exchange,
            self.config.trading_pair
        )

        # Load cached candles
        cache = self._load_cached_candles()

        all_candles = []

        if cache is not None:
            # Convert cache data back to DataFrame
            cached_data = []
            for timestamp_str, candle_data in cache['data'].items():
                candle_data['timestamp'] = int(timestamp_str)
                cached_data.append(candle_data)

            if cached_data:
                cached_df = pd.DataFrame(cached_data)
                logger.info(f"Loaded {len(cached_df)} candles from cache")

                # Determine what ranges we need to fetch
                cache_start = cache['metadata']['from']
                cache_end = cache['metadata']['to']

                fetch_ranges = []

                # Need data before cache?
                if self.config.start_timestamp < cache_start:
                    fetch_ranges.append((self.config.start_timestamp, cache_start - 1))
                    logger.info(f"Need to fetch candles before cache: {self.config.start_timestamp} to {cache_start - 1}")

                # Need data after cache?
                if self.config.end_timestamp > cache_end:
                    fetch_ranges.append((cache_end + 1, self.config.end_timestamp))
                    logger.info(f"Need to fetch candles after cache: {cache_end + 1} to {self.config.end_timestamp}")

                # Fetch missing ranges
                for start, end in fetch_ranges:
                    logger.info(f"Fetching candles from {start} to {end}...")
                    new_candles = await self.market_data_provider.get_historical_candles_df(
                        connector_name=self.config.connector_name,
                        trading_pair=self.config.candle_pair,
                        interval=self.config.candle_interval,
                        start_time=start,
                        end_time=end,
                    )

                    if new_candles is not None and len(new_candles) > 0:
                        logger.info(f"Fetched {len(new_candles)} new candles")
                        all_candles.append(new_candles)
                        # Save new candles to cache
                        self._save_candles_to_cache(new_candles)

                # Add cached candles to the list
                all_candles.append(cached_df)
        else:
            # No cache, fetch all data
            logger.info(f"No cache found, fetching all candles from {self.config.start_timestamp} to {self.config.end_timestamp}...")
            new_candles = await self.market_data_provider.get_historical_candles_df(
                connector_name=self.config.connector_name,
                trading_pair=self.config.candle_pair,
                interval=self.config.candle_interval,
                start_time=self.config.start_timestamp,
                end_time=self.config.end_timestamp,
            )

            if new_candles is not None and len(new_candles) > 0:
                logger.info(f"Fetched {len(new_candles)} candles")
                all_candles.append(new_candles)
                # Save to cache
                self._save_candles_to_cache(new_candles)

        # Merge all candles if we have multiple DataFrames
        if len(all_candles) == 0:
            raise ValueError(f"No candles available for {self.config.trading_pair}")

        if len(all_candles) == 1:
            self.candles = all_candles[0]
        else:
            # Concatenate and remove duplicates
            self.candles = pd.concat(all_candles, ignore_index=True)
            # Remove duplicate timestamps, keeping first occurrence
            self.candles = self.candles.drop_duplicates(subset=['timestamp'], keep='first')
            # Sort by timestamp
            self.candles = self.candles.sort_values('timestamp').reset_index(drop=True)
            logger.info(f"Merged candles, total: {len(self.candles)}")

        # Filter to requested range
        self.candles = self.candles[
            (self.candles['timestamp'] >= self.config.start_timestamp) &
            (self.candles['timestamp'] <= self.config.end_timestamp)
        ].reset_index(drop=True)

        if len(self.candles) == 0:
            raise ValueError(f"No candles in requested time range for {self.config.trading_pair}")

        logger.info(f"Final candle count: {len(self.candles)}")
        logger.info(f"Trading rules: min_order_size={self.trading_rule.min_order_size}, "
                   f"min_price_increment={self.trading_rule.min_price_increment}")

    def _create_synthetic_orderbook(
        self, best_price: Decimal, side: str
    ) -> pd.DataFrame:
        """
        Create synthetic order book levels from candle price.

        Args:
            best_price: Best bid or ask price
            side: 'bid' or 'ask'

        Returns:
            DataFrame with columns: price, amount, update_id
        """
        levels = []
        spacing = best_price * self.config.level_spacing_bps / Decimal("10000")

        for i in range(self.config.orderbook_levels):
            if side == "bid":
                price = best_price - (spacing * i)
            else:
                price = best_price + (spacing * i)

            levels.append(
                OrderBookRow(
                    float(price), float(self.config.level_size), 1  # update_id
                )
            )

        return pd.DataFrame(data=levels, columns=OrderBookRow._fields, dtype="float64")

    def calculate_tick_data(self, candle: pd.Series, timestamp: int) -> TickData:
        """
        Pre-compile all data needed for create_proposal.

        This is the key method that converts raw candle data into
        the TickData structure that strategy needs.

        The resulting TickData has the same structure as what
        strategy.process_tick_data() produces in live mode.
        """
        close = Decimal(str(candle["close"]))
        spread = close * self.config.spread_bps / Decimal("10000")

        # Synthetic best bid/ask from close price
        best_bid = close - spread / 2
        best_ask = close + spread / 2

        # Create synthetic order book depth
        bids_df = self._create_synthetic_orderbook(best_bid, "bid")
        asks_df = self._create_synthetic_orderbook(best_ask, "ask")

        # Mid price (weighted by size - but sizes are equal so just average)
        mid_price = (best_bid + best_ask) / 2

        return TickData(
            timestamp=timestamp,
            best_bid_price=best_bid,
            best_bid_size=self.config.level_size,
            best_ask_price=best_ask,
            best_ask_size=self.config.level_size,
            bids_df=bids_df,
            asks_df=asks_df,
            mid_price=mid_price,
            mark_price=close,  # Use close as mark price in backtest
            ema_mid=self._ema_mid,  # Pre-calculated EMA
            position=self.position,
            last_order_created_timestamp=self._last_order_created_timestamp,
            last_fill_timestamp=self._last_fill_timestamp,
            last_fill_direction=self._last_fill_direction,
            is_last_fill_in_same_direction=self._is_last_fill_in_same_direction,
            order_refresh_time=self.strategy_config.order_refresh_time,
            order_cooldown=self.strategy_config.order_cooldown,
        )

    def _update_ema(self, mid_price: Decimal) -> Decimal:
        """Update EMA of mid price - mirrors strategy logic"""
        self._mid_history.append(mid_price)

        max_len = self.strategy_config.ema_window
        if len(self._mid_history) > max_len:
            self._mid_history = self._mid_history[-max_len:]

        s = pd.Series([float(m) for m in self._mid_history])
        ema_val = s.ewm(span=self.strategy_config.ema_window, adjust=False).mean().iloc[-1]
        self._ema_mid = Decimal(str(ema_val))
        return self._ema_mid

    def _quantize_order_price(self, price: Decimal) -> Decimal:
        """Quantize order price according to exchange trading rules"""
        if not self.trading_rule:
            return price

        # Round to the nearest tick
        tick_size = self.trading_rule.min_price_increment
        return (price // tick_size) * tick_size

    def _quantize_order_amount(self, amount: Decimal) -> Decimal:
        """Quantize order amount according to exchange trading rules"""
        if not self.trading_rule:
            return amount

        # Round down to the nearest increment
        increment = self.trading_rule.min_base_amount_increment
        quantized = (amount // increment) * increment

        # Ensure it meets minimum order size
        if quantized < self.trading_rule.min_order_size:
            return Decimal("0")  # Order too small, will be filtered out

        return quantized

    def simulate_fills(self, candle: pd.Series) -> List[Tuple[SimulatedOrder, Decimal]]:
        """
        Check if any orders would fill given candle's high/low.

        Returns list of (order, fill_price) tuples.
        """
        fills = []
        high = Decimal(str(candle["high"]))
        low = Decimal(str(candle["low"]))

        for order in list(self.orders.values()):
            if order.status != OrderStatus.OPEN:
                continue

            fill_price = None

            if order.side == TradeType.BUY:
                # Buy limit fills if low touches order price
                if low <= order.price:
                    fill_price = order.price  # Assume fill at limit price
            else:
                # Sell limit fills if high touches order price
                if high >= order.price:
                    fill_price = order.price

            if fill_price is not None:
                fills.append((order, fill_price))

        return fills

    def process_fill(
        self, order: SimulatedOrder, fill_price: Decimal, timestamp: int
    ) -> Fill:
        """
        Process an order fill: update position, PnL, and cooldown.
        """
        fill_amount = order.remaining_amount
        fee = fill_amount * fill_price * self.config.trade_fee_bps / Decimal("10000")

        # Track PnL
        trade_pnl = Decimal("0")

        if order.side == TradeType.BUY:
            if self.position >= 0:
                # Adding to long or opening long
                if self.position == 0:
                    self.entry_price = fill_price
                else:
                    # Average entry price
                    total_cost = self.entry_price * self.position + fill_price * fill_amount
                    self.entry_price = total_cost / (self.position + fill_amount)
                self.position += fill_amount
            else:
                # Closing short
                close_amount = min(fill_amount, abs(self.position))
                trade_pnl = (self.entry_price - fill_price) * close_amount
                self.position += fill_amount
                if self.position >= 0:
                    self.entry_price = fill_price if self.position > 0 else None
        else:
            # SELL
            if self.position <= 0:
                # Adding to short or opening short
                if self.position == 0:
                    self.entry_price = fill_price
                else:
                    total_cost = self.entry_price * abs(self.position) + fill_price * fill_amount
                    self.entry_price = total_cost / (abs(self.position) + fill_amount)
                self.position -= fill_amount
            else:
                # Closing long
                close_amount = min(fill_amount, self.position)
                trade_pnl = (fill_price - self.entry_price) * close_amount
                self.position -= fill_amount
                if self.position <= 0:
                    self.entry_price = fill_price if self.position < 0 else None

        # Update realized PnL before and after fees
        self.realized_pnl_before_fees += trade_pnl
        self.realized_pnl += trade_pnl - fee

        # Update order status
        order.filled_amount += fill_amount
        order.status = OrderStatus.FILLED

        # Remove filled order
        if order.id in self.orders:
            del self.orders[order.id]

        # Update timing state (passed to TickData for strategy to check cooldown)
        self._last_fill_timestamp = timestamp
        self._last_fill_direction = 1 if order.side == TradeType.BUY else -1

        # Create fill record
        fill = Fill(
            timestamp=timestamp,
            order_id=order.id,
            trading_pair=order.trading_pair,
            side=order.side,
            price=fill_price,
            amount=fill_amount,
            fee=fee,
            position_after=self.position,
            realized_pnl=trade_pnl - fee,
            cumulative_pnl=self.realized_pnl,
            pnl_before_fees=trade_pnl,
            cumulative_pnl_before_fees=self.realized_pnl_before_fees,
        )
        self.fills.append(fill)

        logger.debug(
            f"Fill: {order.side.name} {fill_amount} @ {fill_price}, "
            f"PnL: {trade_pnl - fee:.4f}, Position: {self.position}"
        )

        return fill

    def process_proposals(
        self, proposals: List[PerpetualOrderCandidate], timestamp: int
    ) -> None:
        """
        Convert strategy proposals into simulated orders with quantization.
        Cancel all existing orders first (like real strategy does).
        """
        # Cancel all existing orders (strategy does cancel-all before placing)
        self.orders.clear()

        # Create new orders from proposals with quantization
        for proposal in proposals:
            # Quantize price and amount according to trading rules
            quantized_price = self._quantize_order_price(proposal.price)
            quantized_amount = self._quantize_order_amount(proposal.amount)

            # Skip orders that don't meet minimum requirements
            if quantized_amount == 0:
                logger.debug(f"Skipping order: amount {proposal.amount} too small after quantization")
                continue

            order_id = f"bt_{self._order_counter}"
            self._order_counter += 1

            order = SimulatedOrder(
                id=order_id,
                trading_pair=proposal.trading_pair,
                side=proposal.order_side,  # PerpetualOrderCandidate uses order_side
                price=quantized_price,
                amount=quantized_amount,
                order_type=proposal.order_type,
                created_at=timestamp,
            )
            self.orders[order_id] = order

        if self.orders:
            # Update timing state (passed to TickData for strategy to check refresh)
            self._last_order_created_timestamp = timestamp
            logger.debug(f"Placed {len(self.orders)} quantized orders at {timestamp}")


    def run(self) -> BacktestResult:
        """
        Execute the backtest.

        For each tick (based on backtest_resolution):
        1. Simulate fills from price action
        2. Update EMA (engine manages this state)
        3. Calculate TickData (includes EMA)
        4. Call strategy.create_proposal(tick_data) - SAME method as live!
        5. Process proposals into orders
        6. Record equity
        """
        if self.candles is None:
            raise RuntimeError("Must call initialize_data() before run()")

        logger.info(f"Starting backtest with {len(self.candles)} candles, "
                   f"resolution={self.config.backtest_resolution}s")

        # Build tick schedule based on backtest_resolution
        # We process the strategy tick every N seconds, but check fills against all candles
        tick_timestamps = []
        current_tick = self.config.start_timestamp
        while current_tick <= self.config.end_timestamp:
            tick_timestamps.append(current_tick)
            current_tick += self.config.backtest_resolution

        logger.info(f"Generated {len(tick_timestamps)} tick timestamps")

        # Create a mapping of timestamp -> candle for fast lookup
        candles_dict = {}
        for idx, candle in self.candles.iterrows():
            ts = int(candle["timestamp"])
            candles_dict[ts] = candle

        # Process each tick
        for tick_idx, tick_timestamp in enumerate(tick_timestamps):
            # Find the closest candle for this tick
            closest_ts = min(candles_dict.keys(), key=lambda t: abs(t - tick_timestamp))
            candle = candles_dict[closest_ts]

            # 1. Simulate fills from this candle's price action
            fills = self.simulate_fills(candle)
            for order, fill_price in fills:
                self.process_fill(order, fill_price, tick_timestamp)

            # 2. Update EMA (engine manages this state for backtest)
            mid = Decimal(str(candle["close"]))
            self._update_ema(mid)

            # 3. Calculate TickData (includes EMA, timing state)
            tick_data = self.calculate_tick_data(candle, tick_timestamp)

            # 4. Call strategy's create_proposal - SAME method used in live trading!
            proposals = self.strategy.create_proposal(tick_data)

            # 5. Process proposals if any
            if proposals:
                self.process_proposals(proposals, tick_timestamp)

            if tick_idx % 10000 == 0:
                logger.info(f"Processed {tick_idx}/{len(tick_timestamps)} ticks")

        logger.info("Backtest complete")
        return self._calculate_results()

    def _calculate_results(self) -> BacktestResult:
        """Calculate summary statistics from backtest"""
        equity_df = pd.DataFrame(self.equity_curve)

        if equity_df.empty:
            return BacktestResult(
                total_pnl=Decimal("0"),
                total_pnl_pct=Decimal("0"),
                total_trades=0,
                total_volume=Decimal("0"),
                # win_rate=Decimal("0"),
                profit_factor=Decimal("0"),
                # max_drawdown=Decimal("0"),
                # max_drawdown_pct=Decimal("0"),
                # sharpe_ratio=0.0,
                # equity_curve=equity_df,
                fills=self.fills,
                final_position=self.position,
                final_equity=self.initial_capital,
            )

        # Total PnL
        final_equity = Decimal(str(equity_df["equity"].iloc[-1]))
        total_pnl = final_equity - self.initial_capital
        total_pnl_pct = total_pnl / self.initial_capital * 100

        # Trade statistics
        total_trades = len(self.fills)
        total_volume = sum(f.amount * f.price for f in self.fills)

        # # Win rate
        # winning_trades = [f for f in self.fills if f.realized_pnl > 0]
        # win_rate = Decimal(len(winning_trades)) / Decimal(total_trades) if total_trades > 0 else Decimal("0")

        # Profit factor
        gross_profit = sum(f.realized_pnl for f in self.fills if f.realized_pnl > 0)
        gross_loss = abs(sum(f.realized_pnl for f in self.fills if f.realized_pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else Decimal("999")

        # # Drawdown
        # equity_series = equity_df["equity"]
        # peak = equity_series.expanding().max()
        # drawdown = equity_series - peak
        # max_drawdown = Decimal(str(abs(drawdown.min())))
        # max_drawdown_pct = max_drawdown / self.initial_capital * 100

        # # Sharpe ratio (simplified - daily returns assumed)
        # returns = equity_df["equity"].pct_change().dropna()
        # sharpe_ratio = float(returns.mean() / returns.std() * np.sqrt(252)) if len(returns) > 1 and returns.std() > 0 else 0.0

        return BacktestResult(
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            total_trades=total_trades,
            total_volume=total_volume,
            # win_rate=win_rate,
            profit_factor=profit_factor,
            # max_drawdown=max_drawdown,
            # max_drawdown_pct=max_drawdown_pct,
            # sharpe_ratio=sharpe_ratio,
            # equity_curve=equity_df,
            fills=self.fills,
            final_position=self.position,
            final_equity=final_equity,
        )
