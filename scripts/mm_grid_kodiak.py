import random
import os
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

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
    order_tag: Optional[str] = Field(default=None)
    ema_window: int = Field(100) # number of ticks (i.e. seconds)
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
        self._cooldown_until_timestamp: int = 0
        self._last_trade = Decimal("0")
        self._cached_inventory: Decimal = Decimal("0")
        self._cached_entry_price: Decimal = Decimal("0")
        self._cached_inventory_ratio: Decimal = Decimal("0")
        self._mid_history: List[Decimal] = []
        self._cached_ema_mid: Decimal = Decimal("0")
        self._ready_to_place_orders: bool = True

    # Built-in event handler methods (called automatically by ScriptStrategyBase)

    def start(self, clock: Clock, timestamp: float):
        self.apply_initial_setting()
        self.create_timestamp = timestamp + self.config.order_refresh_time
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
        if len(proposals) > 0 and self._ready_to_place_orders:
            safe_ensure_future(self._cancel_and_place_orders(proposals))  # Execute cancel then place sequentially to avoid order accumulation
        if self.current_timestamp >= self.create_timestamp:
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
            mid_price = ((best_bid_price * best_ask_size) + (best_bid_size * best_ask_price)) / (best_bid_size + best_ask_size)

        self._mid_history, ema = compute_ema_mid(self._mid_history, mid_price, self.config.ema_window)

        position = connector._perpetual_trading.get_position(self.config.trading_pair)
        inventory, entry_price = inventory_from_position(position)

        # Calculate values
        inventory_ratio = compute_inventory_ratio(inventory, self.config.target_inventory, self.config.min_inventory_pct_for_adjustment, self.config.max_inventory)
        bid_anchor = min(ema, mid_price, mark_price)
        ask_anchor = max(ema, mid_price, mark_price)
        skew_factor = Decimal("1") - inventory_ratio * self.config.max_price_adjustment
        spread_mult = Decimal("1") + abs(inventory_ratio) * (self.config.max_spread_mult - Decimal("1"))
        random_factor = compute_random_factor(self.config.randomization)


        # Entry_price bias
        if self.config.target_inventory == 0 and inventory != 0 and entry_price > 0 and ema > 0:
            if abs(entry_price/ema-1) <= Decimal("0.1"): #Magic number, if price is off by 10%, assume it's wrong and do nothing
                avg = (entry_price + ema) / 2

                if inventory > 0 and ema < entry_price:
                    # Long and underwater → push asks toward average
                    ask_anchor = max(ask_anchor, avg)
                elif inventory < 0 and ema > entry_price:
                    # Short and underwater → push bids toward average
                    bid_anchor = min(bid_anchor, avg)

        # Detect trade: update cooldown_until_timestamp and last_trade
        # Note that cooldown timestamp is based on current timestamp and isn't affected by refresh rate
        self._detect_trade(self._cached_inventory, inventory)

        # Update all caches (for status reporting)
        self._cached_mark_price = mark_price
        self._cached_mid_price = mid_price
        self._cached_bid_anchor = bid_anchor
        self._cached_ask_anchor = ask_anchor
        self._cached_inventory = inventory
        self._cached_entry_price = entry_price
        self._cached_inventory_ratio = inventory_ratio
        self._cached_skew_mult = skew_factor
        self._cached_spread_mult = spread_mult
        self._cached_random_factor = random_factor
        self._cached_ema_mid = ema

        if self.current_timestamp < self.create_timestamp:
            return []

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

    # Detect trades, update order cooldown
    def _detect_trade(self, last_inventory, inventory) -> None:

        last_trade = self._last_trade

        # Initialization
        if last_trade == 0 and last_inventory == 0:
            self._cooldown_until_timestamp = self.current_timestamp
            return

        # change in position = trade
        trade = inventory - last_inventory

        if trade == 0:
            return

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
        return

    async def _cancel_and_place_orders(self, proposal: List[PerpetualOrderCandidate]) -> None:
        """
        Cancel all active orders and then place new orders sequentially.
        This ensures old orders are cancelled before new ones are placed.
        """
        self._ready_to_place_orders = False
        try:
            connector = self.connectors[self.config.exchange]
            # orders_to_cancel = self._get_active_orders_from_connector()

            cancel_success = await connector.cancel_all_symbol(self.config.trading_pair)
            if not cancel_success:
                self.logger().warning(f"Cancel all failed: Skipping new order placement this cycle.")
                return

            # Then place new orders
            await self._async_place_orders(proposal)

        finally:
            self._ready_to_place_orders = True

    async def _async_place_orders(self, proposals: List[PerpetualOrderCandidate]) -> None:
        """Place multiple orders using batch API and wait for completion"""
        if not proposals or len(proposals) == 0:
            return

        connector = self.connectors[self.config.exchange]

        # Convert PerpetualOrderCandidate objects to order dictionaries for batch_order_create
        orders_to_create = []
        for order in proposals:
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
        try:
            await connector.batch_order_create(orders_to_create)
        except Exception as e:
            self.logger().error(f"Order placement failed: {e}")


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
        ema = self._cached_ema_mid
        mark = self._cached_mark_price
        entry = self._cached_entry_price

        skew_bps = (self._cached_skew_mult - Decimal("1")) * Decimal("10000")
        spread_mult = self._cached_spread_mult
        inv_ratio_pct = self._cached_inventory_ratio * Decimal("100")

        lines: List[str] = []
        lines.append("")
        lines.append("  Strategy Status")
        lines.append("  ----------------------------")
        lines.append(f"    Exchange:            {self.config.exchange}")
        lines.append(f"    Trading Pair:        {self.config.trading_pair}")
        lines.append("")
        lines.append(f"    Mark Price:          {mark:.5f}")
        lines.append(f"    Mid Price:           {mid:.5f}")
        lines.append(f"    EMA:                 {ema:.5f} ({self.config.ema_window})")
        lines.append(f"    Entry Price:         {entry:.5f}")
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
        lines.append(f"    Current Time:        {fmt(self.current_timestamp)}")
        lines.append(f"    Cooldown Ends At:    {fmt(self._cooldown_until_timestamp)}")
        lines.append(f"    Next Refresh:        {fmt(self.create_timestamp)}")
        lines.append("")
        lines.append(f"    Last Trade Size:     {self._last_trade:.6f}")
        lines.append("")

        # Open orders, formatted like proposals with MID/EMA markers and AGE at far right
        active_orders = self._get_active_orders_from_connector()

        if active_orders:
            indent = "    "

            lines.append("")
            lines.append("  Open Orders (Orderbook-style Sort)")

            # Header built with same widths as row formatting
            header = (
                f"{indent}{'PRICE':>12}   "
                f"{'SIDE':<6}   "
                f"{'AMOUNT':>10}   "
                f"{'ΔMID (bps)':>10}   "
                f"{'AGE':>10}"
            )
            lines.append(header)
            lines.append(indent + "-" * (len(header) - len(indent)))

            rows = []

            # Append active orders
            for order in active_orders:
                price = float(order.price) if order.price is not None else 0.0
                amount = float(order.amount)
                side = "BUY" if order.trade_type == TradeType.BUY else "SELL"

                # Compute age
                age_seconds = self.current_timestamp - order.creation_timestamp
                if age_seconds <= 0:
                    age_txt = "n/a"
                else:
                    age_txt = pd.Timestamp(age_seconds, unit="s").strftime("%H:%M:%S")

                rows.append({
                    "price": price,
                    "side": side,
                    "amount": amount,
                    "age": age_txt,
                    "marker": False,
                })

            # MID / EMA markers
            if mid and mid > 0:
                rows.append({
                    "price": float(mid),
                    "side": "MID",
                    "amount": None,
                    "age": "-",
                    "marker": True,
                })
            if ema and ema > 0:
                rows.append({
                    "price": float(ema),
                    "side": "EMA",
                    "amount": None,
                    "age": "-",
                    "marker": True,
                })
            if entry and entry > 0:
                rows.append({
                    "price": float(entry),
                    "side": "ENTRY",
                    "amount": None,
                    "age": "-",
                    "marker": True,
                })
            # Orderbook sort: descending by price
            rows.sort(key=lambda r: r["price"], reverse=True)

            def spread_bps(price: float) -> str:
                if mid is None or mid == 0:
                    return f"{'n/a':>10}"
                value = (float(price) / float(mid) - 1) * 10000
                return f"{value:>10.2f}"

            # Render rows
            for r in rows:
                amount_str = "-" if r["marker"] else f"{r['amount']:.3f}"
                delta_str = spread_bps(r["price"])
                age_str = r["age"]

                lines.append(
                    f"{indent}{r['price']:>12.5f}   "
                    f"{r['side']:<6}   "
                    f"{amount_str:>10}   "
                    f"{delta_str}   "
                    f"{age_str:>10}"
                )
        else:
            lines.append("")
            lines.append("  No open orders.")

        return "\n".join(lines)

def fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")

def sign(x):
    return (x > 0) - (x < 0)

def compute_random_factor(randomization: Decimal) -> Decimal:
    """
    Returns a multiplier in [1 - randomization, 1 + randomization].
    E.g. randomization = 0.25 → [0.75, 1.25]
    """
    max_var = float(randomization)  # e.g. 0.25
    variation = random.uniform(-max_var, max_var)  # float in [-0.25, 0.25]
    return Decimal("1") + Decimal(str(variation))

def inventory_from_position(position) -> Tuple[Decimal, Decimal]:
    """
    Pure function:
    Takes a raw position object (or None) and returns signed inventory.
    """
    if position is None:
        return Decimal("0"), Decimal("0")

    if position.position_side == PositionSide.LONG:
        inventory = position.amount
    elif position.position_side == PositionSide.SHORT:
        inventory = -position.amount
    else:
        inventory = Decimal("0")

    if hasattr(position, "entry_price") and position.entry_price is not None:
        entry_price = Decimal(str(position.entry_price))
    else:
        entry_price = Decimal("0")

    return inventory, entry_price


def compute_inventory_ratio(inventory: Decimal, target_inventory: Decimal, min_inventory_pct_for_adjustment: Decimal, max_inventory: Decimal) -> Decimal:
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
    if max_inventory == 0:
        return Decimal("0")

    # Calculate deviation from target (not from zero!)
    deviation = inventory - target_inventory

    ratio = abs(deviation) / max_inventory
    ratio = min(ratio, Decimal("1"))

    if ratio <= min_inventory_pct_for_adjustment:
        return Decimal("0")

    # Apply sign based on deviation direction
    return ratio * sign(deviation)

def compute_ema_mid(
    mid_history: List[Decimal],
    new_mid: Decimal,
    window: int
) -> Tuple[List[Decimal], Decimal]:
    """
    Pure function:
    - Accepts old history
    - Returns (new_history, new_ema)
    - Does NOT mutate anything
    """
    # Build new history
    new_history = mid_history + [new_mid]
    if len(new_history) > window:
        new_history = new_history[-window:]

    s = pd.Series([float(m) for m in new_history])
    ema_val = s.ewm(span=window, adjust=False).mean().iloc[-1]
    ema = Decimal(str(ema_val))

    return new_history, ema