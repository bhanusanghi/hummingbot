# Hummingbot Strategy V2 Lifecycle Management Guide

## Overview

This guide provides a complete understanding of the Strategy V2 lifecycle, from initialization through market data processing, signal generation, executor management, and position tracking. Understanding this lifecycle is crucial for building bug-free strategies.

## Complete Lifecycle Flow

```
1. Strategy Initialization
   ↓
2. Market Data Subscription
   ↓
3. Controller Activation
   ↓
4. Control Loop Execution
   ├─→ Market Data Update
   ├─→ Signal Generation
   ├─→ Executor Actions
   └─→ Position Updates
   ↓
5. Executor Lifecycle
   ├─→ Creation
   ├─→ Order Management
   ├─→ Event Processing
   └─→ Termination
   ↓
6. Performance Tracking
```

## 1. Strategy Initialization Phase

### 1.1 Configuration Loading

```python
# Strategy V2 Base initializes with:
def __init__(self, connectors: Dict[str, ConnectorBase], config: StrategyV2ConfigBase):
    # Load controller configurations
    self.controller_configs = config.load_controller_configs()

    # Initialize market data provider
    self.market_data_provider = MarketDataProvider(connectors)

    # Initialize executor orchestrator
    self.executor_orchestrator = ExecutorOrchestrator(self)

    # Create controllers
    self.controllers = self._create_controllers()
```

### 1.2 Controller Creation

```python
def _create_controllers(self):
    controllers = {}
    for controller_config in self.controller_configs:
        controller_class = controller_config.get_controller_class()
        controller = controller_class(
            config=controller_config,
            market_data_provider=self.market_data_provider,
            actions_queue=self.actions_queue
        )
        controllers[controller_config.id] = controller
    return controllers
```

### 1.3 Initial Position Loading

The orchestrator loads historical data:
- Archived executors from database
- Existing positions
- Performance metrics

## 2. Market Data Subscription

### 2.1 Candles Initialization

Controllers initialize their candles feeds:

```python
def initialize_candles(self):
    for candles_config in self.config.candles_config:
        self.market_data_provider.initialize_candles_feed(candles_config)
```

### 2.2 Market Data Provider Lifecycle

```python
# Market data provider manages:
- Candles feeds for multiple exchanges
- Real-time price updates
- Order book snapshots
- Ready state management
```

## 3. Controller Control Loop

### 3.1 Control Task Execution

Every controller update interval:

```python
async def control_task(self):
    if self.market_data_provider.ready and self.executors_update_event.is_set():
        # Step 1: Update market data and indicators
        await self.update_processed_data()

        # Step 2: Generate executor actions based on signals
        executor_actions = self.determine_executor_actions()

        # Step 3: Send actions to orchestrator
        if len(executor_actions) > 0:
            await self.send_actions(executor_actions)
```

### 3.2 Update Processed Data

```python
async def update_processed_data(self):
    # Fetch latest candles
    candles = self.market_data_provider.get_candles(...)

    # Calculate indicators
    self.current_signal = self.calculate_signals(candles)

    # Update internal state
    self.processed_data = {...}
```

### 3.3 Determine Executor Actions

```python
def determine_executor_actions(self) -> List[ExecutorAction]:
    actions = []

    # Check active executors
    active_executors = self.filter_executors(
        self.executors_info,
        lambda e: e.is_active
    )

    # Generate new executor if conditions met
    if self.should_create_executor():
        actions.append(CreateExecutorAction(
            controller_id=self.config.id,
            executor_config=self.create_executor_config()
        ))

    # Stop executors if needed
    for executor in active_executors:
        if self.should_stop_executor(executor):
            actions.append(StopExecutorAction(
                controller_id=self.config.id,
                executor_id=executor.id,
                keep_position=False
            ))

    return actions
```

## 4. Executor Orchestrator Processing

### 4.1 Action Processing

```python
async def control_loop(self):
    while True:
        # Process actions from all controllers
        actions = await self.actions_queue.get()

        for action in actions:
            if isinstance(action, CreateExecutorAction):
                await self.create_executor(action)
            elif isinstance(action, StopExecutorAction):
                await self.stop_executor(action)
```

### 4.2 Executor Creation

```python
async def create_executor(self, action: CreateExecutorAction):
    # Get executor class
    executor_class = self._executor_mapping[action.executor_config.type]

    # Create executor instance
    executor = executor_class(
        strategy=self.strategy,
        config=action.executor_config,
        update_interval=self.executors_update_interval,
        max_retries=self.executors_max_retries
    )

    # Start executor
    executor.start()

    # Track in active executors
    self.active_executors[action.controller_id].append(executor)
```

## 5. Executor Lifecycle

### 5.1 Executor Initialization

```python
class PositionExecutor(ExecutorBase):
    def __init__(self, strategy, config, update_interval=1.0):
        super().__init__(strategy, config, [config.connector_name], update_interval)

        # Initialize order tracking
        self._open_order = None
        self._close_order = None

        # Register event handlers
        self.register_events()
```

### 5.2 Executor Start

```python
async def on_start(self):
    # Validate sufficient balance
    await self.validate_sufficient_balance()

    # Place initial order
    await self.place_orders()
```

### 5.3 Order Lifecycle Events

```python
# Order Created
def process_order_created_event(self, event_tag, market, event):
    order_id = event.order_id
    tracked_order = TrackedOrder(order_id=order_id, ...)
    self._open_order = tracked_order

# Order Filled
def process_order_filled_event(self, event_tag, market, event):
    # Update order tracking
    order = self.get_in_flight_order(connector_name, event.order_id)

    # Check if position entry complete
    if self.should_create_close_order():
        self.place_close_order()

# Order Completed
def process_order_completed_event(self, event_tag, market, event):
    # Check if executor should terminate
    if self.is_complete():
        self.stop()
```

### 5.4 Control Task

```python
async def control_task(self):
    # Check stop conditions
    if self.should_stop():
        self.terminate_with_reason(CloseType.STOP_LOSS)
        return

    # Check take profit
    if self.should_take_profit():
        self.terminate_with_reason(CloseType.TAKE_PROFIT)
        return

    # Manage orders
    await self.manage_orders()
```

### 5.5 Executor Termination

```python
def stop(self):
    # Set close timestamp
    self.close_timestamp = self._strategy.current_timestamp

    # Cancel active orders
    self.cancel_active_orders()

    # Unregister events
    self.unregister_events()

    # Update status
    super().stop()
```

## 6. Position Management

### 6.1 Position Tracking

The orchestrator maintains position state:

```python
class PositionSummary:
    connector_name: str
    trading_pair: str
    amount: Decimal
    side: TradeType
    breakeven_price: Decimal
    unrealized_pnl_quote: Decimal
    realized_pnl_quote: Decimal
    cum_fees_quote: Decimal
```

### 6.2 Position Aggregation

```python
def get_positions_held_by_controller(self, controller_id: str):
    # Aggregate from active executors
    positions = []
    for executor in self.active_executors[controller_id]:
        if executor.is_trading:
            positions.append(executor.get_position_summary())

    # Add held positions
    positions.extend(self.positions_held[controller_id])

    return positions
```

## 7. State Synchronization

### 7.1 Executor Info Updates

```python
async def update_executors_info(self):
    for controller_id, executors in self.active_executors.items():
        # Collect executor info
        executors_info = [e.executor_info for e in executors]

        # Update controller
        controller = self.strategy.controllers[controller_id]
        controller.executors_info = executors_info
        controller.executors_update_event.set()
```

### 7.2 Performance Tracking

```python
def update_performance_report(self, controller_id: str):
    report = self.cached_performance[controller_id]

    # Update from archived executors
    for executor_info in self.archived_executors[controller_id]:
        report.realized_pnl_quote += executor_info.net_pnl_quote
        report.total_executors += 1

        if executor_info.close_type:
            report.close_type_counts[executor_info.close_type] += 1
```

## Common Lifecycle Patterns

### 1. Signal → Executor Pattern

```python
# In controller
if signal == "BUY" and len(active_executors) == 0:
    actions.append(CreateExecutorAction(
        executor_config=PositionExecutorConfig(
            side=TradeType.BUY,
            triple_barrier_config=self.get_triple_barrier_config()
        )
    ))
```

### 2. Position Management Pattern

```python
# In executor
@property
def is_complete(self):
    return (self._open_order and self._open_order.is_done and
            self._close_order and self._close_order.is_done)
```

### 3. Risk Management Pattern

```python
# Triple barrier checks in control_task
if self.is_expired:
    self.early_stop(CloseType.TIME_LIMIT)
elif self.should_stop_loss:
    self.early_stop(CloseType.STOP_LOSS)
elif self.should_take_profit:
    self.early_stop(CloseType.TAKE_PROFIT)
```

## Lifecycle Debugging Tips

### 1. Status Monitoring

Use `format_status()` to track:
- Controller state
- Active executors
- Position details
- Market conditions

### 2. Event Logging

```python
self.logger().info(f"Creating executor: {executor_config}")
self.logger().info(f"Order filled: {event.order_id}")
self.logger().info(f"Executor terminated: {self.config.id}")
```

### 3. State Validation

Always validate:
- Market data readiness
- Sufficient balance
- Order constraints
- Position limits

## Common Pitfalls to Avoid

1. **Not checking market data readiness**
   ```python
   if not self.market_data_provider.ready:
       return []  # Don't generate actions
   ```

2. **Creating duplicate executors**
   ```python
   # Always check active executors first
   if any(e.is_active for e in self.executors_info):
       return []  # Already have active executor
   ```

3. **Ignoring executor update events**
   ```python
   # Wait for orchestrator updates
   if not self.executors_update_event.is_set():
       return
   ```

4. **Improper cleanup**
   ```python
   # Always unregister events on stop
   def on_stop(self):
       self.unregister_events()
   ```

5. **Race conditions**
   ```python
   # Use proper synchronization
   self.executors_update_event.clear()  # After sending actions
   ```
