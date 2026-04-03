"""Jet Stream — 1U flanger with aviation red-and-white theme."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

_FACE = "#1a0808"     # Dark red-black faceplate
_RED = "#ff3333"
_WHITE = "#eeeeee"
_DIM_RED = "#993333"


class _JetLogo(QWidget):
    """Swept-back wing icon for the Jet Stream flanger."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 26)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Swept delta wing shape
        path = QPainterPath()
        path.moveTo(4, 13)
        path.lineTo(22, 6)
        path.lineTo(20, 13)
        path.lineTo(22, 20)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_RED))
        p.drawPath(path)
        # Contrail lines
        p.setPen(QPen(QColor(255, 255, 255, 100), 1))
        p.drawLine(2, 10, 12, 10)
        p.drawLine(2, 16, 12, 16)
        p.end()


class _ToggleSwitch(QPushButton):
    """Period-accurate toggle switch (on/off)."""

    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self.setCheckable(True)
        self.setFixedSize(40, 18)
        self._update_style()
        self.toggled.connect(lambda: self._update_style())

    def _update_style(self):
        if self.isChecked():
            self.setStyleSheet(
                f"QPushButton {{ background: {_RED}; color: {_WHITE};"
                "font-size: 8px; font-weight: bold; border: 1px solid #ff6666;"
                "border-radius: 2px; }}"
            )
        else:
            self.setStyleSheet(
                "QPushButton { background: #2a1a1a; color: #886666;"
                "font-size: 8px; font-weight: bold; border: 1px solid #553333;"
                "border-radius: 2px; }"
            )


class FlangerModule(RackModuleBase):
    """1U Flanger with red/white faceplate and toggle switches."""

    effect_type = "flanger"
    _enabled_param = "flanger_enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="JET STREAM", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(6)

        # Logo
        row.addWidget(_JetLogo())

        # Title block with stencil aviation font
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 2)
        name_lbl = QLabel("JET STREAM")
        name_lbl.setStyleSheet(
            f"color: {_WHITE}; font-size: 10px; font-weight: bold;"
            f"background: {_RED}; padding: 0 4px; border-radius: 2px;"
            "font-family: 'Courier New'; letter-spacing: 2px;"
        )
        title_col.addWidget(name_lbl)
        type_lbl = QLabel("FLANGER")
        type_lbl.setStyleSheet(
            f"color: {_DIM_RED}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 3px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Rate knob
        self._rate = RotaryKnob(color=_RED)
        self._rate.set_value(0.05)
        self._rate.set_formatter(lambda v: f"{v * 5.0:.2f} Hz")
        self._rate.value_changed.connect(
            lambda v: self.param_changed.emit("flanger_rate", v * 5.0)
        )
        col = QVBoxLayout(); col.setSpacing(0)
        lbl_r = QLabel("RATE")
        lbl_r.setStyleSheet(f"color: {_RED}; font-size: 7px; font-weight: bold;")
        col.addWidget(lbl_r, alignment=Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._rate, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col)

        # Depth knob
        self._depth = RotaryKnob(color=_RED)
        self._depth.set_value(0.40)
        self._depth.value_changed.connect(
            lambda v: self.param_changed.emit("flanger_depth", v)
        )
        col2 = QVBoxLayout(); col2.setSpacing(0)
        lbl_d = QLabel("DEPTH")
        lbl_d.setStyleSheet(f"color: {_RED}; font-size: 7px; font-weight: bold;")
        col2.addWidget(lbl_d, alignment=Qt.AlignmentFlag.AlignCenter)
        col2.addWidget(self._depth, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col2)

        # Feedback knob
        self._fb = RotaryKnob(color=_RED)
        self._fb.set_value(0.25)
        self._fb.value_changed.connect(
            lambda v: self.param_changed.emit("flanger_feedback", v)
        )
        col3 = QVBoxLayout(); col3.setSpacing(0)
        lbl_rg = QLabel("REGEN")
        lbl_rg.setStyleSheet(f"color: {_RED}; font-size: 7px; font-weight: bold;")
        col3.addWidget(lbl_rg, alignment=Qt.AlignmentFlag.AlignCenter)
        col3.addWidget(self._fb, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col3)

        # Mix knob
        self._mix = RotaryKnob(color=_RED)
        self._mix.set_value(0.30)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("flanger_mix", v)
        )
        col4 = QVBoxLayout(); col4.setSpacing(0)
        lbl_m = QLabel("MIX")
        lbl_m.setStyleSheet(f"color: {_RED}; font-size: 7px; font-weight: bold;")
        col4.addWidget(lbl_m, alignment=Qt.AlignmentFlag.AlignCenter)
        col4.addWidget(self._mix, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col4)

        row.addStretch()
        self._faceplate_layout.addLayout(row)

    def set_state(self, enabled: bool, rate: float, depth: float,
                  feedback: float, mix: float):
        self.set_power(enabled, emit=False)
        self._rate.set_value(rate / 5.0)
        self._depth.set_value(depth)
        self._fb.set_value(feedback)
        self._mix.set_value(mix)
