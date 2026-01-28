"""
Candle Cache Manager

Reusable utility for caching and fetching historical candle data.
Implements intelligent caching to minimize API calls and speed up backtests.

Features:
- Caches candles to JSON files by connector/trading_pair/interval
- Detects missing ranges and fetches only what's needed
- Merges cached and newly fetched data
- Appends new data to existing cache files
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from hummingbot.data_feed.market_data_provider import MarketDataProvider

logger = logging.getLogger(__name__)


class CandleCacheManager:
    """
    Manages caching of historical candle data to minimize exchange API calls.

    Usage:
        cache_manager = CandleCacheManager(cache_dir="backtest_cache")
        candles_df = await cache_manager.get_candles(
            market_data_provider=mdp,
            connector_name="binance",
            trading_pair="BTC-USDT",
            interval="1s",
            start_timestamp=1234567890000,
            end_timestamp=1234567899000,
        )
    """

    def __init__(self, cache_dir: str = "backtest_cache"):
        """
        Initialize cache manager.

        Args:
            cache_dir: Directory path for storing cache files
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_filepath(self, connector_name: str, trading_pair: str, interval: str) -> Path:
        """
        Generate cache filepath based on connector, trading_pair, and interval.

        Format: connector_tradingpair_interval.json
        Example: binance_BTCUSDC_1s.json

        Args:
            connector_name: Exchange connector name
            trading_pair: Trading pair (e.g., "BTC-USDT")
            interval: Candle interval (e.g., "1s", "1m")

        Returns:
            Path object for the cache file
        """
        # Remove hyphens and underscores for clean filename
        trading_pair_clean = trading_pair.replace("-", "")
        connector_clean = connector_name.replace("_", "")
        interval_clean = interval

        filename = f"{connector_clean}_{trading_pair_clean}_{interval_clean}.json"
        return self.cache_dir / filename

    def _load_cached_candles(self, cache_file: Path) -> Optional[Dict]:
        """
        Load cached candles from JSON file.

        Args:
            cache_file: Path to the cache file

        Returns:
            Dict with 'metadata' and 'data' keys, or None if cache doesn't exist
        """
        if not cache_file.exists():
            logger.info(f"No cache file found at {cache_file}")
            return None

        try:
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)

            logger.info(f"Loaded cache from {cache_file}")
            logger.info(f"Cache range: {cache_data['metadata']['from']} to {cache_data['metadata']['to']}")

            return cache_data
        except Exception as e:
            logger.warning(f"Failed to load cache from {cache_file}: {e}")
            return None

    def _save_candles_to_cache(self, cache_file: Path, candles_df: pd.DataFrame) -> None:
        """
        Save or append candles to cache file.

        Args:
            cache_file: Path to the cache file
            candles_df: DataFrame with candle data including 'timestamp' column
        """
        if candles_df is None or len(candles_df) == 0:
            logger.warning("No candles to cache")
            return

        # Convert DataFrame to dict format for JSON storage
        # data structure: {timestamp: {candle_data}}
        new_data = {}
        for idx, row in candles_df.iterrows():
            timestamp = int(row['timestamp'])
            candle_data = {
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
            }
            new_data[str(timestamp)] = candle_data

        # Load existing cache or create new
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cache = json.load(f)

                # Merge new data with existing
                cache['data'].update(new_data)

                # Update metadata timestamps
                all_timestamps = [int(ts) for ts in cache['data'].keys()]
                cache['metadata']['from'] = min(all_timestamps)
                cache['metadata']['to'] = max(all_timestamps)

                logger.info(f"Appended {len(new_data)} candles to existing cache")
            except Exception as e:
                logger.warning(f"Failed to load existing cache, creating new: {e}")
                cache = {
                    'metadata': {
                        'from': int(candles_df['timestamp'].min()),
                        'to': int(candles_df['timestamp'].max()),
                    },
                    'data': new_data
                }
        else:
            # Create new cache
            cache = {
                'metadata': {
                    'from': int(candles_df['timestamp'].min()),
                    'to': int(candles_df['timestamp'].max()),
                },
                'data': new_data
            }
            logger.info(f"Created new cache with {len(new_data)} candles")

        # Save to file
        try:
            with open(cache_file, 'w') as f:
                json.dump(cache, f, indent=2)
            logger.info(f"Saved cache to {cache_file}")
            logger.info(f"Total cached candles: {len(cache['data'])}")
        except Exception as e:
            logger.error(f"Failed to save cache to {cache_file}: {e}")

    async def get_candles(
        self,
        market_data_provider: MarketDataProvider,
        connector_name: str,
        trading_pair: str,
        interval: str,
        start_timestamp: int,
        end_timestamp: int,
    ) -> pd.DataFrame:
        """
        Get historical candles with intelligent caching.

        This method:
        1. Loads cached candles if available
        2. Determines which time ranges are missing
        3. Fetches only missing ranges from exchange API
        4. Merges cached and new data
        5. Saves new data to cache for future use
        6. Returns complete DataFrame for requested range

        Args:
            market_data_provider: MarketDataProvider instance for fetching data
            connector_name: Exchange connector name (e.g., "binance")
            trading_pair: Trading pair (e.g., "BTC-USDT")
            interval: Candle interval (e.g., "1s", "1m", "1h")
            start_timestamp: Start time in milliseconds
            end_timestamp: End time in milliseconds

        Returns:
            DataFrame with candle data for the requested time range

        Raises:
            ValueError: If no candles are available for the requested range
        """
        cache_file = self._get_cache_filepath(connector_name, trading_pair, interval)
        cache = self._load_cached_candles(cache_file)

        all_candles = []

        if cache is not None:
            # Convert cache data back to DataFrame
            cached_data = []
            for timestamp_str, candle_data in cache['data'].items():
                candle_data['timestamp'] = int(timestamp_str)
                cached_data.append(candle_data)

            if cached_data:
                cached_df = pd.DataFrame(cached_data)
                logger.info(f"Loaded {len(cached_df)} candles from cache")

                # Determine what ranges we need to fetch
                cache_start = cache['metadata']['from']
                cache_end = cache['metadata']['to']

                fetch_ranges = []

                # Need data before cache?
                if start_timestamp < cache_start:
                    fetch_ranges.append((start_timestamp, cache_start - 1))
                    logger.info(f"Need to fetch candles before cache: {start_timestamp} to {cache_start - 1}")

                # Need data after cache?
                if end_timestamp > cache_end:
                    fetch_ranges.append((cache_end + 1, end_timestamp))
                    logger.info(f"Need to fetch candles after cache: {cache_end + 1} to {end_timestamp}")

                # Fetch missing ranges
                for start, end in fetch_ranges:
                    logger.info(f"Fetching candles from {start} to {end}...")
                    new_candles = await market_data_provider.get_historical_candles_df(
                        connector_name=connector_name,
                        trading_pair=trading_pair,
                        interval=interval,
                        start_time=start,
                        end_time=end,
                    )

                    if new_candles is not None and len(new_candles) > 0:
                        logger.info(f"Fetched {len(new_candles)} new candles")
                        all_candles.append(new_candles)
                        # Save new candles to cache
                        self._save_candles_to_cache(cache_file, new_candles)

                # Add cached candles to the list
                all_candles.append(cached_df)
        else:
            # No cache, fetch all data
            logger.info(f"No cache found, fetching all candles from {start_timestamp} to {end_timestamp}...")
            new_candles = await market_data_provider.get_historical_candles_df(
                connector_name=connector_name,
                trading_pair=trading_pair,
                interval=interval,
                start_time=start_timestamp,
                end_time=end_timestamp,
            )

            if new_candles is not None and len(new_candles) > 0:
                logger.info(f"Fetched {len(new_candles)} candles")
                all_candles.append(new_candles)
                # Save to cache
                self._save_candles_to_cache(cache_file, new_candles)

        # Merge all candles if we have multiple DataFrames
        if len(all_candles) == 0:
            raise ValueError(f"No candles available for {trading_pair}")

        if len(all_candles) == 1:
            result_df = all_candles[0]
        else:
            # Concatenate and remove duplicates
            result_df = pd.concat(all_candles, ignore_index=True)
            # Remove duplicate timestamps, keeping first occurrence
            result_df = result_df.drop_duplicates(subset=['timestamp'], keep='first')
            # Sort by timestamp
            result_df = result_df.sort_values('timestamp').reset_index(drop=True)
            logger.info(f"Merged candles, total: {len(result_df)}")

        # Filter to requested range
        result_df = result_df[
            (result_df['timestamp'] >= start_timestamp) &
            (result_df['timestamp'] <= end_timestamp)
        ].reset_index(drop=True)

        if len(result_df) == 0:
            raise ValueError(f"No candles in requested time range for {trading_pair}")

        logger.info(f"Final candle count: {len(result_df)}")

        return result_df
