"""
User Stream Data Source for Orderly Network Perpetual Connector

This module handles:
- Private WebSocket connections with authentication
- Order execution updates (fills, cancellations, rejections)
- Position updates
- Balance updates
- Account-level events

Reference: Orderly Network EVM API
https://orderly.network/docs/build-on-omnichain/evm-api/websocket-api/private
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_constants as CONSTANTS
import hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_web_utils as web_utils
from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_auth import OrderlyPerpetualAuth
from hummingbot.core.data_type.user_stream_tracker_data_source import UserStreamTrackerDataSource
from hummingbot.core.web_assistant.connections.data_types import WSJSONRequest
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.core.web_assistant.ws_assistant import WSAssistant
from hummingbot.logger import HummingbotLogger

if TYPE_CHECKING:
    from hummingbot.connector.derivative.orderly_perpetual.orderly_perpetual_derivative import (
        OrderlyPerpetualDerivative,
    )


class OrderlyPerpetualUserStreamDataSource(UserStreamTrackerDataSource):
    """
    User stream data source for Orderly Network Perpetual.

    Manages private WebSocket connection for:
    - Order execution reports
    - Position updates
    - Balance changes
    """

    _logger: Optional[HummingbotLogger] = None

    def __init__(
        self,
        auth: OrderlyPerpetualAuth,
        trading_pairs: List[str],
        connector: 'OrderlyPerpetualDerivative',
        api_factory: WebAssistantsFactory,
        domain: str = CONSTANTS.DOMAIN,
    ):
        """
        Initialize the user stream data source.

        Args:
            auth: Authentication handler
            trading_pairs: List of trading pairs to track
            connector: Reference to main connector instance
            api_factory: Factory for creating REST/WS assistants
            domain: Domain identifier (mainnet or testnet)
        """
        super().__init__()
        self._auth = auth
        self._domain = domain
        self._api_factory = api_factory
        self._connector = connector
        self._trading_pairs = trading_pairs
        self._last_ws_message_timestamp = 0

    @classmethod
    def logger(cls) -> HummingbotLogger:
        """Get logger instance"""
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    @property
    def last_recv_time(self) -> float:
        """
        Return the time of the last received message.

        Returns:
            Timestamp of last received WebSocket message
        """
        return self._last_ws_message_timestamp

    async def _connected_websocket_assistant(self) -> WSAssistant:
        """
        Create and connect a WebSocket assistant for private stream.

        Orderly private WebSocket URL includes account_id:
        wss://ws-private-evm.orderly.org/v2/ws/private/stream/{account_id}

        Returns:
            Connected WSAssistant instance
        """
        # Get account ID from auth
        account_id = self._auth.account_id

        # Build private WebSocket URL with account_id
        ws_url = web_utils.wss_url(
            endpoint_type="private",
            domain=self._domain,
            account_id=account_id
        )

        # Create WebSocket assistant
        ws: WSAssistant = await self._api_factory.get_ws_assistant()

        # Connect to private WebSocket
        await ws.connect(
            ws_url=ws_url,
            ping_timeout=CONSTANTS.HEARTBEAT_TIME_INTERVAL
        )

        # Authenticate the connection
        await self._authenticate(ws)

        return ws

    async def _authenticate(self, ws: WSAssistant):
        """
        Authenticate the WebSocket connection.

        Orderly private WebSocket requires authentication after connection:
        {
            "id": "auth",
            "event": "auth",
            "params": {
                "orderly_key": "ed25519:BASE58_PUBLIC_KEY",
                "sign": "BASE58_SIGNATURE",
                "timestamp": 1683270060000
            }
        }

        The signature is: sign(timestamp) using ed25519 private key

        Args:
            ws: WebSocket assistant to authenticate
        """
        try:
            # Generate authentication payload
            auth_payload = await self._auth.get_ws_auth_payload()

            # Send authentication message
            auth_request = WSJSONRequest(payload=auth_payload)
            await ws.send(auth_request)

            # Wait for authentication response
            auth_response = await ws.receive()

            # Check if authentication was successful
            if auth_response.get("event") == "auth":
                if auth_response.get("success", False):
                    self.logger().info("Successfully authenticated private WebSocket connection")
                else:
                    error_msg = auth_response.get("message", "Unknown authentication error")
                    raise IOError(f"WebSocket authentication failed: {error_msg}")
            else:
                raise IOError(f"Unexpected authentication response: {auth_response}")

        except Exception as e:
            self.logger().error(f"Error authenticating WebSocket: {e}", exc_info=True)
            raise

    async def _subscribe_channels(self, websocket_assistant: WSAssistant):
        """
        Subscribe to private channels after authentication.

        Orderly private channels:
        - executionreport: Order updates and fills
        - position: Position changes
        - balance: Balance updates

        Subscription format:
        {
            "id": "unique-id",
            "topic": "executionreport",
            "event": "subscribe"
        }

        Args:
            websocket_assistant: Authenticated WebSocket assistant
        """
        try:
            # Subscribe to execution report (order updates and fills)
            execution_payload = {
                "id": "executionreport_subscribe",
                "topic": CONSTANTS.WS_EXECUTION_REPORT_CHANNEL,
                "event": "subscribe"
            }
            await websocket_assistant.send(WSJSONRequest(payload=execution_payload))

            # Subscribe to position updates
            position_payload = {
                "id": "position_subscribe",
                "topic": CONSTANTS.WS_POSITION_CHANNEL,
                "event": "subscribe"
            }
            await websocket_assistant.send(WSJSONRequest(payload=position_payload))

            # Subscribe to balance updates
            balance_payload = {
                "id": "balance_subscribe",
                "topic": CONSTANTS.WS_BALANCE_CHANNEL,
                "event": "subscribe"
            }
            await websocket_assistant.send(WSJSONRequest(payload=balance_payload))

            self.logger().info("Subscribed to private channels: executionreport, position, balance")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger().error(f"Error subscribing to private channels: {e}", exc_info=True)
            raise

    async def _process_event_message(self, event_message: Dict[str, Any], queue: asyncio.Queue):
        """
        Process and route private channel messages to the output queue.

        Orderly private message format:
        {
            "topic": "executionreport",
            "ts": 1683270060000,
            "data": {...}
        }

        Args:
            event_message: WebSocket message from private channel
            queue: Output queue for processed messages
        """
        # Update last message timestamp
        self._last_ws_message_timestamp = event_message.get("ts", 0) / 1000

        # Check message type
        event_type = event_message.get("event")

        # Skip subscription confirmations and pings
        if event_type in ["subscribe", "unsubscribe", "pong"]:
            return

        # Handle authentication responses
        if event_type == "auth":
            if not event_message.get("success", False):
                error_msg = event_message.get("message", "Authentication failed")
                self.logger().error(f"WebSocket authentication error: {error_msg}")
            return

        # Handle error messages
        if event_type == "error":
            error_msg = event_message.get("message", "Unknown error")
            self.logger().error(f"WebSocket error: {error_msg}")
            return

        # Route data messages to the queue
        topic = event_message.get("topic")

        if topic in [
            CONSTANTS.WS_EXECUTION_REPORT_CHANNEL,
            CONSTANTS.WS_POSITION_CHANNEL,
            CONSTANTS.WS_BALANCE_CHANNEL,
        ]:
            # Forward the message to the connector for processing
            queue.put_nowait(event_message)

    async def _process_websocket_messages(self, websocket_assistant: WSAssistant, queue: asyncio.Queue):
        """
        Process incoming WebSocket messages continuously.

        Args:
            websocket_assistant: Connected WebSocket assistant
            queue: Output queue for messages
        """
        async for ws_response in websocket_assistant.iter_messages():
            data = ws_response.data
            await self._process_event_message(event_message=data, queue=queue)
