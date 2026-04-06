"""Band Practice — 2U SSL-style parametric EQ channel strip.

Layout mirrors the Analog Obsession SSQ / classic SSL 4000 series EQ:
  HP/LP FILTER | LF | LMF | [badge] | HMF | HF | DRIVE | OUTPUT/EQ
"""

from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QCheckBox, QWidget, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import FilterState

# ── SSL 4000 / SSQ inspired palette ──
_FACE = "#2a2a30"        # Charcoal steel
_CREAM = "#ccccaa"        # Knob labels / filter section
_BLUE = "#5588cc"         # LF / LMF band knobs
_GREEN = "#55aa77"        # HMF / HF band knobs
_RED = "#cc5566"          # Drive knob
_DIM = "#555560"          # Dim labels
_GOLD = "#cc9933"         # Badge accent
_TEXT = "#ddddcc"         # Bright text


class _SSPBadge(QWidget):
    """Analog Obsession-style center badge — triangle emblem in circle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(30, 30)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 15, 15
        r = 12
        p.setPen(QPen(QColor(_GOLD), 1.5))
        p.setBrush(QColor("#1a1a20"))
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_GOLD))
        tri = QPainterPath()
        tri.moveTo(cx, cy - 8)
        tri.lineTo(cx - 7, cy + 6)
        tri.lineTo(cx + 7, cy + 6)
        tri.closeSubpath()
        p.drawPath(tri)
        p.setPen(QPen(QColor("#1a1a20"), 2))
        p.drawLine(cx - 4, cy + 1, cx + 4, cy + 1)
        p.end()


def _lbl(text, color, size=6, bold=True):
    """Tiny SSL-style label."""
    l = QLabel(text)
    w = "font-weight: bold;" if bold else ""
    l.setStyleSheet(
        f"color: {color}; font-size: {size}px; {w}"
        "font-family: 'Arial';"
    )
    l.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return l


def _sep():
    """Thin vertical channel divider."""
    s = QFrame()
    s.setFixedWidth(1)
    s.setStyleSheet("background: #444;")
    return s


class MultibandFilterModule(RackModuleBase):
    """2U SSL-style Parametric EQ: HP/LP filter, LF, LMF, HMF, HF, Drive, Output."""

    effect_type = "filter_a"

    def __init__(self, parent=None):
        super().__init__(unit_height=2, faceplate_color=_FACE,
                         title="BAND PRACTICE", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        outer = QHBoxLayout()
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(3)

        # ── 1. HP/LP FILTER section ──────────────────────────
        filt = QVBoxLayout()
        filt.setSpacing(1)
        filt.addWidget(_lbl("HP/LP", _CREAM, size=6))
        self._hp_chk = QCheckBox("HP")
        self._hp_chk.setChecked(False)
        self._hp_chk.setStyleSheet(f"color: {_CREAM}; font-size: 6px;")
        self._hp_chk.toggled.connect(
            lambda v: self.param_changed.emit("hp_enabled", v))
        filt.addWidget(self._hp_chk, alignment=Qt.AlignmentFlag.AlignCenter)
        self._hp_freq = RotaryKnob(color=_CREAM)
        self._hp_freq.set_value(0.01)
        self._hp_freq.set_formatter(lambda v: f"{int(16 + v * 334)} Hz")
        self._hp_freq.value_changed.connect(
            lambda v: self.param_changed.emit("hp_freq", 16 + v * 334))
        filt.addWidget(self._hp_freq, alignment=Qt.AlignmentFlag.AlignCenter)
        self._lp_chk = QCheckBox("LP")
        self._lp_chk.setChecked(False)
        self._lp_chk.setStyleSheet(f"color: {_CREAM}; font-size: 6px;")
        self._lp_chk.toggled.connect(
            lambda v: self.param_changed.emit("lp_enabled", v))
        filt.addWidget(self._lp_chk, alignment=Qt.AlignmentFlag.AlignCenter)
        self._lp_freq = RotaryKnob(color=_CREAM)
        self._lp_freq.set_value(0.99)
        self._lp_freq.set_formatter(lambda v: f"{int(1500 + v * 18500)} Hz")
        self._lp_freq.value_changed.connect(
            lambda v: self.param_changed.emit("lp_freq", 1500 + v * 18500))
        filt.addWidget(self._lp_freq, alignment=Qt.AlignmentFlag.AlignCenter)
        outer.addLayout(filt)
        outer.addWidget(_sep())

        # ── 2. LF band ──────────────────────────────────────
        lf = QVBoxLayout()
        lf.setSpacing(1)
        lf.addWidget(_lbl("LF", _BLUE, size=7))
        self._lf_chk = QCheckBox()
        self._lf_chk.setChecked(False)
        self._lf_chk.setStyleSheet(f"color: {_BLUE}; font-size: 6px;")
        self._lf_chk.toggled.connect(
            lambda v: self.param_changed.emit("lf_enabled", v))
        lf.addWidget(self._lf_chk, alignment=Qt.AlignmentFlag.AlignCenter)
        self._lo_mid_gain = RotaryKnob(color=_BLUE)
        self._lo_mid_gain.set_value(0.5)
        self._lo_mid_gain.value_changed.connect(
            lambda v: self.param_changed.emit("lo_mid_gain", v))
        lf.addWidget(self._lo_mid_gain, alignment=Qt.AlignmentFlag.AlignCenter)
        lf.addWidget(_lbl("Hz", _DIM, size=5, bold=False))
        self._lo_mid_freq = RotaryKnob(color=_CREAM)
        self._lo_mid_freq.set_value(0.3)
        self._lo_mid_freq.set_formatter(lambda v: f"{int(30 + v * 470)} Hz")
        self._lo_mid_freq.value_changed.connect(
            lambda v: self.param_changed.emit("lo_mid_freq", 30 + v * 470))
        lf.addWidget(self._lo_mid_freq, alignment=Qt.AlignmentFlag.AlignCenter)
        outer.addLayout(lf)

        # ── 3. LMF band ─────────────────────────────────────
        lmf = QVBoxLayout()
        lmf.setSpacing(1)
        lmf.addWidget(_lbl("LMF", _BLUE, size=7))
        self._mid_gain = RotaryKnob(color=_BLUE)
        self._mid_gain.set_value(0.5)
        self._mid_gain.value_changed.connect(
            lambda v: self.param_changed.emit("mid_gain", v))
        lmf.addWidget(self._mid_gain, alignment=Qt.AlignmentFlag.AlignCenter)
        lmf.addWidget(_lbl("Hz", _DIM, size=5, bold=False))
        self._mid_freq = RotaryKnob(color=_CREAM)
        self._mid_freq.set_value(0.5)
        self._mid_freq.set_formatter(lambda v: f"{int(200 + v * 2300)} Hz")
        self._mid_freq.value_changed.connect(
            lambda v: self.param_changed.emit("mid_freq", 200 + v * 2300))
        lmf.addWidget(self._mid_freq, alignment=Qt.AlignmentFlag.AlignCenter)
        lmf.addWidget(_lbl("Q", _DIM, size=5, bold=False))
        self._mid_q = RotaryKnob(color=_CREAM)
        self._mid_q.set_value(0.5)
        self._mid_q.value_changed.connect(
            lambda v: self.param_changed.emit("mid_q", v))
        lmf.addWidget(self._mid_q, alignment=Qt.AlignmentFlag.AlignCenter)
        outer.addLayout(lmf)

        # ── 4. Center badge ──────────────────────────────────
        center = QVBoxLayout()
        center.setSpacing(0)
        center.addStretch()
        center.addWidget(_SSPBadge(), alignment=Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel("SSP")
        lbl.setStyleSheet(
            f"color: {_GOLD}; font-size: 8px; font-weight: bold;"
            "font-family: 'Arial';"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(lbl)
        sub = QLabel("BAND PRACTICE")
        sub.setStyleSheet(
            f"color: {_DIM}; font-size: 5px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(sub)
        center.addStretch()
        outer.addLayout(center)

        # ── 5. HMF band ─────────────────────────────────────
        hmf = QVBoxLayout()
        hmf.setSpacing(1)
        hmf.addWidget(_lbl("HMF", _GREEN, size=7))
        self._hi_mid_gain = RotaryKnob(color=_GREEN)
        self._hi_mid_gain.set_value(0.5)
        self._hi_mid_gain.value_changed.connect(
            lambda v: self.param_changed.emit("hi_mid_gain", v))
        hmf.addWidget(self._hi_mid_gain, alignment=Qt.AlignmentFlag.AlignCenter)
        hmf.addWidget(_lbl("kHz", _DIM, size=5, bold=False))
        self._hi_mid_freq = RotaryKnob(color=_GREEN)
        self._hi_mid_freq.set_value(0.5)
        self._hi_mid_freq.set_formatter(lambda v: f"{int(600 + v * 7400)} Hz")
        self._hi_mid_freq.value_changed.connect(
            lambda v: self.param_changed.emit("hi_mid_freq", 600 + v * 7400))
        hmf.addWidget(self._hi_mid_freq, alignment=Qt.AlignmentFlag.AlignCenter)
        hmf.addWidget(_lbl("Q", _DIM, size=5, bold=False))
        self._hi_mid_q = RotaryKnob(color=_GREEN)
        self._hi_mid_q.set_value(0.5)
        self._hi_mid_q.value_changed.connect(
            lambda v: self.param_changed.emit("hi_mid_q", v))
        hmf.addWidget(self._hi_mid_q, alignment=Qt.AlignmentFlag.AlignCenter)
        outer.addLayout(hmf)

        # ── 6. HF band ──────────────────────────────────────
        hf = QVBoxLayout()
        hf.setSpacing(1)
        hf.addWidget(_lbl("HF", _GREEN, size=7))
        self._hf_chk = QCheckBox()
        self._hf_chk.setChecked(False)
        self._hf_chk.setStyleSheet(f"color: {_GREEN}; font-size: 6px;")
        self._hf_chk.toggled.connect(
            lambda v: self.param_changed.emit("hf_enabled", v))
        hf.addWidget(self._hf_chk, alignment=Qt.AlignmentFlag.AlignCenter)
        self._hp_q = RotaryKnob(color=_GREEN)
        self._hp_q.set_value(0.5)
        self._hp_q.value_changed.connect(
            lambda v: self.param_changed.emit("hp_q", v))
        hf.addWidget(self._hp_q, alignment=Qt.AlignmentFlag.AlignCenter)
        hf.addWidget(_lbl("kHz", _DIM, size=5, bold=False))
        self._lp_q = RotaryKnob(color=_CREAM)
        self._lp_q.set_value(0.5)
        self._lp_q.value_changed.connect(
            lambda v: self.param_changed.emit("lp_q", v))
        hf.addWidget(self._lp_q, alignment=Qt.AlignmentFlag.AlignCenter)
        outer.addLayout(hf)

        outer.addWidget(_sep())

        # ── 7. DRIVE + OUTPUT + EQ enable ────────────────────
        out_sec = QVBoxLayout()
        out_sec.setSpacing(1)
        out_sec.addWidget(_lbl("DRIVE", _RED, size=6))
        self._drive = RotaryKnob(color=_RED)
        self._drive.set_value(0.0)
        self._drive.value_changed.connect(
            lambda v: self.param_changed.emit("drive", v))
        out_sec.addWidget(self._drive, alignment=Qt.AlignmentFlag.AlignCenter)
        out_sec.addWidget(_lbl("OUTPUT", _CREAM, size=6))
        self._output = RotaryKnob(color=_CREAM)
        self._output.set_value(0.5)
        self._output.value_changed.connect(
            lambda v: self.param_changed.emit("output_gain", v))
        out_sec.addWidget(self._output, alignment=Qt.AlignmentFlag.AlignCenter)
        outer.addLayout(out_sec)

        self._faceplate_layout.addLayout(outer)

    def set_state(self, state: FilterState):
        self.set_power(state.enabled, emit=False)
        self._lp_chk.blockSignals(True)
        self._lp_chk.setChecked(state.lp_enabled)
        self._lp_chk.blockSignals(False)
        self._hp_chk.blockSignals(True)
        self._hp_chk.setChecked(state.hp_enabled)
        self._hp_chk.blockSignals(False)
        self._lf_chk.blockSignals(True)
        self._lf_chk.setChecked(state.lf_enabled)
        self._lf_chk.blockSignals(False)
        self._hf_chk.blockSignals(True)
        self._hf_chk.setChecked(state.hf_enabled)
        self._hf_chk.blockSignals(False)
        self._lp_freq.set_value((state.lp_freq - 1500) / 18500)
        self._hp_freq.set_value((state.hp_freq - 16) / 334)
        self._lp_q.set_value(state.lp_q)
        self._hp_q.set_value(state.hp_q)
        self._lo_mid_gain.set_value(state.lo_mid_gain)
        self._lo_mid_freq.set_value((state.lo_mid_freq - 30) / 470)
        self._mid_gain.set_value(state.mid_gain)
        self._mid_freq.set_value((state.mid_freq - 200) / 2300)
        self._mid_q.set_value(state.mid_q)
        self._hi_mid_gain.set_value(state.hi_mid_gain)
        self._hi_mid_freq.set_value((state.hi_mid_freq - 600) / 7400)
        self._hi_mid_q.set_value(state.hi_mid_q)
        self._drive.set_value(state.drive)
        self._output.set_value(state.output_gain)
