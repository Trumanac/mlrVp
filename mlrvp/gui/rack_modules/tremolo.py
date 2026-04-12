"""Heartquake — 1U Demeter TRM-1-style optical tremolo."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt, QPointF, QTimer
from PySide6.QtGui import QPainter, QColor, QPen

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

# ── colour palette — warm orange/amber Demeter look ─────────────────────────
_FACE    = "#12100c"   # near-black warm brown faceplate
_ACCENT  = "#ee8833"   # warm orange — primary accent
_DIM     = "#774422"   # dim amber — secondary labels
_AMBER   = "#ffaa33"   # bright amber — active button
_SILVER  = "#999999"   # silver — knob labels
_SEP     = "#1e1a14"   # separator line


def _knob_col(label: str, knob: RotaryKnob, color: str = _SILVER) -> QVBoxLayout:
    col = QVBoxLayout()
    col.setSpacing(0)
    col.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label)
    lbl.setFixedHeight(10)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"color: {color}; font-size: 7px; font-weight: bold;"
        "font-family: 'Arial'; letter-spacing: 1px;"
    )
    col.addWidget(lbl)
    col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
    return col


class _HeartbeatLogo(QWidget):
    """EKG heartbeat line — Heartquake brand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 18)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(_ACCENT), 1.5))
        pts = [
            QPointF(0, 9), QPointF(5, 9),
            QPointF(7, 9), QPointF(8, 5),
            QPointF(9, 9),
            QPointF(11, 14), QPointF(13, 2), QPointF(15, 16), QPointF(16, 9),
            QPointF(18, 9), QPointF(19, 6), QPointF(21, 9),
            QPointF(23, 9), QPointF(26, 9),
        ]
        for i in range(len(pts) - 1):
            p.drawLine(pts[i], pts[i + 1])
        p.end()


class _ButtonGroup(QWidget):
    """Exclusive toggle button group."""

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        self._btns: list[QPushButton] = []
        self._callback = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        for i, label in enumerate(labels):
            btn = QPushButton(label)
            btn.setFixedSize(36, 15)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda checked, idx=i: self._select(idx))
            self._btns.append(btn)
            lay.addWidget(btn)
        self._update_styles()

    def _select(self, idx: int):
        for i, btn in enumerate(self._btns):
            btn.setChecked(i == idx)
        self._update_styles()
        if self._callback:
            self._callback(idx)

    def set_index(self, idx: int):
        for i, btn in enumerate(self._btns):
            btn.blockSignals(True)
            btn.setChecked(i == idx)
            btn.blockSignals(False)
        self._update_styles()

    def on_change(self, cb):
        self._callback = cb

    def _update_styles(self):
        for btn in self._btns:
            if btn.isChecked():
                btn.setStyleSheet(
                    f"QPushButton {{ background: {_ACCENT}; border: 1px solid {_ACCENT};"
                    f"border-radius: 2px; color: #ffffff; font-size: 7px; font-weight: bold;"
                    f"padding: 0px 2px; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #1a1a1a; border: 1px solid #333;"
                    f"border-radius: 2px; color: {_DIM}; font-size: 7px; font-weight: bold;"
                    f"padding: 0px 2px; }}"
                    f"QPushButton:hover {{ border-color: {_ACCENT}; color: {_ACCENT}; }}"
                )


class _PulseLED(QWidget):
    """Pulsing LED that can be driven by the LFO for visual feedback."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._brightness = 1.0

    def set_brightness(self, v: float):
        self._brightness = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        b = self._brightness
        r = int(238 * b)
        g = int(136 * b)
        bb = int(51 * b)
        p.setBrush(QColor(r, g, bb))
        p.drawEllipse(1, 1, 8, 8)
        p.end()


class TremoloModule(RackModuleBase):
    """1U Tremolo — Demeter TRM-1 inspired optical tremolo."""

    effect_type = "tremolo"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="HEARTQUAKE", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(4)

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(_HeartbeatLogo())
        name_lbl = QLabel("HEARTQUAKE")
        name_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 9px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px;"
        )
        title_row.addWidget(name_lbl)
        title_col.addLayout(title_row)
        sub_lbl = QLabel("OPTICAL TREMOLO")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        # Pulse LED — driven by internal LFO timer
        self._pulse_led = _PulseLED()
        row.addWidget(self._pulse_led)
        row.addSpacing(2)

        self._led_rate_hz = 4.0  # default ~4 Hz
        self._led_phase = 0.0
        self._led_timer = QTimer(self)
        self._led_timer.setInterval(33)  # ~30 fps
        self._led_timer.timeout.connect(self._tick_led)
        self._led_timer.start()

        # RATE knob — 0.5–15 Hz
        self._rate = RotaryKnob(color=_ACCENT)
        self._rate.set_value(0.24)  # ~4 Hz default
        self._rate.value_changed.connect(self._on_rate)
        self._rate.setToolTip("LFO rate (0.5–15 Hz)")
        self._rate.set_formatter(lambda v: f"{0.5 * 30.0 ** v:.1f} Hz")
        row.addLayout(_knob_col("RATE", self._rate, _ACCENT))

        # DEPTH knob — 0–1
        self._depth = RotaryKnob(color=_AMBER)
        self._depth.set_value(0.6)
        self._depth.value_changed.connect(
            lambda v: self.param_changed.emit("depth", v)
        )
        self._depth.setToolTip("Tremolo depth — amount of amplitude modulation")
        row.addLayout(_knob_col("DEPTH", self._depth, _AMBER))

        # SHAPE selector
        shape_col = QVBoxLayout()
        shape_col.setSpacing(0)
        shape_col.setContentsMargins(0, 0, 0, 0)
        shape_lbl = QLabel("SHAPE")
        shape_lbl.setFixedHeight(10)
        shape_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        shape_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        shape_col.addWidget(shape_lbl)
        self._shape_sel = _ButtonGroup(["SIN", "TRI", "SQ"])
        self._shape_sel.on_change(lambda idx: self.param_changed.emit("shape", idx))
        shape_col.addWidget(self._shape_sel)
        row.addLayout(shape_col)

        # STEREO selector
        stereo_col = QVBoxLayout()
        stereo_col.setSpacing(0)
        stereo_col.setContentsMargins(0, 0, 0, 0)
        stereo_lbl = QLabel("STEREO")
        stereo_lbl.setFixedHeight(10)
        stereo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stereo_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        stereo_col.addWidget(stereo_lbl)
        self._stereo_sel = _ButtonGroup(["MON", "ST", "P-P"])
        self._stereo_sel.on_change(
            lambda idx: self.param_changed.emit("stereo_mode", idx)
        )
        stereo_col.addWidget(self._stereo_sel)
        row.addLayout(stereo_col)

        # MIX knob
        self._mix = RotaryKnob(color=_SILVER)
        self._mix.set_value(1.0)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", v)
        )
        self._mix.setToolTip("Wet/dry blend of the tremolo effect")
        row.addLayout(_knob_col("MIX", self._mix, _SILVER))

        self._faceplate_layout.addLayout(row)

    # ── Parameter scaling ─────────────────────────────────────────────────────

    @staticmethod
    def _knob_to_rate(v: float) -> float:
        """0-1 knob → 0.5–15 Hz (log)."""
        return 0.5 * (30.0 ** v)

    @staticmethod
    def _rate_to_knob(hz: float) -> float:
        import math
        hz = max(0.5, min(15.0, hz))
        return math.log(hz / 0.5) / math.log(30.0)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_rate(self, v: float):
        hz = self._knob_to_rate(v)
        self._led_rate_hz = hz
        self.param_changed.emit("rate", hz)

    def _tick_led(self):
        """Advance LED phase and set brightness from a sine LFO."""
        self._led_phase += self._led_rate_hz * 0.033  # dt ≈ 33 ms
        if self._led_phase >= 1.0:
            self._led_phase -= int(self._led_phase)
        brightness = 0.5 + 0.5 * math.sin(self._led_phase * 2.0 * math.pi)
        self._pulse_led.set_brightness(brightness)

    # ── State sync ────────────────────────────────────────────────────────────

    def set_state(self, state):
        """Sync GUI from a TremoloState dataclass."""
        self.set_power(state.enabled, emit=False)
        self._rate.set_value(self._rate_to_knob(state.rate))
        self._led_rate_hz = state.rate
        self._depth.set_value(state.depth)
        self._shape_sel.set_index(state.shape)
        self._stereo_sel.set_index(state.stereo_mode)
        self._mix.set_value(state.mix)
