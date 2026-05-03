"""Master Filter panel — compact HP/LP trim for the master bus."""

import math
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from mlrvp.gui.styles import BG_PANEL, BG_BLACK, SEPARATOR, TEXT_SECONDARY, TEXT_PRIMARY
from mlrvp.gui.widgets.fader import RotaryKnob


class _MasterFilterCurve(QWidget):
    """Compact filter response curve for the master HP/LP trim."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hp_freq = 20.0
        self._lp_freq = 20000.0
        self.setMinimumHeight(40)
        self.setMinimumWidth(80)

    def set_params(self, hp_freq: float, lp_freq: float):
        self._hp_freq = hp_freq
        self._lp_freq = lp_freq
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        p.fillRect(self.rect(), QColor(BG_BLACK))

        # Grid lines
        p.setPen(QPen(QColor(SEPARATOR), 1, Qt.PenStyle.DotLine))
        p.drawLine(0, h // 2, w, h // 2)
        for f in (100, 1000, 10000):
            x = int(self._freq_to_x(f, w))
            p.drawLine(x, 0, x, h)

        # Frequency labels
        p.setPen(QColor(TEXT_SECONDARY))
        font = p.font()
        font.setPixelSize(8)
        p.setFont(font)
        for f in (100, 1000, 10000):
            x = int(self._freq_to_x(f, w))
            label = f"{f // 1000}k" if f >= 1000 else str(f)
            p.drawText(x + 2, h - 2, label)

        # Compute and draw curve
        path = QPainterPath()
        points = []
        for px in range(w):
            freq = self._x_to_freq(px, w)
            gain = self._compute_gain(freq)
            y = h * (1.0 - gain) * 0.9 + h * 0.05
            points.append((px, y))

        if points:
            path.moveTo(points[0][0], points[0][1])
            for px, py in points[1:]:
                path.lineTo(px, py)

            # Fill
            fill_path = QPainterPath(path)
            fill_path.lineTo(w, h)
            fill_path.lineTo(0, h)
            fill_path.closeSubpath()
            fill_color = QColor("#ffffff")
            fill_color.setAlpha(25)
            p.fillPath(fill_path, fill_color)

            # Line
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawPath(path)

        # Border
        p.setPen(QPen(QColor(SEPARATOR), 1))
        p.drawRect(0, 0, w - 1, h - 1)
        p.end()

    def _freq_to_x(self, freq, width):
        log_min, log_max = math.log10(20), math.log10(20000)
        if freq <= 0:
            freq = 20
        return (math.log10(freq) - log_min) / (log_max - log_min) * width

    def _x_to_freq(self, x, width):
        log_min, log_max = math.log10(20), math.log10(20000)
        return 10 ** (log_min + (x / max(width, 1)) * (log_max - log_min))

    def _compute_gain(self, freq):
        gain = 1.0
        if self._lp_freq < 19900.0:
            ratio = freq / self._lp_freq
            gain *= 1.0 / math.sqrt(1.0 + ratio ** 4)
        if self._hp_freq > 21.0:
            ratio = freq / self._hp_freq
            if ratio > 0:
                gain *= 1.0 / math.sqrt(1.0 + (1.0 / ratio) ** 4)
        return gain


class MasterFilterView(QWidget):
    """Compact master filter with HP and LP knobs and response curve."""

    hp_changed = Signal(float)  # 0.0-1.0
    lp_changed = Signal(float)  # 0.0-1.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"MasterFilterView {{ background: {BG_PANEL}; }}")
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(4)

        # Label
        title = QLabel("MASTER\nFILTER")
        title.setStyleSheet(
            f"font-size: 8px; font-weight: bold; color: {TEXT_SECONDARY};"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Separator
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setFixedHeight(40)
        sep.setStyleSheet(f"background: {SEPARATOR};")
        layout.addWidget(sep)

        # HP knob
        hp_col = QVBoxLayout()
        hp_col.setContentsMargins(0, 0, 0, 0)
        hp_col.setSpacing(1)
        hp_label = QLabel("HP")
        hp_label.setStyleSheet(f"font-size: 8px; font-weight: bold; color: {TEXT_SECONDARY};")
        hp_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hp_col.addWidget(hp_label)
        self._hp_knob = RotaryKnob(color="#79b1ff")
        self._hp_knob.set_value(0.0)
        self._hp_knob.set_formatter(lambda v: f"{int(round(20.0 + v * 480.0))} Hz")
        self._hp_knob.value_changed.connect(self._on_hp)
        self._hp_knob.setToolTip("Master high-pass trim — rolls off low-frequency rumble (20–500 Hz)")
        hp_col.addWidget(self._hp_knob, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(hp_col)

        # LP knob
        lp_col = QVBoxLayout()
        lp_col.setContentsMargins(0, 0, 0, 0)
        lp_col.setSpacing(1)
        lp_label = QLabel("LP")
        lp_label.setStyleSheet(f"font-size: 8px; font-weight: bold; color: {TEXT_SECONDARY};")
        lp_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lp_col.addWidget(lp_label)
        self._lp_knob = RotaryKnob(color="#f5d97a", inverted=True)
        self._lp_knob.set_value(1.0)
        self._lp_knob.set_formatter(lambda v: f"{int(round(200.0 + v * 19800.0))} Hz")
        self._lp_knob.value_changed.connect(self._on_lp)
        self._lp_knob.setToolTip("Master low-pass trim — rolls off high frequencies from the final mix (200Hz–20kHz)")
        lp_col.addWidget(self._lp_knob, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(lp_col)

        # Filter curve display
        self._curve = _MasterFilterCurve()
        layout.addWidget(self._curve, stretch=1)

    def _on_hp(self, val: float):
        hp_freq = 20.0 + val * 480.0
        self._curve.set_params(hp_freq, self._curve._lp_freq)
        self.hp_changed.emit(val)

    def _on_lp(self, val: float):
        lp_freq = 200.0 + val * 19800.0
        self._curve.set_params(self._curve._hp_freq, lp_freq)
        self.lp_changed.emit(val)

    def set_lp_value_external(self, val: float):
        """Update LP knob and curve from an external source (tilt/MIDI) without re-emitting lp_changed."""
        if abs(self._lp_knob.value - val) > 0.001:
            lp_freq = 200.0 + val * 19800.0
            self._curve.set_params(self._curve._hp_freq, lp_freq)
            self._lp_knob.set_value(val)

    def set_hp_value_external(self, val: float):
        """Update HP knob and curve from an external source (tilt/MIDI) without re-emitting hp_changed."""
        if abs(self._hp_knob.value - val) > 0.001:
            hp_freq = 20.0 + val * 480.0
            self._curve.set_params(hp_freq, self._curve._lp_freq)
            self._hp_knob.set_value(val)
