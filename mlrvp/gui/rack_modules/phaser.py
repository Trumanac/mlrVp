"""Snake Charmer — 1U phaser with psychedelic serpentine theme."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

_FACE = "#2a1040"   # Deep purple faceplate
_ACCENT = "#cc66ff"  # Bright purple accent
_DIM = "#9944cc"


class _SnakeLogo(QWidget):
    """Serpentine sine-wave icon for the Snake Charmer phaser."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 26)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        for i in range(40):
            t = i / 39.0
            x = 2 + t * 22
            y = 13 + math.sin(t * 2.5 * math.pi) * 7
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(QColor(204, 102, 255, 50), 4))
        p.drawPath(path)
        p.setPen(QPen(QColor(_ACCENT), 2))
        p.drawPath(path)
        end_y = 13 + math.sin(2.5 * math.pi) * 7
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_ACCENT))
        p.drawEllipse(21, int(end_y) - 3, 5, 5)
        p.end()


class PhaserModule(RackModuleBase):
    """1U Snake Charmer phaser with psychedelic purple theme."""

    effect_type = "phaser"
    _enabled_param = "phaser_enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="SNAKE CHARMER", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(6)

        # Logo
        row.addWidget(_SnakeLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 2)
        name_lbl = QLabel("SNAKE CHARMER")
        name_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 10px; font-weight: bold;"
            "font-family: 'Georgia'; font-style: italic; letter-spacing: 1px;"
        )
        title_col.addWidget(name_lbl)
        type_lbl = QLabel("PHASER")
        type_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 3px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Rate — red knob
        self._rate = RotaryKnob(color="#ff4444")
        self._rate.set_value(0.08)
        self._rate.set_formatter(lambda v: f"{v * 5.0:.2f} Hz")
        self._rate.value_changed.connect(
            lambda v: self.param_changed.emit("phaser_rate", v * 5.0)
        )
        self._rate.setToolTip("LFO rate — controls the speed of the phase sweep (0–5 Hz)")
        col = QVBoxLayout(); col.setSpacing(0)
        lbl_r = QLabel("RATE")
        lbl_r.setStyleSheet("color: #ff4444; font-size: 7px; font-weight: bold;")
        col.addWidget(lbl_r, alignment=Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._rate, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col)

        # Depth — blue knob
        self._depth = RotaryKnob(color="#4488ff")
        self._depth.set_value(0.40)
        self._depth.value_changed.connect(
            lambda v: self.param_changed.emit("phaser_depth", v)
        )
        self._depth.setToolTip("Sweep depth — how far the phase notches sweep through the frequency spectrum")
        col2 = QVBoxLayout(); col2.setSpacing(0)
        lbl_d = QLabel("DEPTH")
        lbl_d.setStyleSheet("color: #4488ff; font-size: 7px; font-weight: bold;")
        col2.addWidget(lbl_d, alignment=Qt.AlignmentFlag.AlignCenter)
        col2.addWidget(self._depth, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col2)

        # Feedback — yellow knob
        self._fb = RotaryKnob(color="#ffcc00")
        self._fb.set_value(0.25)
        self._fb.value_changed.connect(
            lambda v: self.param_changed.emit("phaser_feedback", v)
        )
        self._fb.setToolTip("Feedback — feeds the phased signal back to create more resonant, pronounced notches")
        col3 = QVBoxLayout(); col3.setSpacing(0)
        lbl_f = QLabel("FDBK")
        lbl_f.setStyleSheet("color: #ffcc00; font-size: 7px; font-weight: bold;")
        col3.addWidget(lbl_f, alignment=Qt.AlignmentFlag.AlignCenter)
        col3.addWidget(self._fb, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col3)

        row.addStretch()
        self._faceplate_layout.addLayout(row)

    def set_state(self, state):
        self.set_power(state.phaser_enabled, emit=False)
        self._rate.set_value(state.phaser_rate / 5.0)
        self._depth.set_value(state.phaser_depth)
        self._fb.set_value(state.phaser_feedback)
