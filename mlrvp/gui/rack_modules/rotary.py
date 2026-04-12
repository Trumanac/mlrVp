"""Spin Cycle — 2U Neo Ventilator II-style rotary speaker cabinet."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import RotaryState

# ── colour palette — dark walnut / cream Leslie cabinet ──────────────────────
_FACE   = "#1a1209"   # dark walnut faceplate
_CREAM  = "#e8dcc0"   # cream Leslie grille cloth
_DIM    = "#6b5a3e"   # dim walnut — secondary labels
_AMBER  = "#dd8822"   # amber — SLOW LED
_RED    = "#dd2222"   # red — FAST LED
_SILVER = "#b0a890"   # warm silver labels
_LED_OFF = "#2a2218"  # unlit LED
_WALNUT = "#3a2a16"   # walnut accent


# ── Speed selector with 3 painted buttons ────────────────────────────────────

class _SpeedSelector(QWidget):
    """STOP / SLOW / FAST three-position toggle — compact horizontal."""

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self._labels = ["STOP", "SLOW", "FAST"]
        self._colors = ["#888888", _AMBER, _RED]
        self._selected = 0
        self._callback = callback
        self._btn_w, self._btn_h = 28, 13
        self.setFixedSize(self._btn_w * 3 + 4, self._btn_h)

    def set_index(self, idx: int):
        self._selected = max(0, min(2, idx))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont("Arial", 6, QFont.Weight.Bold)
        p.setFont(font)
        bw, bh = self._btn_w, self._btn_h
        for i, (label, color) in enumerate(zip(self._labels, self._colors)):
            x = i * (bw + 2)
            active = (i == self._selected)
            bg = QColor(color) if active else QColor(_LED_OFF)
            p.setPen(QPen(QColor("#444"), 1))
            p.setBrush(bg)
            p.drawRoundedRect(x, 0, bw, bh, 3, 3)
            txt_color = QColor("#ffffff") if active else QColor("#666")
            p.setPen(txt_color)
            p.drawText(x, 0, bw, bh, Qt.AlignmentFlag.AlignCenter, label)
        p.end()

    def mousePressEvent(self, event):
        bw = self._btn_w
        x = int(event.position().x())
        idx = min(2, max(0, x // (bw + 2)))
        if idx != self._selected:
            self._selected = idx
            self.update()
            if self._callback:
                self._callback(idx)


# ── Spinning rotor animation widget ─────────────────────────────────────────

class _RotorAnim(QWidget):
    """Small animated spinning horn/rotor indicator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self._angle = 0.0
        self._speed_hz = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30fps
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_speed(self, hz: float):
        self._speed_hz = hz

    def _tick(self):
        if self._speed_hz > 0.001:
            self._angle += self._speed_hz * 360.0 * 0.033
            self._angle %= 360.0
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 16, 16
        r_outer, r_inner = 13, 4

        # Outer ring — walnut cabinet
        p.setPen(QPen(QColor(_DIM), 1.0))
        p.setBrush(QColor(_WALNUT))
        p.drawEllipse(cx - r_outer, cy - r_outer, r_outer * 2, r_outer * 2)

        # Cream grille cloth
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#d4c8a8"))
        p.drawEllipse(cx - r_outer + 2, cy - r_outer + 2,
                       (r_outer - 2) * 2, (r_outer - 2) * 2)

        # Spinning horn blades (2 opposing)
        p.save()
        p.translate(cx, cy)
        p.rotate(self._angle)
        blade_color = QColor(_AMBER) if self._speed_hz > 0.001 else QColor(_DIM)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(blade_color)
        for offset in (0, 180):
            p.save()
            p.rotate(offset)
            p.drawRoundedRect(-2, -r_outer + 3, 4, r_outer - r_inner - 1, 2, 2)
            p.restore()
        p.restore()

        # Center hub
        p.setPen(QPen(QColor("#555"), 1))
        p.setBrush(QColor("#222"))
        p.drawEllipse(cx - r_inner, cy - r_inner, r_inner * 2, r_inner * 2)
        p.end()


# ── Rotary Speaker Module ───────────────────────────────────────────────────

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


class RotaryModule(RackModuleBase):
    """1U Spin Cycle — Neo Ventilator II rotary speaker cabinet."""

    effect_type = "rotary"
    _enabled_param: str = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="SPIN CYCLE", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        # ── Rotor animation ──
        self._rotor_anim = _RotorAnim()
        row.addWidget(self._rotor_anim)

        # ── Title block ──
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 2)
        title = QLabel("SPIN CYCLE")
        title.setStyleSheet(
            f"color: {_CREAM}; font-size: 9px; font-weight: bold;"
            "font-family: 'Georgia'; font-style: italic; letter-spacing: 2px;"
        )
        title_col.addWidget(title)
        sub = QLabel("ROTARY SPEAKER")
        sub.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub)
        row.addLayout(title_col)

        # ── Speed selector (horizontal) ──
        self._speed_sel = _SpeedSelector(self._on_speed)
        row.addWidget(self._speed_sel, alignment=Qt.AlignmentFlag.AlignVCenter)

        # ── Knobs ──
        for name, default, param, tip in [
            ("ACCEL", 0.50, "acceleration",
             "Acceleration — ramp time between speed changes"),
            ("HORN", 0.80, "horn_level",
             "Horn level — high-frequency rotating horn driver"),
            ("ROTOR", 0.80, "rotor_level",
             "Rotor level — low-frequency rotating drum driver"),
            ("MIX", 1.00, "mix",
             "Wet/dry blend — 0%=dry, 100%=full rotary"),
        ]:
            knob = RotaryKnob()
            knob.set_value(default)
            knob.value_changed.connect(
                lambda v, p=param: self.param_changed.emit(p, v))
            knob.setToolTip(tip)
            setattr(self, f"_{param}_knob", knob)
            row.addLayout(_knob_col(name, knob))

        self._faceplate_layout.addLayout(row)

    # ── callbacks ──

    def _on_speed(self, idx: int):
        self.param_changed.emit("speed", idx)
        _speeds = [0.0, 0.7, 6.7]
        self._rotor_anim.set_speed(_speeds[idx])

    # ── state restore ──

    def set_state(self, state: RotaryState):
        self._speed_sel.set_index(state.speed)
        _speeds = [0.0, 0.7, 6.7]
        self._rotor_anim.set_speed(_speeds[state.speed])
        for param, val in [
            ("acceleration", state.acceleration),
            ("horn_level",   state.horn_level),
            ("rotor_level",  state.rotor_level),
            ("mix",          state.mix),
        ]:
            knob = getattr(self, f"_{param}_knob")
            knob.blockSignals(True)
            knob.set_value(val)
            knob.blockSignals(False)
