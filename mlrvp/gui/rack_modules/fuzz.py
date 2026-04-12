"""Wool Over — 1U Big Muff-style fuzz/distortion."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

# ── colour palette — raw wool / sheep / Big Muff industrial ──────────────────
_FACE    = "#181410"   # dark charcoal brown faceplate
_WOOL    = "#cc9966"   # warm wool tan
_DIM     = "#776644"   # dim label
_RED     = "#cc3322"   # clipping red indicator
_CREAM   = "#ddccaa"   # cream for secondary labels
_DARK    = "#332211"   # dark border


def _knob_col(label: str, knob: RotaryKnob, color: str = _CREAM) -> QVBoxLayout:
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


class _WoolLogo(QWidget):
    """Fluffy wool / sheep outline icon — Wool Over brand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Wool cloud shape (fluffy circles)
        p.setPen(QPen(QColor(_WOOL), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Bottom row of puffs
        p.drawEllipse(3, 12, 8, 8)
        p.drawEllipse(10, 13, 8, 8)
        p.drawEllipse(17, 12, 8, 8)
        # Top row of puffs
        p.drawEllipse(6, 6, 8, 8)
        p.drawEllipse(14, 6, 8, 8)
        # Crown puff
        p.drawEllipse(9, 2, 8, 8)
        # Lightning bolt through it (distortion)
        p.setPen(QPen(QColor(_RED), 1.5))
        from PySide6.QtCore import QPointF
        bolt = [QPointF(14, 4), QPointF(11, 10), QPointF(15, 10),
                QPointF(12, 18)]
        for i in range(len(bolt) - 1):
            p.drawLine(bolt[i], bolt[i + 1])
        p.end()


class FuzzModule(RackModuleBase):
    """1U Wool Over — Big Muff-style fuzz/distortion."""

    effect_type = "fuzz"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="WOOL OVER", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(5)

        # Logo + title
        row.addWidget(_WoolLogo())
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel("WOOL OVER")
        name_lbl.setStyleSheet(
            f"color: {_WOOL}; font-size: 10px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 2px;"
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
        self._sustain = RotaryKnob(color=_WOOL)
        self._sustain.set_value(0.6)
        self._sustain.value_changed.connect(
            lambda v: self.param_changed.emit("sustain", round(v, 3))
        )
        self._sustain.setToolTip("Pre-fuzz compression — sustain/squash")
        row.addLayout(_knob_col("SUSTAIN", self._sustain, _WOOL))

        # TONE knob
        self._tone = RotaryKnob(color=_CREAM)
        self._tone.set_value(0.5)
        self._tone.value_changed.connect(
            lambda v: self.param_changed.emit("tone", round(v, 3))
        )
        self._tone.setToolTip("Tone — 0=dark mud, 1=bright buzz")
        row.addLayout(_knob_col("TONE", self._tone, _CREAM))

        # MIX knob
        self._mix = RotaryKnob(color=_DIM)
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
