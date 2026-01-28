"""
Mock strategy for grid backtesting.

Provides minimal interface that GridExecutor and ExecutorOrchestrator expect from a strategy.
Does NOT inherit from ScriptStrategyBase - only implements what's actually used.
"""

from decimal import Decimal
from typing import Dict, Optional, Set

from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType

from backtester.grid.mock_connector import BacktestingMockConnector


class BacktestingMockStrategy:
    """
    Lightweight mock strategy that provides what GridExecutor and ExecutorOrchestrator need.

    The executor calls:
    - self._strategy.buy()
    - self._strategy.sell()
    - self._strategy.cancel()

    And accesses:
    - self.connectors[connector_name]
    - self.current_timestamp
    - self.controllers
    - self.markets
    - self.market_data_provider
    """

    def __init__(
        self,
        connector: BacktestingMockConnector,
        connector_name: str,
        trading_pair: str,
        market_data_provider,
    ):
        """
        Initialize mock strategy.

        Args:
            connector: The mock connector instance
            connector_name: Name to use when accessing connectors dict
            trading_pair: Trading pair (e.g., "BTC-USDC")
            market_data_provider: BacktestingMarketDataProvider instance
        """
        self.connectors: Dict[str, BacktestingMockConnector] = {
            connector_name: connector
        }
        self.current_timestamp = 0.0

        # Required by ExecutorOrchestrator
        self.controllers: Dict = {}  # Will be set by backtester after controller creation
        self.markets: Dict[str, Set[str]] = {
            connector_name: {trading_pair}
        }
        self.market_data_provider = market_data_provider

    def buy(
        self,
        connector_name: str,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal,
        position_action: PositionAction = PositionAction.NIL,
        **kwargs
    ) -> str:
        """
        Place a buy order.

        Called by GridExecutor via ExecutorBase.place_order()

        Returns:
            Order ID
        """
        connector = self.connectors[connector_name]
        return connector.buy(trading_pair, amount, order_type, price)

    def sell(
        self,
        connector_name: str,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal,
        position_action: PositionAction = PositionAction.NIL,
        **kwargs
    ) -> str:
        """
        Place a sell order.

        Called by GridExecutor via ExecutorBase.place_order()

        Returns:
            Order ID
        """
        connector = self.connectors[connector_name]
        return connector.sell(trading_pair, amount, order_type, price)

    def cancel(self, connector_name: str, trading_pair: str, order_id: str):
        """
        Cancel an order.

        Called by GridExecutor when canceling orders.
        """
        connector = self.connectors[connector_name]
        connector.cancel(trading_pair, order_id)

    def get_connector(self, connector_name: str) -> Optional[BacktestingMockConnector]:
        """Get connector by name"""
        return self.connectors.get(connector_name)
