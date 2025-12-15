"""
Orderly Network Perpetual Derivative Connector

This module implements the main connector class for Orderly Network perpetual futures trading.

The connector provides:
- Order placement and management
- Position tracking
- Balance management
- Funding rate information
- Real-time market data via WebSocket

Reference: Orderly Network EVM API
https://orderly.network/docs/build-on-omnichain/evm-api/introduction
"""

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from async_timeout import timeout
from bidict import bidict
import pandas as pd

import hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_constants as CONSTANTS
import hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_web_utils as web_utils
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_api_order_book_data_source import (
    OrderlyPerpetualAPIOrderBookDataSource,
)
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_auth import OrderlyPerpetualAuth
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_user_stream_data_source import (
    OrderlyPerpetualUserStreamDataSource,
)
from hummingbot.connector.derivative.position import Position
from hummingbot.connector.perpetual_derivative_py_base import PerpetualDerivativePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.connector.utils import combine_to_hb_trading_pair, get_new_client_order_id
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.data_type.cancellation_result import CancellationResult
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, PositionSide, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, OrderUpdate, TradeUpdate
from hummingbot.core.data_type.order_book_tracker_data_source import OrderBookTrackerDataSource
from hummingbot.core.data_type.trade_fee import TokenAmount, TradeFeeBase
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.utils.async_utils import safe_gather
from hummingbot.core.utils.estimate_fee import build_trade_fee
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory


class OrderlyPerpetualDerivative(PerpetualDerivativePyBase):
    """
    Orderly Network Perpetual Derivative Connector.

    Main connector class that integrates with Hummingbot's trading framework.
    """

    web_utils = web_utils

    def __init__(
        self,
        balance_asset_limit: Optional[Dict[str, Dict[str, Decimal]]] = None,
        rate_limits_share_pct: Decimal = Decimal("100"),
        orderly_perpetual_api_key: str = None,
        orderly_perpetual_api_secret: str = None,
        orderly_perpetual_account_id: str = None,
        trading_pairs: Optional[List[str]] = None,
        trading_required: bool = True,
        domain: str = CONSTANTS.DOMAIN,
        order_tag: Optional[str] = None,
    ):
        """
        Initialize Orderly Perpetual connector.

        Args:
            balance_asset_limit: Optional balance limits per asset
            rate_limits_share_pct: Percentage of rate limits to use
            orderly_perpetual_api_key: Orderly API key (public key)
            orderly_perpetual_api_secret: Orderly API secret (private key)
            orderly_perpetual_account_id: Orderly account ID
            trading_pairs: List of trading pairs to trade
            trading_required: Whether trading is required
            domain: Domain (mainnet or testnet)
            order_tag: Optional tag to add to all orders placed through this connector
        """
        self._orderly_perpetual_api_key = orderly_perpetual_api_key
        self._orderly_perpetual_api_secret = orderly_perpetual_api_secret
        self._orderly_perpetual_account_id = orderly_perpetual_account_id
        self._trading_required = trading_required
        self._trading_pairs = trading_pairs or []
        self._domain = domain
        self._position_mode = None
        self._order_tag = order_tag
        self._position_history: Optional[pd.DataFrame] = None  # Store closed position history
        super().__init__(balance_asset_limit, rate_limits_share_pct)

    # ============================================================
    # Properties
    # ============================================================

    @property
    def name(self) -> str:
        """Exchange name"""
        return self._domain

    @property
    def authenticator(self) -> Optional[OrderlyPerpetualAuth]:
        """Return authenticator if API keys are provided (needed for balance queries even if trading not required)"""
        # Check if API keys are provided - balance queries require authentication
        has_api_keys = (
            self._orderly_perpetual_api_key and
            self._orderly_perpetual_api_secret and
            self._orderly_perpetual_account_id
        )

        if has_api_keys:
            return OrderlyPerpetualAuth(
                account_id=self._orderly_perpetual_account_id,
                orderly_key=self._orderly_perpetual_api_key,
                orderly_secret=self._orderly_perpetual_api_secret,
            )
        return None

    @property
    def rate_limits_rules(self) -> List[RateLimit]:
        """Rate limits from constants"""
        return CONSTANTS.RATE_LIMITS

    @property
    def domain(self) -> str:
        """Domain identifier"""
        return self._domain

    @property
    def client_order_id_max_length(self) -> int:
        """Max length for client order IDs"""
        return CONSTANTS.MAX_ORDER_ID_LEN

    @property
    def client_order_id_prefix(self) -> str:
        """Prefix for client order IDs"""
        return CONSTANTS.BROKER_ID

    @property
    def trading_rules_request_path(self) -> str:
        """API path for trading rules"""
        return CONSTANTS.TRADING_RULES_URL

    @property
    def trading_pairs_request_path(self) -> str:
        """API path for trading pairs"""
        return CONSTANTS.EXCHANGE_INFO_URL

    @property
    def check_network_request_path(self) -> str:
        """API path for network check"""
        return CONSTANTS.SYSTEM_INFO_URL

    @property
    def trading_pairs(self) -> List[str]:
        """List of trading pairs"""
        return self._trading_pairs

    @property
    def position_history(self) -> Optional[pd.DataFrame]:
        """
        DataFrame containing closed position history.
        
        Columns:
            - position_id: Unique position identifier
            - status: Position status (e.g., 'closed')
            - type: Position type (e.g., 'liquidated', 'normal')
            - symbol: Trading pair symbol
            - side: Position side (LONG/SHORT)
            - avg_open_price: Average entry price
            - avg_close_price: Average exit price
            - max_position_qty: Maximum position quantity reached
            - closed_position_qty: Quantity closed
            - realized_pnl: Realized profit/loss
            - trading_fee: Trading fees paid
            - funding_fee: Accumulated funding fees
            - insurance_fund_fee: Insurance fund fees
            - liquidator_fee: Liquidator fees
            - liquidation_id: Liquidation ID (if applicable)
            - leverage: Leverage used
            - open_timestamp: Unix timestamp when position was opened
            - close_timestamp: Unix timestamp when position was closed
            - last_update_timestamp: Unix timestamp of last update
            
        Returns:
            DataFrame with closed position history, or None if no history available
        """
        return self._position_history

    @property
    def is_cancel_request_in_exchange_synchronous(self) -> bool:
        """Whether cancel requests are synchronous"""
        return True

    @property
    def is_trading_required(self) -> bool:
        """Whether trading is enabled"""
        return self._trading_required

    @property
    def funding_fee_poll_interval(self) -> int:
        """Funding fee polling interval in seconds"""
        return 120

    def set_order_tag(self, order_tag: Optional[str]) -> None:
        """
        Set order tag that will be added to all orders placed through this connector.
        
        Args:
            order_tag: Tag string to add to orders, or None to remove tag
        """
        self._order_tag = order_tag
        if order_tag:
            self.logger().info(f"Order tag set to: {order_tag}")
        else:
            self.logger().info("Order tag cleared")

    # ============================================================
    # Abstract Methods Implementation
    # ============================================================

    def supported_order_types(self) -> List[OrderType]:
        """
        :return a list of OrderType supported by this connector
        """
        return [OrderType.LIMIT, OrderType.LIMIT_MAKER, OrderType.MARKET]

    def supported_position_modes(self) -> List[PositionMode]:
        """
        Return list of supported position modes.

        Orderly supports ONEWAY mode (single position per symbol).
        """
        return [PositionMode.ONEWAY]

    def get_buy_collateral_token(self, trading_pair: str) -> str:
        """Return collateral token for buy orders"""
        return CONSTANTS.CURRENCY

    def get_sell_collateral_token(self, trading_pair: str) -> str:
        """Return collateral token for sell orders"""
        return CONSTANTS.CURRENCY

    def _create_web_assistants_factory(self) -> WebAssistantsFactory:
        """Create web assistants factory"""
        return web_utils.build_api_factory(
            throttler=self._throttler,
            auth=self._auth,
        )

    def _create_order_book_data_source(self) -> OrderBookTrackerDataSource:
        """Create order book data source"""
        return OrderlyPerpetualAPIOrderBookDataSource(
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
            domain=self._domain,
        )

    def _create_user_stream_data_source(self) -> UserStreamTrackerDataSource:
        """Create user stream data source"""
        return OrderlyPerpetualUserStreamDataSource(
            auth=self._auth,
            trading_pairs=self._trading_pairs,
            connector=self,
            api_factory=self._web_assistants_factory,
            domain=self._domain,
        )

    # ============================================================
    # Symbol Mapping
    # ============================================================

    async def _initialize_trading_pair_symbol_map(self):
        """
        Initialize trading pair symbol map by fetching trading rules.

        This method is called by the base class when exchange_symbol_associated_to_pair()
        is called before trading rules have been fetched.
        """
        try:
            exchange_info = await self._make_trading_rules_request()
            self._initialize_trading_pair_symbols_from_exchange_info(exchange_info=exchange_info)
        except Exception:
            self.logger().exception("There was an error requesting exchange info for symbol map initialization.")

    def _initialize_trading_pair_symbols_from_exchange_info(self, exchange_info: Dict[str, Any]):
        """
        Initialize bidirectional mapping between exchange symbols and Hummingbot trading pairs.

        Orderly format: PERP_BTC_USDC
        Hummingbot format: BTC-USDC

        Args:
            exchange_info: Exchange information dictionary
        """
        mapping = bidict()
        symbols_processed = 0
        symbols_skipped = 0

        for symbol_data in exchange_info:
            try:
                exchange_symbol = symbol_data.get("symbol")
                if not exchange_symbol or not exchange_symbol.startswith("PERP_"):
                    symbols_skipped += 1
                    continue

                # Convert to Hummingbot format
                trading_pair = web_utils.format_trading_pair(exchange_symbol)

                # Orderly uses unique symbols (PERP_BTC_USDC), no duplicates expected
                if trading_pair not in mapping.inverse:
                    mapping[exchange_symbol] = trading_pair
                    symbols_processed += 1
                else:
                    self.logger().error(f"[SYMBOL CONVERSION] Duplicate symbol found: {exchange_symbol} -> {trading_pair}")

            except Exception:
                self.logger().exception(f"[SYMBOL CONVERSION] Error parsing symbol: {symbol_data}")

        self._set_trading_pair_symbol_map(mapping)

    async def exchange_symbol_associated_to_pair(self, trading_pair: str) -> str:
        """
        Override to add logging for symbol conversion.

        Args:
            trading_pair: Trading pair in Hummingbot format (e.g., "ETH-USDC")

        Returns:
            Symbol in Orderly format (e.g., "PERP_ETH_USDC")
        """
        try:
            symbol_map = await self.trading_pair_symbol_map()

            if trading_pair not in symbol_map.inverse:
                raise KeyError(f"Trading pair '{trading_pair}' not found in symbol map")

            orderly_symbol = symbol_map.inverse[trading_pair]
            return orderly_symbol
        except KeyError:
            # Re-raise KeyError with more context
            raise
        except Exception as e:
            self.logger().error(
                f"[SYMBOL CONVERSION] Error converting trading pair '{trading_pair}': {e}",
                exc_info=True
            )
            raise

    # ============================================================
    # Trading Rules
    # ============================================================

    async def _make_trading_rules_request(self) -> Any:
        """
        Fetch trading rules from exchange.

        According to Orderly API docs:
        GET /v1/public/info - Returns all available symbols with trading rules

        Response structure:
        {
            "success": true,
            "data": {
                "rows": [
                    {
                        "symbol": "PERP_BTC_USDC",
                        "base_min": 1.0E-5,
                        "base_max": 20,
                        "base_tick": 1.0E-5,
                        "quote_min": 0,
                        "quote_max": 100000,
                        "quote_tick": 0.1,
                        "min_notional": 1,
                        ...
                    }
                ]
            }
        }

        Returns:
            List of trading rule dictionaries (rows from response)
        """
        url = web_utils.public_rest_url(
            CONSTANTS.TRADING_RULES_URL,
            domain=self._domain
        )

        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.TRADING_RULES_URL,
            method=RESTMethod.GET,
        )

        if not response.get("success", False):
            self.logger().error(f"[TRADING RULES] Failed to fetch trading rules: {response}")
            raise IOError(f"Failed to fetch trading rules: {response}")

        # Return the rows array which contains all trading rules
        data = response.get("data", {})
        rows = data.get("rows", [])

        # Log fetched trading rules
        self.logger().info(f"[TRADING RULES] Fetched {len(rows)} trading rules from exchange")
        if rows:
            self.logger().debug(f"[TRADING RULES] Sample symbols from exchange: {[r.get('symbol') for r in rows[:5]]}")

        return rows

    async def _make_trading_pairs_request(self) -> Any:
        """
        Fetch available trading pairs.

        Returns:
            Raw trading pairs response
        """
        url = web_utils.public_rest_url(
            CONSTANTS.EXCHANGE_INFO_URL,
            domain=self._domain
        )

        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.EXCHANGE_INFO_URL,
            method=RESTMethod.GET,
        )

        if not response.get("success", False):
            raise IOError(f"Failed to fetch trading pairs: {response}")

        return response.get("data", {}).get("rows", [])

    async def _format_trading_rules(self, exchange_info_list: List[Dict[str, Any]]) -> List[TradingRule]:
        """
        Parse raw trading rules into TradingRule objects.

        Args:
            exchange_info_list: List of raw trading rule dictionaries

        Returns:
            List of TradingRule objects
        """
        trading_rules = []

        for rule_data in exchange_info_list:
            try:
                if not web_utils.is_exchange_information_valid(rule_data):
                    continue

                orderly_symbol = rule_data["symbol"]
                # Format trading pair directly from symbol (don't use mapping since it's not initialized yet)
                trading_pair = web_utils.format_trading_pair(orderly_symbol)

                trading_rule = TradingRule(
                    trading_pair=trading_pair,
                    min_order_size=Decimal(str(rule_data.get("base_min", "0"))),
                    max_order_size=Decimal(str(rule_data.get("base_max", "1000000"))),
                    min_price_increment=Decimal(str(rule_data.get("quote_tick", "0.01"))),
                    min_base_amount_increment=Decimal(str(rule_data.get("base_tick", "0.01"))),
                    min_notional_size=Decimal(str(rule_data.get("min_notional", "0"))),
                    buy_order_collateral_token=CONSTANTS.CURRENCY,
                    sell_order_collateral_token=CONSTANTS.CURRENCY,
                )

                trading_rules.append(trading_rule)

            except Exception:
                self.logger().exception(f"Error parsing trading rule: {rule_data}")

        return trading_rules

    # ============================================================
    # Network & Connectivity
    # ============================================================

    async def _make_network_check_request(self):
        """Make network check request"""
        url = web_utils.public_rest_url(CONSTANTS.SYSTEM_INFO_URL, domain=self._domain)
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.SYSTEM_INFO_URL,
            method=RESTMethod.GET,
        )
        return response.get("success", False) and response.get("data", {}).get("status") == 0

    def _is_request_exception_related_to_time_synchronizer(self, request_exception: Exception):
        """Check if error is time-related (Orderly doesn't require time sync)"""
        return False

    def _is_order_not_found_during_status_update_error(self, status_update_exception: Exception) -> bool:
        """Check if error is due to order not found"""
        return CONSTANTS.ORDER_NOT_EXIST_MESSAGE in str(status_update_exception)

    def _is_order_not_found_during_cancelation_error(self, cancelation_exception: Exception) -> bool:
        """Check if error is due to order not found during cancel"""
        error_str = str(cancelation_exception)
        return (
            CONSTANTS.ORDER_NOT_EXIST_MESSAGE in error_str
            or CONSTANTS.ORDER_ALREADY_CANCELLED_MESSAGE in error_str
            or CONSTANTS.ORDER_ALREADY_FILLED_MESSAGE in error_str
            or CONSTANTS.CANCELLING_COMPLETED_ORDER_MESSAGE in error_str
            or f"'code': {CONSTANTS.ORDER_NOT_FOUND_ERROR_CODE}" in error_str  # Check code -1006
            or ("-1005" in error_str and "order" in error_str.lower() and "invalid" in error_str.lower())# -1005 "The order ID is invalid"
        )

    # ============================================================
    # Helper Methods
    # ============================================================

    async def _get_last_traded_price(self, trading_pair: str) -> float:
        """
        Get last traded price for a trading pair.

        Args:
            trading_pair: Trading pair in Hummingbot format

        Returns:
            Last traded price
        """
        symbol = await self.exchange_symbol_associated_to_pair(trading_pair)
        url = web_utils.public_rest_url(
            CONSTANTS.SYMBOL_INFO_URL.format(symbol=symbol),
            domain=self._domain
        )

        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.SYMBOL_INFO_URL,
            method=RESTMethod.GET,
        )

        if response.get("success"):
            data = response.get("data", {})
            return float(data.get("mark_price", 0))

        return 0.0

    # ============================================================
    # Order Placement & Management - Helper Methods
    # ============================================================

    async def _start_tracking_and_validate_order(
        self,
        trade_type: TradeType,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Optional[Decimal] = None,
        position_action: PositionAction = PositionAction.NIL,
        **kwargs
    ) -> Optional[InFlightOrder]:
        """
        Start tracking an order and validate it before placing.

        This method:
        1. Calculates/quantizes price and amount
        2. Starts tracking the order
        3. Validates order parameters (type, min size, min notional)
        4. Returns the tracked order object or None on failure

        Args:
            trade_type: BUY or SELL
            order_id: Client order ID
            trading_pair: Trading pair
            amount: Order amount
            order_type: Order type (LIMIT, LIMIT_MAKER, MARKET)
            price: Order price (optional for MARKET orders)
            position_action: Position action (OPEN/CLOSE)
            **kwargs: Additional parameters

        Returns:
            InFlightOrder object if valid, None if validation fails
        """
        try:
            # Calculate price for market orders
            if price is None or price.is_nan():
                price = self.get_price_for_volume(
                    trading_pair,
                    True if trade_type == TradeType.BUY else False,
                    amount
                ).result_price

            # Quantize price and amount
            price = self.quantize_order_price(trading_pair, price)
            amount = self.quantize_order_amount(trading_pair, amount)

            # Start tracking the order
            self.start_tracking_order(
                order_id=order_id,
                exchange_order_id=None,  # Will be set after API call
                trading_pair=trading_pair,
                trade_type=trade_type,
                price=price,
                amount=amount,
                order_type=order_type,
                position_action=position_action,
            )

            # Get the tracked order
            tracked_order = self._order_tracker.all_updatable_orders.get(order_id)
            if not tracked_order:
                self.logger().error(f"Failed to start tracking order {order_id}")
                return None

            # Validate order type support
            if order_type not in self.supported_order_types():
                self._update_order_after_creation_failure(
                    order_id=order_id,
                    trading_pair=trading_pair,
                    amount=amount,
                    trade_type=trade_type,
                    order_type=order_type,
                    price=price,
                    exception=ValueError(f"Order type {order_type} is not supported"),
                )
                return None

            # Get trading rules
            trading_rule = self._trading_rules.get(trading_pair)
            if not trading_rule:
                self._update_order_after_creation_failure(
                    order_id=order_id,
                    trading_pair=trading_pair,
                    amount=amount,
                    trade_type=trade_type,
                    order_type=order_type,
                    price=price,
                    exception=ValueError(f"Trading rule not found for {trading_pair}"),
                )
                return None

            # Validate min order size
            if amount < trading_rule.min_order_size:
                self._update_order_after_creation_failure(
                    order_id=order_id,
                    trading_pair=trading_pair,
                    amount=amount,
                    trade_type=trade_type,
                    order_type=order_type,
                    price=price,
                    exception=ValueError(
                        f"Order amount {amount} is below minimum order size {trading_rule.min_order_size}"
                    ),
                )
                return None

            # Validate min notional size
            notional_size = amount * price
            if notional_size < trading_rule.min_notional_size:
                self._update_order_after_creation_failure(
                    order_id=order_id,
                    trading_pair=trading_pair,
                    amount=amount,
                    trade_type=trade_type,
                    order_type=order_type,
                    price=price,
                    exception=ValueError(
                        f"Order notional size {notional_size} is below minimum {trading_rule.min_notional_size}"
                    ),
                )
                return None

            return tracked_order

        except Exception as e:
            self.logger().error(
                f"Error in _start_tracking_and_validate_order for {order_id}: {e}",
                exc_info=True
            )
            self._update_order_after_creation_failure(
                order_id=order_id,
                trading_pair=trading_pair,
                amount=amount,
                trade_type=trade_type,
                order_type=order_type,
                price=price if price else Decimal("0"),
                exception=e,
            )
            return None

    def _update_order_after_creation_success(
        self,
        exchange_order_id: Optional[str],
        order: InFlightOrder,
        update_timestamp: float,
        misc_updates: Optional[Dict[str, Any]] = None
    ):
        """
        Update order after successful creation on the exchange.

        Creates an OrderUpdate with the exchange_order_id and processes it
        through the order tracker. This triggers the appropriate order
        creation events.

        Args:
            exchange_order_id: Exchange-assigned order ID
            order: InFlightOrder object
            update_timestamp: Timestamp of the update
            misc_updates: Optional additional updates dictionary
        """
        order_update: OrderUpdate = OrderUpdate(
            client_order_id=order.client_order_id,
            exchange_order_id=exchange_order_id,
            trading_pair=order.trading_pair,
            update_timestamp=update_timestamp,
            new_state=order.current_state,  # Keep current state (typically PENDING_CREATE)
            misc_updates=misc_updates,
        )
        self._order_tracker.process_order_update(order_update)

    def _on_order_creation_failure(
        self,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
        exception: Exception,
        position_action: PositionAction = PositionAction.NIL,
    ):
        """
        Handle order creation failure that occurred during API call.

        Creates an OrderUpdate with FAILED state and processes it through
        the order tracker. This triggers the OrderFailure event.

        Args:
            order_id: Client order ID
            trading_pair: Trading pair
            amount: Order amount
            trade_type: BUY or SELL
            order_type: Order type
            price: Order price
            exception: Exception that caused the failure
            position_action: Position action (OPEN/CLOSE)
        """
        self.logger().error(
            f"Order creation failed for {order_id}: {exception}",
            exc_info=True
        )

        order_update: OrderUpdate = OrderUpdate(
            client_order_id=order_id,
            trading_pair=trading_pair,
            update_timestamp=self.current_timestamp,
            new_state=OrderState.FAILED,
        )
        self._order_tracker.process_order_update(order_update)

    def _update_order_after_creation_failure(
        self,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
        exception: Exception,
        position_action: PositionAction = PositionAction.NIL,
    ):
        """
        Handle order creation failure during validation (before API call).

        Similar to _on_order_creation_failure but used for validation failures
        that occur before the API call is made.

        Args:
            order_id: Client order ID
            trading_pair: Trading pair
            amount: Order amount
            trade_type: BUY or SELL
            order_type: Order type
            price: Order price
            exception: Exception that caused the failure
            position_action: Position action (OPEN/CLOSE)
        """
        self.logger().warning(
            f"Order validation failed for {order_id}: {exception}"
        )

        order_update: OrderUpdate = OrderUpdate(
            client_order_id=order_id,
            trading_pair=trading_pair,
            update_timestamp=self.current_timestamp,
            new_state=OrderState.FAILED,
        )
        self._order_tracker.process_order_update(order_update)

    # ============================================================
    # Order Placement & Management
    # ============================================================

    async def _place_order(
        self,
        order_id: str,
        trading_pair: str,
        amount: Decimal,
        trade_type: TradeType,
        order_type: OrderType,
        price: Decimal,
        position_action: PositionAction = PositionAction.NIL,
        **kwargs,
    ) -> Tuple[str, float]:
        """
        Place an order on the exchange.

        Args:
            order_id: Client order ID
            trading_pair: Trading pair
            amount: Order amount
            trade_type: BUY or SELL
            order_type: LIMIT or MARKET
            price: Order price
            position_action: OPEN or CLOSE

        Returns:
            Tuple of (exchange_order_id, timestamp)
        """
        symbol = await self.exchange_symbol_associated_to_pair(trading_pair)

        # Build order parameters according to Orderly API spec
        # Map Hummingbot order types to Orderly order types
        orderly_order_type = "MARKET"
        if order_type == OrderType.LIMIT:
            orderly_order_type = "LIMIT"
        elif order_type == OrderType.LIMIT_MAKER:
            orderly_order_type = "POST_ONLY"

        order_params = {
            "symbol": symbol,
            "client_order_id": order_id,
            "side": "BUY" if trade_type == TradeType.BUY else "SELL",
            "order_type": orderly_order_type,
            "order_quantity": float(self.quantize_order_amount(trading_pair, amount)),
            "reduce_only": position_action == PositionAction.CLOSE,
        }

        # Add price for non-MARKET orders
        if order_type != OrderType.MARKET:
            order_params["order_price"] = float(self.quantize_order_price(trading_pair, price))

        # Add order tag if configured
        if self._order_tag:
            self.logger().info(f"Adding order tag: {self._order_tag}")
            order_params["order_tag"] = self._order_tag

        # Make API call
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        url = web_utils.public_rest_url(
            CONSTANTS.CREATE_ORDER_URL,
            domain=self._domain
        )
        self.logger().info(f"Order params: {order_params}")
        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.CREATE_ORDER_LIMIT_ID,
            method=RESTMethod.POST,
            data=order_params,
            is_auth_required=True,
        )

        if not response.get("success", False):
            raise IOError(f"Order placement failed: {response}")

        data = response.get("data", {})
        exchange_order_id = str(data.get("order_id"))

        return exchange_order_id, self.current_timestamp

    async def _place_cancel(self, order_id: str, tracked_order: InFlightOrder):
        """
        Cancel an order.

        Args:
            order_id: Client order ID
            tracked_order: Tracked order object
        """
        symbol = await self.exchange_symbol_associated_to_pair(tracked_order.trading_pair)

        # Use exchange_order_id if available, otherwise use client_order_id
        # Orderly has separate endpoints:
        # - /v1/order: Cancel by exchange_order_id (requires order_id param)
        # - /v1/client/order: Cancel by client_order_id (requires client_order_id param)
        if tracked_order.exchange_order_id:
            # Cancel by exchange_order_id
            params = {
                "order_id": str(tracked_order.exchange_order_id),
                "symbol": symbol,
            }
            url = web_utils.public_rest_url(
                CONSTANTS.CANCEL_ORDER_URL,
                domain=self._domain
            )
            throttler_limit_id = CONSTANTS.CANCEL_ORDER_LIMIT_ID
        else:
            # Cancel by client_order_id
            params = {
                "client_order_id": str(order_id),
                "symbol": symbol,
            }
            url = web_utils.public_rest_url(
                CONSTANTS.CANCEL_ORDER_BY_CLIENT_ID_URL,
                domain=self._domain
            )
            throttler_limit_id = CONSTANTS.CANCEL_ORDER_BY_CLIENT_ID_LIMIT_ID
        
        # Make API call
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=throttler_limit_id,
            method=RESTMethod.DELETE,
            params=params,
            is_auth_required=True,
        )

        if not response.get("success", False):
            raise IOError(f"Order cancellation failed: {response}")

    async def batch_order_create(
        self,
        orders_to_create: List[Dict[str, Any]]
    ) -> List[Tuple[str, float]]:
        """
        Place multiple orders when more than 10 orders are to be created by creating multiple batch requests and calling promise.all on batch_order_create
        """
        if len(orders_to_create) <= 10:
            return await self.create_batch(orders_to_create)
        else:
            # Split into batches of 10
            batches = [orders_to_create[i:i + 10] for i in range(0, len(orders_to_create), 10)]
            self.logger().info(f"Placing {len(batches)} batches of {len(batches[0])} orders")
            # Execute all batches in parallel and collect results
            batch_results: List[List[Tuple[str, float]]] = await asyncio.gather(*[self.batch_order_create(batch) for batch in batches])
            # Flatten the nested list: List[List[Tuple[str, float]]] -> List[Tuple[str, float]]
            flattened_results: List[Tuple[str, float]] = []
            for batch_result in batch_results:
                flattened_results.extend(batch_result)
            return flattened_results
    
    async def create_batch(
        self,
        orders_to_create: List[Dict[str, Any]]
    ) -> List[Tuple[str, float]]:
        """
        Place multiple orders in a single batch request using modular pattern.

        This method:
        1. Generates client_order_ids for each order
        2. Tracks and validates each order using _start_tracking_and_validate_order()
        3. Makes batch API call with valid orders
        4. Processes results through order tracker (_update_order_after_creation_success
           or _on_order_creation_failure)

        Args:
            orders_to_create: List of order dictionaries with keys:
                - order_id: Client order ID (string, optional - will be generated if not provided)
                - trading_pair: Trading pair in Hummingbot format
                - amount: Order amount (Decimal)
                - trade_type: TradeType.BUY or TradeType.SELL
                - order_type: OrderType (LIMIT, LIMIT_MAKER, or MARKET)
                - price: Order price (Decimal)
                - position_action: PositionAction (OPEN or CLOSE)

        Returns:
            List of (exchange_order_id, timestamp) tuples for each order.
            If an order fails, exchange_order_id will be empty string.

        Raises:
            ValueError: If more than 10 orders provided (Orderly limitation)
            IOError: If the API request itself fails
        """
        # Validation: Check batch size limit
        if len(orders_to_create) > 10:
            raise ValueError(
                f"Batch order creation limited to 10 orders per request. "
                f"Received {len(orders_to_create)} orders."
            )

        if not orders_to_create:
            self.logger().warning("[BATCH ORDER] No orders to create")
            return []

        # Step 1: Generate client_order_ids and track/validate orders
        inflight_orders_to_create = []
        order_id_map = {}  # Map index to order_id for result matching

        for i, order_data in enumerate[Dict[str, Any]](orders_to_create):
            try:
                # Extract order parameters
                order_id = order_data.get("order_id")
            
                # Generate client_order_id if not provided
                if not order_id:
                    order_id = get_new_client_order_id(
                        is_buy=order_data["trade_type"] == TradeType.BUY,
                        trading_pair=order_data["trading_pair"],
                        hbot_order_id_prefix=self.client_order_id_prefix+str(i),
                        max_id_len=self.client_order_id_max_length,
                    )
                    order_data["order_id"] = order_id

                trading_pair = order_data["trading_pair"]
                amount = order_data["amount"]
                trade_type = order_data["trade_type"]
                order_type = order_data["order_type"]
                price = order_data.get("price")
                position_action = order_data.get("position_action", PositionAction.NIL)

                # Track and validate order
                valid_order = await self._start_tracking_and_validate_order(
                    trade_type=trade_type,
                    order_id=order_id,
                    trading_pair=trading_pair,
                    amount=amount,
                    order_type=order_type,
                    price=price,
                    position_action=position_action,
                )

                if valid_order is not None:
                    inflight_orders_to_create.append(valid_order)
                    order_id_map[i] = order_id
                else:
                    # Order failed validation, already handled by _start_tracking_and_validate_order
                    order_id_map[i] = None

            except Exception as e:
                self.logger().error(
                    f"[BATCH ORDER] Error preparing order {order_data.get('order_id', 'unknown')}: {e}",
                    exc_info=True
                )
                order_id_map[i] = None

        # Step 2: Build batch API request for valid orders
        if not inflight_orders_to_create:
            self.logger().error("[BATCH ORDER] No valid orders to submit after validation")
            return [("", self.current_timestamp) for _ in orders_to_create]

        batch_orders = []
        for in_flight_order in inflight_orders_to_create:
            try:
                # Get exchange symbol
                symbol = await self.exchange_symbol_associated_to_pair(in_flight_order.trading_pair)

                # Map Hummingbot order types to Orderly order types
                orderly_order_type = "MARKET"
                if in_flight_order.order_type == OrderType.LIMIT:
                    orderly_order_type = "LIMIT"
                elif in_flight_order.order_type == OrderType.LIMIT_MAKER:
                    orderly_order_type = "POST_ONLY"

                # Build order parameters (price and amount already quantized)
                order_params = {
                    "symbol": symbol,
                    "client_order_id": in_flight_order.client_order_id,
                    "side": "BUY" if in_flight_order.trade_type == TradeType.BUY else "SELL",
                    "order_type": orderly_order_type,
                    "order_quantity": float(in_flight_order.amount),
                    "reduce_only": in_flight_order.position == PositionAction.CLOSE,
                }

                # Add price for non-MARKET orders
                if in_flight_order.order_type != OrderType.MARKET:
                    order_params["order_price"] = float(in_flight_order.price)

                # Add order tag if configured
                if self._order_tag:
                    # self.logger().info(f"Adding order tag: {self._order_tag}")
                    order_params["order_tag"] = self._order_tag

                batch_orders.append(order_params)

            except Exception as e:
                self.logger().error(
                    f"[BATCH ORDER] Error building order params for {in_flight_order.client_order_id}: {e}",
                    exc_info=True
                )

        if not batch_orders:
            self.logger().error("[BATCH ORDER] Failed to build batch order params")
            return [("", self.current_timestamp) for _ in orders_to_create]

        # Step 3: Make batch API call
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        url = web_utils.public_rest_url(
            CONSTANTS.BATCH_CREATE_ORDER_URL,
            domain=self._domain
        )

        request_data = {"orders": batch_orders}

        self.logger().info(f"[BATCH ORDER] Submitting batch of {len(batch_orders)} orders")
        self.logger().debug(f"[BATCH ORDER DEBUG] throttler_limit_id={CONSTANTS.BATCH_CREATE_ORDER_LIMIT_ID}, throttler={self._throttler}")

        try:
            response = await rest_assistant.execute_request(
                url=url,
                throttler_limit_id=CONSTANTS.BATCH_CREATE_ORDER_LIMIT_ID,
                method=RESTMethod.POST,
                data=request_data,
                is_auth_required=True,
            )

            if not response.get("success", False):
                self.logger().error(f"[BATCH ORDER] Batch order creation failed: {response}")
                # Mark all orders as failed
                for in_flight_order in inflight_orders_to_create:
                    self._on_order_creation_failure(
                        order_id=in_flight_order.client_order_id,
                        trading_pair=in_flight_order.trading_pair,
                        amount=in_flight_order.amount,
                        trade_type=in_flight_order.trade_type,
                        order_type=in_flight_order.order_type,
                        price=in_flight_order.price,
                        exception=IOError(f"Batch order creation failed: {response}"),
                        position_action=in_flight_order.position,
                    )
                raise IOError(f"Batch order creation failed: {response}")

            # Step 4: Process results through order tracker
            data = response.get("data", {})
            rows = data.get("rows", [])
            timestamp = self.current_timestamp

            # Map results by client_order_id
            result_map = {row.get("client_order_id"): row for row in rows}

            # Process each in-flight order
            for in_flight_order in inflight_orders_to_create:
                order_result = result_map.get(in_flight_order.client_order_id)

                if order_result:
                    error_message = order_result.get("error_message", "")

                    # Check if order succeeded
                    if not error_message or error_message.lower() in ["none", "", "null"]:
                        exchange_order_id = str(order_result.get("order_id", ""))
                        self._update_order_after_creation_success(
                            exchange_order_id=exchange_order_id,
                            order=in_flight_order,
                            update_timestamp=timestamp,
                        )
                        self.logger().debug(
                            f"[BATCH ORDER] Order {in_flight_order.client_order_id} created successfully "
                            f"with exchange_order_id {exchange_order_id}"
                        )
                    else:
                        # Order failed on exchange
                        self._on_order_creation_failure(
                            order_id=in_flight_order.client_order_id,
                            trading_pair=in_flight_order.trading_pair,
                            amount=in_flight_order.amount,
                            trade_type=in_flight_order.trade_type,
                            order_type=in_flight_order.order_type,
                            price=in_flight_order.price,
                            exception=IOError(f"Exchange error: {error_message}"),
                            position_action=in_flight_order.position,
                        )
                        self.logger().error(
                            f"[BATCH ORDER] Order {in_flight_order.client_order_id} failed: {error_message}"
                        )
                else:
                    # Order not found in response
                    self._on_order_creation_failure(
                        order_id=in_flight_order.client_order_id,
                        trading_pair=in_flight_order.trading_pair,
                        amount=in_flight_order.amount,
                        trade_type=in_flight_order.trade_type,
                        order_type=in_flight_order.order_type,
                        price=in_flight_order.price,
                        exception=IOError("Order not found in response"),
                        position_action=in_flight_order.position,
                    )
                    self.logger().error(
                        f"[BATCH ORDER] Order {in_flight_order.client_order_id} not found in response"
                    )

            # Build return results (maintain order with original indices)
            results = []
            for i in range(len(orders_to_create)):
                order_id = order_id_map.get(i)

                if order_id is None:
                    # Order failed validation
                    results.append(("", timestamp))
                else:
                    # Look up result
                    order_result = result_map.get(order_id)
                    if order_result:
                        error_message = order_result.get("error_message", "")
                        if not error_message or error_message.lower() in ["none", "", "null"]:
                            exchange_order_id = str(order_result.get("order_id", ""))
                            results.append((exchange_order_id, timestamp))
                        else:
                            results.append(("", timestamp))
                    else:
                        results.append(("", timestamp))

            success_count = sum(1 for exchange_id, _ in results if exchange_id)
            self.logger().info(
                f"[BATCH ORDER] Batch completed: {success_count}/{len(orders_to_create)} orders successful"
            )

            return results

        except IOError:
            # Re-raise IOError (API request failed)
            raise
        except Exception as e:
            self.logger().error(f"[BATCH ORDER] Unexpected error in batch order creation: {e}", exc_info=True)
            # Mark all in-flight orders as failed
            for in_flight_order in inflight_orders_to_create:
                self._on_order_creation_failure(
                    order_id=in_flight_order.client_order_id,
                    trading_pair=in_flight_order.trading_pair,
                    amount=in_flight_order.amount,
                    trade_type=in_flight_order.trade_type,
                    order_type=in_flight_order.order_type,
                    price=in_flight_order.price,
                    exception=e,
                    position_action=in_flight_order.position,
                )
            raise IOError(f"Batch order creation failed: {e}")

    async def cancel_all(self, timeout_seconds: float = 10.0) -> List[CancellationResult]:
        """
        Override ExchangePyBase.cancel_all to use Orderly's CANCEL_ALL-per-symbol endpoint.

        Semantics:
        - All-or-nothing per symbol:
          * cancel_all_symbol(trading_pair) is responsible for:
              - Calling the exchange CANCEL_ALL endpoint
              - Marking all non-done local orders for that trading_pair as CANCELED
          * Here we only aggregate per-order CancellationResult based on the
            per-symbol success flags.
        """
        incomplete_orders = [o for o in self.in_flight_orders.values() if not o.is_done]

        if not incomplete_orders:
            self.logger().info("[CANCEL_ALL] No in-flight orders to cancel.")
            return []

        trading_pairs = sorted({o.trading_pair for o in incomplete_orders})
        self.logger().info(
            f"[CANCEL_ALL] Cancelling all orders across {len(trading_pairs)} trading pair(s): {trading_pairs}"
        )

        # Call CANCEL_ALL per symbol, with timeout
        try:
            async with timeout(timeout_seconds):
                # cancel_all_symbol returns bool for each trading_pair
                raw_results = await safe_gather(
                    *[self.cancel_all_symbol(tp) for tp in trading_pairs],
                    return_exceptions=False,
                )
        except Exception:
            # Only fires if the *whole* operation (timeout or something global) fails
            self.logger().network(
                "[CANCEL_ALL] Unexpected error while cancelling all orders.",
                exc_info=True,
                app_warning_msg="Failed to cancel all orders. Check API key and network connection.",
            )
            return [CancellationResult(o.client_order_id, False) for o in incomplete_orders]

        # Map trading_pair -> success flag (True/False)
        symbol_success: Dict[str, bool] = {
            tp: bool(res) for tp, res in zip(trading_pairs, raw_results)
        }

        # Build per-order results; local state is already handled in cancel_all_symbol
        results: List[CancellationResult] = []
        for o in incomplete_orders:
            success_for_symbol = symbol_success.get(o.trading_pair, False)
            results.append(CancellationResult(o.client_order_id, success_for_symbol))

        return results

    async def cancel_all_symbol(self, trading_pair: str) -> bool:
        """
        Cancel all orders for a single trading pair (symbol) via Orderly's CANCEL_ALL endpoint.

        Side effects on success:
        - All locally tracked, non-done InFlightOrders for this trading_pair
          are marked CANCELED via OrderUpdate.

        Returns:
            True  -> we consider CANCEL_ALL successful for this symbol
            False -> request failed or response was not the expected "success" shape
        """
        try:
            symbol = await self.exchange_symbol_associated_to_pair(trading_pair)
            url = web_utils.public_rest_url(
                CONSTANTS.CANCEL_ALL_ORDERS_URL,
                domain=self._domain,
            )
            params = {"symbol": symbol}

            rest_assistant = await self._web_assistants_factory.get_rest_assistant()
            self.logger().info(f"[CANCEL_ALL] Cancelling all orders for {symbol}")
            resp = await rest_assistant.execute_request(
                url=url,
                throttler_limit_id=CONSTANTS.TRADING_LIMIT_ID,
                method=RESTMethod.DELETE,
                params=params,
                is_auth_required=True,
            )

            success_flag = resp.get("success", True)
            data = resp.get("data") or {}
            status = data.get("status")

            if not success_flag or status != "CANCEL_ALL_SENT":
                self.logger().warning(f"[CANCEL_ALL] Unexpected response for {symbol}: {resp}")
                return False

            # ------- Local state update on success (per symbol) -------
            timestamp = self.current_timestamp

            # Collect all active (non-done) in-flight orders for this trading_pair
            affected_orders = [
                o for o in self.in_flight_orders.values()
                if o.trading_pair == trading_pair and not o.is_done
            ]

            if not affected_orders:
                self.logger().info(
                    f"[CANCEL_ALL] No non-done in-flight orders to update locally for {trading_pair}"
                )
                return True

            self.logger().info(
                f"[CANCEL_ALL] Marking {len(affected_orders)} local in-flight order(s) "
                f"as CANCELED for {trading_pair}"
            )

            for order in affected_orders:
                order_update = OrderUpdate(
                    client_order_id=order.client_order_id,
                    exchange_order_id=order.exchange_order_id,
                    trading_pair=order.trading_pair,
                    update_timestamp=timestamp,
                    new_state=OrderState.CANCELED,
                )
                self._order_tracker.process_order_update(order_update)

            return True

        except Exception as e:
            # Swallow the error, just mark symbol-level failure
            self.logger().warning(
                f"[CANCEL_ALL] Error cancelling all orders for {trading_pair}: {e}",
                exc_info=True,
            )
            return False

    async def batch_order_cancel(self, orders_to_cancel: List[InFlightOrder]) -> bool:
        """
        Cancel multiple orders in batches of up to 10 client_order_ids.

        Semantics:
        - Each batch is cancelled via cancel_batch(), which:
            * Calls the batch cancel endpoint
            * Marks its own local orders as CANCELED on success
            * Returns a bool flag for that batch
        - This method:
            * Runs all cancel_batch calls concurrently via safe_gather
            * Aggregates the per-batch boolean results
            * Returns:
                True  -> all batches succeeded and no wrong symbols
                False -> at least one batch failed or any wrong symbols present

        Partial success:
        - Some batches may succeed and some may fail.
        - cancel_batch updates local state only for its successful batch.
        - This method will still return False if any batch fails.
        """
        if not orders_to_cancel:
            self.logger().info("[BATCH CANCEL] No in-flight orders to cancel.")
            return True

        # Filter out any orders that are not under connector trading symbols
        filtered_orders_to_cancel = [
            order for order in orders_to_cancel
            if order.trading_pair in self._trading_pairs
        ]

        if not filtered_orders_to_cancel:
            self.logger().warning("[BATCH CANCEL] No valid orders to cancel after filtering.")
            return False

        # Batch the orders into groups of 10 (API limit)
        batch_size = 10
        total_orders = len(filtered_orders_to_cancel)
        batches: List[List[InFlightOrder]] = [
            filtered_orders_to_cancel[i:i + batch_size]
            for i in range(0, total_orders, batch_size)
        ]

        self.logger().info(
            f"[BATCH CANCEL] Cancelling {total_orders} order(s) in {len(batches)} batch(es)."
        )


        try:
            # cancel_batch returns bool for each batch
            raw_results = await safe_gather(
                *[self.cancel_batch(batch, i + 1) for i, batch in enumerate(batches)],
                return_exceptions=False,
            )
        except Exception:
            # Only fires if something global fails (not per-batch)
            self.logger().network(
                "[BATCH CANCEL] Unexpected error while cancelling batched orders.",
                exc_info=True,
                app_warning_msg="Failed to cancel batched orders. Check API key and network connection.",
            )
            return False

        # raw_results is List[bool]
        all_success = all(bool(res) for res in raw_results)

        if not all_success:
            self.logger().warning(
                "[BATCH CANCEL] One or more batch cancellations failed or returned an "
                "unexpected response. Some orders may remain active."
            )

        wrong_symbol = len(filtered_orders_to_cancel) < len(orders_to_cancel)
        if wrong_symbol:
            invalid_symbols = sorted({
                o.trading_pair for o in orders_to_cancel
                if o.trading_pair not in self._trading_pairs
            })
            self.logger().warning(
                f"[BATCH CANCEL] One or more cancellations sent with wrong symbol(s): {invalid_symbols}"
            )

        return all_success and not wrong_symbol

    # Create all batch cancel tasks
    async def cancel_batch(self, batch: List[InFlightOrder], batch_num: int) -> bool:
        if not batch:
            self.logger().info(f"[BATCH CANCEL] Batch {batch_num}: no orders provided.")
            return True

        client_order_ids = [o.client_order_id for o in batch]
        params = {"client_order_ids": ",".join(client_order_ids)}

        self.logger().debug(
            f"[BATCH CANCEL] Processing batch {batch_num}: {len(batch)} order(s)"
        )

        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        url = web_utils.public_rest_url(
            CONSTANTS.BATCH_CANCEL_ORDER_BY_CLIENT_ID_URL,
            domain=self._domain,
        )

        try:
            resp = await rest_assistant.execute_request(
                url=url,
                throttler_limit_id=CONSTANTS.BATCH_CANCEL_ORDER_BY_CLIENT_ID_LIMIT_ID,
                method=RESTMethod.DELETE,
                params=params,
                is_auth_required=True,
            )
        except Exception as e:
            self.logger().warning(
                f"[BATCH CANCEL] Batch {batch_num} - error while cancelling orders: {e}",
                exc_info=True,
            )
            return False

        success_flag = resp.get("success", True)
        data = resp.get("data") or {}
        status = data.get("status")

        if not success_flag or not (status == "CANCEL_SENT" or status == "CANCEL_ALL_SENT"):
            self.logger().warning(
                f"[BATCH CANCEL] Batch {batch_num} - unexpected response: {resp}"
            )
            return False

        # ---- Local state update ----
        for order in batch:
            if order.is_done:
                continue
            order_update = OrderUpdate(
                client_order_id=order.client_order_id,
                exchange_order_id=order.exchange_order_id,
                trading_pair=order.trading_pair,
                update_timestamp=self.current_timestamp,
                new_state=OrderState.CANCELED,
            )
            self._order_tracker.process_order_update(order_update)

        return True

    async def bulk_edit_order(self, orders: List[InFlightOrder], new_prices: List[Decimal], new_sizes: List[Optional[Decimal]]) -> bool:
        """
        Edit multiple orders concurrently.
        
        Args:
            orders: List of InFlightOrder objects to edit
            new_prices: List of new prices (must match orders length)
            new_sizes: List of new sizes (must match orders length, can contain None for optional sizes)
        
        Returns:
            True if all edits succeeded, False if any failed
        """
        if len(orders) != len(new_prices) or len(orders) != len(new_sizes):
            self.logger().error(
                f"[BULK EDIT] Length mismatch: orders={len(orders)}, prices={len(new_prices)}, sizes={len(new_sizes)}"
            )
            return False

        # Create list of edit coroutines
        edit_tasks = [
            self.edit_order(order, new_price, new_size)
            for order, new_price, new_size in zip(orders, new_prices, new_sizes)
        ]
        
        # Execute all edits concurrently
        try:
            results = await asyncio.gather(*edit_tasks, return_exceptions=True)
            
            # Check if any failed
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.logger().error(
                        f"[BULK EDIT] Order {orders[i].client_order_id} failed with exception: {result}",
                        exc_info=True
                    )
                    return False
                elif result is False:
                    self.logger().warning(
                        f"[BULK EDIT] Order {orders[i].client_order_id} edit failed"
                    )
                    return False
            
            self.logger().info(f"[BULK EDIT] Successfully edited {len(orders)} orders")
            return True
            
        except Exception as e:
            self.logger().error(f"[BULK EDIT] Unexpected error: {e}", exc_info=True)
            return False
    
    async def edit_order(self, order: InFlightOrder, new_price: Decimal, new_size: Optional[Decimal] = None) -> bool:
        """
        Edit an existing order on the exchange.
        
        According to Orderly API:
        - PUT /v1/order
        - Only order_price or order_quantity can be amended
        - Requires: order_id, symbol, order_type, side
        
        Args:
            order: InFlightOrder to edit
            new_price: New price for the order
            new_size: Optional new size for the order (if None, only price is updated)
        
        Returns:
            True if edit succeeded, False otherwise
        """
        try:
            # Get exchange order ID
            exchange_order_id = await order.get_exchange_order_id()
            if not exchange_order_id:
                self.logger().error(
                    f"[EDIT ORDER] Order {order.client_order_id} has no exchange_order_id"
                )
                return False
            
            # Get exchange symbol
            symbol = await self.exchange_symbol_associated_to_pair(order.trading_pair)
            
            orderly_order_type = "MARKET"
            if order.order_type == OrderType.LIMIT:
                orderly_order_type = "LIMIT"
            elif order.order_type == OrderType.LIMIT_MAKER:
                orderly_order_type = "POST_ONLY"
            
            # Map trade type to side
            side = "BUY" if order.trade_type == TradeType.BUY else "SELL"
            
            # Build request payload
            order_params = {
                "order_id": str(exchange_order_id),
                "symbol": symbol,
                "order_type": orderly_order_type,
                "side": side,
                "client_order_id": order.client_order_id,
            }
            
            if (self._order_tag):
                order_params["order_tag"] = self._order_tag
            # Add price if provided (quantize it)
            if new_price is not None and not new_price.is_nan():
                order_params["order_price"] = float(
                    self.quantize_order_price(order.trading_pair, new_price)
                )
            
            # Add quantity if provided (quantize it)
            if new_size is not None and not new_size.is_nan():
                order_params["order_quantity"] = float(
                    self.quantize_order_amount(order.trading_pair, new_size)
                )
            else: # use existing size 
                order_params["order_quantity"] = float(order.amount)
            
            # Validate that at least one of price or quantity is provided
            if "order_price" not in order_params and "order_quantity" not in order_params:
                self.logger().error(
                    f"[EDIT ORDER] Must provide either new_price or new_size for order {order.client_order_id}"
                )
                return False
            
            # Make API call
            rest_assistant = await self._web_assistants_factory.get_rest_assistant()
            url = web_utils.public_rest_url(
                CONSTANTS.EDIT_ORDER_URL,
                domain=self._domain
            )
            
            response = await rest_assistant.execute_request(
                url=url,
                throttler_limit_id=CONSTANTS.EDIT_ORDER_LIMIT_ID,
                method=RESTMethod.PUT,
                data=order_params,
                is_auth_required=True,
            )
            
         
            if not response.get("success", False):
                self.logger().error(
                    f"[EDIT ORDER] Failed to edit order {order.client_order_id}"
                )
                return False
            
            # Check response status
            data = response.get("data", {})
            status = data.get("status", "")
            
            if status == "EDIT_SENT":
                self.logger().info(
                    f"[EDIT ORDER] Successfully edited order {order.client_order_id}"
                )
                return True
            else:
                self.logger().warning(
                    f"[EDIT ORDER] Unexpected status '{status}' for order {order.client_order_id}: {response}"
                )
                return False
                
        except asyncio.TimeoutError:
            self.logger().error(
                f"[EDIT ORDER] Timeout waiting for exchange_order_id for order {order.client_order_id}"
            )
            return False
        except Exception as e:
            self.logger().error(
                f"[EDIT ORDER] Error editing order {order.client_order_id}: {e}",
                exc_info=True
            )
            return False
        

    async def _request_order_status(self, tracked_order: InFlightOrder) -> OrderUpdate:
        """
        Request order status from exchange.

        Args:
            tracked_order: Order to check status for

        Returns:
            OrderUpdate with current status
        """
        exchange_order_id = tracked_order.exchange_order_id

        if not exchange_order_id:
            return OrderUpdate(
                trading_pair=tracked_order.trading_pair,
                update_timestamp=self.current_timestamp,
                new_state=OrderState.FAILED,
                client_order_id=tracked_order.client_order_id,
            )
        
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        url = web_utils.public_rest_url(
            CONSTANTS.GET_ORDER_URL.format(order_id=exchange_order_id),
            domain=self._domain
        )
        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.GET_ORDER_URL,
            method=RESTMethod.GET,
            is_auth_required=True,
        )

        if not response.get("success", False):
            raise IOError(f"Failed to fetch order status: {response}")

        data = response.get("data", {})
        order_state = CONSTANTS.ORDER_STATE.get(data.get("status"), OrderState.OPEN)

        return OrderUpdate(
            trading_pair=tracked_order.trading_pair,
            update_timestamp=data.get("updated_time", self.current_timestamp) * 1e-3,
            new_state=order_state,
            client_order_id=tracked_order.client_order_id,
            exchange_order_id=exchange_order_id,
        )

    async def _all_trade_updates_for_order(self, order: InFlightOrder) -> List[TradeUpdate]:
        """
        Fetches all trade updates for a specific order from Orderly.

        Uses the GET /v1/order/{order_id}/trades endpoint to fetch all fills for an order.

        Args:
            order: The InFlightOrder to fetch trades for

        Returns:
            List of TradeUpdate objects representing all fills for this order
        """
        trade_updates = []

        try:
            exchange_order_id = await order.get_exchange_order_id()

            rest_assistant = await self._web_assistants_factory.get_rest_assistant()
            url = web_utils.public_rest_url(
                CONSTANTS.GET_ORDER_TRADES_URL.format(order_id=str(exchange_order_id)),
                domain=self._domain
            )
            response = await rest_assistant.execute_request(
                url=url,
                throttler_limit_id=CONSTANTS.GET_ORDER_TRADES_URL,
                method=RESTMethod.GET,
                is_auth_required=True
            )

            if not response.get("success", False):
                self.logger().warning(f"Failed to fetch trades for order {order.client_order_id}: {response}")
                return trade_updates

            data = response.get("data", {})
            rows = data.get("rows", [])

            for trade in rows:
                # Determine position action (OPEN or CLOSE)
                # Orderly returns side as "BUY" or "SELL" for the trade
                position_action = PositionAction.OPEN  # Default

                # Parse fee information
                fee_asset = trade.get("fee_asset", order.quote_asset)
                fee_amount = Decimal(str(trade.get("fee", "0")))

                fee = TradeFeeBase.new_perpetual_fee(
                    fee_schema=self.trade_fee_schema(),
                    position_action=position_action,
                    percent_token=fee_asset,
                    flat_fees=[TokenAmount(amount=fee_amount, token=fee_asset)] if fee_amount > 0 else []
                )

                # Create TradeUpdate
                trade_update = TradeUpdate(
                    trade_id=str(trade.get("id")),
                    client_order_id=order.client_order_id,
                    exchange_order_id=str(trade.get("order_id")),
                    trading_pair=order.trading_pair,
                    fill_timestamp=int(trade.get("executed_timestamp", 0) * 1e-3),
                    fill_price=Decimal(str(trade.get("executed_price", "0"))),
                    fill_base_amount=Decimal(str(trade.get("executed_quantity", "0"))),
                    fill_quote_amount=Decimal(str(trade.get("executed_price", "0"))) * Decimal(str(trade.get("executed_quantity", "0"))),
                    fee=fee,
                )

                trade_updates.append(trade_update)

        except asyncio.TimeoutError:
            raise IOError(f"Skipped order trade updates for {order.client_order_id} - waiting for exchange order id.")
        except Exception as e:
            self.logger().warning(f"Failed to fetch trade updates for order {order.client_order_id}: {e}")

        return trade_updates

    # ============================================================
    # Position Management
    # ============================================================
    async def _update_positions(self):
        """Fetch and update positions"""
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        if len(self._trading_pairs) == 1:
            # Convert trading pair to exchange symbol format
            symbol = await self.exchange_symbol_associated_to_pair(self._trading_pairs[0])
            url = web_utils.public_rest_url(
                CONSTANTS.POSITION_URL.format(symbol=symbol),
                domain=self._domain
            )
        else:
            url = web_utils.public_rest_url(
                CONSTANTS.POSITIONS_URL,
                domain=self._domain
            )
        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.POSITIONS_URL,
            method=RESTMethod.GET,
            is_auth_required=True,
        )

        if not response.get("success", False):
            self.logger().error(f"Failed to fetch positions: {response}")
            return

        positions_data = response.get("data", {}).get("rows", [])

        for position_data in positions_data:
            try:
                symbol = position_data["symbol"]
                trading_pair = await self.trading_pair_associated_to_exchange_symbol(symbol)
                if trading_pair not in self._trading_pairs:
                    self.logger().debug(f"Skipping position {symbol} -> {trading_pair} not in configured trading pairs {self._trading_pairs}")
                    continue

                position_qty = Decimal(str(position_data.get("position_qty", "0")))

                # Determine position side before checking for zero (needed for pos_key)
                position_side = PositionSide.LONG if position_qty > 0 else PositionSide.SHORT
                pos_key = self._perpetual_trading.position_key(trading_pair, position_side)

                if position_qty == 0:
                    # Remove position if it exists
                    self._perpetual_trading.remove_position(pos_key)
                    continue

                # unrealized_pnl = Decimal(str(position_data.get("unsettled_pnl", "0")))
                entry_price = Decimal(str(position_data.get("average_open_price", "0")))
                leverage = Decimal(str(position_data.get("leverage", "1")))

                position = self._perpetual_trading.get_position(trading_pair, position_side)
                if position is not None:
                    position.update_position(
                        position_side=position_side,
                        unrealized_pnl=Decimal("0"),
                        entry_price=entry_price,
                        amount=abs(position_qty),
                    )
                else:
                    _position = Position(
                        trading_pair=trading_pair,
                        position_side=position_side,
                        unrealized_pnl=Decimal("0"),
                        entry_price=entry_price,
                        amount=abs(position_qty),
                        leverage=leverage,
                    )
                    self._perpetual_trading.set_leverage(trading_pair, int(leverage))
                    self._perpetual_trading.set_position(pos_key, _position)

            except Exception:
                self.logger().exception(f"Error updating position: {position_data}")

    async def _set_trading_pair_leverage(self, trading_pair: str, leverage: int) -> Tuple[bool, str]:
        """
        Set leverage for a trading pair.

        Args:
            trading_pair: Trading pair
            leverage: Leverage value

        Returns:
            Tuple of (success, message)
        """
        try:
            symbol = await self.exchange_symbol_associated_to_pair(trading_pair)

            data = {
                "symbol": symbol,
                "leverage": leverage,
            }

            rest_assistant = await self._web_assistants_factory.get_rest_assistant()
            url = web_utils.public_rest_url(
                CONSTANTS.SET_LEVERAGE_URL,
                domain=self._domain
            )
            response = await rest_assistant.execute_request(
                url=url,
                throttler_limit_id=CONSTANTS.SET_LEVERAGE_URL,
                method=RESTMethod.POST,
                data=data,
                is_auth_required=True
            )

            if response.get("success", False):
                return True, f"Leverage set to {leverage}x for {trading_pair}"
            else:
                error_msg = response.get("message", "Unknown error")
                return False, f"Failed to set leverage: {error_msg}"

        except Exception as e:
            return False, f"Error setting leverage: {str(e)}"

    async def _get_position_mode(self) -> Optional[PositionMode]:
        """
        Get current position mode.

        Orderly only supports ONEWAY position mode (single position per symbol).

        Returns:
            PositionMode.ONEWAY - Orderly only supports one-way mode
        """
        return PositionMode.ONEWAY

    async def _trading_pair_position_mode_set(self, mode: PositionMode, trading_pair: str) -> Tuple[bool, str]:
        """
        Set position mode (Orderly only supports ONEWAY).

        Args:
            mode: Position mode
            trading_pair: Trading pair

        Returns:
            Tuple of (success, message)
        """
        if mode != PositionMode.ONEWAY:
            return False, "Orderly only supports ONEWAY position mode"
        return True, "Position mode is ONEWAY"

    # ============================================================
    # Balance Management
    # ============================================================

    async def _update_balances(self):
        """Fetch and update account balances"""
        rest_assistant = await self._web_assistants_factory.get_rest_assistant()
        url = web_utils.public_rest_url(
            CONSTANTS.ACCOUNT_HOLDING_URL,
            domain=self._domain
        )
        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.ACCOUNT_HOLDING_URL,
            method=RESTMethod.GET,
            is_auth_required=True,
        )
        data = response.get("data", {})
        holdings = data.get("holding", [])

        self._account_balances.clear()
        self._account_available_balances.clear()

        for holding in holdings:
            token = holding.get("token")
            total = Decimal(str(holding.get("holding", "0")))
            frozen = Decimal(str(holding.get("frozen", "0")))
            available = total - frozen

            self._account_balances[token] = total
            self._account_available_balances[token] = available

    # ============================================================
    # Funding
    # ============================================================

    async def _fetch_last_fee_payment(self, trading_pair: str) -> Tuple[int, Decimal, Decimal]:
        """
        Fetch last funding payment.

        Args:
            trading_pair: Trading pair

        Returns:
            Tuple of (timestamp, funding_rate, payment_amount)
        """
        try:
            symbol = await self.exchange_symbol_associated_to_pair(trading_pair)
            rest_assistant = await self._web_assistants_factory.get_rest_assistant()
            url = web_utils.public_rest_url(
                CONSTANTS.FUNDING_FEE_HISTORY_URL,
                domain=self._domain
            )
            response = await rest_assistant.execute_request(
                url=url,
                throttler_limit_id=CONSTANTS.FUNDING_FEE_HISTORY_URL,
                method=RESTMethod.GET,
                params={"symbol": symbol, "size": "1"},
                is_auth_required=True,
            )

            if not response.get("success", False):
                return 0, Decimal("-1"), Decimal("-1")

            data = response.get("data", {})
            rows = data.get("rows", [])

            if not rows:
                return 0, Decimal("-1"), Decimal("-1")

            last_payment = rows[0]
            timestamp = int(last_payment.get("timestamp", 0) * 1e-3)
            funding_rate = Decimal(str(last_payment.get("funding_rate", "0")))
            payment = Decimal(str(last_payment.get("funding_fee", "0")))

            return timestamp, funding_rate, payment

        except Exception:
            self.logger().exception(f"Error fetching funding payment for {trading_pair}")
            return 0, Decimal("-1"), Decimal("-1")

    # ============================================================
    # Fees
    # ============================================================

    async def _update_trading_fees(self):
        """
        Update fees information from the exchange.

        Note: Orderly provides fee information in the account info endpoint,
        but fees are already handled per-trade. This method is stubbed as
        fees are retrieved with each trade/order response.
        """
        pass

    def _get_fee(
        self,
        base_currency: str,
        quote_currency: str,
        order_type: OrderType,
        order_side: TradeType,
        position_action: PositionAction,
        amount: Decimal,
        price: Decimal = Decimal("NaN"),
        is_maker: Optional[bool] = None,
    ) -> TradeFeeBase:
        """
        Calculate trading fees.

        Args:
            base_currency: Base currency
            quote_currency: Quote currency
            order_type: Order type
            order_side: Trade side
            amount: Order amount
            price: Order price
            is_maker: Whether order is maker

        Returns:
            TradeFeeBase object
        """
        is_maker = is_maker or False
        return build_trade_fee(
            exchange=self.name,
            is_maker=is_maker,
            base_currency=base_currency,
            quote_currency=quote_currency,
            order_type=order_type,
            order_side=order_side,
            amount=amount,
            price=price,
        )

    # ============================================================
    # User Stream Event Handling
    # ============================================================

    async def _user_stream_event_listener(self):
        """
        Listen to user stream messages and process them.

        Handles order updates, trade updates, position updates, and balance updates.
        """
        async for event_message in self._iter_user_event_queue():
            try:
                topic = event_message.get("topic")

                if topic == CONSTANTS.WS_EXECUTION_REPORT_CHANNEL:
                    await self._process_order_event(event_message)
                elif topic == CONSTANTS.WS_POSITION_CHANNEL:
                    await self._process_position_event(event_message)
                elif topic == CONSTANTS.WS_BALANCE_CHANNEL:
                    await self._process_balance_event(event_message)

            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger().error("Unexpected error in user stream listener", exc_info=True)

    async def _process_order_event(self, event: Dict[str, Any]):
        """
        Process order update event from WebSocket.

        This method handles both order status updates and trade fills.
        When executedQuantity > 0, it creates a TradeUpdate to record the fill.
        """
        data = event.get("data", {})

        # Get client_order_id - Orderly uses camelCase in websocket
        client_order_id = data.get("clientOrderId")
        symbol = data.get("symbol", "UNKNOWN")
        
        self.logger().debug(
            f"[WS ORDER EVENT] Received order event: clientOrderId={client_order_id}, "
            f"symbol={symbol}, status={data.get('status', 'UNKNOWN')}, order_type={data.get('type', 'UNKNOWN')}"
        )
        
        if not client_order_id:
            self.logger().debug(
                f"[WS ORDER EVENT] Skipping order event - no clientOrderId in data: {data}"
            )
            return

        tracked_order = self._order_tracker.all_updatable_orders.get(client_order_id)
        if not tracked_order:
            # This is expected for orders from other bot instances or orders not tracked by this instance
            self.logger().debug(
                f"[WS ORDER EVENT] Skipping order {client_order_id} (symbol: {symbol}) - "
                f"not found in order tracker (not from this bot instance)"
            )
            return
        
        # Verify the order belongs to a trading pair we're tracking
        if tracked_order.trading_pair not in self._trading_pairs:
            self.logger().warning(
                f"[WS ORDER EVENT] Order {client_order_id} has trading pair {tracked_order.trading_pair} "
                f"not in configured pairs {self._trading_pairs}"
            )
            return
        
        self.logger().info(
            f"[WS ORDER EVENT] Processing order {client_order_id} with pair {tracked_order.trading_pair}, "
            f"status={data.get('status', 'UNKNOWN')}"
        )
        
        # Process trade fill if executedQuantity > 0
        executed_quantity = Decimal(str(data.get("executedQuantity", "0")))
        if executed_quantity > Decimal("0"):
            # This order update contains a fill - create TradeUpdate
            executed_price = Decimal(str(data.get("executedPrice", "0")))
            fee_amount = Decimal(str(data.get("fee", "0")))
            fee_asset = data.get("feeAsset", tracked_order.quote_asset)
            is_maker = data.get("maker", False)

            # Build fee object
            fee = TradeFeeBase.new_perpetual_fee(
                fee_schema=self.trade_fee_schema(),
                position_action=PositionAction.OPEN,  # Default, actual action determined by order
                percent_token=fee_asset,
                flat_fees=[TokenAmount(amount=fee_amount, token=fee_asset)] if fee_amount > 0 else []
            )

            # Use match_id as trade_id (unique identifier for this fill)
            # If match_id is not available, fall back to a combination of orderId and timestamp
            trade_id = str(data.get("match_id", data.get("tradeId", "0")))
            
            # get current trade from connector if it is long, trade update will have positionaction as close, otherwise as open

            trade_update = TradeUpdate(
                trade_id=trade_id,
                client_order_id=client_order_id,
                exchange_order_id=str(data.get("orderId", "")),
                trading_pair=tracked_order.trading_pair,
                fill_timestamp=int(data.get("timestamp", self.current_timestamp * 1000) * 1e-3),
                fill_price=executed_price,
                fill_base_amount=executed_quantity,
                fill_quote_amount=executed_price * executed_quantity,
                fee=fee,
                is_taker=not is_maker,
            )

            self._order_tracker.process_trade_update(trade_update)

        # Process order state update
        new_state = CONSTANTS.ORDER_STATE.get(data.get("status"), OrderState.OPEN)

        order_update = OrderUpdate(
            trading_pair=tracked_order.trading_pair,
            update_timestamp=data.get("timestamp", self.current_timestamp * 1000) * 1e-3,
            new_state=new_state,
            client_order_id=client_order_id,
            exchange_order_id=str(data.get("orderId", "")),
        )

        self._order_tracker.process_order_update(order_update)

    async def _process_position_event(self, event: Dict[str, Any]):
        """
        Process position update event from WebSocket.

        WebSocket event format:
        {
          "topic": "position",
          "ts": 1684926050966,
          "data": {
            "positions": [
              {
                "symbol": "PERP_ETH_USDC",
                "positionQty": 3.1408,
                "averageOpenPrice": 1804.51490427,
                "unsettledPnl": -2.79856,
                "leverage": 10,
                ...
              }
            ]
          }
        }

        Updates the internal position state based on WebSocket data.
        Only processes positions for trading pairs configured for this bot instance.
        """
        data = event.get("data", {})
        positions = data.get("positions", [])

        for position_data in positions:
            try:
                symbol = position_data.get("symbol")
                if not symbol:
                    self.logger().warning(f"[WS POSITION] Skipping position with no symbol: {position_data}")
                    continue

                trading_pair = await self.trading_pair_associated_to_exchange_symbol(symbol)
                
                # Filter: Only process positions for trading pairs configured for this bot instance
                if trading_pair not in self._trading_pairs:
                    self.logger().debug(
                        f"[WS POSITION] Skipping position update for {symbol} -> {trading_pair} "
                        f"(not in configured trading pairs: {self._trading_pairs})"
                    )
                    continue

                # WebSocket uses camelCase field names
                position_qty = Decimal(str(position_data.get("positionQty", "0")))

                self.logger().info(
                    f"[WS POSITION] Processing {symbol} -> {trading_pair}: "
                    f"positionQty={position_qty}"
                )

                # Determine position side before checking for zero (needed for pos_key)
                position_side = PositionSide.LONG if position_qty > 0 else PositionSide.SHORT
                pos_key = self._perpetual_trading.position_key(trading_pair, position_side)

                if position_qty == 0:
                    # Remove current side position as well (position fully closed)
                    self._perpetual_trading.remove_position(pos_key)
                    self.logger().info(
                        f"[WS POSITION] CLOSED - {trading_pair}: Removed zero position (both sides cleared)"
                    )

                    continue

                # Extract position details (camelCase from WebSocket)
                # unsettled_pnl = Decimal(str(position_data.get("unsettledPnl", "0")))
                unsettled_pnl = Decimal(0)
                entry_price = Decimal(str(position_data.get("averageOpenPrice", "0")))
                leverage = Decimal(str(position_data.get("leverage", "1")))

                # Get or create position
                position = self._perpetual_trading.get_position(trading_pair, position_side)
                if position is not None:
                    # Update existing position
                    position.update_position(
                        position_side=position_side,
                        unrealized_pnl=unsettled_pnl,
                        entry_price=entry_price,
                        amount=abs(position_qty),
                    )
                else:
                    # Create new position
                    _position = Position(
                        trading_pair=trading_pair,
                        position_side=position_side,
                        unrealized_pnl=unsettled_pnl,
                        entry_price=entry_price,
                        amount=abs(position_qty),
                        leverage=leverage,
                    )
                    self._perpetual_trading.set_leverage(trading_pair, int(leverage))
                    self._perpetual_trading.set_position(pos_key, _position)

            except Exception as e:
                self.logger().exception(
                    f"[WS POSITION] Error processing position update for {position_data.get('symbol', 'UNKNOWN')}: {e}"
                )


    async def _process_balance_event(self, event: Dict[str, Any]):
        """
        Process balance update event from WebSocket.

        WebSocket event format:
        {
          "topic": "balance",
          "ts": 1651836695254,
          "data": {
            "balances": {
              "USDC": {
                "holding": 5555815.47398272,
                "frozen": 0,
                "interest": 0,
                ...
              }
            }
          }
        }

        Replaces all balances with the data from the WebSocket event.
        """
        data = event.get("data", {})
        balances = data.get("balances", {})

        self.logger().info(f"[WS BALANCE] Processing balance update for {len(balances)} token(s)")

        # Replace all balances with the new data (as per user requirement)
        self._account_balances.clear()
        self._account_available_balances.clear()

        for token, balance_data in balances.items():
            try:
                total = Decimal(str(balance_data.get("holding", "0")))
                frozen = Decimal(str(balance_data.get("frozen", "0")))
                available = total - frozen

                self._account_balances[token] = total
                self._account_available_balances[token] = available

                self.logger().debug(
                    f"[WS BALANCE] {token}: total={total}, frozen={frozen}, available={available}"
                )

            except Exception as e:
                self.logger().exception(
                    f"[WS BALANCE] Error processing balance for {token}: {e}"
                )

    # ============================================================
    # Position History & Realized PnL
    # ============================================================

    async def _fetch_and_store_position_history(self, rest_assistant, url: str, params: Dict[str, Any]):
        """
        Helper method to fetch and store position history data.
        
        Args:
            rest_assistant: REST assistant instance
            url: API URL
            params: Query parameters (including symbol filter if specified)
        """
        try:
            response = await rest_assistant.execute_request(
                url=url,
                throttler_limit_id=CONSTANTS.POSITION_HISTORY_URL,
                method=RESTMethod.GET,
                params=params,
                is_auth_required=True,
            )
            
            if not response.get("success", False):
                self.logger().warning(f"Failed to fetch position history: {response}")
                return
                
            data = response.get("data", {})
            rows = data.get("rows", [])
            
            if not rows:
                self.logger().debug("No closed positions in history")
                return
                
            # Parse position data and create DataFrame with all API fields
            position_data = []
            total_realized_pnl = Decimal("0")
            
            for position in rows:
                # Extract all fields from API response
                position_id = position.get("position_id")
                status = position.get("status", "")
                position_type = position.get("type", "")
                symbol = position.get("symbol", "")
                side = position.get("side", "")
                avg_open_price = Decimal(str(position.get("avg_open_price", "0")))
                avg_close_price = Decimal(str(position.get("avg_close_price", "0")))
                max_position_qty = Decimal(str(position.get("max_position_qty", "0")))
                closed_position_qty = Decimal(str(position.get("closed_position_qty", "0")))
                realized_pnl = Decimal(str(position.get("realized_pnl", "0")))
                trading_fee = Decimal(str(position.get("trading_fee", "0")))
                funding_fee = Decimal(str(position.get("accumulated_funding_fee", "0")))
                insurance_fund_fee = Decimal(str(position.get("insurance_fund_fee", "0")))
                liquidator_fee = Decimal(str(position.get("liquidator_fee", "0")))
                liquidation_id = position.get("liquidation_id")
                leverage = Decimal(str(position.get("leverage", "0")))
                open_timestamp = position.get("open_timestamp", 0)
                close_timestamp = position.get("close_timestamp", 0)
                last_update_timestamp = position.get("last_update_timestamp", 0)
                
                total_realized_pnl += realized_pnl
                
                # Store all position data for DataFrame
                position_data.append({
                    "position_id": position_id,
                    "status": status,
                    "type": position_type,
                    "symbol": symbol,
                    "side": side,
                    "avg_open_price": float(avg_open_price),
                    "avg_close_price": float(avg_close_price),
                    "max_position_qty": float(max_position_qty),
                    "closed_position_qty": float(closed_position_qty),
                    "realized_pnl": float(realized_pnl),
                    "trading_fee": float(trading_fee),
                    "funding_fee": float(funding_fee),
                    "insurance_fund_fee": float(insurance_fund_fee),
                    "liquidator_fee": float(liquidator_fee),
                    "liquidation_id": liquidation_id,
                    "leverage": float(leverage),
                    "open_timestamp": open_timestamp,
                    "close_timestamp": close_timestamp,
                    "last_update_timestamp": last_update_timestamp,
                })
                
                self.logger().info(
                    f"[POSITION CLOSED] {symbol} {side}: "
                    f"Qty={closed_position_qty}, "
                    f"Entry={avg_open_price}, "
                    f"Exit={avg_close_price}, "
                    f"Realized PnL={realized_pnl} {CONSTANTS.CURRENCY}, "
                    f"Trading Fee={trading_fee}, "
                    f"Funding Fee={funding_fee}, "
                    f"Closed={close_timestamp}"
                )
            
            # Create DataFrame from new data
            new_df = pd.DataFrame(position_data)
            
            # Merge with existing data and deduplicate by position_id
            if self._position_history is not None and not self._position_history.empty:
                # Concatenate old and new data
                self._position_history = pd.concat([self._position_history, new_df], ignore_index=True)
                
                # Remove duplicates, keeping the most recent entry (last occurrence)
                # This handles cases where the same position is returned in multiple API calls
                self._position_history = self._position_history.drop_duplicates(
                    subset=['position_id'], 
                    keep='last'
                ).reset_index(drop=True)
                
                # Sort by close_timestamp descending (most recent first)
                self._position_history = self._position_history.sort_values(
                    by='close_timestamp', 
                    ascending=False
                ).reset_index(drop=True)
                
                self.logger().debug(
                    f"[POSITION HISTORY] Added {len(new_df)} positions, "
                    f"total unique positions: {len(self._position_history)}"
                )
            else:
                # First time initialization
                self._position_history = new_df.sort_values(
                    by='close_timestamp', 
                    ascending=False
                ).reset_index(drop=True)
            
            self.logger().info(
                f"[POSITION HISTORY] Total Realized PnL from {len(rows)} closed positions: "
                f"{total_realized_pnl} {CONSTANTS.CURRENCY}"
            )
            
        except Exception as e:
            self.logger().exception(f"Error in _fetch_and_store_position_history: {e}")

    # ============================================================
    # Status Polling
    # ============================================================

    async def _status_polling_loop_fetch_updates(self):
        """Fetch updates in status polling loop"""
        await safe_gather(
            self._update_order_status(),
            self._update_balances(),
            self._update_positions(),
        )
