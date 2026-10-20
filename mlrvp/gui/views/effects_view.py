"""Effects view — FX rack tabs and the studio recorder panel in mlrVp.

The FX racks (Group A/B/C/D/Master) are skeuomorphic rack-mount modules
from mlrvp.gui.rack_modules, hosted in RackContainer widgets further
down this file. RecorderPanel below is the standalone studio capture
panel, distinct from the LIVE looper.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QGroupBox, QGridLayout, QScrollArea,
    QProgressBar, QInputDialog, QMenu, QMessageBox
)
from PySide6.QtCore import Qt, Signal

from mlrvp.config import RecorderState
from mlrvp.gui.styles import (
    ACCENT_GOLD, BG_BLACK, BG_PANEL, SEPARATOR, TEXT_SECONDARY, TEXT_PRIMARY,
    ACCENT_BLUE,
)
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.gui.widgets.vu_meter import VUMeter
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_width, bind_min_width


class RecorderPanel(QFrame):
    """Studio capture panel — distinct from the LIVE looper.

    Workflow targets: gain-staging, free-length capture, retroactive
    pre-roll grab, loopback resampling of the main output, A/B take
    comparison, post-capture processing, and one-click handoff to Slice.
    """

    param_changed = Signal(str, object)
    record_toggled = Signal(bool)
    capture_preroll = Signal(float)   # seconds
    use_take = Signal(int)
    play_take = Signal(int)
    clear_takes = Signal()
    send_to_slice = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Scroll wrapper so all controls remain reachable in tight layouts
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        host = QWidget()
        scroll.setWidget(host)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        title = QLabel("RECORDER")
        title.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {TEXT_SECONDARY};")
        layout.addWidget(title)

        # ---- Routing & timing -----------------------------------------
        grid = QGridLayout()
        grid.setSpacing(6)

        self._buffer_combo = QComboBox()
        self._buffer_combo.addItems([f"Channel {i+1}" for i in range(16)])
        self._buffer_combo.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("buffer_idx", v)
        )
        self._buffer_combo.setToolTip("Channel slot the recording loads into")
        grid.addWidget(QLabel("Target:"), 0, 0)
        grid.addWidget(self._buffer_combo, 0, 1, 1, 3)

        self._input_combo = QComboBox()
        self._input_combo.addItem("Default Input")
        self._input_combo.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("input_device", v)
        )
        self._input_combo.setToolTip("Audio input device")
        grid.addWidget(QLabel("Input:"), 1, 0)
        grid.addWidget(self._input_combo, 1, 1, 1, 3)

        self._input_ch = QSpinBox()
        self._input_ch.setRange(0, 31)
        self._input_ch.valueChanged.connect(
            lambda v: self.param_changed.emit("input_channel", v)
        )
        self._input_ch.setToolTip("Input channel index on the selected device")
        grid.addWidget(QLabel("In Ch:"), 2, 0)
        grid.addWidget(self._input_ch, 2, 1)

        self._loopback_chk = QCheckBox("Loopback (record output)")
        self._loopback_chk.toggled.connect(
            lambda v: self._on_loopback_toggled(v)
        )
        self._loopback_chk.setToolTip(
            "Record the final master output instead of the device input.\n"
            "Use this for resampling — play your grid pattern, capture the result."
        )
        grid.addWidget(self._loopback_chk, 2, 2, 1, 2)

        # Length: bars + beats side by side
        self._length_spin = QSpinBox()
        self._length_spin.setRange(1, 128)
        self._length_spin.setValue(16)
        self._length_spin.setSuffix(" bars")
        self._length_spin.valueChanged.connect(
            lambda v: self.param_changed.emit("length_bars", v)
        )
        self._length_spin.setToolTip("Recording length in bars (synced to current BPM)")
        grid.addWidget(QLabel("Length:"), 3, 0)
        grid.addWidget(self._length_spin, 3, 1)

        self._length_beats_spin = QSpinBox()
        self._length_beats_spin.setRange(0, 3)
        self._length_beats_spin.setValue(0)
        self._length_beats_spin.setSuffix(" beats")
        self._length_beats_spin.valueChanged.connect(
            lambda v: self.param_changed.emit("length_beats", v)
        )
        self._length_beats_spin.setToolTip("Additional beats beyond the bar count")
        grid.addWidget(self._length_beats_spin, 3, 2)

        self._free_length_chk = QCheckBox("Free length")
        self._free_length_chk.toggled.connect(self._on_free_length_toggled)
        self._free_length_chk.setToolTip(
            "Record indefinitely until manually stopped (bar/beat length ignored)"
        )
        grid.addWidget(self._free_length_chk, 3, 3)

        self._quantize_combo = QComboBox()
        self._quantize_combo.addItems(["Immediate", "Beat", "Bar"])
        self._quantize_combo.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("quantize", v)
        )
        self._quantize_combo.setToolTip("When recording starts after pressing ARM")
        grid.addWidget(QLabel("Quantize:"), 4, 0)
        grid.addWidget(self._quantize_combo, 4, 1)

        self._count_in_spin = QSpinBox()
        self._count_in_spin.setRange(0, 4)
        self._count_in_spin.setSuffix(" bars")
        self._count_in_spin.valueChanged.connect(
            lambda v: self.param_changed.emit("count_in", v)
        )
        self._count_in_spin.setToolTip("Bars of count-in before recording starts (0 = off)")
        grid.addWidget(QLabel("Count-in:"), 4, 2)
        grid.addWidget(self._count_in_spin, 4, 3)

        self._loop_chk = QCheckBox("Loop recording")
        self._loop_chk.toggled.connect(
            lambda v: self.param_changed.emit("loop", v)
        )
        self._loop_chk.setToolTip("Continuously overwrite the buffer when it fills")
        grid.addWidget(self._loop_chk, 5, 0, 1, 2)

        self._thru_chk = QCheckBox("Input thru")
        self._thru_chk.toggled.connect(
            lambda v: self.param_changed.emit("thru", v)
        )
        self._thru_chk.setToolTip("Monitor the input through the main output while recording")
        grid.addWidget(self._thru_chk, 5, 2, 1, 2)

        self._auto_load_chk = QCheckBox("Auto-load to channel")
        self._auto_load_chk.setChecked(True)
        self._auto_load_chk.toggled.connect(
            lambda v: self.param_changed.emit("auto_load", v)
        )
        self._auto_load_chk.setToolTip(
            "Load the recorded buffer to the target channel automatically"
        )
        grid.addWidget(self._auto_load_chk, 6, 0, 1, 4)

        layout.addLayout(grid)

        # ---- Input level meter + trim ----------------------------------
        meter_row = QHBoxLayout()
        meter_row.setSpacing(8)

        meter_label = QLabel("INPUT")
        meter_label.setStyleSheet(f"font-size: 10px; color: {TEXT_SECONDARY};")
        meter_row.addWidget(meter_label)

        self._vu = VUMeter(orientation=Qt.Orientation.Horizontal)
        bind_min_width(self._vu, 120)
        self._vu.setToolTip("Real-time input level — peak hold (gain-stage before recording)")
        meter_row.addWidget(self._vu, 1)

        # Trim knob: widget value 0..1 maps to trim 0..2 (1.0 = unity at midpoint)
        trim_box = QVBoxLayout()
        trim_box.setSpacing(0)
        self._trim_knob = RotaryKnob(default_value=0.5)
        self._trim_knob.value_changed.connect(
            lambda v: self.param_changed.emit("input_trim", float(v) * 2.0)
        )
        self._trim_knob.setToolTip("Pre-record gain (knob: left=mute, center=unity, right=+6dB)")
        trim_box.addWidget(self._trim_knob, alignment=Qt.AlignmentFlag.AlignHCenter)
        trim_label = QLabel("Trim")
        trim_label.setStyleSheet(f"font-size: 9px; color: {TEXT_SECONDARY};")
        trim_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        trim_box.addWidget(trim_label)
        meter_row.addLayout(trim_box)

        layout.addLayout(meter_row)

        # ---- Progress bar ---------------------------------------------
        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        bind_fixed_height(self._progress, 6)
        self._progress.setToolTip("Recording progress (0–100%)")
        layout.addWidget(self._progress)

        # ---- Record button --------------------------------------------
        self._rec_btn = QPushButton("ARM")
        self._rec_btn.setCheckable(True)
        bind_fixed_height(self._rec_btn, 34)
        self._rec_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG_BLACK}; border: 1px solid {SEPARATOR}; border-radius: 0px;
                          font-size: 12px; font-weight: bold; color: {TEXT_SECONDARY}; }}
            QPushButton:checked {{ background: #cc3333; border-color: #cc3333; color: white; }}
            QPushButton:hover {{ border-color: {ACCENT_GOLD}; }}
        """)
        self._rec_btn.toggled.connect(self.record_toggled.emit)
        self._rec_btn.setToolTip(
            "ARM: ready the recorder; recording starts based on the Quantize setting.\n"
            "Click again to stop and load to the target channel."
        )
        layout.addWidget(self._rec_btn)

        # ---- Pre-roll / retroactive capture ---------------------------
        preroll_box = QGroupBox("Pre-roll Capture")
        preroll_box.setStyleSheet(f"QGroupBox {{ color: {TEXT_SECONDARY}; font-size: 10px; }}")
        pr_lay = QHBoxLayout(preroll_box)
        pr_lay.setSpacing(6)
        self._preroll_spin = QDoubleSpinBox()
        self._preroll_spin.setRange(1.0, 30.0)
        self._preroll_spin.setSingleStep(0.5)
        self._preroll_spin.setSuffix(" s")
        self._preroll_spin.setValue(5.0)
        self._preroll_spin.valueChanged.connect(
            lambda v: self.param_changed.emit("pre_roll_sec", float(v))
        )
        self._preroll_spin.setToolTip(
            "Seconds of input audio kept in the ring buffer for retroactive capture"
        )
        pr_lay.addWidget(self._preroll_spin)
        self._capture_btn = QPushButton("Capture Last")
        self._capture_btn.clicked.connect(
            lambda: self.capture_preroll.emit(float(self._preroll_spin.value()))
        )
        self._capture_btn.setToolTip(
            "Grab the most recent N seconds of input — perfect for catching\n"
            "happy accidents you forgot to record"
        )
        pr_lay.addWidget(self._capture_btn, 1)
        layout.addWidget(preroll_box)

        # ---- Post-capture processing ----------------------------------
        post_box = QGroupBox("On Capture")
        post_box.setStyleSheet(f"QGroupBox {{ color: {TEXT_SECONDARY}; font-size: 10px; }}")
        pl = QGridLayout(post_box)
        pl.setSpacing(4)
        self._normalize_chk = QCheckBox("Normalize")
        self._normalize_chk.setChecked(True)
        self._normalize_chk.toggled.connect(
            lambda v: self.param_changed.emit("normalize", v)
        )
        self._normalize_chk.setToolTip("Peak-normalize the recording to -0.1 dBFS")
        pl.addWidget(self._normalize_chk, 0, 0)

        self._trim_silence_chk = QCheckBox("Trim silence")
        self._trim_silence_chk.toggled.connect(
            lambda v: self.param_changed.emit("trim_silence", v)
        )
        self._trim_silence_chk.setToolTip("Strip leading/trailing silence below -50 dBFS")
        pl.addWidget(self._trim_silence_chk, 0, 1)

        pl.addWidget(QLabel("Fade:"), 1, 0)
        self._fade_spin = QSpinBox()
        self._fade_spin.setRange(0, 50)
        self._fade_spin.setValue(10)
        self._fade_spin.setSuffix(" ms")
        self._fade_spin.valueChanged.connect(
            lambda v: self.param_changed.emit("fade_ms", v)
        )
        self._fade_spin.setToolTip("Fade-in/out applied to recording edges (prevents clicks)")
        pl.addWidget(self._fade_spin, 1, 1)
        layout.addWidget(post_box)

        # ---- Take slots -----------------------------------------------
        takes_box = QGroupBox("Takes")
        takes_box.setStyleSheet(f"QGroupBox {{ color: {TEXT_SECONDARY}; font-size: 10px; }}")
        takes_outer = QVBoxLayout(takes_box)
        takes_outer.setSpacing(2)
        takes_header = QHBoxLayout()
        takes_header.addWidget(QLabel("Slots:"))
        self._takes_count = QSpinBox()
        self._takes_count.setRange(1, 4)
        self._takes_count.setValue(1)
        self._takes_count.valueChanged.connect(
            lambda v: self.param_changed.emit("takes", v)
        )
        self._takes_count.setToolTip("Maximum number of takes kept in memory (oldest dropped)")
        takes_header.addWidget(self._takes_count)
        takes_header.addStretch()
        self._clear_takes_btn = QPushButton("Clear")
        self._clear_takes_btn.clicked.connect(self.clear_takes.emit)
        takes_header.addWidget(self._clear_takes_btn)
        takes_outer.addLayout(takes_header)

        self._takes_list_widget = QWidget()
        self._takes_list_lay = QVBoxLayout(self._takes_list_widget)
        self._takes_list_lay.setContentsMargins(0, 0, 0, 0)
        self._takes_list_lay.setSpacing(2)
        takes_outer.addWidget(self._takes_list_widget)
        self._takes_empty_label = QLabel("(no takes recorded yet)")
        self._takes_empty_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10px;")
        self._takes_list_lay.addWidget(self._takes_empty_label)
        layout.addWidget(takes_box)

        # ---- Send to Slice --------------------------------------------
        self._send_slice_btn = QPushButton("Send to Slice →")
        self._send_slice_btn.setEnabled(False)
        self._send_slice_btn.clicked.connect(self.send_to_slice.emit)
        self._send_slice_btn.setToolTip(
            "Save the latest take as a WAV in data/slices/ and open it\n"
            "in the Slice tab for chopping"
        )
        layout.addWidget(self._send_slice_btn)

        layout.addStretch()

    # ---------------------------------------------------------------- helpers

    def _on_free_length_toggled(self, v: bool):
        self.param_changed.emit("free_length", v)
        # Disable length controls when in free-length mode (irrelevant)
        self._length_spin.setEnabled(not v)
        self._length_beats_spin.setEnabled(not v)
        self._quantize_combo.setEnabled(not v)
        self._count_in_spin.setEnabled(not v)

    def _on_loopback_toggled(self, v: bool):
        self.param_changed.emit("loopback", v)
        # Hide device input combo when loopback is active (it's irrelevant)
        self._input_combo.setEnabled(not v)
        self._input_ch.setEnabled(not v)

    # ---------------------------------------------------------------- public API

    def set_state(self, state: RecorderState):
        for w, attr, signal_name in [
            (self._buffer_combo, "buffer_idx", "currentIndex"),
            (self._length_spin, "length_bars", "value"),
            (self._length_beats_spin, "length_beats", "value"),
            (self._loop_chk, "loop", "checked"),
            (self._thru_chk, "thru", "checked"),
            (self._quantize_combo, "quantize", "currentIndex"),
            (self._count_in_spin, "count_in", "value"),
            (self._auto_load_chk, "auto_load", "checked"),
            (self._free_length_chk, "free_length", "checked"),
            (self._loopback_chk, "loopback", "checked"),
            (self._preroll_spin, "pre_roll_sec", "value"),
            (self._takes_count, "takes", "value"),
            (self._normalize_chk, "normalize", "checked"),
            (self._trim_silence_chk, "trim_silence", "checked"),
            (self._fade_spin, "fade_ms", "value"),
        ]:
            if not hasattr(state, attr):
                continue
            v = getattr(state, attr)
            w.blockSignals(True)
            try:
                if signal_name == "currentIndex":
                    w.setCurrentIndex(int(v))
                elif signal_name == "value":
                    w.setValue(v)
                elif signal_name == "checked":
                    w.setChecked(bool(v))
            finally:
                w.blockSignals(False)
        # Trim knob (special: state value is 0..2, knob is 0..1)
        if hasattr(state, "input_trim"):
            self._trim_knob.blockSignals(True)
            try:
                self._trim_knob.set_value(max(0.0, min(1.0, float(state.input_trim) / 2.0)))
            finally:
                self._trim_knob.blockSignals(False)
        # Sync derived enables
        self._length_spin.setEnabled(not state.free_length)
        self._length_beats_spin.setEnabled(not state.free_length)
        self._quantize_combo.setEnabled(not state.free_length)
        self._count_in_spin.setEnabled(not state.free_length)
        self._input_combo.setEnabled(not state.loopback)
        self._input_ch.setEnabled(not state.loopback)

    def set_input_devices(self, devices: list[str]):
        self._input_combo.blockSignals(True)
        self._input_combo.clear()
        self._input_combo.addItems(devices)
        self._input_combo.blockSignals(False)

    def set_input_level(self, peak: float, rms: float):
        try:
            self._vu.set_level(float(peak))
        except Exception:
            pass

    def set_progress(self, value: float):
        try:
            v = max(0.0, min(1.0, float(value)))
            self._progress.setValue(int(v * 1000))
        except Exception:
            pass

    def set_recording(self, recording: bool, label: str = ""):
        """Update the ARM button label and visual state."""
        self._rec_btn.blockSignals(True)
        try:
            if label:
                self._rec_btn.setText(label)
            else:
                self._rec_btn.setText("● REC" if recording else "ARM")
            self._rec_btn.setChecked(recording)
        finally:
            self._rec_btn.blockSignals(False)

    def set_armed(self, armed: bool):
        """External force-uncheck (e.g. on auto-complete)."""
        self._rec_btn.blockSignals(True)
        try:
            self._rec_btn.setChecked(armed)
            if not armed:
                self._rec_btn.setText("ARM")
        finally:
            self._rec_btn.blockSignals(False)

    def set_takes(self, takes: list):
        """takes: list of (idx, duration_sec) tuples."""
        # Clear existing rows. Drop the stale Python ref to the empty label
        # FIRST so we don't accidentally touch a deleteLater'd C++ object.
        self._takes_empty_label = None
        while self._takes_list_lay.count() > 0:
            item = self._takes_list_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not takes:
            self._takes_empty_label = QLabel("(no takes recorded yet)")
            self._takes_empty_label.setStyleSheet(
                f"color: {TEXT_SECONDARY}; font-size: 10px;"
            )
            self._takes_list_lay.addWidget(self._takes_empty_label)
            return
        for idx, dur in takes:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(4)
            lbl = QLabel(f"Take {idx + 1}  ({dur:.2f}s)")
            lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 10px;")
            rl.addWidget(lbl, 1)
            play_btn = QPushButton("▶")
            bind_fixed_width(play_btn, 28)
            play_btn.clicked.connect(lambda _=False, i=idx: self.play_take.emit(i))
            rl.addWidget(play_btn)
            use_btn = QPushButton("Use")
            bind_fixed_width(use_btn, 40)
            use_btn.clicked.connect(lambda _=False, i=idx: self.use_take.emit(i))
            rl.addWidget(use_btn)
            self._takes_list_lay.addWidget(row)

    def set_send_to_slice_enabled(self, enabled: bool):
        self._send_slice_btn.setEnabled(bool(enabled))


# ---------------------------------------------------------------------------
#  FX Rack view — replaces old tabbed layout with skeuomorphic rack UI
# ---------------------------------------------------------------------------

from mlrvp.gui.widgets.rack_container import RackContainer
from mlrvp.gui.rack_modules.digital_delay import DigitalDelayModule
from mlrvp.gui.rack_modules.tape_delay import TapeDelayModule
from mlrvp.gui.rack_modules.multiband_filter import MultibandFilterModule
from mlrvp.gui.rack_modules.moog_filter import MoogFilterModule
from mlrvp.gui.rack_modules.phaser import PhaserModule
from mlrvp.gui.rack_modules.chorus import ChorusModule
from mlrvp.gui.rack_modules.flanger import FlangerModule
from mlrvp.gui.rack_modules.dual_vu_meter import DualVUMeterModule
from mlrvp.gui.rack_modules.compressor import CompressorModule
from mlrvp.gui.rack_modules.glue_compressor import GlueCompressorModule
from mlrvp.gui.rack_modules.oscilloscope import OscilloscopeModule
from mlrvp.gui.rack_modules.reel_to_reel import ReelToReelModule
from mlrvp.gui.rack_modules.cassette_tape import MixtapeModule
from mlrvp.gui.rack_modules.vhs import VHSModule
from mlrvp.gui.rack_modules.vinyl import VinylModule
from mlrvp.gui.rack_modules.ether_drift import EtherDriftModule
from mlrvp.gui.rack_modules.hall_reverb import HallReverbModule
from mlrvp.gui.rack_modules.bit_crusher import BitCrusherModule
from mlrvp.gui.rack_modules.tube_overdrive import TubeOverdriveModule
from mlrvp.gui.rack_modules.noise_gate import NoiseGateModule
from mlrvp.gui.rack_modules.tremolo import TremoloModule
from mlrvp.gui.rack_modules.auto_wah import AutoWahModule
from mlrvp.gui.rack_modules.fet_limiter import FETLimiterModule
from mlrvp.gui.rack_modules.transient_shaper import TransientShaperModule

# Total effects across all FX groups must stay under this cap.
# Budget: 512 samples @ 44100 Hz → 11.6ms callback, ~10.1ms for FX (after ~1.5ms routing overhead).
# Benchmarked timings (post-optimisation):
#   MoogLadder 0.43ms  |  HallReverb 0.16ms  |  PlateReverb 0.13ms
#   Compressor 0.15ms  |  LoFi processors 0.08–0.13ms  |  everything else < 0.08ms
# Worst-case (all 28 × MoogLadder) = 12.04ms — ring buffer (8 blocks, ~93ms)
# absorbs transient spikes.  Realistic 28-module mix ≈ 4.64ms — very comfortable.
MAX_TOTAL_EFFECTS = 28


class _GroupTabButton(QPushButton):
    """Vintage backlit console-style tab button."""

    def __init__(self, text: str, active: bool = False, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setChecked(active)
        bind_fixed_height(self, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()
        self.toggled.connect(lambda: self._update_style())

    def _update_style(self):
        if self.isChecked():
            self.setStyleSheet(
                "QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                "stop:0 #444444, stop:0.4 #333333, stop:1 #222222);"
                f"color: {ACCENT_GOLD}; font-weight: bold; font-size: 10px;"
                "border: 1px solid #666666; border-bottom: none;"
                "border-top-left-radius: 3px; border-top-right-radius: 3px; padding: 2px 12px;"
                "letter-spacing: 1px; }"
            )
        else:
            self.setStyleSheet(
                "QPushButton { background: #1a1a1a;"
                f"color: {TEXT_SECONDARY}; font-weight: bold; font-size: 10px;"
                "border: 1px solid #444444; border-bottom: none;"
                "border-top-left-radius: 3px; border-top-right-radius: 3px; padding: 2px 12px;"
                "letter-spacing: 1px; }"
                "QPushButton:hover { background: #2a2a2a; }"
            )


class _RackLoadMenu(QMenu):
    """QMenu subclass that shows a rename/delete context menu on right-click.

    Rack-profile entries are tagged via QAction.setData(name).  Right-clicking
    such an entry invokes the parent EffectsView's context-menu handler.
    """

    def __init__(self, effects_view):
        super().__init__(effects_view)
        self._effects_view = effects_view

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            act = self.actionAt(event.pos())
            if act is not None:
                name = act.data()
                if isinstance(name, str) and name:
                    self._effects_view._rack_profile_context_menu(
                        name, self, event.globalPosition().toPoint()
                    )
                    return
        super().mousePressEvent(event)


class EffectsView(QWidget):
    """FX Rack view with two switchable groups (A/B), each an independent rack."""

    # Same signals as the old EffectsView — app.py wiring untouched
    delay_param_changed = Signal(str, object, int)
    tape_delay_param_changed = Signal(str, object, int)
    filter_a_param_changed = Signal(str, object, int)
    filter_b_param_changed = Signal(str, object, int)
    mod_param_changed = Signal(str, object, int)
    reel_to_reel_param_changed = Signal(str, object, int)
    cassette_tape_param_changed = Signal(str, object, int)
    vhs_param_changed = Signal(str, object, int)
    vinyl_param_changed = Signal(str, object, int)
    compressor_param_changed = Signal(str, object, int)
    glue_compressor_param_changed = Signal(str, object, int)
    hall_reverb_param_changed = Signal(str, object, int)
    plate_reverb_param_changed = Signal(str, object, int)
    bit_crusher_param_changed = Signal(str, object, int)
    tube_overdrive_param_changed = Signal(str, object, int)
    ether_drift_param_changed = Signal(str, object, int)
    noise_gate_param_changed = Signal(str, object, int)
    tremolo_param_changed = Signal(str, object, int)
    auto_wah_param_changed = Signal(str, object, int)
    fet_limiter_param_changed = Signal(str, object, int)
    rotary_param_changed = Signal(str, object, int)
    exciter_param_changed = Signal(str, object, int)
    transient_shaper_param_changed = Signal(str, object, int)
    harmonizer_param_changed = Signal(str, object, int)
    stereo_widener_param_changed = Signal(str, object, int)
    freq_shifter_param_changed = Signal(str, object, int)
    ring_mod_param_changed = Signal(str, object, int)
    simple_eq_param_changed = Signal(str, object, int)
    ping_pong_delay_param_changed = Signal(str, object, int)
    shimmer_reverb_param_changed = Signal(str, object, int)
    lofi_radio_param_changed = Signal(str, object, int)
    auto_pan_param_changed = Signal(str, object, int)
    fuzz_param_changed = Signal(str, object, int)
    granular_freeze_param_changed = Signal(str, object, int)
    gate_reverb_param_changed = Signal(str, object, int)
    stutter_param_changed = Signal(str, object, int)
    stowaway_param_changed = Signal(str, object, int)
    effect_route_changed = Signal(str, int)
    midi_map_toggled = Signal(bool)  # activates the global MIDI map manager
    rack_contents_changed = Signal(dict)  # rack/effect sync payload for app↔engine
    # RACK profile (LOAD/SAVE) signals
    rack_save_requested = Signal(str, str, list)  # (profile_name, bus_suffix, [effect_type...])
    rack_load_requested = Signal(str, str)        # (profile_name, bus_suffix)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {BG_PANEL};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Tab bar: Group A / Group B buttons ---
        tab_bar = QHBoxLayout()
        tab_bar.setContentsMargins(4, 4, 4, 0)
        tab_bar.setSpacing(4)
        self._tab_a = _GroupTabButton("FX 1", active=True)
        self._tab_b = _GroupTabButton("FX 2", active=False)
        self._tab_c = _GroupTabButton("FX 3", active=False)
        self._tab_d = _GroupTabButton("FX 4", active=False)
        self._tab_m = _GroupTabButton("MASTER", active=False)
        self._tab_a.setToolTip("FX Group A — independent effects chain routed from mixer channels set to FX1")
        self._tab_b.setToolTip("FX Group B — independent effects chain routed from mixer channels set to FX2")
        self._tab_c.setToolTip("FX Group C — independent effects chain routed from mixer channels set to FX3")
        self._tab_d.setToolTip("FX Group D — independent effects chain routed from mixer channels set to FX4")
        self._tab_m.setToolTip("FX Group M — master bus effects chain applied to the final mix output")
        self._tab_a.clicked.connect(lambda: self._switch_group(0))
        self._tab_b.clicked.connect(lambda: self._switch_group(1))
        self._tab_c.clicked.connect(lambda: self._switch_group(2))
        self._tab_d.clicked.connect(lambda: self._switch_group(3))
        self._tab_m.clicked.connect(lambda: self._switch_group(4))

        # MIDI map toggle
        self._midi_map_btn = QPushButton("MIDI MAP")
        self._midi_map_btn.setCheckable(True)
        bind_fixed_width(self._midi_map_btn, 80)
        bind_fixed_height(self._midi_map_btn, 26)
        self._midi_map_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_PANEL}; border: 1px solid #444444; border-bottom: none;"
            f"border-top-left-radius: 3px; border-top-right-radius: 3px; color: {TEXT_SECONDARY}; font-size: 9px; font-weight: bold; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ background: #2a2a2a; color: {TEXT_PRIMARY}; }}"
            f"QPushButton:checked {{ background: {ACCENT_BLUE}; border-color: {ACCENT_BLUE};"
            f"color: {BG_BLACK}; }}"
        )
        self._midi_map_btn.toggled.connect(self._toggle_midi_map)
        self._midi_map_btn.setToolTip(
            "Toggle MIDI map overlay — hover over any knob or fader\n"
            "to assign a MIDI CC controller to that parameter"
        )

        # RACK profile LOAD / SAVE buttons
        rack_btn_qss = (
            f"QPushButton {{ background: {BG_PANEL}; border: 1px solid #444444; border-bottom: none;"
            f"border-top-left-radius: 3px; border-top-right-radius: 3px; color: {TEXT_SECONDARY};"
            f"font-size: 9px; font-weight: bold; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ background: #2a2a2a; color: {TEXT_PRIMARY}; }}"
            f"QPushButton:pressed {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        self._rack_load_btn = QPushButton("LOAD")
        bind_fixed_width(self._rack_load_btn, 54)
        bind_fixed_height(self._rack_load_btn, 26)
        self._rack_load_btn.setStyleSheet(rack_btn_qss)
        self._rack_load_btn.setToolTip(
            "Load a saved RACK profile into the currently selected rack tab"
        )
        self._rack_load_btn.clicked.connect(self._on_rack_load_clicked)

        self._rack_save_btn = QPushButton("SAVE")
        bind_fixed_width(self._rack_save_btn, 54)
        bind_fixed_height(self._rack_save_btn, 26)
        self._rack_save_btn.setStyleSheet(rack_btn_qss)
        self._rack_save_btn.setToolTip(
            "Save the currently selected rack tab as a named RACK profile"
        )
        self._rack_save_btn.clicked.connect(self._on_rack_save_clicked)

        tab_bar.addWidget(self._tab_a)
        tab_bar.addWidget(self._tab_b)
        tab_bar.addWidget(self._tab_c)
        tab_bar.addWidget(self._tab_d)
        tab_bar.addWidget(self._tab_m)
        tab_bar.addStretch()
        tab_bar.addWidget(self._rack_load_btn)
        tab_bar.addWidget(self._rack_save_btn)
        tab_bar.addWidget(self._midi_map_btn)
        layout.addLayout(tab_bar)

        # --- Per-effect route state ---
        self._effect_routes: dict[str, int] = {
            "delay": 1, "tape_delay": 2,
            "filter_a": 1, "filter_b": 2,
            "mod": 1,
        }
        self._route_btns: dict[str, QPushButton] = {}

        # --- Stack for the two rack groups ---
        from PySide6.QtWidgets import QStackedWidget
        self._stack = QStackedWidget()
        self._rack_a = RackContainer()
        self._rack_b = RackContainer()
        self._rack_c = RackContainer()
        self._rack_d = RackContainer()
        self._rack_m = RackContainer()
        self._stack.addWidget(self._rack_a)
        self._stack.addWidget(self._rack_b)
        self._stack.addWidget(self._rack_c)
        self._stack.addWidget(self._rack_d)
        self._stack.addWidget(self._rack_m)
        layout.addWidget(self._stack, stretch=1)

        # Cross-rack global limit enforcement
        self._rack_a.set_global_limit_fn(self._at_global_limit)
        self._rack_b.set_global_limit_fn(self._at_global_limit)
        self._rack_c.set_global_limit_fn(self._at_global_limit)
        self._rack_d.set_global_limit_fn(self._at_global_limit)
        self._rack_m.set_global_limit_fn(self._at_global_limit)

        # Track MIDI highlight state so newly-added modules get highlighted
        self._midi_highlight_on = False
        self._rack_a.rack_changed.connect(self._on_rack_changed)
        self._rack_b.rack_changed.connect(self._on_rack_changed)
        self._rack_c.rack_changed.connect(self._on_rack_changed)
        self._rack_d.rack_changed.connect(self._on_rack_changed)
        self._rack_m.rack_changed.connect(self._on_rack_changed)

        # --- Populate Group A (FX Bus 1) — Clean Channel: dynamics → delay ---
        self._vu_a = DualVUMeterModule()
        self._compressor_a = CompressorModule()
        self._delay_module = DigitalDelayModule()

        for mod in [self._vu_a, self._compressor_a, self._delay_module]:
            self._rack_a.add_module(mod)

        # --- Populate Group B (FX Bus 2) — Breakbeat: glue comp → transient → cassette ---
        self._vu_b = DualVUMeterModule()
        self._glue_compressor_b = GlueCompressorModule()
        self._transient_shaper_b = TransientShaperModule()
        self._cassette_b = MixtapeModule()

        for mod in [self._vu_b, self._glue_compressor_b, self._transient_shaper_b,
                    self._cassette_b]:
            self._rack_b.add_module(mod)

        # Keep reel and tape_delay as hidden instances (legacy shims reference them)
        self._reel_module = ReelToReelModule(parent=self)
        self._reel_module.setVisible(False)
        self._tape_delay_module = TapeDelayModule(parent=self)
        self._tape_delay_module.setVisible(False)

        # --- Populate Group C (FX Bus 3) — Space: reverb → modulation ---
        self._vu_c = DualVUMeterModule()
        self._hall_reverb_c = HallReverbModule()
        self._chorus_module = ChorusModule()

        for mod in [self._vu_c, self._hall_reverb_c, self._chorus_module]:
            self._rack_c.add_module(mod)

        # --- Populate Group D (FX Bus 4) — Lo-Fi / Destroy: crush → drive ---
        self._vu_d = DualVUMeterModule()
        self._bit_crusher_d = BitCrusherModule()
        self._tube_overdrive_d = TubeOverdriveModule()

        for mod in [self._vu_d, self._bit_crusher_d, self._tube_overdrive_d]:
            self._rack_d.add_module(mod)

        # --- Populate Group M (Master bus) — Mastering: glue → limiter → monitor ---
        self._vu_m = DualVUMeterModule()
        self._compressor_m = GlueCompressorModule()
        self._fet_limiter_m = FETLimiterModule()
        self._scope_m = OscilloscopeModule()
        for mod in [self._vu_m, self._compressor_m,
                    self._fet_limiter_m, self._scope_m]:
            self._rack_m.add_module(mod)

        # Route ALL module params (including dynamically added modules)
        # through the container's relay signal so app.py handlers get them.
        self._rack_a.module_param_changed.connect(
            lambda cls, p, v: self._route_module_param(cls, p, v, 1))
        self._rack_b.module_param_changed.connect(
            lambda cls, p, v: self._route_module_param(cls, p, v, 2))
        self._rack_c.module_param_changed.connect(
            lambda cls, p, v: self._route_module_param(cls, p, v, 4))
        self._rack_d.module_param_changed.connect(
            lambda cls, p, v: self._route_module_param(cls, p, v, 5))
        self._rack_m.module_param_changed.connect(
            lambda cls, p, v: self._route_module_param(cls, p, v, 3))

        # --- Legacy panel shims for app.py property access ---
        # Hidden instances for effects not in any default rack but still
        # needed for state management and the ＋ menu add workflow.
        # Parent them to self so Qt keeps their C++ objects alive.
        self._filter_a_module = MultibandFilterModule(parent=self)
        self._filter_a_module.setVisible(False)
        self._filter_b_module = MoogFilterModule(parent=self)
        self._filter_b_module.setVisible(False)
        self._phaser_module = PhaserModule(parent=self)
        self._phaser_module.setVisible(False)
        self._flanger_module = FlangerModule(parent=self)
        self._flanger_module.setVisible(False)
        self._bit_crusher_b_hidden = BitCrusherModule(parent=self)
        self._bit_crusher_b_hidden.setVisible(False)
        self._cassette_module = MixtapeModule(parent=self)
        self._cassette_module.setVisible(False)
        self._vhs_module = VHSModule(parent=self)
        self._vhs_module.setVisible(False)
        self._vinyl_module = VinylModule(parent=self)
        self._vinyl_module.setVisible(False)
        self._vinyl_b = VinylModule(parent=self)
        self._vinyl_b.setVisible(False)
        self._scope_b = OscilloscopeModule(parent=self)
        self._scope_b.setVisible(False)
        self._ether_drift_module = EtherDriftModule(parent=self)
        self._ether_drift_module.setVisible(False)
        self._noise_gate_module = NoiseGateModule(parent=self)
        self._noise_gate_module.setVisible(False)
        self._tremolo_module = TremoloModule(parent=self)
        self._tremolo_module.setVisible(False)
        self._auto_wah_module = AutoWahModule(parent=self)
        self._auto_wah_module.setVisible(False)
        self._fet_limiter_module = FETLimiterModule(parent=self)
        self._fet_limiter_module.setVisible(False)
        self._delay_panel = _LegacyPanelShim(self._delay_module)
        self._tape_delay_panel = _LegacyPanelShim(self._tape_delay_module)
        self._filter_a_panel = _LegacyPanelShim(self._filter_a_module)
        self._filter_b_panel = _LegacyPanelShim(self._filter_b_module)
        self._mod_panel = _LegacyModPanelShim(
            self._phaser_module, self._chorus_module, self._flanger_module
        )

    # --- Group switching ---
    def _switch_group(self, idx: int):
        self._stack.setCurrentIndex(idx)
        self._tab_a.setChecked(idx == 0)
        self._tab_b.setChecked(idx == 1)
        self._tab_c.setChecked(idx == 2)
        self._tab_d.setChecked(idx == 3)
        self._tab_m.setChecked(idx == 4)

    # --- RACK profile (LOAD / SAVE) ---
    # Map stack index -> (rack container, AppConfig bus suffix)
    _BUS_SUFFIX_BY_INDEX = {0: "", 1: "_b", 2: "_c", 3: "_d", 4: "_m"}

    @property
    def current_bus_suffix(self) -> str:
        """AppConfig attribute suffix for the rack tab currently visible."""
        return self._BUS_SUFFIX_BY_INDEX.get(self._stack.currentIndex(), "")

    def _current_rack(self):
        racks = (self._rack_a, self._rack_b, self._rack_c,
                 self._rack_d, self._rack_m)
        idx = self._stack.currentIndex()
        if 0 <= idx < len(racks):
            return racks[idx]
        return self._rack_a

    def _on_rack_save_clicked(self):
        rack = self._current_rack()
        types = [getattr(m, "effect_type", None) for m in rack.get_modules()]
        types = [t for t in types if t]
        if not types:
            QMessageBox.information(self, "Save Rack",
                                    "The current rack is empty — nothing to save.")
            return
        name, ok = QInputDialog.getText(self, "Save Rack Profile",
                                        "Profile name:")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        # Confirm overwrite if a profile with this name already exists.
        try:
            from mlrvp.preset.rack_manager import list_racks, _sanitize_name  # type: ignore
        except Exception:
            existing = []
            sanitised = name
        else:
            existing = list_racks()
            sanitised = _sanitize_name(name)
        if sanitised in existing:
            ans = QMessageBox.question(
                self, "Overwrite Rack Profile",
                f"A rack profile named '{sanitised}' already exists.\n\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        self.rack_save_requested.emit(name, self.current_bus_suffix, types)

    def _on_rack_load_clicked(self):
        try:
            from mlrvp.preset.rack_manager import list_racks
        except Exception:
            return
        from mlrvp.gui.widgets.overflow_menu import (
            MAX_FLAT, COLUMN_SIZE, MENU_QSS,
            _group_by_letter_range, _cap_to_screen,
        )
        names = list_racks()
        menu = _RackLoadMenu(self)
        menu.setToolTipsVisible(True)
        menu.setStyleSheet(MENU_QSS)
        _cap_to_screen(menu)

        if not names:
            menu.addAction("(no saved racks)").setEnabled(False)
        else:
            bus = self.current_bus_suffix
            items = [(n, n) for n in sorted(names, key=str.lower)]

            def _add_profile_action(target_menu, n):
                act = target_menu.addAction(n)
                act.setData(n)
                act.setToolTip(
                    f"Click to load '{n}' into the current rack\n"
                    "Right-click to rename or delete"
                )
                act.triggered.connect(
                    lambda checked=False, _n=n, _b=bus:
                        self.rack_load_requested.emit(_n, _b)
                )

            if len(items) <= MAX_FLAT:
                for n, _ in items:
                    _add_profile_action(menu, n)
            else:
                groups = _group_by_letter_range(items, COLUMN_SIZE)
                for group_label, group_items in groups:
                    if len(group_items) == 1:
                        _add_profile_action(menu, group_items[0][0])
                    else:
                        sub = _RackLoadMenu(self)
                        sub.setTitle(group_label)
                        sub.setStyleSheet(MENU_QSS)
                        sub.setToolTipsVisible(True)
                        _cap_to_screen(sub)
                        menu.addMenu(sub)
                        for n, _ in group_items:
                            _add_profile_action(sub, n)

        pos = self._rack_load_btn.mapToGlobal(
            self._rack_load_btn.rect().bottomLeft()
        )
        menu.exec(pos)

    def _rack_profile_context_menu(self, profile_name: str, parent_menu: QMenu,
                                   global_pos):
        """Show a Rename/Delete context menu for a saved rack profile."""
        from mlrvp.preset import rack_manager
        ctx = QMenu(self)
        ctx.setStyleSheet(parent_menu.styleSheet())
        rename_act = ctx.addAction("Rename…")
        delete_act = ctx.addAction("Delete…")
        chosen = ctx.exec(global_pos)
        if chosen is None:
            return
        if chosen is rename_act:
            new_name, ok = QInputDialog.getText(
                self, "Rename Rack Profile",
                "New name:", text=profile_name,
            )
            if not ok:
                return
            new_name = new_name.strip()
            if not new_name or new_name == profile_name:
                return
            if not rack_manager.rename_rack(profile_name, new_name):
                QMessageBox.warning(
                    self, "Rename Failed",
                    f"Could not rename '{profile_name}' to '{new_name}'.\n"
                    "A profile with that name may already exist.",
                )
                return
            # Close parent menu (the list is now stale).
            parent_menu.close()
        elif chosen is delete_act:
            ans = QMessageBox.question(
                self, "Delete Rack Profile",
                f"Are you sure you want to permanently delete\n"
                f"the rack profile '{profile_name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            if not rack_manager.delete_rack(profile_name):
                QMessageBox.warning(
                    self, "Delete Failed",
                    f"Could not delete '{profile_name}'.",
                )
                return
            parent_menu.close()

    def load_rack_into_current(self, entries: list):
        """Replace the currently visible rack with the given module entries.

        ``entries`` is a list of ``(module_class, state_obj_or_None)`` tuples
        in rack order.  Called from app.py after AppConfig has been updated
        with the saved parameter values.  Existing ``rack_changed`` signal
        propagates new layout to the engine; app.py is responsible for
        pushing parameter values to the engine afterwards.
        """
        from mlrvp.gui.rack_modules.stowaway import ForeignExchangeModule
        rack = self._current_rack()
        rack.clear()
        # Cap at MAX_MODULES_PER_RACK; respect global cap silently.
        for entry in entries:
            try:
                cls, state = entry
            except (TypeError, ValueError):
                continue
            if cls is None:
                continue
            try:
                if cls is ForeignExchangeModule:
                    slot = rack._get_next_stowaway_slot() \
                        if hasattr(rack, "_get_next_stowaway_slot") else None
                    if slot is None:
                        continue
                    mod = cls(slot_index=slot)
                else:
                    mod = cls()
            except Exception:
                continue
            if not rack.add_module(mod):
                # Limit reached — stop loading further entries.
                try:
                    mod.deleteLater()
                except Exception:
                    pass
                break
            if state is not None:
                try:
                    mod.set_state(state)
                except Exception:
                    pass

    # --- Global effects limit ---
    def _at_global_limit(self) -> bool:
        """Return True if total effects across all racks hit the cap."""
        total = (len(self._rack_a.get_modules())
                 + len(self._rack_b.get_modules())
                 + len(self._rack_c.get_modules())
                 + len(self._rack_d.get_modules())
                 + len(self._rack_m.get_modules()))
        return total >= MAX_TOTAL_EFFECTS

    # --- MIDI mapping mode ---
    def _toggle_midi_map(self, on: bool):
        self._midi_highlight_on = on
        for rack in (self._rack_a, self._rack_b, self._rack_c, self._rack_d, self._rack_m):
            for mod in rack.get_modules():
                mod.set_midi_highlight(on)
        self.midi_map_toggled.emit(on)

    def set_midi_map_active(self, on: bool):
        """Sync the MIDI MAP button state from outside (e.g. setup view Learn)."""
        self._midi_highlight_on = on
        self._midi_map_btn.blockSignals(True)
        self._midi_map_btn.setChecked(on)
        self._midi_map_btn.blockSignals(False)
        for rack in (self._rack_a, self._rack_b, self._rack_c, self._rack_d, self._rack_m):
            for mod in rack.get_modules():
                mod.set_midi_highlight(on)

    def _on_rack_changed(self):
        """Sync MIDI highlight and effect in-rack status when rack contents change."""
        if self._midi_highlight_on:
            for rack in (self._rack_a, self._rack_b, self._rack_c, self._rack_d, self._rack_m):
                for mod in rack.get_modules():
                    mod.set_midi_highlight(True)
        self.rack_contents_changed.emit(self.get_rack_sync_state())

    def get_rack_sync_state(self) -> dict:
        """Return per-bus effect presence and ordering derived from current rack contents."""
        def _collect(rack):
            types = set()
            order = []
            for mod in rack.get_modules():
                et = getattr(mod, 'effect_type', None)
                if et:
                    types.add(et)
                    order.append(et)
            return types, order

        a_types, a_order = _collect(self._rack_a)
        b_types, b_order = _collect(self._rack_b)
        c_types, c_order = _collect(self._rack_c)
        d_types, d_order = _collect(self._rack_d)
        m_types, m_order = _collect(self._rack_m)

        all_fx = {"delay", "tape_delay", "filter_a", "filter_b",
                  "phaser", "chorus", "flanger",
                  "reel_to_reel", "cassette_tape", "vhs", "vinyl",
                  "compressor", "glue_compressor",
                  "hall_reverb", "plate_reverb",
                  "bit_crusher", "tube_overdrive", "ether_drift",
                  "noise_gate", "tremolo", "auto_wah",
                  "fet_limiter", "rotary", "exciter",
                  "transient_shaper", "harmonizer", "stereo_widener",
                  "freq_shifter", "ring_mod",
                  "simple_eq", "ping_pong_delay", "shimmer_reverb",
                  "lofi_radio", "auto_pan", "fuzz",
                  "granular_freeze", "gate_reverb", "stutter"}

        return {
            "presence_a": {et: (et in a_types) for et in all_fx},
            "presence_b": {et: (et in b_types) for et in all_fx},
            "presence_c": {et: (et in c_types) for et in all_fx},
            "presence_d": {et: (et in d_types) for et in all_fx},
            "presence_master": {et: (et in m_types) for et in all_fx},
            "order_a": a_order,
            "order_b": b_order,
            "order_c": c_order,
            "order_d": d_order,
            "order_m": m_order,
        }

    def _route_module_param(self, module_class: str, param: str, value, bus: int = 1):
        """Route param changes from any rack module to the correct signal."""
        _MAP = {
            "DigitalDelayModule": self.delay_param_changed,
            "TapeDelayModule": self.tape_delay_param_changed,
            "MultibandFilterModule": self.filter_a_param_changed,
            "MoogFilterModule": self.filter_b_param_changed,
            "PhaserModule": self.mod_param_changed,
            "ChorusModule": self.mod_param_changed,
            "FlangerModule": self.mod_param_changed,
            "ReelToReelModule": self.reel_to_reel_param_changed,
            "MixtapeModule": self.cassette_tape_param_changed,
            "VHSModule": self.vhs_param_changed,
            "VinylModule": self.vinyl_param_changed,
            "CompressorModule": self.compressor_param_changed,
            "GlueCompressorModule": self.glue_compressor_param_changed,
            "HallReverbModule": self.hall_reverb_param_changed,
            "PlateSpringReverbModule": self.plate_reverb_param_changed,
            "BitCrusherModule": self.bit_crusher_param_changed,
            "TubeOverdriveModule": self.tube_overdrive_param_changed,
            "EtherDriftModule": self.ether_drift_param_changed,
            "NoiseGateModule": self.noise_gate_param_changed,
            "TremoloModule": self.tremolo_param_changed,
            "AutoWahModule": self.auto_wah_param_changed,
            "FETLimiterModule": self.fet_limiter_param_changed,
            "RotaryModule": self.rotary_param_changed,
            "ExciterModule": self.exciter_param_changed,
            "TransientShaperModule": self.transient_shaper_param_changed,
            "HarmonizerModule": self.harmonizer_param_changed,
            "StereoWidenerModule": self.stereo_widener_param_changed,
            "FreqShifterModule": self.freq_shifter_param_changed,
            "RingModModule": self.ring_mod_param_changed,
            "SimpleEQModule": self.simple_eq_param_changed,
            "PingPongDelayModule": self.ping_pong_delay_param_changed,
            "ShimmerReverbModule": self.shimmer_reverb_param_changed,
            "LoFiRadioModule": self.lofi_radio_param_changed,
            "AutoPanModule": self.auto_pan_param_changed,
            "FuzzModule": self.fuzz_param_changed,
            "GranularFreezeModule": self.granular_freeze_param_changed,
            "GatedReverbModule": self.gate_reverb_param_changed,
            "StutterModule": self.stutter_param_changed,
            "ForeignExchangeModule": self.stowaway_param_changed,
        }
        sig = _MAP.get(module_class)
        if sig:
            sig.emit(param, value, bus)

    # --- Route buttons (kept for compatibility) ---
    def set_effect_route(self, effect_name: str, route: int):
        if effect_name in self._effect_routes:
            self._effect_routes[effect_name] = route % 3

    # --- Meter / visualizer data push ---
    def update_meters(self, levels: dict):
        """Push audio levels and waveform data to VU, compressor, and scope modules.

        VU meters always show master output (utility diagnostic).
        Oscilloscopes show per-bus waveform (Rack A → FX1, Rack B → FX2).
        Compressors receive per-bus levels.
        """
        if not levels:
            return
        fx1 = levels.get("fx1", [0.0, 0.0])
        fx2 = levels.get("fx2", [0.0, 0.0])
        fx3 = levels.get("fx3", [0.0, 0.0])
        fx4 = levels.get("fx4", [0.0, 0.0])

        # Per-bus waveform for oscilloscope isolation
        bus_waveforms = {
            id(self._rack_a): (levels.get("waveform_fx1_l"), levels.get("waveform_fx1_r")),
            id(self._rack_b): (levels.get("waveform_fx2_l"), levels.get("waveform_fx2_r")),
            id(self._rack_c): (levels.get("waveform_fx3_l"), levels.get("waveform_fx3_r")),
            id(self._rack_d): (levels.get("waveform_fx4_l"), levels.get("waveform_fx4_r")),
            id(self._rack_m): (levels.get("waveform_l"), levels.get("waveform_r")),
        }

        master = levels.get("master", [0.0, 0.0])
        for rack, bus_levels in (
            (self._rack_a, fx1), (self._rack_b, fx2),
            (self._rack_c, fx3), (self._rack_d, fx4),
            (self._rack_m, master),
        ):
            bus_l, bus_r = bus_levels[0], bus_levels[1]
            wf_l, wf_r = bus_waveforms.get(id(rack), (None, None))
            for mod in rack.get_modules():
                if isinstance(mod, DualVUMeterModule):
                    if mod.enabled:
                        mod.set_levels(bus_l, bus_r)
                        mod.decay_peaks()
                elif isinstance(mod, CompressorModule):
                    if mod.enabled:
                        if rack is self._rack_a:
                            gr_key = "compressor_gr_a"
                        elif rack is self._rack_b:
                            gr_key = "compressor_gr_b"
                        elif rack is self._rack_c:
                            gr_key = "compressor_gr_c"
                        elif rack is self._rack_d:
                            gr_key = "compressor_gr_d"
                        else:
                            gr_key = "compressor_gr_m"
                        gr = levels.get(gr_key, 0.0)
                        mod.update_meters(bus_l, bus_r, gr)
                elif isinstance(mod, GlueCompressorModule):
                    if mod.enabled:
                        if rack is self._rack_a:
                            gr_key = "glue_compressor_gr_a"
                        elif rack is self._rack_b:
                            gr_key = "glue_compressor_gr_b"
                        elif rack is self._rack_c:
                            gr_key = "glue_compressor_gr_c"
                        elif rack is self._rack_d:
                            gr_key = "glue_compressor_gr_d"
                        else:
                            gr_key = "glue_compressor_gr_m"
                        gr = levels.get(gr_key, 0.0)
                        mod.update_meters(bus_l, bus_r, gr)
                elif isinstance(mod, FETLimiterModule):
                    if mod.enabled:
                        if rack is self._rack_a:
                            gr_key = "fet_limiter_gr_a"
                        elif rack is self._rack_b:
                            gr_key = "fet_limiter_gr_b"
                        elif rack is self._rack_c:
                            gr_key = "fet_limiter_gr_c"
                        elif rack is self._rack_d:
                            gr_key = "fet_limiter_gr_d"
                        else:
                            gr_key = "fet_limiter_gr_m"
                        gr = levels.get(gr_key, 0.0)
                        mod.set_gr(gr)
                elif isinstance(mod, OscilloscopeModule):
                    if mod.enabled and wf_l is not None and wf_r is not None:
                        if bus_l > 0.001 or bus_r > 0.001:
                            mod.push_samples(wf_l, wf_r)
                        else:
                            mod.push_samples(
                                [0.0] * len(wf_l), [0.0] * len(wf_r))

    # --- Panel properties (app.py accesses these) ---
    @property
    def delay_panel(self):
        return self._delay_panel

    @property
    def tape_delay_panel(self):
        return self._tape_delay_panel

    @property
    def filter_a_panel(self):
        return self._filter_a_panel

    @property
    def filter_b_panel(self):
        return self._filter_b_panel

    @property
    def mod_panel(self):
        return self._mod_panel

    @property
    def compressor_module(self):
        return self._compressor_a

    @property
    def reel_module(self):
        return self._reel_module

    @property
    def cassette_module(self):
        return self._cassette_module

    @property
    def vhs_module(self):
        return self._vhs_module

    @property
    def vinyl_module(self):
        return self._vinyl_module

    @property
    def vinyl_b_module(self):
        return self._vinyl_b


class _LegacyPanelShim:
    """Thin adapter so app.py can call panel.set_state(state) on rack modules.
    Also proxies attribute access for MIDI mapping (e.g. panel._lp_chk)."""

    def __init__(self, module):
        self._module = module

    def set_state(self, state):
        self._module.set_state(state)

    def __getattr__(self, name):
        # Proxy any attribute access to the underlying module
        return getattr(self._module, name)


class _LegacyModPanelShim:
    """Adapter that fans out ModState to three separate rack modules.
    Also exposes legacy attribute names for MIDI mapping."""

    # Map old panel attribute names → (module_attr, rack_module_attr)
    _ATTR_MAP = {
        "_phaser_chk": ("_phaser", "power_widget"),
        "_phaser_rate": ("_phaser", "_rate"),
        "_phaser_depth": ("_phaser", "_depth"),
        "_phaser_fb": ("_phaser", "_fb"),
        "_chorus_chk": ("_chorus", "power_widget"),
        "_chorus_rate": ("_chorus", "_rate"),
        "_chorus_depth": ("_chorus", "_depth"),
        "_chorus_mix": ("_chorus", "_mix"),
        "_flanger_chk": ("_flanger", "power_widget"),
        "_flanger_rate": ("_flanger", "_rate"),
        "_flanger_depth": ("_flanger", "_depth"),
        "_flanger_fb": ("_flanger", "_fb"),
        "_flanger_mix": ("_flanger", "_mix"),
    }

    def __init__(self, phaser, chorus, flanger):
        self._phaser = phaser
        self._chorus = chorus
        self._flanger = flanger

    def set_state(self, state):
        self._phaser.set_state(state)
        self._chorus.set_state(state)
        self._flanger.set_state(state)

    def __getattr__(self, name):
        if name in self._ATTR_MAP:
            mod_attr, widget_attr = self._ATTR_MAP[name]
            mod = object.__getattribute__(self, mod_attr)
            return getattr(mod, widget_attr)
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
