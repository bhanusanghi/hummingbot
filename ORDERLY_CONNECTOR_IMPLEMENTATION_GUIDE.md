# Orderly Network Perpetual Connector Implementation Guide

## Overview

This document provides a comprehensive comparison between the Hyperliquid Perpetual connector implementation and the required implementation for Orderly Network. It serves as a blueprint for implementing the Orderly connector in Hummingbot.

## Connector Architecture

Hummingbot connectors follow a standardized architecture with the following core components:

1. **Main Connector Class** - Inherits from `PerpetualDerivativePyBase`
2. **Authentication** - Handles API key signing and request authentication
3. **Order Book Data Source** - Fetches and maintains order book data
4. **User Stream Data Source** - Manages user-specific WebSocket streams
5. **Web Utils** - Helper functions for URLs and data transformation
6. **Constants** - Exchange-specific configuration and endpoints

---

## Function-by-Function Implementation Comparison

| # | Function/Component | Hyperliquid Implementation | Orderly Implementation |
|---|-------------------|---------------------------|------------------------|
| **1. AUTHENTICATION** |
| 1.1 | Auth Class | `HyperliquidPerpetualAuth` - Uses Ethereum wallet signing with EIP-712 typed data. Signs actions using eth_account library with msgpack encoding. | Use `orderly_network_core` SDK: `Account` class for account management, `getDefaultSigner()` for signing operations. Use `generateAddOrderlyKeyMessage()` for key setup and sign with Secp256k1 ECDSA algorithm. |
| 1.2 | Signature Generation | `sign_l1_action()` - Creates phantom agent hash, constructs EIP-712 typed data with domain (chainId: 1337, name: "Exchange"), signs with wallet private key. Returns `{r, s, v}` signature. | Use `orderly_network_core.utils.getTimestamp()` for nonce. Create signature using orderly-trading-key and orderly-trading-secret. Each order requires additional signature with trading keys. Use SDK's signing methods. |
| 1.3 | Request Authentication | `rest_authenticate()` - For POST requests, wraps params with action, nonce, signature, and vaultAddress. Supports order, cancel, and updateLeverage types. | Set headers: `orderly-account-id`, `orderly-key`, `orderly-signature`, `orderly-timestamp`. Use `parseAccountId()` from utils. For orders, add `orderly-trading-key` and order signature. |
| 1.4 | Order Signing | `_sign_order_params()` - Converts order spec to wire format, creates order action with type "order", signs with L1 action hash. | Generate order signature separately using trading keys. Use `base64url()` encoding. Include all order parameters in signature payload. Reference: POST /v1/order endpoint requirements. |
| **2. CONSTANTS & CONFIGURATION** |
| 2.1 | Base URLs | Mainnet: `https://api.hyperliquid.xyz`<br>Testnet: `https://api.hyperliquid-testnet.xyz` | Mainnet: `https://api.orderly.org`<br>Testnet: `https://testnet-api.orderly.org` |
| 2.2 | WebSocket URLs | Mainnet: `wss://api.hyperliquid.xyz/ws`<br>Testnet: `wss://api.hyperliquid-testnet.xyz/ws` | Use same base URL with WebSocket upgrade. Check Orderly docs for specific WS paths (likely `/ws` or `/v1/ws/stream`). |
| 2.3 | Symbol Format | `{BASE}-USD` (e.g., "BTC-USD") | `PERP_{SYMBOL}_USDC` (e.g., "PERP_BTC_USDC") |
| 2.4 | Broker ID | `"HBOT"` | Configure as broker ID or use builder ID from Orderly registration. |
| 2.5 | Rate Limits | 1200 requests per 60 seconds (global) | Check Orderly API docs. Rate limits counted per Orderly key. Trading endpoint: 10 req/sec. |
| 2.6 | Collateral Token | `"USD"` (USDC-settled) | `"USDC"` |
| **3. MAIN CONNECTOR CLASS** |
| 3.1 | Initialization | `__init__()` - Takes api_key, api_secret, use_vault, trading_pairs, trading_required, domain. | Similar params but: `orderly_api_key`, `orderly_api_secret`, `orderly_account_id`. No vault concept. May need `broker_id`. |
| 3.2 | Authenticator Property | Returns `HyperliquidPerpetualAuth` with api_key, secret, use_vault. | Return `OrderlyPerpetualAuth` with api_key, secret, account_id (use `parseAccountId()` from SDK utils). |
| 3.3 | Supported Order Types | `[OrderType.LIMIT, OrderType.LIMIT_MAKER, OrderType.MARKET]` | Check Orderly API. Likely: `[OrderType.LIMIT, OrderType.MARKET]`. Orderly supports LIMIT, MARKET order types. |
| 3.4 | Position Modes | `[PositionMode.ONEWAY]` only | Check Orderly support. Implement `supported_position_modes()` accordingly. May support both ONEWAY and HEDGE. |
| 3.5 | Client Order ID | Uses MD5 hash of order_id prefixed with "0x". Max length unrestricted (None). | Use standard client order ID generation. Check Orderly max length requirements. Likely alphanumeric string. |
| **4. TRADING RULES & EXCHANGE INFO** |
| 4.1 | Get Trading Rules | POST `/info` with `{"type": "metaAndAssetCtxs"}`. Returns list with universe and price info. | GET `/v1/public/info` or `/v1/public/futures` for trading pair info. Parse JSON response for trading rules. |
| 4.2 | Parse Trading Rules | `_format_trading_rules()` - Extracts from universe array: szDecimals for step size, markPx decimals for price increment. Creates coin_to_asset mapping. | Parse response: extract `base_tick`, `quote_tick`, `base_min`, `base_max`, `quote_min`, `quote_max`. Create TradingRule objects with proper increments and min sizes. |
| 4.3 | Trading Pair Symbols | `_initialize_trading_pair_symbols_from_exchange_info()` - Maps exchange format `{COIN}-USD` to Hummingbot format. | Map `PERP_{BASE}_USDC` format to Hummingbot trading pair format `{BASE}-USDC`. Use bidict for bidirectional mapping. |
| 4.4 | Price Quantization | `quantize_order_price()` - Rounds to 5 significant figures, then to 6 decimal places. Custom logic: `Decimal(round(float(f"{price:.5g}"), 6))` | Use trading rule's `min_price_increment` from exchange info. Quantize to proper decimal places based on quote_tick. |
| **5. ORDER PLACEMENT** |
| 5.1 | Buy Method | `buy()` - Generates MD5 hex order ID. For MARKET orders, calculates price with 5% slippage. Calls `_create_order()`. | Similar structure. Generate client_order_id. For MARKET orders, may need to use order book mid price or not send price. Check Orderly requirements. |
| 5.2 | Sell Method | `sell()` - Same as buy but with negative slippage for MARKET. | Same approach as buy. Adjust slippage direction for MARKET sells. |
| 5.3 | Place Order API | `_place_order()` - POST `/exchange` with:<br>```{"type": "order", "grouping": "na", "orders": {asset, isBuy, limitPx, sz, reduceOnly, orderType, cloid}}```<br>OrderType: `{"limit": {"tif": "Gtc"}}` for LIMIT, `{"limit": {"tif": "Alo"}}` for LIMIT_MAKER, `{"limit": {"tif": "Ioc"}}` for MARKET. | POST `/v1/order` with JSON body:<br>```{"symbol": "PERP_BTC_USDC", "client_order_id": "...", "order_type": "LIMIT", "order_price": 50000.0, "order_quantity": 0.1, "side": "BUY", "reduce_only": false}```<br>Include order signature in headers. |
| 5.4 | Order Response | Returns exchange order ID from `response["data"]["statuses"][0]["resting/filled"]["oid"]`. Timestamp from nonce. | Parse response JSON: extract `order_id` (exchange ID), `status`, `timestamp`. Map to Hummingbot order state. |
| 5.5 | Position Action | `reduceOnly` field in order params. Set to True when `position_action == PositionAction.CLOSE`. | Use `reduce_only` field in order JSON. Set to true for closing positions. |
| **6. ORDER CANCELLATION** |
| 6.1 | Cancel Order | `_place_cancel()` - POST `/exchange` with:<br>```{"type": "cancel", "cancels": {"asset": coin_to_asset[coin], "cloid": order_id}}```<br>Signs with cancelByCloid action type. | POST `/v1/order` with DELETE or POST `/v1/cancel_order` with:<br>```{"symbol": "PERP_BTC_USDC", "client_order_id": "..."}```<br>or by order_id: `DELETE /v1/order/{order_id}`. Check Orderly API docs. |
| 6.2 | Cancel Response | Checks for "success" in `response["data"]["statuses"][0]`. Returns True/False. Handles "error" field. | Parse response: check `success` field. Handle error codes. Return boolean success status. |
| **7. ORDER STATUS & UPDATES** |
| 7.1 | Request Order Status | `_request_order_status()` - POST `/info` with:<br>```{"type": "orderStatus", "user": api_key, "oid": exchange_order_id}```<br>Returns order status, oid, cloid, timestamp. | GET `/v1/order/{order_id}` or GET `/v1/client/order/{client_order_id}` or POST `/v1/orders` for batch. Parse: `status`, `order_id`, `client_order_id`, `created_time`, `updated_time`. |
| 7.2 | Order State Mapping | Maps exchange states to OrderState:<br>```{"open": OPEN, "resting": OPEN, "filled": FILLED, "canceled": CANCELED, "rejected": FAILED, "reduceOnlyCanceled": CANCELED, "perpMarginRejected": FAILED}``` | Map Orderly states: `NEW->OPEN`, `PARTIAL_FILLED->OPEN`, `FILLED->FILLED`, `CANCELLED->CANCELED`, `REJECTED->FAILED`, `INCOMPLETE->OPEN`, `COMPLETED->FILLED`. |
| 7.3 | Update Orders | `_update_order_status()` -> calls `_update_orders()` from base class. Base class iterates tracked orders and calls `_request_order_status()`. | Same pattern. Override `_update_order_status()` to call base class method, which handles the update loop. |
| **8. TRADE HISTORY & FILLS** |
| 8.1 | Get Trade History | `_update_trade_history()` - POST `/info` with:<br>```{"type": "userFills", "user": api_key}```<br>Returns list of all fills. | GET `/v1/trades` with optional params: `symbol`, `start_t`, `end_t`, `page`, `size`. Or use `/v1/client/trades` for account trades. |
| 8.2 | Process Trade | `_process_trade_message()` / `_process_trade_rs_event_message()` - Extracts: oid, coin, dir (Open/Close), fee, tid, sz, px, time. Creates TradeUpdate with fee calculation. | Parse trade response: extract `order_id`, `executed_price`, `executed_quantity`, `fee`, `fee_asset`, `trade_id`, `executed_time`, `side`. Create TradeUpdate. |
| 8.3 | Fee Calculation | Uses `TradeFeeBase.new_perpetual_fee()` with position_action (OPEN/CLOSE). Flat fee from trade data. | Similar: extract `fee` and `fee_asset` from trade. Use `new_perpetual_fee()` with proper position_action derived from trade direction and position change. |
| **9. ACCOUNT BALANCE** |
| 9.1 | Update Balances | `_update_balances()` - POST `/info` with:<br>```{"type": "clearinghouseState", "user": api_key}```<br>Extracts `crossMarginSummary.accountValue` and `withdrawable`. | GET `/v1/client/holding` or `/v1/client/info` for account summary. Extract `holding` array: `token`, `holding`, `frozen`. Calculate available = holding - frozen. |
| 9.2 | Balance Storage | Stores in `_account_balances[quote]` and `_account_available_balances[quote]` where quote is "USD". | Store USDC balances: `_account_balances["USDC"]` and `_account_available_balances["USDC"]`. |
| **10. POSITIONS** |
| 10.1 | Update Positions | `_update_positions()` - Same clearinghouseState request. Parses `assetPositions` array with position details. | GET `/v1/positions` or included in `/v1/client/info`. Parse positions array with `symbol`, `position_qty`, `average_open_price`, `mark_price`, `unrealized_pnl`. |
| 10.2 | Position Parsing | Extracts: coin, szi (size, signed), unrealizedPnl, entryPx, leverage.value. Determines LONG if szi > 0, SHORT if < 0. | Parse: `symbol`, `position_qty` (signed), `cost_position`, `mark_price`, `pending_long_qty`, `pending_short_qty`, `unrealized_pnl`, `average_open_price`. Derive position_side from sign. |
| 10.3 | Position Storage | Creates Position object, stores with `_perpetual_trading.set_position(pos_key, position)`. Removes if amount == 0. | Same pattern. Create Position objects, store with position keys. Handle position removal when closed. |
| **11. LEVERAGE** |
| 11.1 | Set Leverage | `_set_trading_pair_leverage()` - POST `/exchange` with:<br>```{"type": "updateLeverage", "asset": coin_to_asset[coin], "isCross": True, "leverage": leverage}```<br>Returns success boolean. | POST `/v1/client/leverage` with:<br>```{"symbol": "PERP_BTC_USDC", "leverage": 10}```<br>Or may be account-level: check Orderly docs. Parse success response. |
| 11.2 | Get Position Mode | `_get_position_mode()` - Returns `PositionMode.ONEWAY` (hardcoded). | Check Orderly support. May return ONEWAY or allow HEDGE mode configuration via API. Implement accordingly. |
| 11.3 | Set Position Mode | `_trading_pair_position_mode_set()` - Returns (False, "hyperliquid only supports ONEWAY") if mode != ONEWAY. | If Orderly supports HEDGE mode: POST to position mode endpoint. Otherwise, similar to Hyperliquid, only allow ONEWAY. |
| **12. FUNDING** |
| 12.1 | Get Funding Info | `get_funding_info()` via data source - POST `/info` with metaAndAssetCtxs. Extracts from response: oraclePx, markPx, funding rate. Next funding time calculated. | GET `/v1/public/funding_rate/{symbol}` or `/v1/public/funding_rates`. Parse: `est_funding_rate`, `last_funding_rate`, `last_funding_time`, `next_funding_time`, `funding_rate_interval`. |
| 12.2 | Funding History | `_fetch_last_fee_payment()` - POST `/info` with:<br>```{"type": "userFunding", "user": api_key, "startTime": last_funding_time}```<br>Returns funding payments with delta.usdc, delta.fundingRate. | GET `/v1/funding_fee/history` or `/v1/client/funding_fee/history` with time range. Parse: `symbol`, `funding_rate`, `funding_fee`, `timestamp`. |
| 12.3 | Funding Time Calculation | `_next_funding_time()` - `int(((time.time() // 3600) + 1) * 3600)` (hourly). `_last_funding_time()` - `int(((time.time() // 3600) - 1) * 3600 * 1e3)`. | Use `next_funding_time` from API response. Orderly funding may be 8-hourly. Don't calculate manually, use exchange-provided timestamps. |
| **13. ORDER BOOK DATA SOURCE** |
| 13.1 | Get Order Book Snapshot | POST `/info` with `{"type": "l2Book", "coin": coin}`. Returns levels (bids/asks) with px, sz. Timestamp in response. | GET `/v1/orderbook/{symbol}` or `/v1/{symbol}/orderbook`. Returns: `asks` and `bids` arrays with `[price, quantity]`. Timestamp included. |
| 13.2 | WebSocket Subscribe | Subscribes to channels: trades and l2Book. Payload:<br>```{"method": "subscribe", "subscription": {"type": "trades"/"l2Book", "coin": coin}}``` | Subscribe to: `{"topic":"orderbook","symbol":"PERP_BTC_USDC"}` and `{"topic":"trade","symbol":"PERP_BTC_USDC"}`. Check exact format in Orderly WS docs. |
| 13.3 | Parse Order Book Diff | Parses WS message with channel "l2Book". Extracts time, coin, levels. Creates DIFF OrderBookMessage. | Parse WS message: extract `symbol`, `asks`, `bids`, `timestamp`. Handle incremental updates. Create DIFF message if partial update, SNAPSHOT if full. |
| 13.4 | Parse Trades | Parses WS message with channel "trades". Extracts: coin, side (A=SELL, B=BUY), hash, px, sz, time. | Parse WS trade: extract `symbol`, `side` (BUY/SELL), `executed_price`, `executed_quantity`, `executed_timestamp`, `trade_id`. |
| 13.5 | Funding Info Updates | `listen_for_funding_info()` - Polls every 60 seconds (FUNDING_RATE_UPDATE_INTERNAL_SECOND). Calls get_funding_info() and puts FundingInfoUpdate in queue. | Similar polling or use WS funding rate channel if available. Update funding info periodically. |
| **14. USER STREAM DATA SOURCE** |
| 14.1 | WebSocket Connection | Connects to WSS URL. No listen key required. Direct connection with auth in subscription. | May require authentication in WS connection or subscription message. Include `orderly-key` in WS headers or auth message. |
| 14.2 | Subscribe User Channels | Subscribes to:<br>- `{"type": "orderUpdates", "user": api_key}`<br>- `{"type": "user", "user": api_key}` | Subscribe to:<br>- `{"topic":"executionreport"}` or `{"topic":"orders"}`<br>- `{"topic":"balance"}` or `{"topic":"position"}`<br>Check Orderly WS API for exact topics. |
| 14.3 | Process Order Messages | `_process_order_message()` - Handles orderUpdates channel. Extracts: cloid, oid, status, statusTimestamp. Creates OrderUpdate. | Parse order WS message: extract `client_order_id`, `order_id`, `status`, `timestamp`, `symbol`. Map status to OrderState. Create OrderUpdate. |
| 14.4 | Process Trade Messages | `_process_trade_message()` - Handles user fills from user channel. Extracts: oid, coin, dir, fee, tid, px, sz, time. Creates TradeUpdate. | Parse execution report: extract order_id, trade_id, executed_price, executed_quantity, fee, timestamp. Create TradeUpdate. |
| 14.5 | Heartbeat/Ping | `_ping_thread()` - Sends `{"method": "ping"}` every 30 seconds in separate task. | Check Orderly WS requirements. May need periodic ping or rely on TCP keep-alive. Implement ping if required by exchange. |
| **15. WEB UTILS** |
| 15.1 | REST URL Builder | `rest_url(path, domain)` - Selects base URL based on domain (mainnet/testnet). Concatenates base + path. | Similar: `rest_url(path, domain)` using Orderly mainnet/testnet URLs. All paths relative to base. |
| 15.2 | WebSocket URL | `wss_url(domain)` - Returns WSS URL based on domain. | Similar: return appropriate WSS URL for Orderly mainnet/testnet. |
| 15.3 | REST Preprocessor | `HyperliquidPerpetualRESTPreProcessor` - Adds `Content-Type: application/json` header to all requests. | Create `OrderlyPerpetualRESTPreProcessor` - Add required headers: `Content-Type: application/json`, authentication headers. |
| 15.4 | Order Wire Format | `order_spec_to_order_wire()` - Converts order spec to wire format with short keys (a, b, p, s, r, t, c). Uses `float_to_wire()` for price/size. | Not needed. Orderly uses standard JSON format. Keep order params in readable format as per API spec. |
| 15.5 | Throttler | `create_throttler()` - Creates AsyncThrottler with RATE_LIMITS from constants. | Similar: create throttler with Orderly rate limits. Define limits per endpoint in constants. |
| **16. ERROR HANDLING** |
| 16.1 | Order Not Found | `_is_order_not_found_during_status_update_error()` - Checks if error message contains "order". | Check Orderly error codes/messages. May return specific error code for order not found (e.g., error code 1006). |
| 16.2 | Cancellation Error | `_is_order_not_found_during_cancelation_error()` - Checks for "Order was never placed, already canceled, or filled". | Check Orderly cancel error responses. Handle "order not found" or "already cancelled" errors. |
| 16.3 | Request Exception | `_is_request_exception_related_to_time_synchronizer()` - Returns False (no time sync needed). | Check if Orderly requires time synchronization. If yes, implement time sync check. May need to handle clock skew errors. |
| **17. ADDITIONAL METHODS** |
| 17.1 | Network Check | `_make_network_check_request()` - POST to ping URL with `{"type": "meta"}`. Simple connectivity test. | GET `/v1/public/system_info` or ping endpoint. Check response status. |
| 17.2 | Last Traded Price | `_get_last_traded_price()` - POST with metaAndAssetCtxs, extracts markPx from matching coin in universe. | GET `/v1/public/ticker/{symbol}` or `/v1/market/ticker`. Extract `last_price` or `mark_price`. |
| 17.3 | Update Trading Fees | `_update_trading_fees()` - Empty pass method. Fees calculated from fee schema. | GET `/v1/public/fee/program` or `/v1/client/fee_rate` if available. Update fee configuration if exchange provides this info. |
| 17.4 | Lost Orders | `_update_lost_orders_status()` - Calls base class `_update_lost_orders()` to handle orders not found repeatedly. | Same: use base class implementation. Base class handles automatic order failure for lost orders. |
| 17.5 | Status Polling | `_status_polling_loop_fetch_updates()` - Gathers: _update_trade_history, _update_order_status, _update_balances, _update_positions. | Same pattern: override to call the same four update methods in parallel using safe_gather. |

---

## Implementation Priority

### Phase 1: Core Infrastructure
1. Constants configuration (URLs, endpoints, rate limits)
2. Web utils (URL builders, throttler)
3. Authentication class (Orderly key signing)

### Phase 2: Data Sources
4. Order book data source (REST snapshot, WebSocket feed)
5. User stream data source (order updates, trades, positions)

### Phase 3: Trading Operations
6. Main connector class initialization
7. Trading rules and exchange info
8. Order placement and cancellation
9. Order status updates

### Phase 4: Account Management
10. Balance updates
11. Position tracking
12. Leverage management

### Phase 5: Advanced Features
13. Funding rate tracking
14. Trade history
15. Error handling and edge cases

---

## Key Differences: Hyperliquid vs Orderly

### Authentication
- **Hyperliquid**: EIP-712 typed data signing with phantom agent, msgpack encoding
- **Orderly**: Standard API key + signature headers, separate trading key for orders

### API Structure
- **Hyperliquid**: Unified `/info` and `/exchange` endpoints with type parameter
- **Orderly**: RESTful API with separate endpoints per function (standard REST pattern)

### Symbol Format
- **Hyperliquid**: `{BASE}-USD` (e.g., "BTC-USD")
- **Orderly**: `PERP_{BASE}_USDC` (e.g., "PERP_BTC_USDC")

### Order Wire Format
- **Hyperliquid**: Compressed format with single-letter keys (a, b, p, s, r, t, c)
- **Orderly**: Standard JSON with readable keys

### Asset Mapping
- **Hyperliquid**: Maintains `coin_to_asset` dict mapping coin names to integer IDs
- **Orderly**: Direct symbol usage, no additional mapping needed

### Market Orders
- **Hyperliquid**: Sent as IOC limit orders with slippage-adjusted price
- **Orderly**: May support native MARKET order type, check API docs

### Vault Support
- **Hyperliquid**: Built-in vault support with vaultAddress in requests
- **Orderly**: No vault concept, standard account-based trading

---

## Testing Checklist

- [ ] Authentication generates valid signatures
- [ ] REST API calls succeed with proper headers
- [ ] WebSocket connections establish and receive messages
- [ ] Order book updates process correctly
- [ ] Orders place and return exchange IDs
- [ ] Order status updates reflect changes
- [ ] Order cancellations work
- [ ] Trade fills process with correct fees
- [ ] Balances update after trades
- [ ] Positions track correctly
- [ ] Leverage changes apply
- [ ] Funding rates update
- [ ] Error handling works for edge cases
- [ ] Rate limiting prevents overages
- [ ] Symbol mapping works both directions

---

## Resources

### Orderly Documentation
- Main API Docs: https://docs.orderly.network/
- API Reference: https://docs-api.orderly.network/
- EVM API Introduction: https://orderly.network/docs/build-on-omnichain/evm-api/introduction
- SDK Documentation: https://orderly.network/docs/sdks/perp/overview
- Python Connector: https://github.com/OrderlyNetwork/orderly-evm-connector-python

### Hummingbot Documentation
- Connector Architecture: https://hummingbot.org/developers/connectors/architecture/
- Perp Connector Checklist: https://hummingbot.org/developers/connectors/perp-connector-checklist/
- Order Lifecycle: https://hummingbot.org/developers/connectors/architecture/order_lifecycle/

### Reference Implementation
- Hyperliquid Connector: `hummingbot/connector/derivative/hyperliquid_perpetual/`
- Base Class: `hummingbot/connector/perpetual_derivative_py_base.py`

---

## Notes

1. **SDK Usage**: While Orderly provides a Python SDK, for Hummingbot integration it's often better to implement direct API calls to maintain consistency with other connectors and have full control over async operations.

2. **Account Setup**: Before implementing, ensure you have:
   - Orderly account registered
   - API keys generated (orderly-key and orderly-trading-key)
   - Account ID derived using `parseAccountId()`
   - Builder ID if required

3. **Testing Environment**: Use Orderly testnet for initial development and testing. Testnet API: https://testnet-api.orderly.org

4. **Rate Limiting**: Pay special attention to Orderly's rate limits. The trading endpoint has a limit of 10 requests per second, which is stricter than Hyperliquid.

5. **WebSocket Reconnection**: Implement robust WebSocket reconnection logic to handle network issues and connection drops.

6. **Order Signatures**: Each order requires a separate signature using the trading key. Implement the signing logic carefully according to Orderly's specification (Secp256k1 ECDSA).

7. **Fee Structure**: Orderly has a builder fee model. Understand how fees are calculated and rebated if you're registering as a builder.

8. **Time Synchronization**: Check if Orderly requires timestamp validation and implement time synchronization if needed.

---

## Next Steps

1. Set up Orderly testnet account and generate API keys
2. Create the basic connector structure following the Hyperliquid template
3. Implement authentication and test with simple API calls
4. Build out the order book data source
5. Implement order placement and track through lifecycle
6. Add WebSocket user stream integration
7. Complete balance and position tracking
8. Implement funding rate tracking
9. Add comprehensive error handling
10. Write unit tests for all components
11. Perform integration testing on testnet
12. Document any Orderly-specific quirks or limitations
13. Prepare for mainnet deployment

---

**Generated by Claude Code**
**Date: 2025-10-23**
