# Hummingbot Strategy V2 Executors Guide

## Overview

Executors are the workhorses of Strategy V2, responsible for executing trades, managing positions, and handling order lifecycle events. Each executor type is designed for specific trading patterns and use cases.

## Executor Types

### 1. PositionExecutor

**Purpose**: Manages a single position with entry and exit, including stop loss, take profit, and time limits.

**Key Features**:
- Triple barrier configuration (stop loss, take profit, time limit)
- Trailing stop support
- Position tracking and PnL calculation
- Order retry mechanism

**Configuration**:
```python
class PositionExecutorConfig(ExecutorConfigBase):
    connector_name: str
    trading_pair: str
    side: TradeType  # BUY or SELL
    amount_quote: Decimal  # Size in quote currency
    entry_price: Optional[Decimal]  # Optional limit price
    triple_barrier_config: TripleBarrierConfig
```

**Triple Barrier Config**:
```python
class TripleBarrierConfig:
    stop_loss: Optional[Decimal]  # Percentage (e.g., 0.03 for 3%)
    take_profit: Optional[Decimal]  # Percentage
    time_limit: Optional[int]  # Seconds
    trailing_stop: Optional[TrailingStop]
    open_order_type: OrderType = OrderType.MARKET
    take_profit_order_type: OrderType = OrderType.LIMIT
    stop_loss_order_type: OrderType = OrderType.MARKET
    time_limit_order_type: OrderType = OrderType.MARKET
```

**Lifecycle**:
```python
# 1. Initialization
executor = PositionExecutor(strategy, config)

# 2. Order placement
async def on_start(self):
    await self.place_order(
        connector_name=self.config.connector_name,
        trading_pair=self.config.trading_pair,
        order_type=self.config.triple_barrier_config.open_order_type,
        side=self.config.side,
        amount=self.amount,
        price=self.entry_price
    )

# 3. Position monitoring
async def control_task(self):
    # Check triple barriers
    if self.should_stop_loss():
        await self.execute_stop_loss()
    elif self.should_take_profit():
        await self.execute_take_profit()
    elif self.is_expired():
        await self.execute_time_limit()

# 4. Position closure
def early_stop(self, close_type: CloseType):
    self.close_type = close_type
    self.place_close_order()
```

**Best Practices**:
- Always validate entry price against current market
- Set reasonable stop loss and take profit levels
- Handle partial fills properly
- Implement proper fee accounting

### 2. DCAExecutor (Dollar Cost Averaging)

**Purpose**: Executes multiple orders over time or price levels to average into/out of a position.

**Key Features**:
- Time-based or price-based execution
- Configurable order levels
- Position aggregation
- Stop loss across all levels

**Configuration**:
```python
class DCAExecutorConfig(ExecutorConfigBase):
    connector_name: str
    trading_pair: str
    side: TradeType
    amount_quote: Decimal
    n_levels: int  # Number of DCA levels
    order_type: OrderType
    order_placement_mode: OrderPlacementMode  # TIME_BASED or PRICE_BASED

    # For price-based mode
    start_price: Optional[Decimal]
    end_price: Optional[Decimal]

    # For time-based mode
    time_interval: Optional[int]  # Seconds between orders

    # Risk management
    stop_loss: Optional[Decimal]
    take_profit: Optional[Decimal]
```

**Execution Modes**:

1. **Time-Based DCA**:
```python
# Places orders at regular time intervals
if self.mode == OrderPlacementMode.TIME_BASED:
    if current_time - last_order_time >= self.config.time_interval:
        self.place_next_order()
```

2. **Price-Based DCA**:
```python
# Places orders at specific price levels
if self.mode == OrderPlacementMode.PRICE_BASED:
    price_levels = self.calculate_price_levels()
    for level in price_levels:
        if self.should_place_at_level(level):
            self.place_order_at_level(level)
```

**Position Management**:
```python
@property
def average_price(self) -> Decimal:
    # Calculate weighted average entry price
    total_amount = sum(order.executed_amount_base for order in self.orders)
    total_cost = sum(order.executed_amount_quote for order in self.orders)
    return total_cost / total_amount if total_amount > 0 else Decimal("0")
```

### 3. GridExecutor

**Purpose**: Creates a grid of buy and sell orders to profit from price oscillations.

**Key Features**:
- Configurable grid levels
- Automatic order replacement
- Profit tracking per grid level
- Dynamic grid adjustment

**Configuration**:
```python
class GridExecutorConfig(ExecutorConfigBase):
    connector_name: str
    trading_pair: str
    grid_lower_bound: Decimal
    grid_upper_bound: Decimal
    n_levels: int
    amount_per_level_quote: Decimal
    order_type: OrderType = OrderType.LIMIT_MAKER

    # Advanced options
    take_profit: Optional[Decimal]
    stop_loss: Optional[Decimal]
    time_limit: Optional[int]
```

**Grid Management**:
```python
def calculate_grid_levels(self):
    # Calculate evenly spaced grid levels
    price_range = self.grid_upper_bound - self.grid_lower_bound
    step = price_range / (self.n_levels - 1)

    levels = []
    for i in range(self.n_levels):
        price = self.grid_lower_bound + (step * i)
        levels.append({
            'price': price,
            'side': TradeType.BUY if price < mid_price else TradeType.SELL,
            'amount': self.amount_per_level
        })
    return levels

async def refresh_orders(self):
    # Cancel and replace filled orders
    for level in self.grid_levels:
        if level.is_filled:
            # Place opposite order
            new_side = TradeType.SELL if level.side == TradeType.BUY else TradeType.BUY
            await self.place_grid_order(level.price, new_side)
```

### 4. ArbitrageExecutor

**Purpose**: Executes arbitrage trades between two markets.

**Key Features**:
- Cross-exchange arbitrage
- Simultaneous order execution
- Spread monitoring
- Risk limits

**Configuration**:
```python
class ArbitrageExecutorConfig(ExecutorConfigBase):
    origin_connector: str
    destination_connector: str
    trading_pair_origin: str
    trading_pair_destination: str
    amount_quote: Decimal
    min_spread: Decimal  # Minimum profitable spread
    order_type: OrderType = OrderType.MARKET
    slippage_tolerance: Decimal = Decimal("0.001")
```

**Arbitrage Logic**:
```python
async def check_arbitrage_opportunity(self):
    # Get prices from both markets
    buy_price = self.get_price(self.origin_connector, PriceType.BestAsk)
    sell_price = self.get_price(self.destination_connector, PriceType.BestBid)

    # Calculate spread
    spread = (sell_price - buy_price) / buy_price

    if spread > self.config.min_spread:
        await self.execute_arbitrage()

async def execute_arbitrage(self):
    # Place simultaneous orders
    buy_order = self.place_order(
        self.origin_connector,
        TradeType.BUY,
        self.amount
    )

    sell_order = self.place_order(
        self.destination_connector,
        TradeType.SELL,
        self.amount
    )

    # Monitor execution
    await self.monitor_execution([buy_order, sell_order])
```

### 5. TWAPExecutor (Time-Weighted Average Price)

**Purpose**: Executes large orders by splitting them into smaller chunks over time.

**Key Features**:
- Time-based order splitting
- Adaptive sizing based on liquidity
- Minimal market impact
- VWAP tracking

**Configuration**:
```python
class TWAPExecutorConfig(ExecutorConfigBase):
    connector_name: str
    trading_pair: str
    side: TradeType
    total_amount_quote: Decimal
    execution_time: int  # Total time in seconds
    n_chunks: int  # Number of order chunks
    order_type: OrderType = OrderType.LIMIT
    price_level: Decimal = Decimal("0")  # Price offset from mid
```

**TWAP Execution**:
```python
def calculate_chunk_schedule(self):
    # Divide execution time evenly
    time_per_chunk = self.config.execution_time / self.config.n_chunks
    amount_per_chunk = self.config.total_amount_quote / self.config.n_chunks

    schedule = []
    for i in range(self.config.n_chunks):
        schedule.append({
            'time': self.start_time + (i * time_per_chunk),
            'amount': amount_per_chunk
        })
    return schedule

async def control_task(self):
    current_chunk = self.get_current_chunk()
    if current_chunk and not current_chunk.is_executed:
        await self.execute_chunk(current_chunk)
```

### 6. XEMMExecutor (Cross-Exchange Market Making)

**Purpose**: Provides liquidity on one exchange while hedging on another.

**Key Features**:
- Maker-taker model
- Inventory management
- Spread optimization
- Hedge execution

**Configuration**:
```python
class XEMMExecutorConfig(ExecutorConfigBase):
    maker_connector: str
    taker_connector: str
    trading_pair_maker: str
    trading_pair_taker: str
    buy_spread: Decimal
    sell_spread: Decimal
    order_amount_quote: Decimal

    # Inventory management
    target_inventory: Decimal = Decimal("0")
    inventory_range: Decimal = Decimal("0.1")

    # Risk parameters
    max_exposure: Decimal
    hedge_ratio: Decimal = Decimal("1.0")
```

### 7. FundingArbitrageExecutor

**Purpose**: Captures funding rate differentials in perpetual markets.

**Key Features**:
- Funding rate monitoring
- Position balancing
- Automatic rebalancing
- PnL tracking including funding

**Configuration**:
```python
class FundingArbitrageExecutorConfig(ExecutorConfigBase):
    spot_connector: str
    perp_connector: str
    trading_pair_spot: str
    trading_pair_perp: str
    amount_quote: Decimal
    min_funding_rate: Decimal  # Minimum profitable funding rate
    rebalance_threshold: Decimal = Decimal("0.01")
```

## Common Executor Patterns

### 1. Order Placement Pattern

```python
async def place_orders(self):
    # Validate balance
    if not await self.validate_sufficient_balance():
        self.logger().error("Insufficient balance")
        return

    # Create order candidate
    order_candidate = OrderCandidate(
        trading_pair=self.config.trading_pair,
        is_maker=self.config.order_type == OrderType.LIMIT_MAKER,
        order_type=self.config.order_type,
        order_side=self.config.side,
        amount=self.amount,
        price=self.price
    )

    # Adjust for budget
    adjusted_candidates = self.adjust_order_candidates(
        self.config.connector_name,
        [order_candidate]
    )

    # Place order
    if adjusted_candidates:
        order_id = self.place_order(
            connector_name=self.config.connector_name,
            trading_pair=self.config.trading_pair,
            order_type=self.config.order_type,
            side=self.config.side,
            amount=adjusted_candidates[0].amount,
            price=adjusted_candidates[0].price
        )
```

### 2. Event Processing Pattern

```python
def process_order_filled_event(self, event_tag, market, event):
    # Update tracked order
    order = self.get_in_flight_order(
        self.config.connector_name,
        event.order_id
    )

    # Update internal state
    if order == self._open_order:
        self.logger().info(f"Entry order filled: {event.trade_fee}")
        # Check if should place exit order
        if self.should_place_exit_order():
            self.place_exit_order()

    elif order == self._close_order:
        self.logger().info(f"Exit order filled: {event.trade_fee}")
        # Check if position fully closed
        if self.is_position_closed():
            self.stop()
```

### 3. PnL Calculation Pattern

```python
def get_net_pnl_quote(self) -> Decimal:
    # Calculate realized PnL
    realized_pnl = Decimal("0")
    if self._close_order and self._close_order.executed_amount_base > 0:
        avg_buy_price = self._open_order.average_executed_price
        avg_sell_price = self._close_order.average_executed_price
        closed_amount = min(
            self._open_order.executed_amount_base,
            self._close_order.executed_amount_base
        )

        if self.config.side == TradeType.BUY:
            realized_pnl = (avg_sell_price - avg_buy_price) * closed_amount
        else:
            realized_pnl = (avg_buy_price - avg_sell_price) * closed_amount

    # Calculate unrealized PnL
    unrealized_pnl = Decimal("0")
    open_amount = self.open_filled_amount - self.close_filled_amount
    if open_amount > 0:
        current_price = self.get_price(
            self.config.connector_name,
            self.config.trading_pair,
            PriceType.MidPrice
        )
        entry_price = self._open_order.average_executed_price

        if self.config.side == TradeType.BUY:
            unrealized_pnl = (current_price - entry_price) * open_amount
        else:
            unrealized_pnl = (entry_price - current_price) * open_amount

    # Subtract fees
    total_fees = self.get_cum_fees_quote()

    return realized_pnl + unrealized_pnl - total_fees
```

### 4. Risk Management Pattern

```python
async def control_task(self):
    # Check all risk conditions
    risk_checks = [
        (self.should_stop_loss, CloseType.STOP_LOSS),
        (self.should_take_profit, CloseType.TAKE_PROFIT),
        (self.is_expired, CloseType.TIME_LIMIT),
        (self.is_trailing_stop_triggered, CloseType.TRAILING_STOP)
    ]

    for check_func, close_type in risk_checks:
        if check_func():
            await self.early_stop(close_type)
            return

    # Normal operations
    await self.manage_position()
```

## Executor Development Guidelines

### 1. State Management
- Track all orders with TrackedOrder objects
- Maintain accurate position state
- Handle partial fills correctly
- Clean up on termination

### 2. Error Handling
- Implement retry logic for failed orders
- Handle insufficient balance gracefully
- Log all important events
- Validate market conditions

### 3. Performance Optimization
- Calculate expensive metrics once per update
- Cache frequently accessed data
- Minimize API calls
- Batch order operations when possible

### 4. Testing Considerations
- Test all order event scenarios
- Verify PnL calculations
- Test edge cases (partial fills, failures)
- Validate risk management triggers

## Custom Executor Template

```python
from typing import Dict, List, Optional
from decimal import Decimal
from hummingbot.strategy_v2.executors.executor_base import ExecutorBase
from hummingbot.strategy_v2.executors.data_types import ExecutorConfigBase

class CustomExecutorConfig(ExecutorConfigBase):
    # Add your configuration fields
    connector_name: str
    trading_pair: str
    # ... other fields

class CustomExecutor(ExecutorBase):
    def __init__(self, strategy, config: CustomExecutorConfig,
                 update_interval: float = 1.0):
        super().__init__(strategy, [config.connector_name],
                        config, update_interval)
        self.config = config
        # Initialize your state

    async def on_start(self):
        """Called when executor starts"""
        await self.validate_sufficient_balance()
        # Place initial orders

    async def control_task(self):
        """Main control loop - called every update_interval"""
        # Implement your logic
        pass

    def get_net_pnl_quote(self) -> Decimal:
        """Calculate and return PnL"""
        return Decimal("0")

    def get_net_pnl_pct(self) -> Decimal:
        """Calculate and return PnL percentage"""
        return Decimal("0")

    def get_cum_fees_quote(self) -> Decimal:
        """Calculate and return cumulative fees"""
        return Decimal("0")

    def early_stop(self, close_type: CloseType):
        """Handle early termination"""
        self.close_type = close_type
        # Clean up orders
        self.stop()

    # Implement order event handlers as needed
    def process_order_filled_event(self, event_tag, market, event):
        # Handle order fills
        pass
```
