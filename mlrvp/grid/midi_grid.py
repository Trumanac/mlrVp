"""MIDI grid controller adapter for mlrVp.

Supports Novation Launchpad series (Mini, S, MK2, MK3, X, Pro)
and generic MIDI grid controllers that send note-on/off for buttons.
"""

import logging
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
]

# MK2/X/MK3/Pro use velocity as color palette index
_LP_MK2_GROUP_COLORS = [
    {0: 0, 4: 11, 8: 10, 15: 9},    # Group 0 — amber/orange
    {0: 0, 4: 19, 8: 20, 15: 21},   # Group 1 — green
    {0: 0, 4: 6,  8: 5,  15: 5},    # Group 2 — red
    {0: 0, 4: 35, 8: 36, 15: 37},   # Group 3 — cyan/teal
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
        self._midi_forward: Optional[Callable] = None

    @property
    def connected(self) -> bool:
        return self._connected

    def set_button_callback(self, callback: Callable):
        """Set callback: callback(x, y, state) where state is 1=press, 0=release."""
        self._button_callback = callback

    def set_control_callback(self, callback: Callable):
        """Set callback for control buttons (top row): callback(btn_index, state)."""
        self._control_callback = callback

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
            return True

        except Exception as e:
            log.error("MIDI grid connect error: %s", e)
            self._connected = False
            return False

    def disconnect(self):
        """Close MIDI ports."""
        self._closed = True
        if self._midi_in is not None:
            try:
                self._midi_in.close_port()
            except Exception:
                pass
            self._midi_in = None
        if self._midi_out is not None:
            try:
                self._clear_all_leds()
            except Exception:
                pass
            try:
                self._midi_out.close_port()
            except Exception:
                pass
            self._midi_out = None
        self._connected = False
        self._in_port = -1
        self._out_port = -1
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
                    if self._control_callback:
                        self._control_callback(btn_idx, state)
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

        try:
            if velocity > 0:
                self._midi_out.send_message([0x90, note, velocity])
            else:
                self._midi_out.send_message([0x80, note, 0])
        except Exception:
            pass

    def set_control_led(self, btn_idx: int, brightness: int, color: int = 0):
        """Set LED on a control button (top row / CC buttons)."""
        if self._midi_out is None or not self._connected:
            return
        cc = self._control_cc_out.get(btn_idx)
        if cc is None:
            return
        cmap = self._group_color_maps[color % len(self._group_color_maps)]
        velocity = cmap.get(brightness, 0)
        try:
            self._midi_out.send_message([0xB0, cc, velocity])
        except Exception:
            pass

    def set_row(self, y: int, values: list[int], color: int = 0):
        """Set an entire row of LEDs."""
        for x, val in enumerate(values):
            if x < self.columns:
                self.set_led(x, y, val, color)

    def clear_all(self):
        """Turn off all LEDs."""
        self._clear_all_leds()

    def _clear_all_leds(self):
        """Send note-off for all grid positions and clear control buttons."""
        if self._midi_out is None:
            return
        for note in list(self._xy_to_note.values()):
            try:
                self._midi_out.send_message([0x80, note, 0])
            except Exception:
                pass
        for cc in list(self._control_cc_out.values()):
            try:
                self._midi_out.send_message([0xB0, cc, 0])
            except Exception:
                pass

    def _enter_programmer_mode(self):
        """Enter programmer/session mode for newer Launchpads.

        This ensures the grid sends standard note messages
        rather than the default 'live' mode behavior.
        """
        if self._midi_out is None:
            return

        try:
            if self.model in (LaunchpadModel.MINI_MK3,):
                # Mini MK3: SysEx to enter programmer mode
                # F0 00 20 29 02 0D 0E 01 F7
                self._midi_out.send_message(
                    [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0D, 0x0E, 0x01, 0xF7]
                )
            elif self.model in (LaunchpadModel.X,):
                # Launchpad X: SysEx to enter programmer mode
                # F0 00 20 29 02 0C 0E 01 F7
                self._midi_out.send_message(
                    [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0C, 0x0E, 0x01, 0xF7]
                )
            elif self.model in (LaunchpadModel.MK2,):
                # MK2: SysEx for session layout
                # F0 00 20 29 02 18 22 00 F7
                self._midi_out.send_message(
                    [0xF0, 0x00, 0x20, 0x29, 0x02, 0x18, 0x22, 0x00, 0xF7]
                )
            elif self.model in (LaunchpadModel.PRO_MK3,):
                # Pro MK3: enter programmer mode
                # F0 00 20 29 02 0E 0E 01 F7
                self._midi_out.send_message(
                    [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0E, 0x0E, 0x01, 0xF7]
                )
            elif self.model in (LaunchpadModel.PRO,):
                # Pro: enter programmer mode
                # F0 00 20 29 02 10 2C 03 F7
                self._midi_out.send_message(
                    [0xF0, 0x00, 0x20, 0x29, 0x02, 0x10, 0x2C, 0x03, 0xF7]
                )
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
