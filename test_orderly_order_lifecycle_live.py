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
import logging
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional

import yaml

# Add hummingbot to path
sys.path.insert(0, str(Path(__file__).parent))

from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_derivative import (
    OrderlyPerpetualDerivative,
)
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, PositionSide, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    BuyOrderCreatedEvent,
    MarketEvent,
    OrderCancelledEvent,
    OrderFilledEvent,
    SellOrderCompletedEvent,
    SellOrderCreatedEvent,
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

        # Test results tracking
        self.results = {
            'connector_initialization': False,
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
        self.market_order_id = None
        self.close_order_id = None
        self.current_position = None

        # Event tracking
        self.events_received = []

        # Initialize connector
        self.connector: Optional[OrderlyPerpetualDerivative] = None

    async def initialize_connector(self):
        """Initialize the Orderly connector"""
        logger.info("Initializing Orderly connector...")

        try:
            # Initialize connector directly without config system
            self.connector = OrderlyPerpetualDerivative(
                orderly_perpetual_api_key=self.api_key,
                orderly_perpetual_api_secret=self.api_secret,
                orderly_perpetual_account_id=self.account_id,
                trading_pairs=[self.trading_pair],
                trading_required=True,
                domain=self.domain,
            )

            # Set up event listeners
            self._setup_event_listeners()

            # Start connector
            await self.connector.start_network()
            await asyncio.sleep(2)  # Give it time to connect

            # Wait for ready
            timeout = 30
            start_time = time.time()
            while not self.connector.ready and time.time() - start_time < timeout:
                await asyncio.sleep(1)

            if not self.connector.ready:
                raise Exception("Connector failed to become ready within timeout")

            self.results['connector_initialization'] = True
            logger.info("✓ Connector initialized successfully")

            return True

        except Exception as e:
            logger.error(f"Failed to initialize connector: {e}", exc_info=True)
            return False

    def _setup_event_listeners(self):
        """Setup event listeners for order and position updates"""
        # Order created events
        self.connector.add_listener(
            MarketEvent.BuyOrderCreated,
            self._on_order_created
        )
        self.connector.add_listener(
            MarketEvent.SellOrderCreated,
            self._on_order_created
        )

        # Order filled events
        self.connector.add_listener(
            MarketEvent.OrderFilled,
            self._on_order_filled
        )

        # Order completed events
        self.connector.add_listener(
            MarketEvent.BuyOrderCompleted,
            self._on_order_completed
        )
        self.connector.add_listener(
            MarketEvent.SellOrderCompleted,
            self._on_order_completed
        )

        # Order cancelled event
        self.connector.add_listener(
            MarketEvent.OrderCancelled,
            self._on_order_cancelled
        )

    def _on_order_created(self, event):
        """Handle order created event"""
        logger.info(f"📨 Order Created Event: {event.order_id}")
        self.events_received.append(('order_created', event))

    def _on_order_filled(self, event: OrderFilledEvent):
        """Handle order filled event"""
        logger.info(f"📨 Order Filled Event: {event.order_id} - {event.amount} @ {event.price}")
        self.events_received.append(('order_filled', event))

    def _on_order_completed(self, event):
        """Handle order completed event"""
        logger.info(f"📨 Order Completed Event: {event.order_id}")
        self.events_received.append(('order_completed', event))

    def _on_order_cancelled(self, event: OrderCancelledEvent):
        """Handle order cancelled event"""
        logger.info(f"📨 Order Cancelled Event: {event.order_id}")
        self.events_received.append(('order_cancelled', event))

    async def run_all_tests(self):
        """Run all order lifecycle tests"""
        logger.info("=" * 80)
        logger.info("Starting Orderly Network Order Lifecycle Live Tests")
        logger.info("=" * 80)
        logger.info(f"Domain: {self.domain}")
        logger.info(f"Trading Pair: {self.trading_pair}")
        logger.info(f"Leverage: {self.leverage}x")
        logger.info(f"Min Order Size: {self.min_order_size}")
        logger.info("=" * 80)
        logger.warning("\n⚠️  WARNING: This test will place REAL orders on mainnet!")
        logger.warning("⚠️  Trading fees will be incurred!")
        logger.info("=" * 80)

        try:
            # Initialize connector
            if not await self.initialize_connector():
                raise Exception("Failed to initialize connector")

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
            # Stop connector
            if self.connector:
                await self.connector.stop_network()

    async def test_balance_and_trading_rules(self):
        """Test balance retrieval and trading rules"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 1: Balance and Trading Rules")
        logger.info("=" * 80)

        try:
            # Check balance
            logger.info("Checking USDC balance...")
            await self.connector._update_balances()

            available_balance = self.connector.available_balance("USDC")
            total_balance = self.connector.get_balance("USDC")

            logger.info(f"Total USDC Balance: {total_balance}")
            logger.info(f"Available USDC Balance: {available_balance}")

            if total_balance > 0:
                self.results['balance_check'] = True
                logger.info("✓ Balance check passed")
            else:
                logger.warning("⚠ No USDC balance found")

            # Check trading rules
            logger.info(f"\nChecking trading rules for {self.trading_pair}...")
            trading_rule = self.connector.trading_rules.get(self.trading_pair)

            if trading_rule:
                logger.info(f"Min Order Size: {trading_rule.min_order_size}")
                logger.info(f"Min Price Increment: {trading_rule.min_price_increment}")
                logger.info(f"Min Base Amount Increment: {trading_rule.min_base_amount_increment}")
                logger.info(f"Min Notional Size: {trading_rule.min_notional_size}")

                self.results['trading_rules_loaded'] = True
                logger.info("✓ Trading rules loaded successfully")
            else:
                logger.error(f"✗ No trading rules found for {self.trading_pair}")

        except Exception as e:
            logger.error(f"Balance and trading rules test failed: {e}", exc_info=True)
            raise

    async def test_set_leverage(self):
        """Test setting leverage"""
        logger.info("\n" + "=" * 80)
        logger.info("TEST 2: Set Leverage")
        logger.info("=" * 80)

        try:
            logger.info(f"Setting leverage to {self.leverage}x for {self.trading_pair}...")

            success, message = await self.connector._set_trading_pair_leverage(
                trading_pair=self.trading_pair,
                leverage=self.leverage
            )

            if success:
                self.results['leverage_set'] = True
                logger.info(f"✓ Leverage set successfully: {message}")
            else:
                logger.error(f"✗ Failed to set leverage: {message}")

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
            current_price = await self.connector._get_last_traded_price(self.trading_pair)
            logger.info(f"Current price: {current_price}")

            # Calculate order price (offset from market to avoid fill)
            offset_price = Decimal(str(current_price)) * (1 - self.order_offset_pct)
            offset_price = self.connector.quantize_order_price(self.trading_pair, offset_price)

            logger.info(f"\n--- Placing LIMIT BUY order ---")
            logger.info(f"Price: {offset_price} (offset {self.order_offset_pct * 100}% below market)")
            logger.info(f"Amount: {self.min_order_size}")

            # Place LIMIT order
            self.limit_order_id = self.connector.buy(
                trading_pair=self.trading_pair,
                amount=self.min_order_size,
                order_type=OrderType.LIMIT,
                price=offset_price,
            )

            logger.info(f"Order ID: {self.limit_order_id}")

            # Wait for order to be created
            await asyncio.sleep(3)

            # Check if order exists in tracking
            if self.limit_order_id in self.connector.in_flight_orders:
                self.results['limit_order_placed'] = True
                logger.info("✓ LIMIT order placed successfully")
            else:
                raise Exception("Order not found in tracking")

            # Check order status via REST API
            logger.info("\n--- Checking order status ---")
            await asyncio.sleep(2)

            tracked_order = self.connector.in_flight_orders.get(self.limit_order_id)
            if tracked_order:
                logger.info(f"Order State: {tracked_order.current_state}")
                logger.info(f"Exchange Order ID: {tracked_order.exchange_order_id}")

                self.results['limit_order_status_checked'] = True
                logger.info("✓ Order status checked successfully")
            else:
                logger.error("✗ Order not found in tracking")

            # Cancel the order
            logger.info("\n--- Cancelling LIMIT order ---")
            await asyncio.sleep(2)

            cancel_result = await self.connector._execute_cancel(
                trading_pair=self.trading_pair,
                order_id=self.limit_order_id
            )

            logger.info(f"Cancel result: {cancel_result}")

            # Wait for cancellation to process
            await asyncio.sleep(3)

            # Verify cancellation
            tracked_order = self.connector.in_flight_orders.get(self.limit_order_id)
            if tracked_order and tracked_order.current_state == OrderState.CANCELED:
                self.results['limit_order_cancelled'] = True
                logger.info("✓ Order cancelled successfully")
            elif self.limit_order_id not in self.connector.in_flight_orders:
                self.results['limit_order_cancelled'] = True
                logger.info("✓ Order cancelled and removed from tracking")
            else:
                logger.warning(f"⚠ Order state: {tracked_order.current_state if tracked_order else 'Not found'}")

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

            self.market_order_id = self.connector.buy(
                trading_pair=self.trading_pair,
                amount=self.min_order_size,
                order_type=OrderType.MARKET,
            )

            logger.info(f"Order ID: {self.market_order_id}")

            # Wait for order to be placed
            await asyncio.sleep(2)

            if self.market_order_id in self.connector.in_flight_orders:
                self.results['market_order_placed'] = True
                logger.info("✓ MARKET order placed successfully")
            else:
                raise Exception("Market order not found in tracking")

            # Wait for fill
            logger.info("\n--- Waiting for order fill ---")
            timeout = 30
            start_time = time.time()
            filled = False

            while time.time() - start_time < timeout:
                await asyncio.sleep(2)

                tracked_order = self.connector.in_flight_orders.get(self.market_order_id)
                if tracked_order:
                    logger.info(f"Order state: {tracked_order.current_state}")

                    if tracked_order.is_filled:
                        filled = True
                        self.results['market_order_filled'] = True
                        logger.info(f"✓ Order filled!")
                        logger.info(f"  Executed amount: {tracked_order.executed_amount_base}")
                        logger.info(f"  Average fill price: {tracked_order.average_executed_price}")
                        break
                else:
                    logger.warning("Order no longer in tracking - may be completed")
                    # Check if we have position
                    break

            if not filled:
                logger.warning("⚠ Order not filled within timeout")

            # Check position
            logger.info("\n--- Checking position ---")
            await asyncio.sleep(3)

            # Update positions
            await self.connector._update_positions()

            position = self.connector.get_position(
                trading_pair=self.trading_pair,
                position_side=PositionSide.LONG
            )

            if position and position.amount > 0:
                self.results['position_opened'] = True
                self.results['position_tracked'] = True
                self.current_position = position

                logger.info("✓ Position opened and tracked successfully")
                logger.info(f"  Position side: {position.position_side}")
                logger.info(f"  Amount: {position.amount}")
                logger.info(f"  Entry price: {position.entry_price}")
                logger.info(f"  Unrealized PnL: {position.unrealized_pnl}")
                logger.info(f"  Leverage: {position.leverage}")
            else:
                logger.error("✗ No position found")

            # Close position
            logger.info("\n--- Closing position with MARKET SELL order ---")
            await asyncio.sleep(2)

            if self.current_position:
                close_amount = self.current_position.amount

                self.close_order_id = self.connector.sell(
                    trading_pair=self.trading_pair,
                    amount=close_amount,
                    order_type=OrderType.MARKET,
                    position_action=PositionAction.CLOSE,
                )

                logger.info(f"Close order ID: {self.close_order_id}")

                # Wait for close order to fill
                timeout = 30
                start_time = time.time()

                while time.time() - start_time < timeout:
                    await asyncio.sleep(2)

                    # Update positions
                    await self.connector._update_positions()

                    position = self.connector.get_position(
                        trading_pair=self.trading_pair,
                        position_side=PositionSide.LONG
                    )

                    if not position or position.amount == 0:
                        self.results['position_closed'] = True
                        logger.info("✓ Position closed successfully")
                        break

                if not self.results['position_closed']:
                    logger.warning("⚠ Position not closed within timeout")

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

            # Fetch funding payments
            timestamp, funding_rate, payment = await self.connector._fetch_last_fee_payment(
                trading_pair=self.trading_pair
            )

            logger.info(f"Last funding timestamp: {timestamp}")
            logger.info(f"Funding rate: {funding_rate}")
            logger.info(f"Payment amount: {payment}")

            self.results['funding_payments_retrieved'] = True
            logger.info("✓ Funding payments retrieved successfully")

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

        logger.info("\nEvents Received:")
        logger.info(f"  Total events: {len(self.events_received)}")

        event_types = {}
        for event_type, _ in self.events_received:
            event_types[event_type] = event_types.get(event_type, 0) + 1

        for event_type, count in event_types.items():
            logger.info(f"    {event_type}: {count}")

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

    # Create and run tester
    tester = OrderlyOrderLifecycleTester(config)

    # Run tests
    asyncio.run(tester.run_all_tests())


if __name__ == "__main__":
    main()
