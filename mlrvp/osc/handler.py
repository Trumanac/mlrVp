"""OSC handler for mlrVp.

Provides OSC control input and button-mirroring output
(matching the serialosnoopy feature from mlrV2.2).
"""

import threading
from typing import Optional, Callable


class OscHandler:
    """Handles OSC input and output for remote control and visualization."""

    def __init__(self, recv_port: int = 8001, send_port: int = 8000,
                 send_host: str = "127.0.0.1"):
        self.recv_port = recv_port
        self.send_port = send_port
        self.send_host = send_host
        self._server = None
        self._client = None
        self._server_thread: Optional[threading.Thread] = None
        self._running = False
        self._handlers: dict[str, Callable] = {}

    def start(self):
        """Start OSC server and client."""
        try:
            from pythonosc import osc_server, dispatcher, udp_client

            # Client for sending
            self._client = udp_client.SimpleUDPClient(self.send_host, self.send_port)

            # Server for receiving
            disp = dispatcher.Dispatcher()
            disp.set_default_handler(self._default_handler)

            # Register specific handlers
            for address, handler in self._handlers.items():
                disp.map(address, handler)

            self._server = osc_server.ThreadingOSCUDPServer(
                ("0.0.0.0", self.recv_port), disp
            )
            self._server_thread = threading.Thread(
                target=self._server.serve_forever, daemon=True
            )
            self._server_thread.start()
            self._running = True

        except ImportError:
            import logging
            logging.getLogger("mlrvp").warning("python-osc not available — OSC disabled")
            self._running = False
            self._client = None
        except Exception as e:
            import logging
            logging.getLogger("mlrvp").warning("OSC start error: %s", e)
            self._running = False
            self._client = None
            self._server = None

    def stop(self):
        self._running = False
        if self._server is not None:
            self._server.shutdown()
            self._server = None

    def add_handler(self, address: str, handler: Callable):
        """Register an OSC address handler."""
        self._handlers[address] = handler

    def send(self, address: str, *args):
        """Send an OSC message."""
        if self._client is not None:
            try:
                self._client.send_message(address, list(args))
            except Exception as e:
                import logging
                logging.getLogger("mlrvp").debug("OSC send failed: %s", e)

    def mirror_button(self, x: int, y: int, state: int):
        """Mirror a grid button press via OSC (serialosnoopy-style)."""
        self.send("/mlrvp/grid/key", x, y, state)

    def send_position(self, row: int, position: float):
        """Send playback position via OSC."""
        self.send("/mlrvp/position", row, position)

    def _default_handler(self, address, *args):
        """Default handler for unmapped OSC addresses."""
        pass
