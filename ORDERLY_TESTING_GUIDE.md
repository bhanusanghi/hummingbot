# Orderly Network Connector - Live Testing Guide

This guide explains how to run live tests for the Orderly Network perpetual connector on mainnet.

## Overview

Two comprehensive test scripts are provided:
1. **`test_orderly_websocket_live.py`** - Tests all WebSocket functionality
2. **`test_orderly_order_lifecycle_live.py`** - Tests complete order lifecycle and position management

⚠️ **WARNING:** These tests interact with Orderly mainnet and will place real orders. Trading fees will be incurred.

---

## Prerequisites

### 1. Orderly Network Account Setup

You need an active Orderly Network account with:
- Account registered on Orderly Network
- API keys generated (account_id, orderly_key, orderly_secret)
- USDC balance for trading
- Leverage configured (default: 10x)

### 2. Get Your Credentials

Visit [Orderly Network](https://orderly.network/) to:
1. Create an account
2. Generate API keys
3. Note your account ID
4. Fund your account with USDC

### 3. Install Dependencies

Ensure you have all required dependencies:
```bash
pip install pyyaml
```

---

## Configuration

### Step 1: Create Configuration File

Copy the example configuration:
```bash
cp test_config.yml.example test_config.yml
```

### Step 2: Edit Configuration

Edit `test_config.yml` with your credentials:

```yaml
orderly:
  # Your Orderly Network credentials
  account_id: "0x1234567890abcdef..."  # Your account ID
  api_key: "ed25519:ABC123..."  # Your API public key
  api_secret: "ed25519:XYZ789..."  # Your API private key

  # Test configuration
  domain: "orderly_perpetual"  # Use "orderly_perpetual_testnet" for testnet
  trading_pair: "BTC-USDC"
  leverage: 10

  # WebSocket test duration (seconds)
  websocket_test_duration: 60

  # Order test configuration
  order_offset_percentage: 0.05  # Place LIMIT orders 5% away from market
  min_order_size: 0.001  # Minimum BTC order size
```

**Important:**
- Keep `test_config.yml` secure (it contains your API keys)
- `test_config.yml` is in `.gitignore` to prevent accidental commits
- Adjust `min_order_size` based on minimum order requirements and your risk tolerance

---

## Test 1: WebSocket Testing

### What It Tests

The WebSocket test validates:

**Public WebSocket:**
- ✅ Connection establishment
- ✅ Order book subscriptions and updates
- ✅ Trade stream subscriptions and updates
- ✅ Heartbeat/ping-pong mechanism

**Private WebSocket:**
- ✅ Connection establishment
- ✅ Authentication with ed25519 signatures
- ✅ Order execution report subscriptions
- ✅ Position update subscriptions
- ✅ Balance update subscriptions

**Connection Management:**
- ✅ Automatic reconnection after disconnect
- ✅ WebSocket stability over time

### Running the Test

```bash
python test_orderly_websocket_live.py
```

### Expected Output

```
================================================================================
Starting Orderly Network WebSocket Live Tests
================================================================================
Domain: orderly_perpetual
Trading Pair: BTC-USDC
Test Duration: 60 seconds
================================================================================

================================================================================
TEST 1: Public WebSocket
================================================================================
Connecting to public WebSocket: wss://ws-evm.orderly.org/ws/stream
✓ Public WebSocket connected successfully
Subscribed to orderbook: PERP_BTC_USDC@orderbook
Subscribed to trades: PERP_BTC_USDC@trade
Listening for messages for 60 seconds...
✓ First orderbook update received
✓ First trade update received
✓ Heartbeat ping received
...

Public WebSocket Test Summary:
  Orderbook updates received: 150
  Trade updates received: 45

================================================================================
TEST 2: Private WebSocket
================================================================================
Connecting to private WebSocket: wss://ws-private-evm.orderly.org/v2/ws/private/stream/0x...
✓ Private WebSocket connected successfully
Authenticating private WebSocket...
✓ Private WebSocket authenticated successfully
Subscribed to executionreport
Subscribed to position
Subscribed to balance
...

================================================================================
TEST RESULTS SUMMARY
================================================================================

Tests Passed: 8/10

  ✓ PASS - public_ws_connection
  ✓ PASS - public_orderbook_received
  ✓ PASS - public_trades_received
  ✓ PASS - private_ws_connection
  ✓ PASS - private_ws_authenticated
  ✗ FAIL - private_order_updates_received
  ✗ FAIL - private_position_updates_received
  ✗ FAIL - private_balance_updates_received
  ✓ PASS - reconnection_successful
  ✓ PASS - heartbeat_working

Note: To test order updates, run test_orderly_order_lifecycle_live.py
Note: Position updates require opening/closing positions
Note: Balance updates require trading activity
```

**Note:** Private message tests (order/position/balance updates) will only pass if there's active trading. This is expected behavior.

---

## Test 2: Order Lifecycle Testing

### What It Tests

The order lifecycle test validates:

**Connector Initialization:**
- ✅ Connector starts successfully
- ✅ Balance retrieval
- ✅ Trading rules loaded
- ✅ Leverage setting

**LIMIT Order Lifecycle:**
- ✅ Place LIMIT order (far from market, won't fill)
- ✅ Check order status via REST API
- ✅ Receive order updates via WebSocket
- ✅ Cancel order successfully
- ✅ Verify cancellation

**MARKET Order & Position Management:**
- ✅ Place MARKET BUY order
- ✅ Order fills immediately
- ✅ Position opened and tracked
- ✅ Position updates via WebSocket
- ✅ Close position with MARKET SELL
- ✅ Position closed successfully

**Funding Payments:**
- ✅ Retrieve funding payment history

### Running the Test

⚠️ **This test places real orders and incurs trading fees!**

```bash
python test_orderly_order_lifecycle_live.py
```

### Safety Confirmation

The script will ask for confirmation before proceeding:

```
================================================================================
⚠️  WARNING: This test will place REAL orders on Orderly mainnet!
⚠️  Trading fees will be incurred!
================================================================================

Type 'YES' to proceed with live trading tests:
```

Type `YES` (all caps) to proceed.

### Expected Output

```
================================================================================
Starting Orderly Network Order Lifecycle Live Tests
================================================================================
Domain: orderly_perpetual
Trading Pair: BTC-USDC
Leverage: 10x
Min Order Size: 0.001
================================================================================

Initializing Orderly connector...
✓ Connector initialized successfully

================================================================================
TEST 1: Balance and Trading Rules
================================================================================
Checking USDC balance...
Total USDC Balance: 1000.0
Available USDC Balance: 950.0
✓ Balance check passed

Checking trading rules for BTC-USDC...
Min Order Size: 0.001
Min Price Increment: 0.1
Min Base Amount Increment: 0.001
Min Notional Size: 10.0
✓ Trading rules loaded successfully

================================================================================
TEST 2: Set Leverage
================================================================================
Setting leverage to 10x for BTC-USDC...
✓ Leverage set successfully

================================================================================
TEST 3: LIMIT Order Lifecycle
================================================================================
Current price: 45000.0

--- Placing LIMIT BUY order ---
Price: 42750.0 (offset 5.0% below market)
Amount: 0.001
Order ID: c0a1b2c3d4e5f6...
✓ LIMIT order placed successfully

--- Checking order status ---
Order State: OPEN
Exchange Order ID: 1234567890
✓ Order status checked successfully

--- Cancelling LIMIT order ---
Cancel result: True
✓ Order cancelled successfully

================================================================================
TEST 4: MARKET Order and Position Management
================================================================================

--- Placing MARKET BUY order to open LONG position ---
Amount: 0.001
Order ID: d1e2f3a4b5c6...
✓ MARKET order placed successfully

--- Waiting for order fill ---
Order state: OPEN
Order state: PARTIALLY_FILLED
Order state: FILLED
✓ Order filled!
  Executed amount: 0.001
  Average fill price: 45010.5

--- Checking position ---
✓ Position opened and tracked successfully
  Position side: LONG
  Amount: 0.001
  Entry price: 45010.5
  Unrealized PnL: 0.25
  Leverage: 10

--- Closing position with MARKET SELL order ---
Close order ID: e2f3a4b5c6d7...
✓ Position closed successfully

================================================================================
TEST 5: Funding Payments
================================================================================
Fetching funding payment history...
Last funding timestamp: 1234567890000
Funding rate: 0.0001
Payment amount: 0.0045
✓ Funding payments retrieved successfully

================================================================================
TEST RESULTS SUMMARY
================================================================================

Tests Passed: 13/13

  ✓ PASS - connector_initialization
  ✓ PASS - balance_check
  ✓ PASS - trading_rules_loaded
  ✓ PASS - leverage_set
  ✓ PASS - limit_order_placed
  ✓ PASS - limit_order_status_checked
  ✓ PASS - limit_order_cancelled
  ✓ PASS - market_order_placed
  ✓ PASS - market_order_filled
  ✓ PASS - position_opened
  ✓ PASS - position_tracked
  ✓ PASS - position_closed
  ✓ PASS - funding_payments_retrieved

Events Received:
  Total events: 8
    order_created: 3
    order_filled: 2
    order_completed: 2
    order_cancelled: 1

Order IDs:
  LIMIT order: c0a1b2c3d4e5f6...
  MARKET order: d1e2f3a4b5c6...
  Close order: e2f3a4b5c6d7...
```

---

## Understanding Test Results

### WebSocket Test Results

| Result | Meaning |
|--------|---------|
| `public_ws_connection` | Public WebSocket connected successfully |
| `public_orderbook_received` | Orderbook updates are being received |
| `public_trades_received` | Trade updates are being received |
| `private_ws_connection` | Private WebSocket connected successfully |
| `private_ws_authenticated` | Authentication with ed25519 succeeded |
| `private_order_updates_received` | Order execution reports received (requires trading) |
| `private_position_updates_received` | Position updates received (requires position changes) |
| `private_balance_updates_received` | Balance updates received (requires balance changes) |
| `reconnection_successful` | WebSocket reconnects after disconnect |
| `heartbeat_working` | Ping/pong heartbeat functioning |

### Order Lifecycle Test Results

| Result | Meaning |
|--------|---------|
| `connector_initialization` | Connector initialized and connected |
| `balance_check` | Account balance retrieved successfully |
| `trading_rules_loaded` | Trading rules for pair loaded |
| `leverage_set` | Leverage set successfully |
| `limit_order_placed` | LIMIT order placed on exchange |
| `limit_order_status_checked` | Order status retrieved via API |
| `limit_order_cancelled` | Order cancelled successfully |
| `market_order_placed` | MARKET order placed on exchange |
| `market_order_filled` | MARKET order filled completely |
| `position_opened` | Position opened successfully |
| `position_tracked` | Position tracked by connector |
| `position_closed` | Position closed successfully |
| `funding_payments_retrieved` | Funding payment history retrieved |

---

## Troubleshooting

### Common Issues

#### 1. "Configuration file not found"

**Solution:** Copy `test_config.yml.example` to `test_config.yml` and fill in your credentials.

#### 2. "Authentication failed"

**Causes:**
- Invalid API credentials
- Account ID doesn't match API keys
- API keys expired or revoked

**Solution:**
- Verify credentials in Orderly dashboard
- Regenerate API keys if necessary
- Ensure account_id matches the API key owner

#### 3. "No orderbook updates received"

**Causes:**
- Trading pair not available
- Network connectivity issues
- WebSocket subscription failed

**Solution:**
- Verify trading pair is active on Orderly
- Check network connection
- Review logs for subscription confirmation

#### 4. "Order placement failed"

**Causes:**
- Insufficient balance
- Order size below minimum
- Invalid trading pair
- Leverage not set

**Solution:**
- Check USDC balance
- Increase `min_order_size` in config
- Verify trading pair format (e.g., "BTC-USDC")
- Ensure leverage is configured

#### 5. "Position not closed"

**Causes:**
- Close order not filled
- Network delay
- Insufficient liquidity

**Solution:**
- Check close order status manually
- May need to wait longer or retry
- Check Orderly exchange for open positions

---

## Cost Estimation

### WebSocket Test
- **Cost:** Free (no trading)
- **Duration:** ~60 seconds
- **Risk:** None

### Order Lifecycle Test
- **Orders Placed:** 3 (1 LIMIT cancelled, 2 MARKET filled)
- **Trading Fees:**
  - MARKET BUY: ~$0.05 (0.05% taker fee on $100 notional)
  - MARKET SELL: ~$0.05 (0.05% taker fee on $100 notional)
  - **Total:** ~$0.10 per test run
- **Duration:** ~2-3 minutes
- **Risk:** Minimal (uses minimum order size)

**Note:** Actual costs depend on:
- Your configured `min_order_size`
- Current BTC price
- Your maker/taker fee tier on Orderly
- Slippage during MARKET order execution

---

## Best Practices

### 1. Start with WebSocket Test
Run the WebSocket test first to verify connectivity before testing orders.

### 2. Use Minimum Order Sizes
Keep `min_order_size` as small as allowed to minimize costs.

### 3. Test During Low Volatility
Run tests during stable market conditions to avoid unexpected slippage.

### 4. Monitor Your Account
Keep the Orderly dashboard open to monitor orders and positions in real-time.

### 5. Review Logs
Both tests provide detailed logs. Review them to understand what happened.

### 6. Clean Up Manually If Needed
If tests fail mid-execution, manually check and cancel any open orders or positions.

---

## Advanced Usage

### Running Specific Tests

You can modify the test scripts to run only specific test scenarios by commenting out unwanted tests in the `run_all_tests()` method.

### Adjusting Test Parameters

Edit `test_config.yml` to customize:
- `websocket_test_duration`: How long to listen for messages
- `order_offset_percentage`: How far from market to place LIMIT orders
- `min_order_size`: Minimum order size for tests
- `leverage`: Leverage multiplier for positions

### Enabling Debug Logging

For more detailed output, change logging level in the scripts:

```python
logging.basicConfig(
    level=logging.DEBUG,  # Changed from INFO
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

---

## Support

If you encounter issues:

1. **Check Logs:** Review the detailed output for error messages
2. **Verify Configuration:** Double-check `test_config.yml`
3. **Test Connectivity:** Ensure you can access Orderly API
4. **Check Balance:** Verify sufficient USDC balance
5. **Review Orderly Status:** Check if Orderly Network is operational

For connector-specific issues, refer to:
- `Progress.md` - Implementation status
- `ORDERLY_CONNECTOR_IMPLEMENTATION_GUIDE.md` - Technical details
- Orderly Network documentation

---

## Security Notes

⚠️ **Important Security Considerations:**

1. **Never commit `test_config.yml`** - It contains your API keys
2. **Use separate API keys** - Create dedicated keys for testing
3. **Limit API key permissions** - Only grant necessary permissions
4. **Monitor your account** - Watch for unexpected activity
5. **Rotate keys regularly** - Change API keys periodically
6. **Use testnet first** - Consider testing on testnet before mainnet

---

## Next Steps

After successful testing:

1. ✅ WebSocket test passes → Connector can receive market data
2. ✅ Order lifecycle test passes → Connector is production-ready
3. 📊 Ready to use connector in Hummingbot strategies
4. 🚀 Deploy your trading bots with confidence!

---

**Last Updated:** 2025-10-29
**Connector Version:** v1.0
**Status:** Production Ready
