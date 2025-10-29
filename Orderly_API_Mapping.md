
# References to documentation that should be followed and considered the sole source of truth unless specified otherwise

- Hummingbot API Requirements doc link - https://hummingbot.org/developers/connectors/build/
- Orderly API Docs - https://github.com/OrderlyNetwork/documentation-public/tree/main/build-on-omnichain/evm-api
- Orderly websocket specific Docs - https://github.com/OrderlyNetwork/documentation-public/tree/main/build-on-omnichain/evm-api/websocket-api

- Orderly API Docs swagger Links - https://raw.githubusercontent.com/OrderlyNetwork/documentation-public/refs/heads/main/evm.openapi.yaml
- Orderly API overview - https://orderly.network/docs/build-on-omnichain/evm-api/introduction
- Orderly API Auth guide - https://orderly.network/docs/build-on-omnichain/evm-api/api-authentication
- Orderly API error codes - https://orderly.network/docs/build-on-omnichain/evm-api/error-codes




## Required Endpoints Mapping

### REST API Endpoints

| Hummingbot Requirement | Orderly API Endpoint | Method | Purpose | Rate Limit Info |
|------------------------|---------------------|--------|---------|----------------|
| Trading rules | `/v1/public/futures` | GET | Get trading rules for all perpetual markets (min order size, tick size, etc.) | Public endpoint |
| Trading rules (single) | `/v1/public/futures/{symbol}` | GET | Get trading rules for specific symbol | Public endpoint |
| Server status | `/v1/public/futures` | GET | Check server health (can use any public endpoint as health check) | Public endpoint |
| Active orders | `/v1/orders` | GET | Retrieve all pending orders with status filters | Private endpoint - requires authentication |
| Create order | `/v1/order` | POST | Place new market or limit order | Private endpoint - order rate limits apply |
| Batch create orders | `/v1/batch-order` | POST | Create multiple orders (max 10) in one request | Private endpoint - batch operations |
| Cancel order | `/v1/order` | DELETE | Cancel order by order_id | Private endpoint |
| Cancel all orders | `/v1/orders` | DELETE | Cancel all pending orders | Private endpoint |
| Batch cancel orders | `/v1/batch-order` | DELETE | Cancel multiple orders (max 10) | Private endpoint |
| Modify order | `/v1/order` | PUT | Edit pending order parameters | Private endpoint |
| Get order details | `/v1/order/{order_id}` | GET | Fetch single order information | Private endpoint |
| Account balance | `/v1/client/holding` | GET | Get current token holdings and available balance | Private endpoint |
| Check positions | `/v1/positions` | GET | Retrieve all open positions | Private endpoint - required for perps |
| Single position | `/v1/position/{symbol}` | GET | Get specific position details | Private endpoint |
| Configure leverage | `POST /v1/client/leverage` | POST | Set leverage for trading (max leverage per symbol) | Private endpoint - required for perps |
| Get leverage | `GET /v1/client/leverage` | GET | Query current leverage settings | Private endpoint |
| Account info | `/v1/client/info` | GET | Get account details including fee rates | Private endpoint |

### WebSocket Channels (Required)

| Hummingbot Requirement | Orderly WebSocket Channel | Purpose | Subscription Type |
|------------------------|--------------------------|---------|------------------|
| Public order book updates | Orderbook channel (per symbol) | Real-time orderbook snapshots and updates | Public - requires symbol subscription |
| Public trades | Market trades channel (per symbol) | Real-time executed trade feed | Public - requires symbol subscription |
| Private order updates | Order execution updates channel | Real-time order status changes | Private - requires authentication |
| Private trade events | Trade execution events channel | Real-time private trade fills and executions | Private - requires authentication |

## Optional/Additional Endpoints Mapping

### REST API Endpoints (Useful)

| Feature | Orderly API Endpoint | Method | Purpose | Benefits |
|---------|---------------------|--------|---------|----------|
| Trading pairs list | `/v1/public/futures` | GET | List all available perpetual markets | Simplifies market discovery |
| Trade history | `/v1/trades` | GET | Query past trade executions | Better trade reconciliation |
| Order trades | `/v1/order/{order_id}/trades` | GET | Get all trades for specific order | Detailed order fill information |
| Position history | `/v1/position_history` | GET | Access closed positions | Historical analysis |
| Funding rates | `/v1/public/funding_rates` | GET | Get predicted funding rates for all markets | Calculate funding costs |
| Funding rate (single) | `/v1/public/funding_rate/{symbol}` | GET | Get funding rate for specific symbol | Symbol-specific funding info |
| Funding fee history | `/v1/funding_fee/history` | GET | Historical funding payments | Track funding P&L |
| Account statistics | `/v1/client/statistics` | GET | Retrieve account performance metrics | Enhanced monitoring |
| Liquidations | `/v1/liquidations` | GET | View liquidation history | Risk management insights |
| Market orderbook | `/v1/orderbook/{symbol}` | GET | REST snapshot of current orderbook | Fallback if WS fails |
| Market trades (REST) | `/v1/public/market_trades` | GET | REST endpoint for recent trades | Fallback if WS fails |
| Kline/Candles | `/v1/kline` | GET | Historical OHLC candlestick data | Strategy development |
| Settle PnL | `/v1/settle_pnl` | POST | Request profit/loss settlement | Manual settlement control |
| Withdraw request | `/v1/withdraw_request` | POST | Initiate withdrawal from trading account | Fund management |
| Internal transfer | `/v1/internal_transfer` | POST | Transfer between sub-accounts | Multi-account management |

### WebSocket Channels (Optional)

| Feature | Orderly WebSocket Channel | Purpose | Benefits |
|---------|--------------------------|---------|----------|
| Balance updates | Account balance channel | Real-time balance changes | Eliminates need for balance estimation |
| Position updates | Position updates channel | Real-time position changes | Better position tracking |
| Funding updates | Funding payment notifications | Real-time funding payments | Accurate funding tracking |

### Authentication Endpoints (Setup)

| Purpose | Orderly API Endpoint | Method | Notes |
|---------|---------------------|--------|-------|
| Register account | `/v1/register_account` | POST | One-time account creation |
| Check registration | `/v1/get_account` | GET | Verify wallet registration status |
| Add API key | `/v1/orderly_key` | POST | Add Orderly access key for trading |
| Validate API key | `/v1/get_orderly_key` | GET | Check if key is valid |
| Remove API key | `/v1/client/remove_orderly_key` | POST | Deactivate trading key |

## Implementation Notes

1. **Rate Limits**: Must document rate limits for all endpoints (obtain from Orderly API docs)
2. **Authentication**: Orderly uses ed25519 elliptic curve cryptography with signature-based authentication
3. **WebSocket Endpoints**:
   - Mainnet: `wss://ws-private-evm.orderly.org/v2/ws/private/stream/{account_id}`
   - Testnet: `wss://testnet-ws-private-evm.orderly.org/v2/ws/private/stream/{account_id}`
4. **WebSocket Channels Confirmed**:
   - Public: `orderbook`, `orderbookupdate`, `trade`, `ticker`, `bbo`, `markprice`, `kline_*`
   - Private: `executionreport`, `position`, `balance`, `account`, `wallet`
5. **Order Types Supported**: MARKET, LIMIT, IOC, FOK, POST_ONLY, ASK/BID (best bid/ask guarantee)
6. **Leverage**: Default and maximum leverage values are symbol-specific (check via `/v1/client/leverage`)
7. **Funding**: Funding payments occur at regular intervals (8-hour typical for perps)

## Missing/Unclear Information - REQUIRES INVESTIGATION

### Critical Missing Information (Required for Implementation)

| Category | Question/Missing Info | Why It Matters | Where to Find |
|----------|----------------------|----------------|---------------|
| **Rate Limits** | Exact rate limit numbers (requests per second/minute/hour) per endpoint | Essential for implementing rate limiting logic to avoid 429 errors | Orderly API docs or error code documentation |
| **Rate Limits** | Burst allowances and rate limit reset windows | Needed for proper request throttling | API documentation or support |
| **Position Mode** | Does Orderly support hedge mode (long+short simultaneously)? | Hummingbot needs to handle position mode correctly | API docs or test with `/v1/position/{symbol}` |
| **Position Mode** | Is there an endpoint to switch between one-way and hedge mode? | Required if mode switching is supported | Search API docs for position mode configuration |
| **Margin Mode** | Does Orderly support cross margin vs isolated margin? | Affects risk management and margin calculations | Leverage/position documentation |
| **Margin Mode** | Endpoint to configure margin mode per symbol | If supported, need to implement in connector | API endpoint documentation |
| **WebSocket Auth** | Exact authentication method for WebSocket connections | Must authenticate before subscribing to private channels | WebSocket API authentication docs |
| **WebSocket Auth** | Do we send auth headers, or sign during connection, or separate auth message? | Implementation depends on auth method | WebSocket connection examples |
| **Order Book Depth** | How many price levels does Orderly provide in orderbook? | Affects orderbook data structure | Test `/v1/orderbook/{symbol}` or WS `orderbook` |
| **Order Book Updates** | Full snapshot vs incremental updates via WebSocket? | Implementation strategy for orderbook sync | WebSocket `orderbookupdate` channel docs |
| **Symbol Notation** | Exact symbol format (e.g., PERP_BTC_USDC vs BTC-USDC-PERP) | Symbol translation between Hummingbot and Orderly | `/v1/public/futures` response format |
| **Leverage Limits** | Per-symbol maximum leverage values | Validate user leverage settings | Test `/v1/client/leverage` response |
| **Leverage Config** | Is leverage set per symbol or account-wide? | Determines leverage management strategy | Leverage endpoint documentation |

### Important Optional Information (Enhances Functionality)

| Category | Question/Missing Info | Why It Matters | Where to Find |
|----------|----------------------|----------------|---------------|
| **Advanced Orders** | Support for stop-loss / take-profit orders? | Would enable advanced risk management | Create order endpoint docs |
| **Advanced Orders** | Support for conditional/trigger orders? | Enhanced order types for strategies | Order type documentation |
| **Advanced Orders** | Support for trailing stop orders? | Advanced trading features | API order types |
| **Time in Force** | Full list of TIF options (GTC, IOC, FOK, etc.) | Order execution control | Create order params docs - IOC/FOK confirmed, need GTC |
| **Order Modification** | Can all order parameters be modified (price, size, TIF)? | Determines edit order capabilities | PUT `/v1/order` endpoint docs |
| **Order Modification** | Limitations on modifying orders (e.g., can't modify partially filled)? | Error handling for order edits | Edit order error scenarios |
| **Funding Rates** | Funding rate calculation methodology | Accurate funding cost predictions | Funding rate documentation |
| **Funding Rates** | Funding interval (8h, 4h, 1h?) | Calculate funding payments timing | Funding schedule docs |
| **Liquidation** | Liquidation price calculation formula | Risk management calculations | Liquidation documentation |
| **Liquidation** | Maintenance margin requirements per symbol | Position sizing and risk limits | Risk parameters docs |
| **Account Config** | Default leverage when opening new positions | Initial connector configuration | Account settings docs |
| **Batch Operations** | Actual batch size limits (docs say "max 10", confirm this) | Optimize bulk operations | Batch order endpoint docs - confirmed max 10 |
| **Iceberg Orders** | Support for iceberg/hidden orders via `visible_quantity`? | Advanced order display options | Order parameters - mentioned in docs |
| **Order IDs** | Format and length of order_id (for validation) | Order tracking implementation | Order response format |
| **Timestamps** | Server time endpoint for clock sync | Avoid timestamp validation errors (300s window) | Look for `/v1/time` or similar |
| **Websocket Limits** | Max subscriptions per connection | Connection pooling strategy | WebSocket docs |
| **Websocket Limits** | Connection timeout and keepalive requirements | Connection stability | WebSocket connection docs |
| **Error Handling** | Complete list of error codes and meanings | Robust error handling | Error codes documentation link provided |
| **Decimals/Precision** | Price and quantity decimal precision per symbol | Order validation and formatting | Trading rules in `/v1/public/futures` |
| **Min Order Size** | Minimum order sizes per symbol | Order validation | Trading rules response |
| **Fees** | Fee structure (maker/taker rates, fee tiers) | PnL calculations | Account info or fee schedule docs |
| **Collateral** | Supported collateral tokens (USDC, USDT, etc.) | Balance management | Holdings endpoint or docs |

### Documentation Gaps to Address

1. **WebSocket Message Formats**: Need exact JSON structure for:
   - Subscription requests
   - Orderbook snapshots/updates
   - Trade execution reports
   - Position updates
   - Balance updates

2. **Authentication Flow**: Need complete flow for:
   - Initial account registration
   - API key generation and management
   - Signature generation examples
   - WebSocket authentication sequence

3. **Symbol Management**: Need to understand:
   - How to get list of active trading pairs
   - Symbol status (active, suspended, delisted)
   - Contract specifications per symbol

4. **Testing Strategy**:
   - Testnet availability and differences from mainnet
   - How to get testnet credentials
   - Test USDC/tokens for testing

## Recommended Next Steps

1. **Review Orderly WebSocket documentation** at provided links for exact message formats
2. **Test rate limits** by making sequential API calls and logging 429 responses
3. **Examine error codes documentation** at https://orderly.network/docs/build-on-omnichain/evm-api/error-codes
4. **Test position endpoints** to determine position mode support (hedge vs one-way)
5. **Review `/v1/public/futures` response** to extract all trading rules and symbol specifications
6. **Check for undocumented endpoints** by reviewing other Orderly connector implementations if available
7. **Verify margin mode** through testing or contacting Orderly support
8. **Document WebSocket authentication** by testing connection flow
