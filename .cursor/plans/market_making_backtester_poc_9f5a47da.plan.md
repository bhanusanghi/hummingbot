---
name: Market Making Backtester POC
overview: Create a backtesting framework for the mm_grid_kodiak_target.py strategy that simulates order fills using kline data, with configurable fill detection modes and comprehensive reporting.
todos:
  - id: refactor-fill-handler
    content: Add did_fill_order() override to MMGrid using existing StrategyPyBase hook
    status: completed
  - id: expose-orders
    content: Add pending_orders property and reset_state() method to MMGrid
    status: completed
  - id: mock-connector
    content: Create MockConnector simulating order book, prices, position from klines
    status: completed
  - id: backtest-engine
    content: Implement BacktestEngine with tick loop and fill detection
    status: completed
  - id: report
    content: Create BacktestReport with fill recording and summary stats
    status: completed
  - id: run-script
    content: Create run_backtest.py entry point
    status: completed
---

# Market Making Backtester Framework

## Key Discovery: Existing Event Hooks

The `StrategyPyBase` already provides event hooks that the current strategy doesn't use:

```python
# strategy_py_base.pyx line 64
def did_fill_order(self, order_filled_event: OrderFilledEvent):
    pass  # Override this!
```

The current `MMGrid._detect_trade()` (lines 224-259) infers fills from position changes instead of using this hook. This is the main issue for backtesting.

---

## Part 1: Minimal Strategy Refactoring (Fully Compatible)

### Change 1: Use `did_fill_order` Hook Instead of Position Inference

Replace `_detect_trade()` with proper `did_fill_order` implementation:

```python
# Add to MMGrid class - override from StrategyPyBase
def did_fill_order(self, order_filled_event: OrderFilledEvent):
    """Handle fill events from the connector - updates cooldown and inventory tracking"""
    trade_amount = order_filled_event.amount
    if order_filled_event.trade_type == TradeType.SELL:
        trade_amount = -trade_amount
    
    # Update cooldown based on trade direction (same logic as _detect_trade)
    same_direction = sign(trade_amount) == sign(self._last_trade)
    if sign(self._last_trade) == 0:
        self._cooldown_until_timestamp = self.current_timestamp + self.config.order_cooldown
    elif same_direction:
        self._cooldown_until_timestamp = max(self.current_timestamp, self._cooldown_until_timestamp) + self.config.order_cooldown / 2
    else:
        self._cooldown_until_timestamp = self.current_timestamp + self.config.order_cooldown
    
    self._last_trade = trade_amount
    self.logger().info(f"Fill: {order_filled_event.trade_type.name} {order_filled_event.amount} @ {order_filled_event.price}")
```

Then simplify `create_proposal()` - remove `_detect_trade()` call (line 140), get inventory directly:

```python
# In create_proposal(), replace line 140:
# OLD: inventory = self._detect_trade()
# NEW: 
inventory = self._get_current_inventory()
```

### Change 2: Expose Pending Orders for Backtest Fill Checking

Add a property to expose orders placed in the last tick:

```python
@property
def pending_orders(self) -> List[PerpetualOrderCandidate]:
    """Returns the most recent proposals for backtest fill simulation"""
    return self._cached_proposals
```

### Change 3: Add Backtest-Friendly State Reset

```python
def reset_state(self):
    """Reset strategy state for backtesting - call before each backtest run"""
    self.create_timestamp = 0
    self._cooldown_until_timestamp = 0
    self._last_trade = Decimal("0")
    self._cached_inventory = Decimal("0")
    self._mid_history = []
    self._ema_mid = Decimal("0")
    self._initialized = False
```

### What Stays Unchanged

- Class inheritance: `MMGrid(ScriptStrategyBase)` - no change
- `on_tick()` signature and behavior - no change
- `create_proposal()` return type - no change
- All public methods - no change

---

## Part 2: Backtester Implementation

### File Structure

```
scripts/backtester/
├── __init__.py
├── mock_connector.py   # Simulates connector from kline data
├── engine.py           # BacktestEngine - main loop
├── report.py           # Report generation
└── run_backtest.py     # Entry point
```

### MockConnector (`mock_connector.py`)

Simulates the connector interface used by the strategy:

```python
class MockConnector:
    """Simulates ConnectorBase for backtesting"""
    
    def __init__(self, trading_pair: str, spread_bps: Decimal = Decimal("5")):
        self.trading_pair = trading_pair
        self.spread_bps = spread_bps
        self._current_candle: dict = None
        self._position_amount: Decimal = Decimal("0")
        self._position_side: PositionSide = PositionSide.BOTH
        self.ready = True
        self.name = "mock_exchange"
    
    def set_candle(self, candle: dict):
        """Called by engine each tick to update market data"""
        self._current_candle = candle
    
    def get_order_book(self, trading_pair: str) -> MockOrderBook:
        """Returns synthetic order book from candle close price"""
        close = Decimal(str(self._current_candle['close']))
        spread = close * self.spread_bps / Decimal("10000")
        return MockOrderBook(
            best_bid=close - spread/2,
            best_ask=close + spread/2,
            best_bid_size=Decimal("100"),
            best_ask_size=Decimal("100")
        )
    
    def get_price_by_type(self, trading_pair: str, price_type: PriceType) -> Decimal:
        """Returns close price as mark/mid price"""
        return Decimal(str(self._current_candle['close']))
    
    @property
    def _perpetual_trading(self):
        return self  # Self implements get_position
    
    def get_position(self, trading_pair: str) -> MockPosition:
        """Returns current simulated position"""
        return MockPosition(self._position_amount, self._position_side)
    
    def update_position(self, fill_amount: Decimal, side: TradeType):
        """Called when orders fill to update position"""
        if side == TradeType.BUY:
            self._position_amount += fill_amount
        else:
            self._position_amount -= fill_amount
        # Update position side
        if self._position_amount > 0:
            self._position_side = PositionSide.LONG
        elif self._position_amount < 0:
            self._position_side = PositionSide.SHORT
        else:
            self._position_side = PositionSide.BOTH
```

### BacktestEngine (`engine.py`)

```python
class BacktestEngine:
    def __init__(self, kline_path: str, config: MMGridConfig, fill_mode: str = "high_low"):
        self.klines = pd.read_csv(kline_path)
        self.fill_mode = fill_mode  # "close_only" or "high_low"
        self.report = BacktestReport()
        
        # Create mock connector and strategy
        self.mock_connector = MockConnector(config.trading_pair)
        self.strategy = MMGrid(
            connectors={config.exchange: self.mock_connector},
            config=config
        )
        self.strategy.reset_state()
    
    def run(self) -> BacktestReport:
        prev_candle = None
        for candle in self.klines.itertuples():
            # 1. Update mock connector with current candle
            self.mock_connector.set_candle(candle._asdict())
            
            # 2. Check fills from previous tick's pending orders
            if prev_candle is not None:
                fills = self._check_fills(candle, prev_candle, self.strategy.pending_orders)
                for fill in fills:
                    # Simulate fill event to strategy
                    fill_event = self._create_fill_event(fill, candle.timestamp)
                    self.strategy.did_fill_order(fill_event)
                    self.mock_connector.update_position(fill.amount, fill.side)
                    self.report.record_fill(fill, candle)
            
            # 3. Update strategy timestamp and call tick
            self.strategy.current_timestamp = candle.timestamp
            self.strategy.tick(candle.timestamp)
            
            prev_candle = candle
        
        return self.report
    
    def _check_fills(self, candle, prev_candle, orders) -> List[Fill]:
        fills = []
        for order in orders:
            if self.fill_mode == "high_low":
                # Buy fills if low <= order price
                if order.order_side == TradeType.BUY and candle.low <= float(order.price):
                    fills.append(Fill(order, candle.low))
                # Sell fills if high >= order price
                elif order.order_side == TradeType.SELL and candle.high >= float(order.price):
                    fills.append(Fill(order, candle.high))
            else:  # close_only
                # Check if price crossed between closes
                prev_close, curr_close = prev_candle.close, candle.close
                if order.order_side == TradeType.BUY:
                    if min(prev_close, curr_close) <= float(order.price) <= max(prev_close, curr_close):
                        fills.append(Fill(order, float(order.price)))
                else:
                    if min(prev_close, curr_close) <= float(order.price) <= max(prev_close, curr_close):
                        fills.append(Fill(order, float(order.price)))
        return fills
```

### Report Output (`report.py`)

CSV columns: `timestamp, side, order_price, fill_price, amount, candle_open, candle_high, candle_low, candle_close, candle_volume, inventory_after, pnl`

---

## Summary of Changes

| Component | Change Type | Impact |

|-----------|-------------|--------|

| `MMGrid.did_fill_order()` | Add override | Uses existing hook from `StrategyPyBase` |

| `MMGrid._detect_trade()` | Remove/simplify | No longer needed with fill events |

| `MMGrid.pending_orders` | Add property | Exposes cached proposals |

| `MMGrid.reset_state()` | Add method | Enables clean backtest runs |

| `scripts/backtester/` | New module | Contains engine, mock connector, report |

**Inheritance unchanged**: `MMGrid(ScriptStrategyBase)` remains the same.

**Public API unchanged**: `on_tick()`, `create_proposal()` signatures preserved.