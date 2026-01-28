"""
Backtesting Market Data Provider

Lightweight provider with simulated time for backtesting.
Delegates price queries to the mock connector while tracking simulated timestamps.
"""

from decimal import Decimal
from typing import Optional

from hummingbot.core.data_type.common import PriceType


class BacktestingMarketDataProvider:
    """
    Market data provider for backtesting that provides simulated time.

    This provider:
    - Maintains a simulated timestamp that advances with the backtest
    - Delegates price queries to the mock connector (which holds current market state)
    - Always returns ready=True (no initialization needed for backtesting)
    """

    def __init__(self, mock_connector, connector_name: str):
        """
        Initialize the backtesting market data provider.

        Args:
            mock_connector: The BacktestingMockConnector instance
            connector_name: Name of the connector (for interface compatibility)
        """
        self._mock_connector = mock_connector
        self._connector_name = connector_name
        self._current_time = 0.0
        self.ready = True

    def time(self) -> float:
        """
        Return the current simulated timestamp in seconds.

        This is used by executors to get the current time.
        """
        return self._current_time

    def get_price_by_type(
        self,
        connector_name: str,
        trading_pair: str,
        price_type: PriceType
    ) -> Decimal:
        """
        Get price for the specified price type from the mock connector.

        Args:
            connector_name: Name of the connector
            trading_pair: Trading pair (e.g., "BTC-USDC")
            price_type: Type of price to retrieve (BestBid, BestAsk, MidPrice, etc.)

        Returns:
            Price as Decimal
        """
        return self._mock_connector.get_price_by_type(trading_pair, price_type)

    def initialize_rate_sources(self, connector_pairs: dict):
        """
        Initialize rate sources (no-op for backtesting).

        In live trading, this would set up price feeds. For backtesting,
        prices come directly from candle data via the mock connector.

        Args:
            connector_pairs: Dict mapping connector names to trading pairs
        """
        pass  # No initialization needed for backtesting

    def get_price(self, connector_name: str, trading_pair: str) -> Optional[Decimal]:
        """
        Get the mid price for a trading pair.

        Args:
            connector_name: Name of the connector
            trading_pair: Trading pair (e.g., "BTC-USDC")

        Returns:
            Mid price as Decimal, or None if not available
        """
        try:
            return self._mock_connector.get_price_by_type(trading_pair, PriceType.MidPrice)
        except Exception:
            return None
