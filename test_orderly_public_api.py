#!/usr/bin/env python
"""
Test Orderly Network Public API Endpoints

This script tests all public REST API endpoints that don't require authentication.
Run this to verify the Orderly API is accessible and returns expected data.

Usage:
    python test_orderly_public_api.py [--testnet]

Arguments:
    --testnet: Use testnet API instead of mainnet (default: mainnet)
"""

import argparse
import asyncio
import json
import sys
from typing import Any, Dict

import aiohttp


class OrderlyPublicAPITester:
    """Test suite for Orderly Network public API endpoints"""

    def __init__(self, use_testnet: bool = False):
        self.base_url = (
            "https://testnet-api.orderly.org" if use_testnet else "https://api.orderly.org"
        )
        self.test_symbol = "PERP_BTC_USDC"  # Most liquid pair for testing
        self.results = {}

    async def make_request(self, endpoint: str, method: str = "GET") -> Dict[str, Any]:
        """
        Make HTTP request to Orderly API.

        Args:
            endpoint: API endpoint path
            method: HTTP method

        Returns:
            Response JSON
        """
        url = f"{self.base_url}{endpoint}"

        async with aiohttp.ClientSession() as session:
            async with session.request(method, url) as response:
                return await response.json()

    async def test_system_info(self) -> bool:
        """
        Test GET /v1/public/system_info

        Expected response:
        {
            "success": true,
            "data": {
                "status": 0,  # 0 = operational, 2 = maintenance
                "msg": "",
                "scheduled_maintenance": {...}
            }
        }
        """
        print("\n[1/6] Testing System Info...")
        try:
            response = await self.make_request("/v1/public/system_info")

            if not response.get("success"):
                print(f"  ✗ FAILED: {response}")
                return False

            data = response.get("data", {})
            status = data.get("status")

            print(f"  ✓ SUCCESS")
            print(f"    Status: {status} (0=operational, 2=maintenance)")
            print(f"    Message: {data.get('msg', 'N/A')}")

            self.results["system_info"] = response
            return status == 0

        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            return False

    async def test_futures_info(self) -> bool:
        """
        Test GET /v1/public/futures

        Returns market info for all perpetual contracts.
        Expected response includes symbol, index_price, mark_price, funding rates, etc.
        """
        print("\n[2/6] Testing Futures Info (All Markets)...")
        try:
            response = await self.make_request("/v1/public/futures")

            if not response.get("success"):
                print(f"  ✗ FAILED: {response}")
                return False

            data = response.get("data", {})
            rows = data.get("rows", [])

            if not rows:
                print("  ✗ FAILED: No market data returned")
                return False

            print(f"  ✓ SUCCESS")
            print(f"    Found {len(rows)} markets")

            # Show first 3 markets as sample
            print(f"    Sample markets:")
            for row in rows[:3]:
                symbol = row.get("symbol")
                mark_price = row.get("mark_price")
                funding = row.get("est_funding_rate")
                print(f"      - {symbol}: Mark=${mark_price}, Funding={funding}")

            self.results["futures_info"] = response
            return True

        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            return False

    async def test_trading_rules(self) -> bool:
        """
        Test GET /v1/public/info

        Returns trading rules for all symbols (tick sizes, min/max, etc).
        """
        print("\n[3/6] Testing Trading Rules...")
        try:
            response = await self.make_request("/v1/public/info")

            if not response.get("success"):
                print(f"  ✗ FAILED: {response}")
                return False

            data = response.get("data", {})
            rows = data.get("rows", [])

            if not rows:
                print("  ✗ FAILED: No trading rules returned")
                return False

            print(f"  ✓ SUCCESS")
            print(f"    Found rules for {len(rows)} symbols")

            # Show sample trading rule
            if rows:
                sample = rows[0]
                symbol = sample.get("symbol")
                base_tick = sample.get("base_tick")
                quote_tick = sample.get("quote_tick")
                min_notional = sample.get("min_notional")
                print(f"    Sample ({symbol}):")
                print(f"      Base tick: {base_tick}")
                print(f"      Quote tick: {quote_tick}")
                print(f"      Min notional: {min_notional}")

            self.results["trading_rules"] = response
            return True

        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            return False

    async def test_funding_rate(self) -> bool:
        """
        Test GET /v1/public/funding_rate/{symbol}

        Returns funding rate information for a specific symbol.
        """
        print(f"\n[4/6] Testing Funding Rate ({self.test_symbol})...")
        try:
            endpoint = f"/v1/public/funding_rate/{self.test_symbol}"
            response = await self.make_request(endpoint)

            if not response.get("success"):
                print(f"  ✗ FAILED: {response}")
                return False

            data = response.get("data", {})

            print(f"  ✓ SUCCESS")
            print(f"    Symbol: {data.get('symbol')}")
            print(f"    Est funding rate: {data.get('est_funding_rate')}")
            print(f"    Last funding rate: {data.get('last_funding_rate')}")
            print(f"    Next funding time: {data.get('next_funding_time')}")

            self.results["funding_rate"] = response
            return True

        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            return False

    async def test_market_trades(self) -> bool:
        """
        Test GET /v1/public/market_trades

        Returns recent public trades for a symbol.
        """
        print(f"\n[5/6] Testing Market Trades ({self.test_symbol})...")
        try:
            endpoint = f"/v1/public/market_trades?symbol={self.test_symbol}&limit=5"
            response = await self.make_request(endpoint)

            if not response.get("success"):
                print(f"  ✗ FAILED: {response}")
                return False

            data = response.get("data", {})
            rows = data.get("rows", [])

            print(f"  ✓ SUCCESS")
            print(f"    Found {len(rows)} recent trades")

            if rows:
                print(f"    Latest trade:")
                latest = rows[0]
                print(f"      Price: {latest.get('price')}")
                print(f"      Quantity: {latest.get('quantity')}")
                print(f"      Side: {latest.get('side')}")

            self.results["market_trades"] = response
            return True

        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            return False

    async def test_supported_tokens(self) -> bool:
        """
        Test GET /v1/public/token

        Returns list of supported collateral tokens.
        """
        print("\n[6/6] Testing Supported Tokens...")
        try:
            response = await self.make_request("/v1/public/token")

            if not response.get("success"):
                print(f"  ✗ FAILED: {response}")
                return False

            data = response.get("data", {})
            rows = data.get("rows", [])

            print(f"  ✓ SUCCESS")
            print(f"    Found {len(rows)} supported tokens")

            if rows:
                print(f"    Tokens:")
                for token in rows:
                    print(f"      - {token.get('token')}: {token.get('token_account_id')}")

            self.results["supported_tokens"] = response
            return True

        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            return False

    async def run_all_tests(self) -> bool:
        """
        Run all public API tests.

        Returns:
            True if all tests passed, False otherwise
        """
        print("=" * 80)
        print("ORDERLY NETWORK PUBLIC API TEST SUITE")
        print("=" * 80)
        print(f"Testing against: {self.base_url}")

        tests = [
            self.test_system_info(),
            self.test_futures_info(),
            self.test_trading_rules(),
            self.test_funding_rate(),
            self.test_market_trades(),
            self.test_supported_tokens(),
        ]

        results = await asyncio.gather(*tests, return_exceptions=False)

        # Summary
        print("\n" + "=" * 80)
        print("TEST SUMMARY")
        print("=" * 80)

        passed = sum(results)
        total = len(results)

        test_names = [
            "System Info",
            "Futures Info",
            "Trading Rules",
            "Funding Rate",
            "Market Trades",
            "Supported Tokens",
        ]

        for name, result in zip(test_names, results):
            status = "✓ PASS" if result else "✗ FAIL"
            print(f"  {status} - {name}")

        print(f"\nTotal: {passed}/{total} tests passed")

        if passed == total:
            print("\n✓ ALL TESTS PASSED! The Orderly API is accessible and working correctly.")
        else:
            print(f"\n✗ {total - passed} test(s) failed. Check the output above for details.")

        return passed == total

    def save_results(self, filename: str = "orderly_api_test_results.json"):
        """
        Save test results to JSON file.

        Args:
            filename: Output filename
        """
        with open(filename, "w") as f:
            json.dump(self.results, f, indent=2)
        print(f"\nResults saved to: {filename}")


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Test Orderly Network public API endpoints"
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Use testnet API instead of mainnet",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save test results to JSON file",
    )

    args = parser.parse_args()

    tester = OrderlyPublicAPITester(use_testnet=args.testnet)
    success = await tester.run_all_tests()

    if args.save:
        tester.save_results()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
