"""
Test script to fetch Orderly candles for BTC-USD pair for the last 10 minutes
"""
import asyncio
import time

from backtester.candle_cache_manager import CandleCacheManager
from hummingbot.data_feed.market_data_provider import MarketDataProvider

async def test_orderly_candles():
    """Test fetching Orderly candles for BTC-USD"""

    # Initialize cache manager
    cache_manager = CandleCacheManager(cache_dir="backtest_cache")

    # Calculate timestamps for last 10 minutes
    start_timestamp = 1769509800
    end_timestamp = 1769682600

    print(f"Fetching BTC-USDC candles from {start_timestamp} to {end_timestamp}")
    
    connector_name = "binance"
    market_data_provider = MarketDataProvider(connectors={})
    
    if connector_name == "orderly_perpetual":
        market_data_provider = None
  
    try:
        # Fetch candles using the Orderly API
        # Note: market_data_provider is not needed for orderly_perpetual connector
        candles_df = await cache_manager.get_candles(
            market_data_provider= market_data_provider,
            connector_name=connector_name,
            trading_pair="BTC-USDC",
            interval="1s",
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )

        print(f"\n✓ Successfully fetched {len(candles_df)} candles")
        print(f"\nDataFrame Info:")
        print(f"Columns: {list(candles_df.columns)}")
        print(f"Shape: {candles_df.shape}")

        print(f"\nFirst 5 candles:")
        print(candles_df.head())

        print(f"\nLast 5 candles:")
        print(candles_df.tail())

        print(f"\nDataFrame dtypes:")
        print(candles_df.dtypes)

        print(f"\nBasic statistics:")
        print(candles_df[['open', 'high', 'low', 'close', 'volume']].describe())

    except Exception as e:
        print(f"\n✗ Error fetching candles: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(test_orderly_candles())
    exit(exit_code)
