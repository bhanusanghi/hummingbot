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
import json
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from bidict import bidict

import hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_constants as CONSTANTS
import hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_web_utils as web_utils
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_api_order_book_data_source import (
    OrderlyPerpetualAPIOrderBookDataSource,
)
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_auth import OrderlyPerpetualAuth
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_user_stream_data_source import (
    OrderlyPerpetualUserStreamDataSource,
)
from hummingbot.connector.perpetual_derivative_py_base import PerpetualDerivativePyBase
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.connector.utils import combine_to_hb_trading_pair, get_new_client_order_id
from hummingbot.core.api_throttler.data_types import RateLimit
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
        """
        self._orderly_perpetual_api_key = orderly_perpetual_api_key
        self._orderly_perpetual_api_secret = orderly_perpetual_api_secret
        self._orderly_perpetual_account_id = orderly_perpetual_account_id
        self._trading_required = trading_required
        self._trading_pairs = trading_pairs or []
        self._domain = domain
        self._position_mode = None
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
            self.logger().info(
                f"[AUTH DEBUG] Creating authenticator - "
                f"account_id={self._orderly_perpetual_account_id}, "
                f"api_key={self._orderly_perpetual_api_key[:20] if self._orderly_perpetual_api_key else None}..., "
                f"api_secret={'SET' if self._orderly_perpetual_api_secret else 'None'}, "
                f"trading_required={self._trading_required}"
            )
            return OrderlyPerpetualAuth(
                account_id=self._orderly_perpetual_account_id,
                orderly_key=self._orderly_perpetual_api_key,
                orderly_secret=self._orderly_perpetual_api_secret,
            )
        self.logger().info(
            f"[AUTH DEBUG] No API keys provided - account_id={self._orderly_perpetual_account_id}, "
            f"api_key={'SET' if self._orderly_perpetual_api_key else 'None'}, "
            f"api_secret={'SET' if self._orderly_perpetual_api_secret else 'None'}"
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
        self.logger().info(
            f"[AUTH DEBUG] Creating web assistants factory - "
            f"auth={self._auth}, "
            f"auth type={type(self._auth).__name__ if self._auth else 'None'}"
        )
        if self._auth:
            self.logger().info(
                f"[AUTH DEBUG] Auth object account_id={getattr(self._auth, '_account_id', 'MISSING')}"
            )
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

                # Log conversion result
                self.logger().debug(
                    f"[SYMBOL CONVERSION] Exchange symbol: {exchange_symbol} -> "
                    f"Hummingbot trading pair: {trading_pair}"
                )

                # Orderly uses unique symbols (PERP_BTC_USDC), no duplicates expected
                if trading_pair not in mapping.inverse:
                    mapping[exchange_symbol] = trading_pair
                    symbols_processed += 1
                else:
                    # Log warning if duplicate found (should not happen with Orderly)
                    self.logger().warning(
                        f"[SYMBOL CONVERSION] Duplicate trading pair found: {trading_pair} "
                        f"for {exchange_symbol} (existing: {mapping.inverse[trading_pair]})"
                    )

            except Exception:
                self.logger().exception(f"[SYMBOL CONVERSION] Error parsing symbol: {symbol_data}")

        self._set_trading_pair_symbol_map(mapping)
        
        # Log summary
        self.logger().info(
            f"[SYMBOL CONVERSION] Initialized symbol map: {symbols_processed} symbols processed, "
            f"{symbols_skipped} skipped, total mappings: {len(mapping)}"
        )
        
        # Log some example mappings
        if mapping:
            sample_mappings = list(mapping.items())[:5]
            self.logger().info(
                f"[SYMBOL CONVERSION] Sample mappings: {sample_mappings}"
            )

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
                self.logger().error(
                    f"[SYMBOL CONVERSION] Trading pair '{trading_pair}' not found in symbol map. "
                    f"Available pairs: {list(symbol_map.inverse.keys())[:10]}"
                )
                raise KeyError(f"Trading pair '{trading_pair}' not found in symbol map")
            
            orderly_symbol = symbol_map.inverse[trading_pair]
            self.logger().debug(
                f"[SYMBOL CONVERSION] Map lookup: Hummingbot '{trading_pair}' -> "
                f"Orderly '{orderly_symbol}'"
            )
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
        return (
            CONSTANTS.ORDER_NOT_EXIST_MESSAGE in str(cancelation_exception)
            or CONSTANTS.ORDER_ALREADY_CANCELLED_MESSAGE in str(cancelation_exception)
            or CONSTANTS.ORDER_ALREADY_FILLED_MESSAGE in str(cancelation_exception)
        )

    # ============================================================
    # Helper Methods
    # ============================================================

    async def _api_request(
        self,
        path: str,
        method: RESTMethod = RESTMethod.GET,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        is_auth_required: bool = False,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make an API request.

        Args:
            path: API endpoint path
            method: HTTP method
            params: Query parameters
            data: Request body data
            is_auth_required: Whether authentication is required

        Returns:
            API response
        """
        self.logger().info(
            f"[AUTH DEBUG] _api_request called - "
            f"path={path}, method={method.name}, "
            f"is_auth_required={is_auth_required}, "
            f"self._auth={self._auth}, "
            f"factory auth={getattr(self._web_assistants_factory, '_auth', 'MISSING')}"
        )
        url = web_utils.private_rest_url(path, self._domain) if is_auth_required else web_utils.public_rest_url(path, self._domain)

        rest_assistant = await self._web_assistants_factory.get_rest_assistant()

        # JSON encode data if it's a dict and method is POST/PUT
        # The auth module expects request.data to be a JSON string for POST/PUT requests
        encoded_data = None
        if data is not None:
            if method in (RESTMethod.POST, RESTMethod.PUT):
                # If data is already a string, use it as-is; otherwise JSON encode
                if isinstance(data, str):
                    encoded_data = data
                else:
                    encoded_data = json.dumps(data)
            else:
                encoded_data = data

        request = RESTRequest(
            method=method,
            url=url,
            params=params,
            data=encoded_data,
            is_auth_required=is_auth_required,
        )

        response = await rest_assistant.call(request=request)
        return await response.json()

    async def _get_last_traded_price(self, trading_pair: str) -> float:
        """
        Get last traded price for a trading pair.

        Args:
            trading_pair: Trading pair in Hummingbot format

        Returns:
            Last traded price
        """
        symbol = await self.exchange_symbol_associated_to_pair(trading_pair)
        self.logger().info(
            f"[SYMBOL CONVERSION] Converting trading pair: Hummingbot '{trading_pair}' -> "
            f"Orderly symbol '{symbol}'"
        )
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
        self.logger().debug(
            f"[SYMBOL CONVERSION] Order placement: Hummingbot '{trading_pair}' -> "
            f"Orderly symbol '{symbol}'"
        )

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

        # Make API call
        response = await self._api_request(
            path=CONSTANTS.CREATE_ORDER_URL,
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

        # IMPORTANT: Parameter order must match SDK: order_id, symbol
        # (Our auth uses sorted() but dict maintains insertion order)
        order_id_to_cancel = tracked_order.exchange_order_id or order_id
        params = {
            "order_id": str(order_id_to_cancel),
            "symbol": symbol,
        }

        response = await self._api_request(
            path=CONSTANTS.CANCEL_ORDER_URL,
            method=RESTMethod.DELETE,
            params=params,
            is_auth_required=True,
        )

        if not response.get("success", False):
            raise IOError(f"Order cancellation failed: {response}")

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

        response = await self._api_request(
            path=CONSTANTS.GET_ORDER_URL.format(order_id=exchange_order_id),
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

    async def _update_order_status(self):
        """Update status of all active orders"""
        await super()._update_order_status()

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

            # Fetch all trades for this order
            # Path parameters are sent as strings in URLs
            response = await self._api_request(
                path=CONSTANTS.GET_ORDER_TRADES_URL.format(order_id=str(exchange_order_id)),
                method=RESTMethod.GET,
                is_auth_required=True,
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
        response = await self._api_request(
            path=CONSTANTS.POSITIONS_URL,
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

                position_qty = Decimal(str(position_data.get("position_qty", "0")))

                if position_qty == 0:
                    continue

                position_side = PositionSide.LONG if position_qty > 0 else PositionSide.SHORT
                unrealized_pnl = Decimal(str(position_data.get("unrealized_pnl", "0")))
                entry_price = Decimal(str(position_data.get("average_open_price", "0")))
                leverage = Decimal(str(position_data.get("leverage", "1")))

                position = self._perpetual_trading.get_position(trading_pair, position_side)
                if position is not None:
                    position.update_position(
                        position_side=position_side,
                        unrealized_pnl=unrealized_pnl,
                        entry_price=entry_price,
                        amount=abs(position_qty),
                    )
                else:
                    await self._perpetual_trading.set_position(
                        trading_pair=trading_pair,
                        position_side=position_side,
                        unrealized_pnl=unrealized_pnl,
                        entry_price=entry_price,
                        amount=abs(position_qty),
                        leverage=leverage,
                    )

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
            self.logger().debug(
                f"[SYMBOL CONVERSION] Setting leverage: Hummingbot '{trading_pair}' -> "
                f"Orderly symbol '{symbol}'"
            )

            data = {
                "symbol": symbol,
                "leverage": leverage,
            }

            response = await self._api_request(
                path=CONSTANTS.SET_LEVERAGE_URL,
                method=RESTMethod.POST,
                data=data,
                is_auth_required=True,
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
        self.logger().info(
            f"[AUTH DEBUG] _update_balances called - "
            f"self._auth={self._auth}, "
            f"auth account_id={getattr(self._auth, '_account_id', 'MISSING') if self._auth else 'NO_AUTH'}"
        )
        response = await self._api_request(
            path=CONSTANTS.ACCOUNT_HOLDING_URL,
            method=RESTMethod.GET,
            is_auth_required=True,
        )
        self.logger().info(f"response: {response}")
        if not response.get("success", False):
            self.logger().error(f"Failed to fetch balances: {response}")
            return

        data = response.get("data", {})
        self.logger().info(f"data: {data}")
        holdings = data.get("holding", [])
        self.logger().info(f"holdings: {holdings}")
        
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
            self.logger().debug(
                f"[SYMBOL CONVERSION] Fetching funding payment: Hummingbot '{trading_pair}' -> "
                f"Orderly symbol '{symbol}'"
            )

            # Orderly API requires size parameter as a string
            response = await self._api_request(
                path=CONSTANTS.FUNDING_FEE_HISTORY_URL,
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
        """Process order update event from WebSocket"""
        data = event.get("data", {})

        client_order_id = data.get("client_order_id")
        if not client_order_id:
            return

        tracked_order = self._order_tracker.all_updatable_orders.get(client_order_id)
        if not tracked_order:
            return

        new_state = CONSTANTS.ORDER_STATE.get(data.get("status"), OrderState.OPEN)

        order_update = OrderUpdate(
            trading_pair=tracked_order.trading_pair,
            update_timestamp=data.get("timestamp", self.current_timestamp) * 1e-3,
            new_state=new_state,
            client_order_id=client_order_id,
            exchange_order_id=str(data.get("order_id", "")),
        )

        self._order_tracker.process_order_update(order_update)

    async def _process_position_event(self, event: Dict[str, Any]):
        """Process position update event from WebSocket"""
        # Trigger position update
        await self._update_positions()

    async def _process_balance_event(self, event: Dict[str, Any]):
        """Process balance update event from WebSocket"""
        # Trigger balance update
        await self._update_balances()

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
