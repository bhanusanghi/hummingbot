#!/usr/bin/env python3
"""
Orderly Network WebSocket Live Test

This script tests all WebSocket functionality for the Orderly perpetual connector:
- Public WebSocket: order book, trades, funding rates
- Private WebSocket: authentication, order updates, position updates, balance updates
- Connection management: reconnection, heartbeat

Requirements:
- test_config.yml with valid Orderly credentials
- Sufficient balance for test orders

Usage:
    python test_orderly_websocket_live.py
"""

import asyncio
import logging
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, List

import yaml

# Add hummingbot to path
sys.path.insert(0, str(Path(__file__).parent))

from hummingbot.connector.derivative.orderly_perpetual import orderly_perpetual_constants as CONSTANTS
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_api_order_book_data_source import (
    OrderlyPerpetualAPIOrderBookDataSource,
)
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_auth import OrderlyPerpetualAuth
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_derivative import (
    OrderlyPerpetualDerivative,
)
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_user_stream_data_source import (
    OrderlyPerpetualUserStreamDataSource,
)
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_web_utils import (
    build_api_factory,
    create_throttler,
    wss_url,
)
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.web_assistant.ws_assistant import WSAssistant

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OrderlyWebSocketTester:
    """Comprehensive WebSocket test suite for Orderly Network"""

    def __init__(self, config: Dict):
        self.config = config
        self.account_id = config['orderly']['account_id']
        self.api_key = config['orderly']['api_key']
        self.api_secret = config['orderly']['api_secret']
        self.domain = config['orderly']['domain']
        self.trading_pair = config['orderly']['trading_pair']
        self.test_duration = config['orderly']['websocket_test_duration']

        # Test results tracking
        self.results = {
            'public_ws_connection': False,
            'public_orderbook_received': False,
            'public_trades_received': False,
            'private_ws_connection': False,
            'private_ws_authenticated': False,
            'private_order_updates_received': False,
            'private_position_updates_received': False,
            'private_balance_updates_received': False,
            'reconnection_successful': False,
            'heartbeat_working': False,
        }

        # Message counters
        self.message_counts = {
            'orderbook_updates': 0,
            'trades': 0,
            'order_updates': 0,
            'position_updates': 0,
            'balance_updates': 0,
        }

        # Initialize components
        self.auth = OrderlyPerpetualAuth(
            account_id=self.account_id,
            orderly_key=self.api_key,
            orderly_secret=self.api_secret,
        )
        self.throttler = create_throttler()
        self.api_factory = build_api_factory(throttler=self.throttler, auth=self.auth)

    async def run_all_tests(self):
        """Run all WebSocket tests"""
        logger.info("=" * 80)
        logger.info("Starting Orderly Network WebSocket Live Tests")
        logger.info("=" * 80)
        logger.info(f"Domain: {self.domain}")
        logger.info(f"Trading Pair: {self.trading_pair}")
        logger.info(f"Test Duration: {self.test_duration} seconds")
        logger.info("=" * 80)

        try:
            # Test 1: Public WebSocket
            await self.test_public_websocket()

            # Test 2: Private WebSocket
            await self.test_private_websocket()

            # Test 3: Reconnection
            await self.test_reconnection()

            # Print results
            self.print_test_results()

        except Exception as e:
            logger.error(f"Test suite failed with error: {e}", exc_info=True)
            raise

    async def test_public_websocket(self):
        """Test public WebSocket: connection, orderbook, trades"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 1: Public WebSocket")
        logger.info("=" * 80)

        try:
            # Connect to public WebSocket
            ws_url = wss_url(endpoint_type="public", domain=self.domain)
            logger.info(f"Connecting to public WebSocket: {ws_url}")

            ws: WSAssistant = await self.api_factory.get_ws_assistant()
            await ws.connect(ws_url=ws_url, ping_timeout=CONSTANTS.HEARTBEAT_TIME_INTERVAL)

            self.results['public_ws_connection'] = True
            logger.info("✓ Public WebSocket connected successfully")

            # Subscribe to orderbook and trades
            orderly_symbol = self.trading_pair.replace("-", "_")
            orderly_symbol = f"PERP_{orderly_symbol}"

            # Subscribe to orderbook
            orderbook_subscription = {
                "id": "orderbook_sub",
                "event": "subscribe",
                "topic": f"{orderly_symbol}@orderbook"
            }
            await ws.send(orderbook_subscription)
            logger.info(f"Subscribed to orderbook: {orderly_symbol}@orderbook")

            # Subscribe to trades
            trades_subscription = {
                "id": "trades_sub",
                "event": "subscribe",
                "topic": f"{orderly_symbol}@trade"
            }
            await ws.send(trades_subscription)
            logger.info(f"Subscribed to trades: {orderly_symbol}@trade")

            # Listen for messages for specified duration
            logger.info(f"Listening for messages for {self.test_duration} seconds...")
            start_time = time.time()
            timeout = self.test_duration

            while time.time() - start_time < timeout:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5.0)

                    if isinstance(msg, dict):
                        # Check for subscription confirmations
                        if msg.get('event') == 'subscribe':
                            logger.info(f"Subscription confirmed: {msg.get('id')}")

                        # Check for orderbook updates
                        if 'orderbook' in msg.get('topic', ''):
                            self.results['public_orderbook_received'] = True
                            self.message_counts['orderbook_updates'] += 1
                            if self.message_counts['orderbook_updates'] == 1:
                                logger.info("✓ First orderbook update received")
                                logger.debug(f"Orderbook data: {msg}")

                        # Check for trade updates
                        if 'trade' in msg.get('topic', ''):
                            self.results['public_trades_received'] = True
                            self.message_counts['trades'] += 1
                            if self.message_counts['trades'] == 1:
                                logger.info("✓ First trade update received")
                                logger.debug(f"Trade data: {msg}")

                        # Check for heartbeat/ping
                        if msg.get('event') == 'ping':
                            self.results['heartbeat_working'] = True
                            logger.info("✓ Heartbeat ping received")
                            # Send pong response
                            await ws.send({"event": "pong"})
                            logger.debug("Sent pong response")

                except asyncio.TimeoutError:
                    continue

            # Close connection
            await ws.disconnect()
            logger.info("Public WebSocket disconnected")

            # Summary
            logger.info("\nPublic WebSocket Test Summary:")
            logger.info(f"  Orderbook updates received: {self.message_counts['orderbook_updates']}")
            logger.info(f"  Trade updates received: {self.message_counts['trades']}")

        except Exception as e:
            logger.error(f"Public WebSocket test failed: {e}", exc_info=True)
            raise

    async def test_private_websocket(self):
        """Test private WebSocket: authentication, order updates, position updates, balance updates"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 2: Private WebSocket")
        logger.info("=" * 80)

        try:
            # Connect to private WebSocket
            ws_url = wss_url(
                endpoint_type="private",
                domain=self.domain,
                account_id=self.account_id
            )
            logger.info(f"Connecting to private WebSocket: {ws_url}")

            ws: WSAssistant = await self.api_factory.get_ws_assistant()
            await ws.connect(ws_url=ws_url, ping_timeout=CONSTANTS.HEARTBEAT_TIME_INTERVAL)

            self.results['private_ws_connection'] = True
            logger.info("✓ Private WebSocket connected successfully")

            # Authenticate
            logger.info("Authenticating private WebSocket...")
            auth_payload = await self.auth.get_ws_auth_payload()
            await ws.send(auth_payload)

            # Wait for auth response
            auth_response = await asyncio.wait_for(ws.receive(), timeout=10.0)
            logger.debug(f"Auth response: {auth_response}")

            if auth_response.get('event') == 'auth' and auth_response.get('success'):
                self.results['private_ws_authenticated'] = True
                logger.info("✓ Private WebSocket authenticated successfully")
            else:
                raise Exception(f"Authentication failed: {auth_response}")

            # Subscribe to private channels
            channels = [
                'executionreport',  # Order updates
                'position',  # Position updates
                'balance',  # Balance updates
            ]

            for channel in channels:
                subscription = {
                    "id": f"{channel}_sub",
                    "event": "subscribe",
                    "topic": channel
                }
                await ws.send(subscription)
                logger.info(f"Subscribed to {channel}")

            # Listen for messages
            logger.info(f"Listening for private messages for {self.test_duration} seconds...")
            logger.info("Note: Order/position/balance updates require trading activity")

            start_time = time.time()
            timeout = self.test_duration

            while time.time() - start_time < timeout:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5.0)

                    if isinstance(msg, dict):
                        # Check for subscription confirmations
                        if msg.get('event') == 'subscribe':
                            logger.info(f"Private subscription confirmed: {msg.get('id')}")

                        # Check for order updates
                        if msg.get('topic') == 'executionreport' or 'executionreport' in str(msg):
                            self.results['private_order_updates_received'] = True
                            self.message_counts['order_updates'] += 1
                            logger.info(f"✓ Order update received (count: {self.message_counts['order_updates']})")
                            logger.debug(f"Order update: {msg}")

                        # Check for position updates
                        if msg.get('topic') == 'position' or 'position' in str(msg):
                            self.results['private_position_updates_received'] = True
                            self.message_counts['position_updates'] += 1
                            logger.info(f"✓ Position update received (count: {self.message_counts['position_updates']})")
                            logger.debug(f"Position update: {msg}")

                        # Check for balance updates
                        if msg.get('topic') == 'balance' or 'balance' in str(msg):
                            self.results['private_balance_updates_received'] = True
                            self.message_counts['balance_updates'] += 1
                            logger.info(f"✓ Balance update received (count: {self.message_counts['balance_updates']})")
                            logger.debug(f"Balance update: {msg}")

                        # Check for heartbeat
                        if msg.get('event') == 'ping':
                            await ws.send({"event": "pong"})
                            logger.debug("Sent pong to private WebSocket")

                except asyncio.TimeoutError:
                    continue

            # Close connection
            await ws.disconnect()
            logger.info("Private WebSocket disconnected")

            # Summary
            logger.info("\nPrivate WebSocket Test Summary:")
            logger.info(f"  Order updates received: {self.message_counts['order_updates']}")
            logger.info(f"  Position updates received: {self.message_counts['position_updates']}")
            logger.info(f"  Balance updates received: {self.message_counts['balance_updates']}")

            if self.message_counts['order_updates'] == 0:
                logger.warning("  ⚠ No order updates received - this is expected if no trading occurred")
            if self.message_counts['position_updates'] == 0:
                logger.warning("  ⚠ No position updates received - this is expected if no positions changed")
            if self.message_counts['balance_updates'] == 0:
                logger.warning("  ⚠ No balance updates received - this is expected if no balance changed")

        except Exception as e:
            logger.error(f"Private WebSocket test failed: {e}", exc_info=True)
            raise

    async def test_reconnection(self):
        """Test WebSocket reconnection after forced disconnect"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 3: WebSocket Reconnection")
        logger.info("=" * 80)

        try:
            # Connect to public WebSocket
            ws_url = wss_url(endpoint_type="public", domain=self.domain)
            logger.info(f"Connecting to public WebSocket: {ws_url}")

            ws: WSAssistant = await self.api_factory.get_ws_assistant()
            await ws.connect(ws_url=ws_url, ping_timeout=CONSTANTS.HEARTBEAT_TIME_INTERVAL)
            logger.info("✓ Initial connection established")

            # Subscribe to orderbook
            orderly_symbol = self.trading_pair.replace("-", "_")
            orderly_symbol = f"PERP_{orderly_symbol}"
            subscription = {
                "id": "test_sub",
                "event": "subscribe",
                "topic": f"{orderly_symbol}@orderbook"
            }
            await ws.send(subscription)
            logger.info("Subscribed to orderbook")

            # Receive a few messages
            for _ in range(3):
                msg = await asyncio.wait_for(ws.receive(), timeout=10.0)
                logger.debug(f"Received: {msg}")

            # Force disconnect
            logger.info("Forcing disconnect...")
            await ws.disconnect()
            logger.info("✓ Disconnected")

            # Wait a moment
            await asyncio.sleep(2)

            # Reconnect
            logger.info("Attempting reconnection...")
            ws = await self.api_factory.get_ws_assistant()
            await ws.connect(ws_url=ws_url, ping_timeout=CONSTANTS.HEARTBEAT_TIME_INTERVAL)
            logger.info("✓ Reconnected successfully")

            # Re-subscribe
            await ws.send(subscription)
            logger.info("Re-subscribed to orderbook")

            # Verify we receive messages
            msg = await asyncio.wait_for(ws.receive(), timeout=10.0)
            logger.debug(f"Received after reconnection: {msg}")

            self.results['reconnection_successful'] = True
            logger.info("✓ Reconnection test passed")

            # Cleanup
            await ws.disconnect()

        except Exception as e:
            logger.error(f"Reconnection test failed: {e}", exc_info=True)
            raise

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

        logger.info("\nMessage Statistics:")
        logger.info(f"  Orderbook updates: {self.message_counts['orderbook_updates']}")
        logger.info(f"  Trade updates: {self.message_counts['trades']}")
        logger.info(f"  Order updates: {self.message_counts['order_updates']}")
        logger.info(f"  Position updates: {self.message_counts['position_updates']}")
        logger.info(f"  Balance updates: {self.message_counts['balance_updates']}")

        logger.info("\n" + "=" * 80)

        # Notes for user
        if not self.results['private_order_updates_received']:
            logger.info("\nNote: To test order updates, run test_orderly_order_lifecycle_live.py")
        if not self.results['private_position_updates_received']:
            logger.info("Note: Position updates require opening/closing positions")
        if not self.results['private_balance_updates_received']:
            logger.info("Note: Balance updates require trading activity")

        logger.info("=" * 80)


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
    required_fields = ['account_id', 'api_key', 'api_secret', 'domain', 'trading_pair']
    for field in required_fields:
        if not config['orderly'].get(field):
            logger.error(f"Missing required field in config: orderly.{field}")
            sys.exit(1)

    return config


def main():
    """Main entry point"""
    # Load configuration
    config = load_config()

    # Create and run tester
    tester = OrderlyWebSocketTester(config)

    # Run tests
    asyncio.run(tester.run_all_tests())


if __name__ == "__main__":
    main()
