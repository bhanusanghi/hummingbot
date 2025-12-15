"""
MMGrid Backtesting Engine

This backtester is specifically designed for mm_grid_kodiak_target strategy.
It pre-computes all data into TickData and calls the SAME strategy.create_proposal()
method used in live trading.

Key design:
- Connector only used for quantization rules (price/size tick)
- All market data pre-compiled into TickData
- Strategy's create_proposal() is a PURE FUNCTION of TickData + config
- Engine handles: fills, position tracking, PnL, timing state
- Same create_proposal() works for both live and backtest
"""

import logging
from decimal import Decimal
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np
import pandas as pd

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.core.data_type.order_candidate import PerpetualOrderCandidate

from scripts.backtester.data_types import (
    BacktestConfig,
    BacktestResult,
    Fill,
    OrderStatus,
    SimulatedOrder,
    TickData,
)

if TYPE_CHECKING:
    from scripts.mm_grid_kodiak_target import MMGrid, MMGridConfig

logger = logging.getLogger(__name__)


def sign(x) -> int:
    """Return sign of number: -1, 0, or 1"""
    return (x > 0) - (x < 0)


class MMGridBacktester:
    """
    Backtesting engine for MMGrid strategy.

    Usage:
        config = BacktestConfig(candles_path="data/btc_1m.csv", ...)
        strategy = MMGrid.create_for_backtest(strategy_config)  # Bypasses connector init
        backtester = MMGridBacktester(strategy, strategy_config, config)
        result = backtester.run()
    """

    def __init__(
        self,
        strategy: "MMGrid",
        strategy_config: "MMGridConfig",
        backtest_config: BacktestConfig,
    ):
        self.strategy = strategy
        self.strategy_config = strategy_config
        self.config = backtest_config

        # Load candle data
        self.candles = self._load_candles()

        # Engine state - position and PnL
        self.position: Decimal = backtest_config.initial_position
        self.entry_price: Optional[Decimal] = None
        self.realized_pnl: Decimal = Decimal("0")
        self.initial_capital: Decimal = backtest_config.initial_capital

        # Engine state - orders
        self.orders: Dict[str, SimulatedOrder] = {}
        self._order_counter: int = 0

        # Engine state - timing (passed to TickData for strategy to check)
        self._last_order_created_timestamp: int = 0
        self._last_fill_timestamp: int = 0
        self._last_fill_direction: int = 0  # -1 (sell), 0 (none), 1 (buy)
        self._is_last_fill_in_same_direction: bool = False

        # Results tracking
        self.fills: List[Fill] = []
        self.equity_curve: List[Dict] = []

        # EMA state (engine manages this for backtest)
        self._mid_history: List[Decimal] = []
        self._ema_mid: Decimal = Decimal("0")

    def _load_candles(self) -> pd.DataFrame:
        """Load and prepare candle data"""
        df = pd.read_csv(self.config.candles_path)

        # Ensure required columns
        required = ["timestamp", "open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing columns in candles CSV: {missing}")

        # Convert timestamp to int if needed
        df["timestamp"] = df["timestamp"].astype(int)

        # Filter by time range if specified
        if self.config.start_timestamp:
            df = df[df["timestamp"] >= self.config.start_timestamp]
        if self.config.end_timestamp:
            df = df[df["timestamp"] <= self.config.end_timestamp]

        df = df.sort_values("timestamp").reset_index(drop=True)
        logger.info(f"Loaded {len(df)} candles from {self.config.candles_path}")
        return df

    def _create_synthetic_orderbook(
        self, best_price: Decimal, side: str
    ) -> pd.DataFrame:
        """
        Create synthetic order book levels from candle price.

        Args:
            best_price: Best bid or ask price
            side: 'bid' or 'ask'

        Returns:
            DataFrame with columns: price, amount, update_id
        """
        levels = []
        spacing = best_price * self.config.level_spacing_bps / Decimal("10000")

        for i in range(self.config.orderbook_levels):
            if side == "bid":
                price = best_price - (spacing * i)
            else:
                price = best_price + (spacing * i)

            levels.append(
                OrderBookRow(
                    float(price), float(self.config.level_size), 1  # update_id
                )
            )

        return pd.DataFrame(data=levels, columns=OrderBookRow._fields, dtype="float64")

    def calculate_tick_data(self, candle: pd.Series, timestamp: int) -> TickData:
        """
        Pre-compile all data needed for create_proposal.

        This is the key method that converts raw candle data into
        the TickData structure that strategy needs.

        The resulting TickData has the same structure as what
        strategy.process_tick_data() produces in live mode.
        """
        close = Decimal(str(candle["close"]))
        spread = close * self.config.spread_bps / Decimal("10000")

        # Synthetic best bid/ask from close price
        best_bid = close - spread / 2
        best_ask = close + spread / 2

        # Create synthetic order book depth
        bids_df = self._create_synthetic_orderbook(best_bid, "bid")
        asks_df = self._create_synthetic_orderbook(best_ask, "ask")

        # Mid price (weighted by size - but sizes are equal so just average)
        mid_price = (best_bid + best_ask) / 2

        return TickData(
            timestamp=timestamp,
            best_bid_price=best_bid,
            best_bid_size=self.config.level_size,
            best_ask_price=best_ask,
            best_ask_size=self.config.level_size,
            bids_df=bids_df,
            asks_df=asks_df,
            mid_price=mid_price,
            mark_price=close,  # Use close as mark price in backtest
            ema_mid=self._ema_mid,  # Pre-calculated EMA
            position=self.position,
            last_order_created_timestamp=self._last_order_created_timestamp,
            last_fill_timestamp=self._last_fill_timestamp,
            last_fill_direction=self._last_fill_direction,
            is_last_fill_in_same_direction=self._is_last_fill_in_same_direction,
            order_refresh_time=self.strategy_config.order_refresh_time,
            order_cooldown=self.strategy_config.order_cooldown,
        )

    def _update_ema(self, mid_price: Decimal) -> Decimal:
        """Update EMA of mid price - mirrors strategy logic"""
        self._mid_history.append(mid_price)

        max_len = self.strategy_config.ema_window
        if len(self._mid_history) > max_len:
            self._mid_history = self._mid_history[-max_len:]

        s = pd.Series([float(m) for m in self._mid_history])
        ema_val = s.ewm(span=self.strategy_config.ema_window, adjust=False).mean().iloc[-1]
        self._ema_mid = Decimal(str(ema_val))
        return self._ema_mid

    def simulate_fills(self, candle: pd.Series) -> List[Tuple[SimulatedOrder, Decimal]]:
        """
        Check if any orders would fill given candle's high/low.

        Returns list of (order, fill_price) tuples.
        """
        fills = []
        high = Decimal(str(candle["high"]))
        low = Decimal(str(candle["low"]))

        for order in list(self.orders.values()):
            if order.status != OrderStatus.OPEN:
                continue

            fill_price = None

            if order.side == TradeType.BUY:
                # Buy limit fills if low touches order price
                if low <= order.price:
                    fill_price = order.price  # Assume fill at limit price
            else:
                # Sell limit fills if high touches order price
                if high >= order.price:
                    fill_price = order.price

            if fill_price is not None:
                fills.append((order, fill_price))

        return fills

    def process_fill(
        self, order: SimulatedOrder, fill_price: Decimal, timestamp: int
    ) -> Fill:
        """
        Process an order fill: update position, PnL, and cooldown.
        """
        fill_amount = order.remaining_amount
        fee = fill_amount * fill_price * self.config.trade_fee_bps / Decimal("10000")

        # Track PnL
        trade_pnl = Decimal("0")

        if order.side == TradeType.BUY:
            if self.position >= 0:
                # Adding to long or opening long
                if self.position == 0:
                    self.entry_price = fill_price
                else:
                    # Average entry price
                    total_cost = self.entry_price * self.position + fill_price * fill_amount
                    self.entry_price = total_cost / (self.position + fill_amount)
                self.position += fill_amount
            else:
                # Closing short
                close_amount = min(fill_amount, abs(self.position))
                trade_pnl = (self.entry_price - fill_price) * close_amount
                self.position += fill_amount
                if self.position >= 0:
                    self.entry_price = fill_price if self.position > 0 else None
        else:
            # SELL
            if self.position <= 0:
                # Adding to short or opening short
                if self.position == 0:
                    self.entry_price = fill_price
                else:
                    total_cost = self.entry_price * abs(self.position) + fill_price * fill_amount
                    self.entry_price = total_cost / (abs(self.position) + fill_amount)
                self.position -= fill_amount
            else:
                # Closing long
                close_amount = min(fill_amount, self.position)
                trade_pnl = (fill_price - self.entry_price) * close_amount
                self.position -= fill_amount
                if self.position <= 0:
                    self.entry_price = fill_price if self.position < 0 else None

        # Update realized PnL (subtract fee)
        self.realized_pnl += trade_pnl - fee

        # Update order status
        order.filled_amount += fill_amount
        order.status = OrderStatus.FILLED

        # Remove filled order
        if order.id in self.orders:
            del self.orders[order.id]

        # Update timing state (passed to TickData for strategy to check cooldown)
        self._last_fill_timestamp = timestamp
        self._last_fill_direction = 1 if order.side == TradeType.BUY else -1

        # Create fill record
        fill = Fill(
            timestamp=timestamp,
            order_id=order.id,
            trading_pair=order.trading_pair,
            side=order.side,
            price=fill_price,
            amount=fill_amount,
            fee=fee,
            position_after=self.position,
            realized_pnl=trade_pnl - fee,
            cumulative_pnl=self.realized_pnl,
        )
        self.fills.append(fill)

        logger.debug(
            f"Fill: {order.side.name} {fill_amount} @ {fill_price}, "
            f"PnL: {trade_pnl - fee:.4f}, Position: {self.position}"
        )

        return fill

    def process_proposals(
        self, proposals: List[PerpetualOrderCandidate], timestamp: int
    ) -> None:
        """
        Convert strategy proposals into simulated orders.
        Cancel all existing orders first (like real strategy does).
        """
        # Cancel all existing orders (strategy does cancel-all before placing)
        self.orders.clear()

        # Create new orders from proposals
        for proposal in proposals:
            order_id = f"bt_{self._order_counter}"
            self._order_counter += 1

            order = SimulatedOrder(
                id=order_id,
                trading_pair=proposal.trading_pair,
                side=proposal.order_side,  # PerpetualOrderCandidate uses order_side
                price=proposal.price,
                amount=proposal.amount,
                order_type=proposal.order_type,
                created_at=timestamp,
            )
            self.orders[order_id] = order

        if proposals:
            # Update timing state (passed to TickData for strategy to check refresh)
            self._last_order_created_timestamp = timestamp
            logger.debug(f"Placed {len(proposals)} orders at {timestamp}")

    def _record_equity(self, candle: pd.Series, timestamp: int) -> None:
        """Record equity curve point"""
        close = Decimal(str(candle["close"]))

        # Calculate unrealized PnL
        unrealized_pnl = Decimal("0")
        if self.position != 0 and self.entry_price:
            if self.position > 0:
                unrealized_pnl = (close - self.entry_price) * self.position
            else:
                unrealized_pnl = (self.entry_price - close) * abs(self.position)

        equity = self.initial_capital + self.realized_pnl + unrealized_pnl

        self.equity_curve.append({
            "timestamp": timestamp,
            "equity": float(equity),
            "position": float(self.position),
            "realized_pnl": float(self.realized_pnl),
            "unrealized_pnl": float(unrealized_pnl),
            "close": float(close),
        })

    def run(self) -> BacktestResult:
        """
        Execute the backtest.

        For each candle:
        1. Simulate fills from price action
        2. Update EMA (engine manages this state)
        3. Calculate TickData (includes EMA)
        4. Call strategy.create_proposal(tick_data) - SAME method as live!
        5. Process proposals into orders
        6. Record equity
        """
        logger.info(f"Starting backtest with {len(self.candles)} candles")

        for idx, candle in self.candles.iterrows():
            timestamp = int(candle["timestamp"])

            # 1. Simulate fills from this candle's price action
            fills = self.simulate_fills(candle)
            for order, fill_price in fills:
                self.process_fill(order, fill_price, timestamp)

            # 2. Update EMA (engine manages this state for backtest)
            mid = Decimal(str(candle["close"]))
            self._update_ema(mid)

            # 3. Calculate TickData (includes EMA, timing state)
            tick_data = self.calculate_tick_data(candle, timestamp)

            # 4. Call strategy's create_proposal - SAME method used in live trading!
            proposals = self.strategy.create_proposal(tick_data)

            # 5. Process proposals if any
            if proposals:
                self.process_proposals(proposals, timestamp)

            # 6. Record equity curve
            self._record_equity(candle, timestamp)

            if idx % 10000 == 0:
                logger.info(f"Processed {idx}/{len(self.candles)} candles")

        logger.info("Backtest complete")
        return self._calculate_results()

    def _calculate_results(self) -> BacktestResult:
        """Calculate summary statistics from backtest"""
        equity_df = pd.DataFrame(self.equity_curve)

        if equity_df.empty:
            return BacktestResult(
                total_pnl=Decimal("0"),
                total_pnl_pct=Decimal("0"),
                total_trades=0,
                total_volume=Decimal("0"),
                win_rate=Decimal("0"),
                profit_factor=Decimal("0"),
                max_drawdown=Decimal("0"),
                max_drawdown_pct=Decimal("0"),
                sharpe_ratio=0.0,
                equity_curve=equity_df,
                fills=self.fills,
                final_position=self.position,
                final_equity=self.initial_capital,
            )

        # Total PnL
        final_equity = Decimal(str(equity_df["equity"].iloc[-1]))
        total_pnl = final_equity - self.initial_capital
        total_pnl_pct = total_pnl / self.initial_capital * 100

        # Trade statistics
        total_trades = len(self.fills)
        total_volume = sum(f.amount * f.price for f in self.fills)

        # Win rate
        winning_trades = [f for f in self.fills if f.realized_pnl > 0]
        win_rate = Decimal(len(winning_trades)) / Decimal(total_trades) if total_trades > 0 else Decimal("0")

        # Profit factor
        gross_profit = sum(f.realized_pnl for f in self.fills if f.realized_pnl > 0)
        gross_loss = abs(sum(f.realized_pnl for f in self.fills if f.realized_pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else Decimal("999")

        # Drawdown
        equity_series = equity_df["equity"]
        peak = equity_series.expanding().max()
        drawdown = equity_series - peak
        max_drawdown = Decimal(str(abs(drawdown.min())))
        max_drawdown_pct = max_drawdown / self.initial_capital * 100

        # Sharpe ratio (simplified - daily returns assumed)
        returns = equity_df["equity"].pct_change().dropna()
        sharpe_ratio = float(returns.mean() / returns.std() * np.sqrt(252)) if len(returns) > 1 and returns.std() > 0 else 0.0

        return BacktestResult(
            total_pnl=total_pnl,
            total_pnl_pct=total_pnl_pct,
            total_trades=total_trades,
            total_volume=total_volume,
            win_rate=win_rate,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
            sharpe_ratio=sharpe_ratio,
            equity_curve=equity_df,
            fills=self.fills,
            final_position=self.position,
            final_equity=final_equity,
        )
