"""Application orchestrator for mlrVp.

Wires together all subsystems: audio engine, grid controller,
transport clock, MIDI, OSC, preset manager, and GUI.
"""

import sys
import logging
import time
import queue
import threading
from typing import Optional

from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QtMsgType, qInstallMessageHandler, QObject, Signal, Qt
from PySide6.QtGui import QIcon

from mlrvp.config import (
    AppConfig, PlaybackMode, MAX_ROWS, MAX_COLUMNS, NUM_GROUPS, NUM_PATTERN_RECORDERS,
    LED_OFF, LED_DIM, LED_BRIGHT, PITCH_SCALE_MAPS, PITCH_SCALE_NAMES,
    LIVE_STATE_EMPTY, LIVE_STATE_COUNTING_IN, LIVE_STATE_RECORDING,
    LIVE_STATE_OVERDUBBING, LIVE_STATE_ARMED_RERECORD, LIVE_STATE_PLAYING,
    LIVE_QUANTIZE_NAMES,
    MONOME_BACKEND_AUTO, MONOME_BACKEND_SERIALOSC,
    MONOME_BACKEND_40H_DIRECT, MONOME_BACKEND_LEGACY_OSC,
    SEQ_STEP_DIV_BEATS,
)
from mlrvp.audio.engine import AudioEngine
from mlrvp.audio.slicer_preview import SlicerPreviewService
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
from mlrvp.gui.views.mixer_view import _fader_to_gain, _gain_to_fader

log = logging.getLogger("mlrvp")


class _SampleLoadWorker(QObject):
    """Delivers background-loaded samples to the main Qt thread via Signal."""
    # row, filepath, SampleBuffer-or-None, preserve_speed flag, load_gen
    _loaded = Signal(int, str, object, bool, int)

    def __init__(self):
        super().__init__()


class _TriggerScheduler(QObject):
    """Marshals grid triggers from any thread to the main Qt thread."""
    _schedule = Signal(int, int, int, int)  # row, column, columns, delay_ms
    _release = Signal(int, int, int)  # row, column, columns

    def __init__(self, execute_fn, release_fn):
        super().__init__()
        self._execute_fn = execute_fn
        self._release_fn = release_fn
        self._schedule.connect(self._do_schedule, Qt.ConnectionType.QueuedConnection)
        self._release.connect(self._do_release, Qt.ConnectionType.QueuedConnection)

    def schedule(self, row, column, columns, delay_ms):
        self._schedule.emit(row, column, columns, delay_ms)

    def release(self, row, column, columns):
        self._release.emit(row, column, columns)

    def _do_schedule(self, row, column, columns, delay_ms):
        if delay_ms <= 0:
            self._execute_fn(row, column, columns)
        else:
            QTimer.singleShot(delay_ms,
                lambda r=row, c=column, cols=columns: self._execute_fn(r, c, cols))

    def _do_release(self, row, column, columns):
        self._release_fn(row, column, columns)


class _LiveLoopMarshaller(QObject):
    """Bounces audio-thread loop_recorded callback to the Qt main thread."""
    _recorded = Signal(int, object)  # row, SampleBuffer

    def __init__(self, recorded_fn):
        super().__init__()
        self._recorded.connect(recorded_fn, Qt.ConnectionType.QueuedConnection)

    def recorded(self, row, buffer):
        self._recorded.emit(row, buffer)


class _RecorderCompleteMarshaller(QObject):
    """Bounces audio-thread recorder-complete callback to the Qt main thread."""
    _completed = Signal()

    def __init__(self, completed_fn):
        super().__init__()
        self._completed.connect(completed_fn, Qt.ConnectionType.QueuedConnection)

    def completed(self):
        self._completed.emit()


class _ClockMarshaller(QObject):
    """Bounces clock-thread callbacks to the Qt main thread."""
    _beat = Signal(int, float)
    _pattern = Signal(int, int, str)
    _pattern_state = Signal(int, str)
    _seq_tick = Signal(float)  # beat_position — 1/64th note SEQ step tick
    _pattern_automation = Signal(str, float)  # param_path, value

    def __init__(self, beat_fn, pattern_fn, pattern_state_fn, seq_tick_fn, automation_fn):
        super().__init__()
        self._beat.connect(beat_fn, Qt.ConnectionType.QueuedConnection)
        self._pattern.connect(pattern_fn, Qt.ConnectionType.QueuedConnection)
        self._pattern_state.connect(pattern_state_fn, Qt.ConnectionType.QueuedConnection)
        self._seq_tick.connect(seq_tick_fn, Qt.ConnectionType.QueuedConnection)
        self._pattern_automation.connect(automation_fn, Qt.ConnectionType.QueuedConnection)

    def beat(self, beat_int, beat_position):
        self._beat.emit(beat_int, beat_position)

    def seq_tick(self, beat_position: float):
        self._seq_tick.emit(beat_position)

    def pattern_trigger(self, row, column, event_type):
        self._pattern.emit(row, column, event_type)

    def pattern_state(self, idx, state):
        self._pattern_state.emit(idx, state)

    def pattern_automation(self, param_path: str, value: float):
        self._pattern_automation.emit(param_path, value)


class AppController:
    """Central orchestrator that connects all subsystems."""

    def __init__(self):
        self.config = AppConfig()

        # Load lightweight startup settings (monome_backend etc.) persisted from
        # last session.  Must happen before GridController is created so that
        # the correct backend is active when grid.connect() fires in init_grid().
        self._load_startup_settings()

        # Pre-warm librosa in a background thread so the first pitch-shift
        # call doesn't stall on a cold import (librosa → numba JIT is slow).
        threading.Thread(target=self._preload_librosa, daemon=True).start()

        # Track physical grid button hold state per row (for ONESHOT HOLD)
        self._grid_row_held: set[int] = set()

        # Subsystems
        self.engine: Optional[AudioEngine] = None
        self.clock: Optional[MasterClock] = None
        self.grid = GridController()
        self._sync_grid_backend_to_controller()
        self.midi = MidiHandler()
        self.osc: Optional[OscHandler] = None
        self.presets = PresetManager()
        self.slicer_preview = SlicerPreviewService()
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
        self._trigger_scheduler = _TriggerScheduler(
            self._execute_grid_trigger, self._execute_grid_release
        )

        # Thread-safe clock marshaller — bounces clock-thread callbacks
        # (beat, pattern trigger, pattern state) to the Qt main thread
        self._clock_marshaller = _ClockMarshaller(
            self._execute_beat,
            self._execute_pattern_trigger,
            self._execute_pattern_state,
            self._execute_seq_tick,
            self._execute_pattern_automation,
        )

        # Background sample loader — disk I/O off the main thread
        self._sample_load_worker = _SampleLoadWorker()
        self._sample_load_worker._loaded.connect(
            self._on_bg_sample_loaded, Qt.ConnectionType.QueuedConnection
        )

        # Tilt axis → target param_path mapping
        self._tilt_mappings: dict[str, str] = {}  # e.g. "tilt.0.x" → "filter_a.lp_freq"
        # Tilt axis → (min, max) output range
        # min = output value at rest (device flat); max = output value at full tilt
        self._tilt_ranges: dict[str, tuple[float, float]] = {}  # e.g. "tilt.0.x" → (0.0, 1.0)
        # Tilt axis → sensitivity multiplier
        # 5.0 = a ~20% physical deflection from neutral produces a full-range sweep
        self._tilt_sensitivity: dict[str, float] = {}  # e.g. "tilt.0.y" → 5.0
        # Output-side EMA per axis: smooths the final mapped value so it glides
        # to its new target rather than jumping.  Alpha=0.35 at 60 Hz ≈ 8 Hz cutoff.
        # This eliminates the hard snap-to-min when the device returns to flat.
        self._tilt_output_ema: dict[str, float] = {}
        self._tilt_output_alpha: float = 0.35

        # Track per-row playing state for LED cleanup
        self._row_was_playing: list[bool] = [False] * MAX_ROWS
        # Cache last LED column per row — skip grid I/O when column unchanged
        self._row_last_led_col: list[int] = [-1] * MAX_ROWS

        # Control-row pattern buttons: hold-to-clear with immediate LED feedback.
        self._pattern_hold_timers: dict[int, QTimer] = {}
        self._pattern_hold_cleared: set[int] = set()
        self._pattern_clear_hold_seconds = 3.0
        # Track which patterns were in "recording" state when their button was pressed.
        # If auto-stop fires while the button is held, the release should NOT stop playback.
        self._pattern_was_recording_at_press: set[int] = set()

        # LIVE-mode looper state (per row)
        # _live_press_time[row] = monotonic timestamp of col-0 press (for 2s long-press)
        self._live_press_time: dict[int, float] = {}
        # _live_long_press_timers[row] = QTimer firing after 2s for col-0 hold detection
        self._live_long_press_timers: dict[int, QTimer] = {}
        # Beats remaining in count-in (key=row, value=int beats left). Counts down on each beat.
        self._live_countin_remaining: dict[int, int] = {}
        # Pending start params for count-in (row → (bars, tempo, input_trim, thru, overdub_buf))
        self._live_pending_start: dict[int, tuple] = {}
        # LIVE long-press hold seconds for re-record arming
        self._live_long_press_seconds = 2.0
        # LIVE recording marshaller — bounces audio-thread callback to GUI thread
        self._live_loop_marshaller = _LiveLoopMarshaller(self._on_loop_recorded)

        # Recorder (Tools tab) state
        self._recorder_complete_marshaller = _RecorderCompleteMarshaller(
            self._on_recorder_complete
        )
        # In-memory take slots (max 4). Cleared on preset load.
        self._recorder_takes: list = []  # list[SampleBuffer]
        # Pending start scheduling: stores a one-shot clock callback handle so
        # we can cancel if the user disarms before quantize boundary fires.
        self._recorder_pending_start: bool = False
        self._recorder_count_in_remaining: int = 0  # bars remaining for count-in
        # GUI poll timer for recorder progress + input meter (started in init_gui)
        self._recorder_poll_timer: Optional[QTimer] = None

        # Unsaved-changes tracking — set whenever the user modifies the loaded
        # preset; cleared on save or when a new preset is recalled.
        self._preset_dirty: bool = False

        # Persistent background worker for startup-settings writes.
        # Thread creation on Python/Windows involves a kernel allocation that
        # can take 0.5–5 ms while holding the GIL.  Creating a fresh thread on
        # every mode switch was an additional GIL spike during the critical
        # audio path.  A single long-lived worker drains a SimpleQueue instead.
        self._settings_save_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._settings_save_thread = threading.Thread(
            target=self._settings_save_worker, daemon=True, name="mlrvp-settings"
        )
        self._settings_save_thread.start()

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
        self.engine.set_live_recorded_callback(self._live_loop_marshaller.recorded)
        # Recorder completion bounce: audio thread → GUI thread
        self.engine.recorder.set_complete_callback(
            self._recorder_complete_marshaller.completed
        )
        log.info("Audio engine initialized")

    def init_transport(self):
        """Initialize the master clock."""
        self.clock = MasterClock(self.config.tempo, self.config.quantize)
        self.clock.on_beat(self._on_beat)
        self.clock.on_bar(self._on_bar)
        self.clock.on_tick(self._on_tick)
        self.clock.on_seq_tick(self._clock_marshaller.seq_tick)
        for pr in self.patterns:
            pr.set_trigger_callback(self._on_pattern_trigger)
            pr.set_state_change_callback(self._on_pattern_auto_state)
            pr.set_automation_callback(self._on_pattern_automation)
        log.info("Transport initialized")

    def init_grid(self):
        """Initialize grid controller and callbacks."""
        self.grid.set_button_callback(self._on_grid_button)
        self.grid.set_control_button_callback(self._on_control_button)
        self.grid.set_led_update_callback(self._on_led_update)
        self.grid.set_tilt_callback(self._on_tilt_data)
        self.grid.enable_tilt(True)
        # Kick off serialosc discovery in a background thread so any
        # monome grid plugged in (now or later) is auto-detected.
        self.grid.connect()
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
        if self.osc is not None:
            try:
                self.osc.stop()
            except Exception:
                pass
            self.osc = None
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
        """Register OSC address handlers for mixer and effects control.

        OSC messages arrive on ThreadingOSCUDPServer worker threads, so
        each handler is bounced to the Qt main thread via QTimer.singleShot.
        """
        osc = self.osc
        if not osc:
            return

        def _osc(fn):
            """Wrap an OSC handler to execute on the Qt main thread."""
            def _handler(addr, *args):
                QTimer.singleShot(0, lambda: fn(addr, *args))
            return _handler

        # Group mixer: /mlrvp/group/<1-N>/volume|send|mute|fx_route  (float 0-1 or int)
        for g in range(NUM_GROUPS):
            grp = g  # capture
            osc.add_handler(f"/mlrvp/group/{g+1}/volume",
                            _osc(lambda addr, v, _g=grp: self._gui_group_volume(_g, _fader_to_gain(float(v)))))
            osc.add_handler(f"/mlrvp/group/{g+1}/send",
                            _osc(lambda addr, v, _g=grp: self._gui_group_send(_g, float(v))))
            osc.add_handler(f"/mlrvp/group/{g+1}/mute",
                            _osc(lambda addr, v, _g=grp: self._gui_group_mute(_g, int(v) > 0)))
            osc.add_handler(f"/mlrvp/group/{g+1}/fx_route",
                            _osc(lambda addr, v, _g=grp: self._gui_group_fx_route(_g, int(v))))
            osc.add_handler(f"/mlrvp/group/{g+1}/stop",
                            _osc(lambda addr, *a, _g=grp: self._gui_group_stop(_g)))

        # AUX: /mlrvp/aux/volume|mute|fx_route|send
        osc.add_handler("/mlrvp/aux/volume",
                        _osc(lambda addr, v: self._gui_aux_volume(_fader_to_gain(float(v)))))
        osc.add_handler("/mlrvp/aux/mute",
                        _osc(lambda addr, v: self._gui_aux_mute(int(v) > 0)))
        osc.add_handler("/mlrvp/aux/fx_route",
                        _osc(lambda addr, v: self._gui_aux_fx_route(int(v))))
        osc.add_handler("/mlrvp/aux/send",
                        _osc(lambda addr, v: self._gui_aux_send(float(v))))

        # Master: /mlrvp/master/volume
        osc.add_handler("/mlrvp/master/volume",
                        _osc(lambda addr, v: self._gui_master_volume(_fader_to_gain(float(v)))))

        # Transport: /mlrvp/transport/tempo|play|stop
        osc.add_handler("/mlrvp/transport/tempo",
                        _osc(lambda addr, v: self._gui_tempo_changed(float(v))))

        # Effects: /mlrvp/delay/<param>  /mlrvp/filter/<param>  /mlrvp/mod/<param>
        osc.add_handler("/mlrvp/delay/time",
                        _osc(lambda addr, v: self._gui_delay_param("sync_time", float(v))))
        osc.add_handler("/mlrvp/delay/feedback",
                        _osc(lambda addr, v: self._gui_delay_param("feedback", float(v))))
        osc.add_handler("/mlrvp/delay/volume",
                        _osc(lambda addr, v: self._gui_delay_param("volume", float(v))))
        osc.add_handler("/mlrvp/filter_a/cutoff",
                        _osc(lambda addr, v: self._gui_filter_a_param("lp_freq", float(v))))
        osc.add_handler("/mlrvp/filter_a/resonance",
                        _osc(lambda addr, v: self._gui_filter_a_param("lp_q", float(v))))
        osc.add_handler("/mlrvp/filter_b/cutoff",
                        _osc(lambda addr, v: self._gui_filter_b_param("cutoff", float(v))))
        osc.add_handler("/mlrvp/filter_b/resonance",
                        _osc(lambda addr, v: self._gui_filter_b_param("resonance", float(v))))
        osc.add_handler("/mlrvp/mod/rate",
                        _osc(lambda addr, v: self._gui_mod_param("rate", float(v))))
        osc.add_handler("/mlrvp/mod/depth",
                        _osc(lambda addr, v: self._gui_mod_param("depth", float(v))))
        osc.add_handler("/mlrvp/mod/type",
                        _osc(lambda addr, v: self._gui_mod_param("mod_type", str(v))))

        # Per-effect routing: /mlrvp/delay/route  /mlrvp/mod/route
        osc.add_handler("/mlrvp/delay/route",
                        _osc(lambda addr, v: self._gui_effect_route("delay", int(v))))
        osc.add_handler("/mlrvp/mod/route",
                        _osc(lambda addr, v: self._gui_effect_route("mod", int(v))))

    def init_gui(self, app: QApplication):
        """Create the main window and wire GUI signals."""
        self.window = MainWindow()
        # Explicitly set the window icon so Windows shows it in the taskbar
        # title bar and alt-tab switcher (QApplication icon alone is not
        # always picked up for individual windows on Windows).
        _resources = Path(__file__).parent / "resources"
        _win_icon = (_resources / "icon.ico") if sys.platform == "win32" else (_resources / "icon.png")
        if not _win_icon.exists():
            _win_icon = _resources / "icon.png"
        if _win_icon.exists():
            self.window.setWindowIcon(QIcon(str(_win_icon)))
        self.window.tools_view.slice_panel.set_preview_service(self.slicer_preview)
        self._midi_map = MidiMapManager(self.window)
        self._connect_gui_signals()
        self._populate_gui_devices()
        self._register_mappable_controls()
        self._populate_tilt_targets()
        # Populate control row MIDI actions section
        cr = self.config.control_row
        self.window.setup_view.midi_panel.set_control_row_actions(
            cr.num_group_stops, cr.num_patterns
        )
        # Restore control row grid settings to UI
        self.window.setup_view.grid_panel.set_control_row_config(
            cr.enabled, cr.monome_row, cr.assignments
        )
        # Apply persisted control row config to the controller now so the
        # correct physical row / offset is used when the grid connects.
        self.grid.configure_control_row(cr.enabled, cr.monome_row)
        # Sync initial grid row colors from config group assignments
        for i, ch in enumerate(self.config.channels[:self.config.row_count]):
            self.window.virtual_grid.set_row_color(i, ch.group)
        # Sync transport button and virtual grid to the grid mode loaded from
        # startup settings.  _load_startup_settings sets config.grid_mode
        # directly (no GUI call), so this is the first time the button and
        # virtual grid learn about the restored mode.
        from .config import GRID_MODE_MIX
        self.window.transport.set_grid_mode(self.config.grid_mode)
        if self.config.grid_mode == GRID_MODE_MIX:
            active = self.config.active_groups
            cols = self.config.grid_columns
            col_colors = list(range(active)) + [0] * (cols - active)
            self.window.virtual_grid.set_col_colors(col_colors)
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

        # UI update timer (~60fps for responsive playback position LEDs)
        self._ui_timer = QTimer()
        self._ui_timer.timeout.connect(self._update_ui)
        self._ui_timer.start(16)

        # Recorder UI poll timer (~20Hz) for input meter + progress bar
        self._recorder_poll_timer = QTimer()
        self._recorder_poll_timer.timeout.connect(self._poll_recorder_ui)
        self._recorder_poll_timer.start(50)

        log.info("All subsystems started")

    def stop(self):
        """Stop all subsystems cleanly."""
        if self._ui_timer:
            self._ui_timer.stop()
        if self._recorder_poll_timer:
            self._recorder_poll_timer.stop()
        # Stop any active pattern hold timers
        for timer in self._pattern_hold_timers.values():
            timer.stop()
            timer.deleteLater()
        self._pattern_hold_timers.clear()
        # Stop any active LIVE long-press timers
        for timer in list(self._live_long_press_timers.values()):
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass
        self._live_long_press_timers.clear()
        # Signal the settings save worker to shut down cleanly
        self._settings_save_queue.put(None)
        if self.clock:
            self.clock.stop()
        if self.engine:
            self.engine.stop()
        if self.osc:
            self.osc.stop()
        self.slicer_preview.close()
        self.grid.disconnect()
        log.info("All subsystems stopped")

    def _mark_preset_dirty(self):
        """Mark that the current preset has unsaved changes."""
        self._preset_dirty = True

    def _clear_preset_dirty(self):
        """Clear the unsaved-changes flag (after save or preset load)."""
        self._preset_dirty = False

    def _on_close_requested(self):
        """Intercept window close — offer to save unsaved changes first."""
        if not self._preset_dirty:
            if self.window:
                self.window.confirm_close()
            return

        sp = self.presets.setlist_presets
        idx = self.presets.active_index
        name = sp[idx] if 0 <= idx < len(sp) else ""
        display = f'"{name}"' if name else "the current preset"

        from PySide6.QtWidgets import QMessageBox
        msg = QMessageBox(self.window)
        msg.setWindowTitle("Unsaved Changes")
        msg.setText(f"Save changes to {display} before closing?")
        msg.setStandardButtons(
            QMessageBox.StandardButton.Save |
            QMessageBox.StandardButton.Discard |
            QMessageBox.StandardButton.Cancel
        )
        msg.setDefaultButton(QMessageBox.StandardButton.Save)
        result = msg.exec()

        if result == QMessageBox.StandardButton.Save:
            self._gui_save_preset(name)
            if self.window:
                self.window.confirm_close()
        elif result == QMessageBox.StandardButton.Discard:
            if self.window:
                self.window.confirm_close()
        # Cancel: do nothing — window stays open

    # ------------------------------------------------------------------ #
    #  Group-exclusive playback                                           #
    # ------------------------------------------------------------------ #

    def _stop_group_siblings(self, row: int, columns: int):
        """Stop all other playing channels that share the same group.

        Only one row per group can play at a time.  When a new row in
        a group is triggered, every *other* row in that group is stopped
        and its LEDs are cleared.

        We unconditionally queue force_stop for every sibling rather
        than checking ch.playing, because _playing is set on the DSP
        thread and a queued trigger may not have been processed yet.
        """
        if not self.engine or row >= len(self.engine.channels):
            return
        group = self.engine.channels[row].state.group
        for i, ch in enumerate(self.engine.channels[:self.config.row_count]):
            if i != row and ch.state.group == group:
                self.engine.force_stop_channel(i)
                # Pause SEQ siblings — keep step pattern, stop advancing
                if ch.state.mode == PlaybackMode.SEQ:
                    ch._seq_stopped = True
                    ch._seq_playing_step = False
                    ch._seq_column = -1
                # Reset any SLICE state on the stopped channel
                if ch.state.mode == PlaybackMode.SLICE:
                    ch._slice_btn1 = None
                    ch._slice_btn2 = None
                    ch._slice_loop_start = None
                    ch._slice_loop_end = None
                    ch._slice_reverse = False
                    ch._slice_latched = False
                    ch._slice_held = set()
                    ch._slice_col_start = -1
                    ch._slice_col_end = -1
                if ch.state.mode == PlaybackMode.SEQ and ch._seq_steps:
                    self._update_seq_leds(i)
                else:
                    self.grid.clear_row_leds(i, columns)

    # ------------------------------------------------------------------ #
    #  Audio / Transport callbacks                                        #
    # ------------------------------------------------------------------ #

    def _on_meter_levels(self, levels: dict):
        """Store latest levels for UI update.

        Now only called via poll_meters() from the GUI refresh timer,
        not directly from the DSP thread.
        """
        self._latest_levels = levels

    def _on_beat(self, beat_int: int, beat_position: float):
        """Beat boundary — runs on clock thread.

        Pattern recorder updates stay on clock thread for timing accuracy.
        SEQ-mode channel logic is bounced to the Qt main thread.
        """
        if self.clock:
            for pr in self.patterns:
                pr.update(beat_position)

        # Bounce SEQ-mode work to main thread
        self._clock_marshaller.beat(beat_int, beat_position)

    def _execute_beat(self, beat_int: int, beat_position: float):
        """Beat-boundary work — runs on Qt main thread."""
        # LIVE-mode count-in tick (decrements remaining beats, fires start)
        self._live_advance_countin(beat_int)
        # Recorder (Tools tab) quantized start handler
        self._recorder_advance_clock(beat_int)
        # Pending sample reloads for SEQ channels (staged while SEQ was running)
        if self.engine:
            for i in range(self.config.row_count):
                ch = self.engine.channels[i]
                if ch.state.mode != PlaybackMode.SEQ:
                    continue
                pending = getattr(ch, '_pending_sample_path', None)
                if not pending:
                    continue
                ch._pending_sample_path = None
                self.engine.stop_channel(i)
                self.grid.clear_row_leds(i, self.config.grid_columns)
                buf = getattr(ch, '_pending_sample_buffer', None)
                ch._pending_sample_buffer = None
                if buf is not None:
                    self._apply_loaded_sample(i, pending, buf)
                else:
                    self.load_sample_to_channel(i, pending)

    def _execute_seq_tick(self, beat_position: float):
        """SEQ-mode step advancement at 1/64th-note resolution — Qt main thread."""
        if not self.engine:
            return
        columns = self.config.grid_columns
        for i in range(self.config.row_count):
            ch = self.engine.channels[i]
            if ch.state.mode != PlaybackMode.SEQ:
                continue
            if ch.sample is None:
                continue
            if ch._seq_stopped:
                continue
            # Skip if a pending reload is waiting for the beat boundary
            if getattr(ch, '_pending_sample_path', None):
                continue
            # Determine current step based on the channel's step division
            div_idx = max(0, min(len(SEQ_STEP_DIV_BEATS) - 1, ch.state.seq_step_div))
            beats_per_step = SEQ_STEP_DIV_BEATS[div_idx]
            step_global = int(beat_position / beats_per_step)
            col = step_global % columns if columns > 0 else 0
            if col == ch._seq_column:
                continue
            ch._seq_column = col
            if col in ch._seq_steps:
                self._stop_group_siblings(i, columns)
                self.engine.trigger_channel(i, 0, 1)
                ch._seq_playing_step = True
            else:
                ch._seq_playing_step = ch._playing

    def _on_bar(self, bar: int):
        """Bar boundary — for future use."""
        pass

    def _on_tick(self, beat_position: float, tempo: float):
        """Clock tick — reserved for future fine-grained updates."""
        pass

    def _on_pattern_trigger(self, row: int, column: int, event_type: str):
        """Pattern recorder replays an event — bounce to main thread."""
        self._clock_marshaller.pattern_trigger(row, column, event_type)

    def _on_pattern_automation(self, param_path: str, value: float):
        """Pattern recorder replays an automation event — bounce to main thread."""
        self._clock_marshaller.pattern_automation(param_path, value)

    def _notify_automation(self, param_path: str, value: float):
        """Record automation to all active pattern recorders.

        Called on the main thread whenever a continuous parameter changes.
        """
        if not self.clock:
            return
        beat = self.clock.beat_position
        for pr in self.patterns:
            if pr.recording:
                pr.record_automation(beat, param_path, value)

    def _execute_pattern_trigger(self, row: int, column: int, event_type: str):
        """Handle pattern replay event on the Qt main thread."""
        log.debug("Pattern trigger: row=%d col=%d type=%s", row, column, event_type)
        if not self.engine or row >= len(self.engine.channels):
            return
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

    def _execute_pattern_automation(self, param_path: str, value: float):
        """Apply a recorded automation event on the Qt main thread."""
        parts = param_path.split(".")
        try:
            if parts[0] == "ch" and len(parts) == 3:
                row = int(parts[1])
                param = parts[2]
                if param in ("mute", "solo"):
                    self._gui_channel_param(row, param, value > 0.5)
                else:
                    self._gui_channel_param(row, param, value)
            elif parts[0] == "grp" and len(parts) == 3:
                grp = int(parts[1])
                param = parts[2]
                if param == "volume":
                    self._gui_group_volume(grp, value)
                elif param == "send":
                    self._gui_group_send(grp, value)
                elif param == "mute":
                    self._gui_group_mute(grp, value > 0.5)
                elif param == "fx_route":
                    self._gui_group_fx_route(grp, int(round(value)))
            elif len(parts) == 3 and parts[0] in {
                "delay", "tape_delay", "filter_a", "filter_b", "mod",
                "reel_to_reel", "cassette_tape", "vhs", "vinyl",
                "compressor", "glue_compressor", "hall_reverb", "plate_reverb",
                "bit_crusher", "tube_overdrive", "ether_drift", "noise_gate",
                "tremolo", "auto_wah", "fet_limiter", "rotary", "exciter",
                "transient_shaper", "harmonizer", "stereo_widener",
                "freq_shifter", "ring_mod", "simple_eq", "ping_pong_delay",
                "shimmer_reverb", "lofi_radio", "auto_pan", "fuzz",
                "granular_freeze", "gate_reverb", "stutter",
            }:
                handler = getattr(self, f"_gui_{parts[0]}_param", None)
                if handler is not None:
                    handler(parts[2], value, int(parts[1]))
            elif param_path == "transport.tempo":
                self._gui_tempo_changed(value)
        except (IndexError, ValueError, TypeError) as exc:
            log.warning("Pattern automation dispatch error for %s: %s", param_path, exc)

    # ------------------------------------------------------------------ #
    #  Grid callbacks                                                     #
    # ------------------------------------------------------------------ #

    def _on_grid_button(self, x: int, y: int, state: int):
        """Grid button press/release handler — core mlr interaction.

        When quantize is enabled, the trigger is scheduled to fire at
        the next quantize boundary so presses stay in sync with BPM.
        """
        # --- Mix mode: grid is a fader surface, not a trigger surface ---
        from .config import GRID_MODE_MIX
        if self.config.grid_mode == GRID_MODE_MIX:
            if state == 1:
                self._mix_mode_on_press(x, y)
            return

        row = y
        column = x
        columns = self.config.grid_columns

        if row >= self.config.row_count:
            return
        if self.engine and row >= len(self.engine.channels):
            return

        if state == 0:
            # Track physical button release
            self._grid_row_held.discard(row)
            # LIVE mode: cancel any pending long-press detection on col-0 release
            if self.engine and row < len(self.engine.channels):
                ch = self.engine.channels[row]
                if ch.state.mode == PlaybackMode.LIVE and column == 0:
                    self._live_cancel_long_press(row)
            # Release handling — bounce to Qt main thread via scheduler
            self._trigger_scheduler.release(row, column, columns)
            return

        # Track physical button press
        self._grid_row_held.add(row)

        # LIVE mode: handle entirely separately from normal trigger flow
        if self.engine and row < len(self.engine.channels):
            ch = self.engine.channels[row]
            if ch.state.mode == PlaybackMode.LIVE:
                self._handle_live_grid_press(row, column, columns)
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
                # Re-arm stopped SEQ row so it resumes on the next beat
                if ch._seq_stopped:
                    ch._seq_stopped = False
                # Update grid LEDs to show step pattern
                self._update_seq_leds(row)
                return

        # Determine quantize delay
        delay = 0.0
        if self.clock:
            delay = self.clock.get_quantize_delay()

        # Immediate LED feedback on press — don't wait for trigger execution
        # or the next _update_ui cycle.  This eliminates ~16-33ms of visual lag.
        grp = self.config.channels[row].group if row < len(self.config.channels) else 0
        self.grid.update_playback_leds(row, column, columns, color=grp)

        if delay <= 0.001:
            # Fire immediately (no quantize, or already on boundary)
            self._trigger_scheduler.schedule(row, column, columns, 0)
        else:
            # Schedule the trigger at the next quantize boundary
            delay_ms = int(delay * 1000)
            self._trigger_scheduler.schedule(row, column, columns, delay_ms)

    # ──────────────────────────────────────────────────────────────────
    #  LIVE-mode looper grid handling
    # ──────────────────────────────────────────────────────────────────

    def _handle_live_grid_press(self, row: int, column: int, columns: int):
        """Handle a grid press on a LIVE-mode row."""
        if not self.engine or row >= len(self.engine.channels):
            return
        ch = self.engine.channels[row]
        live_state = ch.state.live.loop_state

        if live_state == LIVE_STATE_EMPTY:
            self._live_begin_recording(row, overdub=False)
            return

        if live_state == LIVE_STATE_COUNTING_IN:
            self._live_pending_start.pop(row, None)
            self._live_countin_remaining.pop(row, None)
            ch.state.live.loop_state = LIVE_STATE_EMPTY
            self._notify_live_state(row)
            return

        if live_state in (LIVE_STATE_RECORDING, LIVE_STATE_OVERDUBBING):
            self.engine.cancel_live_recording()
            if live_state == LIVE_STATE_OVERDUBBING:
                ch.state.live.loop_state = LIVE_STATE_PLAYING
            else:
                ch.state.live.loop_state = LIVE_STATE_EMPTY
            self._notify_live_state(row)
            return

        if live_state == LIVE_STATE_ARMED_RERECORD:
            self._live_begin_recording(row, overdub=False)
            return

        if live_state == LIVE_STATE_PLAYING:
            # If playback was stopped (group/all stop), any press resumes the loop
            if not ch.state.playing:
                self._stop_group_siblings(row, columns)
                self.engine.trigger_channel(row, column, columns)
                return
            if column == 0:
                self._live_press_time[row] = time.monotonic()
                self._live_arm_long_press(row)
                self._live_begin_recording(row, overdub=True)
            else:
                ch.state.mute = not ch.state.mute
            return

    def _live_arm_long_press(self, row: int):
        """Start a 2-second QTimer that arms re-record if col-0 is still held."""
        prev = self._live_long_press_timers.pop(row, None)
        if prev is not None:
            prev.stop()
            prev.deleteLater()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda r=row: self._live_long_press_fired(r))
        timer.start(int(self._live_long_press_seconds * 1000))
        self._live_long_press_timers[row] = timer

    def _live_cancel_long_press(self, row: int):
        """Cancel the pending long-press timer (button released early)."""
        timer = self._live_long_press_timers.pop(row, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        self._live_press_time.pop(row, None)

    def _live_clear_all_pending(self):
        """Cancel every pending LIVE timer + count-in / pending-start dict.

        Called on preset recall: stale long-press timers, count-in beats,
        or pending-start tuples from the previous preset must not fire on
        the new preset (whose row modes may have changed from LIVE to
        something else, or whose channels have a different sample bound).
        """
        for timer in list(self._live_long_press_timers.values()):
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass
        self._live_long_press_timers.clear()
        self._live_press_time.clear()
        self._live_pending_start.clear()
        self._live_countin_remaining.clear()

    def _live_long_press_fired(self, row: int):
        """Long-press (2s) detected on col-0 — arm re-record."""
        self._live_long_press_timers.pop(row, None)
        if not self.engine or row >= len(self.engine.channels):
            return
        if row not in self._grid_row_held:
            return
        ch = self.engine.channels[row]
        if ch.state.mode != PlaybackMode.LIVE:
            return
        if ch.state.live.loop_state == LIVE_STATE_OVERDUBBING:
            self.engine.cancel_live_recording()
        ch.state.live.loop_state = LIVE_STATE_ARMED_RERECORD
        self._notify_live_state(row)

    def _live_begin_recording(self, row: int, overdub: bool):
        """Arm a recording — apply count-in or quantize delay, then start."""
        if not self.engine or row >= len(self.engine.channels):
            return
        ch = self.engine.channels[row]
        live = ch.state.live
        bars = max(1, int(live.bars))
        tempo = self.config.tempo if self.config else 120.0

        overdub_buf = None
        if overdub and ch.sample is not None:
            try:
                overdub_buf = ch.sample.data.copy() if ch.sample.data is not None else None
            except Exception:
                overdub_buf = None

        params = (bars, tempo, float(live.input_trim), bool(live.thru), overdub_buf)

        if live.count_in:
            self._live_pending_start[row] = (params, overdub)
            self._live_countin_remaining[row] = 4
            ch.state.live.loop_state = LIVE_STATE_COUNTING_IN
            self._notify_live_state(row)
            return

        self._live_start_now(row, params, overdub)

    def _live_start_now(self, row: int, params: tuple, overdub: bool):
        """Actually call engine.start_live_recording with the prepared params."""
        if not self.engine:
            return
        bars, tempo, input_trim, thru, overdub_buf = params
        ok = self.engine.start_live_recording(
            row=row, bars=bars, tempo=tempo,
            input_trim=input_trim, thru=thru,
            overdub_buffer=overdub_buf,
        )
        if not ok:
            ch = self.engine.channels[row]
            ch.state.live.loop_state = (
                LIVE_STATE_PLAYING if overdub else LIVE_STATE_EMPTY
            )
            self._notify_live_state(row)
            if self.window:
                self.window.set_status("LIVE: another row is already recording")
            return
        ch = self.engine.channels[row]
        ch.state.live.loop_state = (
            LIVE_STATE_OVERDUBBING if overdub else LIVE_STATE_RECORDING
        )
        self._notify_live_state(row)

    def _live_advance_countin(self, beat_int: int):
        """Called from _execute_beat — decrements count-in and fires start."""
        if not self._live_countin_remaining:
            return
        for row in list(self._live_countin_remaining.keys()):
            remaining = self._live_countin_remaining[row] - 1
            if remaining > 0:
                self._live_countin_remaining[row] = remaining
            else:
                self._live_countin_remaining.pop(row, None)
                pending = self._live_pending_start.pop(row, None)
                if pending is not None:
                    params, overdub = pending
                    self._live_start_now(row, params, overdub)

    def _on_loop_recorded(self, row: int, recorder):
        """Audio thread → marshalled to GUI thread.

        A LIVE recording finished. Finalize the recorder (crossfade + buffer
        allocation happens here on the GUI thread, NOT in the audio callback),
        then load the buffer as the channel's sample and start playback.
        """
        if not self.engine or row >= len(self.engine.channels):
            return
        # Finalize off the audio thread to avoid underruns
        try:
            buffer = recorder.finalize() if recorder is not None else None
        except Exception as e:
            log.error("LIVE: finalize failed for row %d: %s", row, e)
            buffer = None
        if buffer is None:
            ch = self.engine.channels[row]
            ch.state.live.loop_state = LIVE_STATE_EMPTY
            self._notify_live_state(row)
            return
        ch = self.engine.channels[row]
        try:
            self.engine.load_sample(row, buffer)
            ch.state.live.loop_state = LIVE_STATE_PLAYING
            self.engine.trigger_channel(row, 0, max(1, self.config.grid_columns))
            if self.window:
                try:
                    overview = get_waveform_overview(buffer, num_points=800)
                    strip = self.window.channel_view.get_strip(row)
                    strip.set_waveform_data(overview, f"LIVE row {row + 1}")
                    strip._waveform.set_grid_columns(self.config.grid_columns)
                except Exception:
                    pass
        except Exception as e:
            log.error("LIVE: failed to apply recorded buffer to row %d: %s", row, e)
            ch.state.live.loop_state = LIVE_STATE_EMPTY
        self._notify_live_state(row)

    def _on_live_param_changed(self, row: int, param: str, value):
        """Handle a LIVE parameter change from the channel-view UI."""
        if not self.engine or row >= len(self.engine.channels):
            return
        live = self.engine.channels[row].state.live
        cfg_live = self.config.channels[row].live if row < len(self.config.channels) else None
        if param == "bars":
            v = max(1, int(value))
            live.bars = v
            if cfg_live is not None:
                cfg_live.bars = v
        elif param == "quantize":
            live.quantize = int(value)
            if cfg_live is not None:
                cfg_live.quantize = int(value)
        elif param == "thru":
            live.thru = bool(value)
            if cfg_live is not None:
                cfg_live.thru = bool(value)
        elif param == "count_in":
            live.count_in = bool(value)
            if cfg_live is not None:
                cfg_live.count_in = bool(value)
        elif param == "input_trim":
            live.input_trim = float(value)
            if cfg_live is not None:
                cfg_live.input_trim = float(value)
            if self.engine.live_recording_row == row:
                self.engine.set_live_input_trim(float(value))

    def _on_live_rec_button(self, row: int, is_hold: bool):
        """REC button on the channel strip clicked (or held 2s)."""
        if not self.engine or row >= len(self.engine.channels):
            return
        ch = self.engine.channels[row]
        if ch.state.mode != PlaybackMode.LIVE:
            return
        live_state = ch.state.live.loop_state

        if is_hold:
            if live_state == LIVE_STATE_PLAYING:
                ch.state.live.loop_state = LIVE_STATE_ARMED_RERECORD
                self._notify_live_state(row)
            return

        if live_state == LIVE_STATE_EMPTY:
            self._live_begin_recording(row, overdub=False)
        elif live_state == LIVE_STATE_PLAYING:
            self._live_begin_recording(row, overdub=True)
        elif live_state in (LIVE_STATE_RECORDING, LIVE_STATE_OVERDUBBING,
                            LIVE_STATE_COUNTING_IN):
            self._live_pending_start.pop(row, None)
            self._live_countin_remaining.pop(row, None)
            self.engine.cancel_live_recording()
            ch.state.live.loop_state = (
                LIVE_STATE_PLAYING if live_state == LIVE_STATE_OVERDUBBING
                else LIVE_STATE_EMPTY
            )
            self._notify_live_state(row)
        elif live_state == LIVE_STATE_ARMED_RERECORD:
            ch.state.live.loop_state = LIVE_STATE_PLAYING
            self._notify_live_state(row)

    def _on_live_export_requested(self, row: int):
        """Export the currently-loaded LIVE loop to data/slices/."""
        if not self.engine or row >= len(self.engine.channels):
            return
        ch = self.engine.channels[row]
        if ch.sample is None:
            if self.window:
                self.window.set_status("LIVE: nothing to export")
            return
        try:
            from mlrvp.audio.live_loop import LiveLoopRecorder
            data_dir = getattr(self.config, "data_dir", None) or "data"
            path = LiveLoopRecorder.export(
                ch.sample, data_dir, row, self.engine.sample_rate
            )
            ch.state.live.exported_path = path
            if row < len(self.config.channels):
                self.config.channels[row].live.exported_path = path
                # Make exported file the channel's filename so preset save records it
                self.config.channels[row].filename = path
            if self.window:
                self.window.set_status(f"LIVE row {row + 1} exported → {path}")
            log.info("LIVE: exported row %d to %s", row, path)
        except Exception as e:
            log.error("LIVE: export failed for row %d: %s", row, e)
            if self.window:
                self.window.set_status(f"LIVE export failed: {e}")

    def _notify_live_state(self, row: int):
        """Push LIVE state to the channel-view strip (badge + REC button visual)."""
        if not self.window or not self.engine:
            return
        try:
            ch = self.engine.channels[row]
            strip = self.window.channel_view.get_strip(row)
            if hasattr(strip, "set_live_loop_state"):
                strip.set_live_loop_state(ch.state.live.loop_state)
        except Exception:
            pass

    def _execute_grid_release(self, row: int, column: int, columns: int):
        """Handle grid button release on the Qt main thread."""
        if not self.engine or row >= len(self.engine.channels):
            return
        ch = self.engine.channels[row]
        if ch.state.mode == PlaybackMode.ONESHOT and ch.state.shot_hold:
            if ch._shot_held:
                ch._shot_held = False
                self.engine.stop_channel(row)
                self.grid.clear_row_leds(row, columns)
            # Also clear the held flag even if trigger hasn't fired yet
            # (quantized trigger will see _grid_row_held is empty)
        elif ch.state.mode == PlaybackMode.SLICE:
            if not hasattr(ch, '_slice_held'):
                ch._slice_held = set()
            ch._slice_held.discard(column)
            # If no buttons held and not latched, stop playback
            if not ch._slice_held and not getattr(ch, '_slice_latched', False):
                    self.engine.stop_channel(row)
                    ch._slice_btn1 = None
                    ch._slice_btn2 = None
                    ch._slice_loop_start = None
                    ch._slice_loop_end = None
                    ch._slice_reverse = False
                    ch._slice_col_start = -1
                    ch._slice_col_end = -1
                    self.grid.clear_row_leds(row, columns)

    def _execute_grid_trigger(self, row: int, column: int, columns: int):
        """Actually fire the grid trigger (after quantize delay if any)."""
        if not self.engine or row >= len(self.engine.channels):
            return
        ch = self.engine.channels[row]

        # If a preset transition staged a new sample, load it now
        pending = getattr(ch, '_pending_sample_path', None)
        if pending:
            ch._pending_sample_path = None
            # Stop current playback so the engine load goes through the
            # idle branch (direct load, not re-staged as pending).
            self.engine.stop_channel(row)
            self.grid.clear_row_leds(row, self.config.grid_columns)
            # Use pre-loaded buffer if background load already finished
            buf = getattr(ch, '_pending_sample_buffer', None)
            ch._pending_sample_buffer = None
            if buf is not None:
                self._apply_loaded_sample(row, pending, buf)
            else:
                # BG load still in flight — kick a fresh load.
                # Don't fall through to the trigger below because the
                # old sample is still loaded; triggering it would play
                # stale audio.  The user will need to press again once
                # the new sample appears in the waveform.
                self.load_sample_to_channel(row, pending)
                return

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

            if ch.playing and ch.sample is not None:
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
            # ONESHOT HOLD: mark button as held so sample loops until release.
            # Check _grid_row_held to handle quantized triggers that arrive
            # after the physical button was already released.
            if ch.state.mode == PlaybackMode.ONESHOT and ch.state.shot_hold:
                if row in self._grid_row_held:
                    ch._shot_held = True
                else:
                    # Button was released before the quantized trigger fired —
                    # don't loop; let it play as a normal one-shot.
                    ch._shot_held = False

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

    def _update_live_leds(self, row: int, live_state: str, columns: int):
        """Render LEDs for a LIVE-mode row in a non-PLAYING substate."""
        if not self.engine:
            return
        ch = self.engine.channels[row]
        grp = ch.state.group
        values = [LED_OFF] * columns
        # ~4 Hz wall-clock blink phase (independent of beat / UI tick rate)
        blink_on = (int(time.monotonic() * 4) & 1) == 0

        if live_state == LIVE_STATE_EMPTY:
            pass  # all off
        elif live_state == LIVE_STATE_COUNTING_IN:
            if blink_on:
                values = [LED_DIM] * columns
        elif live_state in (LIVE_STATE_RECORDING, LIVE_STATE_OVERDUBBING):
            progress = self.engine.live_recording_progress
            col = int(progress * columns)
            if col >= columns:
                col = columns - 1
            if col < 0:
                col = 0
            values[col] = LED_BRIGHT
        elif live_state == LIVE_STATE_ARMED_RERECORD:
            if blink_on:
                values[0] = LED_BRIGHT

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

    def _on_virtual_grid_size_changed(self, rows: int, cols: int):
        """Virtual grid size toggle selected — apply as if the user changed rows+columns in Setup."""
        rows = max(1, min(rows, MAX_ROWS))
        cols = max(1, min(cols, MAX_COLUMNS))
        self.config.row_count = rows
        self.config.grid_columns = cols
        for ch in self.config.channels:
            ch.steps = cols
        # Reset LED column cache — resized grid needs a full refresh
        self._row_last_led_col = [-1] * MAX_ROWS
        self._row_was_playing = [False] * MAX_ROWS
        if self.engine:
            for i in range(MAX_ROWS):
                self.engine.channels[i].state.steps = cols
            for i in range(rows):
                self.engine.set_channel_speed(i, self.config.tempo)
        if self.window:
            self.window.set_grid_dimensions(rows, cols)
            self._sync_grid_columns_to_waveforms()
            show_vg = self.window._grid_window.isVisible()
            self.window.setup_view.grid_panel.set_grid_settings(
                row_count=rows,
                grid_columns=cols,
                show_virtual_grid=show_vg,
                snap_speed_to_rate=getattr(self.config, "snap_speed_to_rate", False),
                monome_backend=getattr(self.config, "monome_backend", MONOME_BACKEND_AUTO),
            )
            self.window.set_status(f"Virtual grid: {cols}×{rows}")
        log.info("Virtual grid size changed to %dx%d", cols, rows)

    # Throttle for tilt debug logging — only log once per second
    _tilt_log_last: float = 0.0

    def _on_tilt_data(self, sensor: int, x: float, y: float, z: float):
        """Handle accelerometer/tilt data from monome grid.

        Forwards normalized 0.0-1.0 values to any mapped target params.
        """
        import time as _time
        # Throttled debug log so the user can confirm data is arriving
        now = _time.monotonic()
        if now - AppController._tilt_log_last >= 1.0:
            AppController._tilt_log_last = now
            log.debug("tilt_data: sensor=%d x=%.3f y=%.3f z=%.3f  mappings=%s",
                      sensor, x, y, z, list(self._tilt_mappings.keys()))
        # Update live display in setup panel (only costs a label setText)
        if self.window:
            self.window.setup_view.update_tilt_live(x, y)
        for axis, val in [("x", x), ("y", y)]:
            src = f"tilt.{sensor}.{axis}"
            target = self._tilt_mappings.get(src)
            if target:
                min_v, max_v = self._tilt_ranges.get(src, (0.0, 1.0))
                sensitivity = self._tilt_sensitivity.get(src, 5.0)
                # Neutral position (device held flat) is centred at 0.5 (ADC 128/255).
                # delta = deviation from rest; t is clamped to [0, 1] so that
                # min_v is output at rest and max_v is output at full deflection.
                # Swap min/max to invert direction; increase sensitivity for
                # big sweeps from small tilts (default 5.0 ≈ ±20% = full sweep).
                t = max(0.0, min(1.0, (val - 0.5) * sensitivity))
                scaled = min_v + t * (max_v - min_v)
                # Output-side EMA: glide the mapped value smoothly to its target.
                # This eliminates the hard snap-to-min (e.g. 200 Hz) when the
                # device returns to flat — the value drifts there instead.
                oa = self._tilt_output_alpha
                prev = self._tilt_output_ema.get(src, scaled)
                smoothed = oa * scaled + (1.0 - oa) * prev
                self._tilt_output_ema[src] = smoothed
                cb = self.midi.get_param_callback(target)
                if cb:
                    cb(smoothed)
                else:
                    log.debug("tilt: no callback for param '%s' (call _register_midi_param_callback first)", target)

    # ------------------------------------------------------------------ #
    #  Control row (monome top row / Launchpad CC buttons)                #
    # ------------------------------------------------------------------ #

    def _on_control_button(self, x: int, state: int):
        """Handle control row button press/release dispatched by slot assignment."""
        from mlrvp.config import CR_SLOT_GROUP_STOP, CR_SLOT_PATTERN, CR_SLOT_ALL_STOP, CR_SLOT_GRID_MODE
        assignments = self.config.control_row.assignments
        if x < 0 or x >= len(assignments):
            return
        slot = assignments[x]
        slot_type = slot.get("type", "empty")
        slot_val = slot.get("value", 0)

        if slot_type == CR_SLOT_GROUP_STOP:
            if state == 1:
                self._gui_group_stop(slot_val)

        elif slot_type == CR_SLOT_ALL_STOP:
            if state == 1:
                for g in range(NUM_GROUPS):
                    self._gui_group_stop(g)

        elif slot_type == CR_SLOT_GRID_MODE:
            if state == 1:
                # Dispatch to the Qt main thread — _gui_grid_mode_set() calls
                # setStyleSheet(), update() and QTimer which must run on the
                # main thread.  Calling them directly from the MIDI callback
                # thread (where this handler runs) causes unpredictable GIL
                # stalls that drain the audio ring buffer.
                QTimer.singleShot(0, self._gui_grid_mode_cycle)

        elif slot_type == CR_SLOT_PATTERN:
            idx = slot_val
            if state == 1:
                old_timer = self._pattern_hold_timers.get(idx)
                if old_timer is not None:
                    old_timer.stop()
                    old_timer.deleteLater()
                timer = QTimer()
                timer.setSingleShot(True)
                timer.setInterval(int(self._pattern_clear_hold_seconds * 1000))
                timer.timeout.connect(lambda _idx=idx: self._pattern_hold_fire(_idx))
                self._pattern_hold_timers[idx] = timer
                self._pattern_hold_cleared.discard(idx)
                timer.start()
                pr = self.patterns[idx] if idx < len(self.patterns) else None
                if pr is not None and pr.recording:
                    self._pattern_was_recording_at_press.add(idx)
                else:
                    self._pattern_was_recording_at_press.discard(idx)
                return

            # Release
            timer = self._pattern_hold_timers.pop(idx, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
            if idx in self._pattern_hold_cleared:
                self._pattern_hold_cleared.discard(idx)
                return
            pr = self.patterns[idx] if idx < len(self.patterns) else None
            if (pr is not None
                    and idx in self._pattern_was_recording_at_press
                    and not pr.recording
                    and pr.playing):
                log.info(
                    "Pattern %d: auto-stop fired while button held — keeping playback", idx
                )
                self._pattern_was_recording_at_press.discard(idx)
                return
            self._pattern_was_recording_at_press.discard(idx)
            self._on_control_pattern_toggle(idx)

    def _pattern_hold_fire(self, idx: int):
        """Timer fired — clear the pattern and update LED immediately."""
        from mlrvp.config import CR_SLOT_PATTERN
        self._pattern_hold_cleared.add(idx)
        timer = self._pattern_hold_timers.pop(idx, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        self._gui_pattern_state(idx, "clear")
        if self.window:
            self.window.transport.set_pattern_state(idx, "idle")
        # Immediately turn off the LED for the physical slot that maps to this pattern
        for slot_x, slot in enumerate(self.config.control_row.assignments):
            if slot.get("type") == CR_SLOT_PATTERN and slot.get("value") == idx:
                self.grid.set_control_led(slot_x, LED_OFF, 0)
                break

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
            with pr._lock:
                pr._recording = False
                if pr.has_events:
                    pr._playing = True
                    pr._start_beat = beat
                    pr._last_playback_pos = -1.0
                    gui_state = "playing"
                else:
                    gui_state = "idle"
            log.info("Pattern %d: manual stop-recording → %s (beat=%.2f)", idx, gui_state, beat)
        elif pr.playing:
            # Stop playing — also stop channels the pattern triggered
            log.info("Pattern %d: control-button STOP PLAYING at beat=%.2f", idx, beat)
            if self.engine:
                with pr._lock:
                    triggered_rows = set(e.row for e in pr.events if e.event_type == 'trigger')
                for r in triggered_rows:
                    if r >= len(self.engine.channels):
                        continue
                    ch = self.engine.channels[r]
                    if ch.playing:
                        self.engine.stop_channel(r)
                        self.grid.clear_row_leds(r, self.config.grid_columns)
            with pr._lock:
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
        """Called from clock thread — bounce to main thread."""
        self._clock_marshaller.pattern_state(idx, state)

    def _execute_pattern_state(self, idx: int, state: str):
        """Handle pattern state change on the Qt main thread."""
        log.info("Pattern %d auto-state → '%s' (main thread)", idx, state)
        if self.window:
            self.window.transport.set_pattern_state(idx, state)

    # ------------------------------------------------------------------ #
    #  Monome async connect handling                                      #
    # ------------------------------------------------------------------ #

    def _check_monome_connect(self):
        """Check if a monome grid just connected (called from _update_ui)."""
        # Snapshot the connect flag and row offset atomically so we can't
        # race a concurrent monome (re)connect from the asyncio thread that
        # mutates these between our reads.
        with self.grid._state_lock:
            if not self.grid._pending_monome_connect:
                return
            self.grid._pending_monome_connect = False
            row_offset = self.grid._monome_row_offset

        if not self.grid.info:
            return

        # Re-apply the control row config now that info.rows is known.
        # configure_control_row resolves -1 → last row using the real height,
        # so calling it here (after on_grid_ready set info.rows) gives the
        # correct physical row index and offset.
        cr = self.config.control_row
        # If cr.monome_row is in Launchpad CC mode (-2) from a previous
        # Launchpad session, a real monome has no separate CC top row —
        # reset to physical top row (0) so the offset and row count are correct.
        from mlrvp.config import CR_MONOME_ROW_LAUNCHPAD_CC
        if cr.monome_row == CR_MONOME_ROW_LAUNCHPAD_CC:
            cr.monome_row = 0
            if self.window:
                self.window.setup_view.grid_panel.set_control_row_config(
                    cr.enabled, cr.monome_row, cr.assignments
                )
        self.grid.configure_control_row(cr.enabled, cr.monome_row)
        with self.grid._state_lock:
            row_offset = self.grid._monome_row_offset

        rows = self.grid.info.rows - row_offset  # data rows
        cols = self.grid.info.columns
        # Clamp to engine channel count to avoid index-out-of-bounds
        if self.engine:
            rows = min(rows, len(self.engine.channels))
        self.config.row_count = max(1, rows)
        self.config.grid_columns = cols

        for ch in self.config.channels:
            ch.steps = cols
        if self.engine:
            for i in range(min(len(self.config.channels), len(self.engine.channels))):
                self.engine.channels[i].state.steps = cols

        # Reset LED column cache — newly connected grid needs a full refresh;
        # stale cached positions from a previous session would suppress updates.
        self._row_last_led_col = [-1] * MAX_ROWS
        self._row_was_playing = [False] * MAX_ROWS

        if self.window:
            self.window.set_grid_dimensions(rows, cols)
            self.window.set_hardware_grid_connected(True)
            self._sync_grid_columns_to_waveforms()
            # Auto-enable tilt control mapping for monome.
            # The 40h direct serial driver now parses ADC frames (opcodes
            # 0x11-0x17) and fires on_tilt — enabling is safe.
            # monome-monomeserial (legacy OSC bridge) does not expose
            # a tilt API, so skip it.
            is_legacy_osc = self.grid.info.type == "monome-monomeserial"
            if not is_legacy_osc:
                self.window.setup_view.grid_panel.set_tilt_enabled(True)
                self.window.setup_view.midi_panel.set_tilt_visible(True)
                self.grid.enable_tilt(True)
            # Monome row setting is editable (this is a monome, not a MIDI grid)
            self.window.setup_view.grid_panel.set_launchpad_connected(False)
            # Update Setup > Grid panel status label so the user can see the
            # monome is recognised (label already exists; was previously only
            # set for MIDI-grid connects).
            grid_type = self.grid.info.type or "monome"
            display_name = (
                f"{self.grid.info.id} ({grid_type})"
                if self.grid.info.id else grid_type
            )
            # Populate the dropdown FIRST so set_connection_status can find
            # and select the entry (it does a substring search on combo items).
            self._gui_scan_grids(auto_connect=False)
            self.window.setup_view.grid_panel.set_connection_status(
                True, display_name
            )
            self.window.set_status(
                f"Monome connected: {cols}×{self.grid.info.rows} "
                f"({rows} sample rows + control row)"
            )
            self.window._auto_resize_for_rows(rows)
            log.info("Monome connected: %dx%d (%d data rows)",
                     cols, self.grid.info.rows, rows)
            # Schedule a one-shot LED sync to clear firmware ghost LEDs.
            # Running 1.5 s after connect gives the firmware and our writer
            # time to finish their startup sequences before we force-resend
            # OFF for any dark cells that the firmware turned on at boot.
            QTimer.singleShot(1500, self._post_connect_led_sync)
            # Repaint LEDs to match the current grid mode after connect.
            from .config import GRID_MODE_MIX
            if self.config.grid_mode == GRID_MODE_MIX:
                QTimer.singleShot(100, self._mix_mode_update_all)
            else:
                QTimer.singleShot(100, self.grid.clear)

    def _post_connect_led_sync(self):
        """Force OFF for firmware ghost LEDs after grid connect has settled."""
        if self.grid and self.grid.connected:
            self.grid.force_hardware_led_sync()

    def _check_monome_disconnect(self):
        """Handle a physical monome unplug detected on the background thread."""
        with self.grid._state_lock:
            if not self.grid._pending_monome_disconnect:
                return
            self.grid._pending_monome_disconnect = False
        if self.window:
            self.window.set_hardware_grid_connected(False)
            self.window.setup_view.grid_panel.set_connection_status(False)
            self.window.set_status("Monome grid disconnected")
        log.info("Monome hardware disconnect processed in UI")

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

        Disk I/O runs on a background thread so the GUI never blocks.
        """
        if not self.engine or not (0 <= row < MAX_ROWS):
            return
        self._mark_preset_dirty()
        ch = self.engine.channels[row]
        ch._load_gen += 1  # invalidate any prior in-flight loads

        if ch._playing:
            # Playing row: stage the filepath now so trigger picks it up.
            # The background thread will fill in the pre-loaded buffer.
            ch._pending_sample_path = filepath
            self.config.channels[row].filename = filepath
            self.config.channels[row].steps = self.config.grid_columns

        # Fire the background loader — _on_bg_sample_loaded will apply the
        # result on the main thread once the I/O finishes.
        self._load_sample_bg(row, filepath)

    def clear_channel_sample(self, row: int):
        """Remove the sample from a channel, restoring it to empty state."""
        self._mark_preset_dirty()
        if self.engine and 0 <= row < len(self.engine.channels):
            ch = self.engine.channels[row]
            self.engine.stop_channel(row)
            ch.sample = None
            ch._position = 0.0
            ch._pending_sample_path = None
            ch._pending_sample_buffer = None
            ch._load_gen += 1  # invalidate in-flight background loads
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
        w.transport.preset_reload_requested.connect(self._gui_preset_reload)
        w.transport.setlist_changed.connect(self._gui_setlist_changed)
        w.transport.setlist_edit_requested.connect(self._gui_setlist_edit)
        w.transport.sync_bpm_requested.connect(self._gui_sync_bpm)
        w.transport.tap_tempo_requested.connect(self._gui_tap_tempo)
        w.transport.group_stop_requested.connect(self._gui_group_stop)
        w.transport.stop_all_requested.connect(self._gui_stop_all)
        w.transport.grid_mode_cycle_requested.connect(self._gui_grid_mode_cycle)
        w.setup_view.link_toggled.connect(self._gui_link_toggled)

        # --- Channel view ---
        w.channel_view.sample_load_requested.connect(self.load_sample_to_channel)
        w.channel_view.sample_clear_requested.connect(self.clear_channel_sample)
        w.channel_view.param_changed.connect(self._gui_channel_param)
        w.channel_view.live_param_changed.connect(self._on_live_param_changed)
        w.channel_view.live_rec_requested.connect(self._on_live_rec_button)
        w.channel_view.live_export_requested.connect(self._on_live_export_requested)

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
        w.effects_view.noise_gate_param_changed.connect(self._gui_noise_gate_param)
        w.effects_view.tremolo_param_changed.connect(self._gui_tremolo_param)
        w.effects_view.auto_wah_param_changed.connect(self._gui_auto_wah_param)
        w.effects_view.fet_limiter_param_changed.connect(self._gui_fet_limiter_param)
        w.effects_view.rotary_param_changed.connect(self._gui_rotary_param)
        w.effects_view.exciter_param_changed.connect(self._gui_exciter_param)
        w.effects_view.transient_shaper_param_changed.connect(self._gui_transient_shaper_param)
        w.effects_view.harmonizer_param_changed.connect(self._gui_harmonizer_param)
        w.effects_view.stereo_widener_param_changed.connect(self._gui_stereo_widener_param)
        w.effects_view.freq_shifter_param_changed.connect(self._gui_freq_shifter_param)
        w.effects_view.ring_mod_param_changed.connect(self._gui_ring_mod_param)
        w.effects_view.simple_eq_param_changed.connect(self._gui_simple_eq_param)
        w.effects_view.ping_pong_delay_param_changed.connect(self._gui_ping_pong_delay_param)
        w.effects_view.shimmer_reverb_param_changed.connect(self._gui_shimmer_reverb_param)
        w.effects_view.lofi_radio_param_changed.connect(self._gui_lofi_radio_param)
        w.effects_view.auto_pan_param_changed.connect(self._gui_auto_pan_param)
        w.effects_view.fuzz_param_changed.connect(self._gui_fuzz_param)
        w.effects_view.granular_freeze_param_changed.connect(self._gui_granular_freeze_param)
        w.effects_view.gate_reverb_param_changed.connect(self._gui_gate_reverb_param)
        w.effects_view.stutter_param_changed.connect(self._gui_stutter_param)
        w.effects_view.stowaway_param_changed.connect(self._gui_stowaway_param_from_gui)
        w.effects_view.effect_route_changed.connect(self._gui_effect_route)
        w.effects_view.midi_map_toggled.connect(self._gui_toggle_midi_map_mode)
        w.effects_view.rack_contents_changed.connect(self._on_rack_contents_changed)
        # Ensure engine state is immediately consistent with current rack layout.
        self._on_rack_contents_changed(w.effects_view.get_rack_sync_state())

        # --- Tools ---
        w.tools_view.record_toggled.connect(self._gui_record_toggle)
        w.tools_view.recorder_param_changed.connect(self._gui_recorder_param_changed)
        w.tools_view.slice_saved.connect(self._on_slice_saved)
        w.tools_view.slice_panel.load_to_channel.connect(
            lambda fp, ch: self.load_sample_to_channel(ch, fp)
        )
        # Library panel (top-level side tab): load-to-channel + preview
        lp = w.library_panel
        lp.load_to_channel.connect(
            lambda fp, ch: self.load_sample_to_channel(ch, fp) if ch >= 0 else None
        )
        lp.load_to_slicer.connect(self._on_library_load_to_slicer)
        lp.preview_requested.connect(self._on_library_preview)
        lp.preview_stop_requested.connect(self._on_library_preview_stop)
        # New recorder workflow signals (added in RecorderPanel)
        rp = w.tools_view.recorder_panel
        if hasattr(rp, "capture_preroll"):
            rp.capture_preroll.connect(self._on_capture_preroll)
        if hasattr(rp, "use_take"):
            rp.use_take.connect(self._on_use_take)
        if hasattr(rp, "play_take"):
            rp.play_take.connect(self._on_play_take)
        if hasattr(rp, "clear_takes"):
            rp.clear_takes.connect(self._on_clear_takes)
        if hasattr(rp, "send_to_slice"):
            rp.send_to_slice.connect(self._on_send_to_slice)

        # --- Setup ---
        w.setup_view.audio_setting_changed.connect(self._gui_audio_setting)
        w.setup_view.audio_panel.rescan_button.clicked.connect(self._populate_gui_devices)
        w.setup_view.grid_setting_changed.connect(self._gui_grid_setting)
        w.setup_view.grid_scan_requested.connect(self._gui_scan_grids)
        w.setup_view.midi_grid_connect_requested.connect(self._gui_midi_grid_connect)
        w.setup_view.midi_grid_disconnect_requested.connect(self._gui_midi_grid_disconnect)
        w.setup_view.save_default_requested.connect(self._gui_save_default)
        w.setup_view.load_default_requested.connect(self._gui_load_default_preset)

        # --- OSC ---
        w.setup_view.osc_setting_changed.connect(self._gui_osc_setting)

        # --- MIDI learn ---
        w.setup_view.midi_learn_toggled.connect(self._gui_toggle_midi_map_mode)
        w.setup_view.midi_setting_changed.connect(self._gui_midi_setting)
        w.setup_view.midi_mapping_range_changed.connect(self._gui_midi_range_changed)
        w.setup_view.midi_mapping_removed.connect(self._gui_midi_mapping_removed)
        w.setup_view.midi_mapping_toggle_changed.connect(self._gui_midi_toggle_changed)
        w.setup_view.midi_tilt_mapping_changed.connect(self._gui_tilt_mapping_changed)
        w.setup_view.midi_tilt_range_changed.connect(self._gui_tilt_range_changed)
        w.setup_view.midi_tilt_sensitivity_changed.connect(self._gui_tilt_sensitivity_changed)
        w.setup_view.midi_input_added.connect(self._gui_midi_input_added)
        w.setup_view.midi_input_removed.connect(self._gui_midi_input_removed)
        w.setup_view.tilt_enabled_changed.connect(self._gui_tilt_enabled_changed)
        w.setup_view.control_row_enabled_changed.connect(self._gui_control_row_enabled_changed)
        w.setup_view.control_row_setting_changed.connect(self._gui_control_row_setting_changed)
        w.setup_view.midi_control_row_learn_requested.connect(self._gui_control_row_learn_requested)
        self.midi.learn_completed.connect(self._on_midi_learn_complete)
        self.midi.mapping_displaced.connect(self._on_midi_mapping_displaced)

        # --- Virtual grid ---
        w.virtual_grid.button_pressed.connect(
            lambda x, y, state: self._on_grid_button(x, y, state)
        )
        w.grid_window.grid_size_changed.connect(self._on_virtual_grid_size_changed)

        # --- Main window ---
        w.close_requested.connect(self._on_close_requested)
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
        self._notify_automation("transport.tempo", float(bpm))

    def _gui_link_toggled(self, enabled: bool):
        """Handle Ableton Link toggle from the Setup > Audio panel."""
        self.config.link_enabled = enabled
        if self.clock:
            ok = self.clock.set_link_enabled(enabled)
            if enabled and not ok:
                # aalink not installed — revert the button
                if self.window:
                    self.window.setup_view.set_link_state(
                        False, 0, available=False
                    )
                self.config.link_enabled = False
                return
        self._save_startup_settings()
        log.info("Ableton Link %s", "enabled" if enabled else "disabled")

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
        # Use immediate clock change for responsive tap feel
        if self.clock:
            self.clock.set_tempo(bpm, immediate=True)
        # Delegate to the same path as the BPM spinner so channel speeds
        # and all config state update correctly.
        self._gui_tempo_changed(bpm)
        # Update the spinner display (blocked signals — already handled above)
        if self.window:
            self.window.transport.set_tempo(bpm)

    def _gui_pattern_state(self, idx: int, state: str):
        if idx >= len(self.patterns):
            return
        pr = self.patterns[idx]
        beat = self.clock.beat_position if self.clock else 0.0
        log.info("GUI pattern %d state → '%s' (beat=%.2f pr.recording=%s pr.playing=%s)",
                 idx, state, beat, pr.recording, pr.playing)
        if state == "recording":
            pr.toggle_record(beat)
        elif state == "playing":
            if pr.recording:
                # GUI button cycled recording → playing.  toggle_play() would
                # leave _recording=True, so handle the transition explicitly:
                # stop recording and start playback with whatever was captured.
                with pr._lock:
                    pr._recording = False
                    if pr.events:
                        pr._playing = True
                        pr._start_beat = beat
                        pr._last_playback_pos = -1.0
                # Nothing recorded — revert GUI button to idle.
                if not pr.playing and self.window:
                    self.window.transport.set_pattern_state(idx, "idle")
            elif not pr.playing:
                # Only start playback if not already running — if auto-stop
                # fired first and set _playing=True, toggle_play() would stop it.
                pr.toggle_play(beat)
        elif state == "clear":
            # Stop channels the pattern was driving before clearing
            if pr.playing and self.engine:
                with pr._lock:
                    triggered_rows = set(e.row for e in pr.events if e.event_type == 'trigger')
                for r in triggered_rows:
                    if r >= len(self.engine.channels):
                        continue
                    ch = self.engine.channels[r]
                    if ch.playing:
                        self.engine.stop_channel(r)
                        self.grid.clear_row_leds(r, self.config.grid_columns)
            pr.clear()
        else:
            # When pattern stops, stop all channels it had triggered
            if pr.playing and self.engine:
                with pr._lock:
                    triggered_rows = set(e.row for e in pr.events if e.event_type == 'trigger')
                for r in triggered_rows:
                    if r >= len(self.engine.channels):
                        continue
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
        # Hard-stop all playback — force_stop sets _playing=False immediately
        # so no release tails re-seed the FX buffers after reset.
        if self.engine:
            for i in range(self.config.row_count):
                self.engine.force_stop_channel(i)
            # Flush all FX internal buffers (delay lines, reverb tails, etc.)
            # so stale audio doesn't bleed into the fresh preset.
            self.engine.reset_all_fx()
            # Zero the ring buffer so stale pre-computed blocks don't play out.
            self.engine.flush_ring_buffer()

        # Stop all pattern recorders — stale events must not fire on the new preset.
        for pr in self.patterns:
            pr.clear()
        if self.window:
            for i in range(NUM_PATTERN_RECORDERS):
                self.window.transport.set_pattern_state(i, "idle")

        # Clear all grid LEDs
        for i in range(self.config.row_count):
            self.grid.clear_row_leds(i, self.config.grid_columns)

        # Preserve system-level settings that should survive a "New"
        saved = {
            "sample_rate": self.config.sample_rate,
            "buffer_size": self.config.buffer_size,
            "output_device": self.config.output_device,
            "input_device": self.config.input_device,
            "host_api": self.config.host_api,
            "row_count": self.config.row_count,
            "grid_columns": self.config.grid_columns,
            "monome_backend": getattr(self.config, "monome_backend", MONOME_BACKEND_AUTO),
            "tempo": self.config.tempo,
            "quantize": self.config.quantize,
            "snap_speed_to_rate": getattr(self.config, "snap_speed_to_rate", False),
            "osc_enabled": getattr(self.config, "osc_enabled", False),
            "osc_port": getattr(self.config, "osc_port", 8000),
            "master_volume": getattr(self.config, "master_volume", 1.0),
        }

        # Reset entire config to factory defaults
        from mlrvp.config import ChannelState
        fresh = AppConfig()
        self.config = fresh

        # Drop any in-memory recorder takes from the previous preset
        self._on_clear_takes()
        # Cancel any pending LIVE long-press / count-in / pending-start state
        self._live_clear_all_pending()

        # Restore preserved system settings
        for key, val in saved.items():
            setattr(self.config, key, val)

        # Reset channel assignments
        for i in range(MAX_ROWS):
            if i < len(self.config.channels):
                self.config.channels[i] = ChannelState(group=0)

        # Reset engine channel state
        if self.engine:
            self.engine.config = self.config
            for i in range(self.config.row_count):
                ech = self.engine.channels[i]
                ech.sample = None
                ech._position = 0.0
                ech.state.speed = 1.0
                ech.state.mode = PlaybackMode.LOOP
                ech.state.gain = 1.0
                ech.state.group = 0
                ech.state.mute = False
                ech.state.sel_start = 0.0
                ech.state.sel_end = 1.0
                ech.state.reverse = False
                ech._pending_sample_path = None
                ech._pending_sample_buffer = None
                ech._load_gen += 1  # invalidate any in-flight background loads
                # Clear mode-specific state
                ech._seq_steps.clear()
                ech._seq_column = -1
                ech._seq_playing_step = False
                ech._seq_stopped = False
                ech._slice_btn1 = None
                ech._slice_btn2 = None
                ech._slice_loop_start = None
                ech._slice_loop_end = None
                ech._slice_reverse = False
                ech._slice_latched = False
                ech._slice_held = set()
                ech._slice_col_start = -1
                ech._slice_col_end = -1
            self._apply_config_to_engine()

        # Clear waveform displays BEFORE applying config to GUI, so
        # even if _apply_config_to_gui() fails partway the displays are clean.
        if self.window:
            for i in range(self.config.row_count):
                strip = self.window.channel_view.get_strip(i)
                strip._waveform.set_waveform(None, "")
                strip._waveform.set_playing(False)
                strip._waveform.set_active_column(-1)
                strip._waveform.set_seq_steps(frozenset())
                strip._sample_name.setText("(no sample)")

        # Update GUI controls from reset config
        self._apply_config_to_gui()

        # De-select active preset (fresh slate, not tied to any preset)
        self.presets.active_index = -1
        self._refresh_setlist_ui()
        self._clear_preset_dirty()
        # Clear tilt mappings — fresh preset has no tilt assignments
        self._apply_tilt_mappings([])
        if self.window:
            self.window.set_status("New preset — all channels cleared")

    def _gui_save_preset(self, name: str):
        """Save current state to the preset library and add to setlist.

        Flow:
        - If current setlist is Default → open setlist editor for New Setlist,
          then prompt the user to name the preset before saving.
        - If name is '<new>' or empty → prompt the user to name the preset.
        - Otherwise save directly with the given name.
        """
        from PySide6.QtWidgets import QInputDialog

        # If on Default setlist, force the user to create a real setlist first
        if self.presets.setlist_name == "Default":
            from mlrvp.gui.setlist_editor import SetlistEditorDialog
            dlg = SetlistEditorDialog(self.presets, self.window)
            dlg.setlist_changed.connect(self._refresh_setlist_ui)
            # Trigger the "New Setlist" input dialog before showing the editor
            sl_name = dlg.open_for_new_setlist()
            if sl_name == "Default":
                # User cancelled the new-setlist prompt — don't save
                return
            # Let user manage the editor (add presets, reorder, etc.)
            dlg.exec()
            # Re-check in case user navigated back to Default inside the editor
            if self.presets.setlist_name == "Default":
                return

        # If no preset name or placeholder, prompt for one
        if not name or name == "<new>":
            name, ok = QInputDialog.getText(
                self.window, "Save Preset", "Preset name:")
            if not ok or not name.strip():
                return
            name = name.strip()

        # Save to library (async write — errors logged in bg thread)
        self.presets.save_preset(
            name, self.config,
            midi_mappings=self.midi.get_mappings_serializable(),
            tilt_mappings=self._get_tilt_mappings_serializable())

        # If this name isn't already the currently-active entry in the
        # setlist, add it (avoids duplicates when overwriting).
        sp = self.presets.setlist_presets
        idx = self.presets.active_index
        if 0 <= idx < len(sp) and sp[idx] == name:
            # Overwriting the currently-active slot — just persist
            self.presets.save_setlist()
        else:
            # New preset or name changed — add to end of setlist
            # (add_to_setlist calls save_setlist internally)
            new_idx = self.presets.add_to_setlist(name)
            if new_idx >= 0:
                self.presets.active_index = new_idx

        self._refresh_setlist_ui()
        self._clear_preset_dirty()
        if self.window:
            self.window.set_status(f"Saved: {name}")
            self.window.transport.flash_save_button()

    def _gui_live_recall_preset(self, idx: int):
        """Recall a preset from the setlist.

        - Stops all current playback immediately (no release tail).
        - Resets FX buffers so old delay/reverb tails don't bleed through.
        - Applies effects, mixer, transport, channel params from the preset.
        - Loads new samples via background threads.
        """
        data = self.presets.load_setlist_preset(idx)
        if not data:
            return

        # ── 1. Hard-stop everything ──
        if self.engine:
            for i in range(self.config.row_count):
                self.engine.force_stop_channel(i)
            self.engine.reset_all_fx()
            self.engine.flush_ring_buffer()

        # Clear all grid LEDs
        for i in range(self.config.row_count):
            self.grid.clear_row_leds(i, self.config.grid_columns)

        # ── 2. Apply config (effects, mixer, transport, channel params) ──
        self.config = self.presets.apply_preset(data, self.config)
        self._sync_grid_backend_to_controller(restart=True)

        # Drop any in-memory recorder takes from the previous preset
        self._on_clear_takes()
        # Cancel any pending LIVE long-press / count-in / pending-start state
        self._live_clear_all_pending()
        # Stop all pattern recorders — stale events must not fire on the new preset.
        for pr in self.patterns:
            pr.clear()
        if self.window:
            for i in range(NUM_PATTERN_RECORDERS):
                self.window.transport.set_pattern_state(i, "idle")

        # Push ALL params to engine (no playing rows to protect)
        self._apply_config_to_engine()
        self._apply_config_to_gui()

        # ── 3. Clear waveform displays while bg loads run ──
        if self.window:
            for i in range(self.config.row_count):
                strip = self.window.channel_view.get_strip(i)
                strip._waveform.set_waveform(None, "")
                strip._waveform.set_playing(False)
                strip._sample_name.setText("(loading...)")

        # ── 4. Load samples — all disk I/O in background threads ──
        if self.engine:
            import os
            for i, ch_cfg in enumerate(self.config.channels[:self.config.row_count]):
                ech = self.engine.channels[i]
                ech._load_gen += 1
                ech._pending_sample_path = None
                ech._pending_sample_buffer = None
                if ch_cfg.filename and os.path.isfile(ch_cfg.filename):
                    self._load_sample_bg(i, ch_cfg.filename, preserve_speed=True)
                elif not ch_cfg.filename:
                    # Ensure the row is fully cleared
                    ech.sample = None
                    if self.window:
                        strip = self.window.channel_view.get_strip(i)
                        strip._sample_name.setText("(no sample)")

        # ── 5. Restore MIDI mappings ──
        midi_maps = data.get("midi_mappings", [])
        self.midi.load_mappings(midi_maps)
        for m in midi_maps:
            self._register_midi_param_callback(m["param_path"])
        self._apply_tilt_mappings(data.get("tilt_mappings", []))
        self._refresh_midi_mapping_table()

        self._refresh_setlist_ui()
        self._clear_preset_dirty()
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
        if new_idx == cur:
            return
        self._gui_live_recall_preset(new_idx)

    def _gui_preset_next(self):
        """Navigate to next preset in the setlist."""
        n = len(self.presets.setlist_presets)
        if n == 0:
            return
        cur = self.presets.active_index
        new_idx = (cur + 1) % n
        if new_idx == cur:
            return
        self._gui_live_recall_preset(new_idx)

    def _gui_preset_reload(self):
        """Reload the currently active preset."""
        idx = self.presets.active_index
        if idx < 0 or idx >= len(self.presets.setlist_presets):
            return
        self._gui_live_recall_preset(idx)

    def _gui_setlist_changed(self, name: str):
        """Switch to a different setlist."""
        if name and name != self.presets.setlist_name:
            self.presets.load_setlist(name)
            self._refresh_setlist_ui()
            # Auto-load the first preset in the new setlist
            if self.presets.setlist_presets:
                self._gui_live_recall_preset(0)
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
            self._sync_grid_backend_to_controller(restart=True)
            self._on_clear_takes()
            self._live_clear_all_pending()
            # Stop all pattern recorders — stale events must not fire on the new preset.
            for pr in self.patterns:
                pr.clear()
            if self.window:
                for i in range(NUM_PATTERN_RECORDERS):
                    self.window.transport.set_pattern_state(i, "idle")
            self._apply_config_to_engine()
            self._apply_config_to_gui()
            # Clear ALL waveform displays so old samples don't linger
            # while background loads run.
            self._clear_all_waveform_displays()
            self._reload_samples_from_config()
            # Restore MIDI and tilt mappings
            midi_maps = data.get("midi_mappings", [])
            self.midi.load_mappings(midi_maps)
            for m in midi_maps:
                self._register_midi_param_callback(m["param_path"])
            self._apply_tilt_mappings(data.get("tilt_mappings", []))
            self._refresh_midi_mapping_table()
            self._refresh_setlist_ui()
            self._clear_preset_dirty()
            if self.window:
                self.window.set_status(f"Preset {idx + 1} recalled")

    def _gui_open_preset_file(self, path: str):
        """Load a preset from a specific file path.

        JSON parsing happens on a background thread so slow storage
        doesn't block the main thread or event loop.
        """
        def _read():
            import json
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                log.error("Failed to read preset file: %s", e)
                return None

        def _apply(data):
            if data is None:
                if self.window:
                    self.window.set_status(f"Error loading preset: {path}")
                return
            try:
                from mlrvp.preset.manager import migrate_preset
                data = migrate_preset(data)
                self.config = self.presets.apply_preset(data, self.config)
                self._sync_grid_backend_to_controller(restart=True)
                self._on_clear_takes()
                self._live_clear_all_pending()
                # Stop all pattern recorders — stale events must not fire on the new preset.
                for pr in self.patterns:
                    pr.clear()
                if self.window:
                    for i in range(NUM_PATTERN_RECORDERS):
                        self.window.transport.set_pattern_state(i, "idle")
                self._apply_config_to_engine()
                self._apply_config_to_gui()
                self._clear_all_waveform_displays()
                self._reload_samples_from_config()
                # Restore MIDI and tilt mappings
                midi_maps = data.get("midi_mappings", [])
                self.midi.load_mappings(midi_maps)
                for m in midi_maps:
                    self._register_midi_param_callback(m["param_path"])
                self._apply_tilt_mappings(data.get("tilt_mappings", []))
                self._refresh_midi_mapping_table()
                if self.window:
                    self.window.set_status(f"Loaded preset: {path}")
            except Exception as e:
                log.error("Failed to open preset file: %s", e)
                if self.window:
                    self.window.set_status(f"Error loading preset: {e}")

        def _bg():
            data = _read()
            from PySide6.QtCore import QTimer
            # Bounce back to main thread for engine/GUI mutation
            QTimer.singleShot(0, lambda: _apply(data))

        threading.Thread(target=_bg, daemon=True).start()

    def _gui_save_preset_file(self, path: str):
        """Save current config to a specific file path (background write)."""
        from pathlib import Path as _Path
        from mlrvp.preset.manager import _atomic_write_json, _SafeFloatEncoder, CURRENT_PRESET_VERSION
        try:
            data = self.presets._serialize_config(self.config)
            payload = {
                "version": CURRENT_PRESET_VERSION,
                "config": data,
                "midi_mappings": self.midi.get_mappings_serializable(),
                "tilt_mappings": self._get_tilt_mappings_serializable(),
            }

            def _write():
                try:
                    _atomic_write_json(_Path(path), payload, cls=_SafeFloatEncoder)
                except Exception as e:
                    log.error("Failed to save preset file: %s", e)

            threading.Thread(target=_write, daemon=False).start()
            if self.window:
                self.window.set_status(f"Saved preset: {path}")
        except Exception as e:
            log.error("Failed to serialize preset: %s", e)
            if self.window:
                self.window.set_status(f"Error saving preset: {e}")

    def _gui_save_default(self, name: str):
        """Save current config as a named default preset (background write)."""
        from .config import get_defaults_dir
        from mlrvp.preset.manager import _atomic_write_json, _SafeFloatEncoder
        try:
            path = get_defaults_dir() / f"{name}.json"
            data = self.presets._serialize_config(self.config)

            def _write():
                try:
                    _atomic_write_json(path, data, cls=_SafeFloatEncoder)
                except Exception as e:
                    log.error("Failed to save default: %s", e)

            threading.Thread(target=_write, daemon=False).start()
            if self.window:
                self.window.set_status(f"Saved default: {name}")
        except Exception as e:
            log.error("Failed to serialize default: %s", e)
            if self.window:
                self.window.set_status(f"Error saving default: {e}")

    def _gui_load_default_preset(self, name: str):
        """Load a named default preset (emitted by Setup > Default State 'Load' button)."""
        import json
        from .config import get_defaults_dir
        if not name:
            return
        try:
            path = get_defaults_dir() / f"{name}.json"
            if not path.exists():
                if self.window:
                    self.window.set_status(f"Default preset not found: {name}")
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            self.config = self.presets.apply_preset(data, self.config)
            self._sync_grid_backend_to_controller(restart=True)
            self._apply_config_to_gui()
            if self.window:
                self.window.set_status(f"Loaded default: {name}")
        except Exception as e:
            log.error("Failed to load default preset '%s': %s", name, e)
            if self.window:
                self.window.set_status(f"Error loading default: {e}")

    def _load_startup_settings(self):
        """Load lightweight startup settings that must be known before init_grid().

        These are a small subset of the full config (just the settings that
        affect which hardware backend is used for grid discovery).  They are
        written by _save_startup_settings() whenever a relevant setting changes.
        """
        import json
        from .config import get_defaults_dir, MONOME_BACKENDS
        try:
            path = get_defaults_dir() / "startup_settings.json"
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                backend = data.get("monome_backend", "")
                if backend in MONOME_BACKENDS:
                    self.config.monome_backend = backend
                    log.info("Startup settings loaded: monome_backend=%s", backend)
                # Grid mode is intentionally not restored — the app always
                # starts in GRID_MODE_GRID regardless of the last session.
                # Active groups
                saved_ag = data.get("active_groups")
                if saved_ag is not None:
                    valid_ag = [c for c in (4, 6, 8) if c <= NUM_GROUPS]
                    try:
                        ag = int(saved_ag)
                    except (TypeError, ValueError):
                        ag = valid_ag[-1]
                    if ag not in valid_ag:
                        ag = min(valid_ag, key=lambda x: abs(x - ag))
                    self.config.active_groups = ag
                    log.info("Startup settings loaded: active_groups=%d", ag)
                # Ableton Link
                if "link_enabled" in data:
                    self.config.link_enabled = bool(data["link_enabled"])
                # Control row settings
                cr_data = data.get("control_row", {})
                if cr_data:
                    self.config.control_row.enabled = bool(
                        cr_data.get("enabled", self.config.control_row.enabled)
                    )
                    self.config.control_row.monome_row = int(
                        cr_data.get("monome_row", self.config.control_row.monome_row)
                    )
                    # Load assignments list; fall back to legacy num_group_stops/num_patterns
                    from mlrvp.config import (
                        CR_SLOT_GROUP_STOP, CR_SLOT_PATTERN, _default_cr_assignments
                    )
                    saved_assignments = cr_data.get("assignments")
                    if saved_assignments and isinstance(saved_assignments, list):
                        # Pad / truncate to 8
                        from mlrvp.config import CR_SLOT_EMPTY
                        empty = {"type": CR_SLOT_EMPTY, "value": 0}
                        saved_assignments = (saved_assignments + [empty] * 8)[:8]
                        self.config.control_row.assignments = saved_assignments
                    elif "num_group_stops" in cr_data or "num_patterns" in cr_data:
                        # Migrate legacy format
                        n_grp = max(1, min(7, int(cr_data.get("num_group_stops", 4))))
                        n_pat = max(1, min(7, int(cr_data.get("num_patterns", 4))))
                        if n_grp + n_pat > 8:
                            n_pat = max(1, 8 - n_grp)
                        from mlrvp.config import CR_SLOT_EMPTY
                        new_assignments = (
                            [{"type": CR_SLOT_GROUP_STOP, "value": i} for i in range(n_grp)] +
                            [{"type": CR_SLOT_PATTERN,    "value": i} for i in range(n_pat)] +
                            [{"type": CR_SLOT_EMPTY,      "value": 0}] * max(0, 8 - n_grp - n_pat)
                        )
                        self.config.control_row.assignments = new_assignments
        except Exception as e:
            log.warning("Could not load startup settings: %s", e)

    def _settings_save_worker(self):
        """Drain the settings-save queue on a persistent background thread.

        Coalesces rapid bursts (e.g. repeated mode switches) so only the most
        recent save request is written to disk.  A None sentinel shuts the
        worker down cleanly on app exit.
        """
        q = self._settings_save_queue
        while True:
            item = q.get()
            if item is None:  # sentinel — exit
                break
            # Drain any pending duplicates so we only write once per burst
            while not q.empty():
                extra = q.get_nowait()
                if extra is None:
                    return
            self._save_startup_settings()

    def _save_startup_settings(self):
        """Persist lightweight startup settings to data/defaults/startup_settings.json."""
        import json
        from .config import get_defaults_dir
        try:
            data = {
                "monome_backend": self.config.monome_backend,
                "grid_mode": self.config.grid_mode,
                "active_groups": self.config.active_groups,
                "link_enabled": getattr(self.config, "link_enabled", False),
                "control_row": {
                    "enabled": self.config.control_row.enabled,
                    "monome_row": self.config.control_row.monome_row,
                    "assignments": self.config.control_row.assignments,
                },
            }
            path = get_defaults_dir() / "startup_settings.json"
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("Could not save startup settings: %s", e)

    # --- Channel param handlers ---

    def _gui_channel_param(self, row: int, param: str, value):
        if not (0 <= row < MAX_ROWS):
            return
        # Transient params that don't change saved state
        _TRANSIENT = {"stop", "playing", "rate_half", "rate_double"}
        if param not in _TRANSIENT:
            self._mark_preset_dirty()
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
                    ech._seq_stopped = False
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
                    # Clear stale pitch-mode state so it doesn't bleed
                    # into other modes (wrong audio data / LED ghost)
                    if self.engine:
                        ech = self.engine.channels[row]
                        ech._active_pitch_data = None
                        ech._active_pitch_semitone = None
                        ech._pitch_mode_col = -1
                        ech._pitch_mode_mult = 1.0
            # Clear SLICE state when leaving SLICE mode to prevent LED ghosts
            # and stale audio boundaries bleeding into the new mode.
            if old_mode == PlaybackMode.SLICE and value != PlaybackMode.SLICE:
                if self.engine:
                    ech = self.engine.channels[row]
                    ech._slice_btn1 = None
                    ech._slice_btn2 = None
                    ech._slice_loop_start = None
                    ech._slice_loop_end = None
                    ech._slice_reverse = False
                    ech._slice_latched = False
                    ech._slice_held = set()
                    ech._slice_col_start = -1
                    ech._slice_col_end = -1
                self.grid.clear_row_leds(row, self.config.grid_columns)
            # Cancel any pending LIVE state when leaving LIVE mode.
            if old_mode == PlaybackMode.LIVE and value != PlaybackMode.LIVE:
                timer = self._live_long_press_timers.pop(row, None)
                if timer is not None:
                    timer.stop()
                    timer.deleteLater()
                self._live_press_time.pop(row, None)
                self._live_pending_start.pop(row, None)
                self._live_countin_remaining.pop(row, None)
                if self.engine and self.engine.live_recording_row == row:
                    self.engine.cancel_live_recording()
        elif param == "stop":
            if self.engine:
                ech = self.engine.channels[row]
                self.engine.force_stop_channel(row)
                # Reset SEQ state and clear pattern
                if ech.state.mode == PlaybackMode.SEQ:
                    ech._seq_steps.clear()
                    ech._seq_column = -1
                    ech._seq_playing_step = False
                    ech._seq_stopped = False
                # Reset SLICE state
                if ech.state.mode == PlaybackMode.SLICE:
                    ech._slice_btn1 = None
                    ech._slice_btn2 = None
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
            # Refresh physical controller LEDs — PITCH mode keeps static DIM/BRIGHT
            # LEDs whose color is determined by group; must repaint after a group change
            if ch.mode == PlaybackMode.PITCH:
                self._update_pitch_mode_leds(row)
        elif param == "gain":
            ch.gain = value
            if self.engine:
                self.engine.channels[row].state.gain = value
            self._notify_automation(f"ch.{row}.gain", float(value))
        elif param == "speed":
            ch.speed = value
            if ch.mode != PlaybackMode.SEQ:
                ch.normal_speed = value
            if self.engine:
                # Only push to engine if no staged sample (old audio keeps its speed)
                ech = self.engine.channels[row]
                if not getattr(ech, '_pending_sample_path', None):
                    ech.state.speed = value
            self._notify_automation(f"ch.{row}.speed", float(value))
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
        elif param == "pitch_loop":
            ch.pitch_loop = bool(value)
            if self.engine:
                self.engine.channels[row].state.pitch_loop = bool(value)
        elif param == "seq_step_div":
            div_idx = max(0, min(len(SEQ_STEP_DIV_BEATS) - 1, int(value)))
            ch.seq_step_div = div_idx
            if self.engine:
                self.engine.channels[row].state.seq_step_div = div_idx
                # Reset the column so the next tick picks up the right position
                self.engine.channels[row]._seq_column = -1
        elif param == "mute":
            ch.mute = value
            self._apply_solo_mutes()
            self._notify_automation(f"ch.{row}.mute", 1.0 if value else 0.0)
        elif param == "solo":
            ch.solo = bool(value)
            self._apply_solo_mutes()
            if self.window:
                strip = self.window.channel_view.get_strip(row)
                strip._solo_btn.blockSignals(True)
                strip._solo_btn.setChecked(ch.solo)
                strip._solo_btn.blockSignals(False)
        elif param == "reverse":
            ch.reverse = bool(value)
            if self.engine:
                self.engine.channels[row].state.reverse = bool(value)
        elif param == "shot_hold":
            ch.shot_hold = bool(value)
            if self.engine:
                self.engine.channels[row].state.shot_hold = bool(value)
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
        elif param == "ch_lp":
            ch.ch_lp = float(value)
            if self.engine:
                self.engine.channels[row].state.ch_lp = ch.ch_lp
            self._notify_automation(f"ch.{row}.ch_lp", float(value))
        elif param == "ch_hp":
            ch.ch_hp = float(value)
            if self.engine:
                self.engine.channels[row].state.ch_hp = ch.ch_hp
            self._notify_automation(f"ch.{row}.ch_hp", float(value))
        elif param == "pan":
            ch.pan = max(0.0, min(1.0, float(value)))
            if self.engine:
                self.engine.channels[row].state.pan = ch.pan
            self._notify_automation(f"ch.{row}.pan", ch.pan)

    def _apply_solo_mutes(self):
        """Recompute engine mute state for all channels based on solo flags.

        If any channel is soloed, all non-soloed channels are effectively muted.
        The GUI MUTE button always reflects the user's intent (ch.mute), not
        the effective engine state.
        """
        if not self.engine:
            return
        active = self.config.row_count
        any_solo = any(self.config.channels[i].solo for i in range(active))
        for i in range(active):
            ch = self.config.channels[i]
            effective = ch.mute or (any_solo and not ch.solo)
            self.engine.channels[i].state.mute = effective

    # --- Mixer handlers ---

    def _gui_group_volume(self, group: int, vol: float):
        if not (0 <= group < NUM_GROUPS):
            return
        self._mark_preset_dirty()
        self.config.groups[group].volume = vol
        if self.engine:
            self.engine.set_group_volume(group, vol)
        self._notify_automation(f"grp.{group}.volume", float(vol))
        # Keep mix-mode LED column in sync whenever volume changes.
        # Deferred via QTimer so LED I/O doesn't run on the same call stack
        # as the audio volume change (avoids GIL contention with DSP thread).
        from .config import GRID_MODE_MIX
        if self.config.grid_mode == GRID_MODE_MIX:
            QTimer.singleShot(0, lambda g=group: self._mix_mode_update_col(g))

    def _gui_group_send(self, group: int, send: float):
        if not (0 <= group < NUM_GROUPS):
            return
        self._mark_preset_dirty()
        send = max(0.0, min(1.0, send))
        self.config.groups[group].send = send
        if self.engine:
            self.engine.set_group_send(group, send)
        self._notify_automation(f"grp.{group}.send", float(send))

    def _gui_group_mute(self, group: int, mute: bool):
        if not (0 <= group < NUM_GROUPS):
            return
        self._mark_preset_dirty()
        self.config.groups[group].mute = mute
        if self.engine:
            self.engine.set_group_mute(group, mute)
        self._notify_automation(f"grp.{group}.mute", 1.0 if mute else 0.0)

    def _gui_group_stop(self, group: int):
        """Stop all channels in the specified group."""
        if self.engine:
            for i, ch in enumerate(self.engine.channels[:self.config.row_count]):
                if ch.state.group == group and (ch.playing or ch.state.mode == PlaybackMode.SEQ):
                    self.engine.force_stop_channel(i)
                    # Reset slice state
                    if ch.state.mode == PlaybackMode.SLICE:
                        ch._slice_btn1 = None
                        ch._slice_btn2 = None
                        ch._slice_loop_start = None
                        ch._slice_loop_end = None
                        ch._slice_reverse = False
                        ch._slice_latched = False
                        ch._slice_held = set()
                        ch._slice_col_start = -1
                        ch._slice_col_end = -1
                    if ch.state.mode == PlaybackMode.SEQ:
                        ch._seq_stopped = True
                        ch._seq_playing_step = False
        # Update grid LEDs — show step pattern (dim) but no active step
        if self.engine:
            for i, ch in enumerate(self.engine.channels[:self.config.row_count]):
                if ch.state.group == group:
                    if ch.state.mode == PlaybackMode.SEQ and ch._seq_steps:
                        ch._seq_column = -1
                        self._update_seq_leds(i)
                    else:
                        self.grid.clear_row_leds(i, self.config.grid_columns)

    def _gui_stop_all(self):
        """Stop all channels and clear all grid LEDs."""
        if self.engine:
            for i, ch in enumerate(self.engine.channels):
                self.engine.force_stop_channel(i)
                if ch.state.mode == PlaybackMode.SLICE:
                    ch._slice_btn1 = None
                    ch._slice_btn2 = None
                    ch._slice_loop_start = None
                    ch._slice_loop_end = None
                    ch._slice_reverse = False
                    ch._slice_latched = False
                    ch._slice_held = set()
                    ch._slice_col_start = -1
                    ch._slice_col_end = -1
                if ch.state.mode == PlaybackMode.SEQ:
                    ch._seq_stopped = True
                    ch._seq_playing_step = False
                    ch._seq_column = -1
        # Update grid LEDs — show step pattern (dim) but no active step
        for i in range(self.config.row_count):
            if self.engine and self.engine.channels[i].state.mode == PlaybackMode.SEQ and self.engine.channels[i]._seq_steps:
                self._update_seq_leds(i)
            else:
                self.grid.clear_row_leds(i, self.config.grid_columns)

    def _gui_grid_mode_cycle(self):
        """Cycle to the next grid display mode."""
        from .config import GRID_MODES
        current = self.config.grid_mode
        try:
            idx = GRID_MODES.index(current)
        except ValueError:
            idx = 0
        self._gui_grid_mode_set(GRID_MODES[(idx + 1) % len(GRID_MODES)])

    def _gui_grid_mode_set(self, mode: str):
        """Set the grid display mode and update transport bar button."""
        from .config import GRID_MODES, GRID_MODE_MIX
        if mode not in GRID_MODES:
            log.warning("Unknown grid mode: %s", mode)
            return
        prev_mode = self.config.grid_mode
        self.config.grid_mode = mode
        if self.window:
            self.window.transport.set_grid_mode(mode)
        if mode == GRID_MODE_MIX:
            # Update virtual grid col colors immediately (no I/O, just a list)
            if self.window:
                active = self.config.active_groups
                cols = self.config.grid_columns
                col_colors = list(range(active)) + [0] * (cols - active)
                self.window.virtual_grid.set_col_colors(col_colors)
            # Defer the bulk MIDI/serial LED writes to the next event loop cycle
            # so the DSP thread is not starved by 64+ send_message calls on the
            # main thread at the moment of the button press.
            QTimer.singleShot(0, self._mix_mode_update_all)
        elif prev_mode == GRID_MODE_MIX:
            # Clear virtual grid col colors immediately (no I/O)
            if self.window:
                self.window.virtual_grid.set_col_colors(None)
            # Reset per-row LED column cache so the next _update_ui cycle
            # redraws playback LEDs immediately instead of leaving dark cells
            # because the dedup cache still thinks those positions are current.
            self._row_last_led_col = [-1] * MAX_ROWS
            # Defer the grid.clear() bulk send for the same reason
            QTimer.singleShot(0, self.grid.clear)
        # Enqueue a settings save on the persistent background worker —
        # avoids per-call thread creation (kernel allocation ~0.5-5ms) which
        # was holding the GIL at the worst possible moment.
        self._settings_save_queue.put(True)
        log.info("Grid mode: %s", mode)

    # ------------------------------------------------------------------ #
    #  Mix mode helpers                                                   #
    # ------------------------------------------------------------------ #

    def _mix_mode_update_col(self, group: int):
        """Repaint one column in mix-mode to match the current group volume.

        Layout (row 0 = top of grid = loudest):
          - Rows below or equal to the fader row  → LED_BRIGHT
          - Row immediately above the fader row   → LED_DIM  (dim 'head' marker)
          - All rows above the head               → LED_OFF
        When the fader is at maximum (fader_row == 0) there is no head row.
        When the fader is fully off (fader_row > last row) the whole column is off.
        """
        rows = self.config.row_count
        cols = self.config.grid_columns
        if group >= cols or group >= self.config.active_groups:
            return  # column doesn't exist or group is inactive

        gain = self.config.groups[group].volume
        fader_pos = _gain_to_fader(gain)          # 0.0 (silent) … 1.0 (full)

        # Map fader position to the topmost lit row.
        # fader_pos == 1.0  → row 0  (top, fully lit column)
        # fader_pos == 0.0  → no lit rows
        # We quantise to rows: lit_rows = round(fader_pos * rows)
        lit_rows = int(round(fader_pos * rows))   # 0 … rows

        for row in range(rows):
            # row 0 = top; physical_row_from_bottom = (rows - 1 - row)
            rows_from_bottom = rows - row          # 1 … rows
            brightness = LED_BRIGHT if rows_from_bottom <= lit_rows else LED_OFF
            self.grid.set_led(group, row, brightness, color=group)

    def _mix_mode_update_all(self):
        """Repaint all group columns in mix mode.

        For MIDI grids (Launchpad MK2/MK3/X/Pro) the entire repaint is
        collapsed to **two** SysEx messages: one clear and one bulk-set.
        This eliminates the 64+ individual note-on burst that was competing
        with the DSP worker for the GIL long enough to drain the audio ring
        buffer and cause audible dropouts.

        For monome (serial led_level_row) we call set_row_colored() once per
        row as before — 8 messages total, which is already fast.
        """
        from .config import GRID_MODE_MIX
        if self.config.grid_mode != GRID_MODE_MIX:
            return  # deferred call fired after mode changed — bail out
        active = self.config.active_groups
        cols = self.config.grid_columns
        rows = self.config.row_count

        # Pre-compute lit_rows per active group
        lit_rows_per_grp = []
        for grp in range(min(active, cols)):
            gain = self.config.groups[grp].volume
            lit_rows_per_grp.append(int(round(_gain_to_fader(gain) * rows)))

        # --- Fast path: MIDI grid with SysEx bulk support ---------------
        midi_grid = getattr(self.grid, '_midi_grid', None)
        if midi_grid is not None and midi_grid.connected and hasattr(midi_grid, 'set_leds_bulk'):
            cells = []
            for row in range(rows):
                rows_from_bottom = rows - row
                for col, lit_rows in enumerate(lit_rows_per_grp):
                    if rows_from_bottom <= lit_rows:
                        cells.append((col, row, LED_BRIGHT, col))
            # set_leds_bulk() is now self-clearing — it explicitly sets
            # every grid cell (lit + unlit) in a single SysEx, so we don't
            # need a separate clear_all() call.  Total: 1 queue.put on the
            # main thread — minimal GIL work.
            midi_grid.set_leds_bulk(cells)
            # Keep virtual grid column colours in sync.  set_col_colors()
            # is a cheap no-op (early return) when the colour list is
            # unchanged from the last call.
            if self.window:
                col_colors = list(range(active)) + [0] * (cols - active)
                self.window.virtual_grid.set_col_colors(col_colors)
            return

        # --- Monome / classic MIDI fallback: one message per row --------
        for row in range(rows):
            rows_from_bottom = rows - row   # rows at top row, 1 at bottom row
            values = [LED_OFF] * cols
            colors = list(range(cols))      # column index == group index
            for col, lit_rows in enumerate(lit_rows_per_grp):
                if rows_from_bottom <= lit_rows:
                    values[col] = LED_BRIGHT
            # One led_level_row on monome; per-cell colored on MIDI grid
            self.grid.set_row_colored(row, values, colors)

        # Keep virtual grid column colours in sync
        if self.window:
            col_colors = list(range(active)) + [0] * (cols - active)
            self.window.virtual_grid.set_col_colors(col_colors)

    def _mix_mode_on_press(self, x: int, y: int):
        """Handle a grid button press while in mix mode.

        Column x maps to group x.  Row y (0 = top) sets the fader so that
        the clicked button becomes the topmost lit cell.
        """
        group = x
        if group >= NUM_GROUPS or group >= self.config.grid_columns:
            return
        rows = self.config.row_count
        if y >= rows:
            return

        # Convert row press to fader position.
        # Row 0 pressed → fader at top → lit_rows = rows → fader_pos = 1.0
        # Row (rows-1) pressed → lit_rows = 1 → fader_pos = 1/rows
        # Pressing bottom row does NOT silence; set to minimum audible step.
        lit_rows = rows - y                        # pressing row 0 → lit_rows=rows
        fader_pos = lit_rows / rows
        fader_pos = max(0.0, min(1.0, fader_pos))

        new_gain = _fader_to_gain(fader_pos)
        # Drive through the standard volume handler so automation, config,
        # engine, and mixer GUI all update in one shot.
        self._gui_group_volume(group, new_gain)
        # Also sync the mixer fader widget immediately
        if self.window:
            gc = self.window.mixer_view.get_group_channel(group)
            if gc:
                gc.set_volume(new_gain)

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
        if not (0 <= group < NUM_GROUPS):
            return
        self._mark_preset_dirty()
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
        self.config.master_hp = freq
        if self.engine:
            self.engine.set_master_hp(freq)
        if self.window:
            self.window.master_filter_view.set_hp_value_external(val)

    def _gui_master_lp(self, val: float):
        """Master LP trim: knob 0.0=200Hz (max trim), 1.0=20kHz (off)."""
        freq = 200.0 + val * 19800.0  # 200-20000 Hz range
        self.config.master_lp = freq
        if self.engine:
            self.engine.set_master_lp(freq)
        if self.window:
            self.window.master_filter_view.set_lp_value_external(val)

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
        self._notify_automation(f"delay.{bus}.{param}", float(value))

    def _gui_tape_delay_param(self, param: str, value, bus: int = 1):
        """Handle tape delay parameter change."""
        cfg, inst = self._resolve_bus("tape_delay", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"tape_delay.{bus}.{param}", float(value))

    def _gui_filter_a_param(self, param: str, value, bus: int = 1):
        """Handle filter A (shapeable LP/HP) parameter change."""
        cfg, inst = self._resolve_bus("filter_a", bus)
        setattr(cfg, param, value)
        if inst:
            inst.update_from_state(cfg)
        self._notify_automation(f"filter_a.{bus}.{param}", float(value))

    def _gui_filter_b_param(self, param: str, value, bus: int = 1):
        """Handle filter B (Moog ladder) parameter change."""
        cfg, inst = self._resolve_bus("filter_b", bus)
        setattr(cfg, param, value)
        if inst:
            inst.update_from_state(cfg)
        self._notify_automation(f"filter_b.{bus}.{param}", float(value))

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
        self._notify_automation(f"mod.{bus}.{param}", float(value))

    def _gui_reel_to_reel_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("reel_to_reel", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"reel_to_reel.{bus}.{param}", float(value))

    def _gui_cassette_tape_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("cassette_tape", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"cassette_tape.{bus}.{param}", float(value))

    def _gui_vhs_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("vhs", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"vhs.{bus}.{param}", float(value))

    def _gui_vinyl_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("vinyl", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"vinyl.{bus}.{param}", float(value))

    def _gui_compressor_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("compressor", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"compressor.{bus}.{param}", float(value))

    def _gui_glue_compressor_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("glue_compressor", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"glue_compressor.{bus}.{param}", float(value))

    def _gui_hall_reverb_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("hall_reverb", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"hall_reverb.{bus}.{param}", float(value))

    def _gui_bit_crusher_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("bit_crusher", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"bit_crusher.{bus}.{param}", float(value))

    def _gui_tube_overdrive_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("tube_overdrive", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"tube_overdrive.{bus}.{param}", float(value))

    def _gui_ether_drift_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("ether_drift", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"ether_drift.{bus}.{param}", float(value))

    def _gui_noise_gate_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("noise_gate", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"noise_gate.{bus}.{param}", float(value))

    def _gui_tremolo_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("tremolo", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"tremolo.{bus}.{param}", float(value))

    def _gui_auto_wah_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("auto_wah", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"auto_wah.{bus}.{param}", float(value))

    def _gui_fet_limiter_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("fet_limiter", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"fet_limiter.{bus}.{param}", float(value))

    def _gui_rotary_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("rotary", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"rotary.{bus}.{param}", float(value))

    def _gui_exciter_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("exciter", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"exciter.{bus}.{param}", float(value))

    def _gui_transient_shaper_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("transient_shaper", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"transient_shaper.{bus}.{param}", float(value))

    def _gui_harmonizer_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("harmonizer", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"harmonizer.{bus}.{param}", float(value))

    def _gui_stereo_widener_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("stereo_widener", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"stereo_widener.{bus}.{param}", float(value))

    def _gui_freq_shifter_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("freq_shifter", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"freq_shifter.{bus}.{param}", float(value))

    def _gui_ring_mod_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("ring_mod", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"ring_mod.{bus}.{param}", float(value))

    def _gui_simple_eq_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("simple_eq", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"simple_eq.{bus}.{param}", float(value))

    def _gui_ping_pong_delay_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("ping_pong_delay", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"ping_pong_delay.{bus}.{param}", float(value))

    def _gui_shimmer_reverb_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("shimmer_reverb", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"shimmer_reverb.{bus}.{param}", float(value))

    def _gui_lofi_radio_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("lofi_radio", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"lofi_radio.{bus}.{param}", float(value))

    def _gui_auto_pan_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("auto_pan", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"auto_pan.{bus}.{param}", float(value))

    def _gui_fuzz_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("fuzz", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"fuzz.{bus}.{param}", float(value))

    def _gui_granular_freeze_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("granular_freeze", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"granular_freeze.{bus}.{param}", float(value))

    def _gui_gate_reverb_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("gate_reverb", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"gate_reverb.{bus}.{param}", float(value))

    def _gui_stutter_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("stutter", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"stutter.{bus}.{param}", float(value))

    # ── Foreign Exchange (stowaway) plugin host handlers ─────────────

    def _gui_stowaway_param_from_gui(self, param: str, value, bus: int = 1):
        """Handle param_changed signals from ForeignExchangeModule in the rack."""
        bus_key = {1: "fx_bus_1", 2: "fx_bus_2", 3: "fx_bus_m",
                   4: "fx_bus_c", 5: "fx_bus_d"}.get(bus, "fx_bus_1")

        if param == "load_plugin":
            self._open_plugin_browser(bus_key, int(value))
        elif param == "open_editor":
            self._open_plugin_editor(bus_key, int(value))
        elif param == "assign_quick_param":
            knob_idx, slot_idx = value
            self._open_quick_param_assign(bus_key, slot_idx, knob_idx)
        elif param == "stowaway_enabled":
            if isinstance(value, (tuple, list)):
                slot_idx, on = value
            else:
                on = bool(value)
                slot_idx = 0
                log.warning("stowaway_enabled received plain bool – assuming slot 0")
            if self.engine:
                self.engine.set_plugin_slot_enabled(bus_key, slot_idx, bool(on))
            state = self._get_stowaway_config(bus_key, slot_idx)
            if state:
                state.enabled = bool(on)
        elif param == "wet_dry":
            slot_idx, val = value
            if self.engine:
                self.engine.set_plugin_slot_wet_dry(bus_key, slot_idx, float(val))
            state = self._get_stowaway_config(bus_key, slot_idx)
            if state:
                state.wet_dry = float(val)
        elif param.startswith("quick_"):
            qi = int(param.split("_")[1])
            slot_idx, val = value
            if self.engine:
                state = self._get_stowaway_config(bus_key, slot_idx)
                if state and qi < len(state.quick_map_params) and state.quick_map_params[qi]:
                    self.engine.set_plugin_slot_param(
                        bus_key, slot_idx, state.quick_map_params[qi], float(val)
                    )

    def _gui_stowaway_param(self, bus_key: str, slot_idx: int, param: str, value):
        """Handle MIDI-mapped stowaway param changes (from _register_midi_param_callback)."""
        if not self.engine:
            return
        if param == "enabled":
            self.engine.set_plugin_slot_enabled(bus_key, slot_idx, bool(value))
            state = self._get_stowaway_config(bus_key, slot_idx)
            if state:
                state.enabled = bool(value)
        elif param == "wet_dry":
            self.engine.set_plugin_slot_wet_dry(bus_key, slot_idx, float(value))
            state = self._get_stowaway_config(bus_key, slot_idx)
            if state:
                state.wet_dry = float(value)
        elif param.startswith("quick_"):
            qi = int(param.split("_")[1])
            state = self._get_stowaway_config(bus_key, slot_idx)
            if state and qi < len(state.quick_map_params) and state.quick_map_params[qi]:
                self.engine.set_plugin_slot_param(
                    bus_key, slot_idx, state.quick_map_params[qi], float(value)
                )

    def _get_stowaway_config(self, bus_key: str, slot_idx: int):
        """Get the StowawayState for a bus + slot from config."""
        bus_offset = {"fx_bus_1": 0, "fx_bus_2": 4, "fx_bus_m": 8,
                      "fx_bus_c": 12, "fx_bus_d": 16}.get(bus_key, 0)
        idx = bus_offset + slot_idx
        if hasattr(self.config, "stowaway_slots") and idx < len(self.config.stowaway_slots):
            return self.config.stowaway_slots[idx]
        return None

    def _open_plugin_browser(self, bus_key: str, slot_idx: int):
        """Open the plugin browser dialog for a stowaway slot."""
        from mlrvp.gui.plugin_browser import PluginBrowserDialog
        dlg = PluginBrowserDialog(self.window)

        def _on_selected(path: str, fmt: str):
            if not self.engine:
                return
            # Show LOADING state and flush paint so the user sees it
            self._update_stowaway_loading(bus_key, slot_idx)
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()

            # Load on main thread (required for show_editor to work later)
            ok = self.engine.load_plugin_slot(bus_key, slot_idx, path, fmt)
            if ok:
                state = self._get_stowaway_config(bus_key, slot_idx)
                if state:
                    state.plugin_path = path
                    state.plugin_format = fmt
                from pathlib import Path
                name = Path(path).stem
                self._update_stowaway_gui(bus_key, slot_idx, name, fmt)
            else:
                self._update_stowaway_error(bus_key, slot_idx)

        dlg.plugin_selected.connect(_on_selected)
        dlg.exec()

    def _open_plugin_editor(self, bus_key: str, slot_idx: int):
        """Open the plugin editor window for a stowaway slot."""
        if not self.engine:
            return
        state = self._get_stowaway_config(bus_key, slot_idx)
        if not state or not state.plugin_path:
            return
        from mlrvp.gui.plugin_editor_window import PluginEditorWindow
        from pathlib import Path
        win = PluginEditorWindow(
            engine=self.engine,
            bus_key=bus_key,
            slot_idx=slot_idx,
            plugin_name=Path(state.plugin_path).stem,
            plugin_path=state.plugin_path,
            plugin_format=state.plugin_format,
            parent=self.window,
        )
        win.show()

    def _open_quick_param_assign(self, bus_key: str, slot_idx: int, knob_idx: int):
        """Show a list of available plugin params for quick-knob assignment."""
        if not self.engine:
            return
        names = self.engine.get_plugin_slot_param_names(bus_key, slot_idx)
        if not names:
            return
        from PySide6.QtWidgets import QInputDialog
        chosen, ok = QInputDialog.getItem(
            self.window, "Assign Parameter",
            f"Choose parameter for P{knob_idx + 1}:",
            names, 0, False,
        )
        if ok and chosen:
            state = self._get_stowaway_config(bus_key, slot_idx)
            if state:
                while len(state.quick_map_params) <= knob_idx:
                    state.quick_map_params.append(None)
                state.quick_map_params[knob_idx] = chosen
            # Update GUI knob label
            self._update_stowaway_quick_label(bus_key, slot_idx, knob_idx, chosen)

    def _update_stowaway_gui(self, bus_key: str, slot_idx: int, name: str, fmt: str):
        """Update the Foreign Exchange module's display after plugin load."""
        if not self.window:
            return
        ev = self.window.effects_view
        rack = {"fx_bus_1": ev._rack_a, "fx_bus_2": ev._rack_b,
                "fx_bus_m": ev._rack_m, "fx_bus_c": ev._rack_c,
                "fx_bus_d": ev._rack_d}.get(bus_key)
        if not rack:
            return
        for mod in rack.get_modules():
            if getattr(mod, "effect_type", "") == f"stowaway_{slot_idx}":
                mod.set_plugin_loaded(name, fmt)
                break

    def _update_stowaway_loading(self, bus_key: str, slot_idx: int):
        """Show LOADING… state on the Foreign Exchange module."""
        if not self.window:
            return
        ev = self.window.effects_view
        rack = {"fx_bus_1": ev._rack_a, "fx_bus_2": ev._rack_b,
                "fx_bus_m": ev._rack_m, "fx_bus_c": ev._rack_c,
                "fx_bus_d": ev._rack_d}.get(bus_key)
        if not rack:
            return
        for mod in rack.get_modules():
            if getattr(mod, "effect_type", "") == f"stowaway_{slot_idx}":
                mod.set_loading()
                break

    def _update_stowaway_error(self, bus_key: str, slot_idx: int):
        """Show LOAD FAILED state on the Foreign Exchange module."""
        if not self.window:
            return
        ev = self.window.effects_view
        rack = {"fx_bus_1": ev._rack_a, "fx_bus_2": ev._rack_b,
                "fx_bus_m": ev._rack_m, "fx_bus_c": ev._rack_c,
                "fx_bus_d": ev._rack_d}.get(bus_key)
        if not rack:
            return
        for mod in rack.get_modules():
            if getattr(mod, "effect_type", "") == f"stowaway_{slot_idx}":
                mod._name_display.setText("LOAD FAILED")
                break

    def _update_stowaway_quick_label(self, bus_key: str, slot_idx: int,
                                      knob_idx: int, label: str):
        if not self.window:
            return
        ev = self.window.effects_view
        rack = {"fx_bus_1": ev._rack_a, "fx_bus_2": ev._rack_b,
                "fx_bus_m": ev._rack_m, "fx_bus_c": ev._rack_c,
                "fx_bus_d": ev._rack_d}.get(bus_key)
        if not rack:
            return
        for mod in rack.get_modules():
            if getattr(mod, "effect_type", "") == f"stowaway_{slot_idx}":
                mod.set_quick_knob_label(knob_idx, label)
                break

    def _gui_plate_reverb_param(self, param: str, value, bus: int = 1):
        cfg, inst = self._resolve_bus("plate_reverb", bus)
        setattr(cfg, param, value)
        if inst:
            setattr(inst.state, param, value)
        self._notify_automation(f"plate_reverb.{bus}.{param}", float(value))

    def _gui_record_toggle(self, armed: bool):
        """ARM button toggled in the Record tool tab.

        Workflow:
          armed=True  → arm recorder, then schedule start() based on quantize
                        + count_in. IMMEDIATE w/ no count-in: start now.
          armed=False → stop recording, finalize buffer, apply post-processing,
                        load to target channel (or push as a take).
        """
        if not self.engine:
            return
        rec_state = self.config.recorder

        if not armed:
            # Stop now and pull whatever we captured
            buf = self.engine.recorder.stop()
            self._recorder_pending_start = False
            self._recorder_count_in_remaining = 0
            self._on_recorded_buffer(buf, source="manual_stop")
            self._update_recorder_button_state(False, label="ARM")
            return

        # ARM path
        free_length = bool(rec_state.free_length)
        try:
            self.engine.recorder.arm(
                self.config.tempo,
                rec_state.length_bars,
                rec_state.length_beats,
                free_length=free_length,
            )
        except Exception as e:
            log.error("Recorder arm failed: %s", e)
            self._update_recorder_button_state(False, label="ARM")
            return

        # Loopback flag
        self.engine.recorder.use_loopback = bool(rec_state.loopback)

        quantize = int(rec_state.quantize)  # 0=IMMEDIATE, 1=BEAT, 2=BAR
        count_in_bars = max(0, int(rec_state.count_in))

        if quantize == 0 and count_in_bars == 0:
            # Start immediately
            self.engine.recorder.start()
            self._update_recorder_button_state(True, label="● REC")
            return

        # Quantized or count-in start: wait for clock boundary
        self._recorder_pending_start = True
        self._recorder_count_in_remaining = count_in_bars
        self._update_recorder_button_state(True, label="WAIT" if count_in_bars == 0 else f"COUNT {count_in_bars}")

    def _gui_recorder_param_changed(self, name: str, value):
        """Handle a parameter change from the RecorderPanel UI."""
        rec = self.config.recorder
        if not hasattr(rec, name):
            return
        # Type-coerce based on the existing field type
        current = getattr(rec, name)
        try:
            if isinstance(current, bool):
                value = bool(value)
            elif isinstance(current, int):
                value = int(value)
            elif isinstance(current, float):
                value = float(value)
        except (TypeError, ValueError):
            return
        setattr(rec, name, value)

        # Engine-side reactions
        if not self.engine:
            return
        if name == "loopback":
            self.engine.recorder.use_loopback = bool(value)
        elif name == "thru":
            self.engine.recorder.state.thru = bool(value)
        elif name == "loop":
            self.engine.recorder.state.loop = bool(value)

    # --- Recorder completion / buffer handling -------------------------

    def _on_recorder_complete(self):
        """Marshalled from audio thread: buffer fills automatically."""
        if not self.engine:
            return
        buf = self.engine.recorder.stop()
        self._on_recorded_buffer(buf, source="auto_complete")
        self._update_recorder_button_state(False, label="ARM")
        # Reflect the un-checked state back to the button
        if self.window:
            try:
                self.window.tools_view.recorder_panel.set_armed(False)
            except Exception:
                pass

    def _on_recorded_buffer(self, buf, source: str = "manual_stop"):
        """Apply post-processing pipeline, then load or push as take."""
        if buf is None:
            return
        rec = self.config.recorder
        try:
            buf = self._apply_recorder_processing(buf, rec)
        except Exception as e:
            log.warning("Recorder post-processing failed: %s", e)

        # Always store as a take (up to N slots)
        max_takes = max(1, int(rec.takes))
        self._recorder_takes.append(buf)
        # Keep only the most recent `max_takes` entries
        if len(self._recorder_takes) > max_takes:
            self._recorder_takes = self._recorder_takes[-max_takes:]
        # Refresh UI take list
        if self.window:
            try:
                rp = self.window.tools_view.recorder_panel
                if hasattr(rp, "set_takes"):
                    rp.set_takes([
                        (i, b.duration_sec) for i, b in enumerate(self._recorder_takes)
                    ])
                if hasattr(rp, "set_send_to_slice_enabled"):
                    rp.set_send_to_slice_enabled(True)
            except Exception:
                pass

        if rec.auto_load:
            self._load_buffer_to_target(buf)

    def _apply_recorder_processing(self, buf, rec_state):
        """Pure-numpy post-capture pipeline: trim silence → normalize → fade."""
        import numpy as np
        from mlrvp.audio.sample import SampleBuffer

        data = buf.data
        if data is None or data.size == 0:
            return buf
        sr = buf.sample_rate

        # 1) Trim silence (threshold = -50 dBFS)
        if getattr(rec_state, "trim_silence", False):
            thresh = 10 ** (-50.0 / 20.0)  # ~0.00316
            mono_amp = np.max(np.abs(data), axis=1)
            above = np.where(mono_amp > thresh)[0]
            if above.size > 0:
                start = int(above[0])
                end = int(above[-1]) + 1
                if end - start >= 64:  # at least ~1.5ms of audio
                    data = data[start:end].copy()

        # 2) Normalize to -0.1 dBFS
        if getattr(rec_state, "normalize", False):
            peak = float(np.max(np.abs(data)))
            if peak > 1e-6:
                target = 10 ** (-0.1 / 20.0)  # ~0.989
                gain = target / peak
                if gain != 1.0:
                    data = (data * gain).astype(np.float32, copy=False)

        # 3) Fade in/out
        fade_ms = int(getattr(rec_state, "fade_ms", 0))
        if fade_ms > 0 and len(data) > 4:
            n = min(int(sr * fade_ms / 1000.0), len(data) // 2)
            if n > 1:
                ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
                # ALWAYS copy before in-place mutation: the input data may
                # alias the recorder's internal buffer, and earlier branches
                # (trim_silence, normalize) may not have copied.
                if data is buf.data or not data.flags.writeable:
                    data = data.copy()
                data[:n] *= ramp
                data[-n:] *= ramp[::-1]

        return SampleBuffer(
            data=data.astype(np.float32, copy=False),
            sample_rate=sr,
            num_frames=int(data.shape[0]),
            num_channels=int(data.shape[1]),
            duration_sec=float(data.shape[0]) / float(sr),
        )

    def _load_buffer_to_target(self, buf):
        """Load a SampleBuffer into the recorder's target channel."""
        if not self.engine or buf is None:
            return
        target = max(0, min(MAX_ROWS - 1, int(self.config.recorder.buffer_idx)))
        try:
            self.engine.load_sample(target, buf)
            self.config.channels[target].filename = f"<recorded {buf.duration_sec:.2f}s>"
            self.config.channels[target].steps = self.config.grid_columns
            if self.window:
                try:
                    overview = get_waveform_overview(buf, num_points=800)
                    strip = self.window.channel_view.get_strip(target)
                    strip.set_waveform_data(overview, f"REC ({buf.duration_sec:.1f}s)")
                    strip._waveform.set_grid_columns(self.config.grid_columns)
                except Exception:
                    pass
                self.window.set_status(
                    f"Recording loaded to channel {target + 1} ({buf.duration_sec:.2f}s)"
                )
        except Exception as e:
            log.error("Failed to load recorded buffer to channel %d: %s", target, e)

    def _update_recorder_button_state(self, recording: bool, label: str = ""):
        """Update RecorderPanel button label / recording indicator."""
        if not self.window:
            return
        try:
            rp = self.window.tools_view.recorder_panel
            if hasattr(rp, "set_recording"):
                rp.set_recording(recording, label)
        except Exception:
            pass

    # --- Pre-roll / takes / send-to-slice ------------------------------

    def _on_capture_preroll(self, seconds: float):
        """User clicked 'Capture Last Xs' — grab from pre-roll ring buffer."""
        if not self.engine:
            return
        try:
            buf = self.engine.pre_roll.capture_last(float(seconds))
        except Exception as e:
            log.error("Pre-roll capture failed: %s", e)
            return
        if buf is None:
            if self.window:
                self.window.set_status("Pre-roll empty — no input has been captured yet")
            return
        self._on_recorded_buffer(buf, source="preroll")

    def _on_use_take(self, idx: int):
        if 0 <= idx < len(self._recorder_takes):
            self._load_buffer_to_target(self._recorder_takes[idx])

    def _on_play_take(self, idx: int):
        """Audition a take through the cue/preview service."""
        if not (0 <= idx < len(self._recorder_takes)):
            return
        buf = self._recorder_takes[idx]
        try:
            self.slicer_preview.play(buf.data, buf.sample_rate, loop=False)
        except Exception as e:
            log.error("Take preview failed: %s", e)

    def _on_library_preview(self, filepath: str, volume: float = 0.5):
        """Preview an audio file from the Library tab via the cue bus."""
        import os
        if not filepath or not os.path.isfile(filepath):
            return
        try:
            import soundfile as _sf
            data, sr = _sf.read(filepath, dtype="float32", always_2d=False)
            self.slicer_preview.set_volume(max(0.0, min(2.0, volume)))
            self.slicer_preview.play(data, sr, loop=False)
        except Exception as e:
            log.error("Library preview failed: %s", e)

    def _on_library_preview_stop(self):
        """Stop library preview playback (stop button or re-click on playing row)."""
        self.slicer_preview.stop()

    def _on_library_load_to_slicer(self, filepath: str):
        """Switch to the Tools > Slice tab and load the file."""
        if not self.window:
            return
        try:
            # Switch side panel to Tools tab then to Slice sub-tab
            side = self.window._side_tabs
            tools_idx = next(
                i for i in range(side.count()) if side.tabText(i) == "Tools"
            )
            side.setCurrentIndex(tools_idx)
            self.window.tools_view.slice_panel.load_file(filepath)
        except Exception as e:
            log.error("Library load-to-slicer failed: %s", e)

    def _on_clear_takes(self):
        self._recorder_takes = []
        if self.window:
            try:
                rp = self.window.tools_view.recorder_panel
                if hasattr(rp, "set_takes"):
                    rp.set_takes([])
                if hasattr(rp, "set_send_to_slice_enabled"):
                    rp.set_send_to_slice_enabled(False)
            except Exception:
                pass

    def _on_send_to_slice(self):
        """Save the latest take to data/slices/ and open it in the Slice tab."""
        if not self._recorder_takes:
            return
        buf = self._recorder_takes[-1]
        import os
        import time as _time
        slices_dir = os.path.join("data", "slices")
        try:
            os.makedirs(slices_dir, exist_ok=True)
        except Exception as e:
            log.error("Send-to-Slice mkdir failed: %s", e)
            return
        ts = _time.strftime("%Y%m%d_%H%M%S")
        filename = f"recording_{ts}.wav"
        path = os.path.join(slices_dir, filename)

        # Snapshot data for the worker thread (buf.data is float32 contiguous;
        # SampleBuffer is immutable enough for our purposes here).
        data = buf.data
        sr = buf.sample_rate

        def _writer():
            try:
                import soundfile as sf
                sf.write(path, data, sr, subtype="PCM_16")
            except Exception as e:
                log.error("Send-to-Slice background write failed: %s", e)
                return
            # Marshal back to GUI thread to load + switch tab
            QTimer.singleShot(0, lambda: self._after_send_to_slice(path, filename))

        threading.Thread(target=_writer, daemon=True, name="SendToSlice").start()

    def _after_send_to_slice(self, path: str, filename: str):
        """GUI-thread continuation of _on_send_to_slice."""
        if self.window:
            try:
                tv = self.window.tools_view
                tv._tabs.setCurrentIndex(1)  # 0=Record, 1=Slice
                tv.slice_panel.load_file(path)
                self.window.set_status(f"Sent recording to Slice: {filename}")
            except Exception as e:
                log.error("Switch to Slice tab failed: %s", e)

    def _poll_recorder_ui(self):
        """Poll engine for input level + recording progress; push to RecorderPanel."""
        if not self.engine or not self.window:
            return
        try:
            rp = self.window.tools_view.recorder_panel
        except AttributeError:
            return
        rec = self.engine.recorder
        if hasattr(rp, "set_input_level"):
            try:
                rp.set_input_level(rec.input_peak, rec.input_rms)
            except Exception:
                pass
        if hasattr(rp, "set_progress"):
            try:
                if rec.recording:
                    if self.config.recorder.free_length:
                        # Free-length: show elapsed seconds rolling 0-100% per 10s
                        secs = rec.free_length_frames / float(rec.sr)
                        rp.set_progress((secs % 10.0) / 10.0)
                    else:
                        rp.set_progress(rec.progress)
                else:
                    rp.set_progress(0.0)
            except Exception:
                pass

    def _recorder_advance_clock(self, beat_int: int):
        """Called from _execute_beat (GUI thread).

        Drives the count-in countdown and quantized start for the Record tool.
        BEAT quantize: start on next beat. BAR quantize: start on next bar.
        """
        if not self._recorder_pending_start or not self.engine:
            return

        rec = self.config.recorder
        quantize = int(rec.quantize)

        # Count-in phase: wait N bars, then proceed to quantized start
        if self._recorder_count_in_remaining > 0:
            # Decrement only on bar boundaries (every 4 beats)
            if beat_int % 4 == 0:
                self._recorder_count_in_remaining -= 1
                if self._recorder_count_in_remaining > 0:
                    self._update_recorder_button_state(
                        True, label=f"COUNT {self._recorder_count_in_remaining}"
                    )
                else:
                    # Count-in complete — start immediately
                    self._recorder_pending_start = False
                    self.engine.recorder.start()
                    self._update_recorder_button_state(True, label="● REC")
            return

        # Quantize-only path (no count-in)
        if quantize == 1:  # BEAT — fire on every beat
            self._recorder_pending_start = False
            self.engine.recorder.start()
            self._update_recorder_button_state(True, label="● REC")
        elif quantize == 2:  # BAR — fire on bar boundary
            if beat_int % 4 == 0:
                self._recorder_pending_start = False
                self.engine.recorder.start()
                self._update_recorder_button_state(True, label="● REC")

    def _on_slice_saved(self, filepath: str):
        """A slice was saved — add it to the sample library."""
        from mlrvp.gui.views.channel_view import ChannelStrip
        ChannelStrip._add_to_library(filepath)

    def _on_rack_contents_changed(self, payload: dict):
        """Sync per-bus effect in-rack status and processing order from rack UI."""
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

        # Update processing order per bus (drives dynamic FX chain in engine)
        if isinstance(payload, dict):
            for key, bus in (("order_a", 1), ("order_b", 2), ("order_m", 3),
                             ("order_c", 4), ("order_d", 5)):
                order = payload.get(key)
                if order is not None:
                    self.engine.set_rack_order(bus, order)

    # --- Setup handlers ---

    @staticmethod
    def _monome_backend_label(mode: str) -> str:
        labels = {
            MONOME_BACKEND_AUTO: "Auto",
            MONOME_BACKEND_SERIALOSC: "serialosc",
            MONOME_BACKEND_40H_DIRECT: "40h direct",
            MONOME_BACKEND_LEGACY_OSC: "Legacy OSC",
        }
        return labels.get(mode or MONOME_BACKEND_AUTO, "Auto")

    def _sync_grid_backend_to_controller(self, restart: bool = False) -> bool:
        previous = self.grid.get_monome_backend()
        desired = getattr(self.config, "monome_backend", MONOME_BACKEND_AUTO)
        self.grid.set_monome_backend(desired)
        changed = previous != self.grid.get_monome_backend()
        if restart and changed:
            self.grid.restart_monome()
        return changed

    def _sync_grid_setup_panel(self):
        if not self.window:
            return
        self.window.setup_view.grid_panel.set_grid_settings(
            row_count=self.config.row_count,
            grid_columns=self.config.grid_columns,
            show_virtual_grid=self.window.virtual_grid.isVisible(),
            snap_speed_to_rate=getattr(self.config, "snap_speed_to_rate", False),
            monome_backend=getattr(self.config, "monome_backend", MONOME_BACKEND_AUTO),
        )

    def _gui_scan_grids(self, auto_connect: bool = True):
        """Scan for MIDI grid controllers and update the UI.

        Also probes serialosc and triggers a fresh monome rescan so
        users get a single button that re-detects every kind of grid.

        If auto_connect is True, automatically connect to the first
        detected device.
        """
        # ── monome / serialosc probe ────────────────────────────────────
        # Done first so its status is logged even if the MIDI scan succeeds.
        legacy_modes = {
            "monome-40h": "40h direct mode",
            "monome-monomeserial": "monomeserial mode",
        }
        selected_backend = getattr(self.config, "monome_backend", MONOME_BACKEND_AUTO)
        active_legacy_mode = legacy_modes.get(self.grid.info.type) if self.grid.connected else None
        # Skip the probe entirely when a legacy monome path is already
        # connected, or when serialosc is not the selected backend.
        if active_legacy_mode or selected_backend not in (MONOME_BACKEND_AUTO, MONOME_BACKEND_SERIALOSC):
            probe = None
        else:
            probe = GridController.probe_monome(timeout=0.6)
            log.info("Monome probe: %s", probe.summary)
        # Kick a rescan so any grid plugged in after launch (or after the
        # serialosc service was started) gets picked up immediately.
        self.grid.rescan_monome()

        # ── MIDI grid scan ──────────────────────────────────────────────
        devices = GridController.scan_midi_grids()
        # If a legacy monome path is connected, prepend it to the
        # device list so the Setup → Grid Device dropdown shows it as a
        # selectable / selected entry (the combo otherwise only contains
        # MIDI grids and 'Virtual Grid').
        if active_legacy_mode:
            mono_name = self.grid.info.id or active_legacy_mode
            devices = [{
                "name": mono_name,
                "model": self.grid.info.type,
                "in_port": -1,
                "out_port": -1,
            }] + list(devices)
        if self.window:
            self.window.setup_view.grid_panel.set_scanned_midi_devices(devices)
            status_parts = []
            if devices:
                names = ', '.join(d['name'] for d in devices)
                status_parts.append(f"MIDI grids: {names}")
            else:
                status_parts.append("No MIDI grid controllers found")
            # Append monome status so the user gets actionable feedback
            # right in the status bar (no need to open a log file).
            if probe is None:
                if active_legacy_mode:
                    legacy_id = self.grid.info.id or active_legacy_mode or "legacy monome"
                    status_parts.append(f"monome: {active_legacy_mode} ({legacy_id})")
                else:
                    label = self._monome_backend_label(selected_backend)
                    status_parts.append(f"monome backend: {label}")
            elif probe.daemon_running and probe.devices:
                names = ', '.join(
                    f"{d['id']} ({d['type']})" for d in probe.devices
                )
                status_parts.append(f"monome: {names}")
            elif probe.daemon_running:
                status_parts.append("monome: serialosc running, no devices")
            else:
                status_parts.append("monome: serialosc not reachable")
            self.window.set_status(" — ".join(status_parts))

        # Auto-connect to first found MIDI device
        if auto_connect and devices and not self.grid.connected:
            d = devices[0]
            self._gui_midi_grid_connect(d["in_port"], d["out_port"])

    def _gui_midi_grid_connect(self, in_port: int, out_port: int):
        """Connect to a MIDI grid controller."""
        from mlrvp.grid.midi_grid import is_launchpad
        from mlrvp.config import CR_MONOME_ROW_LAUNCHPAD_CC
        ok = self.grid.connect_midi_grid(in_port, out_port)
        if ok:
            # Forward MIDI messages from the grid adapter to the MIDI handler
            # so MIDI learn can capture Launchpad button presses
            if self.grid._midi_grid:
                self.grid._midi_grid.set_midi_forward(self.midi.inject_message)
            name = self.grid._midi_grid.device_name if self.grid._midi_grid else "MIDI Grid"
            lp = is_launchpad(name)

            # For Launchpads: default to CC top-row control mode (-2)
            cr = self.config.control_row
            if lp and cr.monome_row == 0:
                # First-time connect or previously on default top — switch to LP CC row
                cr.monome_row = CR_MONOME_ROW_LAUNCHPAD_CC

            # Apply control row config now that midi_grid is connected; this also
            # sets _monome_row_offset (0 for CC mode, 1 for note-row top mode, etc.)
            self.grid.configure_control_row(cr.enabled, cr.monome_row)
            with self.grid._state_lock:
                row_offset = self.grid._monome_row_offset

            # Effective data rows (note grid minus any consumed row)
            total_rows = self.grid.info.rows
            cols = self.grid.info.columns
            rows = total_rows - row_offset
            if self.engine:
                rows = min(rows, len(self.engine.channels))
            rows = max(1, rows)

            self.config.row_count = rows
            self.config.grid_columns = cols
            for ch in self.config.channels:
                ch.steps = cols
            if self.engine:
                for i in range(min(len(self.config.channels), len(self.engine.channels))):
                    self.engine.channels[i].state.steps = cols
            if self.window:
                self.window.set_grid_dimensions(rows, cols)
                self.window.set_hardware_grid_connected(True)
                self._sync_grid_columns_to_waveforms()
                self.window.setup_view.grid_panel.set_connection_status(True, name)
                self.window.set_status(f"Connected: {name} ({cols}×{rows})")
                self.window._auto_resize_for_rows(rows)
                if lp:
                    # Add LP option + auto-select; then sync full config so UI
                    # reflects any preset that was loaded from saved settings.
                    self.window.setup_view.grid_panel.set_launchpad_connected(True)
                    self.window.setup_view.grid_panel.set_control_row_config(
                        cr.enabled, cr.monome_row, cr.assignments
                    )
                log.info("Grid connected: %s — set grid to %dx%d", name, cols, rows)
            # Repaint LEDs to match the current grid mode after connect.
            from .config import GRID_MODE_MIX
            if self.config.grid_mode == GRID_MODE_MIX:
                QTimer.singleShot(100, self._mix_mode_update_all)
            else:
                QTimer.singleShot(100, self.grid.clear)
        else:
            if self.window:
                self.window.setup_view.grid_panel.set_connection_status(False)
                self.window.set_status("Failed to connect MIDI grid")

    def _gui_midi_grid_disconnect(self):
        """Disconnect the current MIDI grid controller."""
        from mlrvp.config import CR_MONOME_ROW_LAUNCHPAD_CC
        cr = self.config.control_row
        was_lp_cc = (cr.monome_row == CR_MONOME_ROW_LAUNCHPAD_CC)
        self.grid.disconnect_midi_grid()
        # Reset LP-specific row selection back to top row on disconnect
        if was_lp_cc:
            cr.monome_row = 0
            self.grid.configure_control_row(cr.enabled, 0)
        if self.window:
            self.window.setup_view.grid_panel.set_connection_status(False)
            self.window.setup_view.grid_panel.set_launchpad_connected(False)
            self.window.set_hardware_grid_connected(False)
            self.window.set_status("MIDI grid disconnected")

    def _gui_audio_setting(self, key: str, value):
        setattr(self.config, key, value)
        if key == "multichannel_output":
            # Requires engine restart to take effect — do it automatically
            # so the user doesn't need to toggle the DAC button.
            if self.engine:
                self.engine.stop()
                try:
                    self.engine.start()
                    n_ch = self.engine._out_channels
                    if value and n_ch > 2:
                        n_grp = (n_ch - 2) // 2
                        n_grp_wanted = min(
                            getattr(self.config, 'active_groups', NUM_GROUPS), NUM_GROUPS
                        )
                        if n_grp < n_grp_wanted:
                            msg = (
                                f"Multi-channel output ON — "
                                f"{n_ch}ch: Master=1-2, Groups 1-{n_grp}=3-{n_ch} "
                                f"(device only supports {n_ch} channels; "
                                f"{n_grp_wanted - n_grp} group(s) not routed)"
                            )
                        else:
                            msg = (
                                f"Multi-channel output ON — "
                                f"{n_ch}ch: Master=1-2, Groups 1-{n_grp}=3-{n_ch}"
                            )
                    elif value:
                        msg = (
                            "Multi-channel output: device only has 2 channels — "
                            "outputting stereo master only"
                        )
                    else:
                        msg = "Multi-channel output OFF (stereo)"
                    if self.window:
                        self.window.set_status(msg)
                except Exception as e:
                    log.warning("Audio restart failed after multichannel toggle: %s", e)
                    # Revert and fall back to stereo so audio keeps working
                    self.config.multichannel_output = False
                    if self.window:
                        self.window.setup_view.audio_panel.set_multichannel_output_state(False)
                    try:
                        self.engine.start()
                        if self.window:
                            self.window.set_status(
                                "Multi-channel output failed — reverted to stereo. "
                                "Use a multi-channel interface or Voicemeeter."
                            )
                    except Exception as e2:
                        log.error("Audio failed to restart after multichannel fallback: %s", e2)
                        if self.window:
                            self.window.set_status(f"Audio restart failed: {e2}")
            return
        if self.window:
            self.window.set_status(f"Audio: {key} = {value} (restart to apply)")

    def _gui_grid_setting(self, key: str, value):
        if key == "monome_backend":
            self.config.monome_backend = value
            self._sync_grid_backend_to_controller(restart=True)
            self._gui_scan_grids(auto_connect=False)
            self._save_startup_settings()
            if self.window:
                self.window.set_status(
                    f"Monome backend: {self._monome_backend_label(value)}"
                )
        elif key == "row_count":
            self.config.row_count = value
            if self.window:
                self.window.set_grid_dimensions(value, self.config.grid_columns)
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
        elif key == "active_groups":
            # Clamp to valid set: {4, 6, 8} ∩ {n : n <= NUM_GROUPS}
            valid = [c for c in (4, 6, 8) if c <= NUM_GROUPS]
            n = int(value)
            if n not in valid:
                n = min(valid, key=lambda x: abs(x - n))
            self.config.active_groups = n
            if self.window:
                self.window.mixer_view.set_active_groups(n)
                self.window.transport.set_active_groups(n)
                self.window.channel_view.set_active_groups(n)
                self.window.setup_view._grid_panel.set_active_groups(n)
            self._save_startup_settings()
            from .config import GRID_MODE_MIX
            if self.config.grid_mode == GRID_MODE_MIX:
                self._mix_mode_update_all()

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
        if key == "osc_meter_output":
            return  # live-change; no restart needed
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
            host_apis = [a["name"] for a in AudioEngine.get_host_apis()]
            out_devs = devices["output"]
            in_devs = devices["input"]
            self.window.setup_view.audio_panel.set_devices(
                out_devs, in_devs, host_apis
            )

            # Restore saved driver type selection
            if self.config.host_api:
                self.window.setup_view.audio_panel.set_driver(self.config.host_api)

            # Restore saved device selections
            if self.config.output_device >= 0:
                self.window.setup_view.audio_panel.select_output_device(
                    self.config.output_device
                )
            if self.config.input_device >= 0:
                self.window.setup_view.audio_panel.select_input_device(
                    self.config.input_device
                )

            # Also populate recorder input list
            in_names = [d["name"] for d in in_devs]
            self.window.tools_view.recorder_panel.set_input_devices(in_names)

            # Populate slicer cue device combo from output device list
            cue_devs = [(d["index"], d["name"]) for d in out_devs]
            sp = self.window.tools_view.slice_panel
            sp.populate_cue_devices(cue_devs)
            # Sync slicer preview to the configured main output device
            sp.set_output_device(self.config.output_device)
            # Apply persisted slicer tool state (volume, mode, loop, etc.)
            sp.set_slicer_state(self.config.slicer)
            # Select the persisted cue device in the combo
            sp.select_cue_device(self.config.slicer.cue_device)
            # Configure preview service device + volume
            self.slicer_preview.set_volume(self.config.slicer.cue_volume)
            self.slicer_preview.set_device(
                self.config.slicer.cue_device,
                self.config.slicer.cue_channel_offset,
            )
        except Exception as e:
            log.warning("Could not enumerate audio devices: %s", e)

        # MIDI devices
        if self.midi:
            self.window.setup_view.midi_panel.set_midi_devices(
                self.midi.available_inputs, self.midi.available_outputs
            )

        self._sync_grid_setup_panel()

        # MIDI grid controllers — auto-scan and connect
        self._gui_scan_grids()

        # If no hardware grid connected after scan, show virtual grid
        if not self.grid.connected:
            self.window.set_hardware_grid_connected(False)

        # Sync grid columns to all waveforms
        self._sync_grid_columns_to_waveforms()

        # OSC panel — sync enabled state
        self.window.setup_view.osc_panel.set_enabled_state(self.config.osc_enabled)
        self.window.setup_view.osc_panel.set_meter_output_state(
            getattr(self.config, "osc_meter_output", True)
        )

        # Audio panel — multi-channel output checkbox
        self.window.setup_view.audio_panel.set_multichannel_output_state(
            getattr(self.config, "multichannel_output", False)
        )

        # Ableton Link — restore saved state and update Setup > Audio button
        if self.clock:
            link_was_enabled = getattr(self.config, "link_enabled", False)
            if link_was_enabled:
                ok = self.clock.set_link_enabled(True)
                if not ok:
                    self.config.link_enabled = False
                    link_was_enabled = False
            self.window.setup_view.set_link_state(
                link_was_enabled,
                self.clock.link_num_peers,
                available=self.clock.link_available,
            )

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
                strip._solo_btn, f"ch.{i}.solo", f"Ch{i+1} Solo")
            self._midi_map.register_control(
                strip._ch_lp_knob, f"ch.{i}.ch_lp", f"Ch{i+1} LP Filter")
            self._midi_map.register_control(
                strip._ch_hp_knob, f"ch.{i}.ch_hp", f"Ch{i+1} HP Filter")
            self._midi_map.register_control(
                strip._pan_knob, f"ch.{i}.pan", f"Ch{i+1} Pan")
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
        for gi in range(NUM_GROUPS):
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
            "noise_gate": {
                "_enabled": "noise_gate.enabled",
                "_thresh": "noise_gate.threshold",
                "_attack": "noise_gate.attack",
                "_hold": "noise_gate.hold",
                "_release": "noise_gate.release",
                "_range": "noise_gate.range_db",
                "_key_freq": "noise_gate.key_freq",
            },
            "tremolo": {
                "_enabled": "tremolo.enabled",
                "_rate": "tremolo.rate",
                "_depth": "tremolo.depth",
                "_mix": "tremolo.mix",
            },
            "auto_wah": {
                "_enabled": "auto_wah.enabled",
                "_sensitivity": "auto_wah.sensitivity",
                "_peak": "auto_wah.peak",
                "_mix": "auto_wah.mix",
            },
            "fet_limiter": {
                "_enabled": "fet_limiter.enabled",
                "_input": "fet_limiter.input_gain",
                "_output": "fet_limiter.output_gain",
            },
            "simple_eq": {
                "_enabled": "simple_eq.enabled",
                "_low": "simple_eq.low",
                "_mid": "simple_eq.mid",
                "_high": "simple_eq.high",
                "_mix": "simple_eq.mix",
            },
            "ping_pong_delay": {
                "_enabled": "ping_pong_delay.enabled",
                "_ping": "ping_pong_delay.ping_ms",
                "_pong": "ping_pong_delay.pong_ms",
                "_feedback": "ping_pong_delay.feedback",
                "_spread": "ping_pong_delay.spread",
                "_damp": "ping_pong_delay.damping",
                "_mix": "ping_pong_delay.mix",
            },
            "shimmer_reverb": {
                "_enabled": "shimmer_reverb.enabled",
                "_decay": "shimmer_reverb.decay",
                "_shimmer": "shimmer_reverb.shimmer",
                "_damp": "shimmer_reverb.damping",
                "_mix": "shimmer_reverb.mix",
            },
            "lofi_radio": {
                "_enabled": "lofi_radio.enabled",
                "_lo_cut": "lofi_radio.lo_cut",
                "_hi_cut": "lofi_radio.hi_cut",
                "_noise": "lofi_radio.noise",
                "_dist": "lofi_radio.distortion",
                "_mix": "lofi_radio.mix",
            },
            "auto_pan": {
                "_enabled": "auto_pan.enabled",
                "_rate": "auto_pan.rate",
                "_depth": "auto_pan.depth",
                "_mix": "auto_pan.mix",
            },
            "fuzz": {
                "_enabled": "fuzz.enabled",
                "_gain": "fuzz.gain",
                "_sustain": "fuzz.sustain",
                "_tone": "fuzz.tone",
                "_mix": "fuzz.mix",
            },
            "granular_freeze": {
                "_enabled": "granular_freeze.enabled",
                "_grain_size": "granular_freeze.grain_size",
                "_scatter": "granular_freeze.scatter",
                "_spread": "granular_freeze.spread",
                "_density": "granular_freeze.density",
                "_freeze": "granular_freeze.freeze",
                "_mix": "granular_freeze.mix",
            },
            "gate_reverb": {
                "_enabled": "gate_reverb.enabled",
                "_room": "gate_reverb.room",
                "_gate_time": "gate_reverb.gate_time",
                "_slope": "gate_reverb.slope",
                "_pre_delay": "gate_reverb.pre_delay",
                "_mode": "gate_reverb.mode",
                "_mix": "gate_reverb.mix",
            },
            "stutter": {
                "_enabled": "stutter.enabled",
                "_size": "stutter.size",
                "_rate": "stutter.rate",
                "_chaos": "stutter.chaos",
                "_smear": "stutter.smear",
                "_trigger": "stutter.trigger",
                "_mix": "stutter.mix",
            },
            "harmonizer": {
                "_enabled": "harmonizer.enabled",
                "_semi_a": "harmonizer.voice_a_semitones",
                "_fine_a": "harmonizer.voice_a_cents",
                "_delay_a": "harmonizer.voice_a_delay_ms",
                "_semi_b": "harmonizer.voice_b_semitones",
                "_fine_b": "harmonizer.voice_b_cents",
                "_delay_b": "harmonizer.voice_b_delay_ms",
                "_fdbk": "harmonizer.feedback",
                "_mix": "harmonizer.mix",
            },
            "ring_mod": {
                "_enabled": "ring_mod.enabled",
                "_freq": "ring_mod.freq_hz",
                "_depth": "ring_mod.depth",
                "_mix": "ring_mod.mix",
            },
            "rotary": {
                "_enabled": "rotary.enabled",
                "_acceleration_knob": "rotary.acceleration",
                "_horn_level_knob": "rotary.horn_level",
                "_rotor_level_knob": "rotary.rotor_level",
                "_mix_knob": "rotary.mix",
            },
            "stereo_widener": {
                "_enabled": "stereo_widener.enabled",
                "_width_knob": "stereo_widener.width",
                "_center_knob": "stereo_widener.center",
                "_lo_mono_knob": "stereo_widener.low_mono_freq",
            },
            "freq_shifter": {
                "_enabled": "freq_shifter.enabled",
                "_shift_knob": "freq_shifter.shift_hz",
                "_fdbk_knob": "freq_shifter.feedback",
                "_mix_knob": "freq_shifter.mix",
            },
            "exciter": {
                "_enabled": "exciter.enabled",
                "_tune_knob": "exciter.tune",
                "_mix_knob": "exciter.mix",
                "_lo_tune_knob": "exciter.lo_tune",
                "_lo_mix_knob": "exciter.lo_mix",
                "_output_knob": "exciter.output",
            },
            "transient_shaper": {
                "_enabled": "transient_shaper.enabled",
                "_attack_knob": "transient_shaper.attack_shape",
                "_sustain_knob": "transient_shaper.sustain_shape",
                "_output_knob": "transient_shaper.output",
                "_link_btn": "transient_shaper.stereo_link",
            },
        }

        _RACK_BUS = {"A": "a", "B": "b", "M": "m", "C": "c", "D": "d"}
        for rack_name, rack in (("A", ev._rack_a), ("B", ev._rack_b), ("M", ev._rack_m), ("C", ev._rack_c), ("D", ev._rack_d)):
            bus_letter = _RACK_BUS[rack_name]
            for mi, mod in enumerate(rack.get_modules()):
                et = getattr(mod, "effect_type", "")
                spec = fx_specs.get(et)
                if not spec:
                    # Handle stowaway (Foreign Exchange) modules dynamically
                    if et.startswith("stowaway_"):
                        slot_idx = int(et.split("_", 1)[1])
                        key_pfx = f"stowaway_{bus_letter}_{slot_idx}"
                        spec = {
                            "_enabled": f"{key_pfx}.enabled",
                            "_wet_dry_knob": f"{key_pfx}.wet_dry",
                        }
                        for ki in range(4):
                            spec[f"_quick_knobs[{ki}]"] = f"{key_pfx}.quick_{ki}"
                        for attr, key in spec.items():
                            if attr == "_enabled":
                                ctrl = mod.power_widget
                            elif attr == "_wet_dry_knob":
                                ctrl = mod.wet_dry_widget
                            elif attr.startswith("_quick_knobs["):
                                qi = int(attr.split("[")[1].rstrip("]"))
                                ctrl = mod.get_quick_knob(qi)
                            else:
                                ctrl = None
                            if ctrl is not None:
                                self._midi_map.register_control(
                                    ctrl, key,
                                    f"FX {rack_name} {et} {attr[1:]} {mi+1}",
                                )
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
            # Cancel any in-progress learn so the next incoming CC doesn't
            # silently create a stale mapping for the last selected control.
            self.midi.stop_learn()
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
        self._mark_preset_dirty()
        # Add row to setup view mapping table
        if self.window:
            label = param_path
            for ctrl in self._midi_map._controls:
                if ctrl.param_path == param_path:
                    label = ctrl.label
                    break
            # Remove any existing row for this param first (re-learn case —
            # avoids a second row appearing for the same parameter).
            self.window.setup_view.midi_panel.remove_mapping_row(param_path)
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
            # If this is a control row path, update the assignment label
            if param_path.startswith(("transport.group_stop.", "transport.pattern.")):
                self._repopulate_control_row_assignments()

    def _on_midi_mapping_displaced(self, param_path: str):
        """A new learn stole the CC that param_path owned — remove its table row."""
        if self.window:
            self.window.setup_view.midi_panel.remove_mapping_row(param_path)

    def _on_midi_map_learn_done(self, param_path: str, channel: int, cc: int):
        """Final notification after learn overlay updates."""
        pass

    def _gui_midi_range_changed(self, param_path: str, min_val: float, max_val: float):
        """User edited min/max range in the mapping table."""
        self.midi.update_mapping_range(param_path, min_val, max_val)
        self._mark_preset_dirty()

    def _gui_midi_mapping_removed(self, param_path: str):
        """User deleted a mapping row from the table."""
        self.midi.remove_mapping(param_path)
        self._mark_preset_dirty()
        if param_path.startswith(("transport.group_stop.", "transport.pattern.")):
            self._repopulate_control_row_assignments()

    def _gui_midi_toggle_changed(self, param_path: str, toggle: bool):
        """User toggled the toggle checkbox in the mapping table."""
        self.midi.update_mapping_toggle(param_path, toggle)
        self._mark_preset_dirty()

    def _gui_tilt_mapping_changed(self, axis: int, target_param: str):
        """User changed tilt axis target assignment."""
        axis_names = ["x", "y", "z"]
        src = f"tilt.0.{axis_names[axis]}"
        if target_param:
            self._tilt_mappings[src] = target_param
            # Reset output EMA so the new target starts from its current position
            self._tilt_output_ema.pop(src, None)
            # Make sure the target param has a callback registered
            self._register_midi_param_callback(target_param)
        else:
            self._tilt_mappings.pop(src, None)
            self._tilt_output_ema.pop(src, None)

    def _gui_tilt_range_changed(self, axis: int, min_val: float, max_val: float):
        """User edited min/max range for a tilt axis."""
        axis_names = ["x", "y", "z"]
        src = f"tilt.0.{axis_names[axis]}"
        self._tilt_ranges[src] = (min_val, max_val)

    def _gui_tilt_sensitivity_changed(self, axis: int, value: float):
        """User edited sensitivity for a tilt axis."""
        axis_names = ["x", "y", "z"]
        src = f"tilt.0.{axis_names[axis]}"
        self._tilt_sensitivity[src] = value

    def _get_tilt_mappings_serializable(self) -> list[dict]:
        """Serialize tilt axis assignments, ranges and sensitivity for preset save."""
        axis_names = ["x", "y", "z"]
        result = []
        for ax in axis_names:
            src = f"tilt.0.{ax}"
            target = self._tilt_mappings.get(src, "")
            if target:
                min_v, max_v = self._tilt_ranges.get(src, (0.0, 1.0))
                sensitivity = self._tilt_sensitivity.get(src, 5.0)
                result.append({"axis": ax, "target": target,
                                "min_val": min_v, "max_val": max_v,
                                "sensitivity": sensitivity})
        return result

    def _apply_tilt_mappings(self, tilt_maps: list[dict]):
        """Restore tilt axis assignments from preset data and update UI."""
        self._tilt_mappings.clear()
        self._tilt_ranges.clear()
        self._tilt_sensitivity.clear()
        axis_name_to_idx = {"x": 0, "y": 1, "z": 2}
        for tm in tilt_maps:
            ax = tm.get("axis", "")
            target = tm.get("target", "")
            min_v = float(tm.get("min_val", 0.0))
            max_v = float(tm.get("max_val", 1.0))
            sensitivity = float(tm.get("sensitivity", 5.0))
            if ax in axis_name_to_idx and target:
                src = f"tilt.0.{ax}"
                self._tilt_mappings[src] = target
                self._tilt_ranges[src] = (min_v, max_v)
                self._tilt_sensitivity[src] = sensitivity
                self._register_midi_param_callback(target)
        if self.window:
            for tm in tilt_maps:
                ax = tm.get("axis", "")
                idx = axis_name_to_idx.get(ax, -1)
                if idx >= 0:
                    self.window.setup_view.midi_panel.set_tilt_axis_state(
                        idx,
                        tm.get("target", ""),
                        float(tm.get("min_val", 0.0)),
                        float(tm.get("max_val", 1.0)),
                        float(tm.get("sensitivity", 5.0)),
                    )

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

    def _gui_control_row_enabled_changed(self, enabled: bool):
        """User toggled the Control Row checkbox."""
        self.config.control_row.enabled = enabled
        monome_row = self.config.control_row.monome_row
        self.grid.configure_control_row(enabled, monome_row)
        self._save_startup_settings()

    def _gui_control_row_setting_changed(self, key: str, value: object):
        """User changed a control row sub-setting."""
        cr = self.config.control_row
        if key == "monome_row":
            cr.monome_row = int(value)
            if cr.enabled:
                self.grid.configure_control_row(cr.enabled, cr.monome_row)
            # When an LP is connected and the user switches between CC row and
            # note-row modes, the effective row count changes.  Recompute it.
            if self.grid._midi_grid and self.grid._midi_grid.connected:
                with self.grid._state_lock:
                    row_offset = self.grid._monome_row_offset
                total_rows = self.grid.info.rows
                cols = self.grid.info.columns
                rows = total_rows - row_offset
                if self.engine:
                    rows = min(rows, len(self.engine.channels))
                rows = max(1, rows)
                self.config.row_count = rows
                if self.window:
                    self.window.channel_view.set_visible_rows(rows)
                    self.window.set_grid_dimensions(rows, cols)
                    self.window._auto_resize_for_rows(rows)
        elif key == "assignments":
            cr.assignments = value  # full list of 8 slot dicts
        # Rebuild the MIDI control row actions section
        if self.window:
            self.window.setup_view.midi_panel.set_control_row_actions(
                cr.num_group_stops, cr.num_patterns
            )
            self._repopulate_control_row_assignments()
        self._save_startup_settings()

    def _gui_control_row_learn_requested(self, param_path: str):
        """User clicked Learn on a control row action row."""
        self.midi.start_learn(param_path)
        if self.window:
            self.window.set_status(f"Waiting for MIDI input for: {param_path}")

    def _repopulate_control_row_assignments(self):
        """Update all control row assignment labels from current MIDI mappings."""
        if not self.window:
            return
        mappings = {m["param_path"]: m for m in self.midi.get_mappings_serializable()}
        cr = self.config.control_row
        paths = (
            [f"transport.group_stop.{i}" for i in range(cr.num_group_stops)]
            + [f"transport.pattern.{i}" for i in range(cr.num_patterns)]
            + ["transport.grid_mode_cycle"]
        )
        for path in paths:
            m = mappings.get(path)
            if m:
                info = f"CC{m['cc']} Ch{m['channel'] + 1}"
            else:
                info = ""
            self.window.setup_view.midi_panel.update_control_row_assignment(path, info)



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
            elif param == "solo":
                self.midi.set_param_callback(param_path,
                    lambda v, r=row: self._gui_channel_param(r, "solo", v > 0.5))
            elif param == "ch_lp":
                self.midi.set_param_callback(param_path,
                    lambda v, r=row: self._gui_channel_param(r, "ch_lp", v))
            elif param == "ch_hp":
                self.midi.set_param_callback(param_path,
                    lambda v, r=row: self._gui_channel_param(r, "ch_hp", v))
            elif param == "pan":
                self.midi.set_param_callback(param_path,
                    lambda v, r=row: self._gui_channel_param(r, "pan", v))
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
                    lambda v, g=grp: self._gui_group_volume(g, _fader_to_gain(v)))
            elif param == "send":
                self.midi.set_param_callback(param_path,
                    lambda v, g=grp: self._gui_group_send(g, v))
            elif param == "mute":
                self.midi.set_param_callback(param_path,
                    lambda v, g=grp: self._gui_group_mute(g, v > 0.5))
            elif param == "fx_route":
                self.midi.set_param_callback(param_path,
                    lambda v, g=grp: self._gui_group_fx_route(g, int(round(v * 4))))
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
        elif param_path == "transport.all_stop":
            self.midi.set_param_callback(param_path,
                lambda v: QTimer.singleShot(
                    0, self._gui_stop_all
                ) if v > 0.5 else None)
        elif param_path == "transport.grid_mode_cycle":
            self.midi.set_param_callback(param_path,
                lambda v: QTimer.singleShot(
                    0, self._gui_grid_mode_cycle
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
                    lambda v: self._gui_aux_volume(_fader_to_gain(v)))
            elif parts[1] == "send":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_aux_send(v))
            elif parts[1] == "mute":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_aux_mute(v > 0.5))
            elif parts[1] == "fx_route":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_aux_fx_route(int(round(v * 4))))
        elif parts[0] == "master" and len(parts) == 2:
            if parts[1] == "volume":
                self.midi.set_param_callback(param_path,
                    lambda v: self._gui_master_volume(_fader_to_gain(v)))
            elif parts[1] == "dac":
                self.midi.set_param_callback(param_path,
                    lambda v: QTimer.singleShot(
                        0, self.window.mixer_view._dac_btn.click
                    ) if v > 0.5 else None)
        elif parts[0] == "effect" and len(parts) == 2:
            effect_name = parts[1].replace("_route", "")
            self.midi.set_param_callback(param_path,
                lambda v, e=effect_name: self._gui_effect_route(e, int(round(v * 4))))
        elif parts[0] in ("reel_to_reel", "cassette_tape", "vhs", "vinyl",
                           "compressor", "hall_reverb", "plate_reverb",
                           "glue_compressor", "bit_crusher", "tube_overdrive",
                           "ether_drift", "noise_gate", "tremolo", "auto_wah",
                           "fet_limiter") and len(parts) == 2:
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
        elif parts[0].startswith("stowaway_") and len(parts) == 2:
            # Foreign Exchange (external plugin) params
            # Format: stowaway_{bus}_{slot}.{param}  e.g. "stowaway_a_0.wet_dry"
            tokens = parts[0].split("_")  # ["stowaway", "a", "0"]
            if len(tokens) < 3:
                return
            bus_letter = tokens[1]
            slot_idx = int(tokens[2])
            _BUS_MAP = {"a": "fx_bus_1", "b": "fx_bus_2", "m": "fx_bus_m",
                        "c": "fx_bus_c", "d": "fx_bus_d"}
            bus_key = _BUS_MAP.get(bus_letter)
            if bus_key is None:
                return
            param = parts[1]    # e.g. "wet_dry", "quick_0", "enabled"
            if param == "enabled":
                self.midi.set_param_callback(param_path,
                    lambda v, _bk=bus_key, _si=slot_idx: self._gui_stowaway_param(_bk, _si, "enabled", v > 0.5))
            else:
                self.midi.set_param_callback(param_path,
                    lambda v, _bk=bus_key, _si=slot_idx, p=param: self._gui_stowaway_param(_bk, _si, p, v))

        # Post-wrap: for knob/fader widgets that are registered in the MIDI map,
        # call widget.set_value(raw_v) on the Qt thread so the visual position
        # updates and the value popup fires.
        _w = self._midi_map.get_widget(param_path)
        if _w is not None and hasattr(_w, 'show_value_popup'):
            _existing = self.midi.get_param_callback(param_path)
            if _existing is not None:
                def _wrapped(v, _fn=_existing, _widget=_w):
                    _fn(v)  # update config + engine (thread-safe)
                    # Schedule widget update on the Qt main thread.
                    # blockSignals() prevents the widget's valueChanged from
                    # bouncing back through _gui_* and re-writing the engine.
                    def _apply(w=_widget, val=v):
                        try:
                            w.blockSignals(True)
                            w.set_value(val)
                        finally:
                            w.blockSignals(False)
                    QTimer.singleShot(0, _apply)
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
        self.engine.set_master_hp(self.config.master_hp)
        self.engine.set_master_lp(self.config.master_lp)
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
                        "bit_crusher", "tube_overdrive", "ether_drift",
                        "noise_gate", "tremolo", "auto_wah",
                        "fet_limiter", "rotary", "exciter",
                        "transient_shaper", "harmonizer", "stereo_widener",
                        "freq_shifter", "ring_mod",
                        "simple_eq", "ping_pong_delay", "shimmer_reverb",
                        "lofi_radio", "auto_pan", "fuzz",
                        "granular_freeze", "gate_reverb", "stutter"):
            for sfx in _suffixes:
                cfg_key = f"{fx_name}{sfx}"
                cfg = getattr(self.config, cfg_key)
                eng = getattr(self.engine, cfg_key)
                for attr, val in vars(cfg).items():
                    if not attr.startswith('_'):
                        setattr(eng.state, attr, val)

        # Sync stowaway (Foreign Exchange) plugin slots from config
        if hasattr(self.config, "stowaway_slots"):
            _BUS_KEYS = ["fx_bus_1", "fx_bus_2", "fx_bus_m", "fx_bus_c", "fx_bus_d"]
            for bus_idx, bus_key in enumerate(_BUS_KEYS):
                for si in range(4):
                    cfg_idx = bus_idx * 4 + si
                    if cfg_idx >= len(self.config.stowaway_slots):
                        continue
                    st = self.config.stowaway_slots[cfg_idx]
                    self.engine.set_plugin_slot_enabled(bus_key, si, st.enabled)
                    self.engine.set_plugin_slot_wet_dry(bus_key, si, st.wet_dry)
                    if st.plugin_path:
                        self.engine.load_plugin_slot(
                            bus_key, si, st.plugin_path, st.plugin_format)
                        if st.plugin_state_b64:
                            self.engine.set_plugin_slot_state_b64(
                                bus_key, si, st.plugin_state_b64)
                    else:
                        self.engine.unload_plugin_slot(bus_key, si)

        # Re-sync in-rack flags from current rack layout
        if self.window:
            self._on_rack_contents_changed(
                self.window.effects_view.get_rack_sync_state())

        _any_solo = any(ch.solo for ch in self.config.channels)
        for i, ch in enumerate(self.config.channels):
            ech = self.engine.channels[i]
            ech.state.speed = ch.speed
            ech.state.mode = ch.mode
            ech.state.gain = ch.gain
            ech.state.group = ch.group
            ech.state.mute = _any_solo and not ch.solo or ch.mute
            ech.state.reverse = ch.reverse
            ech.state.shot_hold = ch.shot_hold
            ech.state.steps = ch.steps
            ech.state.sel_start = ch.sel_start
            ech.state.sel_end = ch.sel_end
            ech.state.attack = ch.attack
            ech.state.release = ch.release
            ech.state.pitch = ch.pitch
            ech.state.velocity = ch.velocity
            ech.state.filter_val = ch.filter_val
            ech.state.ch_lp = ch.ch_lp
            ech.state.ch_hp = ch.ch_hp
            ech.state.pan = ch.pan
            ech.state.pitch_scale = ch.pitch_scale
            ech.state.pitch_root = ch.pitch_root
            ech.state.pitch_loop = ch.pitch_loop
            ech.state.transpose = ch.transpose
            # LIVE looper state — copy params; reset transient loop_state
            ech.state.live.bars = ch.live.bars
            ech.state.live.quantize = ch.live.quantize
            ech.state.live.thru = ch.live.thru
            ech.state.live.count_in = ch.live.count_in
            ech.state.live.input_trim = ch.live.input_trim
            ech.state.live.exported_path = ch.live.exported_path
            ech.state.live.loop_state = (
                LIVE_STATE_PLAYING if (ch.mode == PlaybackMode.LIVE and ech.sample is not None)
                else LIVE_STATE_EMPTY
            )
            if ch.transpose != 0 and ech.sample is not None:
                self._apply_pitch_shift_async(i, ch.transpose)

    def _apply_config_to_engine_live(self, playing_rows: set[int]):
        """Push config to engine, but protect playing channels from state
        clobber (sel_start/sel_end/group/mode/speed stay intact while
        the channel is still outputting audio)."""
        if not self.engine:
            return

        self.engine.set_tempo(self.config.tempo)
        self.engine.set_master_volume(self.config.master_volume)
        self.engine.set_master_hp(self.config.master_hp)
        self.engine.set_master_lp(self.config.master_lp)
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
                        "bit_crusher", "tube_overdrive", "ether_drift",
                        "noise_gate", "tremolo", "auto_wah",
                        "fet_limiter", "rotary", "exciter",
                        "transient_shaper", "harmonizer", "stereo_widener",
                        "freq_shifter", "ring_mod",
                        "simple_eq", "ping_pong_delay", "shimmer_reverb",
                        "lofi_radio", "auto_pan", "fuzz",
                        "granular_freeze", "gate_reverb", "stutter"):
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

        _any_solo = any(ch.solo for ch in self.config.channels)
        for i, ch in enumerate(self.config.channels):
            ech = self.engine.channels[i]
            if i in playing_rows:
                ech.state.gain = ch.gain
                ech.state.mute = _any_solo and not ch.solo or ch.mute
                ech.state.attack = ch.attack
                ech.state.release = ch.release
                ech.state.pitch_loop = ch.pitch_loop
                ech.state.ch_lp = ch.ch_lp
                ech.state.ch_hp = ch.ch_hp
                ech.state.pan = ch.pan
            else:
                ech.state.speed = ch.speed
                ech.state.mode = ch.mode
                ech.state.gain = ch.gain
                ech.state.group = ch.group
                ech.state.mute = _any_solo and not ch.solo or ch.mute
                ech.state.reverse = ch.reverse
                ech.state.shot_hold = ch.shot_hold
                ech.state.steps = ch.steps
                ech.state.sel_start = ch.sel_start
                ech.state.sel_end = ch.sel_end
                ech.state.attack = ch.attack
                ech.state.release = ch.release
                ech.state.pitch = ch.pitch
                ech.state.velocity = ch.velocity
                ech.state.filter_val = ch.filter_val
                ech.state.ch_lp = ch.ch_lp
                ech.state.ch_hp = ch.ch_hp
                ech.state.pan = ch.pan
                ech.state.pitch_scale = ch.pitch_scale
                ech.state.pitch_root = ch.pitch_root
                ech.state.pitch_loop = ch.pitch_loop
                # LIVE looper params (safe to update for non-playing rows)
                ech.state.live.bars = ch.live.bars
                ech.state.live.quantize = ch.live.quantize
                ech.state.live.thru = ch.live.thru
                ech.state.live.count_in = ch.live.count_in
                ech.state.live.input_trim = ch.live.input_trim
                ech.state.live.exported_path = ch.live.exported_path
                ech.state.live.loop_state = (
                    LIVE_STATE_PLAYING if (ch.mode == PlaybackMode.LIVE and ech.sample is not None)
                    else LIVE_STATE_EMPTY
                )
            ech.state.transpose = ch.transpose
            if ch.transpose != 0 and ech.sample is not None:
                self._apply_pitch_shift_async(i, ch.transpose)

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

    def _load_waveform_display(self, row: int, filepath: str, sample=None):
        """Update only the waveform display for a channel (no engine load).
        Used for visual preview when the channel is still playing old audio.
        If *sample* is provided the disk read is skipped entirely."""
        try:
            if sample is None:
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

    # ------------------------------------------------------------------ #
    #  Background (non-blocking) sample loading                           #
    # ------------------------------------------------------------------ #

    def _load_sample_bg(self, row: int, filepath: str, preserve_speed: bool = False):
        """Start loading a sample on a background thread.

        When the load finishes the ``_on_bg_sample_loaded`` slot runs on the
        main Qt thread (via queued signal) and applies the result to the
        engine / GUI.  This keeps all disk I/O off the main thread so that
        HDD users don't get audio glitches during preset recall or sample
        swaps while channels are playing.
        """
        sr = self.config.sample_rate
        worker = self._sample_load_worker
        # Capture the current generation so stale loads can be discarded.
        ech = self.engine.channels[row] if self.engine and row < len(self.engine.channels) else None
        gen = ech._load_gen if ech else 0

        def _io_thread():
            sample = load_sample(filepath, sr)
            worker._loaded.emit(row, filepath, sample, preserve_speed, gen)

        threading.Thread(target=_io_thread, daemon=True).start()

    def _on_bg_sample_loaded(self, row: int, filepath: str, sample, preserve_speed: bool, gen: int):
        """Main-thread callback when a background sample load completes."""
        if sample is None:
            if self.window:
                self.window.set_status(f"Failed to load: {filepath}")
            return
        if not self.engine or row >= len(self.engine.channels):
            return
        ech = self.engine.channels[row]

        # Discard stale loads — if the channel's generation has moved on
        # (e.g. New pressed, or a different preset recalled), this load
        # is obsolete and must not overwrite the current state.
        if gen != ech._load_gen:
            return

        # ---- Pending pre-load (playing channel awaiting next trigger) ----
        pending = getattr(ech, '_pending_sample_path', None)
        if pending == filepath:
            ech._pending_sample_buffer = sample
            self._load_waveform_display(row, filepath, sample=sample)

            # Pre-compute speed for the new sample (store in config only)
            if sample.duration_sec > 0:
                beats = self.config.grid_columns
                bar_dur = beats * (60.0 / self.config.tempo)
                raw_speed = sample.duration_sec / bar_dur
                raw_speed = self._normalize_speed(raw_speed)
                if self.config.snap_speed_to_rate:
                    raw_speed = self._snap_to_rate(raw_speed)
                self.config.channels[row].normal_speed = raw_speed
                if self.config.channels[row].mode != PlaybackMode.SEQ:
                    self.config.channels[row].speed = raw_speed
            if self.window:
                strip = self.window.channel_view.get_strip(row)
                strip._true_speed = self.config.channels[row].speed
                strip._speed_spin.blockSignals(True)
                strip._speed_spin.setValue(self.config.channels[row].speed)
                strip._speed_spin.blockSignals(False)

            from mlrvp.gui.views.channel_view import ChannelStrip
            ChannelStrip._add_to_library(filepath)
            if self.window:
                self.window.set_status(
                    f"Loaded: {filepath} → Channel {row + 1}"
                )
                self.window.tools_view.slice_panel.add_loaded_sample(filepath)
            log.info("Loaded sample: %s → channel %d (staged)", filepath, row)
            return

        # ---- Direct load into an idle channel ----
        if preserve_speed:
            self._apply_loaded_sample_no_speed_reset(row, filepath, sample)
        else:
            self._apply_loaded_sample(row, filepath, sample)

    def _apply_loaded_sample_no_speed_reset(self, row: int, filepath: str, sample):
        """Apply a pre-loaded sample into a channel, preserving stored speed."""
        try:
            saved_speed = self.config.channels[row].speed
            self.engine.load_sample(row, sample)
            self.config.channels[row].filename = filepath

            from mlrvp.gui.views.channel_view import ChannelStrip
            ChannelStrip._add_to_library(filepath)

            self.config.channels[row].steps = self.config.grid_columns

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
            log.error("Failed to apply loaded sample (no speed reset): %s", e)

    def _apply_loaded_sample(self, row: int, filepath: str, sample):
        """Apply a pre-loaded sample into an idle channel with speed calc."""
        try:
            self.engine.load_sample(row, sample)
            self.config.channels[row].filename = filepath
            self.config.channels[row].steps = self.config.grid_columns

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

            self.engine.set_channel_speed(row, self.config.tempo, sample)
            speed = self.engine.channels[row].state.speed
            speed = self._normalize_speed(speed)
            if self.config.snap_speed_to_rate:
                speed = self._snap_to_rate(speed)
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

            from mlrvp.gui.views.channel_view import ChannelStrip
            ChannelStrip._add_to_library(filepath)

            if self.window:
                self.window.set_status(
                    f"Loaded: {filepath} → Channel {row + 1}"
                )
                self.window.tools_view.slice_panel.add_loaded_sample(filepath)
            log.info("Loaded sample: %s → channel %d", filepath, row)
        except Exception as e:
            log.error("Failed to apply loaded sample: %s", e)

    def _clear_all_waveform_displays(self):
        """Clear waveform and sample name for all visible rows."""
        if not self.window:
            return
        for i in range(self.config.row_count):
            strip = self.window.channel_view.get_strip(i)
            strip._waveform.set_waveform(None, "")
            strip._sample_name.setText("(no sample)")

    def _reload_samples_from_config(self):
        """Reload audio samples from filepaths stored in config.

        Uses background threads so disk I/O doesn't block the main thread.
        """
        import os
        if self.engine:
            for i in range(self.config.row_count):
                self.engine.channels[i]._load_gen += 1
        for i, ch in enumerate(self.config.channels[:self.config.row_count]):
            if ch.filename and os.path.isfile(ch.filename):
                self._load_sample_bg(i, ch.filename)

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

            w.set_grid_dimensions(self.config.row_count, self.config.grid_columns)
            # Apply active groups to all views
            w.mixer_view.set_active_groups(self.config.active_groups)
            w.transport.set_active_groups(self.config.active_groups)
            w.channel_view.set_active_groups(self.config.active_groups)
            w.setup_view._grid_panel.set_active_groups(self.config.active_groups)
            w.transport.set_grid_mode(self.config.grid_mode)
            self._sync_grid_setup_panel()

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

            # Master filter knobs — convert freq back to 0-1 knob value
            hp_v = max(0.0, min(1.0, (self.config.master_hp - 20.0) / 480.0))
            lp_v = max(0.0, min(1.0, (self.config.master_lp - 200.0) / 19800.0))
            mf = w.master_filter_view
            mf._hp_knob.blockSignals(True)
            mf._hp_knob.set_value(hp_v)
            mf._hp_knob.blockSignals(False)
            mf._lp_knob.blockSignals(True)
            mf._lp_knob.set_value(lp_v)
            mf._lp_knob.blockSignals(False)
            mf._curve.set_params(self.config.master_hp, self.config.master_lp)

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
            _RACK_BUS_KEY = {"": "fx_bus_1", "_b": "fx_bus_2", "_m": "fx_bus_m",
                             "_c": "fx_bus_c", "_d": "fx_bus_d"}
            for rack, sfx in ((w.effects_view._rack_a, ""),
                              (w.effects_view._rack_b, "_b"),
                              (w.effects_view._rack_m, "_m"),
                              (w.effects_view._rack_c, "_c"),
                              (w.effects_view._rack_d, "_d")):
                for mod in rack.get_modules():
                    et = getattr(mod, "effect_type", "")
                    if et in _SKIP_TYPES or not et:
                        continue
                    # Stowaway modules have config in stowaway_slots list
                    if et.startswith("stowaway_"):
                        si = int(et.split("_", 1)[1])
                        bk = _RACK_BUS_KEY.get(sfx, "fx_bus_1")
                        cfg = self._get_stowaway_config(bk, si)
                        if cfg is not None and hasattr(mod, "set_state"):
                            mod.set_state(cfg)
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

        # Check for async monome connection / disconnection
        self._check_monome_connect()
        self._check_monome_disconnect()

        # Ableton Link — update peer count in Setup > Audio panel
        if self.clock:
            self.window.setup_view.set_link_state(
                self.config.link_enabled,
                self.clock.link_num_peers,
                available=self.clock.link_available,
            )

        # Beat position display — wraps bars so counter doesn't run forever
        if self.clock:
            bar = (self.clock.bar_position % 4) + 1  # 1-4 repeating cycle
            beat = int(self.clock.beat_in_bar) + 1    # 1-4 within bar
            self.window.transport.set_beat_position(bar, beat)

        # Track which groups have playing channels (for control row LEDs)
        group_active = [False] * NUM_GROUPS

        # Channel playback positions + active column highlighting
        columns = self.config.grid_columns
        from .config import GRID_MODE_MIX
        in_mix_mode = self.config.grid_mode == GRID_MODE_MIX
        for i in range(self.config.row_count):
            ch = self.engine.channels[i]
            strip = self.window.channel_view.get_strip(i)

            # LIVE mode: special LED handling for non-PLAYING substates
            if ch.state.mode == PlaybackMode.LIVE:
                live_state = ch.state.live.loop_state
                if live_state in (LIVE_STATE_EMPTY, LIVE_STATE_COUNTING_IN,
                                  LIVE_STATE_RECORDING, LIVE_STATE_OVERDUBBING,
                                  LIVE_STATE_ARMED_RERECORD):
                    if not in_mix_mode:
                        self._update_live_leds(i, live_state, columns)
                    strip.set_playing(False)
                    self._row_was_playing[i] = True  # prevent stray clears
                    continue
                # PLAYING falls through to the standard playback logic below

            # SEQ mode: always show step pattern LEDs regardless of playing state
            if ch.state.mode == PlaybackMode.SEQ and ch.sample is not None:
                if ch._seq_playing_step and ch.state.playing:
                    strip.set_playback_position(ch.position)
                    strip.set_playing(True)
                else:
                    strip.set_playing(False)
                # Waveform active column follows the sequencer step
                strip._waveform.set_active_column(ch._seq_column if ch._seq_column >= 0 else -1)
                strip._waveform.set_seq_steps(frozenset(ch._seq_steps))
                if not in_mix_mode:
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
                if not in_mix_mode:
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
                # Only send LED update when the active column changes to
                # avoid serial/MIDI I/O on every frame (reduces GIL hold time)
                col = ch.get_grid_column(columns)
                if col >= 0 and col != self._row_last_led_col[i]:
                    self._row_last_led_col[i] = col
                    sl_col_a = getattr(ch, '_slice_col_start', -1)
                    sl_col_b = getattr(ch, '_slice_col_end', -1)
                    if not in_mix_mode:
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
                    self._row_last_led_col[i] = -1
                    if not in_mix_mode:
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

        # Poll meter data from DSP thread (avoids GIL contention)
        polled = self.engine.poll_meters()
        if polled is not None:
            self._latest_levels = polled
            # Forward peak levels to any OSC listener (mlrV2-style meter output)
            if (self.osc is not None
                    and self.osc._running
                    and self.config.osc_meter_output):
                self.osc.send_meters(polled)

        # Surface DSP errors to status bar
        dsp_err = self.engine.poll_dsp_errors()
        if dsp_err is not None:
            count, msg = dsp_err
            self.window.statusBar().showMessage(
                f"⚠ DSP error ({count}x): {msg}", 5000
            )

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
        """Update control row LEDs based on slot assignments."""
        from mlrvp.config import CR_SLOT_GROUP_STOP, CR_SLOT_PATTERN, CR_SLOT_ALL_STOP, CR_SLOT_GRID_MODE, GRID_MODE_GRID, GRID_MODES
        assignments = self.config.control_row.assignments
        any_group_active = any(group_active[:NUM_GROUPS])

        for slot_x, slot in enumerate(assignments):
            slot_type = slot.get("type", "empty")
            slot_val = slot.get("value", 0)

            if slot_type == CR_SLOT_GROUP_STOP:
                g = slot_val
                brightness = LED_BRIGHT if (g < len(group_active) and group_active[g]) else LED_OFF
                self.grid.set_control_led(slot_x, brightness, color=g)

            elif slot_type == CR_SLOT_ALL_STOP:
                brightness = LED_BRIGHT if any_group_active else LED_OFF
                self.grid.set_control_led(slot_x, brightness, color=0)

            elif slot_type == CR_SLOT_GRID_MODE:
                # Lit (green) when in any non-default mode; dim when in default GRID mode
                mode_idx = GRID_MODES.index(self.config.grid_mode) if self.config.grid_mode in GRID_MODES else 0
                brightness = LED_BRIGHT if mode_idx > 0 else LED_DIM
                self.grid.set_control_led(slot_x, brightness, color=1)

            elif slot_type == CR_SLOT_PATTERN:
                idx = slot_val
                pr = self.patterns[idx] if idx < len(self.patterns) else None
                if pr is None:
                    brightness, color = LED_OFF, 0
                elif pr.recording:
                    brightness, color = LED_BRIGHT, 2  # red
                elif pr.playing:
                    brightness, color = LED_BRIGHT, 0  # amber
                elif pr.has_events:
                    brightness, color = LED_DIM, 0
                else:
                    brightness, color = LED_OFF, 0
                self.grid.set_control_led(slot_x, brightness, color)

            else:  # empty
                self.grid.set_control_led(slot_x, LED_OFF, 0)


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

    # On Windows, set an explicit App User Model ID *before* QApplication is
    # created.  Without this Windows groups the process under python.exe's
    # taskbar button and shows Python's icon instead of ours.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "mlrvp.mlrVp.app"
            )
        except Exception:
            pass

    app = QApplication(argv or sys.argv)
    app.setApplicationName("mlrVp")
    app.setApplicationVersion("0.6.9.20")

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

    # Set application icon — prefer .ico on Windows for proper multi-size
    # taskbar rendering; fall back to .png on other platforms.
    _resources = Path(__file__).parent / "resources"
    _icon_path = (_resources / "icon.ico") if sys.platform == "win32" else (_resources / "icon.png")
    if not _icon_path.exists():
        _icon_path = _resources / "icon.png"  # ultimate fallback
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