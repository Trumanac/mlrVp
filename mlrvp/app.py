"""Application orchestrator for mlrVp.

Wires together all subsystems: audio engine, grid controller,
transport clock, MIDI, OSC, preset manager, and GUI.
"""

import sys
import logging
import time
import threading
from typing import Optional

from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QtMsgType, qInstallMessageHandler, QObject, Signal, Qt
from PySide6.QtGui import QIcon

from mlrvp.config import AppConfig, PlaybackMode, MAX_ROWS, NUM_GROUPS, NUM_PATTERN_RECORDERS, LED_OFF, LED_DIM, LED_BRIGHT, PITCH_SCALE_MAPS, PITCH_SCALE_NAMES
from mlrvp.audio.engine import AudioEngine
from mlrvp.audio.sample import load_sample, get_waveform_overview
from mlrvp.transport.clock import MasterClock
from mlrvp.transport.pattern import PatternRecorder
from mlrvp.grid.controller import GridController
from mlrvp.midi.handler import MidiHandler, MidiMapping
from mlrvp.osc.handler import OscHandler
from mlrvp.preset.manager import PresetManager
from mlrvp.gui.main_window import MainWindow
from mlrvp.gui.midi_map_overlay import MidiMapManager
from mlrvp.gui.widgets.fader import ControlDisplaySettings

log = logging.getLogger("mlrvp")


class _TriggerScheduler(QObject):
    """Marshals grid triggers from any thread to the main Qt thread."""
    _schedule = Signal(int, int, int, int)  # row, column, columns, delay_ms

    def __init__(self, execute_fn):
        super().__init__()
        self._execute_fn = execute_fn
        self._schedule.connect(self._do_schedule, Qt.ConnectionType.QueuedConnection)

    def schedule(self, row, column, columns, delay_ms):
        self._schedule.emit(row, column, columns, delay_ms)

    def _do_schedule(self, row, column, columns, delay_ms):
        if delay_ms <= 0:
            self._execute_fn(row, column, columns)
        else:
            QTimer.singleShot(delay_ms,
                lambda r=row, c=column, cols=columns: self._execute_fn(r, c, cols))


class AppController:
    """Central orchestrator that connects all subsystems."""

    def __init__(self):
        self.config = AppConfig()

        # Pre-warm librosa in a background thread so the first pitch-shift
        # call doesn't stall on a cold import (librosa → numba JIT is slow).
        threading.Thread(target=self._preload_librosa, daemon=True).start()

        # Subsystems
        self.engine: Optional[AudioEngine] = None
        self.clock: Optional[MasterClock] = None
        self.grid = GridController()
        self.midi = MidiHandler()
        self.osc: Optional[OscHandler] = None
        self.presets = PresetManager()
        self.patterns: list[PatternRecorder] = [
            PatternRecorder(i, length * 4)  # convert bars to beats
            for i, length in enumerate([4, 8, 16, 32])
        ]

        # GUI
        self.window: Optional[MainWindow] = None

        # Update timer
        self._ui_timer: Optional[QTimer] = None

        # Cached data from audio thread
        self._latest_levels: dict = {}

        # Thread-safe trigger scheduler for quantized grid events
        self._trigger_scheduler = _TriggerScheduler(self._execute_grid_trigger)

        # Tilt axis → target param_path mapping
        self._tilt_mappings: dict[str, str] = {}  # e.g. "tilt.0.x" → "filter_a.lp_freq"

        # Track per-row playing state for LED cleanup
        self._row_was_playing: list[bool] = [False] * MAX_ROWS

        # Control-row pattern buttons: press timestamp for hold-to-clear.
        self._pattern_hold_started: dict[int, float] = {}
        self._pattern_clear_hold_seconds = 3.0

    # ------------------------------------------------------------------ #
    #  Initialization                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _preload_librosa():
        """Import librosa (and its numba JIT backend) so the first
        pitch-shift request doesn't block on a cold import."""
        try:
            import librosa  # noqa: F401
            import librosa.effects  # noqa: F401
            # Run a tiny no-op pitch shift to trigger numba JIT compilation
            import numpy as np
            _dummy = np.zeros(4096, dtype=np.float32)
            librosa.effects.pitch_shift(_dummy, sr=44100, n_steps=1)
        except Exception:
            pass  # non-critical, will just be slow on first use

    def init_audio(self):
        """Initialize the audio engine."""
        self.engine = AudioEngine(self.config)
        self.engine.set_meter_callback(self._on_meter_levels)
        log.info("Audio engine initialized")

    def init_transport(self):
        """Initialize the master clock."""
        self.clock = MasterClock(self.config.tempo, self.config.quantize)
        self.clock.on_beat(self._on_beat)
        self.clock.on_bar(self._on_bar)
        self.clock.on_tick(self._on_tick)
        for pr in self.patterns:
            pr.set_trigger_callback(self._on_pattern_trigger)
            pr.set_state_change_callback(self._on_pattern_auto_state)
        log.info("Transport initialized")

    def init_grid(self):
        """Initialize grid controller and callbacks."""
        self.grid.set_button_callback(self._on_grid_button)
        self.grid.set_control_button_callback(self._on_control_button)
        self.grid.set_led_update_callback(self._on_led_update)
        self.grid.set_tilt_callback(self._on_tilt_data)
        self.grid.enable_tilt(True)
        log.info("Grid controller initialized")

    def init_midi(self):
        """Scan and initialize MIDI."""
        self.midi.scan_devices()
        log.info(
            "MIDI: %d inputs, %d outputs",
            len(self.midi.available_inputs), len(self.midi.available_outputs)
        )

    def init_osc(self):
        """Initialize OSC handler (only if enabled in config)."""
        if not self.config.osc_enabled:
            log.info("OSC disabled in config — skipping init")
            return
        self.osc = OscHandler(
            recv_port=self.config.osc_recv_port,
            send_port=self.config.osc_send_port,
            send_host=self.config.osc_host,
        )
        self._register_osc_handlers()
        log.info("OSC handler initialized")

    def _register_osc_handlers(self):
        """Register OSC address handlers for mixer and effects control."""
        osc = self.osc
        if not osc:
            return

        # Group mixer: /mlrvp/group/<1-4>/volume|send|mute|fx_route  (float 0-1 or int)
        for g in range(4):
            grp = g  # capture
            osc.add_handler(f"/mlrvp/group/{g+1}/volume",
                            lambda addr, v, _g=grp: self._gui_group_volume(_g, float(v)))
            osc.add_handler(f"/mlrvp/group/{g+1}/send",
                            lambda addr, v, _g=grp: self._gui_group_send(_g, float(v)))
            osc.add_handler(f"/mlrvp/group/{g+1}/mute",
                            lambda addr, v, _g=grp: self._gui_group_mute(_g, int(v) > 0))
            osc.add_handler(f"/mlrvp/group/{g+1}/fx_route",
                            lambda addr, v, _g=grp: self._gui_group_fx_route(_g, int(v)))
            osc.add_handler(f"/mlrvp/group/{g+1}/stop",
                            lambda addr, *a, _g=grp: self._gui_group_stop(_g))

        # AUX: /mlrvp/aux/volume|mute|fx_route|send
        osc.add_handler("/mlrvp/aux/volume",
                        lambda addr, v: self._gui_aux_volume(float(v)))
        osc.add_handler("/mlrvp/aux/mute",
                        lambda addr, v: self._gui_aux_mute(int(v) > 0))
        osc.add_handler("/mlrvp/aux/fx_route",
                        lambda addr, v: self._gui_aux_fx_route(int(v)))
        osc.add_handler("/mlrvp/aux/send",
                        lambda addr, v: self._gui_aux_send(float(v)))

        # Master: /mlrvp/master/volume
        osc.add_handler("/mlrvp/master/volume",
                        lambda addr, v: self._gui_master_volume(float(v)))

        # Transport: /mlrvp/transport/tempo|play|stop
        osc.add_handler("/mlrvp/transport/tempo",
                        lambda addr, v: self._gui_tempo_changed(float(v)))

        # Effects: /mlrvp/delay/<param>  /mlrvp/filter/<param>  /mlrvp/mod/<param>
        osc.add_handler("/mlrvp/delay/time",
                        lambda addr, v: self._gui_delay_param("sync_time", float(v)))
        osc.add_handler("/mlrvp/delay/feedback",
                        lambda addr, v: self._gui_delay_param("feedback", float(v)))
        osc.add_handler("/mlrvp/delay/volume",
                        lambda addr, v: self._gui_delay_param("volume", float(v)))
        osc.add_handler("/mlrvp/filter_a/cutoff",
                        lambda addr, v: self._gui_filter_a_param("lp_freq", float(v)))
        osc.add_handler("/mlrvp/filter_a/resonance",
                        lambda addr, v: self._gui_filter_a_param("lp_q", float(v)))
        osc.add_handler("/mlrvp/filter_b/cutoff",
                        lambda addr, v: self._gui_filter_b_param("cutoff", float(v)))
        osc.add_handler("/mlrvp/filter_b/resonance",
                        lambda addr, v: self._gui_filter_b_param("resonance", float(v)))
        osc.add_handler("/mlrvp/mod/rate",
                        lambda addr, v: self._gui_mod_param("rate", float(v)))
        osc.add_handler("/mlrvp/mod/depth",
                        lambda addr, v: self._gui_mod_param("depth", float(v)))
        osc.add_handler("/mlrvp/mod/type",
                        lambda addr, v: self._gui_mod_param("mod_type", str(v)))

        # Per-effect routing: /mlrvp/delay/route  /mlrvp/mod/route
        osc.add_handler("/mlrvp/delay/route",
                        lambda addr, v: self._gui_effect_route("delay", int(v)))
        osc.add_handler("/mlrvp/mod/route",
                        lambda addr, v: self._gui_effect_route("mod", int(v)))

    def init_gui(self, app: QApplication):
        """Create the main window and wire GUI signals."""
        self.window = MainWindow()
        self._midi_map = MidiMapManager(self.window)
        self._connect_gui_signals()
        self._populate_gui_devices()
        self._register_mappable_controls()
        self._populate_tilt_targets()
        # Sync initial grid row colors from config group assignments
        for i, ch in enumerate(self.config.channels[:self.config.row_count]):
            self.window.virtual_grid.set_row_color(i, ch.group)
        # Populate setlist/preset combos
        self._refresh_setlist_ui()
        self.window.show()
        self.window.set_status("mlrVp — Ready")
        log.info("GUI initialized")

    # ------------------------------------------------------------------ #
    #  Startup / Shutdown                                                 #
    # ------------------------------------------------------------------ #

    def start(self):
        """Start all subsystems."""
        if self.engine:
            self.engine.start()
        if self.clock:
            self.clock.start()
        if self.osc:
            try:
                self.osc.start()
            except Exception as e:
                log.warning("OSC start failed: %s", e)

        # Sync DAC button and channel overlay to reflect audio running state
        if self.window and self.engine:
            self.window.mixer_view.set_audio_enabled(self.engine._running)
            self.window.channel_view.set_audio_enabled(self.engine._running)

        # UI update timer (~30fps)
        self._ui_timer = QTimer()
        self._ui_timer.timeout.connect(self._update_ui)
        self._ui_timer.start(33)

        log.info("All subsystems started")

    def stop(self):
        """Stop all subsystems cleanly."""
        if self._ui_timer:
            self._ui_timer.stop()
        if self.clock:
            self.clock.stop()
        if self.engine:
            self.engine.stop()
        if self.osc:
            self.osc.stop()
        self.grid.disconnect()
        log.info("All subsystems stopped")

    # ------------------------------------------------------------------ #
    #  Group-exclusive playback                                           #
    # ------------------------------------------------------------------ #

    def _stop_group_siblings(self, row: int, columns: int):
        """Stop all other playing channels that share the same group.

        Only one row per group can play at a time.  When a new row in
        a group is triggered, every *other* row in that group is stopped
        and its LEDs are cleared.
        """
        if not self.engine:
            return
        group = self.engine.channels[row].state.group
        for i, ch in enumerate(self.engine.channels[:self.config.row_count]):
            if i != row and ch.state.group == group and ch.playing:
                self.engine.force_stop_channel(i)
                # Reset any SLICE state on the stopped channel
                if ch.state.mode == PlaybackMode.SLICE:
                    ch._slice_btn1 = None
                    ch._slice_loop_start = None
                    ch._slice_loop_end = None
                    ch._slice_reverse = False
                    ch._slice_latched = False
                    ch._slice_held = set()
                    ch._slice_col_start = -1
                    ch._slice_col_end = -1
                self.grid.clear_row_leds(i, columns)

    # ------------------------------------------------------------------ #
    #  Audio / Transport callbacks                                        #
    # ------------------------------------------------------------------ #

    def _on_meter_levels(self, levels: dict):
        """Store latest levels for UI update (called from audio thread)."""
        self._latest_levels = levels

    def _on_beat(self, beat_int: int, beat_position: float):
        """Beat boundary — update pattern recorders and SEQ mode channels."""
        if self.clock:
            for pr in self.patterns:
                pr.update(beat_position)

        # Advance SEQ-mode channels
        if self.engine:
            columns = self.config.grid_columns
            for i in range(self.config.row_count):
                ch = self.engine.channels[i]
                if ch.state.mode != PlaybackMode.SEQ:
                    continue
                if ch.sample is None:
                    continue
                # If a new sample was staged while SEQ was running, load it now
                pending = getattr(ch, '_pending_sample_path', None)
                if pending:
                    ch._pending_sample_path = None
                    self.engine.stop_channel(i)
                    self.grid.clear_row_leds(i, self.config.grid_columns)
                    self.load_sample_to_channel(i, pending)
                col = beat_int % columns if columns > 0 else 0
                ch._seq_column = col
                if col in ch._seq_steps:
                    # Retrigger sample from the beginning on each active step
                    self.engine.trigger_channel(i, 0, 1)
                    ch._seq_playing_step = True
                else:
                    # Inactive step — let the sample keep playing until it
                    # reaches its end or the next active step retriggers it.
                    ch._seq_playing_step = ch._playing

    def _on_bar(self, bar: int):
        """Bar boundary — for future use."""
        pass

    def _on_tick(self, beat_position: float, tempo: float):
        """Clock tick — update pattern recorders on fine grid."""
        for pr in self.patterns:
            pr.update(beat_position)

    def _on_pattern_trigger(self, row: int, column: int, event_type: str):
        """Pattern recorder replays an event — trigger the engine."""
        if self.engine:
            ch = self.engine.channels[row]
            if event_type == 'stop':
                self.engine.stop_channel(row)
                self.grid.clear_row_leds(row, self.config.grid_columns)
            else:
                self._stop_group_siblings(row, self.config.grid_columns)
                self.engine.trigger_channel(row, column, self.config.grid_columns)
                grp = ch.state.group
                if ch.state.mode == PlaybackMode.PITCH:
                    self._update_pitch_mode_leds(row, active_col=column)
                else:
                    self.grid.update_playback_leds(row, column, self.config.grid_columns, color=grp)

    # ------------------------------------------------------------------ #
    #  Grid callbacks                                                     #
    # ------------------------------------------------------------------ #

    def _on_grid_button(self, x: int, y: int, state: int):
        """Grid button press/release handler — core mlr interaction.

        When quantize is enabled, the trigger is scheduled to fire at
        the next quantize boundary so presses stay in sync with BPM.
        """
        row = y
        column = x
        columns = self.config.grid_columns

        if row >= self.config.row_count:
            return

        if state == 0:
            # Release handling for SLICE hold-to-play
            if self.engine:
                ch = self.engine.channels[row]
                if ch.state.mode == PlaybackMode.SLICE:
                    held = getattr(ch, '_slice_held', set())
                    held.discard(column)
                    # If no buttons held and not latched, stop playback
                    if not held and not getattr(ch, '_slice_latched', False):
                        self.engine.stop_channel(row)
                        ch._slice_btn1 = None
                        ch._slice_loop_start = None
                        ch._slice_loop_end = None
                        ch._slice_reverse = False
                        ch._slice_col_start = -1
                        ch._slice_col_end = -1
                        self.grid.clear_row_leds(row, columns)
            return

        # Check if channel has a sample — if not, ignore the press
        if self.engine and self.engine.channels[row].sample is None:
            return

        # SEQ mode: toggle steps on/off instead of triggering playback
        if self.engine:
            ch = self.engine.channels[row]
            if ch.state.mode == PlaybackMode.SEQ:
                if column in ch._seq_steps:
                    ch._seq_steps.discard(column)
                else:
                    ch._seq_steps.add(column)
                ch._seq_columns = columns
                # Update grid LEDs to show step pattern
                self._update_seq_leds(row)
                return

        # Determine quantize delay
        delay = 0.0
        if self.clock:
            delay = self.clock.get_quantize_delay()

        if delay <= 0.001:
            # Fire immediately (no quantize, or already on boundary)
            self._trigger_scheduler.schedule(row, column, columns, 0)
        else:
            # Schedule the trigger at the next quantize boundary
            delay_ms = int(delay * 1000)
            self._trigger_scheduler.schedule(row, column, columns, delay_ms)

    def _execute_grid_trigger(self, row: int, column: int, columns: int):
        """Actually fire the grid trigger (after quantize delay if any)."""
        if self.engine:
            ch = self.engine.channels[row]

            # If a preset transition staged a new sample, load it now
            pending = getattr(ch, '_pending_sample_path', None)
            if pending:
                ch._pending_sample_path = None
                # Stop current playback so the engine load goes through the
                # idle branch (direct load, not re-staged as pending).
                self.engine.stop_channel(row)
                self.grid.clear_row_leds(row, self.config.grid_columns)
                self.load_sample_to_channel(row, pending)

            # SLICE mode logic
            if ch.state.mode == PlaybackMode.SLICE:
                held = getattr(ch, '_slice_held', set())
                held.add(column)
                ch._slice_held = held

                # If latched (sub-loop active), any press stops and clears
                if getattr(ch, '_slice_latched', False):
                    self.engine.stop_channel(row)
                    ch._slice_btn1 = None
                    ch._slice_btn2 = None
                    ch._slice_loop_start = None
                    ch._slice_loop_end = None
                    ch._slice_reverse = False
                    ch._slice_latched = False
                    ch._slice_held = set()
                    ch._slice_col_start = -1
                    ch._slice_col_end = -1
                    self.grid.clear_row_leds(row, columns)
                    return

                if ch.playing:
                    btn1 = getattr(ch, '_slice_btn1', None)
                    # Second button pressed while first held — create latched sub-loop
                    if btn1 is not None and column != btn1:
                        ch._slice_btn2 = column
                        ch._slice_latched = True
                        c1, c2 = min(btn1, column), max(btn1, column)
                        ch._slice_col_start = c1
                        ch._slice_col_end = c2
                        sel_start = ch.state.sel_start
                        sel_end = ch.state.sel_end
                        sel_len = sel_end - sel_start
                        sub_start = sel_start + (c1 / columns) * sel_len
                        sub_end = sel_start + ((c2 + 1) / columns) * sel_len
                        sub_start_frames = sub_start * ch.sample.num_frames
                        sub_end_frames = sub_end * ch.sample.num_frames
                        ch._slice_loop_start = sub_start_frames
                        ch._slice_loop_end = sub_end_frames
                        ch._slice_reverse = column < btn1
                        # Reposition if cursor is outside the sub-loop
                        if ch._position < sub_start_frames or ch._position >= sub_end_frames:
                            self.engine.set_channel_position(row, sub_start_frames)
                        return
                    # Same button pressed again while held — ignore
                    if column == btn1:
                        return

                # Start hold-to-play (not latched yet)
                self._stop_group_siblings(row, columns)
                self.engine.trigger_channel(row, column, columns)
                ch._slice_btn1 = column
                ch._slice_btn2 = None
                ch._slice_loop_start = None
                ch._slice_loop_end = None
                ch._slice_reverse = False
                ch._slice_latched = False
                ch._slice_col_start = -1
                ch._slice_col_end = -1
            else:
                # Normal trigger (non-SLICE modes)
                self._stop_group_siblings(row, columns)
                self.engine.trigger_channel(row, column, columns)

        # Record to active pattern recorders
        if self.clock:
            beat = self.clock.beat_position
            for pr in self.patterns:
                if pr.recording:
                    pr.record_event(beat, row, column, 'trigger')

        # Update grid LEDs for the row
        grp = self.config.channels[row].group if row < len(self.config.channels) else 0
        if self.engine and self.engine.channels[row].state.mode == PlaybackMode.PITCH:
            self._update_pitch_mode_leds(row, active_col=column)
        else:
            self.grid.update_playback_leds(row, column, columns, color=grp)

        # Mirror via OSC
        if self.osc:
            self.osc.mirror_button(column, row, 1)

    def _update_seq_leds(self, row: int):
        """Update grid LEDs for a SEQ-mode row (dim=active step, bright=current step)."""
        ch = self.engine.channels[row]
        columns = self.config.grid_columns
        grp = ch.state.group
        values = [LED_OFF] * columns
        for c in ch._seq_steps:
            if 0 <= c < columns:
                values[c] = LED_DIM
        cur = ch._seq_column
        if 0 <= cur < columns:
            values[cur] = LED_BRIGHT
        self.grid.set_row(row, values, grp)

    def _update_pitch_mode_leds(self, row: int, active_col: int = -1):
        """Update grid LEDs for a PITCH-mode row.

        All columns show DIM (every button is a playable pitch).
        The currently-sounding column shows BRIGHT.
        """
        if not self.engine:
            return
        ch = self.engine.channels[row]
        columns = self.config.grid_columns
        grp = ch.state.group
        values = [LED_DIM] * columns
        if 0 <= active_col < columns:
            values[active_col] = LED_BRIGHT
        self.grid.set_row(row, values, grp)

    def _on_led_update(self, led_state):
        """Push LED state to virtual grid widget."""
        if self.window:
            self.window.virtual_grid.set_led_state(led_state)

    def _on_tilt_data(self, sensor: int, x: float, y: float, z: float):
        """Handle accelerometer/tilt data from monome grid.

        Forwards normalized 0.0-1.0 values to any mapped target params.
        """
        for axis, val in [("x", x), ("y", y), ("z", z)]:
            src = f"tilt.{sensor}.{axis}"
            target = self._tilt_mappings.get(src)
            if target and target in self.midi._param_callbacks:
                self.midi._param_callbacks[target](val)

    # ------------------------------------------------------------------ #
    #  Control row (monome top row / Launchpad CC buttons)                #
    # ------------------------------------------------------------------ #

    def _on_control_button(self, x: int, state: int):
        """Handle control row button press/release.

        x=0..3 → group stop, x=4..7 → pattern toggle or hold-to-clear.
        """
        if 0 <= x <= 3:
            if state == 1:
                self._gui_group_stop(x)
        elif 4 <= x <= 7:
            idx = x - 4
            if state == 1:
                self._pattern_hold_started[idx] = time.monotonic()
                return

            # Release: short press toggles, long hold clears.
            started = self._pattern_hold_started.pop(idx, None)
            if started is None:
                return
            held_for = time.monotonic() - started
            if held_for >= self._pattern_clear_hold_seconds:
                self._gui_pattern_state(idx, "clear")
                if self.window:
                    self.window.transport.set_pattern_state(idx, "idle")
            else:
                self._on_control_pattern_toggle(idx)

    def _on_control_pattern_toggle(self, idx: int):
        """Toggle pattern recorder from control button.

        State machine:
        - idle + no events → start recording
        - idle + has events → start playing
        - recording → stop recording, auto-play if events
        - playing → stop (keep events)
        """
        if idx >= len(self.patterns):
            return
        pr = self.patterns[idx]
        beat = self.clock.beat_position if self.clock else 0.0

        if pr.recording:
            pr._recording = False
            if pr.has_events:
                pr._playing = True
                pr._start_beat = beat
                gui_state = "playing"
            else:
                gui_state = "idle"
        elif pr.playing:
            # Stop playing — also stop channels the pattern triggered
            if self.engine:
                triggered_rows = set(e.row for e in pr.events if e.event_type == 'trigger')
                for r in triggered_rows:
                    ch = self.engine.channels[r]
                    if ch.playing:
                        self.engine.stop_channel(r)
                        self.grid.clear_row_leds(r, self.config.grid_columns)
            pr._playing = False
            gui_state = "idle"
        else:
            if pr.has_events:
                pr.toggle_play(beat)
                gui_state = "playing"
            else:
                pr.toggle_record(beat)
                gui_state = "recording"

        if self.window:
            self.window.transport.set_pattern_state(idx, gui_state)

    def _on_pattern_auto_state(self, idx: int, state: str):
        """Called when a pattern recorder auto-changes state (e.g. auto-stop recording)."""
        if self.window:
            self.window.transport.set_pattern_state(idx, state)

    # ------------------------------------------------------------------ #
    #  Monome async connect handling                                      #
    # ------------------------------------------------------------------ #

    def _check_monome_connect(self):
        """Check if a monome grid just connected (called from _update_ui)."""
        if not self.grid._pending_monome_connect:
            return
        self.grid._pending_monome_connect = False

        rows = self.grid.info.rows - self.grid._monome_row_offset  # data rows
        cols = self.grid.info.columns
        # Clamp to engine channel count to avoid index-out-of-bounds
        if self.engine:
            rows = min(rows, len(self.engine.channels))
        self.config.row_count = max(1, rows)
        self.config.grid_columns = cols

        for ch in self.config.channels:
            ch.steps = cols

        if self.window:
            self.window.channel_view.set_visible_rows(rows)
            self.window.set_grid_dimensions(rows, cols)
            self.window.set_hardware_grid_connected(True)
            self._sync_grid_columns_to_waveforms()
            # Auto-enable tilt control mapping for monome
            self.window.setup_view.grid_panel.set_tilt_enabled(True)
            self.window.setup_view.midi_panel.set_tilt_visible(True)
            self.grid.enable_tilt(True)
            self.window.set_status(
                f"Monome connected: {cols}×{self.grid.info.rows} "
                f"({rows} sample rows + control row)"
            )
            self.window._auto_resize_for_rows(rows)
            log.info("Monome connected: %dx%d (%d data rows)",
                     cols, self.grid.info.rows, rows)

    # ------------------------------------------------------------------ #
    #  Sample loading                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _snap_to_rate(speed: float) -> float:
        """Snap a raw speed to the nearest clean ½× / 2× step.

        E.g. 0.74 → 0.5, 1.3 → 1.0, 3.1 → 4.0.
        """
        import math
        if speed <= 0:
            return 1.0
        exp = math.log2(speed)
        snapped_exp = round(exp)
        return 2.0 ** snapped_exp

    @staticmethod
    def _normalize_speed(speed: float) -> float:
        """Normalize speed to the nearest octave of 1.0×.

        Divides or multiplies by powers of 2 so the result is
        as close to 1.0 as possible (always in ~0.71–1.41 range).
        E.g. 4.0 → 1.0, 3.1 → 0.775, 0.18 → 0.72, 0.37 → 0.74.
        """
        import math
        if speed <= 0:
            return 1.0
        exp = math.log2(speed)
        return speed / (2.0 ** round(exp))

    def load_sample_to_channel(self, row: int, filepath: str):
        """Load an audio file into a channel.

        If the row is currently playing, the old audio and LEDs keep
        looping — the new sample is shown in the GUI waveform and staged
        so it loads on the next grid trigger (or group/ALL stop).
        """
        try:
            sample = load_sample(filepath, self.config.sample_rate)
            if sample is None:
                if self.window:
                    self.window.set_status(f"Failed to load: {filepath}")
                return
            if self.engine:
                ch = self.engine.channels[row]

                if ch._playing:
                    # --- Playing row: visual-only swap, stage for next trigger ---
                    ch._pending_sample_path = filepath
                    self.config.channels[row].filename = filepath
                    self.config.channels[row].steps = self.config.grid_columns

                    # Pre-compute speed for the new sample (store in config only)
                    if sample.duration_sec > 0:
                        beats = self.config.grid_columns
                        bar_dur = beats * (60.0 / self.config.tempo)
                        raw_speed = sample.duration_sec / bar_dur
                        raw_speed = self._normalize_speed(raw_speed)
                        if self.config.snap_speed_to_rate:
                            raw_speed = self._snap_to_rate(raw_speed)
                        # Always store the BPM-calculated speed as normal_speed
                        self.config.channels[row].normal_speed = raw_speed
                        if self.config.channels[row].mode != PlaybackMode.SEQ:
                            # Only update active speed for non-SEQ modes
                            self.config.channels[row].speed = raw_speed

                    # Update waveform + speed spinner in the GUI
                    self._load_waveform_display(row, filepath)
                    if self.window:
                        strip = self.window.channel_view.get_strip(row)
                        strip._true_speed = self.config.channels[row].speed
                        strip._speed_spin.blockSignals(True)
                        strip._speed_spin.setValue(self.config.channels[row].speed)
                        strip._speed_spin.blockSignals(False)
                else:
                    # --- Idle row: load directly into engine ---
                    self.engine.load_sample(row, sample)
                    self.config.channels[row].filename = filepath
                    self.config.channels[row].steps = self.config.grid_columns

                    # Re-apply pitch shift if transpose was already set
                    transpose = self.config.channels[row].transpose
                    if transpose != 0:
                        self._apply_pitch_shift_async(row, transpose)
                    elif self.config.channels[row].mode == PlaybackMode.PITCH:
                        self._build_pitch_scale_cache_async(row)

                    overview = get_waveform_overview(sample, num_points=800)
                    if self.window:
                        strip = self.window.channel_view.get_strip(row)
                        import os
                        strip.set_waveform_data(overview, os.path.basename(filepath))
                        strip._waveform.set_grid_columns(self.config.grid_columns)

                    self.engine.set_channel_speed(row, self.config.tempo)
                    speed = self.engine.channels[row].state.speed
                    speed = self._normalize_speed(speed)
                    if self.config.snap_speed_to_rate:
                        speed = self._snap_to_rate(speed)
                    # Store BPM-calculated speed; use 1.0 as active speed in SEQ mode
                    self.config.channels[row].normal_speed = speed
                    if self.config.channels[row].mode == PlaybackMode.SEQ:
                        speed = 1.0
                    self.engine.channels[row].state.speed = speed
                    self.config.channels[row].speed = speed
                    if self.window:
                        strip = self.window.channel_view.get_strip(row)
                        strip._true_speed = speed
                        strip._speed_spin.blockSignals(True)
                        strip._speed_spin.setValue(speed)
                        strip._speed_spin.blockSignals(False)

                # Common bookkeeping for both paths
                from mlrvp.gui.views.channel_view import ChannelStrip
                ChannelStrip._add_to_library(filepath)

                if self.window:
                    self.window.set_status(
                        f"Loaded: {filepath} → Channel {row + 1}"
                    )
                    self.window.tools_view.slice_panel.add_loaded_sample(filepath)
                log.info("Loaded sample: %s → channel %d", filepath, row)
        except Exception as e:
            log.error("Failed to load sample: %s", e)
            if self.window:
                self.window.set_status(f"Error loading: {e}")

    def clear_channel_sample(self, row: int):
        """Remove the sample from a channel, restoring it to empty state."""
        if self.engine and 0 <= row < len(self.engine.channels):
            ch = self.engine.channels[row]
            self.engine.stop_channel(row)
            ch.sample = None
            ch._position = 0.0
            self.config.channels[row].filename = ""
            self.grid.clear_row_leds(row, self.config.grid_columns)
            if self.window:
                strip = self.window.channel_view.get_strip(row)
                strip._waveform.set_waveform(None, "")
                strip._sample_name.setText("(no sample)")
                strip._sample_name.setToolTip("Click to select from loaded samples")
                self.window.set_status(f"Channel {row + 1} cleared")
            log.info("Cleared sample from channel %d", row)

    # ------------------------------------------------------------------ #
    #  GUI signal wiring                                                  #
    # ------------------------------------------------------------------ #

    def _connect_gui_signals(self):
        """Wire all GUI signals to controller methods."""
        w = self.window
        if not w:
            return

        # --- Transport ---
        w.transport.tempo_changed.connect(self._gui_tempo_changed)
        w.transport.quantize_changed.connect(self._gui_quantize_changed)
        w.transport.pattern_state_changed.connect(self._gui_pattern_state)
        w.transport.pattern_length_changed.connect(self._gui_pattern_length)
        w.transport.preset_save_requested.connect(self._gui_save_preset)
        w.transport.preset_new_requested.connect(self._gui_new_preset)
        w.transport.preset_recall_requested.connect(self._gui_live_recall_preset)
        w.transport.preset_prev_requested.connect(self._gui_preset_prev)
        w.transport.preset_next_requested.connect(self._gui_preset_next)
        w.transport.setlist_changed.connect(self._gui_setlist_changed)
        w.transport.setlist_edit_requested.connect(self._gui_setlist_edit)
        w.transport.sync_bpm_requested.connect(self._gui_sync_bpm)
        w.transport.tap_tempo_requested.connect(self._gui_tap_tempo)
        w.transport.group_stop_requested.connect(self._gui_group_stop)
        w.transport.stop_all_requested.connect(self._gui_stop_all)

        # --- Channel view ---
        w.channel_view.sample_load_requested.connect(self.load_sample_to_channel)
        w.channel_view.sample_clear_requested.connect(self.clear_channel_sample)
        w.channel_view.param_changed.connect(self._gui_channel_param)

        # --- Mixer ---
        w.mixer_view.group_volume_changed.connect(self._gui_group_volume)
        w.mixer_view.group_send_changed.connect(self._gui_group_send)
        w.mixer_view.group_mute_toggled.connect(self._gui_group_mute)
        w.mixer_view.group_fx_route_changed.connect(self._gui_group_fx_route)
        w.mixer_view.group_stop_requested.connect(self._gui_group_stop)
        w.mixer_view.aux_volume_changed.connect(self._gui_aux_volume)
        w.mixer_view.aux_mute_toggled.connect(self._gui_aux_mute)
        w.mixer_view.aux_fx_route_changed.connect(self._gui_aux_fx_route)
        w.mixer_view.aux_send_changed.connect(self._gui_aux_send)
        w.mixer_view.master_volume_changed.connect(self._gui_master_volume)
        w.mixer_view.dac_toggled.connect(self._gui_dac_toggle)
        w.master_filter_view.hp_changed.connect(self._gui_master_hp)
        w.master_filter_view.lp_changed.connect(self._gui_master_lp)

        # --- Effects ---
        w.effects_view.delay_param_changed.connect(self._gui_delay_param)
        w.effects_view.tape_delay_param_changed.connect(self._gui_tape_delay_param)
        w.effects_view.filter_a_param_changed.connect(self._gui_filter_a_param)
        w.effects_view.filter_b_param_changed.connect(self._gui_filter_b_param)
        w.effects_view.mod_param_changed.connect(self._gui_mod_param)
        w.effects_view.reel_to_reel_param_changed.connect(self._gui_reel_to_reel_param)
        w.effects_view.cassette_tape_param_changed.connect(self._gui_cassette_tape_param)
        w.effects_view.vhs_param_changed.connect(self._gui_vhs_param)
        w.effects_view.vinyl_param_changed.connect(self._gui_vinyl_param)
        w.effects_view.compressor_param_changed.connect(self._gui_compressor_param)
        w.effects_view.glue_compressor_param_changed.connect(self._gui_glue_compressor_param)
        w.effects_view.hall_reverb_param_changed.connect(self._gui_hall_reverb_param)
        w.effects_view.plate_reverb_param_changed.connect(self._gui_plate_reverb_param)
        w.effects_view.bit_crusher_param_changed.connect(self._gui_bit_crusher_param)
        w.effects_view.tube_overdrive_param_changed.connect(self._gui_tube_overdrive_param)
        w.effects_view.ether_drift_param_changed.connect(self._gui_ether_drift_param)
        w.effects_view.effect_route_changed.connect(self._gui_effect_route)
        w.effects_view.midi_map_toggled.connect(self._gui_toggle_midi_map_mode)
        w.effects_view.rack_contents_changed.connect(self._on_rack_contents_changed)
        # Ensure engine state is immediately consistent with current rack layout.
        self._on_rack_contents_changed(w.effects_view.get_rack_sync_state())

        # --- Tools ---
        w.tools_view.record_toggled.connect(self._gui_record_toggle)
        w.tools_view.slice_saved.connect(self._on_slice_saved)

        # --- Setup ---
        w.setup_view.audio_setting_changed.connect(self._gui_audio_setting)
        w.setup_view.grid_setting_changed.connect(self._gui_grid_setting)
        w.setup_view.grid_scan_requested.connect(self._gui_scan_grids)
        w.setup_view.midi_grid_connect_requested.connect(self._gui_midi_grid_connect)
        w.setup_view.midi_grid_disconnect_requested.connect(self._gui_midi_grid_disconnect)
        w.setup_view.save_default_requested.connect(self._gui_save_default)

        # --- OSC ---
        w.setup_view.osc_setting_changed.connect(self._gui_osc_setting)

        # --- MIDI learn ---
        w.setup_view.midi_learn_toggled.connect(self._gui_toggle_midi_map_mode)
        w.setup_view.midi_setting_changed.connect(self._gui_midi_setting)
        w.setup_view.midi_mapping_range_changed.connect(self._gui_midi_range_changed)
        w.setup_view.midi_mapping_removed.connect(self._gui_midi_mapping_removed)
        w.setup_view.midi_mapping_toggle_changed.connect(self._gui_midi_toggle_changed)
        w.setup_view.midi_tilt_mapping_changed.connect(self._gui_tilt_mapping_changed)
        w.setup_view.midi_input_added.connect(self._gui_midi_input_added)
        w.setup_view.midi_input_removed.connect(self._gui_midi_input_removed)
        w.setup_view.tilt_enabled_changed.connect(self._gui_tilt_enabled_changed)
        self.midi.learn_completed.connect(self._on_midi_learn_complete)

        # --- Virtual grid ---
        w.virtual_grid.button_pressed.connect(
            lambda x, y, state: self._on_grid_button(x, y, state)
        )

        # --- Main window ---
        w.quit_requested.connect(self.stop)
        w.preset_file_open_requested.connect(self._gui_open_preset_file)
        w.preset_file_save_requested.connect(self._gui_save_preset_file)
        w.stop_all_requested.connect(self._gui_stop_all)
        w.restart_requested.connect(self._gui_restart)
        w.scale_change_requested.connect(lambda f: self._gui_restart())

    # --- Transport handlers ---

    def _gui_tempo_changed(self, bpm: float):
        if self.clock:
            self.clock.set_tempo(bpm)
        if self.engine:
            old_bpm = self.config.tempo
            self.engine.set_tempo(bpm)
            # Update speed spinners in channel strips
            if self.window:
                ratio = bpm / old_bpm if old_bpm > 0 else 1.0
                for i in range(self.config.row_count):
                    ch = self.engine.channels[i]
                    if ch.sample is not None:
                        cfg_ch = self.config.channels[i]
                        strip = self.window.channel_view.get_strip(i)
                        if cfg_ch.mode == PlaybackMode.SEQ:
                            # Scale normal_speed to stay BPM-relative, but
                            # keep the active SEQ speed (engine skips SEQ channels)
                            cfg_ch.normal_speed = max(0.01, min(8.0, cfg_ch.normal_speed * ratio))
                            seq_speed = cfg_ch.speed
                            ch.state.speed = seq_speed
                            strip._true_speed = seq_speed
                            strip._speed_spin.blockSignals(True)
                            strip._speed_spin.setValue(seq_speed)
                            strip._speed_spin.blockSignals(False)
                        else:
                            speed = ch.state.speed
                            cfg_ch.speed = speed
                            cfg_ch.normal_speed = speed
                            strip._true_speed = speed
                            strip._speed_spin.blockSignals(True)
                            strip._speed_spin.setValue(speed)
                            strip._speed_spin.blockSignals(False)
        self.config.tempo = bpm

    def _gui_quantize_changed(self, idx: int):
        from mlrvp.config import QuantizeMode
        self.config.quantize = QuantizeMode(idx)
        if self.clock:
            self.clock.quantize = QuantizeMode(idx)

    def _gui_sync_bpm(self):
        """Auto-detect BPM from loaded samples.

        Uses sample duration and grid columns to estimate the most likely
        BPM, averaging across all loaded channels.
        """
        if not self.engine:
            return

        candidates = []
        for i in range(self.config.row_count):
            ch = self.engine.channels[i]
            if ch.sample is None:
                continue
            dur = ch.sample.duration_sec
            if dur <= 0:
                continue
            cols = self.config.grid_columns
            # Each column = 1 beat, so total beats = cols
            # BPM = beats / (duration_in_minutes)
            bpm = cols / (dur / 60.0)
            # Bring BPM into a reasonable range (60-200)
            while bpm < 60:
                bpm *= 2
            while bpm > 200:
                bpm /= 2
            candidates.append(bpm)

        if not candidates:
            if self.window:
                self.window.set_status("SYNC: No samples loaded to detect BPM")
            return

        # Use the median BPM for robustness
        candidates.sort()
        mid = len(candidates) // 2
        detected_bpm = round(candidates[mid], 1)

        self.config.tempo = detected_bpm
        if self.clock:
            self.clock.set_tempo(detected_bpm, immediate=True)
        if self.engine:
            self.engine.set_tempo(detected_bpm)
        if self.window:
            self.window.transport.set_tempo(detected_bpm)
            self.window.set_status(f"SYNC: Detected BPM = {detected_bpm}")

    def _gui_tap_tempo(self, bpm: float):
        """Apply a BPM value received from the tap tempo button."""
        bpm = round(bpm, 1)
        self.config.tempo = bpm
        if self.clock:
            self.clock.set_tempo(bpm, immediate=True)
        if self.engine:
            self.engine.set_tempo(bpm)
        if self.window:
            self.window.transport.set_tempo(bpm)

    def _gui_pattern_state(self, idx: int, state: str):
        if idx >= len(self.patterns):
            return
        pr = self.patterns[idx]
        beat = self.clock.beat_position if self.clock else 0.0
        if state == "recording":
            pr.toggle_record(beat)
        elif state == "playing":
            pr.toggle_play(beat)
        elif state == "clear":
            # Stop channels the pattern was driving before clearing
            if pr.playing and self.engine:
                triggered_rows = set(e.row for e in pr.events if e.event_type == 'trigger')
                for r in triggered_rows:
                    ch = self.engine.channels[r]
                    if ch.playing:
                        self.engine.stop_channel(r)
                        self.grid.clear_row_leds(r, self.config.grid_columns)
            pr.clear()
        else:
            # When pattern stops, stop all channels it had triggered
            if pr.playing and self.engine:
                triggered_rows = set(e.row for e in pr.events if e.event_type == 'trigger')
                for r in triggered_rows:
                    ch = self.engine.channels[r]
                    if ch.playing:
                        self.engine.stop_channel(r)
                        self.grid.clear_row_leds(r, self.config.grid_columns)
            pr.stop()

    def _gui_pattern_length(self, idx: int, length: int):
        """Update pattern recorder length when user changes bar count."""
        if idx < len(self.patterns):
            self.patterns[idx].set_length(length * 4)  # convert bars to beats

    def _gui_new_preset(self):
        """Clear all channels and reset to a fresh default state."""
        # Stop all playback
        if self.engine:
            for i in range(self.config.row_count):
                self.engine.stop_channel(i)

        # Clear all grid LEDs
        for i in range(self.config.row_count):
            self.grid.clear_row_leds(i, self.config.grid_columns)

        # Reset channel config to defaults
        from mlrvp.config import ChannelState
        for i in range(self.config.row_count):
            self.config.channels[i] = ChannelState(group=i % NUM_GROUPS)
            if self.engine:
                ech = self.engine.channels[i]
                ech.sample = None
                ech._position = 0.0
                ech.state.speed = 1.0
                ech.state.mode = PlaybackMode.LOOP
                ech.state.gain = 1.0
                ech.state.group = i % NUM_GROUPS
                ech.state.mute = False
                ech.state.sel_start = 0.0
                ech.state.sel_end = 1.0
                ech.state.reverse = False
                ech._pending_sample_path = None

        # Clear waveform displays BEFORE applying config to GUI, so
        # even if _apply_config_to_gui() fails partway the displays are clean.
        if self.window:
            for i in range(self.config.row_count):
                strip = self.window.channel_view.get_strip(i)
                strip._waveform.set_waveform(None, "")
                strip._waveform.set_playing(False)
                strip._waveform.set_active_column(-1)
                strip._sample_name.setText("(no sample)")

        # Update GUI controls from reset config
        self._apply_config_to_gui()

        # De-select active preset (fresh slate, not tied to any preset)
        self.presets.active_index = -1
        self._refresh_setlist_ui()
        if self.window:
            self.window.set_status("New preset — all channels cleared")

    def _gui_save_preset(self, name: str):
        """Save current state to the preset library and add to setlist."""
        # Save to library
        ok = self.presets.save_preset(
            name, self.config,
            midi_mappings=self.midi.get_mappings_serializable())
        if not ok:
            if self.window:
                self.window.set_status(f"Failed to save preset: {name}")
            return

        # If this name isn't already the currently-active entry in the
        # setlist, add it (avoids duplicates when overwriting).
        sp = self.presets.setlist_presets
        idx = self.presets.active_index
        if 0 <= idx < len(sp) and sp[idx] == name:
            # Overwriting the currently-active slot — nothing to add
            pass
        else:
            # New preset or name changed — add to end of setlist
            new_idx = self.presets.add_to_setlist(name)
            if new_idx >= 0:
                self.presets.active_index = new_idx

        self._refresh_setlist_ui()
        if self.window:
            self.window.set_status(f"Saved: {name}")
            self.window.transport.flash_save_button()

    def _gui_live_recall_preset(self, idx: int):
        """Recall a preset from the setlist with live transition.

        - Effects, mixer, transport update immediately.
        - Waveform displays update for ALL channels (visual preview).
        - Playing channels keep their current audio looping;
          speed/group/selection are NOT overwritten mid-playback.
        - New samples are staged (_pending_sample_path) and loaded
          on the next grid trigger for that row.
        """
        data = self.presets.load_setlist_preset(idx)
        if not data:
            return

        # Remember which engine channels are currently playing
        playing_rows: set[int] = set()
        if self.engine:
            for i in range(self.config.row_count):
                if self.engine.channels[i]._playing:
                    playing_rows.add(i)

        # Apply config (effects, mixer, transport, channel params)
        self.config = self.presets.apply_preset(data, self.config)

        # Push to engine — but protect playing channels from state clobber
        self._apply_config_to_engine_live(playing_rows)
        self._apply_config_to_gui()

        # Load samples — visual update for ALL channels, audio staged for playing
        if self.engine:
            import os
            for i, ch_cfg in enumerate(self.config.channels[:self.config.row_count]):
                ech = self.engine.channels[i]
                if ch_cfg.filename and os.path.isfile(ch_cfg.filename):
                    if i in playing_rows:
                        # Stage sample for next trigger — current audio keeps looping
                        ech._pending_sample_path = ch_cfg.filename
                        # But still update waveform display (visual preview)
                        self._load_waveform_display(i, ch_cfg.filename)
                    else:
                        self._load_sample_no_speed_reset(i, ch_cfg.filename)
                elif not ch_cfg.filename and i not in playing_rows:
                    self.clear_channel_sample(i)

        # Restore MIDI mappings
        midi_maps = data.get("midi_mappings", [])
        self.midi.load_mappings(midi_maps)
        for m in midi_maps:
            self._register_midi_param_callback(m["param_path"])
        self._refresh_midi_mapping_table()

        self._refresh_setlist_ui()
        if self.window:
            name = self.presets.setlist_presets[idx] if idx < len(self.presets.setlist_presets) else f"Preset {idx+1}"
            self.window.set_status(f"Recalled: {name}")

    def _gui_preset_prev(self):
        """Navigate to previous preset in the setlist."""
        n = len(self.presets.setlist_presets)
        if n == 0:
            return
        cur = self.presets.active_index
        new_idx = (cur - 1) % n if cur > 0 else n - 1
        self._gui_live_recall_preset(new_idx)

    def _gui_preset_next(self):
        """Navigate to next preset in the setlist."""
        n = len(self.presets.setlist_presets)
        if n == 0:
            return
        cur = self.presets.active_index
        new_idx = (cur + 1) % n
        self._gui_live_recall_preset(new_idx)

    def _gui_setlist_changed(self, name: str):
        """Switch to a different setlist."""
        if name and name != self.presets.setlist_name:
            self.presets.load_setlist(name)
            self._refresh_setlist_ui()
            if self.window:
                self.window.set_status(f"Setlist: {name}")

    def _gui_setlist_edit(self):
        """Open the setlist editor dialog."""
        from mlrvp.gui.setlist_editor import SetlistEditorDialog
        dlg = SetlistEditorDialog(self.presets, self.window)
        dlg.setlist_changed.connect(self._refresh_setlist_ui)
        dlg.exec()

    def _refresh_setlist_ui(self):
        """Sync setlist/preset dropdowns with current manager state."""
        if not self.window:
            return
        t = self.window.transport
        t.populate_setlist_combo(
            self.presets.list_setlists(), self.presets.setlist_name
        )
        t.populate_preset_combo(
            self.presets.setlist_presets, self.presets.active_index
        )

    def _gui_recall_preset(self, idx: int):
        """Full (non-live) recall — used by file open. Stops all channels."""
        data = self.presets.load_setlist_preset(idx)
        if data:
            self.config = self.presets.apply_preset(data, self.config)
            self._apply_config_to_engine()
            self._apply_config_to_gui()
            self._reload_samples_from_config()
            midi_maps = data.get("midi_mappings", [])
            self.midi.load_mappings(midi_maps)
            for m in midi_maps:
                self._register_midi_param_callback(m["param_path"])
            self._refresh_midi_mapping_table()
            self._refresh_setlist_ui()
            if self.window:
                self.window.set_status(f"Preset {idx + 1} recalled")

    def _gui_open_preset_file(self, path: str):
        """Load a preset from a specific file path."""
        import json
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            self.config = self.presets.apply_preset(data, self.config)
            self._apply_config_to_engine()
            self._apply_config_to_gui()
            self._reload_samples_from_config()
            # Restore MIDI mappings
            midi_maps = data.get("midi_mappings", [])
            self.midi.load_mappings(midi_maps)
            for m in midi_maps:
                self._register_midi_param_callback(m["param_path"])
            self._refresh_midi_mapping_table()
            if self.window:
                self.window.set_status(f"Loaded preset: {path}")
        except Exception as e:
            log.error("Failed to open preset file: %s", e)
            if self.window:
                self.window.set_status(f"Error loading preset: {e}")

    def _gui_save_preset_file(self, path: str):
        """Save current config to a specific file path."""
        import json
        try:
            data = self.presets._serialize_config(self.config)
            payload = {
                "version": "0.6.9",
                "config": data,
                "midi_mappings": self.midi.get_mappings_serializable(),
            }
            with open(path, 'w') as f:
                json.dump(payload, f, indent=2)
            if self.window:
                self.window.set_status(f"Saved preset: {path}")
        except Exception as e:
            log.error("Failed to save preset file: %s", e)
            if self.window:
                self.window.set_status(f"Error saving preset: {e}")

    def _gui_save_default(self, name: str):
        """Save current config as a named default preset."""
        import json
        from .config import get_defaults_dir
        try:
            path = get_defaults_dir() / f"{name}.json"
            data = self.presets._serialize_config(self.config)
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            if self.window:
                self.window.set_status(f"Saved default: {name}")
        except Exception as e:
            log.error("Failed to save default: %s", e)
            if self.window:
                self.window.set_status(f"Error saving default: {e}")

    # --- Channel param handlers ---

    def _gui_channel_param(self, row: int, param: str, value):
        ch = self.config.channels[row]
        if param == "mode":
            old_mode = ch.mode
            ch.mode = value
            if self.engine:
                ech = self.engine.channels[row]
                ech.state.mode = value
                # Clear SEQ state when leaving SEQ mode
                if value != PlaybackMode.SEQ:
                    ech._seq_steps.clear()
                    ech._seq_column = -1
                    ech._seq_playing_step = False
            # SEQ mode: lock steps to grid column count (one step per physical button)
            if self.window:
                strip = self.window.channel_view.get_strip(row)
                if value == PlaybackMode.SEQ:
                    # Save current BPM speed and switch to SEQ default 1.0
                    ch.normal_speed = ch.speed
                    seq_speed = 1.0
                    ch.speed = seq_speed
                    if self.engine:
                        self.engine.channels[row].state.speed = seq_speed
                    strip._true_speed = seq_speed
                    strip._speed_spin.blockSignals(True)
                    strip._speed_spin.setValue(seq_speed)
                    strip._speed_spin.blockSignals(False)
                    cols = self.config.grid_columns
                    ch.steps = cols
                    if self.engine:
                        self.engine.channels[row].state.steps = cols
                    strip._steps_spin.blockSignals(True)
                    strip._steps_spin.setValue(cols)
                    strip._steps_spin.blockSignals(False)
                    strip._steps_spin.setEnabled(False)
                    strip._waveform.set_grid_columns(cols)
                else:
                    # Restore the BPM speed when leaving SEQ mode
                    if old_mode == PlaybackMode.SEQ or old_mode == PlaybackMode.PITCH:
                        restored = ch.normal_speed
                        ch.speed = restored
                        if self.engine:
                            self.engine.channels[row].state.speed = restored
                        strip._true_speed = restored
                        strip._speed_spin.blockSignals(True)
                        strip._speed_spin.setValue(restored)
                        strip._speed_spin.blockSignals(False)
                    strip._steps_spin.setEnabled(value != PlaybackMode.PITCH)
                # PITCH mode: show all buttons as DIM (available pitch triggers)
                if value == PlaybackMode.PITCH:
                    if self.engine:
                        self._update_pitch_mode_leds(row)
                        self._build_pitch_scale_cache_async(row)
                elif old_mode == PlaybackMode.PITCH:
                    # Leaving PITCH mode — clear the DIM LEDs
                    self.grid.clear_row_leds(row, self.config.grid_columns)
        elif param == "stop":
            if self.engine:
                ech = self.engine.channels[row]
                self.engine.force_stop_channel(row)
                # Reset SEQ state and clear pattern
                if ech.state.mode == PlaybackMode.SEQ:
                    ech._seq_steps.clear()
                    ech._seq_column = -1
                    ech._seq_playing_step = False
                # Reset SLICE state
                if ech.state.mode == PlaybackMode.SLICE:
                    ech._slice_btn1 = None
                    ech._slice_loop_start = None
                    ech._slice_loop_end = None
                    ech._slice_reverse = False
                    ech._slice_latched = False
                    ech._slice_held = set()
                    ech._slice_col_start = -1
                    ech._slice_col_end = -1
                # PITCH mode: restore DIM LEDs (all buttons remain available)
                if ech.state.mode == PlaybackMode.PITCH:
                    self._update_pitch_mode_leds(row)
                else:
                    self.grid.clear_row_leds(row, self.config.grid_columns)
        elif param == "group":
            ch.group = value
            # Sync group to audio engine state
            if self.engine:
                self.engine.channels[row].state.group = value
            # Update virtual grid row color to match group
            if self.window:
                self.window.virtual_grid.set_row_color(row, value)
        elif param == "gain":
            ch.gain = value
        elif param == "speed":
            ch.speed = value
            if ch.mode != PlaybackMode.SEQ:
                ch.normal_speed = value
            if self.engine:
                # Only push to engine if no staged sample (old audio keeps its speed)
                ech = self.engine.channels[row]
                if not getattr(ech, '_pending_sample_path', None):
                    ech.state.speed = value
        elif param == "transpose":
            ch.transpose = value
            if self.engine:
                self.engine.channels[row].state.transpose = value
                self._apply_pitch_shift_async(row, value)
        elif param == "pitch_scale":
            ch.pitch_scale = int(value)
            if self.engine:
                self.engine.channels[row].state.pitch_scale = int(value)
                if ch.mode == PlaybackMode.PITCH:
                    self._build_pitch_scale_cache_async(row)
        elif param == "pitch_root":
            ch.pitch_root = int(value)
            if self.engine:
                self.engine.channels[row].state.pitch_root = int(value)
                if ch.mode == PlaybackMode.PITCH:
                    self._build_pitch_scale_cache_async(row)
        elif param == "pitch_hold":
            ch.pitch_hold = bool(value)
            if self.engine:
                self.engine.channels[row].state.pitch_hold = bool(value)
        elif param == "mute":
            ch.mute = value
            if self.engine:
                self.engine.channels[row].state.mute = value
        elif param == "steps":
            ch.steps = value
            if self.engine:
                self.engine.channels[row].state.steps = value
            if self.window:
                strip = self.window.channel_view.get_strip(row)
                strip._waveform.set_grid_columns(value)
        elif param == "selection":
            ch.sel_start, ch.sel_end = value
            if self.engine:
                self.engine.channels[row].state.sel_start = ch.sel_start
                self.engine.channels[row].state.sel_end = ch.sel_end
                # Recalculate speed to fit new selection length to grid
                self.engine.set_channel_speed(row, self.config.tempo)
                speed = self.engine.channels[row].state.speed
                ch.speed = speed
                if self.window:
                    strip = self.window.channel_view.get_strip(row)
                    strip._true_speed = speed
                    strip._speed_spin.blockSignals(True)
                    strip._speed_spin.setValue(speed)
                    strip._speed_spin.blockSignals(False)

    # --- Mixer handlers ---

    def _gui_group_volume(self, group: int, vol: float):
        self.config.groups[group].volume = vol
        if self.engine:
            self.engine.set_group_volume(group, vol)

    def _gui_group_send(self, group: int, send: float):
        self.config.groups[group].send = send
        if self.engine:
            self.engine.set_group_send(group, send)

    def _gui_group_mute(self, group: int, mute: bool):
        self.config.groups[group].mute = mute
        if self.engine:
            self.engine.set_group_mute(group, mute)

    def _gui_group_stop(self, group: int):
        """Stop all channels in the specified group."""
        if self.engine:
            for i, ch in enumerate(self.engine.channels[:self.config.row_count]):
                if ch.state.group == group and (ch.playing or ch.state.mode == PlaybackMode.SEQ):
                    self.engine.force_stop_channel(i)
                    # Reset slice state
                    if ch.state.mode == PlaybackMode.SLICE:
                        ch._slice_btn1 = None
                        ch._slice_loop_start = None
                        ch._slice_loop_end = None
                        ch._slice_reverse = False
                        ch._slice_latched = False
                        ch._slice_held = set()
                        ch._slice_col_start = -1
                        ch._slice_col_end = -1
                    if ch.state.mode == PlaybackMode.SEQ:
                        ch._seq_steps.clear()
                        ch._seq_column = -1
                        ch._seq_playing_step = False
        # Clear grid LEDs for the stopped rows
        for i, ch in enumerate(self.config.channels[:self.config.row_count]):
            if ch.group == group:
                self.grid.clear_row_leds(i, self.config.grid_columns)

    def _gui_stop_all(self):
        """Stop all channels and clear all grid LEDs."""
        if self.engine:
            for i, ch in enumerate(self.engine.channels):
                self.engine.force_stop_channel(i)
                if ch.state.mode == PlaybackMode.SLICE:
                    ch._slice_btn1 = None
                    ch._slice_loop_start = None
                    ch._slice_loop_end = None
                    ch._slice_reverse = False
                    ch._slice_latched = False
                    ch._slice_held = set()
                    ch._slice_col_start = -1
                    ch._slice_col_end = -1
                if ch.state.mode == PlaybackMode.SEQ:
                    ch._seq_steps.clear()
                    ch._seq_column = -1
                    ch._seq_playing_step = False
        for i in range(self.config.row_count):
            self.grid.clear_row_leds(i, self.config.grid_columns)

    def _gui_restart(self):
        """Restart the application by re-launching the process."""
        import subprocess
        # Stop audio/transport first
        self.stop()
        # Use -m to ensure the package is importable on restart
        subprocess.Popen([sys.executable, "-m", "mlrvp"])
        QApplication.instance().quit()

    def _gui_group_fx_route(self, group: int, route: int):
        """Set FX routing for a group: 0=OFF/M, 1=FX1, 2=FX2."""
        self.config.groups[group].fx_route = route
        if self.engine:
            self.engine.mixer.groups[group].fx_route = route
        # Sync mixer view
        w = self.window
        if w:
            w.mixer_view.set_group_fx_route(group, route)

    def _gui_aux_volume(self, vol: float):
        self.config.aux_volume = vol
        if self.engine:
            self.engine.set_aux_volume(vol)

    def _gui_aux_mute(self, muted: bool):
        self.config.aux_mute = muted
        if self.engine:
            self.engine.mixer.aux_mute = muted

    def _gui_aux_fx_route(self, route: int):
        self.config.aux_fx_route = route
        if self.engine:
            self.engine.mixer.aux_fx_route = route

    def _gui_aux_send(self, level: float):
        self.config.aux_send_level = max(0.0, min(1.0, level))
        if self.engine:
            self.engine.mixer.aux_send_level = self.config.aux_send_level

    def _gui_master_volume(self, vol: float):
        self.config.master_volume = vol
        if self.engine:
            self.engine.set_master_volume(vol)

    def _gui_dac_toggle(self, dac_checked: bool):
        """DAC button: unchecked (white) = audio ON, checked (dark) = audio OFF."""
        if not self.engine:
            return
        if dac_checked:
            self.engine.stop()
        else:
            try:
                self.engine.start()
            except Exception as e:
                log.warning("Audio start failed: %s", e)
        audio_on = not dac_checked
        if self.window:
            self.window.mixer_view.set_audio_enabled(audio_on)
            self.window.channel_view.set_audio_enabled(audio_on)

    def _gui_master_hp(self, val: float):
        """Master HP trim: knob 0.0=20Hz (off), 1.0=500Hz (max trim)."""
        freq = 20.0 + val * 480.0  # 20-500 Hz range
        if self.engine:
            self.engine.set_master_hp(freq)

    def _gui_master_lp(self, val: float):
        """Master LP trim: knob 0.0=5kHz (max trim), 1.0=20kHz (off)."""
        freq = 5000.0 + val * 15000.0  # 5000-20000 Hz range
        if self.engine:
            self.engine.set_master_lp(freq)

    # --- Effects handlers ---

    # Bus→suffix mapping for per-bus config/engine attribute resolution
    _BUS_CFG_SUFFIX = {1: "", 2: "_b", 3: "_m", 4: "_c", 5: "_d"}
    _BUS_MOD_MAP = {1: "fx_bus_1", 2: "fx_bus_2", 3: "fx_bus_m", 4: "fx_bus_c", 5: "fx_bus_d"}

    def _resolve_bus(self, fx_name: str, bus: int):
        """Return (config_state, engine_instance) for an effect on the given bus."""
        suffix = self._BUS_CFG_SUFFIX.get(bus, "")
        cfg_key = f"{fx_name}{suffix}" if suffix else fx_name
        eng_key = f"{fx_name}{suffix}" if suffix else fx_name
        return getattr(self.config, cfg_key), getattr(self.engine, eng_key) if self.engine else None

    def _gui_delay_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("delay", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
            if param in ("filter_freq_lp", "filter_freq_hp"):
                inst._update_filters()

    def _gui_tape_delay_param(self, param: str, value, bus: int = 1):
        """Handle tape delay parameter change."""
        cfg, inst = self._resolve_bus("tape_delay", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_filter_a_param(self, param: str, value, bus: int = 1):
        """Handle filter A (shapeable LP/HP) parameter change."""
        cfg, inst = self._resolve_bus("filter_a", bus)
        setattr(cfg, param, value)
        if inst:
            inst.update_from_state(cfg)

    def _gui_filter_b_param(self, param: str, value, bus: int = 1):
        """Handle filter B (Moog ladder) parameter change."""
        cfg, inst = self._resolve_bus("filter_b", bus)
        setattr(cfg, param, value)
        if inst:
            inst.update_from_state(cfg)

    def _gui_effect_route(self, effect_name: str, route: int):
        """Legacy handler — routing is now implicit from rack placement.

        Kept for OSC/MIDI compatibility; stores the value in config but
        the engine uses per-bus in_rack flags instead.
        """
        if effect_name == "delay":
            self.config.delay.fx_route = route
        elif effect_name == "tape_delay":
            self.config.tape_delay.fx_route = route
        elif effect_name == "filter_a":
            self.config.filter_a.fx_route = route
        elif effect_name == "filter_b":
            self.config.filter_b.fx_route = route
        elif effect_name == "mod":
            self.config.mod_route = route

    def _gui_mod_param(self, param: str, value, bus: int = 1):
        """Handle modulation effect parameter change."""
        suffix = self._BUS_CFG_SUFFIX.get(bus, "")
        cfg = getattr(self.config, f"mod{suffix}" if suffix else "mod")
        setattr(cfg, param, value)
        if self.engine:
            fx_bus = getattr(self.engine, self._BUS_MOD_MAP[bus])
            fx_bus.update_mod(cfg)

    def _gui_reel_to_reel_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("reel_to_reel", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_cassette_tape_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("cassette_tape", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_vhs_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("vhs", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_vinyl_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("vinyl", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_compressor_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("compressor", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_glue_compressor_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("glue_compressor", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_hall_reverb_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("hall_reverb", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_bit_crusher_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("bit_crusher", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_tube_overdrive_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("tube_overdrive", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_ether_drift_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("ether_drift", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_plate_reverb_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("plate_reverb", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)

    def _gui_record_toggle(self, armed: bool):
        if self.engine:
            if armed:
                self.engine.recorder.arm(self.config.tempo,
                                         self.config.recorder.length_bars,
                                         self.config.recorder.length_beats)
            else:
                self.engine.recorder.stop()

    def _on_slice_saved(self, filepath: str):
        """A slice was saved — add it to the sample library."""
        from mlrvp.gui.views.channel_view import ChannelStrip
        ChannelStrip._add_to_library(filepath)

    def _on_rack_contents_changed(self, payload: dict):
        """Sync per-bus effect in-rack status from rack UI."""
        # Register any newly-added FX controls so MIDI learn works on both tabs.
        self._register_fx_rack_mappable_controls()
        if not self.engine:
            return
        pres_a = payload.get("presence_a", {}) if isinstance(payload, dict) else {}
        pres_b = payload.get("presence_b", {}) if isinstance(payload, dict) else {}
        pres_m = payload.get("presence_master", {}) if isinstance(payload, dict) else {}
        pres_c = payload.get("presence_c", {}) if isinstance(payload, dict) else {}
        pres_d = payload.get("presence_d", {}) if isinstance(payload, dict) else {}

        for effect_type, in_rack in pres_a.items():
            self.engine.set_effect_in_rack(effect_type, 1, in_rack)
        for effect_type, in_rack in pres_b.items():
            self.engine.set_effect_in_rack(effect_type, 2, in_rack)
        for effect_type, in_rack in pres_m.items():
            self.engine.set_effect_in_rack(effect_type, 3, in_rack)
        for effect_type, in_rack in pres_c.items():
            self.engine.set_effect_in_rack(effect_type, 4, in_rack)
        for effect_type, in_rack in pres_d.items():
            self.engine.set_effect_in_rack(effect_type, 5, in_rack)

    # --- Setup handlers ---

    def _gui_scan_grids(self, auto_connect: bool = True):
        """Scan for MIDI grid controllers and update the UI.

        If auto_connect is True, automatically connect to the first
        detected device.
        """
        devices = GridController.scan_midi_grids()
        if self.window:
            self.window.setup_view.grid_panel.set_scanned_midi_devices(devices)
            if devices:
                names = ', '.join(d['name'] for d in devices)
                self.window.set_status(f"Found MIDI grids: {names}")
            else:
                self.window.set_status("No MIDI grid controllers found")

        # Auto-connect to first found device
        if auto_connect and devices and not self.grid.connected:
            d = devices[0]
            self._gui_midi_grid_connect(d["in_port"], d["out_port"])

    def _gui_midi_grid_connect(self, in_port: int, out_port: int):
        """Connect to a MIDI grid controller."""
        ok = self.grid.connect_midi_grid(in_port, out_port)
        if ok:
            # Forward MIDI messages from the grid adapter to the MIDI handler
            # so MIDI learn can capture Launchpad button presses
            if self.grid._midi_grid:
                self.grid._midi_grid.set_midi_forward(self.midi.inject_message)
            # Sync grid dimensions from connected hardware
            rows = self.grid.info.rows
            cols = self.grid.info.columns
            self.config.row_count = rows
            self.config.grid_columns = cols
            # Update channel steps to match grid columns
            for ch in self.config.channels:
                ch.steps = cols
            if self.window:
                self.window.channel_view.set_visible_rows(rows)
                self.window.set_grid_dimensions(rows, cols)
                self.window.set_hardware_grid_connected(True)
                # Sync grid columns to all waveform widgets
                self._sync_grid_columns_to_waveforms()
                name = self.grid._midi_grid.device_name if self.grid._midi_grid else "MIDI Grid"
                self.window.setup_view.grid_panel.set_connection_status(True, name)
                self.window.set_status(f"Connected: {name} ({cols}×{rows})")
                self.window._auto_resize_for_rows(rows)
                log.info("Grid connected: %s — set grid to %dx%d", name, cols, rows)
        else:
            if self.window:
                self.window.setup_view.grid_panel.set_connection_status(False)
                self.window.set_status("Failed to connect MIDI grid")

    def _gui_midi_grid_disconnect(self):
        """Disconnect the current MIDI grid controller."""
        self.grid.disconnect_midi_grid()
        if self.window:
            self.window.setup_view.grid_panel.set_connection_status(False)
            self.window.set_hardware_grid_connected(False)
            self.window.set_status("MIDI grid disconnected")

    def _gui_audio_setting(self, key: str, value):
        setattr(self.config, key, value)
        if self.window:
            self.window.set_status(f"Audio: {key} = {value} (restart to apply)")

    def _gui_grid_setting(self, key: str, value):
        if key == "row_count":
            self.config.row_count = value
            if self.window:
                self.window.channel_view.set_visible_rows(value)
                self.window._auto_resize_for_rows(value)
        elif key == "grid_columns":
            self.config.grid_columns = value
            # Sync steps for all channels and recalculate speeds
            for i, ch in enumerate(self.config.channels):
                ch.steps = value
            if self.engine:
                for i in range(self.config.row_count):
                    self.engine.channels[i].state.steps = value
                    self.engine.set_channel_speed(i, self.config.tempo)
            # Sync grid columns to waveform dividers
            self._sync_grid_columns_to_waveforms()
            # Update virtual grid dimensions
            if self.window:
                self.window.set_grid_dimensions(self.config.row_count, value)
        elif key == "show_virtual_grid":
            if self.window:
                self.window.set_virtual_grid_visible(value)
        elif key == "snap_speed_to_rate":
            self.config.snap_speed_to_rate = bool(value)

    def _sync_grid_columns_to_waveforms(self):
        """Push per-channel steps to each waveform widget."""
        if not self.window:
            return
        for i in range(MAX_ROWS):
            strip = self.window.channel_view.get_strip(i)
            steps = (
                self.config.channels[i].steps
                if i < len(self.config.channels)
                else self.config.grid_columns
            )
            strip._waveform.set_grid_columns(steps)

    def _gui_midi_setting(self, key: str, value):
        """Handle MIDI device selection from the setup panel."""
        if key == "midi_out_device":
            port = int(value)
            if port >= 0:
                self.midi.open_output(port)

    def _gui_osc_setting(self, key: str, value):
        """Handle OSC setting changes from the setup panel."""
        setattr(self.config, key, value)
        if key == "osc_enabled":
            if value:
                # Start OSC
                if self.osc is None:
                    self.init_osc()
                if self.osc:
                    try:
                        self.osc.start()
                        log.info("OSC started")
                    except Exception as e:
                        log.warning("OSC start failed: %s", e)
            else:
                # Stop OSC
                if self.osc:
                    self.osc.stop()
                    self.osc = None
                    log.info("OSC stopped")

    # ------------------------------------------------------------------ #
    #  Device population                                                  #
    # ------------------------------------------------------------------ #

    def _populate_gui_devices(self):
        """Fill device combo boxes in setup panels."""
        if not self.window:
            return

        # Audio devices
        try:
            devices = AudioEngine.get_devices()
            out_names = [d["name"] for d in devices["output"]]
            in_names = [d["name"] for d in devices["input"]]
            self.window.setup_view.audio_panel.set_devices(out_names, in_names)

            # Also populate recorder input list
            self.window.tools_view.recorder_panel.set_input_devices(in_names)
        except Exception as e:
            log.warning("Could not enumerate audio devices: %s", e)

        # MIDI devices
        if self.midi:
            self.window.setup_view.midi_panel.set_midi_devices(
                self.midi.available_inputs, self.midi.available_outputs
            )

        # MIDI grid controllers — auto-scan and connect
        self._gui_scan_grids()

        # If no hardware grid connected after scan, show virtual grid
        if not self.grid.connected:
            self.window.set_hardware_grid_connected(False)

        # Sync grid columns to all waveforms
        self._sync_grid_columns_to_waveforms()

        # OSC panel — sync enabled state
        self.window.setup_view.osc_panel.set_enabled_state(self.config.osc_enabled)

    def _register_mappable_controls(self):
        """Register all GUI controls that can be MIDI-mapped."""
        if not self.window:
            return

        w = self.window

        # Channel strip controls
        for i in range(MAX_ROWS):
            strip = w.channel_view.get_strip(i)
            self._midi_map.register_control(
                strip._gain_fader, f"ch.{i}.gain", f"Ch{i+1} Gain")
            self._midi_map.register_control(
                strip._mute_btn, f"ch.{i}.mute", f"Ch{i+1} Mute")
            self._midi_map.register_control(
                strip._stop_btn, f"ch.{i}.stop", f"Ch{i+1} Stop")

        # Mixer group controls
        for i in range(NUM_GROUPS):
            gc = w.mixer_view.get_group_channel(i)
            self._midi_map.register_control(
                gc._vol_fader, f"grp.{i}.volume", f"Group{i+1} Vol")
            self._midi_map.register_control(
                gc._send_fader, f"grp.{i}.send", f"Group{i+1} Send")
            self._midi_map.register_control(
                gc._mute_btn, f"grp.{i}.mute", f"Group{i+1} Mute")
            self._midi_map.register_control(
                gc._fx_route_btn, f"grp.{i}.fx_route", f"Group{i+1} FX Route")

        # Channel strip rate buttons and group select buttons
        for i in range(MAX_ROWS):
            strip = w.channel_view.get_strip(i)
            self._midi_map.register_control(
                strip._half_btn, f"ch.{i}.rate_half", f"Ch{i+1} Rate -½")
            self._midi_map.register_control(
                strip._double_btn, f"ch.{i}.rate_double", f"Ch{i+1} Rate +2x")
            for gi, gbtn in enumerate(strip._group_btns):
                self._midi_map.register_control(
                    gbtn, f"ch.{i}.group.{gi}", f"Ch{i+1} Group {gi+1}")

        # DAC button
        self._midi_map.register_control(
            w.mixer_view._dac_btn, "master.dac", "DAC On/Off")

        # Transport BPM +/- buttons, tap tempo, and sync
        self._midi_map.register_control(
            w.transport._tempo_down_btn, "transport.tempo_down", "Tempo -")
        self._midi_map.register_control(
            w.transport._tempo_up_btn, "transport.tempo_up", "Tempo +")
        self._midi_map.register_control(
            w.transport._tap_btn, "transport.tap_tempo", "Tap Tempo")
        self._midi_map.register_control(
            w.transport._sync_btn, "transport.sync_bpm", "Sync BPM")

        # Group stop buttons and ALL stop
        for gi in range(4):
            self._midi_map.register_control(
                w.transport._grp_stop_btns[gi],
                f"transport.group_stop.{gi}",
                f"Group{gi+1} Stop")
        self._midi_map.register_control(
            w.transport._all_stop_btn, "transport.all_stop", "All Stop")

        # Pattern buttons (label button = cycle state)
        for pi in range(NUM_PATTERN_RECORDERS):
            pb = w.transport._pattern_btns[pi]
            self._midi_map.register_control(
                pb._label_btn, f"transport.pattern.{pi}", f"Pattern{pi+1}")

        # FX rack controls (both rack A and B, actual modules currently present)
        self._register_fx_rack_mappable_controls()

        ev = w.effects_view

        # Effect route buttons (may not exist in rack UI)
        route_btns = getattr(ev, '_route_btns', {})
        for name in ("delay", "tape_delay", "filter_a", "filter_b", "mod"):
            if name in route_btns:
                self._midi_map.register_control(
                    route_btns[name], f"effect.{name}_route", f"{name} FX Route")

        # Master filter controls
        mf = w.master_filter_view
        self._midi_map.register_control(mf._hp_knob, "master_filter.hp", "Master HP")
        self._midi_map.register_control(mf._lp_knob, "master_filter.lp", "Master LP")

        # Mixer aux / master controls
        self._midi_map.register_control(
            w.mixer_view._aux_fader, "aux.volume", "Aux Volume")
        self._midi_map.register_control(
            w.mixer_view._aux_send_fader, "aux.send", "Aux Send Level")
        self._midi_map.register_control(
            w.mixer_view._aux_mute_btn, "aux.mute", "Aux Mute")
        self._midi_map.register_control(
            w.mixer_view._aux_fx_route_btn, "aux.fx_route", "Aux FX Route")
        self._midi_map.register_control(
            w.mixer_view._master_fader, "master.volume", "Master Volume")

        # Wire learn events
        self._midi_map.mapping_started.connect(self._on_midi_map_learn_start)
        self._midi_map.mapping_completed.connect(self._on_midi_map_learn_done)

    def _register_fx_rack_mappable_controls(self):
        """Register all currently visible FX rack module controls for MIDI learn."""
        if not self.window:
            return
        ev = self.window.effects_view
        fx_specs = {
            "delay": {
                "_enabled": "delay.enabled",
                "_mode": "delay.time_mode",
                "_sync": "delay.sync_time",
                "_fb_gain": "delay.gain",
                "_lp": "delay.filter_freq_lp",
            },
            "tape_delay": {
                "_enabled": "tape_delay.enabled",
                "_sync": "tape_delay.sync_time",
                "_feedback": "tape_delay.feedback",
                "_wow_flutter": "tape_delay.wow_flutter",
                "_saturation": "tape_delay.saturation",
                "_tone": "tape_delay.tone",
                "_gain": "tape_delay.gain",
            },
            "filter_a": {
                "_enabled": "filter_a.enabled",
                "_lp_chk": "filter_a.lp_enabled",
                "_hp_chk": "filter_a.hp_enabled",
                "_lf_chk": "filter_a.lf_enabled",
                "_hf_chk": "filter_a.hf_enabled",
                "_lp_freq": "filter_a.lp_freq",
                "_hp_freq": "filter_a.hp_freq",
                "_lp_q": "filter_a.lp_q",
                "_hp_q": "filter_a.hp_q",
                "_lo_mid_gain": "filter_a.lo_mid_gain",
                "_lo_mid_freq": "filter_a.lo_mid_freq",
                "_mid_gain": "filter_a.mid_gain",
                "_mid_freq": "filter_a.mid_freq",
                "_mid_q": "filter_a.mid_q",
                "_hi_mid_gain": "filter_a.hi_mid_gain",
                "_hi_mid_freq": "filter_a.hi_mid_freq",
                "_hi_mid_q": "filter_a.hi_mid_q",
                "_drive": "filter_a.drive",
                "_output": "filter_a.output_gain",
            },
            "filter_b": {
                "_enabled": "filter_b.enabled",
                "_cutoff": "filter_b.cutoff",
                "_resonance": "filter_b.resonance",
                "_drive": "filter_b.drive",
            },
            "phaser": {
                "_enabled": "mod.phaser_enabled",
                "_rate": "mod.phaser_rate",
                "_depth": "mod.phaser_depth",
                "_fb": "mod.phaser_feedback",
            },
            "chorus": {
                "_enabled": "mod.chorus_enabled",
                "_rate": "mod.chorus_rate",
                "_depth": "mod.chorus_depth",
                "_mix": "mod.chorus_mix",
            },
            "flanger": {
                "_enabled": "mod.flanger_enabled",
                "_rate": "mod.flanger_rate",
                "_depth": "mod.flanger_depth",
                "_fb": "mod.flanger_feedback",
                "_mix": "mod.flanger_mix",
            },
            "reel_to_reel": {
                "_enabled": "reel_to_reel.enabled",
                "_speed": "reel_to_reel.tape_speed",
                "_saturation": "reel_to_reel.saturation",
                "_flutter": "reel_to_reel.flutter",
                "_bias": "reel_to_reel.bias",
                "_tone": "reel_to_reel.tone",
                "_mix": "reel_to_reel.mix",
            },
            "cassette_tape": {
                "_enabled": "cassette_tape.enabled",
                "_saturation": "cassette_tape.saturation",
                "_hiss": "cassette_tape.hiss",
                "_punch": "cassette_tape.punch",
                "_warp": "cassette_tape.warp",
                "_tone": "cassette_tape.tone",
                "_mix": "cassette_tape.mix",
            },
            "vhs": {
                "_enabled": "vhs.enabled",
                "_wobble": "vhs.wobble",
                "_dropout": "vhs.dropout",
                "_worn": "vhs.worn",
                "_noise": "vhs.noise",
                "_mix": "vhs.mix",
            },
            "vinyl": {
                "_enabled": "vinyl.enabled",
                "_crackle": "vinyl.crackle",
                "_pops": "vinyl.pops",
                "_warmth": "vinyl.warmth",
                "_wear": "vinyl.wear",
                "_mix": "vinyl.mix",
            },
            "compressor": {
                "_enabled": "compressor.enabled",
                "_threshold": "compressor.threshold",
                "_ratio": "compressor.ratio",
                "_attack": "compressor.attack",
                "_release": "compressor.release",
                "_makeup_gain": "compressor.makeup_gain",
                "_thrust_btn": "compressor.thrust_enabled",
                "_sc_hp": "compressor.sc_hp_freq",
                "_sc_source": "compressor.sidechain_source",
            },
            "hall_reverb": {
                "_enabled": "hall_reverb.enabled",
                "_room_size": "hall_reverb.room_size",
                "_decay": "hall_reverb.decay",
                "_diffusion": "hall_reverb.diffusion",
                "_mix": "hall_reverb.mix",
                "_room_combo": "hall_reverb.room_type",
            },
            "plate_reverb": {
                "_enabled": "plate_reverb.enabled",
                "_decay": "plate_reverb.decay",
                "_predelay": "plate_reverb.pre_delay",
                "_damping": "plate_reverb.damping",
                "_mix": "plate_reverb.mix",
            },
            "glue_compressor": {
                "_enabled": "glue_compressor.enabled",
                "_threshold": "glue_compressor.threshold",
                "_makeup_gain": "glue_compressor.makeup_gain",
                "_attack_sel": "glue_compressor.attack_idx",
                "_ratio_sel": "glue_compressor.ratio_idx",
                "_release_sel": "glue_compressor.release_idx",
                "_hpf_sel": "glue_compressor.hpf_idx",
            },
            "bit_crusher": {
                "_enabled": "bit_crusher.enabled",
                "_bits": "bit_crusher.bit_depth",
                "_crush": "bit_crusher.sample_rate_reduction",
                "_mix": "bit_crusher.mix",
            },
            "tube_overdrive": {
                "_enabled": "tube_overdrive.enabled",
                "_drive": "tube_overdrive.drive",
                "_tone": "tube_overdrive.tone",
                "_bias": "tube_overdrive.tube_bias",
                "_mix": "tube_overdrive.mix",
            },
            "ether_drift": {
                "_enabled": "ether_drift.enabled",
                "_rate": "ether_drift.rate",
                "_depth": "ether_drift.depth",
                "_fdbk": "ether_drift.feedback",
                "_spread": "ether_drift.spread",
                "_mix": "ether_drift.mix",
                "_sync_btn": "ether_drift.sync",
            },
        }

        for rack_name, rack in (("A", ev._rack_a), ("B", ev._rack_b), ("M", ev._rack_m), ("C", ev._rack_c), ("D", ev._rack_d)):
            for mi, mod in enumerate(rack.get_modules()):
                et = getattr(mod, "effect_type", "")
                spec = fx_specs.get(et)
                if not spec:
                    continue
                for attr, key in spec.items():
                    ctrl = mod.power_widget if attr == "_enabled" else getattr(mod, attr, None)
                    if ctrl is not None:
                        self._midi_map.register_control(
                            ctrl,
                            key,
                            f"FX {rack_name} {et} {attr[1:]} {mi+1}",
                        )

    def _gui_toggle_midi_map_mode(self, active: bool):
        """Toggle MIDI mapping mode on/off."""
        if active:
            self._midi_map.activate()
            if self.window:
                self.window.set_status("MIDI Map Mode — Click a control, then move a MIDI knob/button")
        else:
            self._midi_map.deactivate()
            if self.window:
                self.window.set_status("MIDI Map Mode off")
        # Sync both MIDI map / learn buttons
        if self.window:
            self.window.effects_view.set_midi_map_active(active)
            self.window.setup_view.set_midi_learn_active(active)
            self.window.transport.set_midi_map_active(active)

    def _on_midi_map_learn_start(self, param_path: str):
        """Start MIDI learn for a parameter."""
        self.midi.start_learn(param_path)
        if self.window:
            self.window.set_status(f"Waiting for MIDI input for: {param_path}")

    def _on_midi_learn_complete(self, param_path: str, channel: int, cc: int):
        """MIDI learn completed — register the mapping callback."""
        self._midi_map.on_learn_complete(param_path, channel, cc)
        self._register_midi_param_callback(param_path)
        # Add row to setup view mapping table
        if self.window:
            label = param_path
            for ctrl in self._midi_map._controls:
                if ctrl.param_path == param_path:
                    label = ctrl.label
                    break
            self.window.setup_view.midi_panel.add_mapping({
                "param_path": param_path,
                "cc": cc,
                "channel": channel,
                "label": label,
                "min_val": 0.0,
                "max_val": 1.0,
                "toggle": False,
            })
            self.window.set_status(f"Mapped: {param_path} → CC{cc} Ch{channel+1}")

    def _on_midi_map_learn_done(self, param_path: str, channel: int, cc: int):
        """Final notification after learn overlay updates."""
        pass

    def _gui_midi_range_changed(self, param_path: str, min_val: float, max_val: float):
        """User edited min/max range in the mapping table."""
        self.midi.update_mapping_range(param_path, min_val, max_val)

    def _gui_midi_mapping_removed(self, param_path: str):
        """User deleted a mapping row from the table."""
        self.midi.remove_mapping(param_path)

    def _gui_midi_toggle_changed(self, param_path: str, toggle: bool):
        """User toggled the toggle checkbox in the mapping table."""
        self.midi.update_mapping_toggle(param_path, toggle)

    def _gui_tilt_mapping_changed(self, axis: int, target_param: str):
        """User changed tilt axis target assignment."""
        axis_names = ["x", "y", "z"]
        src = f"tilt.0.{axis_names[axis]}"
        if target_param:
            self._tilt_mappings[src] = target_param
            # Make sure the target param has a callback registered
            self._register_midi_param_callback(target_param)
        else:
            self._tilt_mappings.pop(src, None)

    def _gui_midi_input_added(self, port: int):
        """User added a new MIDI input controller."""
        self.midi.open_input(port)
        name = self.midi.available_inputs[port] if port < len(self.midi.available_inputs) else "?"
        if self.window:
            self.window.set_status(f"MIDI input opened: {name}")

    def _gui_midi_input_removed(self, port: int):
        """User removed a MIDI input controller."""
        self.midi.close_input(port)
        if self.window:
            self.window.set_status("MIDI input closed")

    def _gui_tilt_enabled_changed(self, enabled: bool):
        """User toggled the tilt control mapping checkbox."""
        self.grid.enable_tilt(enabled)
        if self.window:
            self.window.setup_view.midi_panel.set_tilt_visible(enabled)

    def _populate_tilt_targets(self):
        """Populate tilt combo boxes with all registered mappable params."""
        if not self.window:
            return
        targets = [(c.param_path, c.label) for c in self._midi_map._controls]
        self.window.setup_view.midi_panel.set_tilt_targets(targets)

    def _refresh_midi_mapping_table(self):
        """Rebuild the mapping table in the setup view from current state."""
        if not self.window:
            return
        mappings = self.midi.get_mappings_serializable()
        # Add labels from registered controls
        label_map = {c.param_path: c.label for c in self._midi_map._controls}
        for m in mappings:
            m["label"] = label_map.get(m["param_path"], m["param_path"])
        self.window.setup_view.midi_panel.refresh_mappings(mappings)

    def _register_midi_param_callback(self, param_path: str):
        """Register a callback so incoming MIDI CC controls the parameter."""
        parts = param_path.split(".")
        if parts[0] == "ch" and len(parts) == 3:
            row = int(parts[1])
            param = parts[2]
            if param == "gain":
                self.midi.set_param_callback(param_path,
                    lambda v, r=row: self._gui_channel_param(r, "gain", v))
            elif param == "speed":
                self.midi.set_param_callback(param_path,
                    lambda v, r=row: self._gui_channel_param(r, "speed", v * 4.0))
            elif param == "mute":
                self.midi.set_param_callback(param_path,
                    lambda v, r=row: self._gui_channel_param(r, "mute", v > 0.5))
            elif param == "rate_half":
                self.midi.set_param_callback(param_path,
                    lambda v, r=row: QTimer.singleShot(
                        0, lambda: self.window.channel_view.get_strip(r)._on_half_speed()
                    ) if v > 0.5 and self.window else None)
            elif param == "rate_double":
                self.midi.set_param_callback(param_path,
                    lambda v, r=row: QTimer.singleShot(
                        0, lambda: self.window.channel_view.get_strip(r)._on_double_speed()
                    ) if v > 0.5 and self.window else None)
            elif param == "stop":
                self.midi.set_param_callback(param_path,
                    lambda v, r=row: QTimer.singleShot(
                        0, lambda: self._gui_channel_param(r, "stop", True)
                    ) if v > 0.5 else None)
        elif parts[0] == "grp" and len(parts) == 3:
            grp = int(parts[1])
            param = parts[2]
            if param == "volume":
                self.midi.set_param_callback(param_path,
                    lambda v, g=grp: self._gui_group_volume(g, v))
            elif param == "send":
                self.midi.set_param_callback(param_path,
                    lambda v, g=grp: self._gui_group_send(g, v))
            elif param == "mute":
                self.midi.set_param_callback(param_path,
                    lambda v, g=grp: self._gui_group_mute(g, v > 0.5))
            elif param == "fx_route":
                self.midi.set_param_callback(param_path,
                    lambda v, g=grp: self._gui_group_fx_route(g, int(round(v * 2))))
        elif param_path == "transport.tempo":
            self.midi.set_param_callback(param_path,
                lambda v: self._gui_tempo_changed(20.0 + v * 280.0))
        elif param_path == "transport.tempo_down":
            self.midi.set_param_callback(param_path,
                lambda v: QTimer.singleShot(
                    0, self.window.transport._tempo_spin.stepDown
                ) if v > 0.5 else None)
        elif param_path == "transport.tempo_up":
            self.midi.set_param_callback(param_path,
                lambda v: QTimer.singleShot(
                    0, self.window.transport._tempo_spin.stepUp
                ) if v > 0.5 else None)
        elif param_path == "transport.tap_tempo":
            self.midi.set_param_callback(param_path,
                lambda v: QTimer.singleShot(
                    0, self.window.transport._tap_btn.click
                ) if v > 0.5 else None)
        elif param_path == "transport.sync_bpm":
            self.midi.set_param_callback(param_path,
                lambda v: QTimer.singleShot(
                    0, self._gui_sync_bpm
                ) if v > 0.5 else None)
        elif parts[0] == "transport" and len(parts) == 3:
            if parts[1] == "group_stop":
                gi = int(parts[2])
                self.midi.set_param_callback(param_path,
                    lambda v, g=gi: QTimer.singleShot(
                        0, lambda: self._gui_group_stop(g)
                    ) if v > 0.5 else None)
            elif parts[1] == "pattern":
                pi = int(parts[2])
                self.midi.set_param_callback(param_path,
                    lambda v, p=pi: QTimer.singleShot(
                        0, lambda: self._on_control_pattern_toggle(p)
                    ) if v > 0.5 else None)
        elif parts[0] == "filter_a" and len(parts) == 2:
            param = parts[1]
            if param == "enabled":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("enabled", v > 0.5))
            elif param == "lp_enabled":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("lp_enabled", v > 0.5))
            elif param == "hp_enabled":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("hp_enabled", v > 0.5))
            elif param == "lf_enabled":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("lf_enabled", v > 0.5))
            elif param == "hf_enabled":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("hf_enabled", v > 0.5))
            elif param == "lp_freq":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("lp_freq", 1500.0 + v * 18500.0))
            elif param == "hp_freq":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("hp_freq", 16.0 + v * 334.0))
            elif param == "lp_q":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("lp_q", v))
            elif param == "hp_q":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("hp_q", v))
            elif param in ("lo_mid_gain", "mid_gain", "mid_q", "hi_mid_gain",
                           "hi_mid_q", "drive", "output_gain"):
                self.midi.set_param_callback(param_path,
                    lambda v, p=param: self._gui_filter_a_param(p, v))
            elif param == "lo_mid_freq":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("lo_mid_freq", 30.0 + v * 470.0))
            elif param == "mid_freq":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("mid_freq", 200.0 + v * 2300.0))
            elif param == "hi_mid_freq":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_a_param("hi_mid_freq", 600.0 + v * 7400.0))
        elif parts[0] == "filter_b" and len(parts) == 2:
            param = parts[1]
            if param == "enabled":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_b_param("enabled", v > 0.5))
            elif param == "cutoff":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_b_param("cutoff", v * 17960.0 + 40.0))
            elif param == "resonance":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_b_param("resonance", v))
            elif param == "drive":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_filter_b_param("drive", v))
        elif parts[0] == "tape_delay" and len(parts) == 2:
            param = parts[1]
            if param == "enabled":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_tape_delay_param("enabled", v > 0.5))
            elif param == "sync_time":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_tape_delay_param("sync_time", int(round(v * 12))))
            else:
                self.midi.set_param_callback(param_path,
                    lambda v, p=param: self._gui_tape_delay_param(p, v))
        elif parts[0] == "mod" and len(parts) == 2:
            param = parts[1]
            if param in ("phaser_enabled", "chorus_enabled", "flanger_enabled"):
                self.midi.set_param_callback(param_path,
                    lambda v, p=param: self._gui_mod_param(p, v > 0.5))
            else:
                self.midi.set_param_callback(param_path,
                    lambda v, p=param: self._gui_mod_param(p, v))
        elif parts[0] == "delay" and len(parts) == 2:
            param = parts[1]
            if param == "enabled":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_delay_param("enabled", v > 0.5))
            elif param in ("fb_attack", "fb_release", "fb_range_low", "fb_range_high"):
                self.midi.set_param_callback(param_path,
                    lambda v, p=param: self._gui_delay_param(p, v * 100.0))
            elif param == "gain":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_delay_param("gain", v))
            elif param == "time_mode":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_delay_param("time_mode", int(round(v))))
            elif param == "sync_time":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_delay_param("sync_time", int(round(v * 12))))
            elif param == "filter_lp":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_delay_param("filter_lp", v > 0.5))
            elif param == "filter_hp":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_delay_param("filter_hp", v > 0.5))
            elif param == "lp_freq":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_delay_param("filter_freq_lp", v * 10000.0 + 100.0))
            elif param == "hp_freq":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_delay_param("filter_freq_hp", v * 5000.0 + 20.0))
            elif param == "filter_freq_lp":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_delay_param("filter_freq_lp", v * 10000.0 + 100.0))
        elif parts[0] == "master_filter" and len(parts) == 2:
            if parts[1] == "hp":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_master_hp(v))
            elif parts[1] == "lp":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_master_lp(v))
        elif parts[0] == "aux" and len(parts) == 2:
            if parts[1] == "volume":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_aux_volume(v))
            elif parts[1] == "send":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_aux_send(v))
            elif parts[1] == "mute":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_aux_mute(v > 0.5))
            elif parts[1] == "fx_route":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_aux_fx_route(int(round(v * 2))))
        elif parts[0] == "master" and len(parts) == 2:
            if parts[1] == "volume":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_master_volume(v))
            elif parts[1] == "dac":
                self.midi.set_param_callback(param_path,
                    lambda v: QTimer.singleShot(
                        0, self.window.mixer_view._dac_btn.click
                    ) if v > 0.5 else None)
        elif parts[0] == "effect" and len(parts) == 2:
            effect_name = parts[1].replace("_route", "")
            self.midi.set_param_callback(param_path,
                lambda v, e=effect_name: self._gui_effect_route(e, int(round(v * 2))))
        elif parts[0] in ("reel_to_reel", "cassette_tape", "vhs", "vinyl",
                           "compressor", "hall_reverb", "plate_reverb",
                           "glue_compressor", "bit_crusher", "tube_overdrive",
                           "ether_drift") and len(parts) == 2:
            fx_n = parts[0]
            param = parts[1]
            handler_fn = getattr(self, f"_gui_{fx_n}_param")
            # Boolean params
            if param in ("enabled", "thrust_enabled", "sync"):
                self.midi.set_param_callback(param_path,
                    lambda v, fn=handler_fn, p=param: fn(p, v > 0.5))
            # Compressor scaled params
            elif fx_n == "compressor":
                if param == "threshold":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("threshold", -60.0 + v * 60.0))
                elif param == "ratio":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("ratio", 1.0 + v * 19.0))
                elif param == "attack":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("attack", 0.1 + v * 199.9))
                elif param == "release":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("release", 10.0 + v * 990.0))
                elif param == "makeup_gain":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("makeup_gain", v * 30.0))
                elif param == "sc_hp_freq":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("sc_hp_freq", 20.0 + v * 1980.0))
                elif param == "sidechain_source":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("sidechain_source", int(round(v * 3))))
                else:
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn, p=param: fn(p, v))
            # Glue compressor scaled params
            elif fx_n == "glue_compressor":
                if param == "threshold":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("threshold", -30.0 + v * 45.0))
                elif param == "makeup_gain":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("makeup_gain", v * 15.0))
                elif param == "ratio_idx":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("ratio_idx", int(round(v * 2))))
                elif param == "attack_idx":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("attack_idx", int(round(v * 5))))
                elif param == "release_idx":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("release_idx", int(round(v * 3))))
                elif param == "hpf_idx":
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn: fn("hpf_idx", int(round(v * 5))))
                else:
                    self.midi.set_param_callback(param_path,
                        lambda v, fn=handler_fn, p=param: fn(p, v))
            # Hall reverb integer param
            elif fx_n == "hall_reverb" and param == "room_type":
                self.midi.set_param_callback(param_path,
                    lambda v, fn=handler_fn: fn("room_type", int(round(v * 4))))
            # Plate reverb pre-delay (0-200 ms)
            elif fx_n == "plate_reverb" and param == "pre_delay":
                self.midi.set_param_callback(param_path,
                    lambda v, fn=handler_fn: fn("pre_delay", v * 200.0))
            # Bit crusher integer bit depth
            elif fx_n == "bit_crusher" and param == "bit_depth":
                self.midi.set_param_callback(param_path,
                    lambda v, fn=handler_fn: fn("bit_depth", max(1, round(v * 32))))
            # Ether drift route (0-2 integer)
            elif fx_n == "ether_drift" and param == "route":
                self.midi.set_param_callback(param_path,
                    lambda v, fn=handler_fn: fn("route", int(round(v * 2))))
            # Reel-to-reel tape speed (0-3 integer)
            elif fx_n == "reel_to_reel" and param == "tape_speed":
                self.midi.set_param_callback(param_path,
                    lambda v, fn=handler_fn: fn("tape_speed", int(round(v * 3))))
            else:
                # 0-1 float pass-through for all remaining continuous params
                self.midi.set_param_callback(param_path,
                    lambda v, fn=handler_fn, p=param: fn(p, v))
        elif parts[0] == "tilt" and len(parts) == 3:
            # Tilt axes are source-only — no callback needed (data flows in)
            pass

        # Post-wrap: for knob/fader widgets that are registered in the MIDI map,
        # call widget.set_value(raw_v) on the Qt thread so the visual position
        # updates and the value popup fires.
        _w = self._midi_map.get_widget(param_path)
        if _w is not None and hasattr(_w, 'show_value_popup'):
            _existing = self.midi.get_param_callback(param_path)
            if _existing is not None:
                def _wrapped(v, _fn=_existing, _widget=_w):
                    _fn(v)  # update config + engine (thread-safe)
                    # Schedule widget update on the Qt main thread
                    QTimer.singleShot(0, lambda w=_widget, val=v: w.set_value(val))
                self.midi.set_param_callback(param_path, _wrapped)

    # ------------------------------------------------------------------ #
    #  State sync helpers                                                 #
    # ------------------------------------------------------------------ #

    def _apply_config_to_engine(self):
        """Push current config to the audio engine after preset recall."""
        if not self.engine:
            return

        self.engine.set_tempo(self.config.tempo)
        self.engine.set_master_volume(self.config.master_volume)
        self.engine.set_aux_volume(self.config.aux_volume)
        self.engine.mixer.aux_mute = self.config.aux_mute
        self.engine.mixer.aux_fx_route = self.config.aux_fx_route
        self.engine.mixer.aux_send_level = self.config.aux_send_level

        for i, grp in enumerate(self.config.groups):
            self.engine.set_group_volume(i, grp.volume)
            self.engine.set_group_send(i, grp.send)
            self.engine.set_group_mute(i, grp.mute)
            self.engine.mixer.groups[i].fx_route = grp.fx_route

        # FX: sync per-bus instances from per-bus config
        _suffixes = ("", "_b", "_m", "_c", "_d")
        for sfx in _suffixes:
            fa = getattr(self.engine, f"filter_a{sfx}")
            fa.update_from_state(getattr(self.config, f"filter_a{sfx}"))
            fb = getattr(self.engine, f"filter_b{sfx}")
            fb.update_from_state(getattr(self.config, f"filter_b{sfx}"))

        for bus_key, cfg_sfx in (("fx_bus_1", ""), ("fx_bus_2", "_b"),
                                  ("fx_bus_m", "_m"), ("fx_bus_c", "_c"),
                                  ("fx_bus_d", "_d")):
            getattr(self.engine, bus_key).update_mod(
                getattr(self.config, f"mod{cfg_sfx}"))

        # Sync delay/tape_delay state per-bus
        for sfx in _suffixes:
            for attr, val in vars(getattr(self.config, f"delay{sfx}")).items():
                if not attr.startswith('_'):
                    setattr(getattr(self.engine, f"delay{sfx}").state, attr, val)
            getattr(self.engine, f"delay{sfx}")._update_filters()
            for attr, val in vars(getattr(self.config, f"tape_delay{sfx}")).items():
                if not attr.startswith('_'):
                    setattr(getattr(self.engine, f"tape_delay{sfx}").state, attr, val)

        # Sync remaining effect states across all buses
        for fx_name in ("reel_to_reel", "cassette_tape", "vhs", "vinyl", "compressor",
                        "hall_reverb", "plate_reverb", "glue_compressor",
                        "bit_crusher", "tube_overdrive", "ether_drift"):
            for sfx in _suffixes:
                cfg_key = f"{fx_name}{sfx}"
                cfg = getattr(self.config, cfg_key)
                eng = getattr(self.engine, cfg_key)
                for attr, val in vars(cfg).items():
                    if not attr.startswith('_'):
                        setattr(eng.state, attr, val)

        # Re-sync in-rack flags from current rack layout
        if self.window:
            self._on_rack_contents_changed(
                self.window.effects_view.get_rack_sync_state())

        for i, ch in enumerate(self.config.channels):
            ech = self.engine.channels[i]
            ech.state.speed = ch.speed
            ech.state.mode = ch.mode
            ech.state.gain = ch.gain
            ech.state.group = ch.group
            ech.state.mute = ch.mute
            ech.state.sel_start = ch.sel_start
            ech.state.sel_end = ch.sel_end
            ech.state.attack = ch.attack
            ech.state.release = ch.release
            ech.state.transpose = ch.transpose

    def _apply_config_to_engine_live(self, playing_rows: set[int]):
        """Push config to engine, but protect playing channels from state
        clobber (sel_start/sel_end/group/mode/speed stay intact while
        the channel is still outputting audio)."""
        if not self.engine:
            return

        self.engine.set_tempo(self.config.tempo)
        self.engine.set_master_volume(self.config.master_volume)
        self.engine.set_aux_volume(self.config.aux_volume)
        self.engine.mixer.aux_mute = self.config.aux_mute
        self.engine.mixer.aux_fx_route = self.config.aux_fx_route
        self.engine.mixer.aux_send_level = self.config.aux_send_level

        for i, grp in enumerate(self.config.groups):
            self.engine.set_group_volume(i, grp.volume)
            self.engine.set_group_send(i, grp.send)
            self.engine.set_group_mute(i, grp.mute)
            self.engine.mixer.groups[i].fx_route = grp.fx_route

        # FX: sync both bus instances from config
        # FX: sync per-bus instances from per-bus config
        _suffixes = ("", "_b", "_m", "_c", "_d")
        for sfx in _suffixes:
            fa = getattr(self.engine, f"filter_a{sfx}")
            fa.update_from_state(getattr(self.config, f"filter_a{sfx}"))
            fb = getattr(self.engine, f"filter_b{sfx}")
            fb.update_from_state(getattr(self.config, f"filter_b{sfx}"))

        for bus_key, cfg_sfx in (("fx_bus_1", ""), ("fx_bus_2", "_b"),
                                  ("fx_bus_m", "_m"), ("fx_bus_c", "_c"),
                                  ("fx_bus_d", "_d")):
            getattr(self.engine, bus_key).update_mod(
                getattr(self.config, f"mod{cfg_sfx}"))

        # Sync delay/tape_delay state per-bus
        for sfx in _suffixes:
            for attr, val in vars(getattr(self.config, f"delay{sfx}")).items():
                if not attr.startswith('_'):
                    setattr(getattr(self.engine, f"delay{sfx}").state, attr, val)
            getattr(self.engine, f"delay{sfx}")._update_filters()
            for attr, val in vars(getattr(self.config, f"tape_delay{sfx}")).items():
                if not attr.startswith('_'):
                    setattr(getattr(self.engine, f"tape_delay{sfx}").state, attr, val)

        # Sync remaining effect states across all buses
        for fx_name in ("reel_to_reel", "cassette_tape", "vhs", "vinyl", "compressor",
                        "hall_reverb", "plate_reverb", "glue_compressor",
                        "bit_crusher", "tube_overdrive", "ether_drift"):
            for sfx in _suffixes:
                cfg_key = f"{fx_name}{sfx}"
                cfg = getattr(self.config, cfg_key)
                eng = getattr(self.engine, cfg_key)
                for attr, val in vars(cfg).items():
                    if not attr.startswith('_'):
                        setattr(eng.state, attr, val)

        # Re-sync in-rack flags from current rack layout
        if self.window:
            self._on_rack_contents_changed(
                self.window.effects_view.get_rack_sync_state())

        for i, ch in enumerate(self.config.channels):
            ech = self.engine.channels[i]
            if i in playing_rows:
                # Only safe to update gain/mute/attack/release while playing
                ech.state.gain = ch.gain
                ech.state.mute = ch.mute
                ech.state.attack = ch.attack
                ech.state.release = ch.release
            else:
                ech.state.speed = ch.speed
                ech.state.mode = ch.mode
                ech.state.gain = ch.gain
                ech.state.group = ch.group
                ech.state.mute = ch.mute
                ech.state.sel_start = ch.sel_start
                ech.state.sel_end = ch.sel_end
                ech.state.attack = ch.attack
                ech.state.release = ch.release
            ech.state.transpose = ch.transpose

    def _apply_pitch_shift_async(self, row: int, semitones: int):
        """Pitch-shift the channel's sample in a background thread using librosa.

        When semitones == 0, the pitch cache is cleared immediately (no thread needed).
        The channel plays unshifted raw audio until the background thread completes.
        """
        if not self.engine:
            return
        ech = self.engine.channels[row]
        if semitones == 0:
            ech.clear_pitch_cache()
            # Source audio changed — scale cache entries are stale
            ech.clear_pitch_scale_cache()
            if ech.state.mode == PlaybackMode.PITCH:
                self._build_pitch_scale_cache_async(row)
            return
        sample = ech.sample
        if sample is None:
            return

        def _worker():
            try:
                import librosa
                import numpy as np
                data = sample.data  # shape: (frames, n_channels)
                sr = ech.sample_rate
                shifted_channels = []
                for c in range(data.shape[1]):
                    shifted = librosa.effects.pitch_shift(
                        data[:, c].astype(np.float32),
                        sr=sr,
                        n_steps=float(semitones),
                    )
                    shifted_channels.append(shifted)
                shifted_data = np.stack(shifted_channels, axis=1).astype(np.float32)
                # Only apply if sample and semitones haven't changed by the time we finish
                if ech.sample is sample and ech.state.transpose == semitones:
                    ech.set_pitch_cache(shifted_data)
                    # Source audio changed — rebuild scale cache from new base.
                    # Pass shifted_data directly because _pending_pitch_cache
                    # hasn't been swapped into _pitch_cache by the audio thread yet.
                    if ech.state.mode == PlaybackMode.PITCH:
                        ech.clear_pitch_scale_cache()
                        self._build_pitch_scale_cache_async(row, source_override=shifted_data)
            except Exception:
                logging.exception("Pitch shift failed for row %d", row)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _build_pitch_scale_cache_async(self, row: int, source_override: 'np.ndarray | None' = None):
        """Pre-compute duration-preserving pitch shifts for each scale semitone.

        Entries accumulate — only semitones not already cached are computed.
        Cleared only when the source audio changes (sample load or transpose).

        Args:
            source_override: If provided, use this audio instead of reading
                ech._pitch_cache.  Needed when called from the transpose
                worker thread where _pending_pitch_cache has been staged
                but the audio thread hasn't swapped it into _pitch_cache yet.
        """
        if not self.engine:
            return
        ech = self.engine.channels[row]
        if ech.sample is None:
            return

        ech._pitch_scale_cache_gen += 1
        gen = ech._pitch_scale_cache_gen

        scale = PITCH_SCALE_MAPS.get(ech.state.pitch_scale, PITCH_SCALE_MAPS[0])
        pitch_root = ech.state.pitch_root
        sample = ech.sample
        sr = ech.sample_rate

        # Source audio: explicit override > transpose cache > raw sample data.
        if source_override is not None:
            source = source_override
        elif ech._pitch_cache is not None:
            source = ech._pitch_cache
        else:
            source = sample.data

        # Only compute semitones for columns that actually exist on the grid.
        num_cols = self.config.grid_columns
        num_scale = min(num_cols, len(scale))

        # Priority order: root first, then musically useful intervals,
        # then remaining columns — all clamped to the actual grid size.
        priority_cols = [c for c in [0, 7, 4, 2] if c < num_scale]
        priority_cols += [c for c in range(num_scale) if c not in priority_cols]
        ordered_semitones = list(dict.fromkeys(
            scale[c] + pitch_root for c in priority_cols
        ))

        # Filter to semitones not already cached
        needed = [s for s in ordered_semitones if s not in ech._pitch_scale_cache]
        if not needed:
            return

        def _worker():
            try:
                import librosa
                import numpy as np
                for semitone in needed:
                    if ech._pitch_scale_cache_gen != gen:
                        return  # stale — a newer build was requested
                    if ech.sample is not sample:
                        return  # sample changed
                    if semitone == 0:
                        # Identity: no pitch shift needed
                        shifted_data = source.copy()
                    else:
                        shifted_channels = []
                        for c in range(source.shape[1]):
                            shifted = librosa.effects.pitch_shift(
                                source[:, c].astype(np.float32),
                                sr=sr,
                                n_steps=float(semitone),
                            )
                            shifted_channels.append(shifted)
                        shifted_data = np.stack(shifted_channels, axis=1).astype(np.float32)
                    # Final stale check after heavy compute
                    if ech._pitch_scale_cache_gen != gen:
                        return
                    if ech.sample is not sample:
                        return
                    ech.set_pitch_scale_entry(semitone, shifted_data)
            except Exception:
                logging.exception("Pitch scale cache build failed for row %d", row)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _load_sample_no_speed_reset(self, row: int, filepath: str):
        """Load a sample into a channel, preserving the preset's stored speed
        instead of auto-calculating from tempo."""
        try:
            sample = load_sample(filepath, self.config.sample_rate)
            if sample is None:
                return
            if self.engine:
                saved_speed = self.config.channels[row].speed
                self.engine.load_sample(row, sample)
                self.config.channels[row].filename = filepath

                from mlrvp.gui.views.channel_view import ChannelStrip
                ChannelStrip._add_to_library(filepath)

                self.config.channels[row].steps = self.config.grid_columns

                # Re-apply pitch shift if transpose was already set
                transpose = self.config.channels[row].transpose
                if transpose != 0:
                    self._apply_pitch_shift_async(row, transpose)
                elif self.config.channels[row].mode == PlaybackMode.PITCH:
                    self._build_pitch_scale_cache_async(row)

                overview = get_waveform_overview(sample, num_points=800)
                if self.window:
                    strip = self.window.channel_view.get_strip(row)
                    import os
                    strip.set_waveform_data(overview, os.path.basename(filepath))
                    strip._waveform.set_grid_columns(self.config.grid_columns)

                # Restore preset speed instead of auto-calculating
                self.engine.channels[row].state.speed = saved_speed
                self.config.channels[row].speed = saved_speed
                if self.window:
                    strip = self.window.channel_view.get_strip(row)
                    strip._true_speed = saved_speed
                    strip._speed_spin.blockSignals(True)
                    strip._speed_spin.setValue(saved_speed)
                    strip._speed_spin.blockSignals(False)

                    self.window.tools_view.slice_panel.add_loaded_sample(filepath)
        except Exception as e:
            log.error("Failed to load sample (live recall): %s", e)

    def _load_waveform_display(self, row: int, filepath: str):
        """Update only the waveform display for a channel (no engine load).
        Used for visual preview when the channel is still playing old audio."""
        try:
            sample = load_sample(filepath, self.config.sample_rate)
            if sample is None:
                return
            overview = get_waveform_overview(sample, num_points=800)
            if self.window:
                import os
                strip = self.window.channel_view.get_strip(row)
                strip.set_waveform_data(overview, os.path.basename(filepath))
                strip._waveform.set_grid_columns(self.config.grid_columns)
        except Exception as e:
            log.error("Failed to load waveform display: %s", e)

    def _reload_samples_from_config(self):
        """Reload audio samples from filepaths stored in config."""
        import os
        for i, ch in enumerate(self.config.channels[:self.config.row_count]):
            if ch.filename and os.path.isfile(ch.filename):
                self.load_sample_to_channel(i, ch.filename)

    def _apply_config_to_gui(self):
        """Push current config to the GUI after preset recall."""
        if not self.window:
            return

        ControlDisplaySettings.notifications_suspended = True
        try:
            w = self.window

            # Transport
            w.transport.set_tempo(self.config.tempo)
            w.transport.set_quantize(int(self.config.quantize))

            # Channel strips
            for i in range(self.config.row_count):
                strip = w.channel_view.get_strip(i)
                strip.set_state(self.config.channels[i])
                # Sync virtual grid row color to group
                w.virtual_grid.set_row_color(i, self.config.channels[i].group)

            w.channel_view.set_visible_rows(self.config.row_count)

            # Mixer groups
            for i, grp in enumerate(self.config.groups):
                gc = w.mixer_view.get_group_channel(i)
                gc.set_volume(grp.volume)
                gc.set_send(grp.send)
                gc.set_mute(grp.mute)
                gc.set_fx_route(grp.fx_route)

            # AUX channel
            w.mixer_view.set_aux_send(self.config.aux_send_level)
            w.mixer_view.set_aux_mute(self.config.aux_mute)
            w.mixer_view.set_aux_fx_route(self.config.aux_fx_route)

            # Effects
            w.effects_view.delay_panel.set_state(self.config.delay)
            w.effects_view.tape_delay_panel.set_state(self.config.tape_delay)
            w.effects_view.filter_a_panel.set_state(self.config.filter_a)
            w.effects_view.filter_b_panel.set_state(self.config.filter_b)
            w.effects_view.mod_panel.set_state(self.config.mod)
            w.effects_view.set_effect_route("delay", self.config.delay.fx_route)
            w.effects_view.set_effect_route("tape_delay", self.config.tape_delay.fx_route)
            w.effects_view.set_effect_route("filter_a", self.config.filter_a.fx_route)
            w.effects_view.set_effect_route("filter_b", self.config.filter_b.fx_route)
            w.effects_view.set_effect_route("mod", self.config.mod_route)

            # Rack modules state recall — push config to every module in all 5 racks
            _MOD_TYPES = {"chorus", "flanger", "phaser"}
            _SKIP_TYPES = {"vu_meter", "oscilloscope"}
            for rack, sfx in ((w.effects_view._rack_a, ""),
                              (w.effects_view._rack_b, "_b"),
                              (w.effects_view._rack_m, "_m"),
                              (w.effects_view._rack_c, "_c"),
                              (w.effects_view._rack_d, "_d")):
                for mod in rack.get_modules():
                    et = getattr(mod, "effect_type", "")
                    if et in _SKIP_TYPES or not et:
                        continue
                    cfg_base = "mod" if et in _MOD_TYPES else et
                    cfg_key = f"{cfg_base}{sfx}" if sfx else cfg_base
                    cfg = getattr(self.config, cfg_key, None)
                    if cfg is not None and hasattr(mod, "set_state"):
                        mod.set_state(cfg)
        finally:
            ControlDisplaySettings.notifications_suspended = False

    # ------------------------------------------------------------------ #
    #  Periodic UI update                                                 #
    # ------------------------------------------------------------------ #

    def _update_ui(self):
        """Push latest audio state to the GUI (~30fps)."""
        if not self.window or not self.engine:
            return

        # Check for async monome connection
        self._check_monome_connect()

        # Beat position display — wraps bars so counter doesn't run forever
        if self.clock:
            bar = (self.clock.bar_position % 4) + 1  # 1-4 repeating cycle
            beat = int(self.clock.beat_in_bar) + 1    # 1-4 within bar
            self.window.transport.set_beat_position(bar, beat)

        # Track which groups have playing channels (for control row LEDs)
        group_active = [False] * NUM_GROUPS

        # Channel playback positions + active column highlighting
        columns = self.config.grid_columns
        for i in range(self.config.row_count):
            ch = self.engine.channels[i]
            strip = self.window.channel_view.get_strip(i)

            # SEQ mode: always show step pattern LEDs regardless of playing state
            if ch.state.mode == PlaybackMode.SEQ and ch.sample is not None:
                if ch._seq_playing_step and ch.state.playing:
                    strip.set_playback_position(ch.position)
                    strip.set_playing(True)
                else:
                    strip.set_playing(False)
                # Waveform active column follows the sequencer step
                strip._waveform.set_active_column(ch._seq_column if ch._seq_column >= 0 else -1)
                self._update_seq_leds(i)
                grp = ch.state.group
                if 0 <= grp < NUM_GROUPS and ch._seq_playing_step:
                    group_active[grp] = True
                self._row_was_playing[i] = True  # prevent clear on next idle frame
                continue

            # PITCH mode: all buttons stay DIM, active column BRIGHT while playing
            if ch.state.mode == PlaybackMode.PITCH and ch.sample is not None:
                active_col = ch._pitch_mode_col if ch.state.playing else -1
                if ch.state.playing:
                    strip.set_playback_position(ch.position)
                    strip.set_playing(True)
                    grp = ch.state.group
                    if 0 <= grp < NUM_GROUPS:
                        group_active[grp] = True
                else:
                    strip.set_playing(False)
                strip._waveform.set_active_column(active_col)
                self._update_pitch_mode_leds(i, active_col=active_col)
                self._row_was_playing[i] = True  # keep DIM LEDs persistent
                continue

            if ch.state.playing:
                strip.set_playback_position(ch.position)
                strip.set_playing(True)
                self._row_was_playing[i] = True
                grp = ch.state.group
                if 0 <= grp < NUM_GROUPS:
                    group_active[grp] = True

                # Update active column on waveform display using state.steps so
                # the highlight sweeps all step divisions, not just grid columns
                waveform_steps = ch.state.steps if ch.state.steps > 0 else columns
                waveform_col = ch.get_grid_column(waveform_steps)
                strip._waveform.set_active_column(waveform_col)

                # Update grid LEDs — map to physical grid columns (hardware)
                col = ch.get_grid_column(columns)
                if col >= 0:
                    sl_col_a = getattr(ch, '_slice_col_start', -1)
                    sl_col_b = getattr(ch, '_slice_col_end', -1)
                    if sl_col_a >= 0 and sl_col_b >= 0:
                        self.grid.update_playback_leds(i, col, columns, sl_col_a, sl_col_b, color=grp)
                    else:
                        self.grid.update_playback_leds(i, col, columns, color=grp)
            else:
                strip.set_playing(False)
                strip._waveform.set_active_column(-1)
                # Clear grid LEDs when a row transitions from playing → stopped
                if self._row_was_playing[i]:
                    self._row_was_playing[i] = False
                    self.grid.clear_row_leds(i, columns)

        # Update control row LEDs (group stops + pattern buttons)
        if self.grid.connected:
            self._update_control_leds(group_active)

        # Update group stop button visuals on transport bar
        for g in range(NUM_GROUPS):
            self.window.transport.set_group_active(g, group_active[g])

        # Master meter levels
        peak = self.engine.output_peak
        self.window.mixer_view.set_master_level(float(peak[0]), float(peak[1]))

        # Group meter levels
        levels = self._latest_levels
        if levels and "groups" in levels:
            for gi, lvl in enumerate(levels["groups"]):
                self.window.mixer_view.set_group_level(gi, float(lvl))

        # Push LED state to virtual grid pop-out
        if self.window.grid_window.isVisible():
            self.window.virtual_grid.set_led_state(self.grid.led_state)

        # Push meter/waveform data to effects rack modules
        self.window.effects_view.update_meters(levels)

    def _update_control_leds(self, group_active: list[bool]):
        """Update control row LEDs for group stops and pattern buttons."""
        # Group stop buttons (x=0..3): lit in group color when group has playing channels
        for g in range(NUM_GROUPS):
            brightness = LED_BRIGHT if group_active[g] else LED_OFF
            self.grid.set_control_led(g, brightness, color=g)

        # Pattern buttons (x=4..7): bright=recording/playing, dim=loaded, off=empty
        for i, pr in enumerate(self.patterns):
            if pr.recording:
                brightness = LED_BRIGHT
                color = 2  # red
            elif pr.playing:
                brightness = LED_BRIGHT
                color = 0  # amber
            elif pr.has_events:
                brightness = LED_DIM
                color = 0  # amber
            else:
                brightness = LED_OFF
                color = 0
            self.grid.set_control_led(4 + i, brightness, color)


# ================================================================== #
#  Entry point                                                        #
# ================================================================== #


def run_app(argv=None) -> int:
    """Launch the mlrVp application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Suppress harmless Qt font warning when using px units in stylesheets
    _original_handler = None

    def _qt_message_handler(msg_type, context, message):
        if "QFont::setPointSize" in message:
            return  # suppress
        if "paintEngine" in message or "Painter not active" in message:
            return  # suppress harmless Qt6 QPainter warnings in custom widgets
        if "Paint device returned engine" in message:
            return  # suppress harmless Qt6 paint engine warnings
        if "endPaint" in message or "QBackingStore" in message:
            return  # suppress Qt6 backing-store painter timing warning
        if _original_handler:
            _original_handler(msg_type, context, message)
        elif msg_type == QtMsgType.QtWarningMsg:
            logging.getLogger("qt").warning(message)
        elif msg_type == QtMsgType.QtCriticalMsg:
            logging.getLogger("qt").error(message)

    qInstallMessageHandler(_qt_message_handler)

    # Apply UI scale factor before QApplication init
    from mlrvp.config import get_ui_scale
    import os
    scale = get_ui_scale()
    if scale != 1.0:
        os.environ["QT_SCALE_FACTOR"] = str(scale)

    app = QApplication(argv or sys.argv)
    app.setApplicationName("mlrVp")
    app.setApplicationVersion("0.6.9")

    # Delay tooltips so they only appear after hovering intentionally.
    from PySide6.QtWidgets import QProxyStyle, QStyle
    class _DelayedToolTipStyle(QProxyStyle):
        def styleHint(self, hint, option=None, widget=None, returnData=None):
            if hint == QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
                return 1200
            if hint == QStyle.StyleHint.SH_ToolTip_FallAsleepDelay:
                return 200
            return super().styleHint(hint, option, widget, returnData)
    app.setStyle(_DelayedToolTipStyle(app.style().objectName()))

    # Force dark tooltip background: intercept the internal QTipLabel widget
    # when it shows and apply a direct stylesheet on it.  This targets *only*
    # the actual tooltip label, not any other top-level or child widget.
    from PySide6.QtCore import QObject, QEvent
    class _ToolTipStyler(QObject):
        _SS = ("background-color: #1a1a1a; color: #ffffff;"
               " border: 1px solid #cc9933; padding: 4px;")
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Show and obj.inherits("QTipLabel"):
                obj.setStyleSheet(self._SS)
            return False
    _tt_styler = _ToolTipStyler(app)
    app.installEventFilter(_tt_styler)

    # Set application icon
    _icon_path = Path(__file__).parent / "resources" / "icon.png"
    if _icon_path.exists():
        app.setWindowIcon(QIcon(str(_icon_path)))

    # Set default application font to suppress QFont::setPointSize warnings
    from PySide6.QtGui import QFont
    default_font = QFont("Helvetica Neue")
    default_font.setPixelSize(11)
    app.setFont(default_font)

    controller = AppController()

    # Initialize subsystems
    controller.init_audio()
    controller.init_transport()
    controller.init_grid()
    controller.init_midi()
    controller.init_osc()
    controller.init_gui(app)

    # Start running
    controller.start()

    # Run Qt event loop
    exit_code = app.exec()

    # Cleanup
    controller.stop()

    return exit_code