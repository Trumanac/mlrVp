"""Stained Glass — 1U shimmer reverb with octave-shifted feedback."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient, QConicalGradient

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

# ── colour palette — cathedral stained glass: deep purple/blue/gold ──────────
_FACE    = "#0c0818"   # deep indigo faceplate
_PURPLE  = "#9944cc"   # purple accent
_BLUE    = "#4466dd"   # blue
_GOLD    = "#ddaa33"   # warm gold shimmer indicator
_DIM     = "#553388"   # dim purple
_SILVER  = "#aaaaaa"


def _knob_col(label: str, knob: RotaryKnob, color: str = _SILVER) -> QVBoxLayout:
    col = QVBoxLayout()
    col.setSpacing(0)
    col.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label)
    bind_fixed_height(lbl, 10)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"color: {color}; font-size: 7px; font-weight: bold;"
        "font-family: 'Arial'; letter-spacing: 1px;"
    )
    col.addWidget(lbl)
    col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
    return col


class _StainedGlassLogo(QWidget):
    """Gothic arch window with color panels — stained glass icon."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 22, 24)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Gothic arch outline
        p.setPen(QPen(QColor(_GOLD), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        from PySide6.QtCore import QPointF
        # Arch shape
        p.drawLine(QPointF(4, 22), QPointF(4, 8))
        p.drawLine(QPointF(18, 22), QPointF(18, 8))
        # Pointed arch top
        p.drawLine(QPointF(4, 8), QPointF(11, 2))
        p.drawLine(QPointF(18, 8), QPointF(11, 2))
        # Cross mullion
        p.setPen(QPen(QColor(_DIM), 0.8))
        p.drawLine(QPointF(11, 22), QPointF(11, 6))
        p.drawLine(QPointF(4, 14), QPointF(18, 14))
        # Color panels
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_PURPLE))
        p.drawRect(5, 9, 5, 4)
        p.setBrush(QColor(_BLUE))
        p.drawRect(12, 9, 5, 4)
        p.setBrush(QColor(_GOLD))
        p.drawRect(5, 15, 5, 6)
        p.setBrush(QColor("#cc3355"))
        p.drawRect(12, 15, 5, 6)
        p.end()


class _PitchSelector(QWidget):
    """Pitch interval selector buttons."""

    _INTERVALS = ["+5", "+7", "+12", "+24"]
    _VALUES = [5, 7, 12, 24]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._idx = 2  # default +12
        self._callback = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self._btns = []
        for i, label in enumerate(self._INTERVALS):
            btn = QPushButton(label)
            bind_fixed_size(btn, 24, 14)
            btn.setCheckable(True)
            btn.setChecked(i == self._idx)
            btn.clicked.connect(lambda checked, idx=i: self._select(idx))
            self._btns.append(btn)
            lay.addWidget(btn)
        self._refresh()

    def _select(self, idx: int):
        self._idx = idx
        for i, btn in enumerate(self._btns):
            btn.setChecked(i == idx)
        self._refresh()
        if self._callback:
            self._callback(self._VALUES[idx])

    def set_value(self, semitones: int):
        if semitones in self._VALUES:
            self._idx = self._VALUES.index(semitones)
            for i, btn in enumerate(self._btns):
                btn.blockSignals(True)
                btn.setChecked(i == self._idx)
                btn.blockSignals(False)
            self._refresh()

    def on_change(self, cb):
        self._callback = cb

    def _refresh(self):
        for i, btn in enumerate(self._btns):
            if btn.isChecked():
                btn.setStyleSheet(
                    f"QPushButton {{ background: {_PURPLE}; border: 1px solid {_GOLD};"
                    f"border-radius: 2px; color: #fff; font-size: 7px; font-weight: bold; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #1a1028; border: 1px solid #332255;"
                    f"border-radius: 2px; color: {_DIM}; font-size: 7px; font-weight: bold; }}"
                    f"QPushButton:hover {{ border-color: {_PURPLE}; }}"
                )


class ShimmerReverbModule(RackModuleBase):
    """1U Stained Glass — shimmer reverb."""

    effect_type = "shimmer_reverb"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="STAINED GLASS", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(4)

        # Logo + title
        row.addWidget(_StainedGlassLogo())
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel("STAINED GLASS")
        name_lbl.setStyleSheet(
            f"color: {_PURPLE}; font-size: 9px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px;"
        )
        title_col.addWidget(name_lbl)
        sub_lbl = QLabel("SHIMMER REVERB")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        row.addStretch()

        # DECAY knob
        self._decay = RotaryKnob(color=_BLUE)
        self._decay.set_value(0.8)
        self._decay.value_changed.connect(
            lambda v: self.param_changed.emit("decay", round(v, 3))
        )
        self._decay.setToolTip("Reverb decay time")
        row.addLayout(_knob_col("DECAY", self._decay, _BLUE))

        # SHIMMER knob
        self._shimmer = RotaryKnob(color=_GOLD)
        self._shimmer.set_value(0.5)
        self._shimmer.value_changed.connect(
            lambda v: self.param_changed.emit("shimmer", round(v, 3))
        )
        self._shimmer.setToolTip("Pitch-shifted feedback amount")
        row.addLayout(_knob_col("SHIMMER", self._shimmer, _GOLD))

        # PITCH selector
        pitch_col = QVBoxLayout()
        pitch_col.setSpacing(0)
        pitch_col.setContentsMargins(0, 0, 0, 0)
        pitch_lbl = QLabel("PITCH")
        bind_fixed_height(pitch_lbl, 10)
        pitch_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pitch_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        pitch_col.addWidget(pitch_lbl)
        self._pitch_sel = _PitchSelector()
        self._pitch_sel.on_change(
            lambda semi: self.param_changed.emit("pitch", semi)
        )
        pitch_col.addWidget(self._pitch_sel)
        row.addLayout(pitch_col)

        # DAMP knob
        self._damp = RotaryKnob(color=_DIM)
        self._damp.set_value(0.4)
        self._damp.value_changed.connect(
            lambda v: self.param_changed.emit("damping", round(v, 3))
        )
        self._damp.setToolTip("Feedback damping — higher = darker shimmer tail")
        row.addLayout(_knob_col("DAMP", self._damp, _DIM))

        # MIX knob
        self._mix = RotaryKnob(color=_SILVER)
        self._mix.set_value(0.3)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", round(v, 3))
        )
        self._mix.setToolTip("Wet/dry blend")
        row.addLayout(_knob_col("MIX", self._mix, _SILVER))

        self._faceplate_layout.addLayout(row)

    # ── State sync ────────────────────────────────────────────────────────

    def set_state(self, state):
        self.set_power(state.enabled, emit=False)
        self._decay.set_value(state.decay)
        self._shimmer.set_value(state.shimmer)
        self._pitch_sel.set_value(int(state.pitch))
        self._damp.set_value(state.damping)
        self._mix.set_value(state.mix)
