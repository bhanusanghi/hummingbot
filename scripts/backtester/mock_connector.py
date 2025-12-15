"""
Mock Perpetual Connector for Backtesting

Inherits from PerpetualDerivativePyBase to satisfy Cython type checks.
Stubs all abstract methods and provides backtest-specific functionality.
"""

import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from hummingbot.connector.derivative.perpetual_budget_checker import PerpetualBudgetChecker
from hummingbot.connector.derivative.position import Position
from hummingbot.connector.perpetual_trading import PerpetualTrading
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, PositionSide, PriceType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.perpetual_api_order_book_data_source import PerpetualAPIOrderBookDataSource
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee, TradeFeeBase
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.network_iterator import NetworkStatus
from hummingbot.core.web_assistant.auth import AuthBase
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory


class MockOrderBook:
    """Mock order book that returns synthetic data from candle close price"""

    def __init__(self, best_bid: Decimal, best_ask: Decimal,
                 bid_levels: List[Tuple[float, float]] = None,
                 ask_levels: List[Tuple[float, float]] = None):
        self._best_bid = best_bid
        self._best_ask = best_ask
        self._bid_levels = bid_levels or []
        self._ask_levels = ask_levels or []

    @property
    def snapshot(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Returns order book snapshot as DataFrames matching real connector format"""
        update_id = 1

        bids_rows = []
        if self._bid_levels:
            for price, amount in self._bid_levels:
                bids_rows.append(OrderBookRow(float(price), float(amount), update_id))
        else:
            bids_rows.append(OrderBookRow(float(self._best_bid), 100.0, update_id))

        asks_rows = []
        if self._ask_levels:
            for price, amount in self._ask_levels:
                asks_rows.append(OrderBookRow(float(price), float(amount), update_id))
        else:
            asks_rows.append(OrderBookRow(float(self._best_ask), 100.0, update_id))

        bids_df = pd.DataFrame(data=bids_rows, columns=OrderBookRow._fields, dtype="float64")
        asks_df = pd.DataFrame(data=asks_rows, columns=OrderBookRow._fields, dtype="float64")

        return bids_df, asks_df


class MockAuth(AuthBase):
    """Minimal auth stub for backtesting"""
    async def rest_authenticate(self, request):
        return request

    async def ws_authenticate(self, request):
        return request


class MockOrderBookDataSource(PerpetualAPIOrderBookDataSource):
    """Minimal order book data source stub"""

    def __init__(self, trading_pairs: List[str]):
        self._trading_pairs = trading_pairs

    async def get_last_traded_prices(self, trading_pairs: List[str], domain: Optional[str] = None) -> Dict[str, float]:
        return {tp: 0.0 for tp in trading_pairs}

    async def get_funding_info(self, trading_pair: str) -> Dict[str, Any]:
        return {}

    async def listen_for_subscriptions(self):
        await asyncio.sleep(float('inf'))

    async def listen_for_order_book_diffs(self, ev_loop, output):
        await asyncio.sleep(float('inf'))

    async def listen_for_order_book_snapshots(self, ev_loop, output):
        await asyncio.sleep(float('inf'))

    async def listen_for_trades(self, ev_loop, output):
        await asyncio.sleep(float('inf'))


class MockUserStreamDataSource(UserStreamTrackerDataSource):
    """Minimal user stream data source stub"""

    @property
    def last_recv_time(self) -> float:
        return 0.0

    async def listen_for_user_stream(self, output):
        await asyncio.sleep(float('inf'))


class MockPerpetualConnector:
    """
    Mock perpetual connector for backtesting.

    This is a minimal implementation that provides the interface needed by
    the strategy without inheriting from the complex Cython base classes.

    For backtesting with mm_grid_backtester.py, we call create_proposal() directly
    and bypass the connector entirely. This mock is only needed to:
    1. Satisfy the strategy constructor requirements
    2. Provide methods used by process_tick_data() if running full tick simulation
    """

    def __init__(self, trading_pair: str, spread_bps: Decimal = Decimal("5")):
        self.trading_pair = trading_pair
        self.spread_bps = spread_bps
        self._trading_pairs = [trading_pair]

        # Current market state (set by backtester each tick)
        self._current_candle: Optional[dict] = None
        self._current_timestamp: int = 0

        # Position tracking
        self._position_amount: Decimal = Decimal("0")
        self._entry_price: Decimal = Decimal("0")
        self._leverage: int = 1

        # Connector interface
        self.name = "mock_perpetual"
        self.display_name = "Mock Perpetual"
        self.ready = True
        self._network_status = NetworkStatus.CONNECTED

        # Balances (effectively infinite for backtesting)
        base, quote = trading_pair.split("-")
        self._account_balances: Dict[str, Decimal] = {
            base: Decimal("999999999"),
            quote: Decimal("999999999"),
        }
        self._account_available_balances = self._account_balances.copy()

        # Order tracking
        self._order_tracker = MockOrderTracker()

        # Perpetual trading interface (for get_position)
        self._perpetual_trading = MockPerpetualTrading(trading_pair)

        # Trading rules
        self._trading_rules: Dict[str, TradingRule] = {
            trading_pair: TradingRule(
                trading_pair=trading_pair,
                min_order_size=Decimal("0.001"),
                min_price_increment=Decimal("0.01"),
                min_base_amount_increment=Decimal("0.001"),
            )
        }

    # === Properties required by strategy ===

    @property
    def current_timestamp(self) -> int:
        return self._current_timestamp

    @property
    def network_status(self) -> NetworkStatus:
        return self._network_status

    @property
    def trading_pairs(self) -> List[str]:
        return self._trading_pairs

    def trading_rules(self) -> Dict[str, TradingRule]:
        return self._trading_rules

    # === Methods called by backtester to update state ===

    def set_candle(self, candle: dict):
        """Called by backtester each tick to update market data"""
        self._current_candle = candle

    def set_timestamp(self, timestamp: int):
        """Called by backtester each tick to update current timestamp"""
        self._current_timestamp = timestamp

    def update_position(self, fill_amount: Decimal, fill_price: Decimal, side: TradeType):
        """Called by backtester when an order fills"""
        if side == TradeType.BUY:
            # Buying increases position (or reduces short)
            new_position = self._position_amount + fill_amount
        else:
            # Selling decreases position (or increases short)
            new_position = self._position_amount - fill_amount

        # Update entry price (simple average for now)
        if self._position_amount == 0:
            self._entry_price = fill_price
        elif (self._position_amount > 0 and side == TradeType.BUY) or \
             (self._position_amount < 0 and side == TradeType.SELL):
            # Adding to position - weighted average
            total_value = abs(self._position_amount) * self._entry_price + fill_amount * fill_price
            self._entry_price = total_value / (abs(self._position_amount) + fill_amount)

        self._position_amount = new_position
        self._perpetual_trading.update_position(self.trading_pair, new_position, self._entry_price)

    def set_position(self, amount: Decimal, entry_price: Decimal = Decimal("0")):
        """Directly set position (for initialization)"""
        self._position_amount = amount
        self._entry_price = entry_price
        self._perpetual_trading.update_position(self.trading_pair, amount, entry_price)

    # === Market data methods used by strategy.process_tick_data() ===

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
        for i in range(10):
            bid_price = best_bid - (spread * Decimal(str(i)) / 2)
            ask_price = best_ask + (spread * Decimal(str(i)) / 2)
            bid_levels.append((float(bid_price), 100.0))
            ask_levels.append((float(ask_price), 100.0))

        return MockOrderBook(
            best_bid=best_bid,
            best_ask=best_ask,
            bid_levels=bid_levels,
            ask_levels=ask_levels,
        )

    def get_price_by_type(self, trading_pair: str, price_type: PriceType) -> Decimal:
        """Returns close price as mark/mid price"""
        if self._current_candle is None:
            raise ValueError("Candle not set - call set_candle() first")
        return Decimal(str(self._current_candle['close']))

    def get_price(self, trading_pair: str, is_buy: bool, amount: Decimal = None) -> Decimal:
        """Get execution price (bid for sell, ask for buy)"""
        if self._current_candle is None:
            raise ValueError("Candle not set - call set_candle() first")

        close = Decimal(str(self._current_candle['close']))
        spread = close * self.spread_bps / Decimal("10000")

        if is_buy:
            return close + spread / 2
        else:
            return close - spread / 2

    # === Balance methods ===

    def get_balance(self, currency: str) -> Decimal:
        return self._account_balances.get(currency, Decimal("999999999"))

    def get_available_balance(self, currency: str) -> Decimal:
        return self._account_available_balances.get(currency, Decimal("999999999"))

    # === Configuration methods ===

    def set_leverage(self, trading_pair: str, leverage: int):
        """Set leverage for trading pair"""
        self._leverage = leverage

    def set_order_tag(self, order_tag: str):
        """Set order tag (no-op for backtesting)"""
        pass

    # === Order methods (stubs - backtester handles orders directly) ===

    async def batch_order_cancel(self, orders_to_cancel: List) -> List:
        """Stub - backtester manages orders directly"""
        return []

    async def batch_order_create(self, orders_to_create: List) -> List:
        """Stub - backtester manages orders directly"""
        return []

    def buy(self, trading_pair: str, amount: Decimal, order_type: OrderType,
            price: Decimal, **kwargs) -> str:
        """Stub for buy order creation"""
        return f"mock_buy_{self._current_timestamp}"

    def sell(self, trading_pair: str, amount: Decimal, order_type: OrderType,
             price: Decimal, **kwargs) -> str:
        """Stub for sell order creation"""
        return f"mock_sell_{self._current_timestamp}"

    def cancel(self, trading_pair: str, client_order_id: str):
        """Stub for order cancellation"""
        pass


class MockOrderTracker:
    """Minimal order tracker for backtesting"""

    def __init__(self):
        self._orders: Dict[str, InFlightOrder] = {}

    @property
    def active_orders(self) -> Dict[str, InFlightOrder]:
        return self._orders

    def start_tracking_order(self, order: InFlightOrder):
        self._orders[order.client_order_id] = order

    def stop_tracking_order(self, client_order_id: str):
        self._orders.pop(client_order_id, None)


class MockPerpetualTrading:
    """Mock perpetual trading interface for position tracking"""

    def __init__(self, trading_pair: str):
        self._trading_pair = trading_pair
        self._positions: Dict[str, Position] = {}

    def get_position(self, trading_pair: str) -> Optional[Position]:
        """Get current position for trading pair"""
        return self._positions.get(trading_pair)

    def update_position(self, trading_pair: str, amount: Decimal, entry_price: Decimal):
        """Update position state"""
        if amount == 0:
            self._positions.pop(trading_pair, None)
        else:
            position_side = PositionSide.LONG if amount > 0 else PositionSide.SHORT
            self._positions[trading_pair] = Position(
                trading_pair=trading_pair,
                position_side=position_side,
                unrealized_pnl=Decimal("0"),
                entry_price=entry_price,
                amount=abs(amount),
                leverage=Decimal("1"),
            )

    @property
    def account_positions(self) -> Dict[str, Position]:
        return self._positions
