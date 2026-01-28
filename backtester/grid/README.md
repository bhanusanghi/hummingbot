# Grid Controller Backtester

Custom tick-by-tick backtester for **MultiGridStrike** controller that runs actual **GridExecutor** instances with simulated market data.

## Overview

This backtester differs from the framework-based approach by:

1. **Actual Executor Simulation** - Runs real GridExecutor instances (not pre-computed lifecycle)
2. **Tick-by-Tick Processing** - Simulates fills and state changes progressively
3. **Event-Driven Architecture** - Fires OrderFilledEvent and other events to executors
4. **Level State Machine** - Handles complex GridLevel states (NOT_ACTIVE → OPEN_PLACED → FILLED → CLOSE_PLACED → COMPLETE → recycle)

## Architecture

```
GridControllerBacktester
├── BacktestingMockConnector (simulates exchange)
│   ├── place_order() → stores SimulatedOrder
│   ├── cancel_order()
│   ├── get_price_by_type() → returns synthetic bid/ask/mid
│   └── simulate_fills(candle) → returns fills list
├── BacktestingMockStrategy (minimal mock interface)
│   └── Wraps connector, routes buy/sell/cancel calls
├── GridExecutor (actual executor, unmodified)
│   └── Instantiated with mock strategy/connector
└── MultiGridStrike Controller (unmodified)
    └── determine_executor_actions() creates GridExecutorConfig
```

## Files

- **data_types.py** - Data structures (GridBacktestConfig, GridExecutorResult, etc.)
- **mock_connector.py** - Simulates exchange connector behavior
- **mock_strategy.py** - Minimal strategy interface for executors
- **grid_backtester.py** - Main orchestration class
- **run_grid_backtest.py** - Entry point script with example configuration

## Usage

### 1. Configure Your Strategy

Edit `run_grid_backtest.py` and modify the `create_sample_controller_config()` function:

```python
def create_sample_controller_config() -> MultiGridStrikeConfig:
    return MultiGridStrikeConfig(
        connector_name="binance_perpetual",
        trading_pair="BTC-USDT",
        total_amount_quote=Decimal("1000"),

        grids=[
            GridConfig(
                grid_id="buy_1",
                start_price=Decimal("40000"),
                end_price=Decimal("42000"),
                limit_price=Decimal("39000"),
                side=TradeType.BUY,
                amount_quote_pct=Decimal("0.5"),  # 50% of capital
                enabled=True,
            ),
            # Add more grids...
        ],

        # Risk management
        triple_barrier_config=TripleBarrierConfig(
            take_profit=Decimal("0.01"),  # 1%
            stop_loss=Decimal("0.02"),  # 2%
            time_limit=3600,
        ),
    )
```

### 2. Configure Backtest Parameters

Modify the `create_backtest_config()` function:

```python
def create_backtest_config() -> GridBacktestConfig:
    return GridBacktestConfig(
        connector_name="binance",
        trading_pair="BTC-USDT",
        candle_interval="1s",

        # Time range
        start_timestamp=parse_datetime("2024-01-01"),
        end_timestamp=parse_datetime("2024-01-07"),

        # Market simulation
        spread_bps=Decimal("5"),  # 0.05% spread
        trade_fee_bps=Decimal("4"),  # 0.04% trading fee
    )
```

### 3. Run the Backtest

```bash
python scripts/backtester/grid/run_grid_backtest.py
```

## Fill Simulation Logic

The backtester uses OHLCV candle data to determine when orders fill:

| Order Type | Condition | Fill Price |
|------------|-----------|------------|
| BUY LIMIT | candle.low <= order.price | order.price |
| SELL LIMIT | candle.high >= order.price | order.price |
| BUY MARKET | triggered by SL/TL | candle.close |
| SELL MARKET | triggered by SL/TL | candle.close |

## Output Files

The backtester generates:

1. **backtest_equity_curve.csv** - Timestamp-by-timestamp equity tracking
2. **backtest_fills.csv** - Complete fill history with PnL

## Results Structure

### BacktestResult

```python
@dataclass
class BacktestResult:
    executor_results: List[GridExecutorResult]  # Per-executor metrics
    equity_curve: pd.DataFrame  # Equity over time
    total_pnl: Decimal
    total_fees: Decimal
    total_volume: Decimal
    total_trades: int
    final_capital: Decimal
```

### GridExecutorResult

```python
@dataclass
class GridExecutorResult:
    executor_id: str
    config: GridExecutorConfig
    fills: List[Fill]

    # PnL metrics
    realized_pnl_quote: Decimal
    realized_fees_quote: Decimal
    position_pnl_quote: Decimal
    net_pnl_quote: Decimal
    net_pnl_pct: Decimal

    # Volume metrics
    realized_buy_size_quote: Decimal
    realized_sell_size_quote: Decimal
    total_volume: Decimal

    # Grid stats
    levels_completed: int
    total_levels: int
    close_type: Optional[CloseType]
```

## Programmatic Usage

```python
from backtester.grid import GridControllerBacktester
from controllers.generic.multi_grid_strike import MultiGridStrikeConfig
from backtester.grid.data_types import GridBacktestConfig

# Create configs
controller_config = MultiGridStrikeConfig(...)
backtest_config = GridBacktestConfig(...)

# Run backtest
backtester = GridControllerBacktester(controller_config, backtest_config)
await backtester.initialize_data()
result = backtester.run()

# Access results
print(f"Total PnL: {result.total_pnl}")
for executor_result in result.executor_results:
    print(f"Executor {executor_result.executor_id}: {executor_result.net_pnl_quote}")
```

## Key Implementation Details

### 1. Mock Connector

The `BacktestingMockConnector` simulates exchange behavior:
- Tracks open orders
- Simulates fills based on candle OHLCV
- Provides synthetic order book for price queries
- Does NOT fire events (handled by backtester)

### 2. Mock Strategy

The `BacktestingMockStrategy` provides minimal interface:
- `buy()`, `sell()`, `cancel()` methods
- Exposes `connectors` dict for executor access
- No inheritance from `ScriptStrategyBase`

### 3. Event Firing

When fills occur:
1. Create `InFlightOrder` with fill data
2. Create `OrderFilledEvent`
3. Update executor's `TrackedOrder` with `InFlightOrder`
4. Call `executor.process_order_filled_event()`

### 4. Executor Management

- Executors created via `CreateExecutorAction` from controller
- Each tick: update executors, get controller actions, execute actions
- Track executor results when stopped
- Update `controller.executors_info` for controller decisions

## Limitations

- Single trading pair per backtest
- No slippage modeling (fills at exact limit prices)
- Synthetic order book (not real depth)
- No partial fills (orders fill completely or not at all)
- Market orders fill at candle close (simplified)

## Future Enhancements

- [ ] Support for GridStrike controller
- [ ] Support for QuantumGridAllocator
- [ ] Partial fill simulation
- [ ] Slippage modeling
- [ ] Real order book replay
- [ ] Multi-pair backtesting
- [ ] Portfolio-level metrics
- [ ] Optimization framework integration

## Troubleshooting

**Issue: No fills occurring**
- Check that grid price ranges overlap with historical price data
- Verify candle data is loading correctly
- Ensure spread_bps is not too wide

**Issue: Executors not being created**
- Check that `is_inside_bounds()` logic matches current price
- Verify grid configs are enabled
- Check controller logs for action generation

**Issue: Import errors**
- Ensure project root is in Python path
- Check all dependencies are installed
- Verify file structure matches expectations

## Contributing

When modifying the backtester:
1. Maintain separation between mock and real components
2. Keep mock connector stateless (state only in backtester)
3. Fire proper events to executors (don't shortcut)
4. Test with different grid configurations
5. Validate PnL calculations manually

## License

Same as hummingbot project.
