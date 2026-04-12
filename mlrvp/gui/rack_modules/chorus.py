"""Parrot Talk — 1U chorus with tropical double-wave theme."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

_FACE = "#0a2a2a"    # Dark teal faceplate
_ACCENT = "#40cccc"   # Bright teal accent
_DIM = "#2a8888"
_CORAL = "#ff7744"
_GOLD = "#ffcc44"


class _ChorusLogo(QWidget):
    """Double offset sine-wave icon representing chorus doubling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for x_off, alpha in [(0, 255), (5, 100)]:
            path = QPainterPath()
            for i in range(30):
                t = i / 29.0
                x = 2 + x_off + t * 18
                y = 14 + math.sin(t * 2 * math.pi) * 7
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            color = QColor(_ACCENT)
            color.setAlpha(alpha)
            p.setPen(QPen(color, 1.8))
            p.drawPath(path)
        p.end()


class ChorusModule(RackModuleBase):
    """1U Parrot Talk chorus with tropical teal theme."""

    effect_type = "chorus"
    _enabled_param = "chorus_enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="PARROT TALK", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(6)

        # Logo
        row.addWidget(_ChorusLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 2)
        name_lbl = QLabel("PARROT TALK")
        name_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 11px; font-weight: bold;"
            "font-family: 'Trebuchet MS'; letter-spacing: 1px;"
        )
        title_col.addWidget(name_lbl)
        type_lbl = QLabel("CHORUS")
        type_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 3px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Speed knob — coral/warm
        self._rate = RotaryKnob(color=_CORAL)
        self._rate.set_value(0.13)
        self._rate.set_formatter(lambda v: f"{v * 5.0:.2f} Hz")
        self._rate.value_changed.connect(
            lambda v: self.param_changed.emit("chorus_rate", v * 5.0)
        )
        self._rate.setToolTip("LFO speed for chorus modulation (0–5 Hz)")
        col = QVBoxLayout(); col.setSpacing(0)
        lbl_s = QLabel("SPEED")
        lbl_s.setStyleSheet(f"color: {_CORAL}; font-size: 7px; font-weight: bold;")
        col.addWidget(lbl_s, alignment=Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._rate, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col)

        # Width / depth — bright teal
        self._depth = RotaryKnob(color="#33dddd")
        self._depth.set_value(0.35)
        self._depth.value_changed.connect(
            lambda v: self.param_changed.emit("chorus_depth", v)
        )
        self._depth.setToolTip("Modulation depth — widens the stereo image")
        col2 = QVBoxLayout(); col2.setSpacing(0)
        lbl_w = QLabel("WIDTH")
        lbl_w.setStyleSheet("color: #33dddd; font-size: 7px; font-weight: bold;")
        col2.addWidget(lbl_w, alignment=Qt.AlignmentFlag.AlignCenter)
        col2.addWidget(self._depth, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col2)

        # Mix — golden
        self._mix = RotaryKnob(color=_GOLD)
        self._mix.set_value(0.30)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("chorus_mix", v)
        )
        self._mix.setToolTip("Wet/dry blend of the chorus effect")
        col3 = QVBoxLayout(); col3.setSpacing(0)
        lbl_m = QLabel("MIX")
        lbl_m.setStyleSheet(f"color: {_GOLD}; font-size: 7px; font-weight: bold;")
        col3.addWidget(lbl_m, alignment=Qt.AlignmentFlag.AlignCenter)
        col3.addWidget(self._mix, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col3)

        row.addStretch()
        self._faceplate_layout.addLayout(row)

    def set_state(self, state):
        self.set_power(state.chorus_enabled, emit=False)
        self._rate.set_value(state.chorus_rate / 5.0)
        self._depth.set_value(state.chorus_depth)
        self._mix.set_value(state.chorus_mix)
