# Orderly Network Connector - Live Tests

Quick reference for running live tests on the Orderly Network perpetual connector.

## 📋 Test Files

| File | Purpose | Cost | Duration |
|------|---------|------|----------|
| `test_orderly_websocket_live.py` | WebSocket connectivity & streaming | Free | ~60 sec |
| `test_orderly_order_lifecycle_live.py` | Order placement & position management | ~$0.10 | ~3 min |

## 🚀 Quick Start

### 1. Setup Configuration

```bash
# Copy example config
cp test_config.yml.example test_config.yml

# Edit with your credentials
nano test_config.yml
```

Fill in:
- `account_id`: Your Orderly account ID
- `api_key`: Your Orderly API public key
- `api_secret`: Your Orderly API private key

### 2. Run WebSocket Test (Free)

```bash
python test_orderly_websocket_live.py
```

Tests:
- ✅ Public WebSocket (orderbook, trades)
- ✅ Private WebSocket (authentication)
- ✅ Message streaming
- ✅ Reconnection handling

### 3. Run Order Lifecycle Test (⚠️ Real Money)

```bash
python test_orderly_order_lifecycle_live.py
```

Tests:
- ✅ Balance & trading rules
- ✅ LIMIT order placement & cancellation
- ✅ MARKET order execution
- ✅ Position opening & closing
- ✅ Funding payments

**Warning:** This places real orders and incurs trading fees (~$0.10 per run).

## 📊 Expected Results

### WebSocket Test - All Green ✅

```
Tests Passed: 10/10

  ✓ PASS - public_ws_connection
  ✓ PASS - public_orderbook_received
  ✓ PASS - public_trades_received
  ✓ PASS - private_ws_connection
  ✓ PASS - private_ws_authenticated
  ✓ PASS - private_order_updates_received
  ✓ PASS - private_position_updates_received
  ✓ PASS - private_balance_updates_received
  ✓ PASS - reconnection_successful
  ✓ PASS - heartbeat_working
```

### Order Lifecycle Test - All Green ✅

```
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
```

## ⚠️ Important Notes

1. **Security:** Never commit `test_config.yml` (it's in `.gitignore`)
2. **Cost:** Order lifecycle test incurs trading fees (~$0.10)
3. **Balance:** Ensure sufficient USDC balance before testing
4. **Mainnet:** Tests run on mainnet by default (use real money)
5. **Confirmation:** Order test asks for "YES" confirmation before proceeding

## 🔧 Configuration Options

Edit `test_config.yml`:

```yaml
orderly:
  account_id: "YOUR_ACCOUNT_ID"
  api_key: "YOUR_API_KEY"
  api_secret: "YOUR_API_SECRET"

  domain: "orderly_perpetual"  # or "orderly_perpetual_testnet"
  trading_pair: "BTC-USDC"
  leverage: 10

  websocket_test_duration: 60  # seconds
  order_offset_percentage: 0.05  # 5% below market
  min_order_size: 0.001  # BTC
```

## 🐛 Troubleshooting

| Issue | Solution |
|-------|----------|
| Config not found | Copy `test_config.yml.example` to `test_config.yml` |
| Auth failed | Verify credentials in Orderly dashboard |
| No balance | Fund account with USDC |
| Order too small | Increase `min_order_size` in config |
| Leverage error | Set leverage in Orderly dashboard first |

## 📚 Full Documentation

See `ORDERLY_TESTING_GUIDE.md` for:
- Detailed test descriptions
- Expected output examples
- Advanced configuration
- Cost calculations
- Security best practices

## ✅ What These Tests Validate

### Production Readiness Checklist

After passing all tests:

- [x] **API Connectivity** - Connector communicates with Orderly
- [x] **Authentication** - Ed25519 signatures working correctly
- [x] **WebSocket Streaming** - Real-time data flowing
- [x] **Order Placement** - Can create LIMIT and MARKET orders
- [x] **Order Management** - Can check status and cancel orders
- [x] **Position Tracking** - Positions opened, tracked, and closed
- [x] **Balance Management** - Account balances retrieved correctly
- [x] **Event Processing** - Order/position/balance events received
- [x] **Error Handling** - Graceful handling of edge cases
- [x] **Reconnection** - WebSocket reconnects after disconnect

**Result:** 🎉 Connector is production-ready for live trading!

## 🚦 Test Sequence

Recommended order:

1. **First:** Run WebSocket test (free, no risk)
   - Validates connectivity
   - Tests authentication
   - Verifies data streaming

2. **Second:** Run Order Lifecycle test (small cost, minimal risk)
   - Places minimum-sized orders
   - Uses offset pricing to avoid immediate fills on LIMIT
   - Closes positions immediately
   - Total cost: ~$0.10

3. **Third:** Deploy in Hummingbot strategies
   - Use in paper trading first
   - Start with small position sizes
   - Monitor closely during initial runs

## 📞 Support

For issues:
1. Check logs for detailed error messages
2. Review `ORDERLY_TESTING_GUIDE.md`
3. Verify configuration in `test_config.yml`
4. Ensure Orderly Network is operational
5. Check account balance and API permissions

---

**Last Updated:** 2025-10-29
**Status:** ✅ Ready for Testing
**Connector Version:** v1.0
