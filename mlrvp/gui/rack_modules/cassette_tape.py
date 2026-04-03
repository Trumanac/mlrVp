"""Mixtape — 1U home cassette tape with warm saturation, hiss, and that punchy lo-fi character."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QFont

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import CassetteTapeState

_FACE = "#1a120a"
_CREAM = "#e8d8b0"
_BROWN = "#8a6a3a"
_DIM = "#6a5a3a"


class _CassetteLogo(QWidget):
    """Mini cassette tape icon for the Mixtape module."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 20)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Cassette body
        p.setPen(QPen(QColor(_CREAM), 1))
        p.setBrush(QColor("#2a1a0a"))
        p.drawRoundedRect(2, 2, 24, 16, 2, 2)
        # Tape window
        p.setBrush(QColor("#0a0a0a"))
        p.drawRect(6, 5, 16, 6)
        # Tape reels in window
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_BROWN))
        p.drawEllipse(9, 6, 4, 4)
        p.drawEllipse(17, 6, 4, 4)
        # Label area
        p.setPen(QPen(QColor(_CREAM), 0.5))
        p.drawLine(6, 13, 22, 13)
        p.end()


class MixtapeModule(RackModuleBase):
    """1U Mixtape — home cassette tape character with saturation, hiss, and punch."""

    effect_type = "cassette_tape"
    _enabled_param: str = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="MIXTAPE", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        # Logo
        row.addWidget(_CassetteLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 2)
        title = QLabel("MIXTAPE")
        title.setStyleSheet(
            f"color: {_CREAM}; font-size: 10px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 2px;"
        )
        title_col.addWidget(title)
        type_lbl = QLabel("CASSETTE TAPE")
        type_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Knobs
        for name, default, param in [
            ("SAT", 0.40, "saturation"),
            ("HISS", 0.15, "hiss"),
            ("PUNCH", 0.35, "punch"),
            ("WARP", 0.12, "warp"),
            ("TONE", 0.55, "tone"),
            ("MIX", 0.30, "mix"),
        ]:
            knob = RotaryKnob(color=_CREAM)
            knob.set_value(default)
            knob.value_changed.connect(lambda v, p=param: self.param_changed.emit(p, v))
            setattr(self, f"_{param}", knob)
            col = QVBoxLayout()
            col.setSpacing(0)
            lbl = QLabel(name)
            lbl.setStyleSheet(f"color: {_CREAM}; font-size: 7px; letter-spacing: 1px;")
            col.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
            col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
            row.addLayout(col)

        row.addStretch()
        self._faceplate_layout.addLayout(row)

    def set_state(self, state: CassetteTapeState):
        self.set_power(state.enabled, emit=False)
        self._saturation.set_value(state.saturation)
        self._hiss.set_value(state.hiss)
        self._punch.set_value(state.punch)
        self._warp.set_value(state.warp)
        self._tone.set_value(state.tone)
        self._mix.set_value(state.mix)
