"""Tracking Shot — 1U VHS tape degradation with wobble, dropout, and worn-tape character."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QFont

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import VHSState
from mlrvp.gui.scale import bind_fixed_size

_FACE = "#0a0a12"
_CYAN = "#00ccdd"
_DIM_CYAN = "#006677"
_STATIC = "#334455"


class _VHSLogo(QWidget):
    """VHS tape tracking-lines icon for the Tracking Shot module."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 28, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # CRT screen shape
        p.setPen(QPen(QColor(_CYAN), 1))
        p.setBrush(QColor("#0a0a18"))
        p.drawRoundedRect(2, 2, 24, 18, 3, 3)
        # Tracking interference lines
        p.setPen(QPen(QColor(_CYAN), 0.8))
        for y_off in (6, 10, 14):
            wobble = math.sin(y_off * 0.8) * 2
            p.drawLine(int(5 + wobble), y_off, int(23 + wobble), y_off)
        # Static noise dots
        p.setPen(QPen(QColor(_DIM_CYAN), 1))
        for pos in [(8, 8), (16, 12), (20, 7), (12, 15), (22, 16)]:
            p.drawPoint(pos[0], pos[1])
        p.end()


class _TrackingMeter(QWidget):
    """Small tracking quality meter showing VHS degradation level."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 36, 22)
        self._level = 0.3

    def set_level(self, v: float):
        self._level = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        w, h = self.width(), self.height()
        # Background
        p.fillRect(0, 0, w, h, QColor("#060610"))
        p.setPen(QPen(QColor("#1a1a2a"), 1))
        p.drawRect(0, 0, w - 1, h - 1)
        # Bar segments
        segments = 8
        seg_w = (w - 4) / segments
        for i in range(segments):
            frac = (i + 1) / segments
            if frac <= self._level:
                if frac < 0.5:
                    c = QColor(_CYAN)
                elif frac < 0.75:
                    c = QColor("#cccc00")
                else:
                    c = QColor("#cc3333")
                p.fillRect(int(2 + i * seg_w), 4, int(seg_w - 1), h - 8, c)
        # Label
        p.setPen(QColor(_DIM_CYAN))
        p.setFont(QFont("Consolas", 5))
        p.drawText(2, h - 2, "TRACK")
        p.end()


class VHSModule(RackModuleBase):
    """1U Tracking Shot — VHS tape degradation with wobble, dropout, and worn character."""

    effect_type = "vhs"
    _enabled_param: str = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="TRACKING SHOT", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        # Logo
        row.addWidget(_VHSLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 2)
        title = QLabel("TRACKING SHOT")
        title.setStyleSheet(
            f"color: {_CYAN}; font-size: 10px; font-weight: bold;"
            "font-family: 'Consolas'; letter-spacing: 1px;"
        )
        title_col.addWidget(title)
        type_lbl = QLabel("VHS DEGRADATION")
        type_lbl.setStyleSheet(
            f"color: {_DIM_CYAN}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Tracking meter
        self._meter = _TrackingMeter()
        row.addWidget(self._meter)

        # Knobs
        _TIPS = {
            "wobble":  "Tape wobble — slow speed instability creating low-frequency pitch wavering",
            "dropout": "Tape dropout — signal gaps and level dips from worn oxide coating",
            "worn":    "Worn tape — HF loss and increased noise from degraded tape surface",
            "noise":   "Tape noise — characteristic VHS hiss and static from worn heads",
            "mix":     "Wet/dry blend — 0%=dry signal, 100%=full VHS degradation character",
        }
        for name, default, param in [
            ("WOBBLE", 0.20, "wobble"),
            ("DROP", 0.08, "dropout"),
            ("WORN", 0.15, "worn"),
            ("NOISE", 0.12, "noise"),
            ("MIX", 0.25, "mix"),
        ]:
            knob = RotaryKnob(color=_CYAN)
            knob.set_value(default)
            knob.value_changed.connect(lambda v, p=param: self.param_changed.emit(p, v))
            setattr(self, f"_{param}", knob)
            knob.setToolTip(_TIPS.get(param, ""))
            col = QVBoxLayout()
            col.setSpacing(0)
            lbl = QLabel(name)
            lbl.setStyleSheet(f"color: {_CYAN}; font-size: 7px; letter-spacing: 1px;")
            col.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
            col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
            row.addLayout(col)

        # Wire wobble knob to tracking meter
        self._wobble.value_changed.connect(self._meter.set_level)

        row.addStretch()
        self._faceplate_layout.addLayout(row)

    def set_state(self, state: VHSState):
        self.set_power(state.enabled, emit=False)
        self._wobble.set_value(state.wobble)
        self._meter.set_level(state.wobble)
        self._dropout.set_value(state.dropout)
        self._worn.set_value(state.worn)
        self._noise.set_value(state.noise)
        self._mix.set_value(state.mix)
