"""Buzz Saw — 1U Big Muff-style fuzz/distortion."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

# ── colour palette — Big Muff Pi (classic NYC red/black) ─────────────────────
_FACE    = "#0d0d0d"   # near-black faceplate
_RED     = "#cc1111"   # Big Muff red — primary accent
_BRIGHT  = "#ff3333"   # bright red highlight
_STEEL   = "#c0c0c0"   # metallic silver for blade / labels
_DIM     = "#882222"   # dim red for secondary labels
_WHITE   = "#e0e0e0"   # near-white primary labels
_DARK    = "#1a0000"   # dark border


def _knob_col(label: str, knob: RotaryKnob, color: str = _WHITE) -> QVBoxLayout:
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


class _BuzzSawLogo(QWidget):
    """Circular saw blade icon — Buzz Saw fuzz."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = 13, 11
        r_outer, r_inner = 9.0, 6.0
        n_teeth = 8

        # Blade — raked teeth (forward-angled, like a real saw)
        blade = QPainterPath()
        for i in range(n_teeth * 2):
            if i % 2 == 0:
                angle = math.radians(i * 180.0 / n_teeth - 90)
                r = r_outer
            else:
                angle = math.radians(i * 180.0 / n_teeth - 90 + 10)
                r = r_inner
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            if i == 0:
                blade.moveTo(x, y)
            else:
                blade.lineTo(x, y)
        blade.closeSubpath()
        p.setPen(QPen(QColor("#888888"), 0.5))
        p.setBrush(QColor(_STEEL))
        p.drawPath(blade)

        # Center arbor hole
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_FACE))
        p.drawEllipse(cx - 2, cy - 2, 5, 5)

        # Sparks — three short red streaks flying off the blade
        p.setPen(QPen(QColor(_RED), 1.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        sparks = [(21, 3, 24, 1), (23, 7, 26, 6), (22, 1, 25, 3)]
        for x1, y1, x2, y2 in sparks:
            p.drawLine(x1, y1, x2, y2)

        p.end()


class FuzzModule(RackModuleBase):
    """1U Buzz Saw — Big Muff-style fuzz/distortion."""

    effect_type = "fuzz"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="BUZZ SAW", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(5)

        # Logo + title
        row.addWidget(_BuzzSawLogo())
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel("BUZZ SAW")
        name_lbl.setStyleSheet(
            f"color: {_WHITE}; font-size: 10px; font-weight: bold;"
            "font-family: 'Arial Black'; letter-spacing: 2px;"
        )
        title_col.addWidget(name_lbl)
        sub_lbl = QLabel("TRANSISTOR FUZZ")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        row.addStretch()

        # GAIN knob
        self._gain = RotaryKnob(color=_RED)
        self._gain.set_value(0.7)
        self._gain.value_changed.connect(
            lambda v: self.param_changed.emit("gain", round(v, 3))
        )
        self._gain.setToolTip("Fuzz drive amount — cranked = destruction")
        row.addLayout(_knob_col("GAIN", self._gain, _RED))

        # SUSTAIN knob
        self._sustain = RotaryKnob(color=_BRIGHT)
        self._sustain.set_value(0.6)
        self._sustain.value_changed.connect(
            lambda v: self.param_changed.emit("sustain", round(v, 3))
        )
        self._sustain.setToolTip("Pre-fuzz compression — sustain/squash")
        row.addLayout(_knob_col("SUSTAIN", self._sustain, _BRIGHT))

        # TONE knob
        self._tone = RotaryKnob(color=_STEEL)
        self._tone.set_value(0.5)
        self._tone.value_changed.connect(
            lambda v: self.param_changed.emit("tone", round(v, 3))
        )
        self._tone.setToolTip("Tone — 0=dark mud, 1=bright buzz")
        row.addLayout(_knob_col("TONE", self._tone, _STEEL))

        # MIX knob
        self._mix = RotaryKnob(color=_WHITE)
        self._mix.set_value(0.5)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", round(v, 3))
        )
        self._mix.setToolTip("Wet/dry blend — 0 = clean, 1 = full fuzz")
        row.addLayout(_knob_col("MIX", self._mix, _DIM))

        self._faceplate_layout.addLayout(row)

    # ── State sync ────────────────────────────────────────────────────────

    def set_state(self, state):
        self.set_power(state.enabled, emit=False)
        self._gain.set_value(state.gain)
        self._sustain.set_value(state.sustain)
        self._tone.set_value(state.tone)
        self._mix.set_value(state.mix)
