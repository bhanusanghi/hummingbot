import os
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import Field, field_validator

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.clock import Clock
from hummingbot.core.data_type.common import MarketDict, OrderType, PositionMode, PriceType, TradeType
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.executors.position_executor.data_types import (
    PositionExecutorConfig,
    TrailingStop,
    TripleBarrierConfig,
)
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction


class SMAConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    markets: MarketDict = MarketDict()
    candles_config: List[CandlesConfig] = []
    controllers_config: List[str] = []

    # Exchange configuration
    exchange: str = Field(default="hyperliquid_perpetual")
    trading_pairs: List[str] = Field(default=["ETH-USD"])
    candles_exchange: str = Field(default="binance_perpetual")
    candles_pairs: List[str] = Field(default=["ETH-USDT"])
    candles_interval: str = Field(default="5m")
    candles_length: int = Field(default=50, gt=0)

    # SMA Configuration
    short_ma_length: int = Field(default=7, gt=0)
    long_ma_length: int = Field(default=25, gt=0)

    # ATR Configuration
    atr_filter: bool = Field(default=True)
    atr_length: int = Field(default=14, gt=0)
    atr_percentile_lookback: int = Field(default=100, gt=0)
    atr_dormant_threshold: Decimal = Field(default=Decimal("20.0"), ge=0, le=100)
    atr_volatile_threshold: Decimal = Field(default=Decimal("80.0"), ge=0, le=100)
    filter_dormant_markets: bool = Field(default=True)
    filter_volatile_markets: bool = Field(default=False)

    # Order Configuration
    order_amount_quote: Decimal = Field(default=Decimal("100"), gt=0)
    leverage: int = Field(default=5, gt=0)
    position_mode: PositionMode = Field(default=PositionMode.ONEWAY)

    # Triple Barrier Configuration
    stop_loss: Optional[Decimal] = Field(default=None)
    take_profit: Optional[Decimal] = Field(default=None)
    time_limit: Optional[int] = Field(default=None)
    trailing_stop_activation_price: Optional[Decimal] = Field(default=None)
    trailing_stop_trailing_delta: Optional[Decimal] = Field(default=None)

    @property
    def triple_barrier_config(self) -> TripleBarrierConfig:
        return TripleBarrierConfig(
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            time_limit=self.time_limit,
            open_order_type=OrderType.MARKET,
            take_profit_order_type=OrderType.LIMIT,
            stop_loss_order_type=OrderType.MARKET,
            time_limit_order_type=OrderType.MARKET,
            trailing_stop=self.trailing_stop_activation_price is not None and self.trailing_stop_trailing_delta is not None and TrailingStop(
                activation_price=self.trailing_stop_activation_price,
                trailing_delta=self.trailing_stop_trailing_delta
            ) or None
        )

    @field_validator('position_mode', mode="before")
    @classmethod
    def validate_position_mode(cls, v: str) -> PositionMode:
        if isinstance(v, str) and v.upper() in PositionMode.__members__:
            return PositionMode[v.upper()]
        elif isinstance(v, PositionMode):
            return v
        raise ValueError(f"Invalid position mode: {v}. Valid options are: {', '.join(PositionMode.__members__)}")

    @field_validator('trading_pairs', mode="before")
    @classmethod
    def validate_trading_pairs(cls, v) -> List[str]:
        if isinstance(v, str):
            return [pair.strip() for pair in v.split(',')]
        return v

    @field_validator('candles_pairs', mode="before")
    @classmethod
    def validate_candles_pairs(cls, v) -> List[str]:
        if isinstance(v, str):
            return [pair.strip() for pair in v.split(',')]
        return v


class SMAStrategy(StrategyV2Base):
    """
    Simple Moving Average (SMA) Crossover Strategy with multiple trading pairs support.
    Opens long when short MA crosses above long MA.
    Opens short when short MA crosses below long MA.

    FIXED VERSION: Properly handles position state transitions and prevents
    opening new positions before existing ones are fully closed.
    """

    account_config_set = False

    @classmethod
    def init_markets(cls, config: SMAConfig):
        cls.markets = {config.exchange: set(config.trading_pairs)}

    def __init__(self, connectors: Dict[str, ConnectorBase], config: SMAConfig):
        self.max_records = max(config.short_ma_length, config.long_ma_length, config.candles_length, config.atr_percentile_lookback) + 10

        if len(config.candles_config) == 0:
            for candles_pair in config.candles_pairs:
                config.candles_config.append(CandlesConfig(
                    connector=config.candles_exchange,
                    trading_pair=candles_pair,
                    interval=config.candles_interval,
                    max_records=self.max_records
                ))

        super().__init__(connectors, config)
        self.config = config

        # Track current and previous MA values per trading pair
        self.current_short_ma = {}
        self.current_long_ma = {}
        self.prev_short_ma = {}
        self.prev_long_ma = {}
        self.current_price = {}
        self.last_signal = {}

        # Track ATR values per trading pair
        self.current_atr = {}
        self.current_atr_percentile = {}
        self.volatility_state = {}

        # Track positions being closed to prevent new positions until fully closed
        self.closing_positions = {}

    def start(self, clock: Clock, timestamp: float) -> None:
        """
        Start the strategy.
        :param clock: Clock to use.
        :param timestamp: Current time.
        """
        self._last_timestamp = timestamp
        self.apply_initial_setting()

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        create_actions = []

        # Check signals for each trading pair
        for i, trading_pair in enumerate(self.config.trading_pairs):
            candles_pair = self.config.candles_pairs[i]
            signal = self.get_signal(self.config.candles_exchange, candles_pair)

            if signal is None or signal == 0:
                continue

            # Check ATR volatility filter
            if self.config.atr_filter:
                vol_state = self.volatility_state.get(candles_pair, "NORMAL")
                if vol_state == "DORMANT" and self.config.filter_dormant_markets:
                    self.logger().info(f"Skipping {candles_pair} signal due to dormant market (ATR percentile: {self.current_atr_percentile.get(candles_pair, 0):.1f}%)")
                    continue
                elif vol_state == "VOLATILE" and self.config.filter_volatile_markets:
                    self.logger().info(f"Skipping {candles_pair} signal due to volatile market (ATR percentile: {self.current_atr_percentile.get(candles_pair, 0):.1f}%)")
                    continue

            active_longs, active_shorts = self.get_active_executors_by_side(
                self.config.exchange, trading_pair
            )

            # Check if we're currently closing a position for this pair
            if trading_pair in self.closing_positions:
                # Skip creating new positions until the close is complete
                continue

            # Check if we have any executors that are not fully terminated
            all_executors = self.get_all_executors()
            non_terminated_executors = [e for e in all_executors
                                        if e.trading_pair == trading_pair
                                        and not e.is_done]  # Not yet TERMINATED

            if non_terminated_executors:
                # Skip creating new positions if executors are still shutting down
                continue

            mid_price = self.market_data_provider.get_price_by_type(
                self.config.exchange,
                trading_pair,
                PriceType.MidPrice
            )

            # Open long position on bullish crossover
            # Check that there are no active positions at all
            if signal == 1 and len(active_longs) == 0 and len(active_shorts) == 0:
                create_actions.append(CreateExecutorAction(
                    executor_config=PositionExecutorConfig(
                        timestamp=self.current_timestamp,
                        connector_name=self.config.exchange,
                        trading_pair=trading_pair,
                        side=TradeType.BUY,
                        entry_price=mid_price,
                        amount=self.config.order_amount_quote / mid_price,
                        triple_barrier_config=self.config.triple_barrier_config,
                        leverage=self.config.leverage
                    )
                ))

            # Open short position on bearish crossover
            # Check that there are no active positions at all
            elif signal == -1 and len(active_shorts) == 0 and len(active_longs) == 0:
                create_actions.append(CreateExecutorAction(
                    executor_config=PositionExecutorConfig(
                        timestamp=self.current_timestamp,
                        connector_name=self.config.exchange,
                        trading_pair=trading_pair,
                        side=TradeType.SELL,
                        entry_price=mid_price,
                        amount=self.config.order_amount_quote / mid_price,
                        triple_barrier_config=self.config.triple_barrier_config,
                        leverage=self.config.leverage
                    )
                ))

        return create_actions

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        stop_actions = []

        # Check signals for each trading pair
        for i, trading_pair in enumerate(self.config.trading_pairs):
            candles_pair = self.config.candles_pairs[i]
            signal = self.get_signal(self.config.candles_exchange, candles_pair)

            if signal is None:
                continue

            active_longs, active_shorts = self.get_active_executors_by_side(
                self.config.exchange, trading_pair
            )

            # Close long positions on bearish crossover
            if signal == -1 and len(active_longs) > 0:
                # Check ATR volatility filter for closing positions
                if self.config.atr_filter:
                    vol_state = self.volatility_state.get(candles_pair, "NORMAL")
                    if vol_state == "DORMANT" and self.config.filter_dormant_markets:
                        self.logger().info(f"Skipping close long for {candles_pair} due to dormant market (ATR percentile: {self.current_atr_percentile.get(candles_pair, 0):.1f}%)")
                        continue
                    elif vol_state == "VOLATILE" and self.config.filter_volatile_markets:
                        self.logger().info(f"Skipping close long for {candles_pair} due to volatile market (ATR percentile: {self.current_atr_percentile.get(candles_pair, 0):.1f}%)")
                        continue
                # Mark that we're closing positions for this pair
                self.closing_positions[trading_pair] = True
                stop_actions.extend([
                    StopExecutorAction(
                        controller_id=e.controller_id or "main",
                        executor_id=e.id
                    ) for e in active_longs
                ])

            # Close short positions on bullish crossover
            elif signal == 1 and len(active_shorts) > 0:
                # Check ATR volatility filter for closing positions
                if self.config.atr_filter:
                    vol_state = self.volatility_state.get(candles_pair, "NORMAL")
                    if vol_state == "DORMANT" and self.config.filter_dormant_markets:
                        self.logger().info(f"Skipping close short for {candles_pair} due to dormant market (ATR percentile: {self.current_atr_percentile.get(candles_pair, 0):.1f}%)")
                        continue
                    elif vol_state == "VOLATILE" and self.config.filter_volatile_markets:
                        self.logger().info(f"Skipping close short for {candles_pair} due to volatile market (ATR percentile: {self.current_atr_percentile.get(candles_pair, 0):.1f}%)")
                        continue
                # Mark that we're closing positions for this pair
                self.closing_positions[trading_pair] = True
                stop_actions.extend([
                    StopExecutorAction(
                        controller_id=e.controller_id or "main",
                        executor_id=e.id
                    ) for e in active_shorts
                ])

            # Clear closing flag if no active positions remain
            if (trading_pair in self.closing_positions and
                    len(active_longs) == 0 and len(active_shorts) == 0):
                del self.closing_positions[trading_pair]

        return stop_actions

    def get_active_executors_by_side(self, connector_name: str, trading_pair: str):
        active_executors = self.filter_executors(
            executors=self.get_all_executors(),
            filter_func=lambda e: e.connector_name == connector_name and e.trading_pair == trading_pair and e.is_active
        )
        active_longs = [e for e in active_executors if e.side == TradeType.BUY]
        active_shorts = [e for e in active_executors if e.side == TradeType.SELL]
        return active_longs, active_shorts

    def get_signal(self, connector_name: str, trading_pair: str) -> Optional[int]:
        candles = self.market_data_provider.get_candles_df(
            connector_name,
            trading_pair,
            self.config.candles_interval,
            self.max_records
        )

        if candles is None or candles.empty or len(candles) < self.config.long_ma_length + 2:
            return None

        # Calculate SMAs
        candles.ta.sma(length=self.config.short_ma_length, append=True)
        candles.ta.sma(length=self.config.long_ma_length, append=True)

        # Calculate ATR
        candles.ta.atr(length=self.config.atr_length, append=True)

        # We need at least 2 candles to detect signals
        if len(candles) < 2:
            return None

        # Use current candle for signal detection (more reactive but may change)
        # Index -2: Previous candle (fully closed)
        # Index -1: Current candle (incomplete, used for signals)
        # WARNING: Signals may change as the current candle develops

        short_ma_col = f"SMA_{self.config.short_ma_length}"
        long_ma_col = f"SMA_{self.config.long_ma_length}"

        # Get MA values including current candle
        short_ma_prev = candles[short_ma_col].iloc[-2]  # Previous closed candle
        long_ma_prev = candles[long_ma_col].iloc[-2]    # Previous closed candle

        short_ma_current = candles[short_ma_col].iloc[-1]  # Current candle (incomplete)
        long_ma_current = candles[long_ma_col].iloc[-1]    # Current candle (incomplete)

        # Store current values for display purposes
        self.prev_short_ma[trading_pair] = short_ma_prev
        self.prev_long_ma[trading_pair] = long_ma_prev
        self.current_short_ma[trading_pair] = short_ma_current
        self.current_long_ma[trading_pair] = long_ma_current
        self.current_price[trading_pair] = candles["close"].iloc[-1]

        # Store ATR value and calculate percentile
        atr_col = f"ATRr_{self.config.atr_length}"
        if atr_col in candles.columns and len(candles) >= self.config.atr_percentile_lookback:
            current_atr = candles[atr_col].iloc[-1]
            self.current_atr[trading_pair] = current_atr

            # Calculate ATR percentile
            atr_lookback_data = candles[atr_col].iloc[-self.config.atr_percentile_lookback:]
            count = (atr_lookback_data < current_atr).sum()
            percentile = (count / self.config.atr_percentile_lookback) * 100
            self.current_atr_percentile[trading_pair] = percentile

            # Determine volatility state
            if percentile < float(self.config.atr_dormant_threshold):
                self.volatility_state[trading_pair] = "DORMANT"
            elif percentile > float(self.config.atr_volatile_threshold):
                self.volatility_state[trading_pair] = "VOLATILE"
            else:
                self.volatility_state[trading_pair] = "NORMAL"
        else:
            self.current_atr[trading_pair] = 0
            self.current_atr_percentile[trading_pair] = 50
            self.volatility_state[trading_pair] = "NORMAL"

        # Detect signals using current candle (may change as candle develops)
        signal = 0

        # Bullish signal: Short MA crosses above long MA
        # Previous candle: Short MA was below or equal to long MA
        # Current candle: Short MA is now above long MA
        if (short_ma_prev <= long_ma_prev and      # Was below or equal in previous candle
                short_ma_current > long_ma_current):    # Now above in current candle
            signal = 1

        # Bearish signal: Short MA crosses below long MA
        # Previous candle: Short MA was above or equal to long MA
        # Current candle: Short MA is now below long MA
        elif (short_ma_prev >= long_ma_prev and     # Was above or equal in previous candle
              short_ma_current < long_ma_current):   # Now below in current candle
            signal = -1

        self.last_signal[trading_pair] = signal
        return signal

    def apply_initial_setting(self):
        if not self.account_config_set:
            for connector_name, connector in self.connectors.items():
                if self.is_perpetual(connector_name):
                    connector.set_position_mode(self.config.position_mode)
                    for trading_pair in self.config.trading_pairs:
                        connector.set_leverage(trading_pair, self.config.leverage)
            self.account_config_set = True

    def format_status(self) -> str:
        if not self.ready_to_trade:
            return "Market connectors are not ready."

        lines = []

        # Create market overview
        lines.extend(["", "  Market Overview:"])

        # Header
        if self.config.atr_filter:
            header = f"  {'Symbol':<15} {'Price':<10} {'MA' + str(self.config.short_ma_length):<10} {'MA' + str(self.config.long_ma_length):<10} {'ATR%':<8} {'Vol State':<10} {'Trend':<10} {'Signal':<10} {'L':<3} {'S':<3} {'Status':<12}"
            separator = f"  {'-' * 15} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 3} {'-' * 3} {'-' * 12}"
        else:
            header = f"  {'Symbol':<15} {'Price':<10} {'MA' + str(self.config.short_ma_length):<10} {'MA' + str(self.config.long_ma_length):<10} {'Trend':<15} {'Signal':<10} {'Longs':<6} {'Shorts':<6} {'Status':<15}"
            separator = f"  {'-' * 15} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 15} {'-' * 10} {'-' * 6} {'-' * 6} {'-' * 15}"
        lines.extend([header, separator])

        # Display each trading pair
        for i, candles_pair in enumerate(self.config.candles_pairs):
            price = self.current_price.get(candles_pair, 0)
            short_ma = self.current_short_ma.get(candles_pair, 0)
            long_ma = self.current_long_ma.get(candles_pair, 0)
            signal = self.last_signal.get(candles_pair, 0)

            # Determine trend
            if short_ma > 0 and long_ma > 0:
                if short_ma > long_ma:
                    trend = "BULLISH"
                else:
                    trend = "BEARISH"
            else:
                trend = "N/A"

            # Format signal
            signal_text = "BUY" if signal == 1 else "SELL" if signal == -1 else "NONE"

            # Get active positions
            if i < len(self.config.trading_pairs):
                actual_trading_pair = self.config.trading_pairs[i]
                active_longs, active_shorts = self.get_active_executors_by_side(
                    self.config.exchange, actual_trading_pair
                )

                # Check if we're closing positions
                status = "CLOSING" if actual_trading_pair in self.closing_positions else "NORMAL"
            else:
                active_longs, active_shorts = [], []
                status = "N/A"

            # Format row
            symbol = candles_pair.replace('-', '/')
            if self.config.atr_filter:
                atr_percentile = self.current_atr_percentile.get(candles_pair, 50)
                vol_state = self.volatility_state.get(candles_pair, "NORMAL")
                row = f"  {symbol:<15} {price:<10.2f} {short_ma:<10.2f} {long_ma:<10.2f} {atr_percentile:<8.1f} {vol_state:<10} {trend:<10} {signal_text:<10} {len(active_longs):<3} {len(active_shorts):<3} {status:<12}"
            else:
                row = f"  {symbol:<15} {price:<10.2f} {short_ma:<10.2f} {long_ma:<10.2f} {trend:<15} {signal_text:<10} {len(active_longs):<6} {len(active_shorts):<6} {status:<15}"
            lines.append(row)

        # Add configuration info
        config_lines = [
            "",
            f"  Config: MA{self.config.short_ma_length} / MA{self.config.long_ma_length} | "
            f"Interval: {self.config.candles_interval} | "
            f"SL: {self.config.stop_loss:.1%} | TP: {self.config.take_profit:.1%}"
        ]

        if self.config.atr_filter:
            config_lines.append(
                f"  ATR Filter: ON | Dormant < {self.config.atr_dormant_threshold}% | Volatile > {self.config.atr_volatile_threshold}% | "
                f"Filter Dormant: {self.config.filter_dormant_markets} | Filter Volatile: {self.config.filter_volatile_markets}"
            )

        lines.extend(config_lines)

        # Display active positions with details
        active_executors = self.filter_executors(
            executors=self.get_all_executors(),
            filter_func=lambda e: e.is_active
        )

        if active_executors:
            lines.extend(["", "  Active Positions:"])

            # Position table header
            pos_header = f"  {'Symbol':<12} {'Side':<5} {'Entry':<10} {'Current':<10} {'PnL USD':<10} {'PnL %':<8} {'Size':<10}"
            pos_separator = f"  {'-' * 12} {'-' * 5} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 10}"
            lines.extend([pos_header, pos_separator])

            for executor in active_executors:
                symbol = executor.trading_pair
                side = "LONG" if executor.side == TradeType.BUY else "SHORT"

                # Get entry price from custom_info
                entry_price = executor.custom_info.get("current_position_average_price", Decimal("0"))
                entry = f"{entry_price:.2f}" if entry_price > 0 else "N/A"

                # Get current price from cached price or fetch it
                if symbol in self.current_price:
                    current = f"{self.current_price[symbol]:.2f}"
                else:
                    # If we don't have cached price, get it from the corresponding candles pair
                    for i, trading_pair in enumerate(self.config.trading_pairs):
                        if trading_pair == symbol and i < len(self.config.candles_pairs):
                            candles_pair = self.config.candles_pairs[i]
                            current = f"{self.current_price.get(candles_pair, 0):.2f}"
                            break
                    else:
                        current = "N/A"

                pnl_usd = f"{executor.net_pnl_quote:+.2f}"
                pnl_pct = f"{executor.net_pnl_pct * 100:+.2f}%"
                size = f"{executor.filled_amount_quote:.2f}"

                row = f"  {symbol:<12} {side:<5} {entry:<10} {current:<10} {pnl_usd:<10} {pnl_pct:<8} {size:<10}"
                lines.append(row)
        else:
            lines.extend(["", "  No active positions"])

        return "\n".join(lines)
