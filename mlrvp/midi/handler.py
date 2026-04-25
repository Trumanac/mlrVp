"""MIDI handler for mlrVp.

Provides MIDI learn and parameter mapping, matching the mlrV2 paramap system.
Uses Qt signals for thread-safe communication from the MIDI callback thread.
"""

import logging
import threading
from typing import Optional, Callable
from dataclasses import dataclass, field, fields

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("mlrvp")


@dataclass
class MidiMapping:
    """A single MIDI CC mapping to a parameter."""
    channel: int = 0
    cc: int = 0
    min_val: float = 0.0
    max_val: float = 1.0
    param_path: str = ""
    toggle: bool = False  # True = toggle on note-on, False = momentary (held)


class MidiHandler(QObject):
    """Handles MIDI input/output and parameter mapping.

    The learn_completed signal is emitted on the main thread (via Qt's
    queued connection) so GUI updates are safe.
    """

    learn_completed = Signal(str, int, int)  # param_path, channel, cc
    _cc_dispatch = Signal(str, float)  # param_path, value — internal signal for thread-safe CC dispatch

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._closed = False
        self._midi_inputs: dict[int, object] = {}  # port -> rtmidi.MidiIn
        self._midi_out = None
        self._mappings: dict[str, MidiMapping] = {}  # param_path -> mapping
        self._cc_to_param: dict[tuple[int, int], str] = {}  # (channel, cc) -> param_path
        self._param_callbacks: dict[str, Callable] = {}
        self._learning: bool = False
        self._learn_param: str = ""
        self._toggle_state: dict[str, bool] = {}  # param_path -> on/off for toggle mode
        self._available_inputs: list = []
        self._available_outputs: list = []
        self._cc_dispatch.connect(self._on_cc_dispatch)

    def scan_devices(self):
        """Scan for available MIDI devices."""
        try:
            import rtmidi
            midi_in = rtmidi.MidiIn()
            midi_out = rtmidi.MidiOut()
            self._available_inputs = midi_in.get_ports()
            self._available_outputs = midi_out.get_ports()
            midi_in.delete()
            midi_out.delete()
        except ImportError:
            self._available_inputs = []
            self._available_outputs = []

    @property
    def available_inputs(self) -> list[str]:
        return self._available_inputs

    @property
    def available_outputs(self) -> list[str]:
        return self._available_outputs

    def open_input(self, port: int):
        """Open a MIDI input port. Multiple ports can be open simultaneously."""
        if port in self._midi_inputs:
            return  # Already open
        try:
            import rtmidi
            midi_in = rtmidi.MidiIn()
            midi_in.open_port(port)
            midi_in.set_callback(self._midi_callback)
            self._midi_inputs[port] = midi_in
        except Exception as e:
            print(f"MIDI input error: {e}")

    def close_input(self, port: int):
        """Close a specific MIDI input port."""
        if port in self._midi_inputs:
            try:
                self._midi_inputs[port].close_port()
                self._midi_inputs[port].delete()
            except Exception:
                pass
            del self._midi_inputs[port]

    def open_output(self, port: int):
        """Open a MIDI output port."""
        try:
            import rtmidi
            if self._midi_out is not None:
                self._midi_out.close_port()
                self._midi_out.delete()
            self._midi_out = rtmidi.MidiOut()
            self._midi_out.open_port(port)
        except Exception as e:
            print(f"MIDI output error: {e}")

    def close(self):
        self._closed = True
        for port, midi_in in list(self._midi_inputs.items()):
            try:
                midi_in.close_port()
                midi_in.delete()
            except Exception:
                pass
        self._midi_inputs.clear()
        if self._midi_out is not None:
            try:
                self._midi_out.close_port()
                self._midi_out.delete()
            except Exception:
                pass
            self._midi_out = None

    def add_mapping(self, param_path: str, mapping: MidiMapping):
        """Add a MIDI CC mapping for a parameter."""
        mapping.param_path = param_path
        with self._lock:
            self._mappings[param_path] = mapping
            self._cc_to_param[(mapping.channel, mapping.cc)] = param_path

    def remove_mapping(self, param_path: str):
        """Remove a MIDI mapping."""
        with self._lock:
            if param_path in self._mappings:
                m = self._mappings[param_path]
                key = (m.channel, m.cc)
                if key in self._cc_to_param:
                    del self._cc_to_param[key]
                del self._mappings[param_path]
                self._toggle_state.pop(param_path, None)

    def set_param_callback(self, param_path: str, callback: Callable):
        """Set callback for when a mapped parameter changes via MIDI."""
        with self._lock:
            self._param_callbacks[param_path] = callback

    def get_param_callback(self, param_path: str) -> Optional[Callable]:
        """Return the current callback for param_path, or None."""
        with self._lock:
            return self._param_callbacks.get(param_path)

    def start_learn(self, param_path: str, callback: Optional[Callable] = None):
        """Start MIDI learn mode for a parameter.

        The optional callback is kept for backward compat but the preferred
        approach is to connect to the ``learn_completed`` signal which is
        emitted on the Qt main thread.
        """
        with self._lock:
            self._learning = True
            self._learn_param = param_path
        if callback:
            # Connect one-shot via signal so it fires on gui thread
            def _once(pp, ch, cc, cb=callback):
                cb(pp, ch, cc)
                try:
                    self.learn_completed.disconnect(_once)
                except RuntimeError:
                    pass
            self.learn_completed.connect(_once)

    def stop_learn(self):
        with self._lock:
            self._learning = False
            self._learn_param = ""

    def inject_message(self, message: list):
        """Process a raw MIDI message (forwarded from another source like a grid adapter)."""
        self._process_message(message)

    def _midi_callback(self, event, data=None):
        """Handle incoming MIDI messages from rtmidi (runs on background thread)."""
        if self._closed:
            return
        try:
            message, delta_time = event
            self._process_message(message)
        except Exception as e:
            log.error(f"MIDI callback error: {e}")

    def _process_message(self, message):
        """Core message processing — handles learn and normal CC mapping."""
        if len(message) < 3:
            return

        status = message[0]
        msg_type = status & 0xF0
        channel = status & 0x0F
        data1 = message[1]
        data2 = message[2]

        # ------ MIDI learn mode ------
        # Atomically check-and-claim the learn slot so two near-simultaneous
        # CCs (or a CC racing with stop_learn) cannot both finish learn.
        with self._lock:
            in_learn = self._learning
        if in_learn:
            # Accept CC, Note On (with velocity > 0)
            if msg_type == 0xB0:
                # CC message
                self._finish_learn(channel, data1)
                return
            elif msg_type == 0x90 and data2 > 0:
                # Note On — use the note number as the "cc" identifier
                # Store with channel offset so we can distinguish from CC
                self._finish_learn(channel, data1)
                return
            # Ignore Note Off and other messages during learn
            return

        # ------ Normal operation ------
        with self._lock:
            key = (channel, data1)
            if key not in self._cc_to_param:
                return
            param_path = self._cc_to_param[key]
            mapping = self._mappings.get(param_path)
            if mapping is None:
                return
            # Snapshot mapping values under lock to avoid races
            m_toggle = mapping.toggle
            m_min = mapping.min_val
            m_max = mapping.max_val

        scaled = None
        if msg_type == 0xB0:
            value = data2
            if m_toggle:
                if value > 0:
                    with self._lock:
                        cur = self._toggle_state.get(param_path, False)
                        nxt = not cur
                        self._toggle_state[param_path] = nxt
                    scaled = m_max if nxt else m_min
            else:
                normalized = value / 127.0
                scaled = m_min + normalized * (m_max - m_min)

        elif msg_type == 0x90:
            velocity = data2
            if m_toggle:
                if velocity > 0:
                    with self._lock:
                        cur = self._toggle_state.get(param_path, False)
                        nxt = not cur
                        self._toggle_state[param_path] = nxt
                    scaled = m_max if nxt else m_min
            else:
                normalized = velocity / 127.0
                scaled = m_min + normalized * (m_max - m_min)

        elif msg_type == 0x80:
            if not m_toggle:
                scaled = m_min

        # Dispatch callback on Qt main thread via signal (thread-safe)
        if scaled is not None:
            self._cc_dispatch.emit(param_path, scaled)

    def _on_cc_dispatch(self, param_path: str, value: float):
        """Handle CC dispatch on the Qt main thread (connected via signal)."""
        with self._lock:
            cb = self._param_callbacks.get(param_path)
        if cb is not None:
            cb(value)

    def _finish_learn(self, channel: int, cc: int):
        """Complete a MIDI learn — creates mapping and emits signal on Qt thread."""
        # Atomically capture-and-clear the learn slot so a second near-
        # simultaneous message cannot also fire _finish_learn.
        with self._lock:
            if not self._learning:
                return
            param_path = self._learn_param
            self._learning = False
            self._learn_param = ""
        if not param_path:
            return
        mapping = MidiMapping(
            channel=channel,
            cc=cc,
            param_path=param_path,
        )
        self.add_mapping(param_path, mapping)
        # Emit signal — Qt marshals this to the main thread
        self.learn_completed.emit(param_path, channel, cc)

    def send_cc(self, channel: int, cc: int, value: int):
        """Send a MIDI CC message."""
        if self._midi_out is not None:
            msg = [0xB0 | (channel & 0x0F), cc & 0x7F, value & 0x7F]
            self._midi_out.send_message(msg)

    def get_mappings(self) -> dict[str, MidiMapping]:
        with self._lock:
            return self._mappings.copy()

    def get_mappings_serializable(self) -> list[dict]:
        """Get mappings as serializable dicts for preset save."""
        with self._lock:
            result = []
            for path, m in self._mappings.items():
                result.append({
                    "param_path": m.param_path,
                    "channel": m.channel,
                    "cc": m.cc,
                    "min_val": m.min_val,
                    "max_val": m.max_val,
                    "toggle": m.toggle,
                })
            return result

    def load_mappings(self, mappings_data: list[dict]):
        """Load mappings from serialized data."""
        new_mappings = {}
        new_cc_to_param = {}
        _fields = {f.name for f in fields(MidiMapping)}
        for m_data in mappings_data:
            filtered = {k: v for k, v in m_data.items() if k in _fields}
            mapping = MidiMapping(**filtered)
            mapping.param_path = m_data.get('param_path', mapping.param_path)
            new_mappings[mapping.param_path] = mapping
            new_cc_to_param[(mapping.channel, mapping.cc)] = mapping.param_path
        with self._lock:
            self._mappings = new_mappings
            self._cc_to_param = new_cc_to_param

    def update_mapping_range(self, param_path: str, min_val: float, max_val: float):
        """Update the min/max range for an existing mapping."""
        with self._lock:
            if param_path in self._mappings:
                self._mappings[param_path].min_val = min_val
                self._mappings[param_path].max_val = max_val

    def update_mapping_toggle(self, param_path: str, toggle: bool):
        """Update the toggle mode for an existing mapping."""
        with self._lock:
            if param_path in self._mappings:
                self._mappings[param_path].toggle = toggle
