import os
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import Field, field_validator

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.clock import Clock
from hummingbot.core.data_type.common import MarketDict, PositionMode, PriceType, TradeType
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.executors.position_executor.data_types import (
    PositionExecutorConfig,
    TrailingStop,
    TripleBarrierConfig,
)
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction


class DEMASTADXTokenConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    # markets: Dict[str, List[str]] = {}
    markets: MarketDict = MarketDict()
    candles_config: List[CandlesConfig] = []
    controllers_config: List[str] = []
    exchange: str = Field(default="hyperliquid_perpetual")
    trading_pairs: List[str] = Field(default=["HYPE-USD"])
    candles_exchange: str = Field(default="binance_perpetual")
    candles_pairs: List[str] = Field(default=["HYPE-USDT"])
    candles_interval: str = Field(default="5m")
    candles_length: int = Field(default=15, gt=0)

    # DEMA Configuration
    dema_length: int = Field(default=200, gt=0)

    # SuperTrend Configuration
    supertrend_length: int = Field(default=12, gt=0)
    supertrend_multiplier: float = Field(default=3.0, gt=0)

    # Order Configuration
    order_amount_quote: Decimal = Field(default=Decimal("100"), gt=0)
    leverage: int = Field(default=5, gt=0)
    position_mode: PositionMode = Field(default=PositionMode.ONEWAY)

    # Executor Timeout Configuration
    executor_timeout: int = Field(default=60, gt=0)

    # Triple Barrier Configuration
    trailing_stop_loss_pct: Decimal = Field(default=Decimal("0.01"), gt=0)

    # ADX Configuration
    adx_length: int = Field(default=14, gt=0)
    adx_threshold_choppy: float = Field(default=25.0, gt=0)
    adx_threshold_trending: float = Field(default=30.0, gt=0)
    adx_threshold_extreme: float = Field(default=45.0, gt=0)
    use_adx_confirmation: bool = Field(default=False)  # Controls whether to wait for ADX confirmation on trend changes

    # early exit tp/sl conditions
    early_exit_tp_pct: Decimal = Field(default=Decimal("0.02"), gt=0)  # when adx shows choppy market, existing position is closed if uPNL > early_exit_tp_pct
    # early_exit_sl_pct: Decimal = Field(default=Decimal("0.03"), gt=0)

    # New optional feature flags
    use_supertrend_filter: bool = Field(default=False)  # Controls whether to filter positions against higher timeframe SuperTrend direction
    supertrend_filter_interval: str = Field(default="1h")  # Configurable timeframe for SuperTrend filter
    filter_candles_length: int = Field(default=12, gt=0)  # Number of filter timeframe candles to fetch for SuperTrend calculation

    @field_validator('position_mode', mode="before")
    @classmethod
    def validate_position_mode(cls, v: str) -> PositionMode:
        if v.upper() in PositionMode.__members__:
            return PositionMode[v.upper()]
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


class DEMASTADXTokenStrategy(StrategyV2Base):
    """
    This strategy uses DEMA and SuperTrend indicators to generate trading signals.
    Supports multiple trading pairs with candles from different exchanges.
    """

    account_config_set = False

    @classmethod
    def init_markets(cls, config: DEMASTADXTokenConfig):
        cls.markets = {config.exchange: set(config.trading_pairs)}

    def __init__(self, connectors: Dict[str, ConnectorBase], config: DEMASTADXTokenConfig):
        self.max_records = max(config.dema_length, config.supertrend_length, config.candles_length) + 20

        # Calculate max records for filter timeframe if enabled
        if config.use_supertrend_filter:
            self.max_records_filter = max(config.supertrend_length, config.filter_candles_length) + 20

        if len(config.candles_config) == 0:
            for candles_pair in config.candles_pairs:
                config.candles_config.append(CandlesConfig(
                    connector=config.candles_exchange,
                    trading_pair=candles_pair,
                    interval=config.candles_interval,
                    max_records=self.max_records
                ))

                # Add filter timeframe candles config if enabled
                if config.use_supertrend_filter:
                    config.candles_config.append(CandlesConfig(
                        connector=config.candles_exchange,
                        trading_pair=candles_pair,
                        interval=config.supertrend_filter_interval,
                        max_records=self.max_records_filter
                    ))

        super().__init__(connectors, config)
        self.config = config
        # Store indicators per trading pair
        self.current_dema = {}
        self.current_supertrend_direction = {}
        self.prev_supertrend_direction = {}
        self.current_price = {}
        self.prev_price = {}
        self.prev_dema = {}
        self.current_signal = {}
        self.signal_source = {}  # Track which condition triggered the signal
        # ADX tracking
        self.current_adx = {}
        self.prev_adx = {}
        self.current_plus_di = {}
        self.current_minus_di = {}
        self.market_condition = {}  # "CHOPPY", "WEAK_TREND", "STRONG_TREND", "EXTREME_TREND"
        self.prev_market_condition = {}

        # New state tracking for optional features
        self.filter_supertrend_direction = {}  # Higher timeframe SuperTrend direction
        self.adx_confirmation_pending = {}  # Track if ADX confirmation is pending
        self.adx_confirmation_direction = {}  # Track which direction needs confirmation

        # Cached indicator values for format_status
        self.cached_trend_momentum = {}

    def start(self, clock: Clock, timestamp: float) -> None:  # clock is required by base class
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
            if signal is None:
                continue
            signal_source = self.signal_source.get(candles_pair, 0)
            active_longs, active_shorts = self.get_active_executors_by_side(self.config.exchange, trading_pair)

            if signal != 0:  # Only process non-zero signals
                mid_price = self.market_data_provider.get_price_by_type(self.config.exchange,
                                                                        trading_pair,
                                                                        PriceType.MidPrice)

                if signal == 1 and len(active_longs) == 0:
                    current_dema = self.current_dema[candles_pair]
                    # Configure triple barrier based on signal source and options
                    if signal_source == 3:  # Condition 3: Use trailing stop that activates at DEMA
                        # For long: activation price is DEMA (where we expect price to reach)
                        activation_price_pct = abs(Decimal(str(current_dema)) - mid_price) / mid_price
                        trailing_delta_pct = self.config.trailing_stop_loss_pct

                        trailing_stop = TrailingStop(
                            activation_price=Decimal(str(activation_price_pct)),
                            trailing_delta=Decimal(str(trailing_delta_pct))
                        )

                        triple_barrier_config = TripleBarrierConfig(
                            trailing_stop=trailing_stop
                        )
                    elif signal_source == 4 and mid_price > current_dema:  # Condition 4: Use DEMA as stop loss for long

                        triple_barrier_config = TripleBarrierConfig(
                            stop_loss=Decimal(str(current_dema))
                        )
                    else:  # Conditions 1 & 2: Default config with all barriers disabled
                        triple_barrier_config = TripleBarrierConfig()

                    create_actions.append(CreateExecutorAction(
                        executor_config=PositionExecutorConfig(
                            timestamp=self.current_timestamp,
                            connector_name=self.config.exchange,
                            trading_pair=trading_pair,
                            side=TradeType.BUY,
                            entry_price=mid_price,
                            amount=self.config.order_amount_quote / mid_price,
                            triple_barrier_config=triple_barrier_config,
                            leverage=self.config.leverage
                        )))
                elif signal == -1 and len(active_shorts) == 0:
                    current_dema = self.current_dema[candles_pair]
                    # Configure triple barrier based on signal source and options
                    if signal_source == 3:  # Condition 3: Use trailing stop that activates at DEMA
                        # For short: activation price is DEMA (where we expect price to reach)
                        activation_price_pct = abs(mid_price - Decimal(str(current_dema))) / mid_price
                        trailing_delta_pct = self.config.trailing_stop_loss_pct

                        trailing_stop = TrailingStop(
                            activation_price=Decimal(str(activation_price_pct)),
                            trailing_delta=Decimal(str(trailing_delta_pct))
                        )

                        triple_barrier_config = TripleBarrierConfig(
                            trailing_stop=trailing_stop
                        )
                    elif signal_source == 4 and mid_price < current_dema:  # Condition 4: Use DEMA as stop loss for short
                        triple_barrier_config = TripleBarrierConfig(
                            stop_loss=Decimal(str(current_dema))
                        )
                    else:  # Conditions 1 & 2: Default config with all barriers disabled
                        triple_barrier_config = TripleBarrierConfig()

                    create_actions.append(CreateExecutorAction(
                        executor_config=PositionExecutorConfig(
                            timestamp=self.current_timestamp,
                            connector_name=self.config.exchange,
                            trading_pair=trading_pair,
                            side=TradeType.SELL,
                            entry_price=mid_price,
                            amount=self.config.order_amount_quote / mid_price,
                            triple_barrier_config=triple_barrier_config,
                            leverage=self.config.leverage
                        )))
        return create_actions

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        stop_actions = []

        # Check signals for each trading pair
        for i, trading_pair in enumerate(self.config.trading_pairs):
            candles_pair = self.config.candles_pairs[i]

            active_longs, active_shorts = self.get_active_executors_by_side(self.config.exchange, trading_pair)
            if len(active_longs) + len(active_shorts) == 0:
                continue

            # Stop position if trend flips
            # Get current SuperTrend direction for stop logic
            current_supertrend_direction = self.current_supertrend_direction.get(candles_pair)

            if current_supertrend_direction is not None:
                # Stop positions when SuperTrend reverses
                if current_supertrend_direction == -1 and len(active_longs) > 0:
                    stop_actions.extend([StopExecutorAction(
                        controller_id=e.controller_id or "main",
                        executor_id=e.id
                    ) for e in active_longs])
                elif current_supertrend_direction == 1 and len(active_shorts) > 0:
                    stop_actions.extend([StopExecutorAction(
                        controller_id=e.controller_id or "main",
                        executor_id=e.id
                    ) for e in active_shorts])

            # NEW: ADX-based stops
            adx_value = self.current_adx.get(candles_pair, 0)

            # Stop if market becomes choppy
            if adx_value < self.config.adx_threshold_choppy:
                all_positions = active_longs + active_shorts
                for executor in all_positions:
                    # Only close profitable positions in choppy markets or wait for ST to reverse
                    if executor.net_pnl_pct > 0:
                        stop_actions.append(StopExecutorAction(
                            controller_id=executor.controller_id or "main",
                            executor_id=executor.id
                        ))
                        self.logger().info(f"Closing profitable {trading_pair} position as market turned choppy")

            # Check for timeout on unfilled executors
            all_active_executors = active_longs + active_shorts
            # find all executors that are active but not trading (unfilled) and have exceeded timeout
            unfilled_executors = [e for e in all_active_executors if e.is_active and not e.is_trading and self.current_timestamp - e.timestamp > self.config.executor_timeout]
            # stop all unfilled executors
            for executor in unfilled_executors:
                stop_actions.append(StopExecutorAction(
                    controller_id=executor.controller_id or "main",
                    executor_id=executor.id
                ))
        return stop_actions

    def get_active_executors_by_side(self, connector_name: str, trading_pair: str):
        active_executors_by_trading_pair = self.filter_executors(
            executors=self.get_all_executors(),
            filter_func=lambda e: e.connector_name == connector_name and e.trading_pair == trading_pair and e.is_active
        )
        active_longs = [e for e in active_executors_by_trading_pair if e.side == TradeType.BUY]
        active_shorts = [e for e in active_executors_by_trading_pair if e.side == TradeType.SELL]
        return active_longs, active_shorts

    def get_signal(self, connector_name: str, trading_pair: str) -> Optional[float]:
        candles = self.market_data_provider.get_candles_df(connector_name,
                                                           trading_pair,
                                                           self.config.candles_interval,
                                                           self.max_records)

        if candles is None or candles.empty:
            return None

        # Calculate indicators and update internal state
        self._calculate_indicators(candles, trading_pair)

        # Generate signal based on conditions
        signal, signal_source = self._generate_signal(trading_pair)

        # Store results and return
        self.current_signal[trading_pair] = signal
        self.signal_source[trading_pair] = signal_source
        return signal

    def _calculate_indicators(self, candles, trading_pair: str):
        """Calculate and store all indicators for the given trading pair."""
        # Calculate indicators
        candles.ta.dema(length=self.config.dema_length, append=True)
        candles.ta.supertrend(length=self.config.supertrend_length, multiplier=self.config.supertrend_multiplier, append=True)
        candles.ta.adx(length=self.config.adx_length, append=True)

        # Store previous ADX values
        if len(candles) > 1:
            self.prev_adx[trading_pair] = candles[f"ADX_{self.config.adx_length}"].iloc[-2]
        else:
            self.prev_adx[trading_pair] = candles[f"ADX_{self.config.adx_length}"].iloc[-1]

        # Get ADX values
        self.current_adx[trading_pair] = candles[f"ADX_{self.config.adx_length}"].iloc[-1]
        self.current_plus_di[trading_pair] = candles[f"DMP_{self.config.adx_length}"].iloc[-1]
        self.current_minus_di[trading_pair] = candles[f"DMN_{self.config.adx_length}"].iloc[-1]

        # Determine market condition
        self._update_market_condition(trading_pair)

        # Get current values
        self.current_price[trading_pair] = candles["close"].iloc[-1]
        self.current_dema[trading_pair] = candles[f"DEMA_{self.config.dema_length}"].iloc[-1]
        self.current_supertrend_direction[trading_pair] = candles[f"SUPERTd_{self.config.supertrend_length}_{self.config.supertrend_multiplier}"].iloc[-1]

        # Get previous values for trend change detection
        if len(candles) > 1:
            self.prev_supertrend_direction[trading_pair] = candles[f"SUPERTd_{self.config.supertrend_length}_{self.config.supertrend_multiplier}"].iloc[-2]
            self.prev_price[trading_pair] = candles["close"].iloc[-2]
            self.prev_dema[trading_pair] = candles[f"DEMA_{self.config.dema_length}"].iloc[-2]
        else:
            self.prev_supertrend_direction[trading_pair] = self.current_supertrend_direction[trading_pair]
            self.prev_price[trading_pair] = self.current_price[trading_pair]
            self.prev_dema[trading_pair] = self.current_dema[trading_pair]

        # Calculate filter timeframe SuperTrend if enabled
        if self.config.use_supertrend_filter:
            filter_candles = self.market_data_provider.get_candles_df(
                self.config.candles_exchange,
                trading_pair,
                self.config.supertrend_filter_interval,
                max(self.config.filter_candles_length, self.config.supertrend_length) + 20
            )

            if filter_candles is not None and not filter_candles.empty:
                filter_candles.ta.supertrend(
                    length=self.config.supertrend_length,
                    multiplier=self.config.supertrend_multiplier,
                    append=True
                )
                self.filter_supertrend_direction[trading_pair] = filter_candles[f"SUPERTd_{self.config.supertrend_length}_{self.config.supertrend_multiplier}"].iloc[-1]
            else:
                self.filter_supertrend_direction[trading_pair] = None

        # Cache trend momentum (slope of DI) for format_status
        if self.current_plus_di[trading_pair] > self.current_minus_di[trading_pair]:
            self.cached_trend_momentum[trading_pair] = self.current_plus_di[trading_pair]
        else:
            self.cached_trend_momentum[trading_pair] = -self.current_minus_di[trading_pair]

    def _update_market_condition(self, trading_pair: str):
        """Update market condition based on ADX value."""
        adx_value = self.current_adx[trading_pair]
        if adx_value < self.config.adx_threshold_choppy:
            self.market_condition[trading_pair] = "CHOPPY"
        elif adx_value < self.config.adx_threshold_trending:
            self.market_condition[trading_pair] = "WEAK_TREND"
        elif adx_value < self.config.adx_threshold_extreme:
            self.market_condition[trading_pair] = "STRONG_TREND"
        else:
            self.market_condition[trading_pair] = "EXTREME_TREND"

    def _generate_signal(self, trading_pair: str) -> tuple[int, int]:
        """Generate trading signal and return signal value and source."""
        # Get current values
        current_price = self.current_price[trading_pair]
        current_dema = self.current_dema[trading_pair]
        current_supertrend_direction = self.current_supertrend_direction[trading_pair]
        prev_supertrend_direction = self.prev_supertrend_direction[trading_pair]

        # Get ADX values for conditions
        current_adx_val = self.current_adx[trading_pair]
        prev_adx_val = self.prev_adx.get(trading_pair, 0)
        adx_crossed_threshold = prev_adx_val < self.config.adx_threshold_choppy and current_adx_val >= self.config.adx_threshold_choppy
        adx_above_threshold = current_adx_val >= self.config.adx_threshold_choppy

        # Check entry conditions
        long_conditions = self._check_long_conditions(current_supertrend_direction, current_price, current_dema,
                                                      adx_crossed_threshold, adx_above_threshold, prev_supertrend_direction)
        short_conditions = self._check_short_conditions(current_supertrend_direction, current_price, current_dema,
                                                        adx_crossed_threshold, adx_above_threshold, prev_supertrend_direction)

        # Determine signal and source
        signal, signal_source = self._determine_signal(long_conditions, short_conditions)

        # Apply directional filter
        signal, signal_source = self._apply_directional_filter(signal, signal_source, trading_pair)

        # Apply configurable timeframe SuperTrend filter if enabled
        if self.config.use_supertrend_filter and signal != 0:
            signal, signal_source = self._apply_supertrend_filter(signal, signal_source, trading_pair)

        # Apply ADX confirmation logic if enabled
        if self.config.use_adx_confirmation and signal != 0:
            signal, signal_source = self._check_adx_confirmation(signal, signal_source, trading_pair)

        return signal, signal_source

    def _check_long_conditions(self, current_supertrend_direction, current_price, current_dema,
                               adx_crossed_threshold, adx_above_threshold, prev_supertrend_direction) -> tuple[bool, bool, bool, bool]:
        """Check all long entry conditions."""
        # Long Entry Conditions:
        # 1. ADX crosses threshold with established bullish trend: ST already positive + Price > DEMA
        # 2. ADX already established and ST flips: ADX above threshold + ST turns positive + Price > DEMA
        # 3. ST green, ADX above threshold, price below DEMA (trailing stop activates at DEMA)
        # 4. ST green, ADX above threshold, price above DEMA (stop loss at DEMA)

        long_condition_1 = (current_supertrend_direction == 1 and
                            current_price > current_dema and
                            adx_crossed_threshold)

        long_condition_2 = (adx_above_threshold and
                            current_price > current_dema and
                            current_supertrend_direction == 1 and
                            prev_supertrend_direction == -1)

        long_condition_3 = (current_supertrend_direction == 1 and
                            adx_above_threshold and
                            current_price < current_dema)

        long_condition_4 = (current_supertrend_direction == 1 and
                            adx_above_threshold and
                            current_price > current_dema)

        return long_condition_1, long_condition_2, long_condition_3, long_condition_4

    def _check_short_conditions(self, current_supertrend_direction, current_price, current_dema,
                                adx_crossed_threshold, adx_above_threshold, prev_supertrend_direction) -> tuple[bool, bool, bool, bool]:
        """Check all short entry conditions."""
        # Short Entry Conditions:
        # 1. ADX crosses threshold with established bearish trend: ST already negative + Price < DEMA + ADX crosses above threshold
        # 2. ADX already established and ST flips: ADX above threshold + ST turns negative + Price < DEMA
        # 3. NEW: ST red, ADX above threshold, price above DEMA (trailing stop activates at DEMA)

        short_condition_1 = (current_supertrend_direction == -1 and
                             current_price < current_dema and
                             adx_crossed_threshold)

        short_condition_2 = (adx_above_threshold and
                             current_price < current_dema and
                             current_supertrend_direction == -1 and
                             prev_supertrend_direction == 1)

        short_condition_3 = (current_supertrend_direction == -1 and
                             adx_above_threshold and
                             current_price > current_dema)

        short_condition_4 = (current_supertrend_direction == -1 and
                             adx_above_threshold and
                             current_price < current_dema)

        return short_condition_1, short_condition_2, short_condition_3, short_condition_4

    def _determine_signal(self, long_conditions: tuple[bool, bool, bool, bool],
                          short_conditions: tuple[bool, bool, bool, bool]) -> tuple[int, int]:
        """Determine signal value and source based on conditions."""
        long_condition_1, long_condition_2, long_condition_3, long_condition_4 = long_conditions
        short_condition_1, short_condition_2, short_condition_3, short_condition_4 = short_conditions

        # Determine signal and track which condition triggered it
        if long_condition_1:
            return 1, 1
        elif long_condition_2:
            return 1, 2
        elif long_condition_3:
            return 1, 3
        elif long_condition_4:
            return 1, 4
        elif short_condition_1:
            return -1, 1
        elif short_condition_2:
            return -1, 2
        elif short_condition_3:
            return -1, 3
        elif short_condition_4:
            return -1, 4
        else:
            return 0, 0

    def _apply_directional_filter(self, signal: int, signal_source: int, trading_pair: str) -> tuple[int, int]:
        """Apply directional filter to ensure directional agreement."""
        # Additional filter: Ensure directional agreement
        if signal == 1 and self.current_plus_di[trading_pair] <= self.current_minus_di[trading_pair]:
            return 0, 0  # Cancel long if -DI is stronger
        elif signal == -1 and self.current_minus_di[trading_pair] <= self.current_plus_di[trading_pair]:
            return 0, 0  # Cancel short if +DI is stronger

        return signal, signal_source

    def _apply_supertrend_filter(self, signal: int, signal_source: int, trading_pair: str) -> tuple[int, int]:
        """Apply configurable timeframe SuperTrend filter to the signal."""
        filter_direction = self.filter_supertrend_direction.get(trading_pair)

        if filter_direction is None:
            # If filter data not available, allow signal to pass
            return signal, signal_source

        # Check if signal aligns with filter timeframe SuperTrend
        if signal == 1 and filter_direction == -1:
            # Block long signal if filter timeframe is bearish
            return 0, 0
        elif signal == -1 and filter_direction == 1:
            # Block short signal if filter timeframe is bullish
            return 0, 0

        return signal, signal_source

    def _check_adx_confirmation(self, signal: int, signal_source: int, trading_pair: str) -> tuple[int, int]:
        """Implement ADX confirmation logic with state machine."""
        current_adx = self.current_adx[trading_pair]
        prev_adx = self.prev_adx.get(trading_pair, 0)

        # Check if we're pending confirmation
        if trading_pair in self.adx_confirmation_pending and self.adx_confirmation_pending[trading_pair]:
            pending_direction = self.adx_confirmation_direction.get(trading_pair, 0)

            # Check if ADX has increased (confirmation)
            if current_adx > prev_adx and pending_direction == signal:
                # Confirmation received
                self.adx_confirmation_pending[trading_pair] = False
                self.adx_confirmation_direction[trading_pair] = 0
                self.logger().info(f"ADX confirmation received for {trading_pair}, executing {('long' if signal == 1 else 'short')} signal")
                return signal, signal_source
            else:
                # Still waiting for confirmation or direction changed
                if pending_direction != signal:
                    # Direction changed, update pending direction
                    self.adx_confirmation_direction[trading_pair] = signal
                self.logger().info(f"Waiting for ADX confirmation for {trading_pair} (ADX: {current_adx:.1f})")
                return 0, 0

        # No pending confirmation, check if we need to start waiting
        # Only require confirmation for ADX threshold crossing signals (source 1)
        if signal_source == 1:
            self.adx_confirmation_pending[trading_pair] = True
            self.adx_confirmation_direction[trading_pair] = signal
            self.logger().info(f"ADX threshold crossed for {trading_pair}, waiting for confirmation on next candle")
            return 0, 0

        # For other signal sources, execute immediately
        return signal, signal_source

    def _calculate_dema_stop_loss(self, side: TradeType, entry_price: Decimal,
                                  current_dema: float, current_price: float) -> Optional[Decimal]:
        """Calculate stop loss percentage based on DEMA levels."""
        if side == TradeType.BUY:
            # For long positions: stop loss at DEMA if DEMA < current price
            if current_dema < current_price:
                stop_loss_pct = (entry_price - Decimal(str(current_dema))) / entry_price
                return stop_loss_pct if stop_loss_pct > 0 else None
        else:  # TradeType.SELL
            # For short positions: stop loss at DEMA if DEMA > current price
            if current_dema > current_price:
                stop_loss_pct = (Decimal(str(current_dema)) - entry_price) / entry_price
                return stop_loss_pct if stop_loss_pct > 0 else None

        return None

    def _update_adx_confirmation_state(self, trading_pair: str):
        """Update ADX confirmation state tracking."""
        # This method is called from _calculate_indicators if needed
        # Currently the state is managed in _check_adx_confirmation
        pass

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

        # Show enabled features
        enabled_features = []
        if self.config.use_supertrend_filter:
            enabled_features.append(f"ST Filter ({self.config.supertrend_filter_interval})")
        if self.config.use_adx_confirmation:
            enabled_features.append("ADX Confirmation")

        if enabled_features:
            lines.extend(["", f"  Enabled Features: {', '.join(enabled_features)}"])

        # Create compact trading pairs overview
        lines.extend(["", "  Market Overview:"])

        # Header for the grid
        header = f"  {'Symbol':<24} {'Price':<20} {'DEMA':<20} {'Trend':<16} {'ADX':<12} {'Market':<24} {'Signal':<16}"
        if self.config.use_supertrend_filter:
            header += f" {'Filter ST':<12}"
        if self.config.use_adx_confirmation:
            header += f" {'ADX Confirm':<16}"
        header += f" {'Momentum':<12} {'Longs':<12} {'Shorts':<12}"

        separator = f"  {'-' * 24} {'-' * 20} {'-' * 20} {'-' * 16} {'-' * 12} {'-' * 24} {'-' * 16}"
        if self.config.use_supertrend_filter:
            separator += f" {'-' * 12}"
        if self.config.use_adx_confirmation:
            separator += f" {'-' * 16}"
        separator += f" {'-' * 12} {'-' * 12} {'-' * 12}"

        lines.extend([header, separator])

        # Display each trading pair in compact format
        for i, candles_pair in enumerate(self.config.candles_pairs):
            # Get indicator values for this pair (using cached values)
            price = self.current_price.get(candles_pair, 0)
            dema = self.current_dema.get(candles_pair, 0)
            st_dir = self.current_supertrend_direction.get(candles_pair, 0)
            signal = self.current_signal.get(candles_pair, 0)

            # Format signal and direction display
            signal_text = "LONG" if signal == 1 else "SHORT" if signal == -1 else "NONE"

            # Get ADX values (using cached values)
            adx = self.current_adx.get(candles_pair, 0)
            condition = self.market_condition.get(candles_pair, "UNKNOWN")

            # Get active positions for this pair
            if i < len(self.config.trading_pairs):
                actual_trading_pair = self.config.trading_pairs[i]
                active_longs, active_shorts = self.get_active_executors_by_side(self.config.exchange, actual_trading_pair)
            else:
                active_longs, active_shorts = [], []

            # Format the row
            symbol = candles_pair.replace('-', '/')
            price_str = f"{price:.4f}"
            dema_str = f"{dema:.4f}"
            trend = "BULLISH" if st_dir == 1 else "BEARISH" if st_dir == -1 else "NEUTRAL"
            adx_str = f"{adx:.1f}"

            # Shorten market condition labels for better fit
            market_map = {
                "CHOPPY": "CHOPPY",
                "WEAK_TREND": "WEAK",
                "STRONG_TREND": "STRONG",
                "EXTREME_TREND": "EXTREME",
                "UNKNOWN": "N/A"
            }
            market = market_map.get(condition, condition)

            row = f"  {symbol:<24} {price_str:<20} {dema_str:<20} {trend:<16} {adx_str:<12} {market:<24} {signal_text:<16}"

            # Add filter ST column if enabled
            if self.config.use_supertrend_filter:
                filter_st = self.filter_supertrend_direction.get(candles_pair, 0)
                filter_st_text = "BULL" if filter_st == 1 else "BEAR" if filter_st == -1 else "N/A"
                row += f" {filter_st_text:<12}"

            # Add ADX confirmation status if enabled
            if self.config.use_adx_confirmation:
                if self.adx_confirmation_pending.get(candles_pair, False):
                    confirm_text = "PENDING"
                else:
                    confirm_text = "READY"
                row += f" {confirm_text:<16}"

            # Add trend momentum (using cached value)
            momentum = self.cached_trend_momentum.get(candles_pair, 0)
            momentum_str = f"{momentum:+.1f}"
            row += f" {momentum_str:<12}"

            row += f" {len(active_longs):<12} {len(active_shorts):<12}"
            lines.append(row)

        # Add configuration info
        config_info = f"  Config: DEMA({self.config.dema_length}) | SuperTrend({self.config.supertrend_length}, {self.config.supertrend_multiplier})"
        config_info += f" | ADX({self.config.adx_length})"
        lines.extend(["", config_info])

        try:
            orders_df = self.active_orders_df()
            lines.extend(["", "  Active Orders:"] + ["    " + line for line in orders_df.to_string(index=False).split("\n")])
        except ValueError:
            lines.extend(["", "  No active maker orders."])

        # Active Position Executors
        active_executors = self.filter_executors(
            executors=self.get_all_executors(),
            filter_func=lambda e: e.is_active
        )

        if active_executors:
            lines.extend(["", "  Active Positions:"])

            # Table header
            header = f"  {'Symbol':<12} {'Side':<5} {'P&L USD':<12} {'P&L %':<8} {'Size USD':<10} {'Fees':<8} {'Status':<10}"
            separator = f"  {'-' * 12} {'-' * 5} {'-' * 10} {'-' * 10} {'-' * 12} {'-' * 8} {'-' * 10} {'-' * 8} {'-' * 10}"
            lines.extend([header, separator])

            # Table rows
            for executor_info in active_executors:
                # Format values for display
                symbol = executor_info.trading_pair
                side = "LONG" if executor_info.side == TradeType.BUY else "SHORT"
                pnl_usd = f"{executor_info.net_pnl_quote:+.2f}"
                pnl_pct = f"{executor_info.net_pnl_pct * 100:+.2f}%"
                size = f"{executor_info.filled_amount_quote:.2f}"
                fees = f"{executor_info.cum_fees_quote:.2f}"
                status = "ACTIVE" if executor_info.is_trading else "PENDING"

                row = f"  {symbol:<12} {side:<5} {pnl_usd:<12} {pnl_pct:<8} {size:<10} {fees:<8} {status:<10}"
                lines.append(row)
        else:
            lines.extend(["", "  No active position executors."])

        return "\n".join(lines)
