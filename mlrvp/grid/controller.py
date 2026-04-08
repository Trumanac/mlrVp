"""Grid controller interface for mlrVp.

Handles communication with:
- Monome grids via serialosc (OSC protocol)
- Novation Launchpad series via MIDI
- Generic MIDI grid controllers
- Built-in virtual grid (always available)
"""

import asyncio
import logging
import threading
from typing import Optional, Callable
from dataclasses import dataclass

from mlrvp.config import MAX_ROWS, MAX_COLUMNS, LED_OFF, LED_DIM, LED_MED, LED_BRIGHT
from mlrvp.grid.midi_grid import MidiGridAdapter, scan_midi_grid_devices

from PySide6.QtCore import QTimer

log = logging.getLogger("mlrvp")


@dataclass
class GridInfo:
    """Information about a connected grid."""
    id: str = ""
    type: str = ""
    port: int = 0
    host: str = "127.0.0.1"
    prefix: str = "/mlrvp"
    rows: int = 8
    columns: int = 16
    rotation: int = 0


class GridController:
    """Interface for monome grid hardware via serialosc.

    Also manages an internal LED state buffer that can be used by
    the virtual grid in the GUI.
    """

    def __init__(self):
        self.info = GridInfo()
        self._connected = False
        self._led_state = [[LED_OFF] * MAX_COLUMNS for _ in range(MAX_ROWS)]
        self._control_led_state = [LED_OFF] * MAX_COLUMNS
        self._control_led_sent: dict[int, tuple] = {}  # track sent state to avoid MIDI spam
        self._button_callback: Optional[Callable] = None
        self._control_button_callback: Optional[Callable] = None
        self._led_update_callback: Optional[Callable] = None
        self._monome = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._monome_row_offset = 0  # 1 when monome connected (top row = control)
        self._pending_monome_connect = False

        # MIDI grid controller (Launchpad, etc.)
        self._midi_grid: Optional[MidiGridAdapter] = None

        # Tilt / accelerometer support (monome only)
        self._tilt_callback: Optional[Callable] = None
        self._tilt_enabled = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def has_control_row(self) -> bool:
        """True if the grid uses a physical control row (monome top row)."""
        return self._monome_row_offset > 0

    @property
    def led_state(self) -> list[list[int]]:
        return self._led_state

    def set_button_callback(self, callback: Callable):
        """Set callback for button presses: callback(x, y, state)."""
        self._button_callback = callback

    def set_control_button_callback(self, callback: Callable):
        """Set callback for control row presses: callback(x, state)."""
        self._control_button_callback = callback

    def set_led_update_callback(self, callback: Callable):
        """Set callback when LED state changes: callback(led_state)."""
        self._led_update_callback = callback

    def set_tilt_callback(self, callback: Callable):
        """Set callback for accelerometer/tilt data: callback(sensor, x, y, z).

        x, y, z are normalized 0.0-1.0 values from the monome accelerometer.
        """
        self._tilt_callback = callback

    def enable_tilt(self, enabled: bool = True):
        """Enable or disable tilt/accelerometer sensor on the monome."""
        self._tilt_enabled = enabled
        if self._monome is not None and hasattr(self._monome, 'grid'):
            try:
                self._monome.grid.tilt_set(0, 1 if enabled else 0)
            except Exception as e:
                log.debug("Tilt enable failed: %s", e)

    def connect(self):
        """Connect to serialosc and find a monome grid."""
        try:
            import pymonome
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run_async, daemon=True)
            self._thread.start()
        except ImportError:
            log.info("pymonome not available — monome grids won't be detected")
        except Exception as e:
            log.warning("Grid connection error: %s", e)

    def connect_midi_grid(self, in_port: int, out_port: int = -1) -> bool:
        """Connect a MIDI grid controller (Launchpad, etc.)."""
        self.disconnect_midi_grid()
        self._midi_grid = MidiGridAdapter()
        self._midi_grid.set_button_callback(self._on_midi_grid_button)
        self._midi_grid.set_control_callback(self._on_midi_grid_control_button)
        if self._midi_grid.connect(in_port, out_port):
            self._connected = True
            self._control_led_sent.clear()  # force LED refresh on reconnect
            self.info.type = f"midi:{self._midi_grid.model.value}"
            self.info.rows = self._midi_grid.rows
            self.info.columns = self._midi_grid.columns
            log.info("MIDI grid connected: %s (%dx%d)",
                     self._midi_grid.model.value,
                     self._midi_grid.columns, self._midi_grid.rows)
            return True
        self._midi_grid = None
        return False

    def disconnect_midi_grid(self):
        """Disconnect the MIDI grid controller."""
        if self._midi_grid is not None:
            self._midi_grid.disconnect()
            self._midi_grid = None
            if self._monome is None:
                self._connected = False

    def _on_midi_grid_button(self, x: int, y: int, state: int):
        """Forward MIDI grid button events to the main callback (on Qt thread)."""
        cb = self._button_callback
        if cb:
            QTimer.singleShot(0, lambda: cb(x, y, state))

    def _on_midi_grid_control_button(self, btn_idx: int, state: int):
        """Forward MIDI grid control button events (on Qt thread)."""
        cb = self._control_button_callback
        if cb:
            QTimer.singleShot(0, lambda: cb(btn_idx, state))

    @staticmethod
    def scan_midi_grids() -> list[dict]:
        """Scan for MIDI grid controllers (Launchpads, etc.)."""
        return scan_midi_grid_devices()

    def _run_async(self):
        """Run the asyncio event loop for serialosc communication."""
        asyncio.set_event_loop(self._loop)
        try:
            import pymonome

            class MlrGridApp(pymonome.GridApp):
                def __init__(self, controller):
                    super().__init__()
                    self.controller = controller

                def on_grid_ready(self):
                    self.controller._connected = True
                    self.controller.info.rows = self.grid.height
                    self.controller.info.columns = self.grid.width
                    self.controller._monome_row_offset = 1  # top row = controls
                    self.controller._pending_monome_connect = True
                    self.controller._refresh_leds()
                    # Enable tilt sensor if requested
                    if self.controller._tilt_enabled:
                        try:
                            self.grid.tilt_set(0, 1)
                        except Exception:
                            pass

                def on_grid_key(self, x, y, s):
                    if self.controller._monome_row_offset > 0 and y == 0:
                        # Top row → control buttons
                        cb = self.controller._control_button_callback
                        if cb:
                            QTimer.singleShot(0, lambda: cb(x, s))
                    else:
                        cb = self.controller._button_callback
                        if cb:
                            # Data rows: offset y for channel mapping
                            adj_y = y - self.controller._monome_row_offset
                            QTimer.singleShot(0, lambda: cb(x, adj_y, s))

                def on_grid_disconnect(self):
                    self.controller._connected = False
                    self.controller._monome_row_offset = 0

                def on_tilt(self, n, x, y, z):
                    """Accelerometer data from the monome."""
                    if self.controller._tilt_callback:
                        # Normalize raw tilt values (typically 0-255) to 0.0-1.0
                        nx = max(0.0, min(1.0, x / 255.0))
                        ny = max(0.0, min(1.0, y / 255.0))
                        nz = max(0.0, min(1.0, z / 255.0))
                        self.controller._tilt_callback(n, nx, ny, nz)

            app = MlrGridApp(self)
            self._monome = app
            self._loop.run_until_complete(
                pymonome.SerialOsc.create(loop=self._loop, autoconnect_app=app)
            )
            self._loop.run_forever()
        except Exception as e:
            print(f"Serialosc error: {e}")

    def disconnect(self):
        """Disconnect from all grid hardware."""
        self._connected = False
        self.disconnect_midi_grid()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def set_led(self, x: int, y: int, brightness: int, color: int = 0):
        """Set a single LED (logical channel row coordinates)."""
        if 0 <= x < MAX_COLUMNS and 0 <= y < MAX_ROWS:
            self._led_state[y][x] = brightness
            if self._connected and self._monome is not None:
                try:
                    physical_y = y + self._monome_row_offset
                    self._monome.grid.led_level_set(x, physical_y, brightness)
                except Exception:
                    pass
            if self._midi_grid is not None and self._midi_grid.connected:
                self._midi_grid.set_led(x, y, brightness, color)

    def set_control_led(self, x: int, brightness: int, color: int = 0):
        """Set an LED on the control row (monome top row / LP CC buttons)."""
        if 0 <= x < MAX_COLUMNS:
            key = (x, brightness, color)
            if key == self._control_led_sent.get(x):
                return  # unchanged — skip redundant MIDI send
            self._control_led_sent[x] = key
            self._control_led_state[x] = brightness
            if self._connected and self._monome is not None and self._monome_row_offset > 0:
                try:
                    self._monome.grid.led_level_set(x, 0, brightness)
                except Exception:
                    pass
            if self._midi_grid is not None and self._midi_grid.connected:
                self._midi_grid.set_control_led(x, brightness, color)

    def set_row(self, y: int, values: list[int], color: int = 0):
        """Set an entire row of LEDs (logical channel row coordinates)."""
        if 0 <= y < MAX_ROWS:
            for x, val in enumerate(values):
                if x < MAX_COLUMNS:
                    self._led_state[y][x] = val
            if self._connected and self._monome is not None:
                try:
                    physical_y = y + self._monome_row_offset
                    self._monome.grid.led_level_row(0, physical_y, values[:self.info.columns])
                except Exception:
                    pass
            if self._midi_grid is not None and self._midi_grid.connected:
                self._midi_grid.set_row(y, values, color)

    def set_all(self, brightness: int):
        """Set all LEDs to the same brightness."""
        for y in range(MAX_ROWS):
            for x in range(MAX_COLUMNS):
                self._led_state[y][x] = brightness
        self._refresh_leds()

    def clear(self):
        """Turn off all LEDs."""
        self.set_all(LED_OFF)

    def _refresh_leds(self):
        """Push all LED state to the hardware grid."""
        if self._connected and self._monome is not None:
            # Control row at physical y=0 for monome
            if self._monome_row_offset > 0:
                try:
                    self._monome.grid.led_level_row(
                        0, 0, self._control_led_state[:self.info.columns]
                    )
                except Exception:
                    pass
            # Data rows offset by monome_row_offset
            data_rows = self.info.rows - self._monome_row_offset
            for y in range(min(data_rows, MAX_ROWS)):
                try:
                    physical_y = y + self._monome_row_offset
                    self._monome.grid.led_level_row(
                        0, physical_y, self._led_state[y][:self.info.columns]
                    )
                except Exception:
                    pass

    def update_playback_leds(self, row: int, play_column: int, total_columns: int,
                             sub_loop_start: int = -1, sub_loop_end: int = -1,
                             color: int = 0):
        """Update LEDs for a row showing playback position.

        Shows a bright LED at playback position, dim LEDs for sub-loop range.
        """
        values = [LED_OFF] * total_columns
        # Show sub-loop range as dim
        if sub_loop_start >= 0 and sub_loop_end >= 0:
            lo, hi = min(sub_loop_start, sub_loop_end), max(sub_loop_start, sub_loop_end)
            for c in range(lo, min(hi + 1, total_columns)):
                values[c] = LED_DIM
        if 0 <= play_column < total_columns:
            values[play_column] = LED_BRIGHT
        self.set_row(row, values, color)

    def clear_row_leds(self, row: int, total_columns: int):
        """Turn off all LEDs in a row."""
        values = [LED_OFF] * total_columns
        self.set_row(row, values, 0)

    def handle_virtual_press(self, x: int, y: int, state: int):
        """Handle a button press from the virtual grid UI."""
        if self._button_callback:
            self._button_callback(x, y, state)
