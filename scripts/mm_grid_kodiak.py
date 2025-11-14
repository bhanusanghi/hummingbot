import random
import logging
import os
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pandas as pd
from numpy.ma.mrecords import reserved_fields
from pydantic import Field

from hummingbot.client.config.config_data_types import BaseClientModel
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PriceType, PositionAction, PositionSide, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.data_type.order_candidate import PerpetualOrderCandidate
from hummingbot.core.event.events import (
    OrderFilledEvent,
)
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.core.clock import Clock


class MMGridConfig(BaseClientModel):
    script_file_name: str = os.path.basename(__file__)
    exchange: str = Field("orderly_perpetual")
    trading_pair: str = Field("BTC-USDC")
    order_amount_quote: List[Decimal] = Field(default=[Decimal("20")])
    bid_spread_levels: List[Decimal] = Field(default=[Decimal("0.001")])
    ask_spread_levels: List[Decimal] = Field(default=[Decimal("0.001")])
    order_refresh_time: int = Field(10)
    order_cooldown: int = Field(30)
    max_inventory: Decimal = Field(0.01)
    min_inventory_pct_for_adjustment: Decimal = Field(default=Decimal("0.25"))
    max_price_adjustment: Decimal = Field(default=Decimal("0.001"))
    max_spread_widening: Decimal = Field(default=Decimal("0.5"))
    randomization: Decimal = Field(default=Decimal("0.25"))
    leverage: int = Field(100)
    order_tag: Optional[str] = Field(default="None")
#    target_inventory: Decimal = Field(0.0)

class MMGrid(ScriptStrategyBase):
    """
    Market Making + Grid Strategy with Batch Order Operations

    Key features:
    - Reservation price calculation based on inventory
    - Multiple constant spread levels
    - Inventory management and tracking
    - Order lifecycle management via connector's order tracker
    - Batch order placement and cancellation for improved efficiency
    """

    create_timestamp = 0
    account_config_set = False

    @classmethod
    def init_markets(cls, config: MMGridConfig):
        cls.markets = {config.exchange: {config.trading_pair}}

    def __init__(self, connectors: Dict[str, ConnectorBase], config: MMGridConfig):
        super().__init__(connectors)
        self.config = config
        self._cached_mark_price: Decimal = Decimal("0")
        self._cached_mid_price: Decimal = Decimal("0")
        self._cached_reservation_price: Decimal = Decimal("0")
        self._cached_inventory_factor: Decimal = Decimal("0")
        self._cached_spread_widening_factor: Decimal = Decimal("0")
        self._cached_random_factor: Decimal = Decimal("0")
        self._cached_skew_factor: Decimal = Decimal("0")
        self._cached_proposals: List[PerpetualOrderCandidate] = []
        self._cooldown_until_timestamp: int = 0
        self._last_inventory: Optional[Decimal] = None

    # Built-in event handler methods (called automatically by ScriptStrategyBase)

    def start(self, clock: Clock, timestamp: float):
        self.apply_initial_setting()
        super().start(clock, timestamp)

    def on_tick(self):

        if self.current_timestamp > self.create_timestamp:

            self._detect_position_increase()

            if self.current_timestamp < self._cooldown_until_timestamp:
                self.create_timestamp = self.current_timestamp + self.config.order_refresh_time
                return

            proposals: List[PerpetualOrderCandidate] = self.create_proposal()
            self._cached_proposals = proposals
            safe_ensure_future(self._cancel_and_place_orders(proposals))  # Execute cancel then place sequentially to avoid order accumulation
            self.create_timestamp = self.current_timestamp + self.config.order_refresh_time

    def _detect_position_increase(self) -> None:
        inventory = self._get_current_inventory()

        if self._last_inventory is None:
            self._last_inventory = inventory
            return

        delta = abs(inventory) - abs(self._last_inventory)
        if delta > 0:
            self.logger().info(f"Detected position increase: {delta:.8f}. Entering cooldown")
            self._cooldown_until_timestamp = self.current_timestamp + self.config.order_cooldown

        self._last_inventory = inventory

    async def _cancel_and_place_orders(self, proposal: List[PerpetualOrderCandidate]) -> None:
        """
        Cancel all active orders and then place new orders sequentially.
        This ensures old orders are cancelled before new ones are placed.
        """
        # First, cancel all active orders and wait for completion
        cancel_results = await self._async_cancel_all_orders()

        # Verify cancellation succeeded before placing new orders
        if cancel_results is None:
            self.logger().warning("cancel_results is None. Skipping new order placement this cycle.")
            return

        if cancel_results is not None:
            if any(not result.get("success", False) for result in cancel_results):
                self.logger().warning("Some orders failed to cancel. Skipping new order placement this cycle.")
                return

        # Then place new orders
        await self._async_place_orders(proposal)

    def apply_initial_setting(self):
        if not self.account_config_set:
            connector = self.connectors[self.config.exchange]
            connector.set_leverage(self.config.trading_pair, self.config.leverage)
            # Set order tag if configured
            if self.config.exchange == "orderly_perpetual" and self.config.order_tag:
                self.logger().info(f"Setting order tag: {self.config.order_tag}")
                connector.set_order_tag(self.config.order_tag)
            self.account_config_set = True

    def create_proposal(self) -> List[PerpetualOrderCandidate]:
        connector = self.connectors[self.config.exchange]
        # Use mark price from exchange instead of mid price
        mark_price = connector.get_price_by_type(self.config.trading_pair, PriceType.MarkPrice)

        # Get order book
        order_book = connector.get_order_book(self.config.trading_pair)
        bids_df, asks_df = order_book.snapshot

        # Get best bid/ask prices and sizes
        best_bid_price = Decimal(str(bids_df.iloc[0].price))
        best_bid_size = Decimal(str(bids_df.iloc[0].amount))
        best_ask_price = Decimal(str(asks_df.iloc[0].price))
        best_ask_size = Decimal(str(asks_df.iloc[0].amount))

        mid_price = (best_bid_price + best_ask_price) / 2

        if best_bid_size + best_ask_size > 0:
            mid_price = ((best_bid_price * best_ask_size) + (best_bid_size * best_ask_price)) / (
                        best_bid_size + best_ask_size)

        inventory = self._get_current_inventory()
        skew_factor = self._get_price_skew(inventory)
        reservation_price = mid_price * skew_factor
        inventory_factor = self._inventory_ratio(inventory)

        # Cache values for status reporting
        self._cached_mark_price = mark_price
        self._cached_mid_price = mid_price
        self._cached_reservation_price = reservation_price
        self._cached_inventory_factor = inventory_factor
        self._cached_skew_factor = skew_factor

        spread_widening_factor = Decimal("1") + inventory_factor * self.config.max_spread_widening
        self._cached_spread_widening_factor = spread_widening_factor

        random_factor = self._random_factor()
        self._cached_random_factor = random_factor

        orders = []
        for idx, bid_spread in enumerate(self.config.bid_spread_levels):
            ask_spread = self.config.ask_spread_levels[idx]

            # Spreads relative to top of book
            bid_price = reservation_price * (Decimal("1") - bid_spread * spread_widening_factor * random_factor)
            ask_price = reservation_price * (Decimal("1") + ask_spread * spread_widening_factor * random_factor)

            # To make sure the limit maker orders are not immediately taken
            # Only the offending side is adjusted, and placed at top of book
            if bid_price >= mid_price:
                bid_price = Decimal(str(bids_df.iloc[idx * 2].price))

            if ask_price <= mid_price:
                ask_price = Decimal(str(asks_df.iloc[idx * 2].price))

            size = self.config.order_amount_quote[idx] / mid_price * random_factor

            if inventory > 0:
                bid_amount = size * (Decimal("1") - inventory_factor)
                ask_amount = size
            else:
                bid_amount = size
                ask_amount = size * (Decimal("1") - inventory_factor)

            bid_order = PerpetualOrderCandidate(
                trading_pair=self.config.trading_pair,
                is_maker=True,
                order_type=OrderType.LIMIT_MAKER,
                order_side=TradeType.BUY,
                amount=bid_amount,
                price=bid_price,
                leverage=Decimal(self.config.leverage)
            )

            ask_order = PerpetualOrderCandidate(
                trading_pair=self.config.trading_pair,
                is_maker=True,
                order_type=OrderType.LIMIT_MAKER,
                order_side=TradeType.SELL,
                amount=ask_amount,
                price=ask_price,
                leverage=Decimal(self.config.leverage)
            )

            # Inventory-based order placement logic:
            if inventory >= self.config.max_inventory and ask_amount > 0:
                # At max long position, only place ask orders to reduce position
                orders.extend([ask_order])
            elif inventory <= -self.config.max_inventory and bid_amount > 0:
                # At max short position, only place bid orders to reduce position
                orders.extend([bid_order])
            else:
                if bid_amount > 0:
                    orders.extend([bid_order])
                if ask_amount > 0:
                    orders.extend([ask_order])
        return orders

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

    async def _async_cancel_all_orders(self) -> Optional[List[Dict[str, Any]]]:
        """Cancel all active orders using batch API and wait for completion"""
        connector = self.connectors[self.config.exchange]

        # Get orders directly from connector's order tracker instead of strategy's order tracker
        # This ensures we get the most up-to-date list including orders just placed
        all_in_flight_orders = connector._order_tracker.active_orders

        # Collect InFlightOrder objects for orders to cancel
        orders_to_cancel = []
        orders_skipped = 0
        total_orders_checked = 0

        for in_flight_order in all_in_flight_orders.values():
            # Filter by current trading pair only
            if in_flight_order.trading_pair != self.config.trading_pair:
                continue

            total_orders_checked += 1

            # Check if order is actually still open
            if in_flight_order.is_open:
                orders_to_cancel.append(in_flight_order)
            else:
                # Order is already done (filled/cancelled/failed), skip it
                self.logger().debug(
                    f"Order {in_flight_order.client_order_id} is {in_flight_order.current_state.name}, "
                    f"skipping cancellation"
                )
                orders_skipped += 1

        # Log when no orders are found to cancel (important for debugging)
        if not orders_to_cancel:
            if total_orders_checked == 0:
                self.logger().debug(
                    f"No active orders found for {self.config.trading_pair} to cancel. "
                    f"This may be normal if all orders were already filled/cancelled."
                )
            else:
                self.logger().info(
                    f"Found {total_orders_checked} order(s) for {self.config.trading_pair}, "
                    f"but all are already {orders_skipped} filled/cancelled/failed. "
                    f"No cancellation needed."
                )
            return []

        # Use batch cancellation if we have orders to cancel and wait for completion
        try:
            self.logger().debug(f"Cancelling {len(orders_to_cancel)} active order(s) for {self.config.trading_pair}")

            results = await connector.batch_order_cancel(
                orders_to_cancel)  # Technically this should do cancel_all if > 10, right now default cancel_all

            return results

        except Exception as e:
            self.logger().warning(
                f"Error cancelling orders: {e}. "
            )
            return None

    def did_fill_order(self, event: OrderFilledEvent):
        """
        Called automatically by framework when order is filled.
        Logs the fill and updates the filled orders DataFrame.
        Note: Inventory is now retrieved from the connector's actual position,
        not tracked manually from fills.
        """
        return

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

    def _get_price_skew(self, inventory: Decimal) -> Decimal:
        """
        Calculate price skew factor based on inventory
        Apply adjustment with correct sign:
        - Long position (inventory > 0): lower price (subtract adjustment)
        - Short position (inventory < 0): raise price (add adjustment)
        """
        adjustment = self._inventory_ratio(inventory) * self.config.max_price_adjustment

        if inventory > 0:
            skew = Decimal("1") - adjustment
        else:
            skew = Decimal("1") + adjustment

        return skew

    def _inventory_ratio(self, inventory: Decimal) -> Decimal:
        """
        Returns a factor in [0, 1].
        """
        if self.config.max_inventory == 0:
            return Decimal("0")

        inventory_ratio = abs(inventory) / self.config.max_inventory
        inventory_ratio = min(inventory_ratio, Decimal("1"))

        if inventory_ratio <= self.config.min_inventory_pct_for_adjustment:
            return Decimal("0")

        # factor is simply r (not rescaled)
        return inventory_ratio

    def _random_factor(self) -> Decimal:
        """
        Returns a multiplier in [1 - randomization, 1 + randomization].
        E.g. randomization = 0.25 → [0.75, 1.25]
        """
        max_var = float(self.config.randomization)  # e.g. 0.25
        variation = random.uniform(-max_var, max_var)  # float in [-0.25, 0.25]
        return Decimal("1") + Decimal(str(variation))

    def format_status(self) -> str:
        """
        Return status string showing current strategy state.
        """
        if not self.ready_to_trade:
            return "Market connectors are not ready."

        mark_price = self._cached_mark_price
        mid_price = self._cached_mid_price
        reservation_price = self._cached_reservation_price
        random_factor = self._cached_random_factor
        spread_widening_factor = self._cached_spread_widening_factor
        inventory_factor = self._cached_inventory_factor
        skew_factor = self._cached_skew_factor



        lines = []
        lines.append("")
        lines.append("  Strategy Status:")
        lines.append(f"    Trading Pair: {self.config.trading_pair}")
        lines.append(f"    Exchange: {self.config.exchange}")
        lines.append(f"    Ask Spread Levels: {[f'{s * 100:.4f}%' for s in self.config.ask_spread_levels]}")
        lines.append(f"    Bid Spread Levels: {[f'{s * 100:.4f}%' for s in self.config.bid_spread_levels]}")
        lines.append(f"    Mark Price: {mark_price:.8f}")
        lines.append(f"    Mid Price: {mid_price:.8f}")
        lines.append(f"    Current Inventory: {self._last_inventory:.8f}")
        lines.append(f"    Max Inventory: {self.config.max_inventory:.8f}")
        lines.append(f"    Inventory Factor: {inventory_factor:.4f}")
        lines.append(f"    Reservation Price: {reservation_price:.8f}")
        lines.append(f"    Price Adjustment: {reservation_price - mid_price:.8f}")
        lines.append(f"    Skew Factor: {skew_factor:.4f}")
        lines.append(f"    Spread Widening Factor: {spread_widening_factor:.4f}")
        lines.append(f"    Random Factor: {random_factor:.4f}")
        lines.append(f"    Current timestamp: {self.current_timestamp}")
        lines.append(f"    Create timestamp: {self.create_timestamp}")
        lines.append(f"    Cooldown timestamp: {self._cooldown_until_timestamp}")

        # proposals = self._cached_proposals
        # if(len(proposals) > 0):
        #     lines.append("")
        #     lines.append("  Order Proposals:")
        #     lines.append(f" {proposal.order_side, proposal.price, proposal.amount}" for proposal in proposals)

        return "\n".join(lines)