"""
Mock connector for grid backtesting.

Simulates exchange connector behavior including:
- Order placement and tracking
- Fill simulation using OHLCV data
- Price queries (bid/ask/mid)
- Synthetic order book generation
"""

import uuid
from decimal import Decimal
from typing import Dict, List, Optional

from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    BuyOrderCreatedEvent,
    MarketEvent,
    OrderCancelledEvent,
    OrderFilledEvent,
    SellOrderCompletedEvent,
    SellOrderCreatedEvent,
)

from backtester.grid.data_types import (
    GridBacktestConfig,
    OrderStatus,
    SimulatedFill,
    SimulatedOrder,
)


class MockOrderTracker:
    """
    Mock order tracker for backtesting.

    Provides fetch_order() method that executors use to get InFlightOrder objects.
    """

    def __init__(self, in_flight_orders: Dict[str, InFlightOrder]):
        """
        Initialize with reference to connector's in-flight orders dict.

        Args:
            in_flight_orders: Reference to connector's _in_flight_orders dict
        """
        self._in_flight_orders = in_flight_orders

    def fetch_order(self, client_order_id: str) -> Optional[InFlightOrder]:
        """
        Fetch an in-flight order by client order ID.

        Args:
            client_order_id: The client order ID

        Returns:
            InFlightOrder if found, None otherwise
        """
        return self._in_flight_orders.get(client_order_id)


class MockBudgetChecker:
    """
    Mock budget checker for backtesting.

    In live trading, this validates orders against available balance.
    For backtesting, we assume infinite balance.
    """

    def adjust_candidates(self, candidates: List, all_or_none: bool = True) -> List:
        """
        Adjust order candidates based on available balance.

        For backtesting, we don't enforce balance constraints,
        so we return all candidates unmodified.

        Args:
            candidates: List of order candidates
            all_or_none: Whether to reject all if any fail

        Returns:
            All candidates unmodified
        """
        return candidates


class BacktestingMockConnector:
    """
    Mock connector that simulates exchange behavior for backtesting.

    This connector:
    - Tracks simulated orders and in-flight orders
    - Simulates fills based on OHLCV candle data
    - Provides synthetic order book and price queries
    - Emits PubSub-compatible events for order lifecycle
    """

    def __init__(
        self,
        trading_rules: Dict[str, TradingRule],
        backtest_config: GridBacktestConfig,
    ):
        self.trading_rules = trading_rules
        self.config = backtest_config
        self.orders: Dict[str, SimulatedOrder] = {}
        self.current_timestamp = 0

        # Current market state
        self.current_candle: Optional[Dict] = None
        self.best_bid = Decimal("0")
        self.best_ask = Decimal("0")
        self.mid_price = Decimal("0")

        # Order ID counter
        self._order_counter = 0

        # PubSub event system
        # _listeners maps event_tag (int) -> list of listener objects
        self._listeners: Dict[int, List] = {}

        # InFlightOrder tracking (needed for executor event processing)
        self._in_flight_orders: Dict[str, InFlightOrder] = {}

        # Order tracker (executors use this to fetch InFlightOrder objects)
        self._order_tracker = MockOrderTracker(self._in_flight_orders)

        # Budget checker (executors use this to validate order sizes)
        self.budget_checker = MockBudgetChecker()

    def add_listener(self, event_tag, listener):
        """
        Add an event listener for the specified event tag.

        Args:
            event_tag: MarketEvent enum value
            listener: EventListener object (or compatible callable)
        """
        tag_value = event_tag.value if hasattr(event_tag, 'value') else event_tag
        if tag_value not in self._listeners:
            self._listeners[tag_value] = []
        self._listeners[tag_value].append(listener)

    def remove_listener(self, event_tag, listener):
        """
        Remove an event listener for the specified event tag.

        Args:
            event_tag: MarketEvent enum value
            listener: EventListener object to remove
        """
        tag_value = event_tag.value if hasattr(event_tag, 'value') else event_tag
        if tag_value in self._listeners:
            try:
                self._listeners[tag_value].remove(listener)
            except ValueError:
                pass  # Listener not in list

    def trigger_event(self, event_tag, message):
        """
        Trigger an event by calling all registered listeners.

        This mimics the PubSub.trigger_event() behavior:
        1. Set _current_event_tag and _current_event_caller on the listener
        2. Call the listener with the message
        3. Clear _current_event_tag and _current_event_caller

        Args:
            event_tag: MarketEvent enum value
            message: Event message object (OrderFilledEvent, etc.)
        """
        tag_value = event_tag.value if hasattr(event_tag, 'value') else event_tag
        listeners = self._listeners.get(tag_value, [])

        for listener in listeners:
            try:
                # Set event info on listener (mimics PubSub behavior)
                if hasattr(listener, '_current_event_tag'):
                    listener._current_event_tag = tag_value
                if hasattr(listener, '_current_event_caller'):
                    listener._current_event_caller = self

                # Call the listener
                listener(message)

            except Exception as e:
                # Log but don't crash
                import logging
                logging.getLogger(__name__).error(
                    f"Error in event listener for tag {tag_value}: {e}",
                    exc_info=True
                )
            finally:
                # Clear event info
                if hasattr(listener, '_current_event_tag'):
                    listener._current_event_tag = 0
                if hasattr(listener, '_current_event_caller'):
                    listener._current_event_caller = None

    def update_market_state(self, candle: Dict, timestamp: int):
        """
        Update market prices and synthetic order book from candle data.

        Args:
            candle: Dict with keys 'open', 'high', 'low', 'close', 'volume'
            timestamp: Current timestamp
        """
        self.current_candle = candle
        self.current_timestamp = timestamp

        close = Decimal(str(candle["close"]))
        spread = close * self.config.spread_bps / Decimal("10000")

        self.mid_price = close
        self.best_bid = close - spread / 2
        self.best_ask = close + spread / 2

    def get_price_by_type(self, trading_pair: str, price_type: PriceType) -> Decimal:
        """
        Get price for the specified price type.

        Called by GridExecutor via ExecutorBase.get_price()
        """
        if price_type == PriceType.BestBid:
            return self.best_bid
        elif price_type == PriceType.BestAsk:
            return self.best_ask
        elif price_type == PriceType.MidPrice:
            return self.mid_price
        else:
            # Default to mid price for other types
            return self.mid_price

    def buy(
        self,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal,
        **kwargs
    ) -> str:
        """
        Place a buy order.

        Creates both SimulatedOrder and InFlightOrder, then emits BuyOrderCreated event.
        """
        order_id = self._generate_order_id()

        # Create simulated order (for fill simulation)
        self.orders[order_id] = SimulatedOrder(
            id=order_id,
            trading_pair=trading_pair,
            side=TradeType.BUY,
            price=price,
            amount=amount,
            order_type=order_type,
            created_at=self.current_timestamp,
            status=OrderStatus.OPEN,
        )

        # Create InFlightOrder (for executor tracking)
        in_flight_order = InFlightOrder(
            client_order_id=order_id,
            exchange_order_id=order_id,
            trading_pair=trading_pair,
            order_type=order_type,
            trade_type=TradeType.BUY,
            amount=amount,
            price=price,
            creation_timestamp=float(self.current_timestamp),
            initial_state=OrderState.OPEN,
        )
        self._in_flight_orders[order_id] = in_flight_order

        # Emit BuyOrderCreated event (executor uses this to link InFlightOrder to TrackedOrder)
        self.trigger_event(
            MarketEvent.BuyOrderCreated,
            BuyOrderCreatedEvent(
                timestamp=float(self.current_timestamp),
                type=order_type,
                trading_pair=trading_pair,
                amount=amount,
                price=price,
                order_id=order_id,
                creation_timestamp=float(self.current_timestamp),
                exchange_order_id=order_id,
            )
        )

        return order_id

    def sell(
        self,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal,
        **kwargs
    ) -> str:
        """
        Place a sell order.

        Creates both SimulatedOrder and InFlightOrder, then emits SellOrderCreated event.
        """
        order_id = self._generate_order_id()

        # Create simulated order (for fill simulation)
        self.orders[order_id] = SimulatedOrder(
            id=order_id,
            trading_pair=trading_pair,
            side=TradeType.SELL,
            price=price,
            amount=amount,
            order_type=order_type,
            created_at=self.current_timestamp,
            status=OrderStatus.OPEN,
        )

        # Create InFlightOrder (for executor tracking)
        in_flight_order = InFlightOrder(
            client_order_id=order_id,
            exchange_order_id=order_id,
            trading_pair=trading_pair,
            order_type=order_type,
            trade_type=TradeType.SELL,
            amount=amount,
            price=price,
            creation_timestamp=float(self.current_timestamp),
            initial_state=OrderState.OPEN,
        )
        self._in_flight_orders[order_id] = in_flight_order

        # Emit SellOrderCreated event (executor uses this to link InFlightOrder to TrackedOrder)
        self.trigger_event(
            MarketEvent.SellOrderCreated,
            SellOrderCreatedEvent(
                timestamp=float(self.current_timestamp),
                type=order_type,
                trading_pair=trading_pair,
                amount=amount,
                price=price,
                order_id=order_id,
                creation_timestamp=float(self.current_timestamp),
                exchange_order_id=order_id,
            )
        )

        return order_id

    def cancel(self, trading_pair: str, order_id: str):
        """
        Cancel an order.

        Emits OrderCancelled event, then removes order from tracking.
        """
        if order_id in self.orders:
            # Emit OrderCancelled event
            event = OrderCancelledEvent(
                timestamp=float(self.current_timestamp),
                order_id=order_id,
                exchange_order_id=order_id,
            )
            self.trigger_event(MarketEvent.OrderCancelled, event)

            # Update order status
            self.orders[order_id].status = OrderStatus.CANCELLED

            # Remove from both tracking dicts
            del self.orders[order_id]
            if order_id in self._in_flight_orders:
                del self._in_flight_orders[order_id]

    def simulate_fills_and_emit_events(self, candle: Dict) -> List[SimulatedFill]:
        """
        Simulate which orders would fill given the candle OHLCV data, and emit events.

        Fill logic:
        - LIMIT BUY: fills if candle.low <= order.price → fill at order.price
        - LIMIT SELL: fills if candle.high >= order.price → fill at order.price
        - MARKET orders: fill immediately at candle.close

        For each fill:
        1. Get InFlightOrder from _in_flight_orders
        2. Apply TradeUpdate (fill_price, fill_amount, fee)
        3. Apply OrderUpdate (new_state=FILLED)
        4. Emit OrderFilled event → executors receive this
        5. Emit BuyOrderCompleted / SellOrderCompleted event
        6. Remove from orders dict (keep in _in_flight_orders for executor reference)

        Args:
            candle: Dict with 'open', 'high', 'low', 'close', 'volume'

        Returns:
            List of SimulatedFill objects
        """
        fills = []
        high = Decimal(str(candle["high"]))
        low = Decimal(str(candle["low"]))
        close = Decimal(str(candle["close"]))

        # Check each open order
        for order in list(self.orders.values()):
            if order.status != OrderStatus.OPEN:
                continue

            fill_price = None

            if order.order_type == OrderType.MARKET:
                # Market orders fill immediately at close price
                fill_price = close
            elif order.order_type.is_limit_type():
                if order.side == TradeType.BUY:
                    # Buy limit fills if low touches or goes below limit price
                    if low <= order.price:
                        fill_price = order.price
                else:  # SELL
                    # Sell limit fills if high touches or goes above limit price
                    if high >= order.price:
                        fill_price = order.price

            if fill_price is not None:
                # Create fill record
                fill = SimulatedFill(
                    timestamp=self.current_timestamp,
                    order_id=order.id,
                    trading_pair=order.trading_pair,
                    side=order.side,
                    order_type=order.order_type,
                    price=fill_price,
                    amount=order.amount,
                    fee_percent=self.config.trade_fee_bps / Decimal("10000"),
                )
                fills.append(fill)

                # Get InFlightOrder and update it
                in_flight_order = self._in_flight_orders.get(order.id)
                if in_flight_order:
                    # Apply trade update
                    trade_update = TradeUpdate(
                        trade_id=str(uuid.uuid4()),
                        client_order_id=order.id,
                        exchange_order_id=order.id,
                        trading_pair=order.trading_pair,
                        fill_timestamp=float(self.current_timestamp),
                        fill_price=fill_price,
                        fill_base_amount=order.amount,
                        fill_quote_amount=order.amount * fill_price,
                        fee=AddedToCostTradeFee(percent=fill.fee_percent),
                    )
                    in_flight_order.update_with_trade_update(trade_update)

                    # Apply order update (mark as FILLED)
                    order_update = OrderUpdate(
                        trading_pair=order.trading_pair,
                        update_timestamp=float(self.current_timestamp),
                        new_state=OrderState.FILLED,
                        client_order_id=order.id,
                        exchange_order_id=order.id,
                    )
                    in_flight_order.update_with_order_update(order_update)

                    # Emit OrderFilled event (executors listen to this)
                    order_filled_event = OrderFilledEvent(
                        timestamp=float(self.current_timestamp),
                        order_id=order.id,
                        trading_pair=order.trading_pair,
                        trade_type=order.side,
                        order_type=order.order_type,
                        price=fill_price,
                        amount=order.amount,
                        trade_fee=AddedToCostTradeFee(percent=fill.fee_percent),
                        exchange_trade_id=str(uuid.uuid4()),
                        exchange_order_id=order.id,
                    )
                    self.trigger_event(MarketEvent.OrderFilled, order_filled_event)

                    # Emit OrderCompleted event
                    base_asset, quote_asset = order.trading_pair.split("-")
                    if order.side == TradeType.BUY:
                        completed_event = BuyOrderCompletedEvent(
                            timestamp=float(self.current_timestamp),
                            order_id=order.id,
                            base_asset=base_asset,
                            quote_asset=quote_asset,
                            base_asset_amount=order.amount,
                            quote_asset_amount=order.amount * fill_price,
                            order_type=order.order_type,
                            exchange_order_id=order.id,
                        )
                        self.trigger_event(MarketEvent.BuyOrderCompleted, completed_event)
                    else:
                        completed_event = SellOrderCompletedEvent(
                            timestamp=float(self.current_timestamp),
                            order_id=order.id,
                            base_asset=base_asset,
                            quote_asset=quote_asset,
                            base_asset_amount=order.amount,
                            quote_asset_amount=order.amount * fill_price,
                            order_type=order.order_type,
                            exchange_order_id=order.id,
                        )
                        self.trigger_event(MarketEvent.SellOrderCompleted, completed_event)

                # Mark order as filled and remove from orders dict
                # (Keep in _in_flight_orders for executor reference)
                order.status = OrderStatus.FILLED
                order.filled_amount = order.amount
                del self.orders[order.id]

        return fills

    def _generate_order_id(self) -> str:
        """Generate unique order ID"""
        self._order_counter += 1
        return f"BACKTEST_{self._order_counter}_{uuid.uuid4().hex[:8]}"

    def create_synthetic_orderbook(
        self, mid_price: Decimal
    ) -> tuple[List[tuple[Decimal, Decimal]], List[tuple[Decimal, Decimal]]]:
        """
        Create synthetic order book levels for depth queries.

        Returns:
            Tuple of (bids, asks) where each is list of (price, size) tuples
        """
        bids = []
        asks = []
        spacing = mid_price * self.config.level_spacing_bps / Decimal("10000")

        for i in range(self.config.orderbook_levels):
            bid_price = mid_price - spacing * Decimal(i + 1)
            ask_price = mid_price + spacing * Decimal(i + 1)
            bids.append((bid_price, self.config.level_size))
            asks.append((ask_price, self.config.level_size))

        return bids, asks
