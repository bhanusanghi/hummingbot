# Hummingbot Strategy V2 Architecture Guide

## Overview

The Strategy V2 architecture in Hummingbot represents a modular, event-driven approach to building trading strategies. It separates concerns between high-level decision making (Controllers), execution logic (Executors), and orchestration (Strategy/Orchestrator).

## Core Architecture Components

### 1. Base Classes Hierarchy

```
RunnableBase (Abstract Base)
├── ControllerBase
│   ├── MarketMakingControllerBase
│   └── DirectionalTradingControllerBase
├── ExecutorBase
│   ├── PositionExecutor
│   ├── OrderExecutor
│   ├── DCAExecutor
│   ├── GridExecutor
│   ├── TWAPExecutor
│   ├── XEMMExecutor
│   ├── ArbitrageExecutor
│   └── FundingArbitrageExecutor
└── StrategyV2Base
```

### 2. RunnableBase - The Foundation

All Strategy V2 components inherit from `RunnableBase`, which provides:

```python
class RunnableBase(ABC):
    def __init__(self, update_interval: float = 0.5):
        self.update_interval = update_interval
        self._status: RunnableStatus = RunnableStatus.NOT_STARTED
        self.terminated = asyncio.Event()
```

**Key Features:**
- Asynchronous control loop with configurable update interval
- Status tracking (NOT_STARTED, RUNNING, SHUTTING_DOWN, TERMINATED)
- Event-driven lifecycle management
- Error handling in control loop

**Lifecycle Methods:**
- `start()`: Initializes and starts the control loop
- `stop()`: Gracefully stops the component
- `control_task()`: Override this for main logic (called every update_interval)
- `on_start()`: Hook for initialization logic
- `on_stop()`: Hook for cleanup logic

### 3. ControllerBase - Decision Making Layer

Controllers handle high-level strategy logic and generate executor actions.

```python
class ControllerBase(RunnableBase):
    def __init__(self, config: ControllerConfigBase,
                 market_data_provider: MarketDataProvider,
                 actions_queue: asyncio.Queue,
                 update_interval: float = 1.0):
        # Stores executor information
        self.executors_info: List[ExecutorInfo] = []
        # Stores position summaries
        self.positions_held: List[PositionSummary] = []
        # Market data access
        self.market_data_provider: MarketDataProvider
        # Queue for sending actions to orchestrator
        self.actions_queue: asyncio.Queue
```

**Key Responsibilities:**
- Process market data and indicators
- Determine when to create/stop executors
- Manage strategy parameters
- Generate `ExecutorAction` objects

**Required Implementations:**
- `update_processed_data()`: Update market data/indicators
- `determine_executor_actions()`: Generate executor actions
- `to_format_status()`: Format status for display

### 4. ExecutorBase - Execution Layer

Executors handle the actual trading operations.

```python
class ExecutorBase(RunnableBase):
    def __init__(self, strategy: ScriptStrategyBase,
                 connectors: List[str],
                 config: ExecutorConfigBase,
                 update_interval: float = 0.5):
        # Access to strategy for order placement
        self._strategy: ScriptStrategyBase
        # Connector instances for trading
        self.connectors: Dict[str, ConnectorBase]
        # Configuration
        self.config: ExecutorConfigBase
```

**Key Features:**
- Event handling for order lifecycle
- Position and order tracking
- PnL calculation
- Risk management (stop loss, take profit)

**Event Handlers:**
- `process_order_created_event()`
- `process_order_filled_event()`
- `process_order_completed_event()`
- `process_order_canceled_event()`
- `process_order_failed_event()`

### 5. ExecutorOrchestrator - Coordination Layer

Manages the lifecycle of all executors and tracks performance.

```python
class ExecutorOrchestrator:
    def __init__(self, strategy: StrategyV2Base,
                 executors_update_interval: float = 1.0,
                 executors_max_retries: int = 10):
        # Active executors by controller ID
        self.active_executors: Dict[str, List[ExecutorBase]]
        # Archived executors for performance tracking
        self.archived_executors: Dict[str, List[ExecutorInfo]]
        # Position tracking
        self.positions_held: Dict[str, List[PositionSummary]]
```

**Key Responsibilities:**
- Create/stop executors based on controller actions
- Track executor performance
- Manage position aggregation
- Handle executor lifecycle events

## Configuration System

### Controller Configuration

All controllers use Pydantic models for configuration:

```python
class ControllerConfigBase(BaseClientModel):
    id: str = Field(default=None)  # Auto-generated if not provided
    controller_name: str
    controller_type: str = "generic"
    total_amount_quote: Decimal
    manual_kill_switch: bool = Field(default=False)
    candles_config: List[CandlesConfig] = []
    initial_positions: List[InitialPositionConfig] = []
```

### Executor Configuration

Each executor type has its own configuration:

```python
class ExecutorConfigBase(BaseModel):
    id: str  # Unique identifier
    timestamp: float  # Creation timestamp
    type: str  # Executor type
    controller_id: str  # Parent controller ID
```

## Data Flow Architecture

```
Market Data → MarketDataProvider → Controllers
                                       ↓
                                 ExecutorActions
                                       ↓
                           ExecutorOrchestrator
                                       ↓
                                  Executors
                                       ↓
                               Order Placement
                                       ↓
                              Exchange Events
                                       ↓
                             Executor Updates
                                       ↓
                            Controller Updates
```

## Communication Protocol

### 1. Controller → Orchestrator

Controllers communicate via `ExecutorAction` objects:

```python
# Create a new executor
CreateExecutorAction(
    controller_id="controller_1",
    executor_config=PositionExecutorConfig(...)
)

# Stop an executor
StopExecutorAction(
    controller_id="controller_1",
    executor_id="executor_123",
    keep_position=False
)
```

### 2. Orchestrator → Controller

Orchestrator updates controllers via:
- `executors_info`: List of active executor states
- `positions_held`: Aggregated position summaries

### 3. Executor → Strategy

Executors interact with the strategy for:
- Order placement
- Market data access
- Balance queries

## State Management

### RunnableStatus States

```python
class RunnableStatus(Enum):
    NOT_STARTED = 1
    RUNNING = 2
    SHUTTING_DOWN = 3
    TERMINATED = 4
```

### ExecutorInfo State

```python
class ExecutorInfo:
    id: str
    timestamp: float
    type: str
    status: RunnableStatus
    close_type: Optional[CloseType]
    close_timestamp: Optional[float]
    config: ExecutorConfigBase
    net_pnl_pct: Decimal
    net_pnl_quote: Decimal
    cum_fees_quote: Decimal
    filled_amount_quote: Decimal
    is_active: bool
    is_trading: bool
    custom_info: Dict
    controller_id: str
```

## Best Practices

### 1. Controller Design
- Keep decision logic separate from execution
- Use `update_processed_data()` for expensive calculations
- Always check `executors_update_event` before sending actions
- Implement proper `to_format_status()` for monitoring

### 2. Executor Design
- Handle all order events properly
- Implement early stop functionality
- Calculate PnL accurately
- Clean up resources in `on_stop()`

### 3. Configuration
- Use Pydantic validators for input validation
- Provide sensible defaults
- Mark updatable fields with `is_updatable`
- Include proper prompts for user input

### 4. Error Handling
- Controllers/Executors handle errors in control_task()
- Use proper logging for debugging
- Implement retry mechanisms where appropriate
- Gracefully handle insufficient balance

## Integration with Legacy Systems

The Strategy V2 architecture integrates with Hummingbot's existing systems:

1. **Connectors**: Access exchange functionality
2. **MarketDataProvider**: Unified market data access
3. **MarketsRecorder**: Performance tracking and persistence
4. **Order Management**: Uses existing order tracking infrastructure

## Performance Considerations

1. **Update Intervals**:
   - Controllers: 1.0s (configurable)
   - Executors: 0.5s (configurable)
   - Orchestrator: 1.0s (configurable)

2. **Event Processing**:
   - Asynchronous event handling
   - Batch processing of executor actions
   - Efficient position aggregation

3. **Memory Management**:
   - Archived executors are stored as ExecutorInfo (lightweight)
   - Position aggregation reduces memory footprint
   - Proper cleanup on executor termination
