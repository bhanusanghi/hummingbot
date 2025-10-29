#!/usr/bin/env python3
"""
Orderly Network Order Lifecycle Live Test

This script tests the complete order lifecycle for the Orderly perpetual connector:
- LIMIT order: placement, status check, cancellation
- MARKET order: placement, fill tracking, position management
- Position management: opening, tracking, closing
- Funding payments: retrieval and tracking

Requirements:
- test_config.yml with valid Orderly credentials
- Sufficient USDC balance for test orders
- Leverage configured on Orderly (default 10x)

WARNING: This test places REAL orders on mainnet and will incur trading fees!

Usage:
    python test_orderly_order_lifecycle_live.py
"""

import asyncio
import json
import logging
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
import yaml

# Add hummingbot to path
sys.path.insert(0, str(Path(__file__).parent))

from hummingbot.connector.derivative.orderly_perpetual import orderly_perpetual_constants as CONSTANTS
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_auth import OrderlyPerpetualAuth
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_web_utils import (
    create_throttler,
    private_rest_url,
    public_rest_url,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OrderlyOrderLifecycleTester:
    """Comprehensive order lifecycle test suite for Orderly Network"""

    def __init__(self, config: Dict):
        self.config = config
        self.account_id = config['orderly']['account_id']
        self.api_key = config['orderly']['api_key']
        self.api_secret = config['orderly']['api_secret']
        self.domain = config['orderly']['domain']
        self.trading_pair = config['orderly']['trading_pair']
        self.leverage = config['orderly']['leverage']
        self.min_order_size = Decimal(str(config['orderly']['min_order_size']))
        self.order_offset_pct = Decimal(str(config['orderly']['order_offset_percentage']))

        # Convert trading pair format: BTC-USDC -> PERP_BTC_USDC
        base, quote = self.trading_pair.split('-')
        self.orderly_symbol = f"PERP_{base}_{quote}"

        # Test results tracking
        self.results = {
            'balance_check': False,
            'trading_rules_loaded': False,
            'leverage_set': False,
            'limit_order_placed': False,
            'limit_order_status_checked': False,
            'limit_order_cancelled': False,
            'market_order_placed': False,
            'market_order_filled': False,
            'position_opened': False,
            'position_tracked': False,
            'position_closed': False,
            'funding_payments_retrieved': False,
        }

        # Store order and position info
        self.limit_order_id = None
        self.limit_client_order_id = None
        self.market_order_id = None
        self.market_client_order_id = None
        self.close_order_id = None
        self.close_client_order_id = None
        self.current_position = None

        # Initialize components
        self.auth = OrderlyPerpetualAuth(
            account_id=self.account_id,
            orderly_key=self.api_key,
            orderly_secret=self.api_secret,
        )
        self.throttler = create_throttler()
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize_session(self):
        """Initialize aiohttp session"""
        self.session = aiohttp.ClientSession()

    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()

    async def rest_request(self, method: str, path: str, params: Optional[Dict] = None,
                           data: Optional[Dict] = None, is_private: bool = False) -> Dict:
        """Make REST API request"""
        url = private_rest_url(path, self.domain) if is_private else public_rest_url(path, self.domain)

        headers = {
            "Content-Type": "application/json",
        }

        # Add authentication headers for private endpoints
        if is_private:
            auth_headers = self.auth.get_headers(method, path, data or params)
            headers.update(auth_headers)

        # Make request
        async with self.session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=data if method in ["POST", "PUT"] else None,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            response_text = await response.text()

            if response.status >= 400:
                logger.error(f"API error: {response.status} - {response_text}")
                raise Exception(f"API request failed: {response.status} - {response_text}")

            try:
                return json.loads(response_text) if response_text else {}
            except json.JSONDecodeError:
                logger.error(f"Failed to parse response: {response_text}")
                raise

    async def run_all_tests(self):
        """Run all order lifecycle tests"""
        logger.info("=" * 80)
        logger.info("Starting Orderly Network Order Lifecycle Live Tests")
        logger.info("=" * 80)
        logger.info(f"Domain: {self.domain}")
        logger.info(f"Trading Pair: {self.trading_pair} ({self.orderly_symbol})")
        logger.info(f"Leverage: {self.leverage}x")
        logger.info(f"Min Order Size: {self.min_order_size}")
        logger.info("=" * 80)
        logger.warning("\n⚠️  WARNING: This test will place REAL orders on mainnet!")
        logger.warning("⚠️  Trading fees will be incurred!")
        logger.info("=" * 80)

        try:
            # Initialize session
            await self.initialize_session()

            # Test 1: Check balance and trading rules
            await self.test_balance_and_trading_rules()

            # Test 2: Set leverage
            await self.test_set_leverage()

            # Test 3: LIMIT order lifecycle
            await self.test_limit_order_lifecycle()

            # Test 4: MARKET order and position management
            await self.test_market_order_and_position()

            # Test 5: Funding payments
            await self.test_funding_payments()

            # Print results
            self.print_test_results()

        except Exception as e:
            logger.error(f"Test suite failed with error: {e}", exc_info=True)
            raise
        finally:
            await self.close_session()

    async def test_balance_and_trading_rules(self):
        """Test balance retrieval and trading rules"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 1: Balance and Trading Rules")
        logger.info("=" * 80)

        try:
            # Check balance
            logger.info("Checking USDC balance...")
            balance_response = await self.rest_request(
                method="GET",
                path=CONSTANTS.ACCOUNT_HOLDING_URL,
                is_private=True
            )

            logger.debug(f"Balance response: {balance_response}")

            if balance_response.get('success'):
                holdings = balance_response.get('data', {}).get('holding', [])
                usdc_balance = next((h for h in holdings if h.get('token') == 'USDC'), None)

                if usdc_balance:
                    total = Decimal(str(usdc_balance.get('holding', 0)))
                    frozen = Decimal(str(usdc_balance.get('frozen', 0)))
                    available = total - frozen

                    logger.info(f"Total USDC Balance: {total}")
                    logger.info(f"Frozen: {frozen}")
                    logger.info(f"Available: {available}")

                    if total > 0:
                        self.results['balance_check'] = True
                        logger.info("✓ Balance check passed")
                    else:
                        logger.warning("⚠ No USDC balance found")
                else:
                    logger.warning("⚠ No USDC balance found in response")
            else:
                logger.error(f"✗ Balance check failed: {balance_response}")

            # Check trading rules
            logger.info(f"\nChecking trading rules for {self.orderly_symbol}...")
            rules_response = await self.rest_request(
                method="GET",
                path=CONSTANTS.TRADING_RULE_URL.format(symbol=self.orderly_symbol),
                is_private=False
            )

            logger.debug(f"Trading rules response: {rules_response}")

            if rules_response.get('success'):
                data = rules_response.get('data', {})
                logger.info(f"Base Tick: {data.get('base_tick')}")
                logger.info(f"Quote Tick: {data.get('quote_tick')}")
                logger.info(f"Min Notional: {data.get('min_notional')}")
                logger.info(f"Base Min: {data.get('base_min')}")
                logger.info(f"Base Max: {data.get('base_max')}")

                self.results['trading_rules_loaded'] = True
                logger.info("✓ Trading rules loaded successfully")
            else:
                logger.error(f"✗ Failed to load trading rules: {rules_response}")

        except Exception as e:
            logger.error(f"Balance and trading rules test failed: {e}", exc_info=True)
            raise

    async def test_set_leverage(self):
        """Test setting leverage"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 2: Set Leverage")
        logger.info("=" * 80)

        try:
            logger.info(f"Setting leverage to {self.leverage}x for {self.orderly_symbol}...")

            leverage_data = {
                "symbol": self.orderly_symbol,
                "leverage": self.leverage
            }

            response = await self.rest_request(
                method="POST",
                path=CONSTANTS.SET_LEVERAGE_URL,
                data=leverage_data,
                is_private=True
            )

            logger.debug(f"Leverage response: {response}")

            if response.get('success'):
                self.results['leverage_set'] = True
                logger.info(f"✓ Leverage set successfully")
            else:
                logger.warning(f"⚠ Leverage setting response: {response}")
                # Don't fail - leverage might already be set

        except Exception as e:
            logger.error(f"Set leverage test failed: {e}", exc_info=True)
            # Don't raise - leverage might already be set

    async def test_limit_order_lifecycle(self):
        """Test LIMIT order: place, check status, cancel"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 3: LIMIT Order Lifecycle")
        logger.info("=" * 80)

        try:
            # Get current price
            futures_response = await self.rest_request(
                method="GET",
                path=CONSTANTS.SYMBOL_INFO_URL.format(symbol=self.orderly_symbol),
                is_private=False
            )

            if not futures_response.get('success'):
                raise Exception(f"Failed to get current price: {futures_response}")

            data = futures_response.get('data', {})
            current_price = Decimal(str(data.get('mark_price', data.get('index_price', 0))))
            logger.info(f"Current price: {current_price}")

            # Calculate order price (offset from market to avoid fill)
            offset_price = current_price * (1 - self.order_offset_pct)
            # Round to reasonable precision
            offset_price = Decimal(str(round(float(offset_price), 1)))

            logger.info(f"\n--- Placing LIMIT BUY order ---")
            logger.info(f"Price: {offset_price} (offset {self.order_offset_pct * 100}% below market)")
            logger.info(f"Amount: {self.min_order_size}")

            # Place LIMIT order
            self.limit_client_order_id = f"test_limit_{uuid.uuid4().hex[:16]}"
            order_data = {
                "symbol": self.orderly_symbol,
                "client_order_id": self.limit_client_order_id,
                "order_type": "LIMIT",
                "order_price": float(offset_price),
                "order_quantity": float(self.min_order_size),
                "side": "BUY",
                "reduce_only": False
            }

            order_response = await self.rest_request(
                method="POST",
                path=CONSTANTS.CREATE_ORDER_URL,
                data=order_data,
                is_private=True
            )

            logger.debug(f"Order response: {order_response}")

            if order_response.get('success'):
                order_info = order_response.get('data', {})
                self.limit_order_id = order_info.get('order_id')
                logger.info(f"Order ID: {self.limit_order_id}")
                logger.info(f"Client Order ID: {self.limit_client_order_id}")

                self.results['limit_order_placed'] = True
                logger.info("✓ LIMIT order placed successfully")
            else:
                raise Exception(f"Order placement failed: {order_response}")

            # Check order status via REST API
            logger.info("\n--- Checking order status ---")
            await asyncio.sleep(2)

            status_response = await self.rest_request(
                method="GET",
                path=CONSTANTS.GET_ORDER_URL.format(order_id=self.limit_order_id),
                is_private=True
            )

            logger.debug(f"Status response: {status_response}")

            if status_response.get('success'):
                order_info = status_response.get('data', {})
                logger.info(f"Order State: {order_info.get('status')}")
                logger.info(f"Order Type: {order_info.get('type')}")
                logger.info(f"Executed Quantity: {order_info.get('executed_quantity', 0)}")

                self.results['limit_order_status_checked'] = True
                logger.info("✓ Order status checked successfully")
            else:
                logger.error(f"✗ Failed to get order status: {status_response}")

            # Cancel the order
            logger.info("\n--- Cancelling LIMIT order ---")
            await asyncio.sleep(2)

            cancel_response = await self.rest_request(
                method="DELETE",
                path=CONSTANTS.CANCEL_ORDER_URL,
                params={"order_id": self.limit_order_id, "symbol": self.orderly_symbol},
                is_private=True
            )

            logger.debug(f"Cancel response: {cancel_response}")

            if cancel_response.get('success'):
                self.results['limit_order_cancelled'] = True
                logger.info("✓ Order cancelled successfully")
            else:
                logger.warning(f"⚠ Cancel response: {cancel_response}")

        except Exception as e:
            logger.error(f"LIMIT order lifecycle test failed: {e}", exc_info=True)
            raise

    async def test_market_order_and_position(self):
        """Test MARKET order placement and position management"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 4: MARKET Order and Position Management")
        logger.info("=" * 80)

        try:
            # Place MARKET BUY order to open position
            logger.info("\n--- Placing MARKET BUY order to open LONG position ---")
            logger.info(f"Amount: {self.min_order_size}")

            self.market_client_order_id = f"test_market_{uuid.uuid4().hex[:16]}"
            order_data = {
                "symbol": self.orderly_symbol,
                "client_order_id": self.market_client_order_id,
                "order_type": "MARKET",
                "order_quantity": float(self.min_order_size),
                "side": "BUY",
                "reduce_only": False
            }

            order_response = await self.rest_request(
                method="POST",
                path=CONSTANTS.CREATE_ORDER_URL,
                data=order_data,
                is_private=True
            )

            logger.debug(f"Order response: {order_response}")

            if order_response.get('success'):
                order_info = order_response.get('data', {})
                self.market_order_id = order_info.get('order_id')
                logger.info(f"Order ID: {self.market_order_id}")

                self.results['market_order_placed'] = True
                logger.info("✓ MARKET order placed successfully")
            else:
                raise Exception(f"Market order placement failed: {order_response}")

            # Wait for fill
            logger.info("\n--- Waiting for order fill ---")
            timeout = 30
            start_time = time.time()
            filled = False

            while time.time() - start_time < timeout:
                await asyncio.sleep(2)

                status_response = await self.rest_request(
                    method="GET",
                    path=CONSTANTS.GET_ORDER_URL.format(order_id=self.market_order_id),
                    is_private=True
                )

                if status_response.get('success'):
                    order_info = status_response.get('data', {})
                    status = order_info.get('status')
                    executed_qty = Decimal(str(order_info.get('executed_quantity', 0)))

                    logger.info(f"Order status: {status}, Executed: {executed_qty}")

                    if status in ['FILLED', 'COMPLETED'] or executed_qty >= self.min_order_size:
                        filled = True
                        self.results['market_order_filled'] = True
                        logger.info(f"✓ Order filled!")
                        logger.info(f"  Executed quantity: {executed_qty}")
                        logger.info(f"  Average price: {order_info.get('average_executed_price', 0)}")
                        break

            if not filled:
                logger.warning("⚠ Order not filled within timeout")

            # Check position
            logger.info("\n--- Checking position ---")
            await asyncio.sleep(3)

            positions_response = await self.rest_request(
                method="GET",
                path=CONSTANTS.POSITIONS_URL,
                is_private=True
            )

            logger.debug(f"Positions response: {positions_response}")

            if positions_response.get('success'):
                positions_data = positions_response.get('data', {})
                rows = positions_data.get('rows', [])

                # Find our position
                position = next((p for p in rows if p.get('symbol') == self.orderly_symbol), None)

                if position:
                    position_qty = Decimal(str(position.get('position_qty', 0)))

                    if position_qty > 0:
                        self.results['position_opened'] = True
                        self.results['position_tracked'] = True
                        self.current_position = position

                        logger.info("✓ Position opened and tracked successfully")
                        logger.info(f"  Position side: LONG")
                        logger.info(f"  Amount: {position_qty}")
                        logger.info(f"  Entry price: {position.get('average_open_price', 0)}")
                        logger.info(f"  Unrealized PnL: {position.get('unrealized_pnl', 0)}")
                        logger.info(f"  Mark price: {position.get('mark_price', 0)}")
                    else:
                        logger.warning("⚠ Position quantity is 0")
                else:
                    logger.error("✗ No position found for symbol")
            else:
                logger.error(f"✗ Failed to get positions: {positions_response}")

            # Close position
            logger.info("\n--- Closing position with MARKET SELL order ---")
            await asyncio.sleep(2)

            if self.current_position:
                close_amount = abs(Decimal(str(self.current_position.get('position_qty', 0))))

                self.close_client_order_id = f"test_close_{uuid.uuid4().hex[:16]}"
                close_data = {
                    "symbol": self.orderly_symbol,
                    "client_order_id": self.close_client_order_id,
                    "order_type": "MARKET",
                    "order_quantity": float(close_amount),
                    "side": "SELL",
                    "reduce_only": True
                }

                close_response = await self.rest_request(
                    method="POST",
                    path=CONSTANTS.CREATE_ORDER_URL,
                    data=close_data,
                    is_private=True
                )

                if close_response.get('success'):
                    order_info = close_response.get('data', {})
                    self.close_order_id = order_info.get('order_id')
                    logger.info(f"Close order ID: {self.close_order_id}")

                    # Wait for position to close
                    timeout = 30
                    start_time = time.time()

                    while time.time() - start_time < timeout:
                        await asyncio.sleep(2)

                        positions_response = await self.rest_request(
                            method="GET",
                            path=CONSTANTS.POSITIONS_URL,
                            is_private=True
                        )

                        if positions_response.get('success'):
                            positions_data = positions_response.get('data', {})
                            rows = positions_data.get('rows', [])
                            position = next((p for p in rows if p.get('symbol') == self.orderly_symbol), None)

                            if not position or Decimal(str(position.get('position_qty', 0))) == 0:
                                self.results['position_closed'] = True
                                logger.info("✓ Position closed successfully")
                                break

                    if not self.results['position_closed']:
                        logger.warning("⚠ Position not closed within timeout")
                else:
                    logger.error(f"✗ Close order failed: {close_response}")

        except Exception as e:
            logger.error(f"MARKET order and position test failed: {e}", exc_info=True)
            raise

    async def test_funding_payments(self):
        """Test funding payment retrieval"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 5: Funding Payments")
        logger.info("=" * 80)

        try:
            logger.info("Fetching funding payment history...")

            # Fetch funding fee history
            funding_response = await self.rest_request(
                method="GET",
                path=CONSTANTS.FUNDING_FEE_HISTORY_URL,
                params={"symbol": self.orderly_symbol},
                is_private=True
            )

            logger.debug(f"Funding response: {funding_response}")

            if funding_response.get('success'):
                funding_data = funding_response.get('data', {})
                rows = funding_data.get('rows', [])

                if rows:
                    latest = rows[0]
                    logger.info(f"Latest funding payment:")
                    logger.info(f"  Time: {latest.get('created_time', 0)}")
                    logger.info(f"  Funding rate: {latest.get('funding_rate', 0)}")
                    logger.info(f"  Payment: {latest.get('funding_fee', 0)}")

                    self.results['funding_payments_retrieved'] = True
                    logger.info("✓ Funding payments retrieved successfully")
                else:
                    logger.info("No funding payments found (this is normal if no positions held during funding)")
                    self.results['funding_payments_retrieved'] = True
            else:
                logger.error(f"✗ Failed to get funding payments: {funding_response}")

        except Exception as e:
            logger.error(f"Funding payments test failed: {e}", exc_info=True)
            # Don't raise - funding might not have occurred yet

    def print_test_results(self):
        """Print final test results"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST RESULTS SUMMARY")
        logger.info("=" * 80)

        passed = sum(1 for v in self.results.values() if v)
        total = len(self.results)

        logger.info(f"\nTests Passed: {passed}/{total}\n")

        for test_name, result in self.results.items():
            status = "✓ PASS" if result else "✗ FAIL"
            logger.info(f"  {status} - {test_name}")

        logger.info("\nOrder IDs:")
        logger.info(f"  LIMIT order: {self.limit_order_id}")
        logger.info(f"  MARKET order: {self.market_order_id}")
        logger.info(f"  Close order: {self.close_order_id}")

        logger.info("\n" + "=" * 80)


def load_config():
    """Load test configuration from YAML file"""
    config_path = Path(__file__).parent / "test_config.yml"

    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        logger.error("Please copy test_config.yml.example to test_config.yml and fill in your credentials")
        sys.exit(1)

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    # Validate required fields
    required_fields = ['account_id', 'api_key', 'api_secret', 'domain', 'trading_pair', 'leverage', 'min_order_size']
    for field in required_fields:
        if not config['orderly'].get(field):
            logger.error(f"Missing required field in config: orderly.{field}")
            sys.exit(1)

    return config


def main():
    """Main entry point"""
    # Load configuration
    config = load_config()

    # Ask for confirmation
    logger.warning("\n" + "=" * 80)
    logger.warning("⚠️  WARNING: This test will place REAL orders on Orderly mainnet!")
    logger.warning("⚠️  Trading fees will be incurred!")
    logger.warning("=" * 80)

    confirmation = input("\nType 'YES' to proceed with live trading tests: ")
    if confirmation.strip().upper() != 'YES':
        logger.info("Test cancelled by user")
        sys.exit(0)

    # Create and run tester
    tester = OrderlyOrderLifecycleTester(config)

    # Run tests
    asyncio.run(tester.run_all_tests())


if __name__ == "__main__":
    main()
