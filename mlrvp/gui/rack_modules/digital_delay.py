"""Déjà Vu Delay — 1U digital delay with blue segmented LED display."""

import math

from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import DelayState
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size, bind_fixed_width, bind_min_width

SYNC_NAMES = [
    "1/32", "1/16t", "1/16", "1/8t", "1/8",
    "1/4t", "3/16", "1/4", "1/3", "3/8",
    "1/2", "3/4", "1 bar",
]

_FACE = "#0c0c14"
_ACCENT = "#3399ff"
_LED_BG = "#060610"
_DIM = "#1a4477"


class _InfinityLogo(QWidget):
    """Infinity loop icon representing echo/repeat."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 26, 20)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        # Left loop
        path.moveTo(13, 10)
        path.cubicTo(13, 4, 3, 4, 3, 10)
        path.cubicTo(3, 16, 13, 16, 13, 10)
        # Right loop
        path.cubicTo(13, 4, 23, 4, 23, 10)
        path.cubicTo(23, 16, 13, 16, 13, 10)
        p.setPen(QPen(QColor(_ACCENT), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()


class _LEDDisplay(QLabel):
    """Blue segmented-LCD style display."""

    def __init__(self, text: str = "250ms", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bind_fixed_size(self, 72, 20)
        self.setStyleSheet(
            f"background: {_LED_BG}; color: {_ACCENT}; font-family: 'Consolas';"
            "font-size: 12px; font-weight: bold; border: 1px solid #1a1a44;"
            "border-radius: 2px; padding: 0 4px;"
        )


class _TactSwitch(QComboBox):
    """Tiny tact-micro-switch style combo selector."""

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self.addItems(items)
        bind_fixed_height(self, 18)
        self.setStyleSheet(
            "QComboBox { background: #1a1a2e; color: #88bbff; font-size: 9px;"
            "border: 1px solid #333366; border-radius: 2px; padding: 0 2px; }"
            "QComboBox::drop-down { width: 12px; }"
            "QComboBox QAbstractItemView { background: #1a1a2e; color: #88bbff; }"
        )


class DigitalDelayModule(RackModuleBase):
    """1U Déjà Vu — digital delay with LED rate display and rate dial.

    A single rate knob drives the delay time.  A SYNC/MS toggle switches
    the knob and display between beat-division mode (13 sync divisions) and
    free millisecond mode (20 ms – 2000 ms).  Knob positions are remembered
    independently per mode so switching back and forth is non-destructive.
    """

    effect_type = "delay"

    # Knob ranges for ms mode
    _MS_MIN = 20.0
    _MS_MAX = 2000.0

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="DÉJÀ VU DELAY", parent=parent)
        # Remember knob position per mode independently
        self._sync_knob_val: float = 3.0 / (len(SYNC_NAMES) - 1)   # → "1/8t"
        self._ms_knob_val:   float = (250.0 - self._MS_MIN) / (self._MS_MAX - self._MS_MIN)
        self._build_faceplate()

    # ── build ────────────────────────────────────────────────────────────────

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(6)

        # ── Logo + title ──────────────────────────────────────────────────
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)

        logo_row = QHBoxLayout()
        logo_row.setSpacing(3)
        logo_row.addWidget(_InfinityLogo())
        lbl = QLabel("DÉJÀ VU")
        lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 10px; font-weight: bold;"
            " font-family: 'Consolas'; letter-spacing: 1px;"
        )
        bind_min_width(lbl, 62)
        logo_row.addWidget(lbl)
        title_col.addLayout(logo_row)

        type_lbl = QLabel("DIGITAL DELAY")
        type_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 7px; font-weight: bold;"
            " font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        row.addStretch()

        # ── LED rate display (left of rate controls) ──────────────────────
        self._led = _LEDDisplay("1/8t")
        row.addWidget(self._led)

        # ── SYNC / MS mode toggle ─────────────────────────────────────────
        self._mode = _TactSwitch(["SYNC", "MS"])
        bind_fixed_width(self._mode, 52)
        self._mode.currentIndexChanged.connect(self._on_mode_changed)
        self._mode.setToolTip(
            "SYNC: lock delay time to BPM grid divisions\n"
            "MS: set delay time manually in milliseconds"
        )
        row.addWidget(self._mode)

        # ── Rate knob ─────────────────────────────────────────────────────
        self._rate_knob = RotaryKnob(color=_ACCENT)
        self._rate_knob.set_value(self._sync_knob_val)
        self._rate_knob.value_changed.connect(self._on_rate_changed)
        self._rate_knob.setToolTip("Delay time — note division in SYNC mode or milliseconds in MS mode")
        row.addWidget(self._rate_knob)
        rate_lbl = QLabel("RATE")
        rate_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 7px; font-weight: bold; letter-spacing: 1px;"
        )
        row.addWidget(rate_lbl)

        # ── Feedback knob ─────────────────────────────────────────────────
        self._fb_gain = RotaryKnob(color=_ACCENT)
        self._fb_gain.set_value(0.45)
        self._fb_gain.value_changed.connect(
            lambda v: self.param_changed.emit("gain", v)
        )
        self._fb_gain.setToolTip("Echo feedback level — controls how many repeats are generated")
        row.addWidget(self._fb_gain)
        fb_lbl = QLabel("ECHO")
        fb_lbl.setStyleSheet(f"color: {_DIM}; font-size: 7px; font-weight: bold;")
        row.addWidget(fb_lbl)

        # ── LP-filter knob ────────────────────────────────────────────────
        self._lp = RotaryKnob(color=_ACCENT)
        self._lp.set_value(0.60)
        self._lp.set_formatter(lambda v: f"{int(v * 19900 + 100)} Hz")
        self._lp.value_changed.connect(
            lambda v: self.param_changed.emit("filter_freq_lp", v * 19900.0 + 100.0)
        )
        self._lp.setToolTip("Low-pass filter for the feedback loop — lower = darker, warmer repeats")
        row.addWidget(self._lp)
        lp_lbl = QLabel("LP")
        lp_lbl.setStyleSheet(f"color: {_DIM}; font-size: 7px; font-weight: bold;")
        row.addWidget(lp_lbl)

        self._faceplate_layout.addLayout(row)

        # Set initial LED text once layout is built
        self._refresh_led()

    # ── rate logic ────────────────────────────────────────────────────────────

    def _on_mode_changed(self, idx: int):
        """Switch between SYNC (0) and MS (1) modes."""
        self.param_changed.emit("time_mode", idx)
        # Restore the knob position for the newly selected mode
        self._rate_knob.blockSignals(True)
        if idx == 0:
            self._rate_knob.set_value(self._sync_knob_val)
        else:
            self._rate_knob.set_value(self._ms_knob_val)
        self._rate_knob.blockSignals(False)
        self._refresh_led()

    def _on_rate_changed(self, value: float):
        """Dispatch rate knob value to DSP and refresh display."""
        mode = self._mode.currentIndex()
        if mode == 0:
            # SYNC — map 0…1 to index 0…12
            self._sync_knob_val = value
            idx = round(value * (len(SYNC_NAMES) - 1))
            idx = max(0, min(idx, len(SYNC_NAMES) - 1))
            self.param_changed.emit("sync_time", idx)
        else:
            # MS — map 0…1 to _MS_MIN…_MS_MAX
            self._ms_knob_val = value
            ms = self._MS_MIN + value * (self._MS_MAX - self._MS_MIN)
            self.param_changed.emit("ms_time", float(ms))
        self._refresh_led()

    def _refresh_led(self):
        """Update the LED to show the current mode's value."""
        mode = self._mode.currentIndex()
        if mode == 0:
            idx = round(self._sync_knob_val * (len(SYNC_NAMES) - 1))
            idx = max(0, min(idx, len(SYNC_NAMES) - 1))
            self._led.setText(SYNC_NAMES[idx])
        else:
            ms = self._MS_MIN + self._ms_knob_val * (self._MS_MAX - self._MS_MIN)
            self._led.setText(f"{int(ms)} ms")

    # ── state sync ────────────────────────────────────────────────────────────

    def set_state(self, state: DelayState):
        self.set_power(state.enabled, emit=False)

        # Compute per-mode knob positions from current state values
        self._sync_knob_val = state.sync_time / max(1, len(SYNC_NAMES) - 1)
        ms_range = self._MS_MAX - self._MS_MIN
        self._ms_knob_val = max(0.0, min(1.0, (state.ms_time - self._MS_MIN) / ms_range))

        self._mode.blockSignals(True)
        self._mode.setCurrentIndex(state.time_mode)
        self._mode.blockSignals(False)

        self._rate_knob.blockSignals(True)
        if state.time_mode == 0:
            self._rate_knob.set_value(self._sync_knob_val)
        else:
            self._rate_knob.set_value(self._ms_knob_val)
        self._rate_knob.blockSignals(False)

        self._fb_gain.set_value(state.gain)
        self._lp.set_value(
            max(0.0, min(1.0, (state.filter_freq_lp - 100.0) / 19900.0))
        )
        self._refresh_led()

