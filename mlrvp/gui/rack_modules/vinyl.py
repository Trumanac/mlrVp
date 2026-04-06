"""Wax Poetic — 1U vinyl record character with crackle, pops, surface noise, and warmth."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import VinylState

_FACE = "#0e0e0e"
_AMBER = "#dd8833"
_DIM_AMBER = "#885522"


class _VinylLogo(QWidget):
    """Vinyl record icon with grooves for the Wax Poetic module."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 26)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 13, 13
        # Record disc
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1a1a1a"))
        p.drawEllipse(cx - 11, cy - 11, 22, 22)
        # Grooves
        p.setPen(QPen(QColor("#2a2a2a"), 0.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for r in (4, 6, 8, 10):
            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        # Label area
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_AMBER))
        p.drawEllipse(cx - 3, cy - 3, 6, 6)
        # Spindle hole
        p.setBrush(QColor("#0e0e0e"))
        p.drawEllipse(cx - 1, cy - 1, 2, 2)
        p.end()


class _WearIndicator(QWidget):
    """Small worn-groove indicator showing vinyl condition."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 22)
        self._wear = 0.3

    def set_wear(self, v: float):
        self._wear = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        w, h = self.width(), self.height()
        # Background
        p.fillRect(0, 0, w, h, QColor("#0a0a0a"))
        p.setPen(QPen(QColor("#2a2a2a"), 1))
        p.drawRect(0, 0, w - 1, h - 1)
        # Wear bar
        bar_w = int((w - 4) * self._wear)
        if self._wear < 0.5:
            c = QColor(_AMBER)
        elif self._wear < 0.75:
            c = QColor("#cc8800")
        else:
            c = QColor("#cc4400")
        p.fillRect(2, 4, bar_w, h - 8, c)
        # Label
        p.setPen(QColor(_DIM_AMBER))
        p.setFont(QFont("Arial", 5))
        p.drawText(2, h - 2, "WEAR")
        p.end()


class VinylModule(RackModuleBase):
    """1U Wax Poetic — vinyl record character with crackle, pops, warmth, and wear."""

    effect_type = "vinyl"
    _enabled_param: str = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="WAX POETIC", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        # Logo
        row.addWidget(_VinylLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 2)
        title = QLabel("WAX POETIC")
        title.setStyleSheet(
            f"color: {_AMBER}; font-size: 10px; font-weight: bold;"
            "font-family: 'Georgia'; font-style: italic; letter-spacing: 2px;"
        )
        title_col.addWidget(title)
        type_lbl = QLabel("VINYL CHARACTER")
        type_lbl.setStyleSheet(
            f"color: {_DIM_AMBER}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Wear indicator
        self._wear_ind = _WearIndicator()
        row.addWidget(self._wear_ind)

        # Knobs
        _TIPS = {
            "crackle": "Surface crackle — continuous high-frequency noise from groove wear and debris",
            "pops":    "Record pops — random impulse noise from dust, scratches, and groove damage",
            "warmth":  "Vinyl warmth — subtle saturation and low-frequency resonance characteristic of vinyl playback",
            "wear":    "Groove wear — HF rolloff and increased surface noise simulating repeated plays",
            "mix":     "Wet/dry blend — 0%=dry signal, 100%=full vinyl character",
        }
        for name, default, param in [
            ("CRACKLE", 0.18, "crackle"),
            ("POPS", 0.10, "pops"),
            ("WARMTH", 0.55, "warmth"),
            ("WEAR", 0.18, "wear"),
            ("MIX", 0.28, "mix"),
        ]:
            knob = RotaryKnob(color=_AMBER)
            knob.set_value(default)
            knob.value_changed.connect(lambda v, p=param: self.param_changed.emit(p, v))
            setattr(self, f"_{param}", knob)
            knob.setToolTip(_TIPS.get(param, ""))
            col = QVBoxLayout()
            col.setSpacing(0)
            lbl = QLabel(name)
            lbl.setStyleSheet(f"color: {_AMBER}; font-size: 7px; letter-spacing: 1px;")
            col.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
            col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
            row.addLayout(col)

        # Wire wear knob to indicator
        self._wear.value_changed.connect(self._wear_ind.set_wear)

        row.addStretch()
        self._faceplate_layout.addLayout(row)

    def set_state(self, state: VinylState):
        self.set_power(state.enabled, emit=False)
        self._crackle.set_value(state.crackle)
        self._pops.set_value(state.pops)
        self._warmth.set_value(state.warmth)
        self._wear.set_value(state.wear)
        self._wear_ind.set_wear(state.wear)
        self._mix.set_value(state.mix)
