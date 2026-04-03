"""Effects view — delay, filter, modulation, and recording tabs in mlrVp.

Matches mlrV2's tabber1/tabber2 layout with delay, recording, and
per-channel filter controls.  Styled with BG_PANEL background, compact
labels in TEXT_SECONDARY, ACCENT_GOLD active states.
"""

import math

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QFrame, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QGroupBox, QGridLayout, QSizePolicy, QScrollArea
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from mlrvp.config import DelayState, RecorderState, FilterState, FilterBState, TapeDelayState, ModState
from mlrvp.gui.styles import (
    ACCENT_GOLD, BG_BLACK, BG_PANEL, SEPARATOR, TEXT_SECONDARY, TEXT_PRIMARY,
    ACCENT_BLUE, FX1_COLOR, FX2_COLOR
)
from mlrvp.gui.widgets.fader import RotaryKnob, HorizontalFader


# Delay sync division names matching mlrV2
SYNC_DIVISION_NAMES = [
    "1/32", "1/16t", "1/16", "1/8t", "1/8",
    "1/4t", "3/16", "1/4", "1/3", "3/8",
    "1/2", "3/4", "1 bar",
]


class DelayPanel(QFrame):
    """Delay effect controls matching mlrV2 aux delay."""

    param_changed = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Header: enable + title
        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        self._enable_chk = QCheckBox()
        self._enable_chk.setChecked(True)
        self._enable_chk.toggled.connect(lambda v: self.param_changed.emit("enabled", v))
        hdr.addWidget(self._enable_chk)
        title = QLabel("DIGITAL DELAY")
        title.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {TEXT_SECONDARY};")
        hdr.addWidget(title)
        hdr.addStretch()
        layout.addLayout(hdr)

        # Time controls
        time_group = QGroupBox("Time")
        time_layout = QGridLayout(time_group)
        time_layout.setContentsMargins(4, 10, 4, 2)
        time_layout.setSpacing(3)

        # Sync / ms mode toggle
        self._time_mode = QComboBox()
        self._time_mode.addItems(["Sync", "Manual (ms)"])
        self._time_mode.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("time_mode", v)
        )
        time_layout.addWidget(QLabel("Mode:"), 0, 0)
        time_layout.addWidget(self._time_mode, 0, 1)

        # Sync division
        self._sync_combo = QComboBox()
        self._sync_combo.addItems(SYNC_DIVISION_NAMES)
        self._sync_combo.setCurrentIndex(3)
        self._sync_combo.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("sync_time", v)
        )
        time_layout.addWidget(QLabel("Sync:"), 1, 0)
        time_layout.addWidget(self._sync_combo, 1, 1)

        # Manual ms time
        self._ms_spin = QDoubleSpinBox()
        self._ms_spin.setRange(1.0, 5000.0)
        self._ms_spin.setValue(250.0)
        self._ms_spin.setSuffix(" ms")
        self._ms_spin.valueChanged.connect(
            lambda v: self.param_changed.emit("ms_time", v)
        )
        time_layout.addWidget(QLabel("Time:"), 2, 0)
        time_layout.addWidget(self._ms_spin, 2, 1)

        layout.addWidget(time_group)

        # Feedback controls
        fb_group = QGroupBox("Feedback Envelope")
        fb_layout = QGridLayout(fb_group)
        fb_layout.setContentsMargins(4, 10, 4, 2)
        fb_layout.setSpacing(3)

        # Attack
        self._fb_attack = RotaryKnob()
        self._fb_attack.set_value(0.67)
        self._fb_attack.value_changed.connect(
            lambda v: self.param_changed.emit("fb_attack", v * 100.0)
        )
        fb_layout.addWidget(QLabel("Attack"), 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        fb_layout.addWidget(self._fb_attack, 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)

        # Release
        self._fb_release = RotaryKnob()
        self._fb_release.set_value(0.67)
        self._fb_release.value_changed.connect(
            lambda v: self.param_changed.emit("fb_release", v * 100.0)
        )
        fb_layout.addWidget(QLabel("Release"), 0, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        fb_layout.addWidget(self._fb_release, 1, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        # Range low
        self._fb_range_low = RotaryKnob()
        self._fb_range_low.set_value(0.42)
        self._fb_range_low.value_changed.connect(
            lambda v: self.param_changed.emit("fb_range_low", v * 100.0)
        )
        fb_layout.addWidget(QLabel("Rng Lo"), 0, 2, alignment=Qt.AlignmentFlag.AlignCenter)
        fb_layout.addWidget(self._fb_range_low, 1, 2, alignment=Qt.AlignmentFlag.AlignCenter)

        # Range high
        self._fb_range_high = RotaryKnob()
        self._fb_range_high.set_value(0.90)
        self._fb_range_high.value_changed.connect(
            lambda v: self.param_changed.emit("fb_range_high", v * 100.0)
        )
        fb_layout.addWidget(QLabel("Rng Hi"), 0, 3, alignment=Qt.AlignmentFlag.AlignCenter)
        fb_layout.addWidget(self._fb_range_high, 1, 3, alignment=Qt.AlignmentFlag.AlignCenter)

        # Gain knob
        self._fb_gain = RotaryKnob()
        self._fb_gain.set_value(0.7)
        self._fb_gain.value_changed.connect(
            lambda v: self.param_changed.emit("gain", v)
        )
        fb_layout.addWidget(QLabel("Gain"), 0, 4, alignment=Qt.AlignmentFlag.AlignCenter)
        fb_layout.addWidget(self._fb_gain, 1, 4, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(fb_group)

        # Filter controls
        flt_group = QGroupBox("Feedback Filter")
        flt_layout = QGridLayout(flt_group)
        flt_layout.setContentsMargins(4, 10, 4, 2)
        flt_layout.setSpacing(3)

        self._filter_lp = QCheckBox("LP")
        self._filter_lp.setChecked(True)
        self._filter_lp.toggled.connect(
            lambda v: self.param_changed.emit("filter_lp", v)
        )
        flt_layout.addWidget(self._filter_lp, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)

        self._lp_freq = RotaryKnob()
        self._lp_freq.set_value(0.7)
        self._lp_freq.value_changed.connect(
            lambda v: self.param_changed.emit("filter_freq_lp", v * 10000.0 + 100.0)
        )
        flt_layout.addWidget(QLabel("LP Freq"), 0, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        flt_layout.addWidget(self._lp_freq, 1, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        self._filter_hp = QCheckBox("HP")
        self._filter_hp.setChecked(True)
        self._filter_hp.toggled.connect(
            lambda v: self.param_changed.emit("filter_hp", v)
        )
        flt_layout.addWidget(self._filter_hp, 0, 2, alignment=Qt.AlignmentFlag.AlignCenter)

        self._hp_freq = RotaryKnob()
        self._hp_freq.set_value(0.03)
        self._hp_freq.value_changed.connect(
            lambda v: self.param_changed.emit("filter_freq_hp", v * 5000.0 + 20.0)
        )
        flt_layout.addWidget(QLabel("HP Freq"), 0, 3, alignment=Qt.AlignmentFlag.AlignCenter)
        flt_layout.addWidget(self._hp_freq, 1, 3, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(flt_group)

    def set_state(self, state: DelayState):
        self._enable_chk.blockSignals(True)
        self._enable_chk.setChecked(state.enabled)
        self._enable_chk.blockSignals(False)

        self._time_mode.blockSignals(True)
        self._time_mode.setCurrentIndex(state.time_mode)
        self._time_mode.blockSignals(False)

        self._sync_combo.blockSignals(True)
        self._sync_combo.setCurrentIndex(state.sync_time)
        self._sync_combo.blockSignals(False)

        self._ms_spin.blockSignals(True)
        self._ms_spin.setValue(state.ms_time)
        self._ms_spin.blockSignals(False)

        self._fb_attack.set_value(state.fb_attack / 100.0)
        self._fb_release.set_value(state.fb_release / 100.0)
        self._fb_range_low.set_value(state.fb_range_low / 100.0)
        self._fb_range_high.set_value(state.fb_range_high / 100.0)
        self._fb_gain.set_value(state.gain)

        self._filter_lp.blockSignals(True)
        self._filter_lp.setChecked(state.filter_lp)
        self._filter_lp.blockSignals(False)

        self._filter_hp.blockSignals(True)
        self._filter_hp.setChecked(state.filter_hp)
        self._filter_hp.blockSignals(False)


class TapeDelayPanel(QFrame):
    """Vintage tape delay effect controls."""

    param_changed = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Header: enable + title
        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        self._enable_chk = QCheckBox()
        self._enable_chk.setChecked(True)
        self._enable_chk.toggled.connect(lambda v: self.param_changed.emit("enabled", v))
        hdr.addWidget(self._enable_chk)
        title = QLabel("TAPE DELAY")
        title.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {TEXT_SECONDARY};")
        hdr.addWidget(title)
        hdr.addStretch()
        layout.addLayout(hdr)

        # Time controls
        time_group = QGroupBox("Time")
        time_layout = QGridLayout(time_group)
        time_layout.setContentsMargins(4, 10, 4, 2)
        time_layout.setSpacing(3)

        self._time_mode = QComboBox()
        self._time_mode.addItems(["Sync", "Manual (ms)"])
        self._time_mode.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("time_mode", v))
        time_layout.addWidget(QLabel("Mode:"), 0, 0)
        time_layout.addWidget(self._time_mode, 0, 1)

        self._sync_combo = QComboBox()
        self._sync_combo.addItems(SYNC_DIVISION_NAMES)
        self._sync_combo.setCurrentIndex(5)
        self._sync_combo.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("sync_time", v))
        time_layout.addWidget(QLabel("Sync:"), 1, 0)
        time_layout.addWidget(self._sync_combo, 1, 1)

        self._ms_spin = QDoubleSpinBox()
        self._ms_spin.setRange(1.0, 5000.0)
        self._ms_spin.setValue(375.0)
        self._ms_spin.setSuffix(" ms")
        self._ms_spin.valueChanged.connect(
            lambda v: self.param_changed.emit("ms_time", v))
        time_layout.addWidget(QLabel("Time:"), 2, 0)
        time_layout.addWidget(self._ms_spin, 2, 1)

        layout.addWidget(time_group)

        # Tape character knobs
        tape_group = QGroupBox("Tape Character")
        tape_layout = QGridLayout(tape_group)
        tape_layout.setContentsMargins(4, 10, 4, 2)
        tape_layout.setSpacing(3)

        self._feedback = RotaryKnob()
        self._feedback.set_value(0.45)
        self._feedback.value_changed.connect(
            lambda v: self.param_changed.emit("feedback", v))
        tape_layout.addWidget(QLabel("Feedback"), 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        tape_layout.addWidget(self._feedback, 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)

        self._wow_flutter = RotaryKnob()
        self._wow_flutter.set_value(0.3)
        self._wow_flutter.value_changed.connect(
            lambda v: self.param_changed.emit("wow_flutter", v))
        tape_layout.addWidget(QLabel("Wow/Fltr"), 0, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        tape_layout.addWidget(self._wow_flutter, 1, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        self._saturation = RotaryKnob()
        self._saturation.set_value(0.4)
        self._saturation.value_changed.connect(
            lambda v: self.param_changed.emit("saturation", v))
        tape_layout.addWidget(QLabel("Saturate"), 0, 2, alignment=Qt.AlignmentFlag.AlignCenter)
        tape_layout.addWidget(self._saturation, 1, 2, alignment=Qt.AlignmentFlag.AlignCenter)

        self._tone = RotaryKnob()
        self._tone.set_value(0.6)
        self._tone.value_changed.connect(
            lambda v: self.param_changed.emit("tone", v))
        tape_layout.addWidget(QLabel("Tone"), 0, 3, alignment=Qt.AlignmentFlag.AlignCenter)
        tape_layout.addWidget(self._tone, 1, 3, alignment=Qt.AlignmentFlag.AlignCenter)

        self._gain = RotaryKnob()
        self._gain.set_value(0.6)
        self._gain.value_changed.connect(
            lambda v: self.param_changed.emit("gain", v))
        tape_layout.addWidget(QLabel("Gain"), 0, 4, alignment=Qt.AlignmentFlag.AlignCenter)
        tape_layout.addWidget(self._gain, 1, 4, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(tape_group)

    def set_state(self, state: TapeDelayState):
        self._enable_chk.blockSignals(True)
        self._enable_chk.setChecked(state.enabled)
        self._enable_chk.blockSignals(False)

        self._time_mode.blockSignals(True)
        self._time_mode.setCurrentIndex(state.time_mode)
        self._time_mode.blockSignals(False)

        self._sync_combo.blockSignals(True)
        self._sync_combo.setCurrentIndex(state.sync_time)
        self._sync_combo.blockSignals(False)

        self._ms_spin.blockSignals(True)
        self._ms_spin.setValue(state.ms_time)
        self._ms_spin.blockSignals(False)

        self._feedback.set_value(state.feedback)
        self._wow_flutter.set_value(state.wow_flutter)
        self._saturation.set_value(state.saturation)
        self._tone.set_value(state.tone)
        self._gain.set_value(state.gain)


class RecorderPanel(QFrame):
    """Live audio recording controls."""

    param_changed = Signal(str, object)
    record_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        title = QLabel("RECORDER")
        title.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {TEXT_SECONDARY};")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(6)

        # Target buffer/channel
        self._buffer_combo = QComboBox()
        self._buffer_combo.addItems([f"Channel {i+1}" for i in range(16)])
        self._buffer_combo.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("buffer_idx", v)
        )
        grid.addWidget(QLabel("Target:"), 0, 0)
        grid.addWidget(self._buffer_combo, 0, 1)

        # Input device
        self._input_combo = QComboBox()
        self._input_combo.addItem("Default Input")
        self._input_combo.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("input_device", v)
        )
        grid.addWidget(QLabel("Input:"), 1, 0)
        grid.addWidget(self._input_combo, 1, 1)

        # Input channel
        self._input_ch = QSpinBox()
        self._input_ch.setRange(0, 31)
        self._input_ch.valueChanged.connect(
            lambda v: self.param_changed.emit("input_channel", v)
        )
        grid.addWidget(QLabel("In Ch:"), 2, 0)
        grid.addWidget(self._input_ch, 2, 1)

        # Length
        self._length_spin = QSpinBox()
        self._length_spin.setRange(1, 128)
        self._length_spin.setValue(16)
        self._length_spin.setSuffix(" bars")
        self._length_spin.valueChanged.connect(
            lambda v: self.param_changed.emit("length_bars", v)
        )
        grid.addWidget(QLabel("Length:"), 3, 0)
        grid.addWidget(self._length_spin, 3, 1)

        # Loop
        self._loop_chk = QCheckBox("Loop recording")
        self._loop_chk.toggled.connect(
            lambda v: self.param_changed.emit("loop", v)
        )
        grid.addWidget(self._loop_chk, 4, 0, 1, 2)

        # Thru monitor
        self._thru_chk = QCheckBox("Input thru")
        self._thru_chk.toggled.connect(
            lambda v: self.param_changed.emit("thru", v)
        )
        grid.addWidget(self._thru_chk, 5, 0, 1, 2)

        layout.addLayout(grid)

        # Record button
        self._rec_btn = QPushButton("ARM")
        self._rec_btn.setCheckable(True)
        self._rec_btn.setFixedHeight(30)
        self._rec_btn.setStyleSheet(f"""
            QPushButton {{ background: {BG_BLACK}; border: 1px solid {SEPARATOR}; border-radius: 0px;
                          font-size: 11px; font-weight: bold; color: {TEXT_SECONDARY}; }}
            QPushButton:checked {{ background: #cc3333; border-color: #cc3333; color: white; }}
        """)
        self._rec_btn.toggled.connect(self.record_toggled.emit)
        layout.addWidget(self._rec_btn)

        layout.addStretch()

    def set_state(self, state: RecorderState):
        self._buffer_combo.blockSignals(True)
        self._buffer_combo.setCurrentIndex(state.buffer_idx)
        self._buffer_combo.blockSignals(False)

        self._length_spin.blockSignals(True)
        self._length_spin.setValue(state.length_bars)
        self._length_spin.blockSignals(False)

        self._loop_chk.blockSignals(True)
        self._loop_chk.setChecked(state.loop)
        self._loop_chk.blockSignals(False)

        self._thru_chk.blockSignals(True)
        self._thru_chk.setChecked(state.thru)
        self._thru_chk.blockSignals(False)

    def set_input_devices(self, devices: list[str]):
        self._input_combo.blockSignals(True)
        self._input_combo.clear()
        self._input_combo.addItems(devices)
        self._input_combo.blockSignals(False)


class FilterCurveWidget(QWidget):
    """Visual display of the current filter frequency response curve."""

    def __init__(self, accent_color: str = ACCENT_GOLD, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(40)
        self.setMaximumHeight(60)
        self._accent = accent_color
        self._lp_freq = 8000.0
        self._hp_freq = 200.0
        self._lp_q = 0.5
        self._hp_q = 0.5
        self._lp_enabled = True
        self._hp_enabled = True

    def set_params(self, lp_freq, hp_freq, lp_q, hp_q, lp_enabled, hp_enabled):
        self._lp_freq = lp_freq
        self._hp_freq = hp_freq
        self._lp_q = lp_q
        self._hp_q = hp_q
        self._lp_enabled = lp_enabled
        self._hp_enabled = hp_enabled
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background
        p.fillRect(self.rect(), QColor(BG_BLACK))

        # The 0 dB line sits at 60% of the height (leaves room for Q peaks above)
        zero_y = h * 0.6

        # Grid lines
        p.setPen(QPen(QColor(SEPARATOR), 1, Qt.PenStyle.DotLine))
        for frac in (0.2, 0.4, 0.6, 0.8):
            y = int(h * frac)
            p.drawLine(0, y, w, y)
        # 0 dB reference line (slightly brighter)
        p.setPen(QPen(QColor(TEXT_SECONDARY), 1, Qt.PenStyle.DashLine))
        p.drawLine(0, int(zero_y), w, int(zero_y))

        # Frequency markers (log scale: 20Hz - 20kHz)
        p.setPen(QPen(QColor(SEPARATOR), 1, Qt.PenStyle.DotLine))
        freqs = [100, 1000, 10000]
        for f in freqs:
            x = int(self._freq_to_x(f, w))
            p.drawLine(x, 0, x, h)

        # Frequency labels
        p.setPen(QColor(TEXT_SECONDARY))
        font = p.font()
        font.setPixelSize(8)
        p.setFont(font)
        for f in freqs:
            x = int(self._freq_to_x(f, w))
            label = f"{f//1000}k" if f >= 1000 else str(f)
            p.drawText(x + 2, h - 2, label)

        # Draw filter curve — gain > 1.0 shows peaks above the 0 dB line
        # Map: gain=0 → bottom, gain=1 → zero_y, gain=max_peak → top
        max_peak = 3.0  # max displayable gain (Q peaks)
        path = QPainterPath()
        points = []
        for px in range(w):
            freq = self._x_to_freq(px, w)
            gain = self._compute_gain(freq)
            # Piecewise linear: gain 0→1 maps to h→zero_y, gain 1→max_peak maps to zero_y→0
            if gain <= 1.0:
                y = h - gain * (h - zero_y)
            else:
                y = zero_y - (gain - 1.0) / (max_peak - 1.0) * zero_y
            y = max(2.0, min(float(h - 2), y))
            points.append((px, y))

        if points:
            path.moveTo(points[0][0], points[0][1])
            for px, py in points[1:]:
                path.lineTo(px, py)

        # Fill below curve with lighter accent color
        fill_path = QPainterPath(path)
        if points:
            fill_path.lineTo(w, h)
            fill_path.lineTo(0, h)
            fill_path.closeSubpath()
            fill_color = QColor(self._accent)
            fill_color.setAlpha(40)
            p.fillPath(fill_path, fill_color)

        # Draw curve line
        p.setPen(QPen(QColor(self._accent), 2))
        p.drawPath(path)

        p.end()

    def _freq_to_x(self, freq, width):
        """Map frequency (20-20000 Hz) to x position (log scale)."""
        log_min = math.log10(20)
        log_max = math.log10(20000)
        if freq <= 0:
            freq = 20
        return (math.log10(freq) - log_min) / (log_max - log_min) * width

    def _x_to_freq(self, x, width):
        """Map x position to frequency (log scale)."""
        log_min = math.log10(20)
        log_max = math.log10(20000)
        return 10 ** (log_min + (x / max(width, 1)) * (log_max - log_min))

    def _compute_gain(self, freq):
        """Compute combined filter response at a given frequency.

        Returns values > 1.0 for resonance peaks (displayed above the 0 dB line).
        """
        gain = 1.0

        if self._lp_enabled and self._lp_freq > 0:
            q = 0.5 + self._lp_q * 4.0  # Q from 0.5 to 4.5
            ratio = freq / self._lp_freq
            lp_resp = 1.0 / math.sqrt(1.0 + ratio ** (2 * 2))
            # Resonance peak near cutoff
            if ratio > 0.5 and ratio < 2.0:
                peak = 1.0 + (q - 0.7) * 0.3 * math.exp(-((ratio - 1.0) ** 2) * 8)
                lp_resp *= peak
            gain *= lp_resp

        if self._hp_enabled and self._hp_freq > 0:
            q = 0.5 + self._hp_q * 4.0  # Q from 0.5 to 4.5
            ratio = freq / self._hp_freq
            if ratio > 0:
                hp_resp = 1.0 / math.sqrt(1.0 + (1.0 / ratio) ** (2 * 2))
                if ratio > 0.5 and ratio < 2.0:
                    peak = 1.0 + (q - 0.7) * 0.3 * math.exp(-((ratio - 1.0) ** 2) * 8)
                    hp_resp *= peak
                gain *= hp_resp
            else:
                gain = 0.0

        return gain  # allow > 1.0 for Q peaks


class FilterPanel(QFrame):
    """Filter A controls: shapeable LP/HP with separate Q knobs + enable toggle."""

    param_changed = Signal(str, object)

    def __init__(self, label: str = "FILTER A", accent_color: str = ACCENT_GOLD, parent=None):
        super().__init__(parent)
        self._accent = accent_color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Header row: enable checkbox + title
        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        self._enable_chk = QCheckBox()
        self._enable_chk.setChecked(True)
        self._enable_chk.toggled.connect(lambda v: self.param_changed.emit("enabled", v))
        hdr.addWidget(self._enable_chk)
        title = QLabel(label)
        title.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {accent_color};")
        hdr.addWidget(title)
        hdr.addStretch()
        layout.addLayout(hdr)

        self._curve = FilterCurveWidget(accent_color)
        layout.addWidget(self._curve)

        ctrl = QGridLayout()
        ctrl.setSpacing(4)

        # LP controls
        self._lp_chk = QCheckBox("LP")
        self._lp_chk.setChecked(True)
        self._lp_chk.toggled.connect(self._on_param_changed)
        self._lp_chk.toggled.connect(lambda v: self.param_changed.emit("lp_enabled", v))
        ctrl.addWidget(self._lp_chk, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)

        self._lp_freq = RotaryKnob()
        self._lp_freq.set_value(0.8)
        self._lp_freq.value_changed.connect(self._on_param_changed)
        self._lp_freq.value_changed.connect(
            lambda v: self.param_changed.emit("lp_freq", v * 19800.0 + 200.0))
        ctrl.addWidget(self._lp_freq, 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)

        lp_q_lbl = QLabel("LP Q")
        lp_q_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        ctrl.addWidget(lp_q_lbl, 0, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        self._lp_q = RotaryKnob()
        self._lp_q.set_value(0.5)
        self._lp_q.value_changed.connect(self._on_param_changed)
        self._lp_q.value_changed.connect(lambda v: self.param_changed.emit("lp_q", v))
        ctrl.addWidget(self._lp_q, 1, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        # HP controls
        self._hp_chk = QCheckBox("HP")
        self._hp_chk.setChecked(True)
        self._hp_chk.toggled.connect(self._on_param_changed)
        self._hp_chk.toggled.connect(lambda v: self.param_changed.emit("hp_enabled", v))
        ctrl.addWidget(self._hp_chk, 0, 2, alignment=Qt.AlignmentFlag.AlignCenter)

        self._hp_freq = RotaryKnob()
        self._hp_freq.set_value(0.03)
        self._hp_freq.value_changed.connect(self._on_param_changed)
        self._hp_freq.value_changed.connect(
            lambda v: self.param_changed.emit("hp_freq", v * 5000.0 + 20.0))
        ctrl.addWidget(self._hp_freq, 1, 2, alignment=Qt.AlignmentFlag.AlignCenter)

        hp_q_lbl = QLabel("HP Q")
        hp_q_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        ctrl.addWidget(hp_q_lbl, 0, 3, alignment=Qt.AlignmentFlag.AlignCenter)

        self._hp_q = RotaryKnob()
        self._hp_q.set_value(0.5)
        self._hp_q.value_changed.connect(self._on_param_changed)
        self._hp_q.value_changed.connect(lambda v: self.param_changed.emit("hp_q", v))
        ctrl.addWidget(self._hp_q, 1, 3, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addLayout(ctrl)

    def _on_param_changed(self, _=None):
        """Update the curve display whenever a parameter changes."""
        lp_freq = self._lp_freq.value * 19800.0 + 200.0
        hp_freq = self._hp_freq.value * 5000.0 + 20.0
        lp_q = self._lp_q.value
        hp_q = self._hp_q.value
        self._curve.set_params(lp_freq, hp_freq, lp_q, hp_q,
                               self._lp_chk.isChecked(), self._hp_chk.isChecked())

    def set_state(self, state: FilterState):
        self._enable_chk.blockSignals(True)
        self._enable_chk.setChecked(state.enabled)
        self._enable_chk.blockSignals(False)

        self._lp_chk.blockSignals(True)
        self._lp_chk.setChecked(state.lp_enabled)
        self._lp_chk.blockSignals(False)

        self._hp_chk.blockSignals(True)
        self._hp_chk.setChecked(state.hp_enabled)
        self._hp_chk.blockSignals(False)

        self._lp_freq.set_value((state.lp_freq - 200.0) / 19800.0)
        self._hp_freq.set_value((state.hp_freq - 20.0) / 5000.0)
        self._lp_q.set_value(state.lp_q)
        self._hp_q.set_value(state.hp_q)
        self._on_param_changed()


class MoogCurveWidget(QWidget):
    """Visual display of a Moog-style ladder filter response."""

    def __init__(self, accent_color: str = FX2_COLOR, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(40)
        self.setMaximumHeight(60)
        self._accent = accent_color
        self._cutoff = 8000.0
        self._resonance = 0.3

    def set_params(self, cutoff, resonance):
        self._cutoff = cutoff
        self._resonance = resonance
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        p.fillRect(self.rect(), QColor(BG_BLACK))

        # 0 dB line at 60% height
        zero_y = h * 0.6

        # Grid
        p.setPen(QPen(QColor(SEPARATOR), 1, Qt.PenStyle.DotLine))
        for frac in (0.2, 0.4, 0.6, 0.8):
            p.drawLine(0, int(h * frac), w, int(h * frac))
        p.setPen(QPen(QColor(TEXT_SECONDARY), 1, Qt.PenStyle.DashLine))
        p.drawLine(0, int(zero_y), w, int(zero_y))

        # Freq markers
        p.setPen(QPen(QColor(SEPARATOR), 1, Qt.PenStyle.DotLine))
        freqs = [100, 1000, 10000]
        for f in freqs:
            x = int(self._freq_to_x(f, w))
            p.drawLine(x, 0, x, h)

        p.setPen(QColor(TEXT_SECONDARY))
        font = p.font()
        font.setPixelSize(8)
        p.setFont(font)
        for f in freqs:
            x = int(self._freq_to_x(f, w))
            label = f"{f//1000}k" if f >= 1000 else str(f)
            p.drawText(x + 2, h - 2, label)

        # Moog 4-pole (24 dB/oct) curve with resonance peak
        max_peak = 3.0
        path = QPainterPath()
        points = []
        for px in range(w):
            freq = self._x_to_freq(px, w)
            gain = self._compute_moog_gain(freq)
            # Piecewise linear: gain 0→1 maps to h→zero_y, gain 1→max_peak maps to zero_y→0
            if gain <= 1.0:
                y = h - gain * (h - zero_y)
            else:
                y = zero_y - (gain - 1.0) / (max_peak - 1.0) * zero_y
            y = max(2.0, min(float(h - 2), y))
            points.append((px, y))

        if points:
            path.moveTo(points[0][0], points[0][1])
            for px, py in points[1:]:
                path.lineTo(px, py)

        fill_path = QPainterPath(path)
        if points:
            fill_path.lineTo(w, h)
            fill_path.lineTo(0, h)
            fill_path.closeSubpath()
            fill_color = QColor(self._accent)
            fill_color.setAlpha(40)
            p.fillPath(fill_path, fill_color)

        p.setPen(QPen(QColor(self._accent), 2))
        p.drawPath(path)
        p.end()

    def _freq_to_x(self, freq, width):
        log_min, log_max = math.log10(20), math.log10(20000)
        if freq <= 0:
            freq = 20
        return (math.log10(freq) - log_min) / (log_max - log_min) * width

    def _x_to_freq(self, x, width):
        log_min, log_max = math.log10(20), math.log10(20000)
        return 10 ** (log_min + (x / max(width, 1)) * (log_max - log_min))

    def _compute_moog_gain(self, freq):
        """4-pole LP with resonance peak at cutoff."""
        if self._cutoff <= 0:
            return 0.0
        ratio = freq / self._cutoff
        # 4-pole (24 dB/oct) rolloff
        base = 1.0 / math.sqrt(1.0 + ratio ** 8)
        # Resonance peak near cutoff
        q = 0.5 + self._resonance * 8.0
        if 0.3 < ratio < 3.0:
            peak = 1.0 + (q - 0.5) * 0.4 * math.exp(-((ratio - 1.0) ** 2) * 6)
            base *= peak
        return base


class MoogFilterPanel(QFrame):
    """Filter B controls: Moog-style ladder sweep filter + enable toggle."""

    param_changed = Signal(str, object)

    def __init__(self, accent_color: str = FX2_COLOR, parent=None):
        super().__init__(parent)
        self._accent = accent_color

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Header: enable + title
        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        self._enable_chk = QCheckBox()
        self._enable_chk.setChecked(True)
        self._enable_chk.toggled.connect(lambda v: self.param_changed.emit("enabled", v))
        hdr.addWidget(self._enable_chk)
        title = QLabel("FILTER B — Moog Ladder")
        title.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {accent_color};")
        hdr.addWidget(title)
        hdr.addStretch()
        layout.addLayout(hdr)

        self._curve = MoogCurveWidget(accent_color)
        layout.addWidget(self._curve)

        ctrl = QGridLayout()
        ctrl.setSpacing(4)

        cut_lbl = QLabel("Cutoff")
        cut_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        ctrl.addWidget(cut_lbl, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)

        self._cutoff = RotaryKnob()
        self._cutoff.set_value(0.7)
        self._cutoff.value_changed.connect(self._on_param_changed)
        self._cutoff.value_changed.connect(
            lambda v: self.param_changed.emit("cutoff", v * 17960.0 + 40.0))
        ctrl.addWidget(self._cutoff, 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)

        res_lbl = QLabel("Resonance")
        res_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        ctrl.addWidget(res_lbl, 0, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        self._resonance = RotaryKnob()
        self._resonance.set_value(0.3)
        self._resonance.value_changed.connect(self._on_param_changed)
        self._resonance.value_changed.connect(
            lambda v: self.param_changed.emit("resonance", v))
        ctrl.addWidget(self._resonance, 1, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        drv_lbl = QLabel("Drive")
        drv_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        ctrl.addWidget(drv_lbl, 0, 2, alignment=Qt.AlignmentFlag.AlignCenter)

        self._drive = RotaryKnob()
        self._drive.set_value(0.0)
        self._drive.value_changed.connect(
            lambda v: self.param_changed.emit("drive", v))
        ctrl.addWidget(self._drive, 1, 2, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addLayout(ctrl)

    def _on_param_changed(self, _=None):
        cutoff = self._cutoff.value * 17960.0 + 40.0
        res = self._resonance.value
        self._curve.set_params(cutoff, res)

    def set_state(self, state: FilterBState):
        self._enable_chk.blockSignals(True)
        self._enable_chk.setChecked(state.enabled)
        self._enable_chk.blockSignals(False)

        self._cutoff.set_value((state.cutoff - 40.0) / 17960.0)
        self._resonance.set_value(state.resonance)
        self._drive.set_value(state.drive)
        self._on_param_changed()


class ModPanel(QFrame):
    """Modulation effects: Phaser, Chorus, Flanger."""

    param_changed = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        title = QLabel("MODULATION")
        title.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {TEXT_SECONDARY};")
        layout.addWidget(title)

        # --- Phaser ---
        phaser_group = QGroupBox("Phaser")
        phaser_layout = QGridLayout(phaser_group)
        phaser_layout.setContentsMargins(4, 10, 4, 2)
        phaser_layout.setSpacing(3)

        self._phaser_chk = QCheckBox("Enable")
        self._phaser_chk.toggled.connect(
            lambda v: self.param_changed.emit("phaser_enabled", v))
        phaser_layout.addWidget(self._phaser_chk, 0, 0, 1, 2)

        self._phaser_rate = RotaryKnob()
        self._phaser_rate.set_value(0.25)
        self._phaser_rate.value_changed.connect(
            lambda v: self.param_changed.emit("phaser_rate", v * 10.0))
        phaser_layout.addWidget(QLabel("Rate"), 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        phaser_layout.addWidget(self._phaser_rate, 2, 0, alignment=Qt.AlignmentFlag.AlignCenter)

        self._phaser_depth = RotaryKnob()
        self._phaser_depth.set_value(0.5)
        self._phaser_depth.value_changed.connect(
            lambda v: self.param_changed.emit("phaser_depth", v))
        phaser_layout.addWidget(QLabel("Depth"), 1, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        phaser_layout.addWidget(self._phaser_depth, 2, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        self._phaser_fb = RotaryKnob()
        self._phaser_fb.set_value(0.3)
        self._phaser_fb.value_changed.connect(
            lambda v: self.param_changed.emit("phaser_feedback", v))
        phaser_layout.addWidget(QLabel("Feedback"), 1, 2, alignment=Qt.AlignmentFlag.AlignCenter)
        phaser_layout.addWidget(self._phaser_fb, 2, 2, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(phaser_group)

        # --- Chorus ---
        chorus_group = QGroupBox("Chorus")
        chorus_layout = QGridLayout(chorus_group)
        chorus_layout.setContentsMargins(4, 10, 4, 2)
        chorus_layout.setSpacing(3)

        self._chorus_chk = QCheckBox("Enable")
        self._chorus_chk.toggled.connect(
            lambda v: self.param_changed.emit("chorus_enabled", v))
        chorus_layout.addWidget(self._chorus_chk, 0, 0, 1, 2)

        self._chorus_rate = RotaryKnob()
        self._chorus_rate.set_value(0.1)
        self._chorus_rate.value_changed.connect(
            lambda v: self.param_changed.emit("chorus_rate", v * 5.0))
        chorus_layout.addWidget(QLabel("Rate"), 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        chorus_layout.addWidget(self._chorus_rate, 2, 0, alignment=Qt.AlignmentFlag.AlignCenter)

        self._chorus_depth = RotaryKnob()
        self._chorus_depth.set_value(0.5)
        self._chorus_depth.value_changed.connect(
            lambda v: self.param_changed.emit("chorus_depth", v))
        chorus_layout.addWidget(QLabel("Depth"), 1, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        chorus_layout.addWidget(self._chorus_depth, 2, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        self._chorus_mix = RotaryKnob()
        self._chorus_mix.set_value(0.5)
        self._chorus_mix.value_changed.connect(
            lambda v: self.param_changed.emit("chorus_mix", v))
        chorus_layout.addWidget(QLabel("Mix"), 1, 2, alignment=Qt.AlignmentFlag.AlignCenter)
        chorus_layout.addWidget(self._chorus_mix, 2, 2, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(chorus_group)

        # --- Flanger ---
        flanger_group = QGroupBox("Flanger")
        flanger_layout = QGridLayout(flanger_group)
        flanger_layout.setContentsMargins(4, 10, 4, 2)
        flanger_layout.setSpacing(3)

        self._flanger_chk = QCheckBox("Enable")
        self._flanger_chk.toggled.connect(
            lambda v: self.param_changed.emit("flanger_enabled", v))
        flanger_layout.addWidget(self._flanger_chk, 0, 0, 1, 2)

        self._flanger_rate = RotaryKnob()
        self._flanger_rate.set_value(0.15)
        self._flanger_rate.value_changed.connect(
            lambda v: self.param_changed.emit("flanger_rate", v * 5.0))
        flanger_layout.addWidget(QLabel("Rate"), 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        flanger_layout.addWidget(self._flanger_rate, 2, 0, alignment=Qt.AlignmentFlag.AlignCenter)

        self._flanger_depth = RotaryKnob()
        self._flanger_depth.set_value(0.5)
        self._flanger_depth.value_changed.connect(
            lambda v: self.param_changed.emit("flanger_depth", v))
        flanger_layout.addWidget(QLabel("Depth"), 1, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        flanger_layout.addWidget(self._flanger_depth, 2, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        self._flanger_fb = RotaryKnob()
        self._flanger_fb.set_value(0.3)
        self._flanger_fb.value_changed.connect(
            lambda v: self.param_changed.emit("flanger_feedback", v))
        flanger_layout.addWidget(QLabel("Feedback"), 1, 2, alignment=Qt.AlignmentFlag.AlignCenter)
        flanger_layout.addWidget(self._flanger_fb, 2, 2, alignment=Qt.AlignmentFlag.AlignCenter)

        self._flanger_mix = RotaryKnob()
        self._flanger_mix.set_value(0.5)
        self._flanger_mix.value_changed.connect(
            lambda v: self.param_changed.emit("flanger_mix", v))
        flanger_layout.addWidget(QLabel("Mix"), 1, 3, alignment=Qt.AlignmentFlag.AlignCenter)
        flanger_layout.addWidget(self._flanger_mix, 2, 3, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(flanger_group)

    def set_state(self, state: ModState):
        self._phaser_chk.blockSignals(True)
        self._phaser_chk.setChecked(state.phaser_enabled)
        self._phaser_chk.blockSignals(False)
        self._phaser_rate.set_value(state.phaser_rate / 10.0)
        self._phaser_depth.set_value(state.phaser_depth)
        self._phaser_fb.set_value(state.phaser_feedback)

        self._chorus_chk.blockSignals(True)
        self._chorus_chk.setChecked(state.chorus_enabled)
        self._chorus_chk.blockSignals(False)
        self._chorus_rate.set_value(state.chorus_rate / 5.0)
        self._chorus_depth.set_value(state.chorus_depth)
        self._chorus_mix.set_value(state.chorus_mix)

        self._flanger_chk.blockSignals(True)
        self._flanger_chk.setChecked(state.flanger_enabled)
        self._flanger_chk.blockSignals(False)
        self._flanger_rate.set_value(state.flanger_rate / 5.0)
        self._flanger_depth.set_value(state.flanger_depth)
        self._flanger_fb.set_value(state.flanger_feedback)
        self._flanger_mix.set_value(state.flanger_mix)


# ---------------------------------------------------------------------------
#  FX Rack view — replaces old tabbed layout with skeuomorphic rack UI
# ---------------------------------------------------------------------------

from mlrvp.gui.widgets.rack_container import RackContainer, MAX_MODULES_PER_RACK
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
# Ensure registry is populated
import mlrvp.gui.rack_modules  # noqa: F401

# Total effects across both FX groups must stay under this cap.
# Budget: 512 samples @ 44100 Hz → 11.6ms callback, ~10.1ms for FX (after ~1.5ms routing overhead).
# Benchmarked timings (post-optimisation):
#   MoogLadder 0.43ms  |  HallReverb 0.16ms  |  PlateReverb 0.13ms
#   Compressor 0.15ms  |  LoFi processors 0.08–0.13ms  |  everything else < 0.08ms
# Worst-case (all 20 × MoogLadder) = 8.6ms — within the 10.1ms FX budget.
# 30% safety margin reserved for GC pauses and OS jitter.
MAX_TOTAL_EFFECTS = 20


class _GroupTabButton(QPushButton):
    """Vintage backlit console-style tab button."""

    def __init__(self, text: str, active: bool = False, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setChecked(active)
        self.setFixedHeight(26)
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
                "border-radius: 3px 3px 0 0; padding: 2px 12px;"
                "letter-spacing: 1px; }"
            )
        else:
            self.setStyleSheet(
                "QPushButton { background: #1a1a1a;"
                f"color: {TEXT_SECONDARY}; font-weight: bold; font-size: 10px;"
                "border: 1px solid #444444; border-bottom: none;"
                "border-radius: 3px 3px 0 0; padding: 2px 12px;"
                "letter-spacing: 1px; }"
                "QPushButton:hover { background: #2a2a2a; }"
            )


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
    effect_route_changed = Signal(str, int)
    midi_map_toggled = Signal(bool)  # activates the global MIDI map manager
    rack_contents_changed = Signal(dict)  # rack/effect sync payload for app↔engine

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
        self._tab_a = _GroupTabButton("FX GROUP A", active=True)
        self._tab_b = _GroupTabButton("FX GROUP B", active=False)
        self._tab_m = _GroupTabButton("FX GROUP M", active=False)
        self._tab_a.clicked.connect(lambda: self._switch_group(0))
        self._tab_b.clicked.connect(lambda: self._switch_group(1))
        self._tab_m.clicked.connect(lambda: self._switch_group(2))

        # MIDI map toggle
        self._midi_map_btn = QPushButton("MIDI MAP")
        self._midi_map_btn.setCheckable(True)
        self._midi_map_btn.setFixedWidth(80)
        self._midi_map_btn.setFixedHeight(26)
        self._midi_map_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_PANEL}; border: 1px solid #444444; border-bottom: none;"
            f"border-radius: 3px 3px 0 0; color: {TEXT_SECONDARY}; font-size: 9px; font-weight: bold; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ background: #2a2a2a; color: {TEXT_PRIMARY}; }}"
            f"QPushButton:checked {{ background: {ACCENT_BLUE}; border-color: {ACCENT_BLUE};"
            f"color: {BG_BLACK}; }}"
        )
        self._midi_map_btn.toggled.connect(self._toggle_midi_map)

        tab_bar.addWidget(self._tab_a)
        tab_bar.addWidget(self._tab_b)
        tab_bar.addWidget(self._tab_m)
        tab_bar.addStretch()
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
        self._rack_m = RackContainer()
        self._stack.addWidget(self._rack_a)
        self._stack.addWidget(self._rack_b)
        self._stack.addWidget(self._rack_m)
        layout.addWidget(self._stack, stretch=1)

        # Cross-rack global limit enforcement
        self._rack_a.set_global_limit_fn(self._at_global_limit)
        self._rack_b.set_global_limit_fn(self._at_global_limit)
        self._rack_m.set_global_limit_fn(self._at_global_limit)

        # Track MIDI highlight state so newly-added modules get highlighted
        self._midi_highlight_on = False
        self._rack_a.rack_changed.connect(self._on_rack_changed)
        self._rack_b.rack_changed.connect(self._on_rack_changed)
        self._rack_m.rack_changed.connect(self._on_rack_changed)

        # --- Populate Group A (FX Bus 1) — Creative Bus: dynamics → EQ → delay ---
        self._vu_a = DualVUMeterModule()
        self._compressor_a = CompressorModule()
        self._filter_a_module = MultibandFilterModule()
        self._filter_b_module = MoogFilterModule()
        self._delay_module = DigitalDelayModule()

        for mod in [self._vu_a, self._compressor_a, self._filter_a_module,
                    self._filter_b_module, self._delay_module]:
            self._rack_a.add_module(mod)

        # --- Populate Group B (FX Bus 2) — Character Bus: glue → tape ---
        self._vu_b = DualVUMeterModule()
        self._glue_compressor_b = GlueCompressorModule()
        self._reel_module = ReelToReelModule()
        # Scope, tape delay, and vinyl kept as hidden instances (property access /
        # state mgmt) but not added to the default rack — add via the ＋ menu.
        self._scope_b = OscilloscopeModule(parent=self)
        self._scope_b.setVisible(False)
        self._tape_delay_module = TapeDelayModule(parent=self)
        self._tape_delay_module.setVisible(False)
        self._vinyl_b = VinylModule(parent=self)
        self._vinyl_b.setVisible(False)

        self._cassette_module = MixtapeModule()
        for mod in [self._vu_b, self._glue_compressor_b, self._reel_module,
                    self._cassette_module]:
            self._rack_b.add_module(mod)

        # --- Populate Group M (Master bus) — Mastering chain: glue → EQ → monitor ---
        self._vu_m = DualVUMeterModule()
        self._compressor_m = GlueCompressorModule()
        self._filter_a_m = MultibandFilterModule()
        self._scope_m = OscilloscopeModule()
        for mod in [self._vu_m, self._compressor_m, self._filter_a_m, self._scope_m]:
            self._rack_m.add_module(mod)

        # Route ALL module params (including dynamically added modules)
        # through the container's relay signal so app.py handlers get them.
        self._rack_a.module_param_changed.connect(
            lambda cls, p, v: self._route_module_param(cls, p, v, 1))
        self._rack_b.module_param_changed.connect(
            lambda cls, p, v: self._route_module_param(cls, p, v, 2))
        self._rack_m.module_param_changed.connect(
            lambda cls, p, v: self._route_module_param(cls, p, v, 3))

        # --- Legacy panel shims for app.py property access ---
        # Phaser/Chorus/Flanger instances exist for state management even when
        # not in the default rack — they can still be added via the ＋ menu.
        # Parent them to self so Qt keeps their C++ objects alive.
        self._phaser_module = PhaserModule(parent=self)
        self._phaser_module.setVisible(False)
        self._chorus_module = ChorusModule(parent=self)
        self._chorus_module.setVisible(False)
        self._flanger_module = FlangerModule(parent=self)
        self._flanger_module.setVisible(False)
        # Hidden instances for effects not in the default racks but still
        # needed for state management and the + menu add workflow.
        self._vhs_module = VHSModule(parent=self)
        self._vhs_module.setVisible(False)
        self._vinyl_module = VinylModule(parent=self)
        self._vinyl_module.setVisible(False)
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
        self._tab_m.setChecked(idx == 2)

    # --- Global effects limit ---
    def _at_global_limit(self) -> bool:
        """Return True if total effects across all racks hit the cap."""
        total = (len(self._rack_a.get_modules())
                 + len(self._rack_b.get_modules())
                 + len(self._rack_m.get_modules()))
        return total > MAX_TOTAL_EFFECTS

    # --- MIDI mapping mode ---
    def _toggle_midi_map(self, on: bool):
        self._midi_highlight_on = on
        for rack in (self._rack_a, self._rack_b, self._rack_m):
            for mod in rack.get_modules():
                mod.set_midi_highlight(on)
        self.midi_map_toggled.emit(on)

    def set_midi_map_active(self, on: bool):
        """Sync the MIDI MAP button state from outside (e.g. setup view Learn)."""
        self._midi_highlight_on = on
        self._midi_map_btn.blockSignals(True)
        self._midi_map_btn.setChecked(on)
        self._midi_map_btn.blockSignals(False)
        for rack in (self._rack_a, self._rack_b, self._rack_m):
            for mod in rack.get_modules():
                mod.set_midi_highlight(on)

    def _on_rack_changed(self):
        """Sync MIDI highlight and effect in-rack status when rack contents change."""
        if self._midi_highlight_on:
            for rack in (self._rack_a, self._rack_b, self._rack_m):
                for mod in rack.get_modules():
                    mod.set_midi_highlight(True)
        self.rack_contents_changed.emit(self.get_rack_sync_state())

    def get_rack_sync_state(self) -> dict:
        """Return per-bus effect presence derived from current rack contents."""
        a_types = {
            mod.effect_type for mod in self._rack_a.get_modules()
            if hasattr(mod, "effect_type")
        }
        b_types = {
            mod.effect_type for mod in self._rack_b.get_modules()
            if hasattr(mod, "effect_type")
        }
        m_types = {
            mod.effect_type for mod in self._rack_m.get_modules()
            if hasattr(mod, "effect_type")
        }
        all_fx = {"delay", "tape_delay", "filter_a", "filter_b",
                  "phaser", "chorus", "flanger",
                  "reel_to_reel", "cassette_tape", "vhs", "vinyl",
                  "compressor", "glue_compressor",
                  "hall_reverb", "plate_reverb",
                  "bit_crusher", "tube_overdrive", "ether_drift"}

        return {
            "presence_a": {et: (et in a_types) for et in all_fx},
            "presence_b": {et: (et in b_types) for et in all_fx},
            "presence_master": {et: (et in m_types) for et in all_fx},
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

        # Per-bus waveform for oscilloscope isolation
        bus_waveforms = {
            id(self._rack_a): (levels.get("waveform_fx1_l"), levels.get("waveform_fx1_r")),
            id(self._rack_b): (levels.get("waveform_fx2_l"), levels.get("waveform_fx2_r")),
            id(self._rack_m): (levels.get("waveform_l"), levels.get("waveform_r")),
        }

        master = levels.get("master", [0.0, 0.0])
        for rack, bus_levels in ((self._rack_a, fx1), (self._rack_b, fx2), (self._rack_m, master)):
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
                        else:
                            gr_key = "glue_compressor_gr_m"
                        gr = levels.get(gr_key, 0.0)
                        mod.update_meters(bus_l, bus_r, gr)
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
        self._phaser.set_state(
            state.phaser_enabled, state.phaser_rate,
            state.phaser_depth, state.phaser_feedback
        )
        self._chorus.set_state(
            state.chorus_enabled, state.chorus_rate,
            state.chorus_depth, state.chorus_mix
        )
        self._flanger.set_state(
            state.flanger_enabled, state.flanger_rate,
            state.flanger_depth, state.flanger_feedback,
            state.flanger_mix
        )

    def __getattr__(self, name):
        if name in self._ATTR_MAP:
            mod_attr, widget_attr = self._ATTR_MAP[name]
            mod = object.__getattribute__(self, mod_attr)
            return getattr(mod, widget_attr)
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")
