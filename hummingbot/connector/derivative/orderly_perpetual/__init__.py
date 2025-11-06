# """
# Orderly Network Perpetual Connector

# This connector integrates Hummingbot with Orderly Network's perpetual futures trading platform.

# Key Components:
# - Authentication: ed25519 signature-based (orderly_perpetual_auth.py)
# - Order Book Data: Public market data via WebSocket (orderly_perpetual_api_order_book_data_source.py)
# - User Stream: Private order/position updates via WebSocket (orderly_perpetual_user_stream_data_source.py)
# - Main Connector: Trading operations and lifecycle management (orderly_perpetual_derivative.py)

# Documentation:
# - Orderly API: https://orderly.network/docs/build-on-omnichain/evm-api/introduction
# - Hummingbot Docs: https://hummingbot.org/developers/connectors/perp-connector-checklist/
# """

# from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_derivative import OrderlyPerpetualDerivative

# __all__ = [
#     "OrderlyPerpetualDerivative",
# ]
