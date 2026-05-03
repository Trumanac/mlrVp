"""OSC adapter for legacy monomeserial-managed monome grids.

monomeserial predates serialosc and bridges older monome devices to OSC.
This module exposes a small wrapper that mimics the subset of the
``monome.GridApp`` / ``monome.Grid`` API used by ``grid/controller.py`` so
mlrVp can talk to legacy grids through monomeserial instead of serialosc.

The default monomeserial OSC wiring on Windows is:

* host address: 127.0.0.1
* host port: 8000   (where button presses are sent)
* listen port: 8080 (where LED/system messages are received)

The adapter probes ``/sys/report`` on startup to discover the active prefix,
device type, and serial number, then sends LED updates to the configured OSC
prefix. Coordinates stay local to the grid; monomeserial offsets are applied
internally when sending / receiving messages.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger("mlrvp")

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_RECV_PORT = 8000
_DEFAULT_SEND_PORT = 8080
_DEFAULT_PREFIX = "/64"

_TYPE_DIMENSIONS: dict[str, tuple[int, int]] = {
    "40h": (8, 8),
    "64": (8, 8),
    "128": (16, 8),
    "256": (16, 16),
    "mk": (16, 8),
}


def _normalize_prefix(prefix: str) -> str:
    prefix = (prefix or "").strip()
    if not prefix:
        return ""
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    if len(prefix) > 1 and prefix.endswith("/"):
        prefix = prefix[:-1]
    return prefix


@dataclass
class _DeviceInfo:
    prefix: str = ""
    serial: str = ""
    device_type: str = ""
    count: int = 0
    offset_x: int = 0
    offset_y: int = 0

    @property
    def dimensions(self) -> tuple[int, int]:
        device_type = self.device_type.lower()
        if device_type in _TYPE_DIMENSIONS:
            return _TYPE_DIMENSIONS[device_type]
        # Fallback: infer width from common prefixes if /sys/type never arrived.
        prefix_key = self.prefix.lstrip("/").lower()
        return _TYPE_DIMENSIONS.get(prefix_key, (8, 8))


class _MonomeSerialGrid:
    """Hardware-facing grid shim that mirrors the needed monome.Grid API."""

    def __init__(
        self,
        send_host: str = _DEFAULT_HOST,
        send_port: int = _DEFAULT_SEND_PORT,
        recv_host: str = _DEFAULT_HOST,
        recv_port: int = _DEFAULT_RECV_PORT,
        prefix: str = "",
    ):
        self.send_host = send_host or _DEFAULT_HOST
        self.send_port = int(send_port)
        self.recv_host = recv_host or _DEFAULT_HOST
        self.recv_port = int(recv_port)
        self.rotation = 0

        self._device = _DeviceInfo(prefix=_normalize_prefix(prefix))
        self._client = None
        self._server = None
        self._server_thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._closed = False
        self._lock = threading.Lock()
        self._on_grid_key: Optional[Callable[[int, int, int], None]] = None

    @property
    def width(self) -> int:
        return self._device.dimensions[0]

    @property
    def height(self) -> int:
        return self._device.dimensions[1]

    @property
    def id(self) -> str:
        if self._device.serial:
            return self._device.serial
        if self._device.device_type:
            return f"monomeserial-{self._device.device_type}"
        return "monomeserial"

    def open(self, timeout: float = 1.5):
        """Start OSC I/O and wait briefly for monomeserial's /sys/report."""
        try:
            from pythonosc import dispatcher, osc_server, udp_client
        except ImportError as exc:
            raise RuntimeError("python-osc is required for monomeserial mode") from exc

        disp = dispatcher.Dispatcher()
        disp.set_default_handler(self._handle_message)

        self._server = osc_server.ThreadingOSCUDPServer(
            (self.recv_host, self.recv_port), disp
        )
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="mlrvp-monomeserial-osc",
        )
        self._server_thread.start()

        self._client = udp_client.SimpleUDPClient(self.send_host, self.send_port)
        deadline = time.monotonic() + timeout
        while not self._ready_event.is_set():
            self.request_report()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._ready_event.wait(min(0.2, remaining))

        if not self._ready_event.is_set():
            self.close()
            raise RuntimeError(
                "monomeserial did not answer /sys/report on "
                f"{self.send_host}:{self.send_port} -> {self.recv_host}:{self.recv_port}"
            )

        # Clear any stale LED state before mlrVp paints its initial frame.
        self.clear()

    def close(self):
        self._closed = True
        self.clear()
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                # Release the UDP socket immediately so the port can be rebound
                # on the next connect attempt.  shutdown() stops the serve_forever
                # loop but does NOT close the socket; server_close() does.
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        if self._server_thread is not None:
            self._server_thread.join(timeout=2.0)
            self._server_thread = None
        self._client = None

    def request_report(self):
        if self._client is None:
            return
        try:
            self._client.send_message("/sys/report", [])
        except Exception as exc:
            log.debug("monomeserial /sys/report send failed: %s", exc)

    def set_button_callback(self, cb: Optional[Callable[[int, int, int], None]]):
        self._on_grid_key = cb

    def tilt_set(self, *_):
        """Legacy monomeserial path exposes no tilt API we need here."""

    def clear(self):
        prefix = self._current_prefix()
        if not prefix or self._client is None:
            return
        try:
            self._client.send_message(f"{prefix}/clear", [0])
        except Exception as exc:
            log.debug("monomeserial clear failed: %s", exc)

    def led_level_set(self, x: int, y: int, brightness: int):
        prefix = self._current_prefix()
        if not prefix or self._client is None:
            return
        with self._lock:
            send_x = x + self._device.offset_x
            send_y = y + self._device.offset_y
        try:
            self._client.send_message(
                f"{prefix}/led",
                [int(send_x), int(send_y), 1 if brightness > 0 else 0],
            )
        except Exception as exc:
            log.debug("monomeserial LED send failed: %s", exc)

    def led_level_row(self, x_offset: int, y: int, values: list[int]):
        for x, value in enumerate(values):
            self.led_level_set(x_offset + x, y, value)

    def _current_prefix(self) -> str:
        prefix = self._device.prefix or _DEFAULT_PREFIX
        return _normalize_prefix(prefix)

    def _mark_ready_if_possible(self):
        with self._lock:
            # We need at least a prefix (for LED/button routing) and a
            # device_type (for grid dimensions).  count and serial are
            # informational only — older monomeserial builds may not send
            # them, and a missing /sys/devices or /sys/serial must not
            # prevent a successful connection.
            if not self._device.prefix:
                return
            if not self._device.device_type:
                return
            self._ready_event.set()

    def _handle_message(self, address: str, *args):
        if self._closed:
            return

        try:
            if address == "/sys/devices":
                with self._lock:
                    if args:
                        self._device.count = int(args[0])
                self._mark_ready_if_possible()
                return

            if address == "/sys/prefix":
                prefix = ""
                if len(args) >= 2:
                    prefix = str(args[1])
                elif args:
                    prefix = str(args[0])
                prefix = _normalize_prefix(prefix)
                if prefix:
                    with self._lock:
                        self._device.prefix = prefix
                    self._mark_ready_if_possible()
                return

            if address == "/sys/type":
                device_type = ""
                if len(args) >= 2:
                    device_type = str(args[1])
                elif args:
                    device_type = str(args[0])
                with self._lock:
                    self._device.device_type = device_type.strip().lower()
                self._mark_ready_if_possible()
                return

            if address == "/sys/serial":
                serial = ""
                if len(args) >= 2:
                    serial = str(args[1])
                elif args:
                    serial = str(args[0])
                with self._lock:
                    self._device.serial = serial.strip()
                # Serial is the last field monomeserial typically sends;
                # check readiness so we don't time out waiting for a call
                # that was already satisfied by the three prior fields.
                self._mark_ready_if_possible()
                return

            if address == "/sys/offset":
                if len(args) >= 3:
                    offset_x = int(args[1])
                    offset_y = int(args[2])
                elif len(args) >= 2:
                    offset_x = int(args[0])
                    offset_y = int(args[1])
                else:
                    return
                with self._lock:
                    self._device.offset_x = offset_x
                    self._device.offset_y = offset_y
                return

            if not address.endswith("/press"):
                return

            prefix = _normalize_prefix(address[:-6])
            with self._lock:
                expected_prefix = self._current_prefix()
                offset_x = self._device.offset_x
                offset_y = self._device.offset_y
                width, height = self._device.dimensions
            if expected_prefix and prefix != expected_prefix:
                return
            if len(args) < 3:
                return
            x = int(args[0]) - offset_x
            y = int(args[1]) - offset_y
            s = 1 if int(args[2]) > 0 else 0
            if not (0 <= x < width and 0 <= y < height):
                return
            cb = self._on_grid_key
            if cb:
                cb(x, y, s)
        except Exception:
            log.debug("monomeserial OSC parse failed for %s %r", address, args, exc_info=True)


class MonomeSerialApp:
    """Wrapper that mimics ``monome.GridApp`` for monomeserial grids."""

    def __init__(self):
        self.grid: Optional[_MonomeSerialGrid] = None

    def on_grid_ready(self):
        pass

    def on_grid_key(self, x: int, y: int, s: int):
        pass

    def on_grid_disconnect(self, *_):
        pass

    def on_tilt(self, n: int, x: float, y: float, z: float):
        pass

    def open_osc(
        self,
        send_host: str = _DEFAULT_HOST,
        send_port: int = _DEFAULT_SEND_PORT,
        recv_host: str = _DEFAULT_HOST,
        recv_port: int = _DEFAULT_RECV_PORT,
        prefix: str = "",
        timeout: float = 1.5,
    ):
        if self.grid is not None:
            return
        self.grid = _MonomeSerialGrid(
            send_host=send_host,
            send_port=send_port,
            recv_host=recv_host,
            recv_port=recv_port,
            prefix=prefix,
        )
        self.grid.set_button_callback(self._dispatch_key)
        self.grid.open(timeout=timeout)
        try:
            self.on_grid_ready()
        except Exception:
            log.exception("on_grid_ready failed")

    def request_report(self):
        if self.grid is not None:
            self.grid.request_report()

    def close(self):
        grid = self.grid
        if grid is None:
            return
        try:
            grid.close()
        finally:
            self.grid = None
            try:
                self.on_grid_disconnect()
            except Exception:
                log.exception("on_grid_disconnect failed")

    def _dispatch_key(self, x: int, y: int, s: int):
        try:
            self.on_grid_key(x, y, s)
        except Exception:
            log.exception("on_grid_key failed")