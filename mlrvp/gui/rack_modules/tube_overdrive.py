"""Glow Worm — 1U tube overdrive with glowing tube vent."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QRadialGradient, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import TubeOverdriveState
from mlrvp.gui.scale import bind_fixed_size

_FACE = "#0a0a0a"
_YELLOW = "#ccff00"
_DIM = "#668800"


class _GlowWormLogo(QWidget):
    """Small glowing worm icon for the Glow Worm overdrive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 20, 26)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Bioluminescent tail glow
        tail_x, tail_y = 10, 21
        grad = QRadialGradient(tail_x, tail_y, 8)
        grad.setColorAt(0, QColor(204, 255, 0, 210))
        grad.setColorAt(0.5, QColor(150, 220, 0, 90))
        grad.setColorAt(1, QColor(80, 180, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(tail_x - 8, tail_y - 8, 16, 16)

        # Worm body — thick S-curve stroke
        worm_path = QPainterPath()
        worm_path.moveTo(10, 3)
        worm_path.cubicTo(16, 8, 4, 14, 10, 21)
        pen = QPen(QColor(45, 95, 20), 5, Qt.PenStyle.SolidLine,
                   Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(worm_path)

        # Body highlight stripe
        pen2 = QPen(QColor(85, 145, 40, 160), 2, Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap)
        p.setPen(pen2)
        p.drawPath(worm_path)

        # Head
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(60, 115, 30))
        p.drawEllipse(7, 0, 7, 7)

        # Eye
        p.setBrush(QColor(204, 255, 0, 240))
        p.drawEllipse(11, 1, 2, 2)

        # Glowing tail tip
        p.setBrush(QColor(204, 255, 0, 255))
        p.drawEllipse(8, 19, 4, 4)

        p.end()


class _TubeVent(QWidget):
    """Circular vent with glowing tube visible inside."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 36, 36)
        self._glow = 0.5

    def set_glow(self, v: float):
        self._glow = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 2

        # Vent grille (dark circle with slots)
        p.setPen(QPen(QColor("#333333"), 1))
        p.setBrush(QColor("#0a0a0a"))
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Vent slots
        p.setPen(QPen(QColor("#1a1a1a"), 1))
        for offset in (-4, -2, 0, 2, 4):
            p.drawLine(cx - r + 4, cy + offset, cx + r - 4, cy + offset)

        # Tube glow
        alpha = int(40 + self._glow * 160)
        grad = QRadialGradient(cx, cy, r * 0.6)
        grad.setColorAt(0, QColor(180, 255, 0, alpha))
        grad.setColorAt(1, QColor(80, 200, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(cx - r + 4, cy - r + 4, (r - 4) * 2, (r - 4) * 2)
        p.end()


class TubeOverdriveModule(RackModuleBase):
    """1U Tube/Overdrive with MosValve aesthetic and glowing tube vent."""

    effect_type = "tube_overdrive"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="GLOW WORM", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(6)

        # Logo
        row.addWidget(_GlowWormLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("GlowWorm")
        lbl.setStyleSheet(
            f"color: {_YELLOW}; font-size: 12px; font-weight: bold;"
            "font-family: 'Impact'; letter-spacing: 1px;"
        )
        title_col.addWidget(lbl)
        type_lbl = QLabel("OVERDRIVE")
        type_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Tube vent
        self._vent = _TubeVent()
        row.addWidget(self._vent)

        # Drive
        self._drive = RotaryKnob(color=_YELLOW)
        self._drive.set_value(0.25)
        self._drive.value_changed.connect(self._on_drive)
        self._drive.setToolTip("Drive — pre-amp gain into the tube stage; higher = more harmonic saturation and clipping")
        col = QVBoxLayout(); col.setSpacing(0)
        col.addWidget(QLabel("DRIVE"), alignment=Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._drive, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col)

        # Tone
        self._tone = RotaryKnob(color=_YELLOW)
        self._tone.set_value(0.55)
        self._tone.value_changed.connect(
            lambda v: self.param_changed.emit("tone", v)
        )
        self._tone.setToolTip("Tone — low = darker/warmer character; high = brighter/harsher edge on the overdrive")
        col2 = QVBoxLayout(); col2.setSpacing(0)
        col2.addWidget(QLabel("TONE"), alignment=Qt.AlignmentFlag.AlignCenter)
        col2.addWidget(self._tone, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col2)

        # Bias
        self._bias = RotaryKnob(color=_YELLOW)
        self._bias.set_value(0.48)
        self._bias.value_changed.connect(
            lambda v: self.param_changed.emit("tube_bias", v)
        )
        self._bias.setToolTip("Tube bias — shifts the operating point; affects even/odd harmonic balance and asymmetric clipping character")
        col_bias = QVBoxLayout(); col_bias.setSpacing(0)
        col_bias.addWidget(QLabel("BIAS"), alignment=Qt.AlignmentFlag.AlignCenter)
        col_bias.addWidget(self._bias, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col_bias)

        # Mix
        self._mix = RotaryKnob(color=_YELLOW)
        self._mix.set_value(0.35)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", v)
        )
        self._mix.setToolTip("Wet/dry blend — parallel mix of the overdriven signal with the clean dry input")
        col3 = QVBoxLayout(); col3.setSpacing(0)
        col3.addWidget(QLabel("MIX"), alignment=Qt.AlignmentFlag.AlignCenter)
        col3.addWidget(self._mix, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col3)

        row.addStretch()
        self._faceplate_layout.addLayout(row)

    def _on_drive(self, v: float):
        self.param_changed.emit("drive", v)
        self._vent.set_glow(v)

    def set_state(self, state: TubeOverdriveState):
        self.set_power(state.enabled, emit=False)
        self._drive.set_value(state.drive)
        self._vent.set_glow(state.drive)
        self._tone.set_value(state.tone)
        self._bias.set_value(state.tube_bias)
        self._mix.set_value(state.mix)
