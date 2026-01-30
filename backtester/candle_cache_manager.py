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
from typing import Dict, Optional

import aiohttp
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

    def _interval_to_seconds(self, interval: str) -> int:
        """
        Convert interval string to seconds.

        Args:
            interval: Interval string (e.g., "1s", "1m", "1h")

        Returns:
            Number of seconds
        """
        interval_map = {
            "1s": 1,
            "1m": 60,
            "3m": 180,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "2h": 7200,
            "4h": 14400,
            "6h": 21600,
            "8h": 28800,
            "12h": 43200,
            "1d": 86400,
            "3d": 259200,
            "1w": 604800,
            "1M": 2592000
        }
        return interval_map.get(interval, 1)  # Default to 1 second

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

    async def _fetch_orderly_candles(
        self,
        trading_pair: str,
        start_timestamp: int,
        end_timestamp: int,
    ) -> pd.DataFrame:
        """
        Fetch 1s candles for orderly_perpetual connector from Kodiak Finance API.

        Args:
            trading_pair: Trading pair (e.g., "BTC-USDT", "BTC-USD")
            start_timestamp: Start time in seconds
            end_timestamp: End time in seconds

        Returns:
            DataFrame with candle data (timestamp, open, high, low, close, volume)
        """
        # Convert trading pair format from "BTC-USDT" to "BTC/USD" format
        # Remove hyphens and replace with /
        pair_parts = trading_pair.split("-")[0]
        symbol = f"Crypto.{pair_parts}/USD"

        # Build API URL
        url = "https://backend.kodiak.finance/chart/history"
        params = {
            "symbol": symbol,
            "resolution": "1S",
            "from": start_timestamp,
            "to": end_timestamp,
        }

        logger.info(f"Fetching Orderly candles from {url} with params: {params}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=300)) as response:
                    if response.status != 200:
                        logger.error(f"Failed to fetch Orderly candles: HTTP {response.status}")
                        raise ValueError(f"API request failed with status {response.status}")

                    data = await response.json()

            # Validate response
            if data.get("s") != "ok":
                logger.error(f"API returned error status: {data.get('s')}")
                raise ValueError(f"API returned error: {data.get('s')}")

            # Transform API response to DataFrame
            timestamps = data.get("t", [])
            opens = data.get("o", [])
            highs = data.get("h", [])
            lows = data.get("l", [])
            closes = data.get("c", [])
            volumes = data.get("v", [])

            if not timestamps:
                logger.warning("No candle data returned from API")
                return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            # Create DataFrame in standard format matching market data provider output
            # Includes all 10 columns expected by the system
            df = pd.DataFrame({
                'timestamp': timestamps,
                'open': opens,
                'high': highs,
                'low': lows,
                'close': closes,
                'volume': volumes,
                'quote_asset_volume': [0.0] * len(timestamps),  # Not provided by API
                'n_trades': [0.0] * len(timestamps),  # Not provided by API
                'taker_buy_base_volume': [0.0] * len(timestamps),  # Not provided by API
                'taker_buy_quote_volume': [0.0] * len(timestamps),  # Not provided by API
            })

            logger.info(f"Fetched {len(df)} Orderly candles")
            return df

        except Exception as e:
            logger.error(f"Error fetching Orderly candles: {e}")
            raise

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
            start_timestamp: Start time in seconds
            end_timestamp: End time in seconds

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

                # Convert interval to seconds for iteration
                interval_seconds = self._interval_to_seconds(interval)

                # Create set of cached timestamps for O(1) lookup
                cached_timestamps_set = {int(ts) for ts in cache['data'].keys()}

                # Find existing ranges by iterating through requested range
                existing_ranges = []
                current_range_start = None
                current_range_end = None

                # Iterate through requested range with step = interval_seconds
                for ts in range(start_timestamp, end_timestamp + 1, interval_seconds):
                    if ts in cached_timestamps_set:
                        if current_range_start is None:
                            # Start new range
                            current_range_start = ts
                            current_range_end = ts
                        else:
                            # Extend current range
                            current_range_end = ts
                    else:
                        # Gap found - close current range if exists
                        if current_range_start is not None:
                            existing_ranges.append((current_range_start, current_range_end))
                            current_range_start = None
                            current_range_end = None

                # Close final range if exists
                if current_range_start is not None:
                    existing_ranges.append((current_range_start, current_range_end))

                logger.info(f"Found {len(existing_ranges)} existing range(s) in cache")

                # Identify missing ranges (gaps) that need to be fetched
                fetch_ranges = []

                # Check for gap before first existing range
                if not existing_ranges or existing_ranges[0][0] > start_timestamp:
                    gap_start = start_timestamp
                    gap_end = existing_ranges[0][0] - interval_seconds if existing_ranges else end_timestamp
                    if gap_start <= gap_end:
                        fetch_ranges.append((gap_start, gap_end))
                        logger.info(f"Need to fetch candles before cache: {gap_start} to {gap_end}")

                # Check for gaps between existing ranges
                for i in range(len(existing_ranges) - 1):
                    gap_start = existing_ranges[i][1] + interval_seconds
                    gap_end = existing_ranges[i + 1][0] - interval_seconds
                    if gap_start <= gap_end:
                        fetch_ranges.append((gap_start, gap_end))
                        logger.info(f"Need to fetch candles in gap: {gap_start} to {gap_end}")

                # Check for gap after last existing range
                if existing_ranges and existing_ranges[-1][1] < end_timestamp:
                    gap_start = existing_ranges[-1][1] + interval_seconds
                    gap_end = end_timestamp
                    if gap_start <= gap_end:
                        fetch_ranges.append((gap_start, gap_end))
                        logger.info(f"Need to fetch candles after cache: {gap_start} to {gap_end}")

                # Fetch missing ranges
                for start, end in fetch_ranges:
                    logger.info(f"Fetching candles from {start} to {end}...")

                    # Use Orderly API for orderly_perpetual connector with 1s interval
                    if connector_name == "orderly_perpetual" and interval == "1s":
                        new_candles = await self._fetch_orderly_candles(
                            trading_pair=trading_pair,
                            start_timestamp=start,
                            end_timestamp=end,
                        )
                    else:
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

            # Use Orderly API for orderly_perpetual connector with 1s interval
            if connector_name == "orderly_perpetual" and interval == "1s":
                new_candles = await self._fetch_orderly_candles(
                    trading_pair=trading_pair,
                    start_timestamp=start_timestamp,
                    end_timestamp=end_timestamp,
                )
            else:
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
