# Grid Backtester Architecture

## High-Level Architecture

```mermaid
graph TB
    subgraph "Entry Point"
        A[run_grid_backtest.py]
    end

    subgraph "Main Backtester"
        B[GridControllerBacktester]
    end

    subgraph "Backtesting Infrastructure"
        C[BacktestingExecutorOrchestrator]
        D[BacktestingMarketDataProvider]
        E[BacktestingMockConnector]
        F[BacktestingMockStrategy]
    end

    subgraph "Real Components (Unmodified)"
        G[MultiGridStrike Controller]
        H[GridExecutor Instances]
    end

    subgraph "Data Sources"
        I[CandleCacheManager]
        J[MarketDataProvider]
        K[Candle Data OHLCV]
    end

    A -->|creates & runs| B
    B -->|initializes| C
    B -->|initializes| D
    B -->|initializes| E
    B -->|initializes| F
    B -->|creates| G
    C -->|manages lifecycle| H
    G -->|creates/stops| H
    D -->|delegates prices to| E
    F -->|wraps| E
    F -->|references| G
    H -->|uses| F
    H -->|places orders on| E
    B -->|fetches candles from| I
    I -->|fetches via| J
    J -->|provides| K

    style B fill:#e1f5ff
    style C fill:#ffe1e1
    style D fill:#ffe1e1
    style E fill:#ffe1e1
    style F fill:#ffe1e1
    style G fill:#e1ffe1
    style H fill:#e1ffe1
```

## Component Initialization Flow

```mermaid
sequenceDiagram
    participant R as run_grid_backtest.py
    participant B as GridControllerBacktester
    participant C as CandleCacheManager
    participant M as MarketDataProvider
    participant MC as MockConnector
    participant MP as MarketDataProvider<br/>(Backtesting)
    participant Ctrl as Controller
    participant S as MockStrategy
    participant O as Orchestrator

    R->>B: __init__(controller_config, backtest_config)
    B->>B: Store configs
    B->>C: Create CandleCacheManager
    B->>M: Create MarketDataProvider (for rules)

    R->>B: initialize_data()
    B->>M: get_trading_rules()
    M-->>B: TradingRule objects
    B->>C: fetch_candles()
    C-->>B: DataFrame with OHLCV

    B->>B: _initialize_mock_components()
    Note over B: Initialize in specific order

    B->>MC: Create BacktestingMockConnector<br/>(trading_rules, config)
    Note over MC: Initializes:<br/>- _listeners: Dict[int, List]<br/>- _in_flight_orders: Dict<br/>- _order_tracker<br/>- budget_checker

    B->>MP: Create BacktestingMarketDataProvider<br/>(mock_connector, connector_name)
    Note over MP: Simulated time provider

    B->>Ctrl: Create MultiGridStrike<br/>(config, market_data_provider, actions_queue)
    Note over Ctrl: Uses backtesting<br/>market data provider

    B->>S: Create BacktestingMockStrategy<br/>(connector, name, pair, mdp)
    Note over S: Wraps connector,<br/>references controller
    B->>S: Set controllers = {id: controller}

    B->>O: Create BacktestingExecutorOrchestrator<br/>(strategy)
    Note over O: Skips DB init,<br/>manual ticking
```

## Main Simulation Loop (Per Candle)

```mermaid
sequenceDiagram
    participant B as GridControllerBacktester
    participant MC as MockConnector
    participant S as MockStrategy
    participant MP as MarketDataProvider<br/>(Backtesting)
    participant O as Orchestrator
    participant E as GridExecutor(s)
    participant Ctrl as Controller

    loop For each candle in DataFrame
        Note over B: 1. UPDATE MARKET STATE
        B->>MC: update_market_state(candle, timestamp)
        Note over MC: Updates:<br/>- current_candle<br/>- best_bid/ask<br/>- mid_price<br/>- current_timestamp

        B->>S: current_timestamp = timestamp / 1000
        B->>MP: _current_time = timestamp / 1000

        Note over B: 2. SIMULATE FILLS & EMIT EVENTS
        B->>MC: simulate_fills_and_emit_events(candle)

        loop For each open order
            MC->>MC: Check if price touched<br/>(low <= buy.price<br/>OR high >= sell.price)

            alt Order filled
                MC->>MC: Get InFlightOrder
                MC->>MC: Apply TradeUpdate<br/>(fill_price, amount, fee)
                MC->>MC: Apply OrderUpdate<br/>(state=FILLED)
                MC->>E: trigger_event(OrderFilled, event)
                Note over E: Executor receives event<br/>via PubSub listener
                MC->>E: trigger_event(OrderCompleted, event)
                MC->>MC: Remove from orders dict
            end
        end

        MC-->>B: List of SimulatedFill
        B->>B: _record_fills(fills)

        Note over B: 3. TICK ALL EXECUTORS
        B->>O: tick_all_executors()

        loop For each active executor
            O->>E: Patch _sleep = noop
            O->>E: control_task()
            Note over E: Executor logic:<br/>- Update grid levels<br/>- Place/cancel orders<br/>- Check barriers<br/>- Process events
            E->>MC: buy()/sell()/cancel()
            MC->>E: trigger_event() for new orders
        end

        Note over B: 4. PUSH REPORTS TO CONTROLLER
        B->>O: get_all_reports()
        O-->>B: {controller_id: {executors, positions}}
        B->>Ctrl: executors_info = report["executors"]
        B->>Ctrl: positions_held = report["positions"]

        Note over B: 5. CONTROLLER DETERMINES ACTIONS
        B->>Ctrl: update_processed_data()
        B->>Ctrl: determine_executor_actions()
        Ctrl-->>B: List[CreateExecutorAction, StopExecutorAction]

        Note over B: 6. ORCHESTRATOR EXECUTES ACTIONS
        B->>O: execute_actions(actions)

        loop For each CreateExecutorAction
            O->>O: create_executor(action)
            O->>E: Instantiate GridExecutor
            O->>E: _status = RUNNING (no start())
            O->>E: register_events()
            Note over E: Registers listeners:<br/>- OrderFilled<br/>- OrderCancelled<br/>- etc.
            E->>MC: add_listener(event_tag, self)
            O->>E: update_metrics()
            O->>O: Add to active_executors
        end

        loop For each StopExecutorAction
            O->>O: store_executor(action)
            O->>O: Update cached_performance
            O->>O: Remove from active_executors
        end

        Note over B: 7. RECORD EQUITY
        B->>O: generate_performance_report(controller_id)
        O-->>B: PerformanceReport
        B->>B: Append to equity_curve
    end
```

## Event Flow: PubSub System

```mermaid
graph TB
    subgraph "MockConnector Event System"
        A[_listeners: Dict]
        B[add_listener]
        C[remove_listener]
        D[trigger_event]
    end

    subgraph "Executor Registration"
        E[executor.register_events]
        F[_fill_order_forwarder]
        G[_cancel_order_forwarder]
    end

    subgraph "Order Lifecycle"
        H[buy/sell called]
        I[Create InFlightOrder]
        J[Store in _in_flight_orders]
        K[Emit BuyOrderCreated/<br/>SellOrderCreated]
    end

    subgraph "Fill Simulation"
        L[simulate_fills_and_emit_events]
        M[Check price conditions]
        N[Get InFlightOrder]
        O[Apply TradeUpdate]
        P[Apply OrderUpdate]
        Q[Emit OrderFilled]
        R[Emit OrderCompleted]
    end

    subgraph "Event Reception"
        S[trigger_event called]
        T[Set listener._current_event_tag]
        U[Set listener._current_event_caller]
        V[Call listener message]
        W[Clear event info]
    end

    E -->|calls| B
    B -->|stores| A
    F -->|registered for| OrderFilled
    G -->|registered for| OrderCancelled

    H --> I
    I --> J
    J --> K

    L --> M
    M -->|if touched| N
    N --> O
    O --> P
    P --> Q
    Q --> R

    Q -->|calls| D
    D --> S
    S --> T
    T --> U
    U --> V
    V --> W
    W -->|invokes| F
    F -->|processes| executor_logic

    style A fill:#ffe1e1
    style D fill:#ffe1e1
    style F fill:#e1ffe1
    style Q fill:#fff4e1
```

## State Management: Orders and InFlightOrders

```mermaid
graph TB
    subgraph "MockConnector State"
        A[orders: Dict<br/>SimulatedOrder]
        B[_in_flight_orders: Dict<br/>InFlightOrder]
        C[current_timestamp: int]
        D[current_candle: Dict]
        E[best_bid/ask/mid: Decimal]
    end

    subgraph "Order Creation"
        F[buy/sell called]
        G[Create SimulatedOrder]
        H[Create InFlightOrder<br/>state=OPEN]
    end

    subgraph "Fill Simulation"
        I[Check order in 'orders']
        J[Price condition met?]
        K[Get from _in_flight_orders]
        L[Apply TradeUpdate]
        M[Apply OrderUpdate<br/>state=FILLED]
        N[Set completely_filled_event]
    end

    subgraph "Order Lifecycle"
        O[Remove from 'orders'<br/>after fill/cancel]
        P[Keep in _in_flight_orders<br/>executor references it]
    end

    subgraph "Executor Access"
        Q[executor.control_task]
        R[Checks level.active_open_order]
        S[Accesses order.order<br/>InFlightOrder]
        T[Reads executed_amount_base<br/>current_state, etc.]
    end

    F --> G
    F --> H
    G -->|stored in| A
    H -->|stored in| B

    I --> J
    J -->|yes| K
    K --> L
    L --> M
    M --> N

    N --> O
    O -.->|not removed| P

    Q --> R
    R --> S
    S --> T
    T -.->|reads from| B

    style A fill:#ffe1e1
    style B fill:#fff4e1
    style P fill:#e1ffe1
```

## Orchestrator Executor Lifecycle

```mermaid
stateDiagram-v2
    [*] --> ControllerDecides: Controller determines actions

    ControllerDecides --> CreateAction: CreateExecutorAction
    ControllerDecides --> StopAction: StopExecutorAction

    state CreateAction {
        [*] --> Instantiate: create_executor()
        Instantiate --> SetRunning: _status = RUNNING
        SetRunning --> RegisterEvents: register_events()
        RegisterEvents --> UpdateMetrics: update_metrics()
        UpdateMetrics --> AddToActive: Add to active_executors
        AddToActive --> [*]

        note right of SetRunning
            NO start() call
            No async control_loop
        end note

        note right of RegisterEvents
            Calls connector.add_listener
            for OrderFilled, OrderCancelled
        end note
    }

    CreateAction --> Running: Executor active

    state Running {
        [*] --> TickCalled: tick_all_executors()
        TickCalled --> PatchSleep: executor._sleep = noop
        PatchSleep --> CallControl: control_task()
        CallControl --> ProcessEvents: Process pending events
        ProcessEvents --> UpdateLevels: Update grid levels
        UpdateLevels --> PlaceOrders: Place/cancel orders
        PlaceOrders --> CheckBarriers: Check barriers
        CheckBarriers --> [*]

        note left of CallControl
            Manual tick instead
            of continuous loop
        end note
    }

    Running --> StopAction: Controller sends StopExecutorAction

    state StopAction {
        [*] --> FindExecutor: store_executor()
        FindExecutor --> UpdateCache: _update_cached_performance()
        UpdateCache --> RemoveFromActive: Remove from active_executors
        RemoveFromActive --> [*]

        note right of UpdateCache
            NO DB write
            Just update in-memory cache
        end note
    }

    StopAction --> [*]: Executor terminated
```

## Market State Updates

```mermaid
flowchart TB
    A[Candle Data<br/>OHLCV] -->|update_market_state| B[MockConnector]

    B --> C{Calculate Prices}

    C -->|mid_price| D[candle.close]
    C -->|spread| E[spread_bps from config]

    D --> F[best_bid = mid - spread/2]
    D --> G[best_ask = mid + spread/2]

    F --> H[Store in connector state]
    G --> H

    H --> I[get_price_by_type called]

    I --> J{Price Type?}

    J -->|MidPrice| K[Return mid_price]
    J -->|BestBid| L[Return best_bid]
    J -->|BestAsk| M[Return best_ask]
    J -->|LastTrade| N[Return mid_price]

    K --> O[Executor uses price]
    L --> O
    M --> O
    N --> O

    O --> P[Place orders at calculated prices]
    P --> Q[Orders stored in connector.orders]

    Q --> R[Next candle arrives]
    R --> S[simulate_fills_and_emit_events]

    S --> T{Check conditions}

    T -->|BUY order| U{candle.low <= order.price?}
    T -->|SELL order| V{candle.high >= order.price?}

    U -->|Yes| W[Fill at order.price]
    V -->|Yes| W

    U -->|No| X[Order remains open]
    V -->|No| X

    W --> Y[Emit events to executor]
    X --> R

    style A fill:#e1f5ff
    style B fill:#ffe1e1
    style W fill:#e1ffe1
    style Y fill:#fff4e1
```

## Key Differences: Live vs Backtesting

```mermaid
graph LR
    subgraph "LIVE SYSTEM"
        L1[Real Connector<br/>Exchange WebSocket]
        L2[ExecutorOrchestrator<br/>DB access]
        L3[Executor.start<br/>async control_loop]
        L4[Events from exchange]
        L5[Real time progression]
    end

    subgraph "BACKTESTING SYSTEM"
        B1[MockConnector<br/>Simulated fills]
        B2[BacktestingExecutorOrchestrator<br/>No DB]
        B3[Manual tick_all_executors<br/>No control_loop]
        B4[Events from simulation]
        B5[Simulated time from candles]
    end

    L1 -.->|replaced by| B1
    L2 -.->|replaced by| B2
    L3 -.->|replaced by| B3
    L4 -.->|replaced by| B4
    L5 -.->|replaced by| B5

    B1 -->|Same interface| API1[buy/sell/cancel/get_price_by_type]
    B2 -->|Same interface| API2[create_executor/store_executor]
    B3 -->|Same logic| API3[control_task]
    B4 -->|Same events| API4[OrderFilled/OrderCancelled]
    B5 -->|Same interface| API5[time/get_price_by_type]

    style L1 fill:#e1ffe1
    style L2 fill:#e1ffe1
    style L3 fill:#e1ffe1
    style L4 fill:#e1ffe1
    style L5 fill:#e1ffe1
    style B1 fill:#ffe1e1
    style B2 fill:#ffe1e1
    style B3 fill:#ffe1e1
    style B4 fill:#ffe1e1
    style B5 fill:#ffe1e1
```

## Summary

### Key Design Principles

1. **Interface Compatibility**: Mock components provide the same interfaces as live components
2. **Event-Driven**: PubSub system mimics real exchange events
3. **Manual Control**: Replace async loops with manual ticking for deterministic simulation
4. **State Isolation**: InFlightOrders tracked separately from active orders for executor reference
5. **No Modifications**: Controller and Executor code remain completely unchanged

### Data Flow

```
Candle Data → MockConnector (state update) → Executors (via events) →
Orders placed → Fill simulation → Events fired → Executors update →
Controller decides → Orchestrator manages → Repeat
```

### Critical Components

1. **MockConnector**: Exchange simulation + event emission
2. **BacktestingMarketDataProvider**: Simulated time management
3. **BacktestingExecutorOrchestrator**: Manual executor lifecycle (no DB/async)
4. **InFlightOrder tracking**: Dual tracking for active orders vs executor references
5. **PubSub emulation**: trigger_event sets listener state before calling
