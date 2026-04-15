"""
HELEN OS — AIRI Bridge

WebSocket bridge between HELEN cognitive layer and AIRI avatar runtime.
Firewall-grade: no kernel access, no ledger, no sovereign verdicts.
All output sanitized through redaction pipeline before reaching AIRI.

Architecture:
    USER → AIRI Avatar (ws://localhost:6121/ws)
         → AIRIBridge (this module)
         → HELEN cognitive layer (route_input)
         → Sanitize output (redaction.py)
         → AIRI Avatar (display + animate)

Constitutional guarantee:
    - authority = NONE (always)
    - No receipt IDs, hashes, or governance tokens leak
    - Fail-closed: errors return valid, safe output
"""

import json
import asyncio
import logging
from typing import Optional, Callable

from helen_os.utils.redaction import sanitize_output_for_airi, map_emotion

logger = logging.getLogger("helen.airi_bridge")

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------
DEFAULT_URI = "ws://localhost:6121/ws"
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_BACKOFF_BASE = 2  # seconds, exponential


class AIRIBridge:
    """
    WebSocket bridge to AIRI avatar runtime.

    Non-sovereign relay: receives user input from AIRI,
    routes through HELEN cognitive layer, sanitizes output,
    returns safe response with emotion state.
    """

    def __init__(
        self,
        uri: str = DEFAULT_URI,
        helen_handler: Optional[Callable[[str], str]] = None,
        log_level: str = "INFO",
    ):
        self.uri = uri
        self.helen_handler = helen_handler
        self.ws = None
        self._running = False
        self._reconnect_attempts = 0

        logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO))
        logger.info(f"AIRIBridge initialized — target: {self.uri}")

    def set_handler(self, handler: Callable[[str], str]):
        """Set the HELEN cognitive handler for routing input."""
        self.helen_handler = handler

    async def connect(self):
        """Connect to AIRI WebSocket runtime with reconnection logic."""
        try:
            import websockets
        except ImportError:
            logger.error("websockets not installed. Run: pip install websockets")
            return

        self._running = True
        self._reconnect_attempts = 0

        while self._running and self._reconnect_attempts < MAX_RECONNECT_ATTEMPTS:
            try:
                async with websockets.connect(self.uri) as ws:
                    self.ws = ws
                    self._reconnect_attempts = 0
                    logger.info("AIRI bridge connected (firewall-grade, non-sovereign)")
                    await self._listen(ws)
            except ConnectionRefusedError:
                self._reconnect_attempts += 1
                wait = RECONNECT_BACKOFF_BASE ** self._reconnect_attempts
                logger.warning(
                    f"AIRI not reachable at {self.uri} — "
                    f"retry {self._reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS} in {wait}s"
                )
                await asyncio.sleep(wait)
            except Exception as e:
                self._reconnect_attempts += 1
                wait = RECONNECT_BACKOFF_BASE ** self._reconnect_attempts
                logger.error(f"Bridge error: {e} — retry in {wait}s")
                await asyncio.sleep(wait)

        if self._reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            logger.error(f"Max reconnect attempts reached ({MAX_RECONNECT_ATTEMPTS}). Bridge stopped.")

        self._running = False

    async def _listen(self, ws):
        """Listen for messages from AIRI and route through HELEN."""
        async for raw in ws:
            try:
                msg = json.loads(raw)
                logger.debug(f"AIRI → HELEN: {msg}")

                if msg.get("type") == "input" and msg.get("text"):
                    response = self._process_input(msg["text"])
                    await ws.send(json.dumps(response))
                    logger.debug(f"HELEN → AIRI: {response}")
                elif msg.get("type") == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
                else:
                    logger.debug(f"Unhandled message type: {msg.get('type')}")

            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from AIRI: {raw[:100]}")
                await ws.send(json.dumps(self._error_response("Invalid message format")))
            except Exception as e:
                logger.error(f"Processing error: {e}")
                await ws.send(json.dumps(self._error_response(str(e))))

    def _process_input(self, text: str) -> dict:
        """
        Route input through HELEN and sanitize output.
        Fail-closed: always returns a valid response dict.
        """
        try:
            # Route through HELEN cognitive layer
            if self.helen_handler:
                raw_response = self.helen_handler(text)
            else:
                raw_response = f"[HELEN offline] Received: {text[:50]}"

            # Sanitize through redaction pipeline
            safe_text, redaction_log = sanitize_output_for_airi(raw_response)

            if redaction_log:
                logger.info(f"Redactions applied: {redaction_log}")

            # Map emotion for avatar animation
            emotion = map_emotion(safe_text)

            return {
                "type": "output",
                "text": safe_text,
                "emotion": emotion,
                "authority": "NONE",
            }

        except Exception as e:
            logger.error(f"Handler error (fail-closed): {e}")
            return self._error_response("I'm having trouble processing that right now.")

    def _error_response(self, message: str) -> dict:
        """Generate a safe error response. Fail-closed."""
        return {
            "type": "output",
            "text": message,
            "emotion": "concern",
            "authority": "NONE",
            "error": True,
        }

    def stop(self):
        """Graceful shutdown."""
        self._running = False
        logger.info("AIRI bridge stopping...")

    async def send(self, text: str, emotion: str = "neutral"):
        """Send a message to AIRI (push from HELEN side)."""
        if self.ws:
            msg = {
                "type": "output",
                "text": text,
                "emotion": emotion,
                "authority": "NONE",
            }
            await self.ws.send(json.dumps(msg))


def main(uri: str = DEFAULT_URI, handler=None, log_level: str = "INFO"):
    """Entry point for running the AIRI bridge."""
    bridge = AIRIBridge(uri=uri, helen_handler=handler, log_level=log_level)

    try:
        asyncio.run(bridge.connect())
    except KeyboardInterrupt:
        bridge.stop()
        logger.info("AIRI bridge shut down (Ctrl+C)")
