import logging
from decimal import Decimal

from hummingbot.core.data_type.common import PositionAction, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.strategy_v2.executors.position_executor.data_types import ExistingPositionExecutorConfig
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import TrackedOrder


class ExistingPositionExecutor(PositionExecutor):
    _logger = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self, strategy: ScriptStrategyBase, config: ExistingPositionExecutorConfig,
                 update_interval: float = 1.0, max_retries: int = 10):
        super().__init__(strategy=strategy, config=config, update_interval=update_interval, max_retries=max_retries)
        self.config: ExistingPositionExecutorConfig = config
        # Create a mock tracked order representing the existing position
        self._open_order = self._create_mock_tracked_order()
        self.logger().info(f"Created ExistingPositionExecutor for {config.trading_pair} "
                           f"{config.side.name} position with amount {config.existing_position_amount}")

    def _create_mock_tracked_order(self) -> TrackedOrder:
        """
        Create a TrackedOrder that represents the existing position as a "filled" order.
        """
        mock_order = TrackedOrder(order_id="existing_position")

        # Create a mock InFlightOrder with filled state
        mock_in_flight = InFlightOrder(
            client_order_id="existing_position",
            exchange_order_id="existing_position",
            trading_pair=self.config.trading_pair,
            order_type=self.config.triple_barrier_config.open_order_type,
            trade_type=self.config.side,
            price=self.config.existing_entry_price,
            amount=self.config.existing_position_amount,
            creation_timestamp=self._strategy.current_timestamp,
            initial_state=OrderState.FILLED,
            leverage=self.config.existing_leverage,
            position=PositionAction.OPEN.name,
        )

        # Set the executed amount and average price
        mock_in_flight.executed_amount_base = self.config.existing_position_amount
        mock_in_flight.executed_amount_quote = self.config.existing_position_amount * self.config.existing_entry_price
        mock_in_flight.last_update_timestamp = self._strategy.current_timestamp
        mock_in_flight.current_state = OrderState.FILLED

        # Attach the mock order to the tracked order
        mock_order.order = mock_in_flight

        return mock_order

    async def on_start(self):
        """
        Skip balance validation since position already exists.
        Move directly to barrier management.
        """
        self._status = RunnableStatus.RUNNING
        self.start_timestamp = self._strategy.current_timestamp
        self.logger().info(f"Starting management of existing position for {self.config.trading_pair}")

    def control_open_order(self):
        """
        Do nothing since the position is already open.
        """
        pass

    def place_open_order(self):
        """
        Should never be called for existing positions.
        """
        self.logger().error(f"place_open_order called on ExistingPositionExecutor for {self.config.trading_pair}")
        raise RuntimeError("Cannot place open order for existing position")

    def validate_sufficient_balance(self) -> bool:
        """
        Skip balance validation since the position already exists and is funded.
        """
        return True

    @property
    def open_filled_amount(self) -> Decimal:
        """
        Return the existing position amount directly from config.
        """
        return self.config.existing_position_amount

    @property
    def entry_price(self) -> Decimal:
        """
        Return the existing entry price from config.
        """
        return self.config.existing_entry_price

    def get_custom_info(self) -> dict:
        """
        Include additional information about the existing position source.
        """
        custom_info = super().get_custom_info()
        custom_info.update({
            "is_existing_position": True,
            "original_entry_price": str(self.config.existing_entry_price),
            "original_position_amount": str(self.config.existing_position_amount),
            "original_leverage": self.config.existing_leverage,
            "initial_unrealized_pnl": str(self.config.existing_unrealized_pnl),
        })
        return custom_info

    def to_format_status(self) -> list:
        """
        Include information that this is managing an existing position.
        """
        lines = []
        lines.append(f"  Executor ID: {self.config.id}")
        lines.append("   Type: Existing Position Executor")
        lines.append(f"  Trading Pair: {self.config.trading_pair}")
        lines.append(f"  Side: {self.config.side.name}")
        lines.append(f"  Position Amount: {self.open_filled_amount:.6f}")
        lines.append(f"  Entry Price: {self.entry_price:.6f}")
        lines.append(f"  Leverage: {self.config.existing_leverage}x")

        if self.config.triple_barrier_config.stop_loss:
            stop_loss_price = self.entry_price * (1 - self.config.triple_barrier_config.stop_loss) if self.config.side == TradeType.BUY else self.entry_price * (1 + self.config.triple_barrier_config.stop_loss)
            lines.append(f"  Stop Loss: {stop_loss_price:.6f} ({self.config.triple_barrier_config.stop_loss:.2%})")

        if self.config.triple_barrier_config.take_profit:
            take_profit_price = self.take_profit_price
            lines.append(f"  Take Profit: {take_profit_price:.6f} ({self.config.triple_barrier_config.take_profit:.2%})")

        if self.config.triple_barrier_config.trailing_stop:
            lines.append(f"  Trailing Stop: Activation at {self.config.triple_barrier_config.trailing_stop.activation_price:.2%}, "
                         f"Delta {self.config.triple_barrier_config.trailing_stop.trailing_delta:.2%}")

        if self.config.triple_barrier_config.time_limit:
            remaining_time = (self.end_time - self._strategy.current_timestamp) if self.end_time else 0
            lines.append(f"  Time Limit: {remaining_time:.0f} seconds remaining")

        lines.append(f"  Current PNL: {self.net_pnl_pct:.2%} ({self.net_pnl_quote:.6f} {self.config.trading_pair.split('-')[1]})")
        lines.append(f"  Status: {self.status.name}")

        return lines
