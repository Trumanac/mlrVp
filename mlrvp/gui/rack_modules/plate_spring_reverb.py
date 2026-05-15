"""Spring Fling — 1U plate/spring reverb with silver brushed faceplate."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient, QFont, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import PlateReverbState
from mlrvp.gui.scale import bind_fixed_size

_FACE = "#b0b0b0"   # Silver brushed
_DARK = "#222222"
_DIM = "#666666"


class _SpringLogo(QWidget):
    """Coiled spring icon for Spring Fling reverb."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 24, 20)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(_DARK), 1.5))
        path = QPainterPath()
        for i in range(20):
            t = i / 19.0
            x = 3 + t * 18
            y = 10 + math.sin(t * 5 * math.pi) * 5
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.drawPath(path)
        p.end()


class PlateSpringReverbModule(RackModuleBase):
    """1U Plate/Spring Reverb with silver brushed faceplate."""

    effect_type = "plate_reverb"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="SPRING FLING", parent=parent)
        self._faceplate.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 #a0a0a0, stop:0.5 #c0c0c0, stop:1 #a0a0a0);"
        )
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(6)

        # Logo
        row.addWidget(_SpringLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        lbl = QLabel("SPRING FLING")
        lbl.setStyleSheet(
            f"color: {_DARK}; font-size: 10px; font-weight: bold;"
            "font-family: 'Georgia'; letter-spacing: 2px;"
        )
        title_col.addWidget(lbl)
        type_lbl = QLabel("PLATE / SPRING REVERB")
        type_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Decay knob
        self._decay = RotaryKnob(color=_DARK)
        self._decay.set_value(0.45)
        self._decay.value_changed.connect(
            lambda v: self.param_changed.emit("decay", v))
        self._decay.setToolTip("Decay time — how long the reverb tail rings out before fading to silence")
        col_decay = QVBoxLayout(); col_decay.setSpacing(0)
        lbl_decay = QLabel("DECAY"); lbl_decay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col_decay.addWidget(lbl_decay)
        col_decay.addWidget(self._decay, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col_decay)

        # Pre-delay knob
        self._predelay = RotaryKnob(color=_DARK)
        self._predelay.set_value(0.075)
        self._predelay.set_formatter(lambda v: f"{int(v * 200)} ms")
        self._predelay.value_changed.connect(
            lambda v: self.param_changed.emit("pre_delay", v * 200.0))
        self._predelay.setToolTip("Pre-delay — time gap before the reverb tail starts, adds space and separation (0–200 ms)")
        col_pre = QVBoxLayout(); col_pre.setSpacing(0)
        lbl_pre = QLabel("PRE"); lbl_pre.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col_pre.addWidget(lbl_pre)
        col_pre.addWidget(self._predelay, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col_pre)

        # Dampening knob (was a slider)
        self._damping = RotaryKnob(color=_DARK)
        self._damping.set_value(0.55)
        self._damping.value_changed.connect(
            lambda v: self.param_changed.emit("damping", v))
        self._damping.setToolTip("Damping — absorbs high frequencies as the reverb decays, like soft surfaces in a room")
        col_damp = QVBoxLayout(); col_damp.setSpacing(0)
        lbl_damp = QLabel("DAMP"); lbl_damp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col_damp.addWidget(lbl_damp)
        col_damp.addWidget(self._damping, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col_damp)

        # Mix knob
        self._mix = RotaryKnob(color=_DARK)
        self._mix.set_value(0.25)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", v))
        self._mix.setToolTip("Wet/dry blend — 0%=dry signal only, 100%=full reverb wet signal")
        col_mix = QVBoxLayout(); col_mix.setSpacing(0)
        lbl_mix = QLabel("MIX"); lbl_mix.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col_mix.addWidget(lbl_mix)
        col_mix.addWidget(self._mix, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col_mix)

        row.addStretch()
        self._faceplate_layout.addLayout(row)

    def set_state(self, state: PlateReverbState):
        self.set_power(state.enabled, emit=False)
        self._decay.set_value(state.decay)
        self._predelay.set_value(state.pre_delay / 200.0)
        self._mix.set_value(state.mix)
        self._damping.set_value(state.damping)
