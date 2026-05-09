"""MIDI grid controller adapter for mlrVp.

Supports Novation Launchpad series (Mini, S, MK2, MK3, X, Pro)
and generic MIDI grid controllers that send note-on/off for buttons.
"""

import logging
import queue
import threading
from typing import Optional, Callable
from enum import Enum

log = logging.getLogger("mlrvp")


class LaunchpadModel(Enum):
    """Known Launchpad hardware variants."""
    MINI_MK1 = "Launchpad Mini"
    MINI_MK2 = "Launchpad Mini MK2"
    MINI_MK3 = "Launchpad Mini MK3"
    S = "Launchpad S"
    MK2 = "Launchpad MK2"
    X = "Launchpad X"
    PRO = "Launchpad Pro"
    PRO_MK3 = "Launchpad Pro MK3"
    GENERIC = "Generic MIDI Grid"


# Launchpad Mini / S / MK1 note layout (8x8 grid)
# Top row: 0-7, next: 16-23, etc. (stride 16, rows 0-7)
_LP_CLASSIC_NOTE_MAP = {}
_LP_CLASSIC_COORD_MAP = {}
for _r in range(8):
    for _c in range(8):
        _note = _r * 16 + _c
        _LP_CLASSIC_NOTE_MAP[_note] = (_c, _r)
        _LP_CLASSIC_COORD_MAP[(_c, _r)] = _note

# Launchpad MK2 / X / Mini MK3 note layout (8x8 grid, programmer mode)
# Row 0 (top): notes 81-88, Row 1: 71-78, ... Row 7 (bottom): 11-18
_LP_MK2_NOTE_MAP = {}
_LP_MK2_COORD_MAP = {}
for _r in range(8):
    for _c in range(8):
        _note = (8 - _r) * 10 + (_c + 1)
        _LP_MK2_NOTE_MAP[_note] = (_c, _r)
        _LP_MK2_COORD_MAP[(_c, _r)] = _note

# Velocity color mapping for Launchpad LED feedback
# Classic Launchpads: velocity = red(0-3) | flags(12) | green(0-3)<<4
# Per-group color maps: group index → {brightness: velocity}
_LP_CLASSIC_GROUP_COLORS = [
    {0: 0, 4: 29, 8: 46, 15: 63},   # Group 0 — amber (r3+g3)
    {0: 0, 4: 28, 8: 44, 15: 60},   # Group 1 — green (g3)
    {0: 0, 4: 13, 8: 14, 15: 15},   # Group 2 — red   (r3)
    {0: 0, 4: 45, 8: 46, 15: 62},   # Group 3 — yellow-green (r2+g3)
    {0: 0, 4: 17, 8: 18, 15: 19},   # Group 4 — purple approx (r1+g1)
    {0: 0, 4: 29, 8: 30, 15: 31},   # Group 5 — orange (r3+g1)
    {0: 0, 4:  5, 8:  6, 15:  7},   # Group 6 — pink/magenta (r1)
    {0: 0, 4: 41, 8: 42, 15: 43},   # Group 7 — blue approx (r3+g2 mix; no blue LED on Classic)
]

# MK2/X/MK3/Pro use velocity as color palette index
_LP_MK2_GROUP_COLORS = [
    {0: 0, 4: 11, 8: 10, 15:  9},   # Group 0 — amber/orange
    {0: 0, 4: 19, 8: 20, 15: 21},   # Group 1 — green
    {0: 0, 4:  6, 8:  5, 15:  5},   # Group 2 — red
    {0: 0, 4: 35, 8: 36, 15: 37},   # Group 3 — cyan/teal
    {0: 0, 4: 49, 8: 50, 15: 51},   # Group 4 — purple/violet
    {0: 0, 4: 85, 8: 86, 15: 87},   # Group 5 — orange
    {0: 0, 4: 57, 8: 58, 15: 59},   # Group 6 — pink/rose
    {0: 0, 4: 43, 8: 44, 15: 45},   # Group 7 — cobalt blue
]


def detect_launchpad_model(device_name: str) -> LaunchpadModel:
    """Detect Launchpad model from MIDI device name."""
    name = device_name.lower()
    if "pro mk3" in name:
        return LaunchpadModel.PRO_MK3
    if "pro" in name:
        return LaunchpadModel.PRO
    if "mini mk3" in name or "lpminimk3" in name:
        return LaunchpadModel.MINI_MK3
    if "mini mk2" in name:
        return LaunchpadModel.MINI_MK2
    if "mini" in name:
        return LaunchpadModel.MINI_MK1
    if "mk2" in name:
        return LaunchpadModel.MK2
    if "launchpad x" in name or "lpx" in name:
        return LaunchpadModel.X
    if "launchpad s" in name:
        return LaunchpadModel.S
    if "launchpad" in name:
        return LaunchpadModel.MINI_MK1  # fallback for unrecognized
    return LaunchpadModel.GENERIC


def is_launchpad(device_name: str) -> bool:
    """Check if a MIDI device name is a Launchpad."""
    return "launchpad" in device_name.lower() or "lpminimk3" in device_name.lower() or "lpx" in device_name.lower()


def is_grid_controller(device_name: str) -> bool:
    """Check if a MIDI device name looks like a grid controller."""
    name = device_name.lower()
    grid_keywords = ["launchpad", "monome", "grid", "lpminimk3", "lpx", "apc"]
    return any(kw in name for kw in grid_keywords)


class MidiGridAdapter:
    """Translates MIDI note messages from grid controllers into (x, y, state) events.

    Supports:
    - Novation Launchpad Mini/S/MK1 (classic note layout)
    - Novation Launchpad MK2/X/Mini MK3/Pro (programmer mode layout)
    - Generic MIDI grids (configurable note-to-coordinate mapping)
    """

    def __init__(self, rows: int = 8, columns: int = 8):
        self.rows = rows
        self.columns = columns
        self.model = LaunchpadModel.GENERIC
        self.device_name: str = ""
        self._midi_in = None
        self._midi_out = None
        self._closed = False
        self._button_callback: Optional[Callable] = None
        self._control_callback: Optional[Callable] = None
        self._connected = False
        self._in_port: int = -1
        self._out_port: int = -1
        self._note_to_xy: dict[int, tuple[int, int]] = {}
        self._xy_to_note: dict[tuple[int, int], int] = {}
        self._group_color_maps: list[dict[int, int]] = [m.copy() for m in _LP_MK2_GROUP_COLORS]
        self._control_cc_map: dict[int, int] = {}   # CC number → button index
        self._control_cc_out: dict[int, int] = {}   # button index → CC number
        self._control_row_enabled: bool = True       # when False, CC row acts as data y=0
        self._midi_forward: Optional[Callable] = None
        # Per-cell LED state cache: (y, x) → (velocity, color) to skip unchanged sends
        self._led_cache: dict[tuple[int, int], tuple[int, int]] = {}
        # Background MIDI output thread — drains _send_queue so main-thread LED
        # writes never block the audio callback waiting for the Python GIL.
        self._send_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._send_thread: Optional[threading.Thread] = None
        self._send_thread_stop = threading.Event()

    @property
    def connected(self) -> bool:
        return self._connected

    def set_button_callback(self, callback: Callable):
        """Set callback: callback(x, y, state) where state is 1=press, 0=release."""
        self._button_callback = callback

    def set_control_callback(self, callback: Callable):
        """Set callback for control buttons (top row): callback(btn_index, state)."""
        self._control_callback = callback

    # ------------------------------------------------------------------ #
    #  Background send thread                                            #
    # ------------------------------------------------------------------ #

    def _start_send_thread(self):
        """Start the background MIDI output thread."""
        if self._send_thread is not None and self._send_thread.is_alive():
            return
        self._send_thread_stop.clear()
        self._send_thread = threading.Thread(
            target=self._send_worker, daemon=True, name="mlrvp-midi-led"
        )
        self._send_thread.start()

    def _stop_send_thread(self):
        """Signal the send thread to exit and wait for it."""
        self._send_thread_stop.set()
        self._send_queue.put(None)  # sentinel to unblock the worker
        if self._send_thread is not None:
            self._send_thread.join(timeout=1.0)
            self._send_thread = None

    def _send_worker(self):
        """Drain the send queue on a dedicated thread.

        Uses a None sentinel (put by _stop_send_thread) to exit — NOT a
        threading.Event.  The event-based check had a race: if stop was set
        before the worker looped back, it would exit without processing
        queued clear-LED messages (causing LEDs to stay lit on disconnect).
        Sentinel-only termination guarantees all queued messages are
        delivered before the thread exits.
        """
        q = self._send_queue
        while True:
            try:
                msg = q.get()
                if msg is None:  # sentinel — exit cleanly
                    break
                midi_out = self._midi_out
                if midi_out is not None:
                    midi_out.send_message(msg)
            except Exception:
                pass

    def _send(self, msg: list):
        """Enqueue a MIDI message for background delivery."""
        self._send_queue.put(msg)

    def _send_now(self, msg: list):
        """Send a MIDI message directly on the current thread.

        Bypasses the background queue.  Use only when the queue cannot or
        should not be used (e.g. on disconnect, when the worker is being
        shut down — queued messages would never be delivered).
        """
        midi_out = self._midi_out
        if midi_out is None:
            return
        try:
            midi_out.send_message(msg)
        except Exception:
            pass

    def configure_control_row(self, enabled: bool):
        """Enable or disable the CC control row.

        When disabled, CC buttons are routed to ``_button_callback`` as if they
        were grid buttons at ``y=0`` so the app can use them as data rows.
        MIDI mappings for transport paths are never affected here.
        """
        self._control_row_enabled = enabled

    def set_midi_forward(self, callback: Optional[Callable]):
        """Set callback to forward raw MIDI messages to (e.g. MIDI handler for learn)."""
        self._midi_forward = callback

    def configure_for_model(self, model: LaunchpadModel):
        """Set up note mapping for a specific controller model."""
        self.model = model
        if model in (LaunchpadModel.MINI_MK1, LaunchpadModel.S):
            self._note_to_xy = _LP_CLASSIC_NOTE_MAP.copy()
            self._xy_to_note = _LP_CLASSIC_COORD_MAP.copy()
            self._group_color_maps = [m.copy() for m in _LP_CLASSIC_GROUP_COLORS]
            self.rows = 8
            self.columns = 8
            # Classic Launchpads: top row = CC 104-111
            cc_range = range(104, 112)
        elif model in (LaunchpadModel.MK2, LaunchpadModel.X,
                       LaunchpadModel.MINI_MK2, LaunchpadModel.MINI_MK3,
                       LaunchpadModel.PRO, LaunchpadModel.PRO_MK3):
            self._note_to_xy = _LP_MK2_NOTE_MAP.copy()
            self._xy_to_note = _LP_MK2_COORD_MAP.copy()
            self._group_color_maps = [m.copy() for m in _LP_MK2_GROUP_COLORS]
            self.rows = 8
            self.columns = 8
            if model == LaunchpadModel.MK2:
                # MK2 uses CC 104-111 for top row
                cc_range = range(104, 112)
            else:
                # MK3/X/Pro use CC 91-98 in programmer mode
                cc_range = range(91, 99)
        else:
            # Generic: straight mapping, note = row*columns + col
            note_to_xy = {}
            xy_to_note = {}
            for r in range(self.rows):
                for c in range(self.columns):
                    note = r * self.columns + c
                    note_to_xy[note] = (c, r)
                    xy_to_note[(c, r)] = note
            self._note_to_xy = note_to_xy
            self._xy_to_note = xy_to_note
            self._group_color_maps = [m.copy() for m in _LP_MK2_GROUP_COLORS]
            cc_range = range(0)  # no control buttons

        self._control_cc_map = {cc: i for i, cc in enumerate(cc_range)}
        self._control_cc_out = {i: cc for i, cc in enumerate(cc_range)}

    def connect(self, in_port: int, out_port: int = -1):
        """Open MIDI ports and start listening."""
        try:
            import rtmidi
        except ImportError:
            log.warning("python-rtmidi not installed — MIDI grid unavailable")
            return False

        try:
            self._midi_in = rtmidi.MidiIn()
            ports = self._midi_in.get_ports()
            if in_port < 0 or in_port >= len(ports):
                log.error("MIDI grid input port %d out of range", in_port)
                self._midi_in = None
                return False

            device_name = ports[in_port]
            self.device_name = device_name
            self.model = detect_launchpad_model(device_name)
            self.configure_for_model(self.model)

            self._midi_in.open_port(in_port)
            self._midi_in.set_callback(self._on_midi_message)
            self._in_port = in_port
            log.info("MIDI grid input opened: %s (model: %s)", device_name, self.model.value)

            # Open output for LED feedback
            if out_port >= 0:
                self._midi_out = rtmidi.MidiOut()
                out_ports = self._midi_out.get_ports()
                if out_port < len(out_ports):
                    self._midi_out.open_port(out_port)
                    self._out_port = out_port
                    log.info("MIDI grid output opened: %s", out_ports[out_port])

                    # Enter programmer mode for MK2/MK3/X/Pro
                    self._enter_programmer_mode()
                else:
                    self._midi_out = None

            self._connected = True
            self._start_send_thread()
            return True

        except Exception as e:
            log.error("MIDI grid connect error: %s", e)
            self._connected = False
            return False

    def disconnect(self):
        """Close MIDI ports."""
        self._closed = True
        # Clear LEDs synchronously BEFORE killing the send thread.  Routing
        # the clear via the queue is unreliable on shutdown — the worker
        # may exit before flushing.  _clear_all_now bypasses the queue and
        # writes the SysEx directly on this thread, guaranteeing the
        # hardware actually receives the clear before the port closes.
        if self._midi_out is not None:
            try:
                self._clear_all_now()
            except Exception:
                pass
        self._stop_send_thread()
        if self._midi_in is not None:
            try:
                self._midi_in.close_port()
                self._midi_in.delete()
            except Exception:
                pass
            self._midi_in = None
        if self._midi_out is not None:
            try:
                self._midi_out.close_port()
                self._midi_out.delete()
            except Exception:
                pass
            self._midi_out = None
        self._connected = False
        self._in_port = -1
        self._out_port = -1
        self._led_cache.clear()
        log.info("MIDI grid disconnected")

    def _on_midi_message(self, event, data=None):
        """Handle incoming MIDI messages from the grid controller."""
        if self._closed:
            return
        try:
            message, _delta = event
            if len(message) < 3:
                return

            # Forward raw message to MIDI handler (for MIDI learn etc.)
            if self._midi_forward:
                self._midi_forward(message)

            status = message[0] & 0xF0
            note = message[1]
            velocity = message[2]

            # Note On (with velocity > 0) = button press
            # Note Off or Note On with velocity 0 = button release
            if status == 0x90:  # Note On
                state = 1 if velocity > 0 else 0
            elif status == 0x80:  # Note Off
                state = 0
            elif status == 0xB0:  # CC — some Launchpads send CC for top row
                cc = message[1]
                value = message[2]
                btn_idx = self._control_cc_map.get(cc)
                if btn_idx is not None:
                    state = 1 if value > 0 else 0
                    if self._control_row_enabled:
                        if self._control_callback:
                            self._control_callback(btn_idx, state)
                    else:
                        # Control row disabled — route as regular grid button at y=0
                        if self._button_callback:
                            self._button_callback(btn_idx, 0, state)
                return
            else:
                return

            # Map note to grid coordinate
            xy = self._note_to_xy.get(note)
            if xy is not None:
                x, y = xy
                if self._button_callback:
                    self._button_callback(x, y, state)
        except Exception as e:
            import logging
            logging.getLogger("mlrvp").error(f"Grid MIDI callback error: {e}")

    def set_led(self, x: int, y: int, brightness: int, color: int = 0):
        """Set a single LED on the grid controller.

        Args:
            color: Group color index (0-3) for colored LED feedback.
        """
        if self._midi_out is None or not self._connected:
            return
        key = (x, y)
        note = self._xy_to_note.get(key)
        if note is None:
            return
        cmap = self._group_color_maps[color % len(self._group_color_maps)]
        velocity = cmap.get(brightness, 0)
        if velocity > 0:
            self._send([0x90, note, velocity])
        else:
            self._send([0x80, note, 0])

    def set_control_led(self, btn_idx: int, brightness: int, color: int = 0):
        """Set LED on a control button (top row / CC buttons)."""
        if self._midi_out is None or not self._connected:
            return
        cc = self._control_cc_out.get(btn_idx)
        if cc is None:
            return
        cmap = self._group_color_maps[color % len(self._group_color_maps)]
        velocity = cmap.get(brightness, 0)
        self._send([0xB0, cc, velocity])

    def set_row(self, y: int, values: list[int], color: int = 0):
        """Set an entire row of LEDs, skipping cells that haven't changed."""
        if self._midi_out is None or not self._connected:
            return
        cache = self._led_cache
        cmap = self._group_color_maps[color % len(self._group_color_maps)]
        cols = self.columns
        xy_to_note = self._xy_to_note
        for x, val in enumerate(values):
            if x >= cols:
                break
            key = (y, x)
            entry = (val, color)
            if cache.get(key) == entry:
                continue
            cache[key] = entry
            note = xy_to_note.get((x, y))
            if note is None:
                continue
            velocity = cmap.get(val, 0)
            if velocity > 0:
                self._send([0x90, note, velocity])
            else:
                self._send([0x80, note, 0])

    def set_row_colored(self, y: int, values: list[int], colors: list[int]):
        """Set a row where each cell may have a different color."""
        if self._midi_out is None or not self._connected:
            return
        cache = self._led_cache
        cols = self.columns
        xy_to_note = self._xy_to_note
        group_color_maps = self._group_color_maps
        for x, (val, color) in enumerate(zip(values, colors)):
            if x >= cols:
                break
            key = (y, x)
            entry = (val, color)
            if cache.get(key) == entry:
                continue
            cache[key] = entry
            note = xy_to_note.get((x, y))
            if note is None:
                continue
            cmap = group_color_maps[color % len(group_color_maps)]
            velocity = cmap.get(val, 0)
            if velocity > 0:
                self._send([0x90, note, velocity])
            else:
                self._send([0x80, note, 0])

    def _build_clear_sysex(self) -> Optional[list]:
        """Build a single SysEx that turns every grid cell off.

        Returns None if the model doesn't support batch LED SysEx — caller
        must fall back to per-cell note-offs.
        """
        if self.model in (LaunchpadModel.MINI_MK3, LaunchpadModel.X,
                          LaunchpadModel.PRO_MK3, LaunchpadModel.PRO):
            # 3-byte format: <Type=0> <LED> <Color=0>
            specs: list[int] = []
            for note in self._xy_to_note.values():
                specs.extend([0, note, 0])
            if not specs:
                return None
            if self.model is LaunchpadModel.MINI_MK3:
                header = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0D, 0x03]
            elif self.model is LaunchpadModel.X:
                header = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0C, 0x03]
            elif self.model is LaunchpadModel.PRO_MK3:
                header = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0E, 0x03]
            else:
                header = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x10, 0x03]
            return header + specs + [0xF7]
        elif self.model in (LaunchpadModel.MK2, LaunchpadModel.MINI_MK2):
            # 2-byte format: <LED> <Color=0>
            specs = []
            for note in self._xy_to_note.values():
                specs.extend([note, 0])
            if not specs:
                return None
            return [0xF0, 0x00, 0x20, 0x29, 0x02, 0x18, 0x0A] + specs + [0xF7]
        return None

    def clear_all(self):
        """Turn off all LEDs.

        For Launchpad models that support batch LED SysEx we send ONE
        SysEx setting every grid cell to colour 0, then a small CC burst
        for the control row LEDs.  This is the reliable "everything off"
        path — does not depend on layout-mode side effects.

        Classic / generic models fall back to individual note-offs for
        currently-lit cells in the cache.
        """
        if self._midi_out is None:
            return

        sysex = self._build_clear_sysex()
        if sysex is not None:
            self._send(sysex)
            for cc in self._control_cc_out.values():
                self._send([0xB0, cc, 0])
        else:
            # Classic / generic — individual note-offs for lit cells only
            cache = self._led_cache
            xy_to_note = self._xy_to_note
            for (y, x), (val, _color) in list(cache.items()):
                if val > 0:
                    note = xy_to_note.get((x, y))
                    if note is not None:
                        self._send([0x80, note, 0])
            for cc in list(self._control_cc_out.values()):
                self._send([0xB0, cc, 0])

        self._led_cache.clear()

    def _clear_all_now(self):
        """Synchronously clear every LED — for use during disconnect.

        Bypasses the background send queue (which is being torn down).
        Sends the SysEx clear directly on the calling thread so the
        hardware actually receives it before the port closes.
        """
        if self._midi_out is None:
            return
        sysex = self._build_clear_sysex()
        if sysex is not None:
            self._send_now(sysex)
            for cc in self._control_cc_out.values():
                self._send_now([0xB0, cc, 0])
        else:
            # Classic / generic — note-off every grid note + every CC
            for note in self._xy_to_note.values():
                self._send_now([0x80, note, 0])
            for cc in self._control_cc_out.values():
                self._send_now([0xB0, cc, 0])
        self._led_cache.clear()

    def set_leds_bulk(self, cells: list):
        """Set every grid LED in a single SysEx burst.

        ``cells`` is a list of ``(x, y, brightness, color_group)`` tuples
        describing the cells that should be *lit*.  All other grid cells
        are explicitly set to 0 in the same SysEx, so a separate clear
        call is **not** required.  This collapses a typical mix-mode
        repaint to a single SysEx (~200 bytes, 1 send_message call).

        For Classic / generic models we fall back to per-cell ``set_led()``.
        """
        if self._midi_out is None or not self._connected:
            return

        xy_to_note = self._xy_to_note
        group_color_maps = self._group_color_maps
        cache = self._led_cache

        # Build a (note → velocity, color_group) map of every requested lit cell
        lit_map: dict[int, tuple[int, int]] = {}
        for x, y, brightness, color_group in cells:
            note = xy_to_note.get((x, y))
            if note is None:
                continue
            cmap = group_color_maps[color_group % len(group_color_maps)]
            velocity = cmap.get(brightness, 0)
            lit_map[note] = (velocity, color_group)
            cache[(y, x)] = (brightness, color_group)

        # Mark every other grid cell as cleared in cache
        for (cx, cy), note in xy_to_note.items():
            if note not in lit_map:
                cache[(cy, cx)] = (0, 0)

        if self.model in (LaunchpadModel.MINI_MK3, LaunchpadModel.X,
                          LaunchpadModel.PRO_MK3, LaunchpadModel.PRO):
            # 3-byte format
            specs: list[int] = []
            for note in xy_to_note.values():
                vel = lit_map.get(note, (0, 0))[0]
                specs.extend([0, note, vel])
            if not specs:
                return
            if self.model is LaunchpadModel.MINI_MK3:
                header = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0D, 0x03]
            elif self.model is LaunchpadModel.X:
                header = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0C, 0x03]
            elif self.model is LaunchpadModel.PRO_MK3:
                header = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0E, 0x03]
            else:
                header = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x10, 0x03]
            self._send(header + specs + [0xF7])
        elif self.model in (LaunchpadModel.MK2, LaunchpadModel.MINI_MK2):
            # 2-byte format: <LED> <Color>
            specs = []
            for note in xy_to_note.values():
                vel = lit_map.get(note, (0, 0))[0]
                specs.extend([note, vel])
            if not specs:
                return
            self._send([0xF0, 0x00, 0x20, 0x29, 0x02, 0x18, 0x0A] + specs + [0xF7])
        else:
            # Classic / generic — individual set_led calls (lit + explicit off)
            for (cx, cy), note in xy_to_note.items():
                if note in lit_map:
                    vel = lit_map[note][0]
                    if vel > 0:
                        self._send([0x90, note, vel])
                    else:
                        self._send([0x80, note, 0])
                else:
                    self._send([0x80, note, 0])

    def _clear_all_leds(self):
        """Send note-off for all grid positions and clear control buttons."""
        if self._midi_out is None:
            return
        for note in list(self._xy_to_note.values()):
            self._send([0x80, note, 0])
        for cc in list(self._control_cc_out.values()):
            self._send([0xB0, cc, 0])

    def _enter_programmer_mode(self):
        """Enter programmer/session mode for newer Launchpads."""
        if self._midi_out is None:
            return
        try:
            if self.model in (LaunchpadModel.MINI_MK3,):
                self._send([0xF0, 0x00, 0x20, 0x29, 0x02, 0x0D, 0x0E, 0x01, 0xF7])
            elif self.model in (LaunchpadModel.X,):
                self._send([0xF0, 0x00, 0x20, 0x29, 0x02, 0x0C, 0x0E, 0x01, 0xF7])
            elif self.model in (LaunchpadModel.MK2,):
                self._send([0xF0, 0x00, 0x20, 0x29, 0x02, 0x18, 0x22, 0x00, 0xF7])
            elif self.model in (LaunchpadModel.PRO_MK3,):
                self._send([0xF0, 0x00, 0x20, 0x29, 0x02, 0x0E, 0x0E, 0x01, 0xF7])
            elif self.model in (LaunchpadModel.PRO,):
                self._send([0xF0, 0x00, 0x20, 0x29, 0x02, 0x10, 0x2C, 0x03, 0xF7])
        except Exception as e:
            log.warning("Failed to enter programmer mode: %s", e)


def scan_midi_grid_devices() -> list[dict]:
    """Scan for MIDI devices that look like grid controllers.

    Returns list of dicts with keys: name, in_port, out_port, model
    """
    try:
        import rtmidi
    except ImportError:
        return []

    devices = []
    midi_in = rtmidi.MidiIn()
    midi_out = rtmidi.MidiOut()
    try:
        in_ports = midi_in.get_ports()
        out_ports = midi_out.get_ports()

        for i, name in enumerate(in_ports):
            if not is_grid_controller(name):
                continue
            # Find matching output port
            out_idx = -1
            for j, out_name in enumerate(out_ports):
                if _ports_match(name, out_name):
                    out_idx = j
                    break

            model = detect_launchpad_model(name)
            devices.append({
                "name": name,
                "in_port": i,
                "out_port": out_idx,
                "model": model.value,
            })
    finally:
        midi_in.delete()
        midi_out.delete()
    return devices


def _ports_match(in_name: str, out_name: str) -> bool:
    """Check if an input and output port likely belong to the same device."""
    # Strip common suffixes and compare
    in_clean = in_name.lower().replace("midi", "").replace("in", "").replace("input", "").strip()
    out_clean = out_name.lower().replace("midi", "").replace("out", "").replace("output", "").strip()
    if in_clean == out_clean:
        return True
    # Require meaningful word overlap (ignore single digits/numbers)
    in_words = {w for w in in_clean.split() if len(w) > 1}
    out_words = {w for w in out_clean.split() if len(w) > 1}
    if not in_words or not out_words:
        return False
    common = in_words & out_words
    return len(common) >= 1 and len(common) / max(len(in_words), len(out_words)) > 0.4
