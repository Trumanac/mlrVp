"""Pendulum — 1U stereo auto-panner with LFO."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QPen

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

# ── colour palette — midnight blue with pendulum swing accent ────────────────
_FACE    = "#0c0e18"   # dark navy faceplate
_BLUE    = "#3388ee"   # bright blue accent
_DIM     = "#224466"   # dim blue
_CYAN    = "#44ccdd"   # cyan swing indicator
_SILVER  = "#999999"
_AMBER   = "#ffaa33"


def _knob_col(label: str, knob: RotaryKnob, color: str = _SILVER) -> QVBoxLayout:
    col = QVBoxLayout()
    col.setSpacing(0)
    col.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label)
    bind_fixed_height(lbl, 10)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"color: {color}; font-size: 7px; font-weight: bold;"
        "font-family: 'Arial'; letter-spacing: 1px;"
    )
    col.addWidget(lbl)
    col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
    return col


class _PendulumLogo(QWidget):
    """Animated pendulum swing indicator — shows current pan position."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 32, 22)
        self._position = 0.0  # -1 to +1

    def set_position(self, v: float):
        self._position = max(-1.0, min(1.0, v))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        from PySide6.QtCore import QPointF
        cx = 16.0
        # Pivot point
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_DIM))
        p.drawEllipse(int(cx) - 2, 0, 4, 4)
        # Pendulum arm
        angle = self._position * 0.6  # max ±0.6 radians
        bx = cx + math.sin(angle) * 16
        by = 2 + math.cos(angle) * 16
        p.setPen(QPen(QColor(_BLUE), 1.5))
        p.drawLine(QPointF(cx, 2), QPointF(bx, by))
        # Bob
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_CYAN))
        p.drawEllipse(QPointF(bx, by), 3, 3)
        # L/R labels
        p.setPen(QPen(QColor(_DIM), 1))
        font = p.font()
        font.setPixelSize(6)
        p.setFont(font)
        p.drawText(1, 20, "L")
        p.drawText(27, 20, "R")
        p.end()


class _ShapeGroup(QWidget):
    """LFO shape selector."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._idx = 0
        self._callback = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self._btns = []
        for i, label in enumerate(["SIN", "TRI", "SQ"]):
            btn = QPushButton(label)
            bind_fixed_size(btn, 28, 14)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda checked, idx=i: self._select(idx))
            self._btns.append(btn)
            lay.addWidget(btn)
        self._refresh()

    def _select(self, idx):
        self._idx = idx
        for i, btn in enumerate(self._btns):
            btn.setChecked(i == idx)
        self._refresh()
        if self._callback:
            self._callback(idx)

    def set_index(self, idx):
        self._idx = idx
        for i, btn in enumerate(self._btns):
            btn.blockSignals(True)
            btn.setChecked(i == idx)
            btn.blockSignals(False)
        self._refresh()

    def on_change(self, cb):
        self._callback = cb

    def _refresh(self):
        for i, btn in enumerate(self._btns):
            if btn.isChecked():
                btn.setStyleSheet(
                    f"QPushButton {{ background: {_BLUE}; border: 1px solid {_CYAN};"
                    f"border-radius: 2px; color: #fff; font-size: 7px; font-weight: bold; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #111522; border: 1px solid #2a3344;"
                    f"border-radius: 2px; color: {_DIM}; font-size: 7px; font-weight: bold; }}"
                    f"QPushButton:hover {{ border-color: {_BLUE}; }}"
                )


class AutoPanModule(RackModuleBase):
    """1U Pendulum — stereo auto-panner."""

    effect_type = "auto_pan"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="PENDULUM", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(4)

        # Animated pendulum logo
        self._pendulum = _PendulumLogo()
        row.addWidget(self._pendulum)

        # Title
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel("PENDULUM")
        name_lbl.setStyleSheet(
            f"color: {_BLUE}; font-size: 9px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px;"
        )
        title_col.addWidget(name_lbl)
        sub_lbl = QLabel("AUTO-PAN")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        row.addStretch()

        # Pendulum animation timer
        self._pan_rate = 2.0
        self._pan_phase = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._tick_pendulum)
        self._anim_timer.start()

        # RATE knob — 0.1–20 Hz (log)
        self._rate = RotaryKnob(color=_BLUE)
        self._rate.set_value(0.3)
        self._rate.value_changed.connect(self._on_rate)
        self._rate.setToolTip("LFO rate (0.1–20 Hz)")
        self._rate.set_formatter(lambda v: f"{0.1 * 200.0 ** v:.2f} Hz")
        row.addLayout(_knob_col("RATE", self._rate, _BLUE))

        # DEPTH knob
        self._depth = RotaryKnob(color=_CYAN)
        self._depth.set_value(0.8)
        self._depth.value_changed.connect(
            lambda v: self.param_changed.emit("depth", round(v, 3))
        )
        self._depth.setToolTip("Pan depth — 0=center, 1=full L/R swing")
        row.addLayout(_knob_col("DEPTH", self._depth, _CYAN))

        # SHAPE selector
        shape_col = QVBoxLayout()
        shape_col.setSpacing(0)
        shape_col.setContentsMargins(0, 0, 0, 0)
        shape_lbl = QLabel("SHAPE")
        bind_fixed_height(shape_lbl, 10)
        shape_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        shape_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        shape_col.addWidget(shape_lbl)
        self._shape_sel = _ShapeGroup()
        self._shape_sel.on_change(
            lambda idx: self.param_changed.emit("shape", idx)
        )
        shape_col.addWidget(self._shape_sel)
        row.addLayout(shape_col)

        # MIX knob
        self._mix = RotaryKnob(color=_SILVER)
        self._mix.set_value(1.0)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", round(v, 3))
        )
        self._mix.setToolTip("Wet/dry blend")
        row.addLayout(_knob_col("MIX", self._mix, _SILVER))

        self._faceplate_layout.addLayout(row)

    # ── Scaling ───────────────────────────────────────────────────────────

    @staticmethod
    def _knob_to_rate(v: float) -> float:
        """0-1 → 0.1–20 Hz (log)."""
        return 0.1 * (200.0 ** v)

    @staticmethod
    def _rate_to_knob(hz: float) -> float:
        hz = max(0.1, min(20.0, hz))
        return math.log(hz / 0.1) / math.log(200.0)

    def _on_rate(self, v: float):
        hz = self._knob_to_rate(v)
        self._pan_rate = hz
        self.param_changed.emit("rate", round(hz, 2))

    def _tick_pendulum(self):
        """Animate the pendulum swing."""
        self._pan_phase += self._pan_rate * 0.033
        if self._pan_phase >= 1.0:
            self._pan_phase -= int(self._pan_phase)
        pos = math.sin(self._pan_phase * 2.0 * math.pi)
        self._pendulum.set_position(pos)

    # ── State sync ────────────────────────────────────────────────────────

    def set_state(self, state):
        self.set_power(state.enabled, emit=False)
        self._rate.set_value(self._rate_to_knob(state.rate))
        self._pan_rate = state.rate
        self._depth.set_value(state.depth)
        self._shape_sel.set_index(state.shape)
        self._mix.set_value(state.mix)
