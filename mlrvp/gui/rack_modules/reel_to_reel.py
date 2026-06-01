"""Reel Deal — 1U studio reel-to-reel tape machine with warm saturation and flutter."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QComboBox
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import ReelToReelState
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

_FACE = "#1c1c1c"
_SILVER = "#c0c0c0"
_DIM = "#707070"
_REEL = "#888888"

TAPE_SPEEDS = ["3¾ ips", "7½ ips", "15 ips", "30 ips"]


class _ReelLogo(QWidget):
    """Spinning reel icon for the Reel Deal module."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 26, 26)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 13, 13
        # Outer reel ring
        p.setPen(QPen(QColor(_SILVER), 1.5))
        p.setBrush(QColor("#2a2a2a"))
        p.drawEllipse(cx - 10, cy - 10, 20, 20)
        # Hub
        p.setBrush(QColor(_SILVER))
        p.drawEllipse(cx - 3, cy - 3, 6, 6)
        # Spokes
        p.setPen(QPen(QColor(_REEL), 1))
        for i in range(3):
            angle = math.radians(i * 120 + 30)
            x1 = cx + int(3 * math.cos(angle))
            y1 = cy + int(3 * math.sin(angle))
            x2 = cx + int(9 * math.cos(angle))
            y2 = cy + int(9 * math.sin(angle))
            p.drawLine(x1, y1, x2, y2)
        p.end()


class ReelToReelModule(RackModuleBase):
    """1U Reel Deal — studio reel-to-reel tape character with saturation and flutter."""

    effect_type = "reel_to_reel"
    _enabled_param: str = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="REEL DEAL", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        # Logo
        row.addWidget(_ReelLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 2)
        title = QLabel("REEL DEAL")
        title.setStyleSheet(
            f"color: {_SILVER}; font-size: 10px; font-weight: bold;"
            "font-family: 'Georgia'; font-style: italic; letter-spacing: 2px;"
        )
        title_col.addWidget(title)
        type_lbl = QLabel("REEL-TO-REEL")
        type_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Tape speed selector
        self._speed = QComboBox()
        self._speed.addItems(TAPE_SPEEDS)
        self._speed.setCurrentIndex(2)  # 15 ips default
        bind_fixed_height(self._speed, 18)
        self._speed.setStyleSheet(
            "QComboBox { background: #2a2a2a; color: #c0c0c0; font-size: 9px;"
            "border: 1px solid #555; border-radius: 2px; }"
            "QComboBox QAbstractItemView { background: #2a2a2a; color: #c0c0c0; }"
        )
        self._speed.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("tape_speed", v)
        )
        self._speed.setToolTip("Tape speed — higher ips = brighter, tighter response; lower ips = warmer, more saturated character")
        row.addWidget(self._speed)

        # Knobs
        _TIPS = {
            "saturation": "Tape saturation — magnetic tape compression and harmonic coloring; simulates tape overload",
            "flutter":    "Flutter — rapid speed fluctuation from irregular tape tension; adds pitch wavering",
            "bias":       "Bias — high-frequency AC bias level; affects HF brightness and saturation character",
            "tone":       "Tone — high-frequency shelf from the playback head; low = duller, high = brighter",
            "mix":        "Wet/dry blend — 0%=dry signal, 100%=full tape character",
        }
        for name, default, param in [
            ("SAT", 0.30, "saturation"),
            ("FLUTTER", 0.15, "flutter"),
            ("BIAS", 0.55, "bias"),
            ("TONE", 0.65, "tone"),
            ("MIX", 0.35, "mix"),
        ]:
            knob = RotaryKnob(color=_SILVER)
            knob.set_value(default)
            knob.value_changed.connect(lambda v, p=param: self.param_changed.emit(p, v))
            setattr(self, f"_{param}", knob)
            knob.setToolTip(_TIPS.get(param, ""))
            col = QVBoxLayout()
            col.setSpacing(0)
            lbl = QLabel(name)
            lbl.setStyleSheet(f"color: {_SILVER}; font-size: 7px; letter-spacing: 1px;")
            col.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
            col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
            row.addLayout(col)

        row.addStretch()
        self._faceplate_layout.addLayout(row)

    def set_state(self, state: ReelToReelState):
        self.set_power(state.enabled, emit=False)
        self._speed.blockSignals(True)
        self._speed.setCurrentIndex(state.tape_speed)
        self._speed.blockSignals(False)
        self._saturation.set_value(state.saturation)
        self._flutter.set_value(state.flutter)
        self._bias.set_value(state.bias)
        self._tone.set_value(state.tone)
        self._mix.set_value(state.mix)
