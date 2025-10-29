"""
Test script for Orderly Network authentication

This script tests the authentication implementation without requiring full connector setup.

Usage:
    python test_orderly_auth.py

Prerequisites:
    - Set environment variables OR edit the credentials section below:
        export ORDERLY_ACCOUNT_ID="0x..."
        export ORDERLY_KEY="ed25519:..."
        export ORDERLY_SECRET="ed25519:..."
"""

import asyncio
import os
import sys
from decimal import Decimal

# Add Hummingbot to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from hummingbot.connector.derivative.orderly_perpetual import orderly_perpetual_constants as CONSTANTS
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_auth import OrderlyPerpetualAuth
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_web_utils import (
    build_api_factory,
    create_throttler,
    public_rest_url,
)
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest

# =============================================================================
# CONFIGURATION - Edit these or use environment variables
# =============================================================================

# Option 1: Set environment variables
# ORDERLY_ACCOUNT_ID = os.getenv("ORDERLY_ACCOUNT_ID", "")
# ORDERLY_KEY = os.getenv("ORDERLY_KEY", "")
# ORDERLY_SECRET = os.getenv("ORDERLY_SECRET", "")


# Option 2: Hardcode test credentials (NOT RECOMMENDED for production)
# Uncomment and fill these if not using environment variables
# ORDERLY_ACCOUNT_ID = "0x1234..."
# ORDERLY_KEY = "ed25519:ABC..."
# ORDERLY_SECRET = "ed25519:123..."

# Test against testnet or mainnet
USE_TESTNET = False
DOMAIN = CONSTANTS.TESTNET_DOMAIN if USE_TESTNET else CONSTANTS.DOMAIN


# =============================================================================
# TEST FUNCTIONS
# =============================================================================

def test_auth_initialization():
    """Test 1: Verify auth class initialization"""
    print("\n" + "=" * 80)
    print("TEST 1: Authentication Initialization")
    print("=" * 80)

    try:
        auth = OrderlyPerpetualAuth(
            account_id=ORDERLY_ACCOUNT_ID,
            orderly_key=ORDERLY_KEY,
            orderly_secret=ORDERLY_SECRET,
        )
        print("✅ Auth object created successfully")
        print(f"   Account ID: {auth._account_id[:10]}...")
        print(f"   Orderly Key: {auth._orderly_key[:20]}...")
        return auth
    except Exception as e:
        print(f"❌ Failed to create auth object: {e}")
        return None


def test_signature_generation(auth):
    """Test 2: Verify signature generation"""
    print("\n" + "=" * 80)
    print("TEST 2: Signature Generation")
    print("=" * 80)

    try:
        timestamp = auth._get_timestamp()
        print(f"   Timestamp: {timestamp}")

        # Test GET request signature
        signature_get = auth.generate_signature(
            timestamp=timestamp,
            method="GET",
            path="/v1/client/info",
            params=None
        )
        print(f"✅ GET signature generated: {signature_get[:30]}...")

        # Test POST request signature
        params = {
            "symbol": "PERP_BTC_USDC",
            "order_type": "LIMIT",
            "side": "BUY",
            "order_price": 50000.0,
            "order_quantity": 0.1
        }
        signature_post = auth.generate_signature(
            timestamp=timestamp,
            method="POST",
            path="/v1/order",
            params=params
        )
        print(f"✅ POST signature generated: {signature_post[:30]}...")

        return True
    except Exception as e:
        print(f"❌ Signature generation failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_header_generation(auth):
    """Test 3: Verify authentication headers"""
    print("\n" + "=" * 80)
    print("TEST 3: Authentication Headers")
    print("=" * 80)

    try:
        headers = auth.get_headers(
            method="GET",
            path="/v1/client/info",
            params=None
        )

        print("✅ Headers generated:")
        for key, value in headers.items():
            if key == "orderly-signature":
                print(f"   {key}: {value[:30]}...")
            else:
                print(f"   {key}: {value}")

        # Verify all required headers are present
        required_headers = ["orderly-account-id", "orderly-key", "orderly-signature", "orderly-timestamp"]
        missing = [h for h in required_headers if h not in headers]

        if missing:
            print(f"❌ Missing headers: {missing}")
            return False

        print("✅ All required headers present")
        return True
    except Exception as e:
        print(f"❌ Header generation failed: {e}")
        return False


async def test_public_endpoint():
    """Test 4: Test public endpoint (no auth required)"""
    print("\n" + "=" * 80)
    print("TEST 4: Public Endpoint (GET /v1/public/futures)")
    print("=" * 80)

    try:
        # Create web assistant without auth
        throttler = create_throttler()
        api_factory = build_api_factory(throttler=throttler, auth=None)
        rest_assistant = await api_factory.get_rest_assistant()

        # Make request to public endpoint
        url = public_rest_url(CONSTANTS.EXCHANGE_INFO_URL, domain=DOMAIN)
        print(f"   URL: {url}")

        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.EXCHANGE_INFO_URL,
            method=RESTMethod.GET,
        )

        if isinstance(response, dict) and "data" in response:
            data = response["data"]
            if "rows" in data:
                symbols = data["rows"]
                print(f"✅ Successfully fetched {len(symbols)} trading pairs")
                print(f"   Sample symbols: {[s['symbol'] for s in symbols[:3]]}")
            else:
                print(f"✅ Response received: {list(response.keys())}")
        else:
            print(f"✅ Response received: {type(response)}")

        return True
    except Exception as e:
        print(f"❌ Public endpoint test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_authenticated_endpoint(auth):
    """Test 5: Test authenticated endpoint"""
    print("\n" + "=" * 80)
    print("TEST 5: Authenticated Endpoint (GET /v1/client/info)")
    print("=" * 80)

    try:
        # Create web assistant with auth
        throttler = create_throttler()
        api_factory = build_api_factory(throttler=throttler, auth=auth)
        rest_assistant = await api_factory.get_rest_assistant()

        # Make authenticated request
        url = public_rest_url(CONSTANTS.ACCOUNT_INFO_URL, domain=DOMAIN)
        print(f"   URL: {url}")

        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.ACCOUNT_INFO_URL,
            method=RESTMethod.GET,
            is_auth_required=True,
        )

        if isinstance(response, dict):
            if "success" in response and response["success"]:
                print("✅ Authentication successful!")
                if "data" in response:
                    data = response["data"]
                    print(f"   Account ID: {data.get('account_id', 'N/A')[:10]}...")
                    print(f"   Total Collateral: {data.get('total_collateral', 'N/A')}")
                    print(f"   Free Collateral: {data.get('free_collateral', 'N/A')}")
            elif "code" in response:
                print(f"❌ API Error: Code {response['code']}")
                print(f"   Message: {response.get('message', 'N/A')}")
                return False
            else:
                print(f"✅ Response received: {list(response.keys())}")
        else:
            print(f"⚠️  Unexpected response type: {type(response)}")
            return False

        return True
    except Exception as e:
        print(f"❌ Authenticated endpoint test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_balance_endpoint(auth):
    """Test 6: Test balance endpoint"""
    print("\n" + "=" * 80)
    print("TEST 6: Balance Endpoint (GET /v1/client/holding)")
    print("=" * 80)

    try:
        throttler = create_throttler()
        api_factory = build_api_factory(throttler=throttler, auth=auth)
        rest_assistant = await api_factory.get_rest_assistant()

        url = public_rest_url(CONSTANTS.ACCOUNT_HOLDING_URL, domain=DOMAIN)
        print(f"   URL: {url}")

        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.ACCOUNT_HOLDING_URL,
            method=RESTMethod.GET,
            is_auth_required=True,
        )

        if isinstance(response, dict) and "success" in response:
            if response["success"]:
                print("✅ Balance fetch successful!")
                if "data" in response:
                    holdings = response["data"].get("holding", [])
                    print(f"   Total holdings: {len(holdings)}")
                    for holding in holdings:
                        token = holding.get("token", "?")
                        amount = holding.get("holding", 0)
                        frozen = holding.get("frozen", 0)
                        available = float(amount) - float(frozen)
                        print(f"   {token}: {amount} (available: {available})")
            else:
                print(f"❌ API Error: {response.get('message', 'Unknown error')}")
                return False
        else:
            print(f"✅ Response received: {type(response)}")

        return True
    except Exception as e:
        print(f"❌ Balance endpoint test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_orderbook_endpoint(auth):
    """Test 7: Test orderbook endpoint (requires authentication)"""
    print("\n" + "=" * 80)
    print("TEST 7: Orderbook Endpoint (GET /v1/orderbook/PERP_BTC_USDC)")
    print("=" * 80)

    try:
        throttler = create_throttler()
        api_factory = build_api_factory(throttler=throttler, auth=auth)
        rest_assistant = await api_factory.get_rest_assistant()

        # Test symbol
        symbol = "PERP_BTC_USDC"
        path = CONSTANTS.ORDERBOOK_SNAPSHOT_URL.format(symbol=symbol)
        url = public_rest_url(path, domain=DOMAIN)
        print(f"   URL: {url}")

        response = await rest_assistant.execute_request(
            url=url,
            throttler_limit_id=CONSTANTS.ORDERBOOK_SNAPSHOT_URL,
            method=RESTMethod.GET,
            is_auth_required=True,
        )

        if isinstance(response, dict) and "data" in response:
            data = response["data"]
            asks = data.get("asks", [])
            bids = data.get("bids", [])
            print(f"✅ Orderbook fetched successfully")
            print(f"   Asks: {len(asks)} levels")
            if asks:
                print(f"   Best Ask: {asks[0]}")
            print(f"   Bids: {len(bids)} levels")
            if bids:
                print(f"   Best Bid: {bids[0]}")
        else:
            print(f"⚠️  Unexpected response format")
            print(f"   Response: {response}")

        return True
    except Exception as e:
        print(f"❌ Orderbook endpoint test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# =============================================================================
# MAIN TEST RUNNER
# =============================================================================

async def run_tests():
    """Run all authentication tests"""
    print("\n" + "=" * 80)
    print("🔐 ORDERLY NETWORK AUTHENTICATION TEST SUITE")
    print("=" * 80)
    print(f"Network: {'TESTNET' if USE_TESTNET else 'MAINNET'}")
    print(f"Domain: {DOMAIN}")
    print("=" * 80)

    # Check credentials
    if not ORDERLY_ACCOUNT_ID or not ORDERLY_KEY or not ORDERLY_SECRET:
        print("\n❌ ERROR: Missing credentials!")
        print("\nPlease set one of the following:")
        print("1. Environment variables:")
        print("   export ORDERLY_ACCOUNT_ID='0x...'")
        print("   export ORDERLY_KEY='ed25519:...'")
        print("   export ORDERLY_SECRET='ed25519:...'")
        print("\n2. Edit this script and hardcode credentials (lines 30-32)")
        return

    # Run tests
    results = {}

    # Test 1: Initialization
    auth = test_auth_initialization()
    results["initialization"] = auth is not None

    if not auth:
        print("\n❌ Cannot proceed without valid auth object")
        return

    # Test 2: Signature generation
    results["signature"] = test_signature_generation(auth)

    # Test 3: Header generation
    results["headers"] = test_header_generation(auth)

    # Test 4: Public endpoint (no auth)
    results["public_endpoint"] = await test_public_endpoint()

    # Test 5: Authenticated endpoint
    results["authenticated_endpoint"] = await test_authenticated_endpoint(auth)

    # Test 6: Balance endpoint
    results["balance_endpoint"] = await test_balance_endpoint(auth)

    # Test 7: Orderbook endpoint
    results["orderbook_endpoint"] = await test_orderbook_endpoint(auth)

    # Summary
    print("\n" + "=" * 80)
    print("📊 TEST SUMMARY")
    print("=" * 80)

    passed = sum(results.values())
    total = len(results)

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")

    print("=" * 80)
    print(f"Result: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed! Authentication is working correctly.")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please check the errors above.")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(run_tests())
