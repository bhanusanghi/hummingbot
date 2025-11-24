import random
import os
from decimal import Decimal
from typing import Dict, List, Optional

import pandas as pd
from pydantic import Field

from hummingbot.client.config.config_data_types import BaseClientModel
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PriceType, PositionAction, PositionSide, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.data_type.order_candidate import PerpetualOrderCandidate
from hummingbot.core.utils.async_utils import safe_ensure_future
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
    ema_window: int = Field(10)  # multiple of refresh rate
    target_inventory: Decimal = Field(default=Decimal("0.0"))

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
        self._mid_history: List[Decimal] = []
        self._ema_mid: Decimal = Decimal("0")

    # Built-in event handler methods (called automatically by ScriptStrategyBase)

    def start(self, clock: Clock, timestamp: float):
        self.apply_initial_setting()
        super().start(clock, timestamp)

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
        proposals: List[PerpetualOrderCandidate] = self.create_proposal()
        if len(proposals) > 0:
            self._cached_proposals = proposals
            safe_ensure_future(self._cancel_and_place_orders(proposals))  # Execute cancel then place sequentially to avoid order accumulation
            self.create_timestamp = self.current_timestamp + self.config.order_refresh_time

    def create_proposal(self) -> List[PerpetualOrderCandidate]:
        connector = self.connectors[self.config.exchange]

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
        mark_price = connector.get_price_by_type(self.config.trading_pair, PriceType.MarkPrice)

        if best_bid_size + best_ask_size > 0:
            mid_price = ((best_bid_price * best_ask_size) + (best_bid_size * best_ask_price)) / (
                        best_bid_size + best_ask_size)

        ema = self._update_ema_mid(mid_price)
        inventory = self._detect_trade() # this updates the cooldown timestamp

        # Cache price info for status reporting (updated every tick)
        self._cached_mark_price = mark_price
        self._cached_mid_price = mid_price

        if self.current_timestamp < self.create_timestamp:
            return []

        if self.current_timestamp < self._cooldown_until_timestamp:
            return []

        # Order specific updates (all calculations needed for when we do orders)
        inventory_ratio = self._inventory_ratio(inventory)
        bid_anchor = min(ema, mid_price, mark_price)
        ask_anchor = max(ema, mid_price, mark_price)
        skew_factor = Decimal("1") - inventory_ratio * self.config.max_price_adjustment
        spread_mult = Decimal("1") + abs(inventory_ratio) * (self.config.max_spread_mult - Decimal("1"))
        random_factor = self._random_factor()

        self._cached_bid_anchor = bid_anchor
        self._cached_ask_anchor = ask_anchor
        self._cached_inventory_ratio = inventory_ratio
        self._cached_skew_mult = skew_factor
        self._cached_spread_mult = spread_mult
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

    def _update_ema_mid(self, mid: Decimal) -> Decimal:
        """
        Update EMA of mid price using pandas.
        Keeps at most ema_window mids in history.
        """
        self._mid_history.append(mid)

        # Keep only the last ema_window
        max_len = self.config.ema_window
        if len(self._mid_history) > max_len:
            self._mid_history = self._mid_history[-max_len:]

        # Use pandas to compute EMA
        # Convert Decimals to float for pandas, then back to Decimal
        s = pd.Series([float(m) for m in self._mid_history])

        # span = ema_window is the standard EMA parameter
        ema_val = s.ewm(span=self.config.ema_window, adjust=False).mean().iloc[-1]

        self._ema_mid = Decimal(str(ema_val))
        return self._ema_mid

    async def _cancel_and_place_orders(self, proposal: List[PerpetualOrderCandidate]) -> None:
        """
        Cancel all active orders and then place new orders sequentially.
        This ensures old orders are cancelled before new ones are placed.
        """
        connector = self.connectors[self.config.exchange]
        orders_to_cancel = self._get_active_orders_from_connector()
        
        if orders_to_cancel:
            await connector.batch_order_cancel(orders_to_cancel)
        
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

        # Call bulk_batch_order_create and wait for completion
        await connector.bulk_batch_order_create(orders_to_create)

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
        Returns a factor in [-1, 1] based on deviation from target inventory.

        Positive ratio → too long relative to target → reduce bids, keep asks (favor selling)
        Negative ratio → too short relative to target → keep bids, reduce asks (favor buying)

        Examples:
        - inventory=-0.3, target=-0.5: deviation=+0.2 → too long, need to sell more
        - inventory=-0.7, target=-0.5: deviation=-0.2 → too short, need to buy back
        - inventory=0.3, target=0.5: deviation=-0.2 → too short, need to buy more
        - inventory=0.7, target=0.5: deviation=+0.2 → too long, need to sell more
        """
        if self.config.max_inventory == 0:
            return Decimal("0")

        # Calculate deviation from target (not from zero!)
        deviation = inventory - self.config.target_inventory

        inventory_ratio = abs(deviation) / self.config.max_inventory
        inventory_ratio = min(inventory_ratio, Decimal("1"))

        if inventory_ratio <= self.config.min_inventory_pct_for_adjustment:
            return Decimal("0")

        # Apply sign based on deviation direction
        return inventory_ratio * sign(deviation)

    def _random_factor(self) -> Decimal:
        """
        Returns a multiplier in [1 - randomization, 1 + randomization].
        E.g. randomization = 0.25 → [0.75, 1.25]
        """
        max_var = float(self.config.randomization)  # e.g. 0.25
        variation = random.uniform(-max_var, max_var)  # float in [-0.25, 0.25]
        return Decimal("1") + Decimal(str(variation))

    def _get_active_orders_from_connector(self) -> List[InFlightOrder]:
        """
        Get active orders directly from connector's order tracker.
        This ensures we see the actual state on the exchange.
        """
        connector = self.connectors[self.config.exchange]
        all_in_flight_orders = connector._order_tracker.active_orders
        
        # Filter: only non-done orders for the current trading pair
        active_orders = [
            in_flight_order
            for in_flight_order in all_in_flight_orders.values()
            if (in_flight_order.trading_pair == self.config.trading_pair 
                and not in_flight_order.is_done)
        ]
        
        return active_orders

    def format_status(self) -> str:
        if not self.ready_to_trade:
            return "Market connectors are not ready."

        mid = self._cached_mid_price
        ema = self._ema_mid
        mark = self._cached_mark_price

        skew_bps = (self._cached_skew_mult - Decimal("1")) * Decimal("10000")
        spread_mult = self._cached_spread_mult
        inv_ratio_pct = self._cached_inventory_ratio * Decimal("100")

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
        lines.append(f"    Target Inventory:    {self.config.target_inventory:.4f}")
        lines.append(f"    Max Inventory:       {self.config.max_inventory:.4f}")
        lines.append(f"    Deviation:           {(self._cached_inventory - self.config.target_inventory):.4f}")
        lines.append("")
        lines.append(f"    Inventory Ratio:     {inv_ratio_pct:.2f}%")
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

        # Get active orders from connector's order tracker
        active_orders = self._get_active_orders_from_connector()
        
        if active_orders:
            # Create DataFrame with order information
            columns = ["Order ID", "Side", "Price", "Amount", "Filled", "Status", "Age"]
            data = []
            for order in active_orders:
                # Calculate age
                age_seconds = self.current_timestamp - order.creation_timestamp
                if age_seconds <= 0:
                    age_txt = "n/a"
                else:
                    age_txt = pd.Timestamp(age_seconds, unit='s').strftime('%H:%M:%S')
                
                # Get filled amount
                filled = float(order.executed_amount_base) if order.executed_amount_base else 0.0
                
                # Get status
                status = order.current_state.name if hasattr(order.current_state, 'name') else str(order.current_state)
                
                data.append([
                    order.client_order_id[:16] + "..." if len(order.client_order_id) > 16 else order.client_order_id,
                    "buy" if order.trade_type == TradeType.BUY else "sell",
                    float(order.price) if order.price else "N/A",
                    float(order.amount),
                    f"{filled:.6f}",
                    status,
                    age_txt
                ])
            
            df = pd.DataFrame(data=data, columns=columns)
            # Sort: buy orders first, then sell orders; within each group, sort by price descending
            df['Side_sort'] = df['Side'].map({'buy': 0, 'sell': 1})
            df['Price_num'] = pd.to_numeric(df['Price'], errors='coerce')
            df.sort_values(by=['Side_sort', 'Price_num'], ascending=[True, False], inplace=True)
            df.drop(['Side_sort', 'Price_num'], axis=1, inplace=True)
            
            lines.append("")
            lines.append("  Open Orders:")
            lines.extend(["    " + line for line in df.to_string(index=False).split("\n")])
        else:
            lines.append("")
            lines.append("  No open orders.")

        return "\n".join(lines)
