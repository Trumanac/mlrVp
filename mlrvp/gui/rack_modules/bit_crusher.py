"""Bit Crusher — 1U rack module with industrial glitch aesthetic."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import BitCrusherState

_FACE = "#0c0c0c"
_GREEN = "#33ff33"
_DIM_GREEN = "#1a8a1a"


class _BinaryLogo(QWidget):
    """Binary matrix icon for the Byte Me bit crusher."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setFont(QFont("Consolas", 5, QFont.Weight.Bold))
        p.setPen(QColor(_GREEN))
        bits = ["0110", "1001", "1010", "0101"]
        for r, row_str in enumerate(bits):
            p.drawText(1, 5 + r * 5, row_str)
        p.end()


class _PixelScreen(QWidget):
    """Low-res pixel-style screen showing bit depth."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 28)
        self._text = "12bit"

    def set_text(self, t: str):
        self._text = t
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#060606"))
        p.setPen(QPen(QColor("#1a1a1a"), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        # Pixelated text
        p.setPen(QColor(_GREEN))
        p.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)
        p.end()


class _SteppedDial(RotaryKnob):
    """Heavy stepped metal dial for bit depth."""

    def __init__(self, steps: int = 32, parent=None):
        super().__init__(color="#888888", parent=parent)
        self._steps = steps

    def mouseMoveEvent(self, event):
        if self._dragging:
            dy = self._drag_start_y - event.position().y()
            val = self._drag_start_val + dy / 100.0
            val = max(0.0, min(1.0, val))
            # Snap to steps
            val = round(val * self._steps) / self._steps
            if val != self._value:
                self._value = val
                self.value_changed.emit(self._value)
                self.update()


class BitCrusherModule(RackModuleBase):
    """1U Bit Crusher with pixel screen and stepped dials."""

    effect_type = "bit_crusher"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="BYTE ME", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(6)

        # Logo
        row.addWidget(_BinaryLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("BYTE ME")
        lbl.setStyleSheet(
            f"color: {_GREEN}; font-size: 10px; font-weight: bold;"
            "font-family: 'Consolas'; letter-spacing: 1px;"
        )
        title_col.addWidget(lbl)
        type_lbl = QLabel("BIT CRUSHER")
        type_lbl.setStyleSheet(
            f"color: {_DIM_GREEN}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Pixel screen
        self._screen = _PixelScreen()
        row.addWidget(self._screen)

        # Bit depth dial (stepped)
        self._bits = _SteppedDial(steps=32)
        self._bits.set_value(12 / 32)
        self._bits.set_formatter(lambda v: f"{max(1, round(v * 32))} bit")
        self._bits.value_changed.connect(self._on_bits)
        self._bits.setToolTip("Bit depth — lower values add more lo-fi crushing distortion (1–32 bits)")
        col = QVBoxLayout(); col.setSpacing(0)
        col.addWidget(QLabel("BITS"), alignment=Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._bits, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col)

        # Sample rate reduction
        self._crush = RotaryKnob(color=_GREEN)
        self._crush.set_value(0.0)
        self._crush.value_changed.connect(
            lambda v: self.param_changed.emit("sample_rate_reduction", v)
        )
        self._crush.setToolTip("Sample rate reduction — lowers the effective sample rate for harsher digital artifacts")
        col2 = QVBoxLayout(); col2.setSpacing(0)
        col2.addWidget(QLabel("CRUSH"), alignment=Qt.AlignmentFlag.AlignCenter)
        col2.addWidget(self._crush, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col2)

        # Mix
        self._mix = RotaryKnob(color=_GREEN)
        self._mix.set_value(0.40)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", v)
        )
        self._mix.setToolTip("Wet/dry blend of the bit crusher effect")
        col3 = QVBoxLayout(); col3.setSpacing(0)
        col3.addWidget(QLabel("MIX"), alignment=Qt.AlignmentFlag.AlignCenter)
        col3.addWidget(self._mix, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(col3)

        row.addStretch()
        self._faceplate_layout.addLayout(row)

    def _on_bits(self, v: float):
        bits = max(1, round(v * 32))
        self._screen.set_text(f"{bits}bit")
        self.param_changed.emit("bit_depth", bits)

    def set_state(self, state: BitCrusherState):
        self.set_power(state.enabled, emit=False)
        self._bits.set_value(state.bit_depth / 32)
        self._screen.set_text(f"{state.bit_depth}bit")
        self._crush.set_value(state.sample_rate_reduction)
        self._mix.set_value(state.mix)
