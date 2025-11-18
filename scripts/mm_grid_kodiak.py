import random
import os
from decimal import Decimal
from typing import Dict, List, Optional

import pandas as pd
import pandas_ta as ta  # noqa: F401
from pydantic import Field

from hummingbot.client.config.config_data_types import BaseClientModel
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PriceType, PositionAction, PositionSide, TradeType
#from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.data_type.order_candidate import PerpetualOrderCandidate
from hummingbot.core.event.events import (
    OrderFilledEvent,
)
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.core.clock import Clock
from datetime import datetime

def _fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")

def sign(x):
    return (x > 0) - (x < 0)

class MMGridConfig(BaseClientModel):
    script_file_name: str = os.path.basename(__file__)
    exchange: str = Field("orderly_perpetual")
    trading_pair: str = Field("BTC-USDC")
    order_size: List[Decimal] = Field(default=[Decimal("0.1")])
    bid_spread_levels: List[Decimal] = Field(default=[Decimal("0.001")])
    ask_spread_levels: List[Decimal] = Field(default=[Decimal("0.001")])
    order_refresh_time: int = Field(10)
    order_cooldown: int = Field(30)
    max_inventory: Decimal = Field(0.01)
    min_inventory_pct_for_adjustment: Decimal = Field(default=Decimal("0.25"))
    max_price_adjustment: Decimal = Field(default=Decimal("0.001"))
    max_spread_mult: Decimal = Field(default=Decimal("1.5"))
    randomization: Decimal = Field(default=Decimal("0.25"))
    leverage: int = Field(100)
    order_tag: Optional[str] = Field(default="None")
    ema_window: int = Field(10)  # EMA period in candles
    candles_connector: str = Field("binance_perpetual")  # Connector for candles data
    candles_trading_pair: Optional[str] = Field(default="ZEC-USDT")  # Trading pair for candles (defaults to trading_pair)
    candles_interval: str = Field("1m")  # Candle interval
    candles_max_records: int = Field(20)  # Maximum number of candles to store
#    target_inventory: Decimal = Field(0.0)

class MMGrid(ScriptStrategyBase):
    """
    Market Making + Grid Strategy with Batch Order Operations

    Key features:
    - Price skew based on inventory
    - Size skew based on inventory
    - Size randomization
    - Cooldown on order fill to avoid immediate recenter
    - Ema to guide bid_anchor and ask_anchor
    - Multiple constant spread levels
    - Inventory management and tracking
    - Order lifecycle management via connector's order tracker
    - Batch order placement and cancel all
    """

    current_timestamp: float

    @classmethod
    def init_markets(cls, config: MMGridConfig):
        cls.markets = {config.exchange: {config.trading_pair}}

    def __init__(self, connectors: Dict[str, ConnectorBase], config: MMGridConfig):
        super().__init__(connectors)
        self.config = config
        self.account_config_set = False
        self.create_timestamp = 0
        self._cached_mark_price: Decimal = Decimal("0")
        self._cached_mid_price: Decimal = Decimal("0")
        self._cached_bid_anchor: Decimal = Decimal("0")
        self._cached_ask_anchor: Decimal = Decimal("0")
        self._cached_spread_mult: Decimal = Decimal("0")
        self._cached_random_factor: Decimal = Decimal("0")
        self._cached_skew_mult: Decimal = Decimal("0")
        self._cached_proposals: List[PerpetualOrderCandidate] = []
        self._cooldown_until_timestamp: int = 0
        self._last_trade = Decimal("0")
        self._cached_inventory: Decimal = Decimal("0")
        self._cached_inventory_ratio: Decimal = Decimal("0")
        self._ema_mid: Decimal = Decimal("0")
        self._bot_start_timestamp: int = 0  # Track when bot started for filtering position history
        
        # Initialize candles for EMA calculation
        candles_trading_pair = self.config.candles_trading_pair or self.config.trading_pair
        candles_config = CandlesConfig(
            connector=self.config.candles_connector,
            trading_pair=candles_trading_pair,
            interval=self.config.candles_interval,
            max_records=self.config.candles_max_records
        )
        self._candles = CandlesFactory.get_candle(candles_config)

    # Built-in event handler methods (called automatically by ScriptStrategyBase)

    def start(self, clock: Clock, timestamp: float):
        self._bot_start_timestamp = int(timestamp * 1000)  # Convert to milliseconds for comparison with API timestamps
        self._candles.start()  # Start candles feed
        self.apply_initial_setting()
        super().start(clock, timestamp)

    def stop(self, clock: Clock):
        """
        Called when bot is stopped (by Clock, after connectors are removed).
        """
        super().stop(clock)
    
    async def on_stop(self):
        """
        Called when bot is stopped. Export position history to CSV and stop candles feed.
        This is called BEFORE connectors are removed, so connector data is still available.
        """
        self._export_position_history()
        self._candles.stop()

    def apply_initial_setting(self):
        n = len(self.config.order_size)
        if len(self.config.bid_spread_levels) != n or len(self.config.ask_spread_levels) != n:
            raise ValueError("order_size, bid_spread_levels, and ask_spread_levels must have the same length")

        #TODO: add other config validations
        if not self.account_config_set:
            connector = self.connectors[self.config.exchange]
            connector.set_leverage(self.config.trading_pair, self.config.leverage)
            # Set order tag if configured
            if self.config.exchange == "orderly_perpetual" and self.config.order_tag and len(self.config.order_tag) > 0:
                self.logger().info(f"Setting order tag: {self.config.order_tag}")
                connector.set_order_tag(self.config.order_tag)
            self.account_config_set = True

    def on_tick(self):
        if self.current_timestamp > self.create_timestamp:
            proposals: List[PerpetualOrderCandidate] = self.create_proposal()
            self._cached_proposals = proposals
            if len(proposals) > 0:
                safe_ensure_future(self._cancel_and_place_orders(proposals))  # Execute cancel then place sequentially to avoid order accumulation
            self.create_timestamp = self.current_timestamp + self.config.order_refresh_time

    def create_proposal(self) -> List[PerpetualOrderCandidate]:
        connector = self.connectors[self.config.exchange]
        # Use mark price from exchange instead of mid price
        mark_price = connector.get_price_by_type(self.config.trading_pair, PriceType.MarkPrice)

        # Get order book
        order_book = connector.get_order_book(self.config.trading_pair)
        bids_df, asks_df = order_book.snapshot

        if bids_df.empty or asks_df.empty:
            self.logger().warning("Order book snapshot is empty, skipping proposal.")
            return []

        # Get best bid/ask prices and sizes
        best_bid_price = Decimal(str(bids_df.iloc[0].price))
        best_bid_size = Decimal(str(bids_df.iloc[0].amount))
        best_ask_price = Decimal(str(asks_df.iloc[0].price))
        best_ask_size = Decimal(str(asks_df.iloc[0].amount))

        mid_price = (best_bid_price + best_ask_price) / 2

        if best_bid_size + best_ask_size > 0:
            mid_price = ((best_bid_price * best_ask_size) + (best_bid_size * best_ask_price)) / (
                        best_bid_size + best_ask_size)

        ema = self._update_ema_from_candles()
        if (ema == 0):
            self.logger().warning("EMA is 0, skipping proposal.")
            return []

        inventory = self._detect_trade()
        inventory_ratio = self._inventory_ratio(inventory)
        skew_factor = Decimal("1") - inventory_ratio * self.config.max_price_adjustment

        bid_anchor = min(ema, mid_price, mark_price)
        ask_anchor = max(ema, mid_price, mark_price)

        # Cache values for status reporting
        self._cached_bid_anchor = bid_anchor
        self._cached_ask_anchor = ask_anchor
        self._cached_mark_price = mark_price
        self._cached_mid_price = mid_price
        self._cached_skew_mult = skew_factor
        self._cached_inventory_ratio = inventory_ratio

        spread_mult = Decimal("1") + abs(inventory_ratio) * (self.config.max_spread_mult - Decimal("1"))
        self._cached_spread_mult = spread_mult

        random_factor = self._random_factor()
        self._cached_random_factor = random_factor

        # After caching is done, check for cooldown
        if self.current_timestamp < self._cooldown_until_timestamp:
            return []

        orders = []
        for idx, bid_spread in enumerate(self.config.bid_spread_levels):
            ask_spread = self.config.ask_spread_levels[idx]

            # Spreads relative to top of book
            bid_price = bid_anchor * skew_factor * (Decimal("1") - bid_spread * spread_mult)
            ask_price = ask_anchor * skew_factor * (Decimal("1") + ask_spread * spread_mult)

            # To make sure the limit maker orders are not immediately taken
            # Only the offending side is adjusted, and placed at top of book

            bid_index = min(idx * 2, len(bids_df) - 1)
            ask_index = min(idx * 2, len(asks_df) - 1)

            if bid_price >= mid_price:
                bid_price = Decimal(str(bids_df.iloc[bid_index].price))

            if ask_price <= mid_price:
                ask_price = Decimal(str(asks_df.iloc[ask_index].price))

            size = self.config.order_size[idx] * random_factor

            # Adjust bid and ask size for inventory. Note: at max inventory, amount is zero
            bid_amount = size * (Decimal("1") - max(inventory_ratio, Decimal("0"))) # reduce bids if long
            ask_amount = size * (Decimal("1") - max(-inventory_ratio, Decimal("0"))) # reduce asks if short

            if bid_amount > 0:
                bid_order = PerpetualOrderCandidate(
                    trading_pair=self.config.trading_pair,
                    is_maker=True,
                    order_type=OrderType.LIMIT_MAKER,
                    order_side=TradeType.BUY,
                    amount=bid_amount,
                    price=bid_price,
                    leverage=Decimal(self.config.leverage)
                )
                orders.extend([bid_order])

            if ask_amount > 0:
                ask_order = PerpetualOrderCandidate(
                    trading_pair=self.config.trading_pair,
                    is_maker=True,
                    order_type=OrderType.LIMIT_MAKER,
                    order_side=TradeType.SELL,
                    amount=ask_amount,
                    price=ask_price,
                    leverage=Decimal(self.config.leverage)
                )
                orders.extend([ask_order])

        return orders

    # Detect trades, update order cooldown, and return current inventory
    def _detect_trade(self) -> Decimal:
        inventory = self._get_current_inventory()
        last_inventory = self._cached_inventory

        # Initialization
        if self._last_trade == 0 and last_inventory == 0:
            self._cached_inventory = inventory
            self._cooldown_until_timestamp = self.current_timestamp
            return inventory

        # change in position = trade
        trade = inventory - last_inventory

        if trade == 0:
            self._cached_inventory = inventory
            return inventory

        last_trade = self._last_trade
        same_direction_trade = sign(trade) == sign(last_trade)

        if sign(last_trade) == 0:
            self.logger().info(f"First trade: {trade:.4f}. Starting cooldown")
            self._cooldown_until_timestamp = self.current_timestamp + self.config.order_cooldown

        elif same_direction_trade:
            self.logger().info(f"Same-direction trade: {trade:.4f}. Add half cooldown")
            self._cooldown_until_timestamp = max(self.current_timestamp, self._cooldown_until_timestamp) + self.config.order_cooldown / 2

        else:
            self.logger().info(f"Opposite-direction trade: {trade:.4f}. Starting cooldown")
            self._cooldown_until_timestamp = self.current_timestamp + self.config.order_cooldown

        self._last_trade = trade
        self._cached_inventory = inventory
        return inventory

    def _update_ema_from_candles(self) -> Decimal:
        """
        Update EMA using candles data and pandas_ta.
        """
        candles_df = self._candles.candles_df
        
        if candles_df.empty or len(candles_df) < self.config.ema_window:
            self.logger().warning(f"Not enough candles for EMA ({len(candles_df)}/{self.config.ema_window}).")
            return Decimal("0")
        
        ema_column_name = f"EMA_{self.config.ema_window}"
        candles_df.ta.ema(length=self.config.ema_window, append=True)
        
        if ema_column_name in candles_df.columns:
            self._ema_mid = Decimal(str(candles_df[ema_column_name].iloc[-1]))
            return self._ema_mid
        else:
            self.logger().warning(f"EMA calculation failed. Column {ema_column_name} not found.")
            return self._ema_mid if self._ema_mid > 0 else Decimal("0")

    async def _cancel_and_place_orders(self, proposal: List[PerpetualOrderCandidate]) -> None:
        """
        Cancel all active orders and then place new orders sequentially.
        This ensures old orders are cancelled before new ones are placed.
        """
        connector = self.connectors[self.config.exchange]

        cancel_success = await connector.cancel_all_symbol(self.config.trading_pair)
        if not cancel_success:
            self.logger().warning(f"Cancel all failed: Skipping new order placement this cycle.")
            return

        # Then place new orders
        await self._async_place_orders(proposal)

    async def _async_place_orders(self, proposal: List[PerpetualOrderCandidate]) -> None:
        """Place multiple orders using batch API and wait for completion"""
        if not proposal or len(proposal) == 0:
            return

        connector = self.connectors[self.config.exchange]

        # Convert PerpetualOrderCandidate objects to order dictionaries for batch_order_create
        orders_to_create = []
        for order in proposal:
            order_dict = {
                "trading_pair": order.trading_pair,
                "amount": order.amount,
                "trade_type": order.order_side,
                "order_type": order.order_type,
                "price": order.price,
                "position_action": PositionAction.OPEN
            }
            orders_to_create.append(order_dict)

        # Call batch_order_create and wait for completion
        await connector.batch_order_create(orders_to_create)

    def _get_current_inventory(self) -> Decimal:
        """
        Get current inventory position from the connector's actual position.
        Returns signed position amount: positive for long, negative for short.

        For perpetual futures in ONEWAY mode:
        - Gets the actual position from the exchange via connector
        - Converts to signed value: positive for long, negative for short
        """
        connector = self.connectors[self.config.exchange]

        # For ONEWAY mode, get position by trading pair (no side needed)
        position = connector._perpetual_trading.get_position(self.config.trading_pair)

        if position is None:
            return Decimal("0")

        # Convert to signed inventory: positive for long, negative for short
        if position.position_side == PositionSide.LONG:
            return position.amount
        elif position.position_side == PositionSide.SHORT:
            return -position.amount
        else:
            return Decimal("0")

    def _inventory_ratio(self, inventory: Decimal) -> Decimal:
        """
        Returns a factor in [0, 1] * sign(inventory).
        """
        if self.config.max_inventory == 0:
            return Decimal("0")

        inventory_ratio = abs(inventory) / self.config.max_inventory
        inventory_ratio = min(inventory_ratio, Decimal("1"))

        if inventory_ratio <= self.config.min_inventory_pct_for_adjustment:
            return Decimal("0")

        # factor is simply r (not rescaled)
        return inventory_ratio * sign(inventory)

    def _random_factor(self) -> Decimal:
        """
        Returns a multiplier in [1 - randomization, 1 + randomization].
        E.g. randomization = 0.25 → [0.75, 1.25]
        """
        max_var = float(self.config.randomization)  # e.g. 0.25
        variation = random.uniform(-max_var, max_var)  # float in [-0.25, 0.25]
        return Decimal("1") + Decimal(str(variation))

    def _get_filtered_position_history(self) -> Optional[pd.DataFrame]:
        """
        Get position history filtered by current trading pair(s) and bot start time.
        
        Returns:
            DataFrame with filtered position history, or None if no data available
        """
        connector = self.connectors[self.config.exchange]
        
        # Check if connector has position_history attribute
        if not hasattr(connector, 'position_history'):
            return None
            
        history = connector.position_history
        
        if history is None or history.empty:
            return None
        
        # Convert trading pair format (e.g., BTC-USDC -> PERP_BTC_USDC for Orderly)
        # Note: config.trading_pair is a string for single pair strategies
        symbol = f"PERP_{self.config.trading_pair.replace('-', '_')}"
        
        # Filter by symbol and timestamp
        filtered = history[
            (history['symbol'] == symbol) & 
            (history['close_timestamp'] >= self._bot_start_timestamp)
        ].copy()
        
        return filtered if not filtered.empty else None

    def _calculate_position_metrics(self) -> Dict[str, any]:
        """
        Calculate aggregated metrics from position history.
        Includes both overall metrics and per-symbol breakdown.
        
        Returns:
            Dict with:
                - total_pnl: Overall realized PnL
                - total_fees: Overall fees
                - total_volume: Overall volume
                - by_symbol: Dict mapping symbol -> metrics dict
                - num_positions: Total number of closed positions
        """
        filtered_history = self._get_filtered_position_history()
        
        if filtered_history is None:
            return {
                "total_pnl": Decimal("0"),
                "total_fees": Decimal("0"),
                "total_volume": Decimal("0"),
                "by_symbol": {},
                "num_positions": 0,
            }
        
        # Calculate overall totals
        total_pnl = Decimal(str(filtered_history['realized_pnl'].sum()))
        total_fees = Decimal(str(filtered_history['trading_fee'].sum()))
        
        # Calculate total volume (both opening and closing trades)
        # For LONG positions: 
        #   - Open (BUY): avg_open_price * |qty|
        #   - Close (SELL): avg_close_price * |qty|
        # For SHORT positions:
        #   - Open (SELL): avg_open_price * |qty|
        #   - Close (BUY): avg_close_price * |qty|
        # Total volume = sum of all opening and closing volumes
        total_volume = Decimal("0")
        
        for _, row in filtered_history.iterrows():
            qty = abs(Decimal(str(row['closed_position_qty'])))
            open_volume = Decimal(str(row['avg_open_price'])) * qty
            close_volume = Decimal(str(row['avg_close_price'])) * qty
            total_volume += (open_volume + close_volume)
        
        # Calculate per-symbol metrics
        by_symbol = {}
        for symbol in filtered_history['symbol'].unique():
            symbol_data = filtered_history[filtered_history['symbol'] == symbol]
            
            symbol_pnl = Decimal(str(symbol_data['realized_pnl'].sum()))
            symbol_fees = Decimal(str(symbol_data['trading_fee'].sum()))
            symbol_volume = Decimal("0")
            
            # Calculate volume for both opening and closing trades (use abs for quantity)
            for _, row in symbol_data.iterrows():
                qty = abs(Decimal(str(row['closed_position_qty'])))
                open_volume = Decimal(str(row['avg_open_price'])) * qty
                close_volume = Decimal(str(row['avg_close_price'])) * qty
                symbol_volume += (open_volume + close_volume)
            
            # Convert symbol back to trading pair format (PERP_BTC_USDC -> BTC-USDC)
            trading_pair = symbol.replace('PERP_', '').replace('_', '-')
            
            by_symbol[trading_pair] = {
                "pnl": symbol_pnl,
                "fees": symbol_fees,
                "volume": symbol_volume,
                "num_positions": len(symbol_data),
                "net_pnl": symbol_pnl - symbol_fees,
            }
        
        return {
            "total_pnl": total_pnl,
            "total_fees": total_fees,
            "total_volume": total_volume,
            "by_symbol": by_symbol,
            "num_positions": len(filtered_history),
        }

    def _export_position_history(self):
        """
        Export position history to CSV files on bot shutdown.
        Creates two files:
        1. Raw data: performance/{script_config_name}/{bot_start}-{bot_end}_raw.csv
        2. Summary by symbol: performance/{script_config_name}/{bot_start}-{bot_end}_summary.csv
        """
        try:
            filtered_history = self._get_filtered_position_history()
            
            if filtered_history is None or filtered_history.empty:
                self.logger().info("No position history to export")
                return
            
            # Get config name from the script file name (remove .py extension)
            config_name = self.config.script_file_name.replace('.py', '')
            
            # Create performance directory structure
            perf_dir = os.path.join('performance', config_name)
            os.makedirs(perf_dir, exist_ok=True)
            
            # Format timestamps for filename
            start_time = datetime.fromtimestamp(self._bot_start_timestamp / 1000).strftime("%Y%m%d_%H%M%S")
            end_time = datetime.fromtimestamp(self.current_timestamp).strftime("%Y%m%d_%H%M%S")
            
            # Export 1: Raw position history data
            raw_filename = f"{start_time}-{end_time}_raw.csv"
            raw_filepath = os.path.join(perf_dir, raw_filename)
            filtered_history.to_csv(raw_filepath, index=False)
            
            self.logger().info(f"Raw position history exported to: {raw_filepath}")
            self.logger().info(f"Total positions exported: {len(filtered_history)}")
            
            # # Export 2: Summary aggregated by symbol
            # summary_data = []
            # for symbol in filtered_history['symbol'].unique():
            #     symbol_data = filtered_history[filtered_history['symbol'] == symbol]
                
            #     # Convert symbol to trading pair format
            #     trading_pair = symbol.replace('PERP_', '').replace('_', '-')
                
            #     # Aggregate metrics
            #     total_pnl = symbol_data['realized_pnl'].sum()
            #     total_fees = symbol_data['trading_fee'].sum()
            #     total_funding_fees = symbol_data['funding_fee'].sum()
            #     num_positions = len(symbol_data)
            #     num_long = len(symbol_data[symbol_data['side'] == 'LONG'])
            #     num_short = len(symbol_data[symbol_data['side'] == 'SHORT'])
                
            #     # Calculate volume (both opening and closing trades, use abs for quantity)
            #     total_volume = sum(
            #         (row['avg_open_price'] * abs(row['closed_position_qty'])) + 
            #         (row['avg_close_price'] * abs(row['closed_position_qty']))
            #         for _, row in symbol_data.iterrows()
            #     )
                
            #     # Win rate
            #     winning_positions = len(symbol_data[symbol_data['realized_pnl'] > 0])
            #     win_rate = (winning_positions / num_positions * 100) if num_positions > 0 else 0
                
            #     # Average position metrics
            #     avg_pnl = total_pnl / num_positions if num_positions > 0 else 0
            #     avg_position_size = symbol_data['closed_position_qty'].mean()
                
            #     summary_data.append({
            #         'trading_pair': trading_pair,
            #         'symbol': symbol,
            #         'num_positions': num_positions,
            #         'num_long': num_long,
            #         'num_short': num_short,
            #         'total_pnl': round(total_pnl, 4),
            #         'total_fees': round(total_fees, 4),
            #         'total_funding_fees': round(total_funding_fees, 4),
            #         'net_pnl': round(total_pnl - total_fees, 4),
            #         'total_volume': round(total_volume, 2),
            #         'win_rate_pct': round(win_rate, 2),
            #         'avg_pnl_per_position': round(avg_pnl, 4),
            #         'avg_position_size': round(avg_position_size, 6),
            #     })
            
            # # Create summary DataFrame and export
            # summary_df = pd.DataFrame(summary_data)
            # summary_filename = f"{start_time}-{end_time}_summary.csv"
            # summary_filepath = os.path.join(perf_dir, summary_filename)
            # summary_df.to_csv(summary_filepath, index=False)
            
            # self.logger().info(f"Summary by symbol exported to: {summary_filepath}")
            # self.logger().info(f"Symbols in summary: {len(summary_data)}")
            
        except Exception as e:
            self.logger().error(f"Error exporting position history: {e}", exc_info=True)

    def format_status(self) -> str:
        if not self.ready_to_trade:
            return "Market connectors are not ready."

        mid = self._cached_mid_price
        ema = self._ema_mid
        mark = self._cached_mark_price

        skew_bps = (self._cached_skew_mult - Decimal("1")) * Decimal("10000")
        spread_mult = self._cached_spread_mult
        inv_ratio_pct = self._cached_inventory_ratio * Decimal("100")

        # Get position metrics
        metrics = self._calculate_position_metrics()
        total_pnl = metrics["total_pnl"]
        total_fees = metrics["total_fees"]
        total_volume = metrics["total_volume"]
        net_pnl = total_pnl - total_fees
        num_positions = metrics["num_positions"]
        by_symbol = metrics["by_symbol"]

        lines = []
        lines.append("")
        lines.append("  Strategy Status")
        lines.append("  ----------------------------")
        lines.append(f"    Exchange:            {self.config.exchange}")
        lines.append(f"    Trading Pair:        {self.config.trading_pair}")
        lines.append("")
        lines.append(f"    Mark Price:          {mark:.4f}")
        lines.append(f"    Mid Price:           {mid:.4f}")
        lines.append(f"    EMA ({self.config.ema_window}):            {ema:.4f}")
        lines.append("")
        lines.append(f"    Inventory:           {self._cached_inventory:.4f}")
        lines.append(f"    Max Inventory:       {self.config.max_inventory:.4f}")
        lines.append(f"    Inventory Ratio:     {inv_ratio_pct:.2f}%")
        lines.append("")
        lines.append(f"    Price Skew:          {skew_bps:+.2f} bps")
        lines.append(f"    Spread Multiplier:   {spread_mult:.3f}x")
        lines.append(f"    Random Factor:       {self._cached_random_factor:.4f}x")
        lines.append("")
        lines.append(f"    Current Time:        {_fmt(self.current_timestamp)}")
        lines.append(f"    Cooldown Ends At:    {_fmt(self._cooldown_until_timestamp)}")
        lines.append(f"    Next Refresh:        {_fmt(self.create_timestamp)}")
        lines.append("")
        lines.append(f"    Last Trade Size:     {self._last_trade:.6f}")
        lines.append("")
        lines.append("  Performance (Since Bot Start)")
        lines.append("  ----------------------------")
        lines.append(f"    Total Positions:     {num_positions}")
        lines.append(f"    Total PnL:           {total_pnl:+.4f} USDC")
        lines.append(f"    Total Fees:          {total_fees:.4f} USDC")
        lines.append(f"    Net PnL:             {net_pnl:+.4f} USDC")
        lines.append(f"    Total Volume:        {total_volume:.2f} USDC")
        
        # Show per-symbol breakdown if multiple symbols
        if by_symbol and len(by_symbol) > 0:
            lines.append("")
            lines.append("  Performance by Symbol")
            lines.append("  ----------------------------")
            for symbol, symbol_metrics in sorted(by_symbol.items()):
                lines.append(f"    {symbol}:")
                lines.append(f"      Positions:     {symbol_metrics['num_positions']}")
                lines.append(f"      PnL:           {symbol_metrics['pnl']:+.4f} USDC")
                lines.append(f"      Fees:          {symbol_metrics['fees']:.4f} USDC")
                lines.append(f"      Net PnL:       {symbol_metrics['net_pnl']:+.4f} USDC")
                lines.append(f"      Volume:        {symbol_metrics['volume']:.2f} USDC")
        
        lines.append("")

        # Order proposals
        proposals = self._cached_proposals
        if proposals:
            lines.append("  Current Proposals (Orderbook-style Sort)")
            lines.append("        PRICE        SIDE      AMOUNT     ΔMID (bps)")
            lines.append("    --------------------------------------------------")

            rows = []

            # append proposals
            for p in proposals:
                rows.append({
                    "price": p.price,
                    "side": p.order_side.name,
                    "amount": p.amount,
                    "marker": False
                })

            # markers: MID + EMA (makes visual alignment)
            rows.append({"price": mid, "side": "MID", "amount": None, "marker": True})
            rows.append({"price": ema, "side": "EMA", "amount": None, "marker": True})

            rows.sort(key=lambda r: r["price"], reverse=True)

            def spread_bps(price):
                if mid == 0:
                    return "   n/a"
                return f"{((float(price / mid) - 1) * 10000):>10.2f}"

            for r in rows:
                amount = "-" if r["marker"] else f"{r['amount']:.6f}"
                lines.append(
                    f"    {r['price']:>12.4f}   {r['side']:<6}   {amount:>10}   {spread_bps(r['price'])}"
                )

        return "\n".join(lines)
