import logging
import os
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd
from pydantic import Field, field_validator

from hummingbot.client.config.config_data_types import BaseClientModel
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PriceType, PositionAction, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.core.data_type.order_candidate import PerpetualOrderCandidate
from hummingbot.core.event.events import (
    OrderFilledEvent,
)
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
import asyncio


class PairConfig(BaseClientModel):
    """Configuration for a single trading pair"""
    exchange: str = Field("orderly_perpetual")
    trading_pair: str = Field("BTC-USDC")
    order_amount_quote: Decimal = Field(20)
    risk_aversion_gamma: Decimal = Field(6.0)
    volatility_sigma: Decimal = Field(0.50)
    risk_horizon_tau_hours: Decimal = Field(2.0)
    bid_spread_levels: List[Decimal] = Field(default=[Decimal("0.001")])  # 10 bps
    ask_spread_levels: List[Decimal] = Field(default=[Decimal("0.001")])  # 10 bps
    max_inventory: Decimal = Field(0.01)  # 1k usd
    leverage: int = Field(10)
    
    @field_validator("order_amount_quote", "risk_aversion_gamma", "volatility_sigma", 
                     "risk_horizon_tau_hours", "max_inventory", mode="before")
    @classmethod
    def convert_to_decimal(cls, v):
        """Convert float/int values to Decimal when loading from YAML"""
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        return v
    
    @field_validator("bid_spread_levels", "ask_spread_levels", mode="before")
    @classmethod
    def convert_list_to_decimal(cls, v):
        """Convert list of float/int values to Decimal"""
        if isinstance(v, list):
            return [Decimal(str(x)) if isinstance(x, (int, float)) else x for x in v]
        return v


class PMMAvellanedaMultiConfig(BaseClientModel):
    script_file_name: str = os.path.basename(__file__)
    pairs: List[PairConfig] = Field(
        default_factory=lambda: [PairConfig()],
        json_schema_extra={
            "prompt": "Enter pair configurations (exchange,trading_pair,order_amount_quote,risk_aversion_gamma,volatility_sigma,risk_horizon_tau_hours,max_inventory,leverage:same_for_other_pairs)",
            "prompt_on_new": True
        }
    )
    order_refresh_time: int = Field(10, ge=5)  # Minimum 5 seconds to avoid rate limits
    
    @field_validator("pairs", mode="before")
    @classmethod
    def validate_pairs(cls, v):
        if isinstance(v, str):
            pairs = []
            for pair_str in v.split(":"):
                parts = pair_str.split(",")
                if len(parts) >= 2:
                    exchange = parts[0].strip()
                    trading_pair = parts[1].strip()
                    order_amount_quote = Decimal(parts[2].strip()) if len(parts) > 2 else Decimal(20)
                    risk_aversion_gamma = Decimal(parts[3].strip()) if len(parts) > 3 else Decimal(6.0)
                    volatility_sigma = Decimal(parts[4].strip()) if len(parts) > 4 else Decimal(0.50)
                    risk_horizon_tau_hours = Decimal(parts[5].strip()) if len(parts) > 5 else Decimal(2.0)
                    max_inventory = Decimal(parts[6].strip()) if len(parts) > 6 else Decimal(0.01)
                    leverage = int(parts[7].strip()) if len(parts) > 7 else 10
                    pairs.append(PairConfig(
                        exchange=exchange,
                        trading_pair=trading_pair,
                        order_amount_quote=order_amount_quote,
                        risk_aversion_gamma=risk_aversion_gamma,
                        volatility_sigma=volatility_sigma,
                        risk_horizon_tau_hours=risk_horizon_tau_hours,
                        max_inventory=max_inventory,
                        leverage=leverage
                    ))
            return pairs if pairs else [PairConfig()]
        return v

class PMMAvellanedaMulti(ScriptStrategyBase):
    """
    Avellaneda-Stoikov Pure Market Making Strategy with Batch Order Operations
    
    This strategy implements the reservation price calculation from the Avellaneda-Stoikov model
    for perpetual futures market making. It places multiple order levels around the reservation
    price with constant spreads. Supports multiple trading pairs, each with its own configuration.
    
    Key features:
    - Reservation price calculation based on inventory
    - Multiple constant spread levels
    - Inventory management and tracking per pair
    - Order lifecycle management via connector's order tracker
    - Batch order placement and cancellation for improved efficiency
    - Multi-pair support with independent configurations
    """

    create_timestamp = 0
    account_config_set = False
    
    @classmethod
    def init_markets(cls, config: PMMAvellanedaMultiConfig):
        """Initialize markets from all pair configurations"""
        markets: Dict[str, Set[str]] = {}
        for pair_config in config.pairs:
            if pair_config.exchange not in markets:
                markets[pair_config.exchange] = set()
            markets[pair_config.exchange].add(pair_config.trading_pair)
        cls.markets = markets

    def __init__(self, connectors: Dict[str, ConnectorBase], config: PMMAvellanedaMultiConfig):
        super().__init__(connectors)
        self.config = config
        
        # Initialize lock for preventing concurrent order operations
        self._order_operation_lock = asyncio.Lock()
        self._order_operation_in_progress = False
        
        # Create a mapping from market key (exchange, trading_pair) to pair config
        self._pair_configs: Dict[Tuple[str, str], PairConfig] = {}
        for pair_config in config.pairs:
            market_key = (pair_config.exchange, pair_config.trading_pair)
            self._pair_configs[market_key] = pair_config
        
        # Per-pair state storage
        self._current_inventory: Dict[Tuple[str, str], Decimal] = {}
        self._cached_mid_price: Dict[Tuple[str, str], Decimal] = {}
        self._cached_reservation_price: Dict[Tuple[str, str], Decimal] = {}
        
        # Per-pair DataFrames to store last 6 filled orders
        self._filled_orders_df: Dict[Tuple[str, str], pd.DataFrame] = {}
        
        # Initialize per-pair state
        for market_key in self._pair_configs.keys():
            self._current_inventory[market_key] = Decimal("0")
            self._cached_mid_price[market_key] = Decimal("0")
            self._cached_reservation_price[market_key] = Decimal("0")
            self._filled_orders_df[market_key] = pd.DataFrame(columns=[
                "Timestamp", "Order ID", "Side", "Amount", "Price", "Inventory"
            ])
        
    def _get_market_key(self, exchange: str, trading_pair: str) -> Tuple[str, str]:
        """Get market key tuple"""
        return (exchange, trading_pair)
    
    def _get_pair_config(self, exchange: str, trading_pair: str) -> Optional[PairConfig]:
        """Get pair configuration for a market"""
        market_key = self._get_market_key(exchange, trading_pair)
        return self._pair_configs.get(market_key)
    
    def _get_in_flight_order(self, order_id: str, exchange: str) -> Optional[InFlightOrder]:
        """Get InFlightOrder from connector's order tracker"""
        connector = self.connectors[exchange]
        return connector._order_tracker.fetch_order(client_order_id=order_id)


    # Built-in event handler methods (called automatically by ScriptStrategyBase)
    
    def on_tick(self):
        if self.create_timestamp <= self.current_timestamp:
            # Prevent concurrent execution
            if self._order_operation_in_progress:
                self.logger().debug("Order operation already in progress, skipping tick")
                return
            
            # Process all pairs
            all_proposals: List[PerpetualOrderCandidate] = []
            for market_key, pair_config in self._pair_configs.items():
                exchange, trading_pair = market_key
                proposals = self.create_proposal(exchange, trading_pair)
                proposal_adjusted = self.adjust_proposal_to_budget(proposals, exchange)
                all_proposals.extend(proposal_adjusted)
            
            # Execute cancel then place sequentially to avoid order accumulation
            safe_ensure_future(self._cancel_and_place_orders(all_proposals))
            self.create_timestamp = self.config.order_refresh_time + self.current_timestamp
    
    async def _cancel_and_place_orders(self, proposal: List[PerpetualOrderCandidate]) -> None:
        """
        Cancel all active orders and then place new orders sequentially.
        This ensures old orders are cancelled before new ones are placed.
        Uses a lock to prevent concurrent execution.
        """
        # Check if operation is already in progress
        if self._order_operation_in_progress:
            self.logger().debug("Order operation already in progress, skipping")
            return
        
        async with self._order_operation_lock:
            try:
                self._order_operation_in_progress = True
                # First, cancel all active orders for all pairs and wait for completion
                await self._async_cancel_all_orders()
                # Then place new orders
                await self._async_place_orders(proposal)
            except Exception as e:
                self.logger().error(f"Error in _cancel_and_place_orders: {e}", exc_info=True)
            finally:
                self._order_operation_in_progress = False
        
    
    def apply_initial_setting(self):
        if not self.account_config_set:
            # Apply settings for all pairs
            for market_key, pair_config in self._pair_configs.items():
                exchange, trading_pair = market_key
                connector = self.connectors[exchange]
                connector.set_leverage(trading_pair, pair_config.leverage)
            self.account_config_set = True
    
    def create_proposal(self, exchange: str, trading_pair: str) -> List[PerpetualOrderCandidate]:
        """Create order proposals for a specific trading pair"""
        pair_config = self._get_pair_config(exchange, trading_pair)
        if not pair_config:
            return []
        
        connector = self.connectors[exchange]
        mid_price = connector.get_price_by_type(trading_pair, PriceType.MidPrice)
        market_key = self._get_market_key(exchange, trading_pair)
        inventory = self._get_current_inventory(exchange, trading_pair)
        
        reservation_price = self._calculate_reservation_price(
            mid_price, inventory, pair_config
        )
        
        # Cache values for status reporting
        self._cached_mid_price[market_key] = mid_price
        self._cached_reservation_price[market_key] = reservation_price
        
        orders = []
        for idx, bid_spread in enumerate(pair_config.bid_spread_levels):
            ask_spread = pair_config.ask_spread_levels[idx]
            # Ensure spreads are Decimal
            bid_spread_decimal = Decimal(str(bid_spread))
            ask_spread_decimal = Decimal(str(ask_spread))
            bid_price = reservation_price * (Decimal("1") - bid_spread_decimal)
            ask_price = reservation_price * (Decimal("1") + ask_spread_decimal)

            # Convert quote amount to base amount for both buy and sell orders
            # For perpetual orders, amount must be in base currency (BTC), not quote (USDC)
            order_amount = Decimal(str(pair_config.order_amount_quote))
            bid_amount = order_amount / bid_price
            ask_amount = order_amount / ask_price

            bid_order = PerpetualOrderCandidate(
                trading_pair=trading_pair,
                is_maker=True,
                order_type=OrderType.LIMIT_MAKER,
                order_side=TradeType.BUY,
                amount=bid_amount,
                price=bid_price,
                leverage=Decimal(pair_config.leverage)
            )
            
            ask_order = PerpetualOrderCandidate(
                trading_pair=trading_pair,
                is_maker=True,
                order_type=OrderType.LIMIT_MAKER,
                order_side=TradeType.SELL,
                amount=ask_amount,
                price=ask_price,
                leverage=Decimal(pair_config.leverage)
            )
            
            max_inv = Decimal(str(pair_config.max_inventory))
            if inventory >= max_inv:
                orders.extend([bid_order])
            elif inventory <= -max_inv:
                orders.extend([ask_order])
            else:
                orders.extend([bid_order, ask_order])
        return orders

    def adjust_proposal_to_budget(self, proposals: List[PerpetualOrderCandidate], exchange: str) -> List[PerpetualOrderCandidate]:
        """Adjust proposals to budget for a specific exchange"""
        connector = self.connectors[exchange]
        budget_checker = connector.budget_checker
        
        proposals_adjusted = budget_checker.adjust_candidates(proposals, all_or_none=True)
        return proposals_adjusted

    async def _async_place_orders(self, proposal: List[PerpetualOrderCandidate]) -> None:
        """Place multiple orders using batch API, grouped by exchange"""
        if not proposal:
            return
        
        # Group orders by exchange
        orders_by_exchange: Dict[str, List[Dict]] = {}
        for order in proposal:
            # Find which exchange this order belongs to
            exchange = None
            for market_key, pair_config in self._pair_configs.items():
                if order.trading_pair == market_key[1]:
                    exchange = market_key[0]
                    break
            
            if not exchange:
                continue
            
            if exchange not in orders_by_exchange:
                orders_by_exchange[exchange] = []
            
            order_dict = {
                "trading_pair": order.trading_pair,
                "amount": order.amount,
                "trade_type": order.order_side,
                "order_type": order.order_type,
                "price": order.price,
                "position_action": PositionAction.OPEN
            }
            orders_by_exchange[exchange].append(order_dict)
        
        # Place orders for each exchange
        for exchange, orders_to_create in orders_by_exchange.items():
            connector = self.connectors[exchange]
            await connector.batch_order_create(orders_to_create)
        
    async def _async_cancel_all_orders(self):
        """Cancel all active orders for all pairs using batch API"""
        # Group orders to cancel by exchange
        orders_by_exchange: Dict[str, List[InFlightOrder]] = {}
        
        for market_key, pair_config in self._pair_configs.items():
            exchange, trading_pair = market_key
            connector = self.connectors[exchange]
            
            # Get orders directly from connector's order tracker
            all_in_flight_orders = connector._order_tracker.active_orders
            
            if exchange not in orders_by_exchange:
                orders_by_exchange[exchange] = []
            
            for in_flight_order in all_in_flight_orders.values():
                # Filter by current trading pair only
                if in_flight_order.trading_pair != trading_pair:
                    continue
                
                # Check if order is actually still open
                if in_flight_order.is_open:
                    orders_by_exchange[exchange].append(in_flight_order)
                else:
                    # Order is already done (filled/cancelled/failed), skip it
                    self.logger().debug(
                        f"Order {in_flight_order.client_order_id} is {in_flight_order.current_state.name}, "
                        f"skipping cancellation"
                    )
        
        # Cancel orders for each exchange
        for exchange, orders_to_cancel in orders_by_exchange.items():
            if orders_to_cancel:
                try:
                    connector = self.connectors[exchange]
                    await connector.batch_order_cancel(orders_to_cancel)
                except Exception as e:
                    # Log error but don't fail - orders might already be cancelled
                    self.logger().warning(
                        f"Error cancelling orders for {exchange}: {e}. "
                        f"This may be normal if orders were already cancelled."
                    )
        
    def did_fill_order(self, event: OrderFilledEvent):
        """
        Called automatically by framework when order is filled.
        Update tracked order state and inventory position based on order fills.
        For perpetual futures:
        - BUY fill increases inventory (long position)
        - SELL fill decreases inventory (short position)
        """
        # Find which market this fill belongs to by matching trading pair
        # Since OrderFilledEvent doesn't have exchange attribute, we match by trading_pair
        market_key = None
        for market_key_candidate in self._pair_configs.keys():
            exchange, trading_pair = market_key_candidate
            if trading_pair == event.trading_pair:
                # Verify this connector has this order
                connector = self.connectors[exchange]
                order = connector._order_tracker.fetch_order(client_order_id=event.order_id)
                if order is not None:
                    market_key = market_key_candidate
                    break
        
        if not market_key or market_key not in self._current_inventory:
            self.logger().warning(f"Fill event for unknown market: {event.trading_pair} (order_id: {event.order_id})")
            return
        
        # Update inventory based on fill direction
        if event.trade_type == TradeType.BUY:
            # BUY fill = we bought, inventory increases (long position)
            self._current_inventory[market_key] += event.amount
        elif event.trade_type == TradeType.SELL:
            # SELL fill = we sold, inventory decreases (short position)
            self._current_inventory[market_key] -= event.amount
        
        # Add fill to DataFrame for this pair
        try:
            timestamp = pd.Timestamp.fromtimestamp(event.timestamp) if event.timestamp else pd.Timestamp.now()
        except (ValueError, OSError, TypeError):
            timestamp = pd.Timestamp.now()
        
        new_row = pd.DataFrame([{
            "Timestamp": timestamp,
            "Order ID": event.order_id[:8] + "..." if len(event.order_id) > 8 else event.order_id,
            "Side": event.trade_type.name,
            "Amount": float(event.amount),
            "Price": float(event.price),
            "Inventory": float(self._current_inventory[market_key])
        }])
        self._filled_orders_df[market_key] = pd.concat([self._filled_orders_df[market_key], new_row], ignore_index=True)
        
        # Keep only last 6 fills per pair
        if len(self._filled_orders_df[market_key]) > 6:
            self._filled_orders_df[market_key] = self._filled_orders_df[market_key].tail(6).reset_index(drop=True)
        
        exchange, trading_pair = market_key
        msg = (f"{event.trade_type.name} {round(event.amount, 2)} {trading_pair} "
               f"{exchange} at {round(event.price, 2)} | Inventory: {self._current_inventory[market_key]:.8f}")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)

    def _get_current_inventory(self, exchange: str, trading_pair: str) -> Decimal:
        """
        Get current inventory position tracked internally from order fills for a specific pair.
        Returns signed position amount: positive for long, negative for short.
        
        Position is tracked by accumulating fills:
        - BUY fills increase inventory (long position)
        - SELL fills decrease inventory (short position)
        """
        market_key = self._get_market_key(exchange, trading_pair)
        return self._current_inventory.get(market_key, Decimal("0"))

    def _calculate_reservation_price(self, mid_price: Decimal, inventory: Decimal, pair_config: PairConfig) -> Decimal:
        """
        Calculate reservation price using Avellaneda-Stoikov formula:
        r = s - q·γ·σ²·τ
        
        Where:
        - s = mid_price
        - q = inventory (signed, positive for long, negative for short)
        - γ = risk_aversion_gamma
        - σ = volatility_sigma (annualized)
        - τ = risk_horizon_tau_hours / 8760 (convert hours to years)
        """
        # Ensure risk_horizon_tau_hours is Decimal (convert if needed)
        tau_hours = Decimal(str(pair_config.risk_horizon_tau_hours))
        # Convert tau from hours to years
        tau_years = tau_hours / Decimal("8760")
        
        # Ensure all values are Decimal (convert if needed)
        gamma = Decimal(str(pair_config.risk_aversion_gamma))
        sigma = Decimal(str(pair_config.volatility_sigma))
        
        # Calculate inventory adjustment: q·γ·σ²·τ
        inventory_adjustment = inventory * gamma * (sigma ** Decimal("2")) * tau_years
        
        # Reservation price: r = s - q·γ·σ²·τ
        reservation_price = mid_price - inventory_adjustment
        
        return reservation_price

    def format_status(self) -> str:
        """
        Return status string showing current strategy state for all pairs.
        """
        if not self.ready_to_trade:
            return "Market connectors are not ready."
        
        lines = []
        lines.append("")
        lines.append("  Strategy Status (Multi-Pair):")
        lines.append(f"    Order Refresh Time: {self.config.order_refresh_time}s")
        lines.append(f"    Number of Pairs: {len(self._pair_configs)}")
        lines.append("")
        
        # Display status for each pair
        for market_key, pair_config in self._pair_configs.items():
            exchange, trading_pair = market_key
            mid_price = self._cached_mid_price.get(market_key, Decimal("0"))
            reservation_price = self._cached_reservation_price.get(market_key, Decimal("0"))
            inventory = self._current_inventory.get(market_key, Decimal("0"))
            
            lines.append(f"  Pair: {exchange}:{trading_pair}")
            lines.append(f"    Mid Price: {mid_price:.8f}")
            lines.append(f"    Reservation Price: {reservation_price:.8f}")
            lines.append(f"    Price Adjustment: {reservation_price - mid_price:.8f}")
            lines.append(f"    Current Inventory: {inventory:.8f}")
            lines.append(f"    Max Inventory: {pair_config.max_inventory:.8f}")
            lines.append(f"    Leverage: {pair_config.leverage}x")
            lines.append(f"    Spread Levels: {[f'{s*100:.4f}%' for s in pair_config.ask_spread_levels]}")
            lines.append(f"    Bid Spread Levels: {[f'{s*100:.4f}%' for s in pair_config.bid_spread_levels]}")
            lines.append("")
        
        # Display active orders
        try:
            df = self.active_orders_df()
            if len(df) > 0:
                lines.append("  Active Orders (All Pairs):")
                lines.extend(["    " + line for line in df.to_string(index=False).split("\n")])
            else:
                lines.append("  No active orders.")
        except ValueError:
            lines.append("  No active orders.")
        
        # Display last 6 filled orders for each pair
        for market_key, pair_config in self._pair_configs.items():
            exchange, trading_pair = market_key
            filled_df = self._filled_orders_df.get(market_key)
            
            if filled_df is not None and len(filled_df) > 0:
                lines.append("")
                lines.append(f"  Last 6 Filled Orders ({exchange}:{trading_pair}):")
                # Display in reverse order so most recent appears at bottom
                filled_df_display = filled_df.iloc[::-1].copy()
                # Format timestamp for display
                filled_df_display["Timestamp"] = filled_df_display["Timestamp"].dt.strftime("%H:%M:%S")
                lines.extend(["    " + line for line in filled_df_display.to_string(index=False).split("\n")])
        
        return "\n".join(lines)