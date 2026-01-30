# Simplified Trading Bot Infrastructure - Architecture Plan

## Executive Summary

Migrate from Hummingbot's over-generalized framework to a simplified, purpose-built trading bot infrastructure optimized for Orderly and Hyperliquid DEXs.

---

## Problem Statement

Hummingbot pain points identified:
- **Dual V1/V2 strategy systems** coexisting with 669+ lines of boilerplate
- **7 executor types** with significant overlap
- **Cython/Python mix** creating maintenance burden
- **3-layer execution path**: Strategy → Orchestrator → Executor → Connector
- **Multiple timing mechanisms**: Clock tick_size, RunnableBase interval, Executor interval
- **17+ event listener classes** requiring registration/deregistration

---

## Recommended Solution

### Language: Python 3.11+

**Rationale:**
- Existing codebase expertise
- Orderly SDK already available (`orderly-evm-connector-python`)
- Mature asyncio support
- Pydantic v2 for type-safe configuration

### Core Dependencies

```
pydantic>=2.0           # Configuration and validation
aiohttp>=3.9            # Async HTTP
websockets>=12.0        # WebSocket connections
pandas>=2.0             # Data manipulation
structlog               # Structured logging
```

---

## Architecture Overview

```
kodiak/
├── core/
│   ├── clock.py              # Single timing mechanism
│   ├── engine.py             # Main trading loop
│   ├── events.py             # Event types
│   └── types.py              # Core data types
├── config/
│   ├── base.py               # Pydantic configs
│   └── loader.py             # YAML/env loading
├── exchanges/
│   ├── base.py               # ExchangeConnector protocol
│   └── orderly/
│       ├── connector.py      # OrderlyConnector
│       ├── auth.py           # Ed25519 auth
│       ├── rest_client.py    # REST API
│       └── ws_client.py      # WebSocket streams
├── data/
│   ├── provider.py           # MarketDataProvider
│   ├── candle_cache.py       # CandleCacheManager (reuse)
│   └── types.py              # TickData, Candle
├── orders/
│   ├── manager.py            # Centralized OrderManager
│   └── types.py              # Order, Fill, OrderRequest
├── account/
│   ├── manager.py            # AccountManager
│   └── position.py           # Position tracking
├── strategy/
│   ├── base.py               # BaseStrategy interface
│   └── grid.py               # GridStrategy
├── backtest/
│   ├── engine.py             # BacktestEngine
│   └── mock_connector.py     # MockExchangeConnector
├── reporting/
│   └── metrics.py            # Performance metrics
└── logging/
    └── config.py             # Logging setup
```

---

## Key Design Decisions

### 1. Strategy Interface (Pure Function)

The core insight from your backtester: `create_proposal()` is a pure function.

```python
class BaseStrategy(ABC):
    @abstractmethod
    def create_proposal(self, tick_data: TickData) -> List[OrderProposal]:
        """
        PURE FUNCTION - same code for live AND backtest
        - All input via tick_data
        - Returns proposals, does NOT place orders
        """
        pass
```

### 2. Single Timing Mechanism

Replace Hummingbot's multiple timers with simple asyncio:

```python
async def _run_loop(self):
    while self._running:
        await self._tick()
        await asyncio.sleep(self.config.tick_interval)
```

### 3. Direct Data Flow (No Orchestrator Layer)

```
Hummingbot:  Strategy → Orchestrator → Executor → Connector
New:         Strategy → Engine → Connector
```

### 4. TickData (Strategy Input)

```python
@dataclass(frozen=True)
class TickData:
    timestamp: int
    best_bid: Decimal
    best_ask: Decimal
    mid_price: Decimal
    mark_price: Decimal
    position: Decimal
    last_order_timestamp: int
    last_fill_timestamp: int
    # ... all data strategy needs
```

### 5. Exchange Connector Protocol (Not Inheritance)

```python
class ExchangeConnector(Protocol):
    async def place_order(self, request: OrderRequest) -> Order: ...
    async def cancel_order(self, order_id: str) -> bool: ...
    async def get_position(self, trading_pair: str) -> Position: ...
    # ... flat interface, no deep hierarchy
```

---

## Data Flow

### Live Trading
```
WebSocket → MarketDataProvider → TickData
                                    ↓
                           Strategy.create_proposal()
                                    ↓
                           List[OrderProposal]
                                    ↓
                           OrderManager.quantize()
                                    ↓
                           Connector.place_batch()
                                    ↓
                              Exchange
```

### Backtesting
```
Historical Candles → BacktestEngine
                           ↓
for each candle:
    1. simulate_fills()
    2. build_tick_data()
    3. strategy.create_proposal()  ← SAME CODE
    4. process_proposals()
```

---

## Phased Implementation Plan

### Phase 1: Core Infrastructure (Foundation)
- [ ] Set up project structure (`kodiak/` package)
- [ ] Implement core types (`Order`, `Fill`, `TickData`, `Position`)
- [ ] Port `OrderlyAuth` from existing code
- [ ] Implement `OrderlyConnector` (REST + WebSocket)
- [ ] Implement `OrderManager` (centralized tracking)
- [ ] Implement `TradingEngine` (main loop)
- [ ] Port `GridStrategy` from `mm_grid_kodiak_target.py`

**Deliverable:** Place/cancel orders on Orderly testnet

### Phase 2: Market Data & Account
- [ ] Implement `MarketDataProvider` with WebSocket subscriptions
- [ ] Implement `AccountManager` (balances, positions)
- [ ] Add trading rules (quantization)
- [ ] Implement configuration system (YAML + env)

**Deliverable:** Full market data streaming, position tracking

### Phase 3: Backtesting
- [ ] Port `BacktestEngine` from existing backtester
- [ ] Port `MockConnector`
- [ ] Port `CandleCacheManager`
- [ ] Ensure strategy works identically in both modes

**Deliverable:** Backtest GridStrategy with historical data

### Phase 4: Production Hardening
- [ ] Structured logging (structlog)
- [ ] Error handling and recovery
- [ ] WebSocket reconnection logic
- [ ] Graceful shutdown
- [ ] Dockerization
- [ ] Health checks

**Deliverable:** Production-ready deployment

### Phase 5: Hyperliquid Integration
- [ ] Implement `HyperliquidConnector`
- [ ] Add wallet-based auth
- [ ] Multi-exchange configuration

**Deliverable:** GridStrategy running on Hyperliquid

---

## What to Reference vs Build Fresh

Since the architecture fundamentally differs from Hummingbot (centralized data collector, NATS pub-sub, Kubernetes), we **cannot directly reuse** most components. Here's the accurate breakdown:

### Use as REFERENCE ONLY (Study Patterns, Don't Port)

| Hummingbot Component | Learn From | Build Fresh Because |
|---------------------|------------|---------------------|
| `orderly_perpetual_auth.py` | Ed25519 signing pattern | New auth class for our architecture |
| `orderly_perpetual_derivative.py` | API endpoints, order params | New connector design (no Cython deps) |
| `orderly_perpetual_constants.py` | Endpoint URLs, rate limits | New constants file |
| `backtester/data_types.py` | TickData fields concept | New TickData for pub-sub serialization |
| `mm_grid_kodiak_target.py` | Grid logic, create_proposal pattern | New strategy with new interfaces |

### Use DIRECTLY (These Are Usable)

| Component | Why It Works |
|-----------|--------------|
| `orderly-evm-connector-python` SDK | External library, no Hummingbot deps |
| Orderly API documentation | Reference for endpoints |
| Ed25519 cryptography patterns | Standard crypto, just re-implement |

### Build FROM SCRATCH

| Component | Why Fresh Build Needed |
|-----------|----------------------|
| **DataCollector** | New: centralized service, NATS publisher |
| **StrategyExecutor** | New: NATS subscriber, no Hummingbot Clock |
| **MarketDataNormalizer** | New: multi-exchange normalization |
| **OrderManager** | New: direct exchange API, no InFlightOrder complexity |
| **BotManager** | New: K8s pod lifecycle, not Hummingbot executors |
| **CredentialVault** | New: encrypted storage for multi-tenant |
| **BacktestEngine** | New: can reuse TickData concept, but different event flow |

### What This Means for Implementation

**Phase 1 is NOT porting Hummingbot code.** It's:

1. **Study** the Orderly SDK and auth patterns
2. **Design** new interfaces (DataCollector, StrategyExecutor)
3. **Implement** from scratch with new architecture in mind
4. **Test** on Orderly testnet

The grid strategy logic (price levels, order placement rules) can be inspired by `mm_grid_kodiak_target.py`, but the implementation will be entirely new code fitting our pub-sub architecture.

---

## Key Differences from Hummingbot

| Aspect | Hummingbot | Kodiak (New) |
|--------|------------|--------------|
| Strategy Layers | 4 layers | 2 layers |
| Timing | Multiple mechanisms | Single tick_interval |
| Strategy Interface | 17+ event listeners | Pure function |
| Connector Hierarchy | 5+ inheritance levels | Flat protocol |
| Order Tracking | Distributed | Centralized |
| Configuration | YAML + interactive | YAML + env + Pydantic |

---

## Verification Plan

### Phase 1 Verification
1. Run `OrderlyConnector` integration tests against testnet
2. Place/cancel limit orders via CLI
3. Verify order state tracking matches exchange

### Phase 2 Verification
1. Subscribe to WebSocket and verify orderbook updates
2. Compare position tracking with exchange dashboard
3. Validate quantization against trading rules

### Phase 3 Verification
1. Run backtest with known historical data
2. Compare backtest fills with expected behavior
3. Verify same strategy config produces consistent results

### Phase 4 Verification
1. Kill process during operation, verify graceful recovery
2. Disconnect network, verify reconnection
3. Run for 24h+ on testnet without intervention

---

## User Decisions

- **Project Location**: New repo (clean slate, no Hummingbot dependencies)
- **First Strategy**: Simple reversal grid (start/end price, grid levels)
- **Priority**: Live trading first on Orderly testnet, then backtesting
- **Future Features**: Architecture should support multi-strategy, database persistence, web dashboard - implemented incrementally

---

## Multi-Tenant Bot-as-a-Service Architecture

**Goal:** Deploy trading bots for many users, similar to CEX trading bot services.

### Revised Architecture

```
kodiak/
├── core/                     # Core trading logic (unchanged)
├── exchanges/                # Exchange connectors (unchanged)
├── strategy/                 # Strategy implementations (unchanged)
│
├── platform/                 # NEW: Multi-tenant platform layer
│   ├── users/
│   │   ├── models.py         # User, Subscription, Limits
│   │   ├── auth.py           # JWT/OAuth authentication
│   │   └── service.py        # User CRUD operations
│   │
│   ├── credentials/
│   │   ├── vault.py          # Encrypted API key storage
│   │   ├── models.py         # ExchangeCredential model
│   │   └── service.py        # Credential management
│   │
│   ├── bots/
│   │   ├── models.py         # Bot, BotConfig, BotStatus
│   │   ├── manager.py        # BotManager (lifecycle)
│   │   ├── supervisor.py     # Health monitoring, auto-restart
│   │   └── service.py        # Bot CRUD operations
│   │
│   ├── metrics/
│   │   ├── usage.py          # Usage tracking (orders, volume, uptime)
│   │   ├── limits.py         # Rate limits, bot limits per user
│   │   └── aggregator.py     # Aggregate metrics for dashboards
│   │
│   └── isolation/
│       ├── process.py        # Per-user process isolation
│       └── resources.py      # Memory/CPU limits per bot
│
├── api/                      # NEW: REST API for platform
│   ├── main.py               # FastAPI app
│   ├── auth.py               # Auth middleware
│   ├── routes/
│   │   ├── users.py          # User management endpoints
│   │   ├── bots.py           # Bot CRUD endpoints
│   │   ├── credentials.py    # Credential management
│   │   └── analytics.py      # Performance data endpoints
│   └── websocket.py          # Real-time bot status
│
└── storage/                  # NEW: Database layer
    ├── models.py             # SQLAlchemy models
    ├── migrations/           # Alembic migrations
    └── repositories/         # Data access layer
```

### Key Platform Components

#### 1. User Management

```python
# platform/users/models.py
class User(BaseModel):
    id: UUID
    email: str
    max_bots: int = 5       # Configurable limit
    is_active: bool = True
    created_at: datetime

# Simple role-based access (not billing tiers)
class UserRole(Enum):
    USER = "user"           # Standard access
    ADMIN = "admin"         # Can manage other users
```

#### 2. Secure Credential Storage

```python
# platform/credentials/vault.py
class CredentialVault:
    """
    Encrypted storage for user's exchange API keys.

    Options:
    - HashiCorp Vault (production)
    - AWS Secrets Manager
    - Encrypted database column (simpler)
    """

    async def store_credential(
        self,
        user_id: UUID,
        exchange: str,
        api_key: str,
        api_secret: str,  # Ed25519 key for Orderly
        account_id: str
    ) -> UUID:
        """Encrypt and store, return credential_id."""
        pass

    async def get_credential(
        self,
        user_id: UUID,
        credential_id: UUID
    ) -> ExchangeCredential:
        """Retrieve and decrypt for bot use."""
        pass
```

#### 3. Bot Lifecycle Management

```python
# platform/bots/manager.py
class BotManager:
    """
    Manages bot instances across all users.

    Each bot runs as isolated TradingEngine with user's credentials.
    """

    async def create_bot(
        self,
        user_id: UUID,
        credential_id: UUID,
        strategy_config: StrategyConfig
    ) -> Bot:
        """Create and persist bot configuration."""
        pass

    async def start_bot(self, bot_id: UUID) -> None:
        """
        Start bot in isolated process/container.

        - Load user's credentials from vault
        - Create TradingEngine with strategy
        - Start in supervised process
        """
        pass

    async def stop_bot(self, bot_id: UUID) -> None:
        """Graceful shutdown with order cancellation."""
        pass

    async def get_user_bots(self, user_id: UUID) -> List[Bot]:
        """List all bots for a user."""
        pass
```

#### 4. Process Isolation

```python
# platform/isolation/process.py
class BotProcess:
    """
    Run each bot in isolated subprocess.

    Benefits:
    - Crash isolation (one bot crash doesn't affect others)
    - Memory limits per bot
    - Clean restart capability
    """

    def __init__(self, bot_id: UUID, config: BotConfig):
        self.bot_id = bot_id
        self.process: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> None:
        """Start bot in subprocess with resource limits."""
        self.process = await asyncio.create_subprocess_exec(
            "python", "-m", "kodiak.runner",
            "--bot-id", str(self.bot_id),
            # Resource limits via cgroups/Docker
        )

    async def health_check(self) -> bool:
        """Check if bot is responsive."""
        pass
```

#### 5. Usage Metrics (No Billing)

```python
# platform/metrics/usage.py
class UsageTracker:
    """Track usage metrics for monitoring and analytics (not billing)."""

    async def record_order(self, bot_id: UUID, order: Order) -> None:
        """Record order count and details."""
        pass

    async def record_fill(self, bot_id: UUID, fill: Fill) -> None:
        """Record fill volume and PnL."""
        pass

    async def get_bot_metrics(self, bot_id: UUID, period: str) -> BotMetrics:
        """
        Get aggregated metrics for a bot.

        Returns:
            BotMetrics with: order_count, fill_count, volume,
            realized_pnl, uptime, error_count
        """
        pass

    async def get_user_metrics(self, user_id: UUID) -> UserMetrics:
        """Aggregate metrics across all user's bots."""
        pass
```

Simple metrics tracked:
- **Order count** per bot/day
- **Fill volume** (notional value)
- **Realized PnL**
- **Bot uptime**
- **Error/restart count**

### API Endpoints

```python
# api/routes/bots.py
@router.post("/bots")
async def create_bot(
    request: CreateBotRequest,
    user: User = Depends(get_current_user)
) -> BotResponse:
    """Create a new trading bot."""
    pass

@router.post("/bots/{bot_id}/start")
async def start_bot(
    bot_id: UUID,
    user: User = Depends(get_current_user)
) -> BotStatusResponse:
    """Start a stopped bot."""
    pass

@router.get("/bots/{bot_id}/performance")
async def get_performance(
    bot_id: UUID,
    user: User = Depends(get_current_user)
) -> PerformanceResponse:
    """Get bot performance metrics."""
    pass
```

### Database Schema (Core Tables)

```sql
-- Users
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR UNIQUE NOT NULL,
    password_hash VARCHAR NOT NULL,
    tier VARCHAR NOT NULL DEFAULT 'free',
    created_at TIMESTAMP DEFAULT NOW()
);

-- Exchange Credentials (encrypted)
CREATE TABLE credentials (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    exchange VARCHAR NOT NULL,
    encrypted_data BYTEA NOT NULL,  -- Encrypted API keys
    created_at TIMESTAMP DEFAULT NOW()
);

-- Bots
CREATE TABLE bots (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    credential_id UUID REFERENCES credentials(id),
    strategy_type VARCHAR NOT NULL,
    config JSONB NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'stopped',
    created_at TIMESTAMP DEFAULT NOW()
);

-- Orders (for history and analytics)
CREATE TABLE orders (
    id UUID PRIMARY KEY,
    bot_id UUID REFERENCES bots(id),
    exchange_order_id VARCHAR,
    side VARCHAR NOT NULL,
    price DECIMAL NOT NULL,
    amount DECIMAL NOT NULL,
    status VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Fills
CREATE TABLE fills (
    id UUID PRIMARY KEY,
    order_id UUID REFERENCES orders(id),
    price DECIMAL NOT NULL,
    amount DECIMAL NOT NULL,
    fee DECIMAL NOT NULL,
    timestamp TIMESTAMP NOT NULL
);
```

### Deployment Architecture

```
                    ┌─────────────────────────────────────────┐
                    │           Load Balancer                 │
                    └─────────────────┬───────────────────────┘
                                      │
                    ┌─────────────────┴───────────────────────┐
                    │           API Server (FastAPI)          │
                    │  - User auth                            │
                    │  - Bot management                       │
                    │  - WebSocket for status                 │
                    └─────────────────┬───────────────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        │                             │                             │
        ▼                             ▼                             ▼
┌───────────────┐           ┌───────────────┐           ┌───────────────┐
│  Bot Worker 1 │           │  Bot Worker 2 │           │  Bot Worker N │
│  (User A)     │           │  (User B)     │           │  (User C)     │
│  - GridBot    │           │  - GridBot    │           │  - GridBot    │
│  - Isolated   │           │  - Isolated   │           │  - Isolated   │
└───────────────┘           └───────────────┘           └───────────────┘
        │                             │                             │
        └─────────────────────────────┴─────────────────────────────┘
                                      │
                    ┌─────────────────┴───────────────────────┐
                    │           PostgreSQL + Redis            │
                    │  - User data                            │
                    │  - Bot configs                          │
                    │  - Order history                        │
                    │  - Session/cache (Redis)                │
                    └─────────────────────────────────────────┘
```

---

## High-Performance CEX-Style Architecture (Research-Based)

Based on research into how major exchanges (Binance, Bybit) and professional trading firms structure their bot services:

### Key Architectural Patterns

**Sources:** [AWS Tick-to-Trade Optimization](https://aws.amazon.com/blogs/web3/optimize-tick-to-trade-latency-for-digital-assets-exchanges-and-trading-platforms-on-aws/), [Chronicle Low-Latency Trading](https://foojay.io/today/low-latency-crypto-trading-systems-using-java-and-chronicle-services/), [HFT Architecture Patterns](https://medium.com/@halljames9963/architectural-design-patterns-for-high-frequency-algo-trading-bots-c84f5083d704)

### 1. Centralized Market Data Service

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATA COLLECTOR POD                           │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │ Orderly WS   │    │ Hyperliquid  │    │ Future       │      │
│  │ Connector    │    │ WS Connector │    │ Exchanges    │      │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘      │
│         │                   │                   │               │
│         └─────────────┬─────┴───────────────────┘               │
│                       │                                         │
│              ┌────────▼────────┐                               │
│              │ Feed Normalizer │ ← Normalize to common format  │
│              │ (Multi-exchange)│                               │
│              └────────┬────────┘                               │
│                       │                                         │
│              ┌────────▼────────┐                               │
│              │ Order Book      │ ← In-memory, allocation-free  │
│              │ Aggregator      │                               │
│              └────────┬────────┘                               │
│                       │                                         │
│              ┌────────▼────────┐                               │
│              │ Event Publisher │ ← NATS/Redis pub-sub          │
│              │ (Low-latency)   │                               │
│              └─────────────────┘                               │
└─────────────────────────────────────────────────────────────────┘
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
    ┌─────────┐    ┌─────────┐    ┌─────────┐
    │ Bot 1   │    │ Bot 2   │    │ Bot N   │  ← Subscribers
    └─────────┘    └─────────┘    └─────────┘
```

**Why Centralized?**
- Single WebSocket connection per exchange (rate limit friendly)
- Shared order book state (memory efficient)
- Normalized data format for all strategies
- Consistent timestamps across bots

### 2. Strategy Executor Pods (Per-User Isolation)

```
┌─────────────────────────────────────────────────────────────────┐
│                  STRATEGY EXECUTOR POD (User A)                 │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Event Loop (Single-threaded)           │  │
│  │                                                           │  │
│  │  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐     │  │
│  │  │ Market Data │   │  Strategy   │   │   Order     │     │  │
│  │  │ Subscriber  │ → │  Engine     │ → │  Sender     │     │  │
│  │  │             │   │             │   │             │     │  │
│  │  │ (from NATS) │   │ (Grid Bot)  │   │ (to Exchange)│    │  │
│  │  └─────────────┘   └─────────────┘   └─────────────┘     │  │
│  │         │                                    │            │  │
│  │         └────────────────────────────────────┘            │  │
│  │                    No context switching                   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Memory: 256MB limit | CPU: 0.5 core | Isolated namespace      │
└─────────────────────────────────────────────────────────────────┘
```

**Why Per-User Pods?**
- Crash isolation (one user's bot crash doesn't affect others)
- Resource limits per user
- Clean restart capability
- Security isolation (user credentials)

### 3. Event-Driven Communication

```python
# Data flow: ~50-200 microseconds internal latency (target)

# 1. Data Collector publishes normalized ticks
class DataCollector:
    async def on_orderbook_update(self, exchange: str, data: dict):
        normalized = self.normalize(exchange, data)
        await self.nats.publish(
            f"market.{exchange}.{normalized.symbol}",
            normalized.to_bytes()  # Binary serialization (MessagePack/FlatBuffers)
        )

# 2. Strategy Executor subscribes and reacts
class StrategyExecutor:
    async def start(self):
        await self.nats.subscribe(
            f"market.orderly.{self.config.trading_pair}",
            self.on_tick
        )

    async def on_tick(self, msg):
        tick_data = TickData.from_bytes(msg.data)
        proposals = self.strategy.create_proposal(tick_data)
        if proposals:
            await self.execute(proposals)
```

### 4. Performance Optimization Techniques

| Technique | Benefit | Implementation |
|-----------|---------|----------------|
| **Allocation-free parsing** | No GC pauses | Reuse buffers, avoid object creation in hot path |
| **Single event loop** | No thread context switching | Process market data and execute in same loop |
| **Binary serialization** | Faster than JSON | MessagePack, FlatBuffers, or Protocol Buffers |
| **CPU affinity** | Predictable latency | Pin latency-sensitive pods to dedicated cores |
| **In-memory orderbook** | Sub-microsecond access | Chronicle Map, custom data structures |
| **Connection pooling** | Reduce handshake overhead | Persistent HTTP/2, WebSocket connections |

### 5. Kubernetes Multi-Tenancy Pattern

**Source:** [Kubernetes Multi-Tenancy](https://kubernetes.io/docs/concepts/security/multi-tenancy/)

```yaml
# Namespace-per-user approach (recommended)
apiVersion: v1
kind: Namespace
metadata:
  name: user-12345

---
# Resource quota per user
apiVersion: v1
kind: ResourceQuota
metadata:
  name: user-quota
  namespace: user-12345
spec:
  hard:
    pods: "5"           # Max 5 bots per user
    cpu: "2"            # Total 2 cores
    memory: "2Gi"       # Total 2GB RAM

---
# Network policy (isolate users)
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-other-namespaces
  namespace: user-12345
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: shared-services  # Only data-collector can reach
```

### 6. Complete Production Architecture

```
                         ┌───────────────────────────────────────┐
                         │           SHARED SERVICES             │
                         │                                       │
                         │  ┌─────────────────────────────────┐  │
                         │  │      DATA COLLECTOR POD         │  │
                         │  │  - WebSocket to Orderly         │  │
                         │  │  - WebSocket to Hyperliquid     │  │
                         │  │  - Order book aggregation       │  │
                         │  │  - Publishes to NATS            │  │
                         │  └─────────────────────────────────┘  │
                         │                                       │
                         │  ┌─────────────────────────────────┐  │
                         │  │      NATS/Redis (Message Bus)   │  │
                         │  └─────────────────────────────────┘  │
                         │                                       │
                         │  ┌─────────────────────────────────┐  │
                         │  │      API GATEWAY (FastAPI)      │  │
                         │  │  - User auth                    │  │
                         │  │  - Bot lifecycle management     │  │
                         │  │  - WebSocket for UI updates     │  │
                         │  └─────────────────────────────────┘  │
                         └───────────────────────────────────────┘
                                          │
           ┌──────────────────────────────┼──────────────────────────────┐
           │                              │                              │
           ▼                              ▼                              ▼
┌─────────────────────┐      ┌─────────────────────┐      ┌─────────────────────┐
│ NAMESPACE: user-001 │      │ NAMESPACE: user-002 │      │ NAMESPACE: user-NNN │
│                     │      │                     │      │                     │
│ ┌─────────────────┐ │      │ ┌─────────────────┐ │      │ ┌─────────────────┐ │
│ │ grid-bot-1 pod  │ │      │ │ grid-bot-1 pod  │ │      │ │ grid-bot-1 pod  │ │
│ │ - Subscribes to │ │      │ │                 │ │      │ │                 │ │
│ │   NATS topics   │ │      │ │                 │ │      │ │                 │ │
│ │ - Executes grid │ │      │ │                 │ │      │ │                 │ │
│ │ - Places orders │ │      │ │                 │ │      │ │                 │ │
│ └─────────────────┘ │      │ └─────────────────┘ │      │ └─────────────────┘ │
│                     │      │                     │      │                     │
│ ┌─────────────────┐ │      │ ┌─────────────────┐ │      │                     │
│ │ grid-bot-2 pod  │ │      │ │ dca-bot-1 pod   │ │      │                     │
│ └─────────────────┘ │      │ └─────────────────┘ │      │                     │
│                     │      │                     │      │                     │
│ ResourceQuota:      │      │ ResourceQuota:      │      │ ResourceQuota:      │
│ - 3 pods max        │      │ - 5 pods max        │      │ - 10 pods max       │
│ - 1GB memory        │      │ - 2GB memory        │      │ - 4GB memory        │
└─────────────────────┘      └─────────────────────┘      └─────────────────────┘
           │                              │                              │
           └──────────────────────────────┼──────────────────────────────┘
                                          │
                         ┌────────────────▼────────────────┐
                         │         PostgreSQL              │
                         │  - User accounts                │
                         │  - Bot configurations           │
                         │  - Order/fill history           │
                         │  - Usage metrics                │
                         └─────────────────────────────────┘
```

### 7. Latency Targets (Based on Industry Research)

| Operation | Target | Notes |
|-----------|--------|-------|
| WebSocket → Order book update | <1ms | Data collector processing |
| NATS message delivery | <100μs | Internal message bus |
| Strategy tick processing | <500μs | Pure function execution |
| Order placement (local) | <1ms | Before network |
| **Total tick-to-order** | **<5ms** | Internal latency goal |
| Exchange round-trip | 50-200ms | Network dependent |

---

## Cost & Infrastructure Options

### Option 1: Single VPS (Simplest Start)
**Best for:** Initial development, testing, <10 users

```
Single VPS (e.g., Hetzner AX41-NVMe)
├── API Server
├── Bot processes (all on same machine)
├── PostgreSQL
└── Redis
```

**Cost:** ~$50-80/month (Hetzner dedicated)
**Pros:** Simple, low latency between components, easy to debug
**Cons:** Single point of failure, limited scaling

### Option 2: Lightweight Container Setup
**Best for:** 10-100 users, production-ready

```
Docker Compose / Podman
├── API container (2-4 replicas behind nginx)
├── Bot worker containers (1 per active bot)
├── PostgreSQL container (or managed RDS)
├── Redis container
└── Traefik/nginx for routing
```

**Cost:** $100-300/month depending on scale
**Pros:** Isolated bots, easy horizontal scaling, reproducible
**Cons:** More operational complexity

### Option 3: Kubernetes (Future Scale)
**Best for:** 100+ users, enterprise

```
Kubernetes Cluster
├── API deployment (auto-scaling)
├── Bot pods (scheduled per user/bot)
├── Managed PostgreSQL (RDS, Cloud SQL)
├── Managed Redis (ElastiCache)
└── Monitoring stack (Prometheus, Grafana)
```

**Cost:** $300-1000+/month
**Pros:** Auto-scaling, self-healing, enterprise-grade
**Cons:** Significant operational overhead

### Recommended Path

| Phase | Infrastructure | Estimated Cost |
|-------|----------------|----------------|
| 1-2 (Dev) | Local + Testnet | $0 |
| 3-4 (Beta) | Single VPS | $50-80/mo |
| 5 (Production) | Docker Compose | $150-300/mo |
| Scale (100+ users) | Kubernetes | $500+/mo |

### Key Cost Factors

1. **Compute:** ~$30-50 per bot worker if running continuously
2. **Database:** PostgreSQL managed = $20-50/mo, self-hosted = $0
3. **Network:** Outbound data, WebSocket connections
4. **Monitoring:** Logging, metrics storage

### Latency Considerations

For trading bots, latency matters:
- **Orderly:** Servers in AWS ap-northeast-1 (Tokyo)
- **Hyperliquid:** Arbitrum chain

**Recommendation:** Host in same region as exchange servers (Tokyo for Orderly)

---

## Revised Implementation Phases (All Fresh Code)

### Phase 1: Core Trading Engine (Single User) - BUILD FRESH
**Goal:** Prove we can place orders on Orderly testnet

Build from scratch:
- [ ] `kodiak/core/types.py` - Order, Fill, Position, TickData dataclasses
- [ ] `kodiak/exchanges/orderly/auth.py` - Ed25519 signing (reference existing pattern)
- [ ] `kodiak/exchanges/orderly/rest_client.py` - REST API wrapper
- [ ] `kodiak/exchanges/orderly/ws_client.py` - WebSocket for orderbook
- [ ] `kodiak/orders/manager.py` - Order tracking and state management
- [ ] `kodiak/strategy/grid.py` - Simple grid strategy with `create_proposal()`
- [ ] `kodiak/core/engine.py` - Main loop (asyncio, simple event loop)
- [ ] `run.py` - CLI entry point with YAML config

**Deliverable:** CLI bot that places grid orders on Orderly testnet

### Phase 2: Database & Persistence - BUILD FRESH
- [ ] `kodiak/storage/models.py` - SQLAlchemy models (Order, Fill, EquityCurve)
- [ ] `kodiak/storage/repository.py` - Data access layer
- [ ] `kodiak/metrics/usage.py` - Simple counters (orders, volume)
- [ ] Alembic migrations setup

**Deliverable:** Orders and fills persisted to PostgreSQL

### Phase 3: Data Collector Split - NEW ARCHITECTURE
**Goal:** Enable multiple bots to share one data feed

- [ ] `kodiak/services/data_collector/` - Standalone service
  - WebSocket connections to Orderly
  - Order book aggregation
  - NATS publisher (normalized TickData)
- [ ] `kodiak/services/strategy_executor/` - Separate service
  - NATS subscriber
  - Runs strategy logic
  - Places orders via REST
- [ ] NATS/Redis setup and deployment config

**Deliverable:** Two separate services communicating via message bus

### Phase 4: Platform Layer (Multi-Tenant) - BUILD FRESH
- [ ] `kodiak/platform/users/` - User auth (JWT/OAuth)
- [ ] `kodiak/platform/credentials/vault.py` - Encrypted API key storage
- [ ] `kodiak/platform/bots/manager.py` - Bot lifecycle (create/start/stop)
- [ ] `kodiak/api/` - FastAPI endpoints

**Deliverable:** API to create users and launch their bots

### Phase 5: Kubernetes Deployment - INFRASTRUCTURE
- [ ] Dockerfile for data-collector
- [ ] Dockerfile for strategy-executor
- [ ] Helm chart or Kustomize manifests
- [ ] Namespace-per-user automation
- [ ] ResourceQuota and NetworkPolicy templates

**Deliverable:** Bots running in isolated K8s namespaces

### Phase 6: Production Hardening & Scale
- [ ] Binary serialization (MessagePack/FlatBuffers)
- [ ] Hyperliquid connector + data-collector integration
- [ ] Prometheus metrics + Grafana dashboards
- [ ] Health checks and auto-restart
- [ ] Backtest engine (using same TickData format)

---

## Immediate Implementation Scope (Phase 1)

For the initial implementation, we focus on proving the core trading works:

1. **Single user, single bot** - no platform layer yet
2. **Orderly testnet** - prove order placement and grid logic
3. **File-based config** - YAML configs, no database
4. **CLI operation** - no API or web dashboard

This validates the core before adding platform complexity.
