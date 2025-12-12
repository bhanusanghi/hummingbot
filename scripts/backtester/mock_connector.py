"""
Mock connector for backtesting - simulates exchange connector using kline data.
"""

from decimal import Decimal
from typing import Dict, List, Optional, Tuple
import pandas as pd

from hummingbot.core.data_type.common import OrderType, PositionSide, PriceType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.core.network_iterator import NetworkStatus
from hummingbot.connector.client_order_tracker import ClientOrderTracker
from hummingbot.connector.derivative.position import Position


class MockOrderBook:
    """Mock order book that returns synthetic data from candle close price"""
    
    def __init__(self, best_bid: Decimal, best_ask: Decimal, best_bid_size: Decimal, best_ask_size: Decimal, 
                 bid_levels: List[Tuple[float, float]] = None, ask_levels: List[Tuple[float, float]] = None):
        self._best_bid = best_bid
        self._best_ask = best_ask
        self._best_bid_size = best_bid_size
        self._best_ask_size = best_ask_size
        self._bid_levels = bid_levels or []
        self._ask_levels = ask_levels or []
    
    @property
    def snapshot(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Returns order book snapshot as DataFrames matching real connector format"""
        update_id = 1
        
        # Create bid levels
        bids_rows = []
        if self._bid_levels:
            for price, amount in self._bid_levels:
                bids_rows.append(OrderBookRow(float(price), float(amount), update_id))
        else:
            # Default: single bid level
            bids_rows.append(OrderBookRow(float(self._best_bid), float(self._best_bid_size), update_id))
        
        # Create ask levels
        asks_rows = []
        if self._ask_levels:
            for price, amount in self._ask_levels:
                asks_rows.append(OrderBookRow(float(price), float(amount), update_id))
        else:
            # Default: single ask level
            asks_rows.append(OrderBookRow(float(self._best_ask), float(self._best_ask_size), update_id))
        
        bids_df = pd.DataFrame(data=bids_rows, columns=OrderBookRow._fields, dtype="float64")
        asks_df = pd.DataFrame(data=asks_rows, columns=OrderBookRow._fields, dtype="float64")
        
        return bids_df, asks_df


class MockConnector:
    """
    Mock connector for backtesting that uses the REAL ClientOrderTracker.

    Note: Does NOT inherit from PubSub/ConnectorBase (Cython classes).
    Event delivery is handled by engine calling strategy methods directly.
    """

    def __init__(self, trading_pair: str, spread_bps: Decimal = Decimal("5")):
        self.trading_pair = trading_pair
        self.spread_bps = spread_bps
        self._current_candle: dict = None
        self._current_timestamp: int = 0
        self._order_counter: int = 0
        self._position_amount: Decimal = Decimal("0")
        self._position_side: PositionSide = PositionSide.BOTH
        self.ready = True
        self.name = "mock_exchange"
        self.display_name = "Mock Exchange"
        self._leverage: int = 100
        self._order_tag: str = None
        self._network_status = NetworkStatus.CONNECTED  # Always connected in backtest

        # Track balances for base/quote assets (infinite for backtesting)
        base, quote = trading_pair.split("-")
        self._balances: Dict[str, Decimal] = {
            base: Decimal("999999999"),  # Effectively infinite
            quote: Decimal("999999999")
        }
        self.trading_pairs = {trading_pair}  # Set of trading pairs
        self._event_logs: List = []  # Empty event logs for backtesting

        # Use the REAL ClientOrderTracker - same as live connectors!
        self._order_tracker = ClientOrderTracker(connector=self)

    @property
    def current_timestamp(self) -> int:
        """Required by ClientOrderTracker"""
        return self._current_timestamp
    
    def set_candle(self, candle: dict):
        """Called by engine each tick to update market data"""
        self._current_candle = candle

    def set_timestamp(self, timestamp: int):
        """Called by engine each tick to update current timestamp"""
        self._current_timestamp = timestamp

    # === Order Lifecycle Methods ===

    def buy(self, trading_pair: str, amount: Decimal, order_type: OrderType,
            price: Decimal, **kwargs) -> str:
        """Create buy order and track via ClientOrderTracker"""
        client_order_id = f"bt_buy_{self._order_counter}"
        self._order_counter += 1

        order = InFlightOrder(
            client_order_id=client_order_id,
            exchange_order_id=client_order_id,  # Same as client in backtest
            trading_pair=trading_pair,
            order_type=order_type,
            trade_type=TradeType.BUY,
            amount=amount,
            price=price,
            creation_timestamp=self._current_timestamp,
            initial_state=OrderState.OPEN,  # Immediately open in backtest
        )
        self._order_tracker.start_tracking_order(order)
        return client_order_id

    def sell(self, trading_pair: str, amount: Decimal, order_type: OrderType,
             price: Decimal, **kwargs) -> str:
        """Create sell order and track via ClientOrderTracker"""
        client_order_id = f"bt_sell_{self._order_counter}"
        self._order_counter += 1

        order = InFlightOrder(
            client_order_id=client_order_id,
            exchange_order_id=client_order_id,
            trading_pair=trading_pair,
            order_type=order_type,
            trade_type=TradeType.SELL,
            amount=amount,
            price=price,
            creation_timestamp=self._current_timestamp,
            initial_state=OrderState.OPEN,
        )
        self._order_tracker.start_tracking_order(order)
        return client_order_id

    def cancel(self, trading_pair: str, client_order_id: str):
        """Cancel order via ClientOrderTracker"""
        self._order_tracker.stop_tracking_order(client_order_id)

    def trigger_event(self, event_tag, event):
        """
        Stub - ClientOrderTracker calls this but we don't use PubSub.
        Engine handles event delivery by calling strategy methods directly.
        """
        pass  # No-op: events delivered by engine, not via PubSub

    # === Market Data ===

    def get_order_book(self, trading_pair: str) -> MockOrderBook:
        """Returns synthetic order book from candle close price"""
        if self._current_candle is None:
            raise ValueError("Candle not set - call set_candle() first")
        
        close = Decimal(str(self._current_candle['close']))
        spread = close * self.spread_bps / Decimal("10000")
        
        best_bid = close - spread / 2
        best_ask = close + spread / 2
        
        # Create multiple levels for maker price adjustment logic
        bid_levels = []
        ask_levels = []
        for i in range(10):  # 10 levels should be enough
            bid_price = best_bid - (spread * Decimal(str(i)) / 2)
            ask_price = best_ask + (spread * Decimal(str(i)) / 2)
            bid_levels.append((float(bid_price), 100.0))
            ask_levels.append((float(ask_price), 100.0))
        
        return MockOrderBook(
            best_bid=best_bid,
            best_ask=best_ask,
            best_bid_size=Decimal("100"),
            best_ask_size=Decimal("100"),
            bid_levels=bid_levels,
            ask_levels=ask_levels
        )
    
    def get_price_by_type(self, trading_pair: str, price_type: PriceType) -> Decimal:
        """Returns close price as mark/mid price"""
        if self._current_candle is None:
            raise ValueError("Candle not set - call set_candle() first")
        return Decimal(str(self._current_candle['close']))
    
    def get_price(self, trading_pair: str, is_buy: bool, amount: Decimal = None) -> Decimal:
        """
        Get price for the market trading pair.
        :param trading_pair: The market trading pair
        :param is_buy: Whether to buy (True = ask price) or sell (False = bid price)
        :param amount: The amount (optional, not used in mock)
        :returns: The price
        """
        if self._current_candle is None:
            raise ValueError("Candle not set - call set_candle() first")
        
        close = Decimal(str(self._current_candle['close']))
        spread = close * self.spread_bps / Decimal("10000")
        
        if is_buy:
            # Buying = ask price = close + spread/2
            return close + spread / 2
        else:
            # Selling = bid price = close - spread/2
            return close - spread / 2
    
    def get_balance(self, currency: str) -> Decimal:
        """
        Get balance for a currency.
        In backtesting, returns effectively infinite balance.
        :param currency: The currency (token) name
        :returns: Balance for the specified currency
        """
        return self._balances.get(currency, Decimal("999999999"))
    
    def get_available_balance(self, currency: str) -> Decimal:
        """
        Get available balance for a currency.
        In backtesting, returns effectively infinite balance.
        :param currency: The currency (token) name
        :returns: Available balance for the specified currency
        """
        return self._balances.get(currency, Decimal("999999999"))
    
    @property
    def network_status(self) -> NetworkStatus:
        """Returns network status - always CONNECTED in backtest"""
        return self._network_status
    
    @property
    def limit_orders(self) -> List:
        """
        Returns list of limit orders.
        In backtesting, returns empty list since orders are tracked via strategy.pending_orders.
        """
        return []
    
    @property
    def event_logs(self) -> List:
        """
        Returns event logs.
        In backtesting, returns empty list since we don't track events.
        """
        return self._event_logs
    
    def set_leverage(self, trading_pair: str, leverage: int):
        """Set leverage (for compatibility with real connector)"""
        self._leverage = leverage
    
    def set_order_tag(self, order_tag: str):
        """Set order tag (for compatibility with real connector)"""
        self._order_tag = order_tag
    
    @property
    def _perpetual_trading(self):
        """Returns self to simulate connector._perpetual_trading.get_position()"""
        return self
    
    def get_position(self, trading_pair: str) -> Position:
        """Returns current simulated position"""
        if self._position_amount == 0:
            return None
        
        position_side = PositionSide.LONG if self._position_amount > 0 else PositionSide.SHORT
        
        # Create a Position object matching the real connector format
        return Position(
            trading_pair=trading_pair,
            position_side=position_side,
            unrealized_pnl=Decimal("0"),  # Not calculated in backtest
            entry_price=Decimal("0"),  # Not tracked in simple backtest
            amount=abs(self._position_amount),
            leverage=Decimal(self._leverage)
        )
    
    def update_position(self, fill_amount: Decimal, side: TradeType):
        """Called when orders fill to update position"""
        if side == TradeType.BUY:
            self._position_amount += fill_amount
        else:
            self._position_amount -= fill_amount
        
        # Update position side
        if self._position_amount > 0:
            self._position_side = PositionSide.LONG
        elif self._position_amount < 0:
            self._position_side = PositionSide.SHORT
        else:
            self._position_side = PositionSide.BOTH
    
    async def batch_order_cancel(self, orders_to_cancel: List):
        """
        Mock batch order cancel - no-op in backtesting.
        In backtesting, orders are tracked via strategy.pending_orders and
        fills are simulated by the engine, so cancellation is not needed.
        """
        # No-op: orders are tracked via strategy.pending_orders, not connector
        pass
    
    async def batch_order_create(self, orders_to_create: List) -> List:
        """
        Mock batch order create - no-op in backtesting.
        Returns empty list to match async signature.
        In backtesting, orders are tracked via strategy.pending_orders.
        """
        # No-op: orders are tracked via strategy.pending_orders, not connector
        return []

