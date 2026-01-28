"""
Minimal Mock Connector for Backtesting

This connector exists ONLY to satisfy tight coupling in ScriptStrategyBase.
It does NOT manage orders, positions, or market data - the backtester handles everything.

The strategy's create_proposal() is called directly with TickData - no connector involvement.
"""

from decimal import Decimal
from typing import List

from hummingbot.core.network_iterator import NetworkStatus


class MockPerpetualConnector:
    """
    Bare minimum mock connector to satisfy interface requirements.

    Responsibilities: NONE - just exists to satisfy constructor signatures.
    The backtester handles all state management directly.
    """

    def __init__(self, trading_pair: str):
        self._trading_pair = trading_pair
        self._trading_pairs = [trading_pair]

        # Minimal interface requirements
        self.name = "mock_perpetual"
        self.display_name = "Mock Perpetual"
        self.ready = True

    # === Properties ===

    @property
    def network_status(self) -> NetworkStatus:
        return NetworkStatus.CONNECTED

    @property
    def trading_pairs(self) -> List[str]:
        return self._trading_pairs

    @property
    def limit_orders(self) -> List:
        return []

    # === Balance stubs ===

    def get_balance(self, currency: str) -> Decimal:
        return Decimal("999999999")

    def get_available_balance(self, currency: str) -> Decimal:
        return Decimal("999999999")

    # === Configuration stubs (no-ops) ===

    def set_leverage(self, trading_pair: str, leverage: int):
        pass

    def set_order_tag(self, order_tag: str):
        pass

    # === Order stubs (backtester handles all order logic) ===

    async def batch_order_cancel(self, orders_to_cancel: List) -> List:
        return []

    async def batch_order_create(self, orders_to_create: List) -> List:
        return []

    async def cancel_all(self, timeout: float) -> List:
        return []
