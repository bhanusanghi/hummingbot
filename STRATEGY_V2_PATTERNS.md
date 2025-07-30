# Hummingbot Strategy V2 Patterns and Best Practices

## Overview

This guide provides common patterns, best practices, and solutions to avoid bugs when developing Strategy V2 components. Following these patterns will help ensure robust, maintainable, and efficient trading strategies.

## Signal Generation Patterns

### 1. Multi-Timeframe Confirmation Pattern

```python
# In controller's update_processed_data()
async def update_processed_data(self):
    # Fetch multiple timeframes
    candles_5m = self.market_data_provider.get_candles(
        connector=self.config.candles_exchange,
        trading_pair=self.config.trading_pair,
        interval="5m"
    )

    candles_1h = self.market_data_provider.get_candles(
        connector=self.config.candles_exchange,
        trading_pair=self.config.trading_pair,
        interval="1h"
    )

    # Calculate indicators on each timeframe
    signal_5m = self.calculate_signal(candles_5m)
    trend_1h = self.calculate_trend(candles_1h)

    # Combine signals with confirmation
    self.current_signal = "BUY" if signal_5m == "BUY" and trend_1h == "BULLISH" else None
```

### 2. Signal Smoothing Pattern

```python
# Avoid noisy signals by requiring confirmation
class SignalBuffer:
    def __init__(self, required_confirmations: int = 3):
        self.buffer = []
        self.required_confirmations = required_confirmations

    def add_signal(self, signal: str) -> Optional[str]:
        self.buffer.append(signal)
        if len(self.buffer) > self.required_confirmations:
            self.buffer.pop(0)

        # Return signal only if all confirmations match
        if len(self.buffer) == self.required_confirmations:
            if all(s == self.buffer[0] for s in self.buffer):
                return self.buffer[0]
        return None

# Usage in controller
self.signal_buffer = SignalBuffer(3)
confirmed_signal = self.signal_buffer.add_signal(raw_signal)
```

### 3. Market Condition Filter Pattern

```python
def determine_executor_actions(self) -> List[ExecutorAction]:
    actions = []

    # Check market conditions first
    market_condition = self.assess_market_condition()

    if market_condition == "CHOPPY":
        # Don't trade in choppy markets
        return actions

    if market_condition == "TRENDING" and self.current_signal:
        # Only trade in trending markets
        actions.append(CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=self.create_position_config()
        ))

    return actions

def assess_market_condition(self) -> str:
    # Example using ADX
    if self.current_adx < 25:
        return "CHOPPY"
    elif self.current_adx > 30:
        return "TRENDING"
    else:
        return "TRANSITIONING"
```

## Executor Management Patterns

### 1. Single Active Executor Pattern

```python
def determine_executor_actions(self) -> List[ExecutorAction]:
    actions = []

    # Get active executors
    active_executors = self.filter_executors(
        self.executors_info,
        lambda e: e.is_active and e.controller_id == self.config.id
    )

    # Only create new executor if none active
    if len(active_executors) == 0 and self.should_create_executor():
        actions.append(CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=self.create_executor_config()
        ))

    # Check if should stop existing executors
    for executor in active_executors:
        if self.should_stop_executor(executor):
            actions.append(StopExecutorAction(
                controller_id=self.config.id,
                executor_id=executor.id,
                keep_position=False
            ))

    return actions
```

### 2. Executor Cooldown Pattern

```python
class ControllerWithCooldown(ControllerBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_executor_close_time = {}
        self.cooldown_period = 60  # seconds

    def can_create_executor(self) -> bool:
        # Check cooldown after executor closure
        current_time = self._strategy.current_timestamp

        for executor in self.executors_info:
            if executor.close_timestamp:
                self.last_executor_close_time[executor.id] = executor.close_timestamp

        if self.last_executor_close_time:
            last_close = max(self.last_executor_close_time.values())
            if current_time - last_close < self.cooldown_period:
                return False

        return True
```

### 3. Position Size Management Pattern

```python
def create_executor_config(self) -> PositionExecutorConfig:
    # Dynamic position sizing based on available balance
    connector = self.connectors[self.config.connector_name]
    quote_balance = connector.get_available_balance(self.quote_asset)

    # Use percentage of available balance
    position_size_pct = self.config.position_size_pct
    max_position_size = self.config.max_position_size_quote

    # Calculate position size
    position_size = min(
        quote_balance * position_size_pct,
        max_position_size
    )

    # Ensure minimum size
    min_size = self.trading_rules.min_notional_size
    if position_size < min_size:
        self.logger().warning(f"Position size {position_size} below minimum {min_size}")
        return None

    return PositionExecutorConfig(
        connector_name=self.config.connector_name,
        trading_pair=self.config.trading_pair,
        side=self.determine_side(),
        amount_quote=position_size,
        triple_barrier_config=self.get_triple_barrier_config()
    )
```

## Status Display Patterns

### 1. Comprehensive Status Format

```python
def to_format_status(self) -> List[str]:
    status = []

    # Market data status
    status.append(f"Market: {self.config.trading_pair}")
    status.append(f"Price: {self.current_price:.4f}")

    # Indicator status
    status.append(f"Signal: {self.current_signal or 'NEUTRAL'}")
    status.append(f"Trend: {self.market_condition}")

    # Position status
    active_executors = [e for e in self.executors_info if e.is_active]
    if active_executors:
        for executor in active_executors:
            status.append(f"Position: {executor.config.side.name}")
            status.append(f"PnL: {executor.net_pnl_quote:.2f} ({executor.net_pnl_pct:.2%})")
    else:
        status.append("Position: None")

    # Risk metrics
    total_exposure = sum(e.filled_amount_quote for e in active_executors)
    status.append(f"Exposure: {total_exposure:.2f}")

    return status
```

### 2. Table Format Pattern

```python
def to_format_status(self) -> List[str]:
    # Create DataFrame for better visualization
    import pandas as pd

    data = []
    for executor in self.executors_info:
        if executor.is_active:
            data.append({
                'ID': executor.id[:8],
                'Side': executor.config.side.name,
                'Amount': f"{executor.filled_amount_quote:.2f}",
                'PnL': f"{executor.net_pnl_quote:.2f}",
                'PnL%': f"{executor.net_pnl_pct:.2%}",
                'Status': executor.status.name
            })

    if data:
        df = pd.DataFrame(data)
        return [df.to_string(index=False)]
    else:
        return ["No active positions"]
```

## Order Management Patterns

### 1. Order Retry Pattern

```python
class ExecutorWithRetry(ExecutorBase):
    def __init__(self, *args, max_retries: int = 3, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_retries = max_retries
        self.retry_count = 0
        self.retry_delay = 5.0  # seconds
        self.last_retry_time = 0

    async def control_task(self):
        # Check if need to retry order
        if self.should_retry_order():
            current_time = self._strategy.current_timestamp
            if current_time - self.last_retry_time >= self.retry_delay:
                await self.retry_order_placement()
                self.last_retry_time = current_time

    def should_retry_order(self) -> bool:
        return (not self._open_order and
                self.retry_count < self.max_retries and
                self.status == RunnableStatus.RUNNING)

    async def retry_order_placement(self):
        self.retry_count += 1
        self.logger().info(f"Retrying order placement (attempt {self.retry_count})")
        await self.place_orders()

    def process_order_failed_event(self, event_tag, market, event):
        self.logger().error(f"Order failed: {event.order_id}")
        # Will trigger retry in control_task
```

### 2. Partial Fill Handling Pattern

```python
def process_order_filled_event(self, event_tag, market, event):
    order = self.get_in_flight_order(
        self.config.connector_name,
        event.order_id
    )

    # Handle partial fills
    if event.trade_type == TradeType.BUY:
        self.total_buy_amount += Decimal(str(event.amount))
        self.total_buy_quote += Decimal(str(event.amount * event.price))

    # Check if order complete
    if order.is_done:
        if order.is_cancelled:
            # Handle partial fill on cancelled order
            self.logger().info(f"Order partially filled: {order.executed_amount_base}")
            self.handle_partial_fill(order)
        else:
            # Order fully filled
            self.logger().info(f"Order completed: {order.client_order_id}")
            self.handle_complete_fill(order)
```

### 3. Order Validation Pattern

```python
async def place_orders(self):
    # Validate market conditions
    if not self.validate_market_conditions():
        return

    # Validate balance
    if not await self.validate_sufficient_balance():
        return

    # Validate order parameters
    if not self.validate_order_parameters():
        return

    # Create order candidate with validation
    order_candidate = self.create_order_candidate()

    # Adjust for exchange requirements
    adjusted_candidates = self.adjust_order_candidates(
        self.config.connector_name,
        [order_candidate]
    )

    if not adjusted_candidates:
        self.logger().error("Order adjustment failed")
        return

    # Place order
    order_id = self.place_order(
        connector_name=self.config.connector_name,
        trading_pair=self.config.trading_pair,
        order_type=self.config.order_type,
        side=self.config.side,
        amount=adjusted_candidates[0].amount,
        price=adjusted_candidates[0].price
    )

    self.logger().info(f"Order placed: {order_id}")
```

## Risk Management Patterns

### 1. Dynamic Stop Loss Pattern

```python
class DynamicStopLossExecutor(PositionExecutor):
    def calculate_dynamic_stop_loss(self) -> Decimal:
        # Use ATR for dynamic stop loss
        atr = self.calculate_atr()
        multiplier = Decimal("2.0")  # 2x ATR

        if self.config.side == TradeType.BUY:
            stop_price = self.entry_price - (atr * multiplier)
        else:
            stop_price = self.entry_price + (atr * multiplier)

        # Convert to percentage
        stop_loss_pct = abs(stop_price - self.entry_price) / self.entry_price

        # Apply maximum stop loss
        max_stop_loss = Decimal("0.05")  # 5%
        return min(stop_loss_pct, max_stop_loss)
```

### 2. Position Scaling Pattern

```python
def determine_executor_actions(self) -> List[ExecutorAction]:
    actions = []

    # Scale into position
    active_executors = self.get_active_executors()
    max_executors = 3

    if len(active_executors) < max_executors:
        # Check if should add to position
        if self.should_scale_in(active_executors):
            # Reduce size for each additional position
            scale_factor = Decimal(str(1 / (len(active_executors) + 1)))

            config = self.create_executor_config()
            config.amount_quote *= scale_factor

            actions.append(CreateExecutorAction(
                controller_id=self.config.id,
                executor_config=config
            ))

    return actions
```

### 3. Portfolio Risk Management Pattern

```python
class PortfolioRiskManager:
    def __init__(self, max_portfolio_risk: Decimal = Decimal("0.02")):
        self.max_portfolio_risk = max_portfolio_risk

    def calculate_position_size(self,
                              account_balance: Decimal,
                              stop_loss_pct: Decimal,
                              current_positions: List[PositionSummary]) -> Decimal:
        # Calculate current portfolio risk
        current_risk = Decimal("0")
        for position in current_positions:
            position_risk = position.amount * position.breakeven_price * stop_loss_pct
            current_risk += position_risk

        # Calculate remaining risk budget
        total_risk_budget = account_balance * self.max_portfolio_risk
        remaining_risk = total_risk_budget - current_risk

        if remaining_risk <= 0:
            return Decimal("0")

        # Calculate position size based on risk
        position_size = remaining_risk / stop_loss_pct

        return position_size
```

## Common Pitfalls and Solutions

### 1. Race Condition in Executor Creation

**Problem**: Creating multiple executors due to delayed state updates.

**Solution**:
```python
def determine_executor_actions(self) -> List[ExecutorAction]:
    # Always wait for executor updates
    if not self.executors_update_event.is_set():
        return []

    # Clear event immediately after reading
    self.executors_update_event.clear()

    # Now safe to determine actions
    return self._determine_actions_internal()
```

### 2. Incorrect PnL Calculation

**Problem**: Not accounting for fees or partial fills.

**Solution**:
```python
def get_net_pnl_quote(self) -> Decimal:
    # Track all components separately
    gross_pnl = self.calculate_gross_pnl()
    total_fees = self.get_cum_fees_quote()
    funding_fees = self.get_funding_fees() if self.is_perpetual else Decimal("0")

    # Net PnL includes all costs
    net_pnl = gross_pnl - total_fees - funding_fees

    return net_pnl
```

### 3. Memory Leaks in Long-Running Strategies

**Problem**: Accumulating data without cleanup.

**Solution**:
```python
class ControllerWithCleanup(ControllerBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_history_size = 1000
        self.price_history = []

    async def update_processed_data(self):
        # Add new data
        self.price_history.append(self.current_price)

        # Cleanup old data
        if len(self.price_history) > self.max_history_size:
            self.price_history = self.price_history[-self.max_history_size:]
```

### 4. Improper Event Handler Cleanup

**Problem**: Event handlers not unregistered, causing duplicate processing.

**Solution**:
```python
def stop(self):
    # Always unregister events before stopping
    try:
        self.unregister_events()
    except Exception as e:
        self.logger().error(f"Error unregistering events: {e}")

    # Cancel any pending orders
    self.cancel_active_orders()

    # Call parent stop
    super().stop()
```

### 5. Balance Validation Failures

**Problem**: Not checking available balance vs total balance.

**Solution**:
```python
async def validate_sufficient_balance(self):
    connector = self.connectors[self.config.connector_name]

    # Use available balance, not total
    quote_available = connector.get_available_balance(self.quote_asset)
    base_available = connector.get_available_balance(self.base_asset)

    # Check for minimum requirements
    required_quote = self.calculate_required_balance()

    if quote_available < required_quote:
        self.logger().error(
            f"Insufficient balance. Required: {required_quote}, "
            f"Available: {quote_available}"
        )
        return False

    return True
```

## Performance Optimization Patterns

### 1. Cached Calculations Pattern

```python
class OptimizedController(ControllerBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cached_indicators = {}
        self._cache_timestamp = 0

    async def update_processed_data(self):
        current_time = self._strategy.current_timestamp

        # Only recalculate if cache expired
        if current_time - self._cache_timestamp > self.update_interval:
            self._cached_indicators = self.calculate_all_indicators()
            self._cache_timestamp = current_time

    @property
    def current_signal(self):
        return self._cached_indicators.get('signal')
```

### 2. Batch Operations Pattern

```python
async def place_multiple_orders(self, orders: List[Dict]):
    # Validate all orders first
    validated_orders = []
    for order in orders:
        if self.validate_order(order):
            validated_orders.append(order)

    # Place all validated orders
    order_ids = []
    for order in validated_orders:
        order_id = await self.place_order(**order)
        order_ids.append(order_id)

    return order_ids
```

## Testing Patterns

### 1. Mock Market Data Pattern

```python
class MockMarketDataProvider:
    def __init__(self, test_data: Dict):
        self.test_data = test_data
        self.current_index = 0

    def get_candles(self, connector, trading_pair, interval):
        # Return test data
        return self.test_data.get(f"{connector}_{trading_pair}_{interval}", [])

    def advance_time(self):
        self.current_index += 1
```

### 2. Executor Testing Pattern

```python
async def test_position_executor():
    # Create mock strategy
    strategy = MockStrategy()

    # Create executor config
    config = PositionExecutorConfig(
        connector_name="test_exchange",
        trading_pair="BTC-USDT",
        side=TradeType.BUY,
        amount_quote=Decimal("1000"),
        triple_barrier_config=TripleBarrierConfig(
            stop_loss=Decimal("0.02"),
            take_profit=Decimal("0.03")
        )
    )

    # Create and start executor
    executor = PositionExecutor(strategy, config)
    executor.start()

    # Simulate order events
    await simulate_order_filled(executor, amount=Decimal("0.1"))

    # Verify state
    assert executor.is_trading
    assert executor.open_filled_amount == Decimal("0.1")
```

## Debugging Patterns

### 1. Comprehensive Logging Pattern

```python
def determine_executor_actions(self) -> List[ExecutorAction]:
    self.logger().debug(
        f"Determining actions - "
        f"Signal: {self.current_signal}, "
        f"Active executors: {len(self.active_executors)}, "
        f"Market condition: {self.market_condition}"
    )

    actions = []
    # ... logic ...

    if actions:
        self.logger().info(f"Generated {len(actions)} actions: {actions}")

    return actions
```

### 2. State Snapshot Pattern

```python
def capture_state_snapshot(self) -> Dict:
    """Capture complete state for debugging"""
    return {
        'timestamp': self._strategy.current_timestamp,
        'market_data': {
            'price': self.current_price,
            'volume': self.current_volume
        },
        'indicators': {
            'signal': self.current_signal,
            'trend': self.trend_direction
        },
        'executors': [
            {
                'id': e.id,
                'status': e.status.name,
                'pnl': float(e.net_pnl_quote)
            }
            for e in self.executors_info
        ]
    }
```
