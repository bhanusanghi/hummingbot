# Orderly Network Authentication & Account System Analysis

## Executive Summary

Orderly Network uses a **two-tier authentication system**:
1. **Wallet-level authentication** (EIP-712) - One-time setup for account/key registration
2. **Trading-level authentication** (ed25519) - Ongoing API operations and trading

This analysis explores both systems and proposes an implementation strategy for Hummingbot integration.

---

## Part 1: Account Registration System

### Account ID Calculation

Orderly generates unique account IDs using this cryptographic process:

```
1. wallet_address_bytes = to_bytes(wallet_address)
2. builder_id_hash = keccak256(builder_id_string)
3. encoded = abi_encode(['address', 'bytes32'], [wallet_address_bytes, builder_id_hash])
4. account_id = keccak256(encoded)
```

**Result**: A unique hex identifier like `0x1234...abcd` per wallet+builder combination

### Account Registration Flow

**Step 1: Pre-checks**
- Verify builder exists: `GET /v1/public/builders`
- Check if wallet already registered: API call to verify registration status
- Choose blockchain (Arbitrum, Optimism, Polygon, etc.)

**Step 2: Get Registration Nonce**
```
GET /v1/registration_nonce
Response: {"data": {"registration_nonce": 12345}}
```

**Step 3: Create EIP-712 Message**
```javascript
message = {
  brokerId: "woofi_dex",           // Builder identifier
  chainId: 421614,                  // Arbitrum testnet
  timestamp: 1698765432000,         // Unix milliseconds
  registrationNonce: 12345          // From step 2
}

domain = {
  name: "Orderly",
  version: "1",
  chainId: 421614,
  verifyingContract: "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"  // Off-chain domain
}

types = {
  Registration: [
    { name: "brokerId", type: "string" },
    { name: "chainId", type: "uint256" },
    { name: "timestamp", type: "uint64" },
    { name: "registrationNonce", type: "uint256" }
  ]
}
```

**Step 4: Sign with Wallet**
```python
from eth_account.messages import encode_structured_data

structured_msg = encode_structured_data(primitive={
    "types": types,
    "primaryType": "Registration",
    "domain": domain,
    "message": message
})

signature = wallet.sign_message(structured_msg)
# Returns: {r, s, v} signature components
```

**Step 5: Submit Registration**
```
POST /v1/register_account
Body: {
  "message": {...},           // The message object
  "signature": "0x1234...",   // Hex-encoded signature
  "userAddress": "0xabc..."   // Wallet address
}

Response: {
  "success": true,
  "data": {
    "account_id": "0x7890..."
  }
}
```

---

## Part 2: Trading Key (Orderly Key) System

### What is an Orderly Key?

An **Orderly Key** is an ed25519 key pair that grants API access without requiring wallet signatures for every request. It's similar to traditional exchange API keys but uses ed25519 cryptography.

### Key Characteristics

- **Algorithm**: ed25519 elliptic curve cryptography
- **Format**:
  - Public key: `ed25519:BASE58_ENCODED_PUBLIC_KEY`
  - Private key: `ed25519:BASE58_ENCODED_PRIVATE_KEY`
- **Scope**:
  - `read` - Query-only access
  - `trading` - Full order management
  - `asset` - Withdrawal capabilities
- **Expiration**: Maximum 365 days from creation

### Trading Key Registration Flow

**Step 1: Generate ed25519 Key Pair**
```python
from cryptography.hazmat.primitives.asymmetric import ed25519
import base58

# Generate key pair
private_key = ed25519.Ed25519PrivateKey.generate()
public_key = private_key.public_key()

# Encode to BASE58
private_bytes = private_key.private_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PrivateFormat.Raw,
    encryption_algorithm=serialization.NoEncryption()
)
public_bytes = public_key.public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw
)

orderly_key = f"ed25519:{base58.b58encode(public_bytes).decode()}"
orderly_secret = f"ed25519:{base58.b58encode(private_bytes).decode()}"
```

**Step 2: Create AddOrderlyKey Message**
```javascript
message = {
  brokerId: "woofi_dex",
  chainId: 421614,
  orderlyKey: "ed25519:ABCD1234...",    // Public key from step 1
  scope: "trading",                      // Or "read" / "asset"
  timestamp: 1698765432000,
  expiration: 1730301432000              // Up to 365 days later
}

domain = {
  name: "Orderly",
  version: "1",
  chainId: 421614,
  verifyingContract: "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"  // Off-chain
}

types = {
  AddOrderlyKey: [
    { name: "brokerId", type: "string" },
    { name: "chainId", type: "uint256" },
    { name: "orderlyKey", type: "string" },
    { name: "scope", type: "string" },
    { name: "timestamp", type: "uint64" },
    { name: "expiration", type: "uint64" }
  ]
}
```

**Step 3: Sign with Wallet (EIP-712)**
```python
structured_msg = encode_structured_data(primitive={
    "types": types,
    "primaryType": "AddOrderlyKey",
    "domain": domain,
    "message": message
})

signature = wallet.sign_message(structured_msg)
```

**Step 4: Submit to API**
```
POST /v1/orderly_key
Body: {
  "message": {...},
  "signature": "0x1234...",
  "userAddress": "0xabc..."
}

Response: {
  "success": true,
  "data": {
    "key": "ed25519:ABCD1234...",
    "scope": "trading",
    "expiration": 1730301432000
  }
}
```

---

## Part 3: Trading Authentication (Ongoing Operations)

Once the trading key is registered, **all subsequent API operations** use ed25519 signing.

### Request Signing Process

**Step 1: Prepare Request Parameters**
```python
timestamp = int(time.time() * 1000)  # Unix milliseconds
method = "POST"
path = "/v1/order"
params = {
    "symbol": "PERP_BTC_USDC",
    "order_type": "LIMIT",
    "side": "BUY",
    "order_price": 50000.0,
    "order_quantity": 0.1
}
```

**Step 2: Create Normalized String**
```python
# Format: timestamp + method + path + body
# Body is JSON string for POST, empty for GET, query string for GET with params

if method == "GET" and params:
    query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    normalized = f"{timestamp}{method}{path}?{query_string}"
elif method in ["POST", "PUT", "DELETE"]:
    body = json.dumps(params, separators=(',', ':'))
    normalized = f"{timestamp}{method}{path}{body}"
else:
    normalized = f"{timestamp}{method}{path}"
```

**Step 3: Sign with ed25519**
```python
import base58
from cryptography.hazmat.primitives.asymmetric import ed25519

# Parse orderly_secret (format: "ed25519:BASE58_KEY")
secret_b58 = orderly_secret.split(':')[1]
private_bytes = base58.b58decode(secret_b58)
private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)

# Sign the normalized string
signature_bytes = private_key.sign(normalized.encode('utf-8'))
signature = base58.b58encode(signature_bytes).decode()
```

**Step 4: Set Request Headers**
```python
headers = {
    "Content-Type": "application/json",
    "orderly-account-id": orderly_account_id,      # e.g., "0x7890..."
    "orderly-key": orderly_key,                     # e.g., "ed25519:ABCD..."
    "orderly-signature": signature,                 # BASE58 signature
    "orderly-timestamp": str(timestamp)             # Unix milliseconds
}
```

**Step 5: Make Request**
```python
response = requests.post(
    f"https://testnet-api.orderly.org{path}",
    headers=headers,
    json=params
)
```

### Special Case: Order-Specific Signatures

For order placement, Orderly requires an **additional order signature**:

```python
# Create order signature message (different from request signature)
order_message = {
    "symbol": "PERP_BTC_USDC",
    "side": "BUY",
    "order_type": "LIMIT",
    "order_price": 50000.0,
    "order_quantity": 0.1,
    "timestamp": timestamp
}

# Normalize for signing (specific format - check API docs)
order_normalized = json.dumps(order_message, separators=(',', ':'), sort_keys=True)
order_signature_bytes = private_key.sign(order_normalized.encode('utf-8'))
order_signature = base58.b58encode(order_signature_bytes).decode()

# Add to headers
headers["orderly-trading-key"] = orderly_key
headers["orderly-order-signature"] = order_signature
```

---

## Part 4: Hummingbot Integration Strategy

### Challenge: Two-Tier Authentication

Hummingbot connectors traditionally only handle **API key authentication** (Level 2). Orderly also requires **wallet-based setup** (Level 1) for initial account and key registration.

### Solution: Pre-Setup Approach (RECOMMENDED)

**Separation of Concerns**:
- **Outside Hummingbot**: Account registration + Trading key setup (one-time)
- **Inside Hummingbot**: Trading operations only (ongoing)

This mirrors the standard pattern:
1. User registers on exchange website → User registers Orderly account with wallet
2. User generates API keys → User generates Orderly trading keys
3. User enters keys in Hummingbot → User enters orderly credentials in Hummingbot

### Required User Credentials

Users provide three configuration values:

```yaml
orderly_perpetual:
  orderly_account_id: "0x1234567890abcdef..."
  orderly_key: "ed25519:ABCD1234..."
  orderly_secret: "ed25519:5678EFGH..."
```

**Where do users get these?**

#### Option A: Manual Setup (User Guide)
Provide documentation for users to:
1. Connect wallet to Orderly UI
2. Register account through UI
3. Generate API keys through UI
4. Copy credentials into Hummingbot config

#### Option B: Setup Script (Better UX)
Provide a standalone setup script:

```python
# scripts/orderly_setup.py

"""
Orderly Network Setup Utility for Hummingbot

This script helps you:
1. Register an Orderly account
2. Generate trading keys
3. Output credentials for Hummingbot configuration

Requirements:
- Private key or mnemonic for your wallet
- Builder ID you want to use
"""

def main():
    print("=== Orderly Network Setup for Hummingbot ===\n")

    # Step 1: Get wallet
    wallet_input = input("Enter wallet private key or mnemonic: ")
    wallet = load_wallet(wallet_input)

    # Step 2: Choose network
    network = choose_network()  # testnet/mainnet

    # Step 3: Choose builder
    builder_id = choose_builder()  # "woofi_dex", etc.

    # Step 4: Register account
    print("\n[1/3] Registering Orderly account...")
    account_id = register_account(wallet, builder_id, network)
    print(f"✓ Account registered: {account_id}")

    # Step 5: Generate trading key
    print("\n[2/3] Generating trading key...")
    orderly_key, orderly_secret = generate_trading_key()
    print(f"✓ Trading key generated")

    # Step 6: Register trading key
    print("\n[3/3] Registering trading key...")
    register_trading_key(wallet, orderly_key, builder_id, network)
    print(f"✓ Trading key registered")

    # Step 7: Output credentials
    print("\n" + "="*60)
    print("SUCCESS! Copy these credentials to Hummingbot:\n")
    print(f"orderly_account_id: {account_id}")
    print(f"orderly_key: {orderly_key}")
    print(f"orderly_secret: {orderly_secret}")
    print("="*60)

    # Save to file
    save_credentials(account_id, orderly_key, orderly_secret)

if __name__ == "__main__":
    main()
```

### Connector Implementation (No Wallet Handling)

The Hummingbot connector assumes credentials already exist:

```python
# hummingbot/connector/derivative/orderly_perpetual/orderly_perpetual_auth.py

class OrderlyPerpetualAuth:
    """
    Authentication handler for Orderly Network API.

    Uses ed25519 signing for all requests.
    Does NOT handle wallet operations or account registration.
    """

    def __init__(
        self,
        account_id: str,      # Hex account ID
        orderly_key: str,     # ed25519:BASE58
        orderly_secret: str,  # ed25519:BASE58
    ):
        self._account_id = account_id
        self._orderly_key = orderly_key
        self._private_key = self._parse_private_key(orderly_secret)

    def _parse_private_key(self, orderly_secret: str) -> ed25519.Ed25519PrivateKey:
        """Parse ed25519 private key from orderly_secret string."""
        secret_b58 = orderly_secret.split(':')[1]
        private_bytes = base58.b58decode(secret_b58)
        return ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)

    def generate_signature(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None
    ) -> str:
        """Generate ed25519 signature for API request."""
        timestamp = int(time.time() * 1000)
        normalized = self._create_normalized_string(timestamp, method, path, params)
        signature_bytes = self._private_key.sign(normalized.encode('utf-8'))
        return base58.b58encode(signature_bytes).decode()

    def rest_authenticate(
        self,
        request: RESTRequest
    ) -> RESTRequest:
        """Add authentication headers to REST request."""
        timestamp = int(time.time() * 1000)
        signature = self.generate_signature(
            request.method,
            request.url.path,
            request.params or request.data
        )

        request.headers = request.headers or {}
        request.headers.update({
            "orderly-account-id": self._account_id,
            "orderly-key": self._orderly_key,
            "orderly-signature": signature,
            "orderly-timestamp": str(timestamp)
        })

        return request

    # No methods for:
    # - EIP-712 signing (not needed)
    # - Wallet management (not needed)
    # - Account registration (done in setup)
    # - Key generation (done in setup)
```

### Configuration Flow

**User Experience**:

1. **Run setup once** (outside Hummingbot):
   ```bash
   python scripts/orderly_setup.py
   # OR use Orderly UI
   ```

2. **Configure Hummingbot** (paste credentials):
   ```bash
   connect orderly_perpetual
   > Enter your Orderly Account ID: 0x1234...
   > Enter your Orderly Key: ed25519:ABCD...
   > Enter your Orderly Secret: ed25519:5678...
   ```

3. **Trade** (connector handles authentication):
   - Connector uses ed25519 signing for all requests
   - No wallet operations needed during trading
   - Standard Hummingbot connector behavior

---

## Part 5: Key Implementation Decisions

### Decision 1: Setup Responsibility ✅

**CHOSEN: Pre-Setup (Outside Connector)**

**Rationale**:
- ✅ Follows Hummingbot patterns (connectors don't manage accounts)
- ✅ Security: No wallet keys in trading bot
- ✅ Simplicity: Connector only handles trading authentication
- ✅ User familiarity: Same flow as other exchanges

**Rejected Alternative**: Integrated setup (wallet handling in connector)
- ❌ Security risk (wallet keys in bot)
- ❌ Complexity (EIP-712 signing, wallet management)
- ❌ Non-standard for Hummingbot
- ❌ Mixing concerns (trading vs account management)

### Decision 2: Setup Tool ✅

**CHOSEN: Provide Standalone Setup Script**

**Rationale**:
- ✅ Better UX than pure documentation
- ✅ Ensures correct implementation
- ✅ Reduces setup errors
- ✅ One-time use (not part of connector)

**Alternative**: Documentation only
- Use if resources are limited
- Point users to Orderly UI

### Decision 3: Authentication Architecture ✅

**CHOSEN: Pure ed25519 Authentication (No EIP-712 in Connector)**

The connector will:
- ✅ Accept pre-registered credentials
- ✅ Use ed25519 signing for all API requests
- ✅ Handle standard REST/WebSocket authentication
- ❌ NOT perform EIP-712 signing
- ❌ NOT handle wallet operations
- ❌ NOT register accounts or keys

### Decision 4: Credential Storage ✅

**CHOSEN: Standard Hummingbot Config Management**

```python
# In connector constants
KEYS = {
    "orderly_account_id": ConfigVar(...),
    "orderly_key": ConfigVar(...),
    "orderly_secret": ConfigVar(..., is_secure=True)  # Encrypted
}
```

### Decision 5: Error Handling for Setup Issues ✅

**Connector behavior when credentials are invalid**:

```python
async def _make_network_check_request(self) -> bool:
    """
    Verify credentials work by making a simple API call.
    Called during connector initialization.
    """
    try:
        response = await self._api_request(
            method="GET",
            path="/v1/client/info"
        )
        return True
    except Exception as e:
        if "authentication" in str(e).lower():
            self.logger().error(
                "❌ Orderly authentication failed. "
                "Your credentials may be invalid or expired.\n"
                "Please run the setup script again:\n"
                "  python scripts/orderly_setup.py"
            )
        raise
```

---

## Part 6: Setup Script Architecture

### High-Level Flow

```
User runs: python scripts/orderly_setup.py

1. Welcome & Requirements Check
   ├─ Check if web3 libraries installed
   ├─ Check network connectivity
   └─ Display requirements

2. Wallet Input
   ├─ Accept private key OR mnemonic
   ├─ Validate format
   ├─ Derive wallet address
   └─ Display address for confirmation

3. Network Selection
   ├─ Testnet or Mainnet
   └─ Set API base URLs

4. Builder Selection
   ├─ Fetch available builders from API
   ├─ Display list with descriptions
   └─ User selects builder ID

5. Account Registration
   ├─ Check if account exists
   ├─ Get registration nonce
   ├─ Create EIP-712 message
   ├─ Sign with wallet
   ├─ Submit to API
   ├─ Calculate account_id locally (verify match)
   └─ Confirm registration

6. Trading Key Generation
   ├─ Generate ed25519 key pair
   ├─ Format as ed25519:BASE58
   └─ Store temporarily

7. Trading Key Registration
   ├─ Create AddOrderlyKey message
   ├─ Sign with wallet (EIP-712)
   ├─ Submit to API
   └─ Verify key is active

8. Output Credentials
   ├─ Display on screen (copy-paste ready)
   ├─ Save to credentials.json (encrypted)
   ├─ Provide Hummingbot config instructions
   └─ Cleanup sensitive data from memory

9. Optional: Test Connection
   ├─ Make test API call with new credentials
   ├─ Verify authentication works
   └─ Display success confirmation
```

### Script Structure

```python
# scripts/orderly_setup.py

from dataclasses import dataclass
from typing import Optional
import json
import requests
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_structured_data
from cryptography.hazmat.primitives.asymmetric import ed25519
import base58
import time

@dataclass
class OrderlyCredentials:
    """Container for Orderly credentials."""
    account_id: str
    orderly_key: str
    orderly_secret: str
    network: str
    builder_id: str

class OrderlySetup:
    """Handles Orderly account and trading key setup."""

    TESTNET_API = "https://testnet-api.orderly.org"
    MAINNET_API = "https://api.orderly.org"

    OFFCHAIN_DOMAIN = "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"

    def __init__(self, testnet: bool = True):
        self.testnet = testnet
        self.base_url = self.TESTNET_API if testnet else self.MAINNET_API
        self.chain_id = 421614 if testnet else 42161  # Arbitrum

    # Wallet methods
    def load_wallet_from_key(self, private_key: str) -> Account:
        """Load wallet from private key."""
        pass

    def load_wallet_from_mnemonic(self, mnemonic: str) -> Account:
        """Load wallet from mnemonic phrase."""
        pass

    # Account registration methods
    def check_account_exists(self, wallet_address: str, builder_id: str) -> Optional[str]:
        """Check if account already registered."""
        pass

    def get_registration_nonce(self) -> int:
        """Fetch registration nonce from API."""
        pass

    def create_registration_message(
        self,
        builder_id: str,
        nonce: int
    ) -> dict:
        """Create EIP-712 registration message."""
        pass

    def sign_eip712_message(
        self,
        wallet: Account,
        message: dict,
        types: dict,
        primary_type: str
    ) -> str:
        """Sign EIP-712 structured message."""
        pass

    def register_account(
        self,
        wallet: Account,
        builder_id: str
    ) -> str:
        """Complete account registration flow."""
        pass

    def calculate_account_id(
        self,
        wallet_address: str,
        builder_id: str
    ) -> str:
        """Calculate account ID locally."""
        pass

    # Trading key methods
    def generate_ed25519_keypair(self) -> tuple[str, str]:
        """Generate ed25519 key pair in orderly format."""
        pass

    def create_add_key_message(
        self,
        builder_id: str,
        public_key: str,
        expiration_days: int = 365
    ) -> dict:
        """Create AddOrderlyKey message."""
        pass

    def register_trading_key(
        self,
        wallet: Account,
        builder_id: str,
        public_key: str
    ) -> bool:
        """Register trading key with Orderly."""
        pass

    # Testing methods
    def test_credentials(
        self,
        account_id: str,
        orderly_key: str,
        orderly_secret: str
    ) -> bool:
        """Test if credentials work."""
        pass

    # Complete setup flow
    def run_setup(self) -> OrderlyCredentials:
        """Run complete setup flow interactively."""
        pass

def main():
    """Interactive setup script."""
    print("="*70)
    print("  Orderly Network Setup for Hummingbot")
    print("="*70)
    print()

    # Network selection
    network_choice = input("Select network (1=Testnet, 2=Mainnet): ")
    testnet = network_choice == "1"

    setup = OrderlySetup(testnet=testnet)
    credentials = setup.run_setup()

    # Display results
    print("\n" + "="*70)
    print("  ✓ Setup Complete!")
    print("="*70)
    print(f"\nAccount ID:    {credentials.account_id}")
    print(f"Orderly Key:   {credentials.orderly_key}")
    print(f"Orderly Secret: {credentials.orderly_secret[:20]}...")
    print(f"Network:       {'Testnet' if testnet else 'Mainnet'}")
    print(f"Builder:       {credentials.builder_id}")
    print("\n" + "="*70)
    print("\nNext Steps:")
    print("1. Start Hummingbot")
    print("2. Run: connect orderly_perpetual")
    print("3. Paste the credentials above when prompted")
    print("="*70)

if __name__ == "__main__":
    main()
```

---

## Part 7: Implementation Checklist

### Pre-Implementation (Setup)

- [ ] Create `scripts/orderly_setup.py` script
  - [ ] Wallet loading (private key + mnemonic)
  - [ ] Account registration flow
  - [ ] Trading key generation
  - [ ] Trading key registration
  - [ ] Credential output and storage
  - [ ] Testing/verification

- [ ] Create setup documentation
  - [ ] Requirements (Python packages)
  - [ ] Step-by-step guide
  - [ ] Troubleshooting section
  - [ ] Security best practices

### Connector Implementation (Phase 1: Authentication)

- [ ] `orderly_perpetual_auth.py`
  - [ ] ed25519 key parsing
  - [ ] Signature generation (normalized string)
  - [ ] REST request authentication
  - [ ] WebSocket authentication
  - [ ] Order signature generation (if separate)
  - [ ] No EIP-712 signing
  - [ ] No wallet operations

- [ ] `orderly_perpetual_constants.py`
  - [ ] Configuration keys (account_id, orderly_key, orderly_secret)
  - [ ] API URLs (testnet/mainnet)
  - [ ] WebSocket URLs
  - [ ] Rate limits
  - [ ] Symbol format mappings

### Testing Authentication

- [ ] Unit tests for signature generation
- [ ] Integration tests with testnet
- [ ] Error handling for invalid credentials
- [ ] Credential expiration handling
- [ ] WebSocket authentication tests

### Documentation

- [ ] User guide: "Setting up Orderly Connector"
  - [ ] Setup script usage
  - [ ] Alternative: Manual setup via UI
  - [ ] Credential entry in Hummingbot
  - [ ] Testnet vs Mainnet

- [ ] Developer guide: "Orderly Authentication Architecture"
  - [ ] Two-tier system explanation
  - [ ] Why we don't handle wallets
  - [ ] ed25519 signing implementation
  - [ ] Future: Credential refresh automation

---

## Part 8: Advanced Considerations

### Credential Expiration

Trading keys expire (max 365 days). Future enhancements:

```python
# Future: Auto-renewal (requires wallet)
class OrderlyCredentialManager:
    """
    Manages credential lifecycle.

    WARNING: Requires wallet private key storage.
    Consider implications carefully.
    """

    def check_expiration(self) -> datetime:
        """Check when trading key expires."""
        pass

    def renew_trading_key(self) -> tuple[str, str]:
        """
        Generate and register new trading key.
        Requires: wallet private key or external signing service.
        """
        pass
```

**Recommendation**:
- Phase 1: Users manually renew (re-run setup script)
- Phase 2: Auto-renewal if secure wallet integration added

### Multi-Account Support

Users may have multiple Orderly accounts (different builders):

```python
# Config
orderly_perpetual_woofi:
  account_id: "0x1234..."
  orderly_key: "ed25519:ABC..."
  orderly_secret: "ed25519:123..."

orderly_perpetual_vertex:
  account_id: "0x5678..."
  orderly_key: "ed25519:DEF..."
  orderly_secret: "ed25519:456..."
```

### Delegate Signer Support

For smart contract accounts (future enhancement):

```python
class OrderlyDelegateSignerAuth(OrderlyPerpetualAuth):
    """
    Authentication for smart contract accounts using delegate signer.

    Requires EOA private key for withdrawals/settlements.
    """

    def __init__(
        self,
        contract_account_id: str,
        orderly_key: str,
        orderly_secret: str,
        delegate_eoa_key: Optional[str] = None  # For withdrawals
    ):
        super().__init__(account_id, orderly_key, orderly_secret)
        self._delegate_key = delegate_eoa_key
```

---

## Conclusion

### Summary

**Orderly uses a two-tier authentication system**:

1. **Setup Tier** (one-time, wallet-based):
   - Account registration (EIP-712 signature)
   - Trading key registration (EIP-712 signature)
   - Requires wallet private key

2. **Trading Tier** (ongoing, ed25519-based):
   - All API requests (ed25519 signature)
   - Order placement (ed25519 signature)
   - No wallet needed

### Hummingbot Integration Strategy

**Recommended Approach**: **Pre-Setup Pattern**

- **Setup**: Outside Hummingbot (standalone script or UI)
- **Trading**: Inside Hummingbot (standard connector)
- **Benefits**: Security, simplicity, follows Hummingbot patterns

### Next Steps

1. ✅ **Implement setup script** (`scripts/orderly_setup.py`)
2. ✅ **Implement authentication** (ed25519 only, no wallet handling)
3. ✅ **Follow existing implementation guide** for rest of connector
4. ✅ **Test thoroughly** on testnet
5. ✅ **Document** setup process clearly

### Files to Create

```
hummingbot/
├── connector/derivative/orderly_perpetual/
│   ├── orderly_perpetual_auth.py          # ed25519 auth (NEW)
│   ├── orderly_perpetual_constants.py     # Config, URLs
│   ├── orderly_perpetual_derivative.py    # Main connector
│   ├── orderly_perpetual_web_utils.py     # URL builders
│   ├── orderly_perpetual_order_book.py    # Order book data source
│   └── orderly_perpetual_user_stream.py   # User stream data source
│
└── scripts/
    └── orderly_setup.py                    # Setup utility (NEW)
```

---

**Generated by**: Claude Code
**Date**: 2025-10-27
**Document Version**: 1.0
