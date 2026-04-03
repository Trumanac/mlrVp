"""VU meter widget for mlrVp — monochrome gray matching original meter~ objects.

Original meters use a single gray color (0.258824 = #424242) at varying alpha
levels for different intensity zones.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QLinearGradient

from mlrvp.gui.styles import BG_BLACK, BG_PANEL, VU_COLOR


class VUMeter(QWidget):
    """Vertical or horizontal VU meter with peak hold — monochrome gray."""

    def __init__(self, orientation=Qt.Orientation.Vertical, parent=None):
        super().__init__(parent)
        self._level: float = 0.0
        self._peak: float = 0.0
        self._peak_hold_frames: int = 0
        self._orientation = orientation

        if orientation == Qt.Orientation.Vertical:
            self.setMinimumSize(8, 40)
            self.setMaximumWidth(14)
        else:
            self.setMinimumSize(40, 8)
            self.setMaximumHeight(14)

        self._decay_timer = QTimer(self)
        self._decay_timer.timeout.connect(self._decay)
        self._decay_timer.start(30)

    def set_level(self, level: float):
        self._level = max(0.0, min(1.0, level))
        if self._level > self._peak:
            self._peak = self._level
            self._peak_hold_frames = 30
        self.update()

    def _decay(self):
        changed = False
        if self._level > 0.001:
            self._level *= 0.85
            changed = True
        elif self._level > 0:
            self._level = 0.0
            changed = True

        if self._peak_hold_frames > 0:
            self._peak_hold_frames -= 1
            changed = True
        elif self._peak > 0.001:
            self._peak *= 0.92
            changed = True
        elif self._peak > 0:
            self._peak = 0.0
            changed = True

        if changed:
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()

        painter.fillRect(self.rect(), QColor(BG_BLACK))

        if self._orientation == Qt.Orientation.Vertical:
            self._paint_vertical(painter, w, h)
        else:
            self._paint_horizontal(painter, w, h)

        painter.end()

    def _paint_vertical(self, painter: QPainter, w: int, h: int):
        bar_h = int(self._level * h)
        if bar_h > 0:
            # Monochrome gray gradient with varying alpha (matching original meter~)
            gradient = QLinearGradient(0, h, 0, 0)
            gradient.setColorAt(0.0, QColor(0x42, 0x42, 0x42, 80))    # cold
            gradient.setColorAt(0.3, QColor(0x42, 0x42, 0x42, 130))   # tepid
            gradient.setColorAt(0.6, QColor(0x42, 0x42, 0x42, 180))   # warm
            gradient.setColorAt(0.85, QColor(0x42, 0x42, 0x42, 220))  # hot
            gradient.setColorAt(1.0, QColor(0x42, 0x42, 0x42, 240))   # overload
            painter.fillRect(0, h - bar_h, w, bar_h, gradient)

        # Peak indicator
        if self._peak > 0.01:
            peak_y = int(h - self._peak * h)
            painter.setPen(QColor(0xFF, 0xFF, 0xFF, 140))
            painter.drawLine(0, peak_y, w, peak_y)

    def _paint_horizontal(self, painter: QPainter, w: int, h: int):
        bar_w = int(self._level * w)
        if bar_w > 0:
            gradient = QLinearGradient(0, 0, w, 0)
            gradient.setColorAt(0.0, QColor(0x42, 0x42, 0x42, 80))
            gradient.setColorAt(0.3, QColor(0x42, 0x42, 0x42, 130))
            gradient.setColorAt(0.6, QColor(0x42, 0x42, 0x42, 180))
            gradient.setColorAt(0.85, QColor(0x42, 0x42, 0x42, 220))
            gradient.setColorAt(1.0, QColor(0x42, 0x42, 0x42, 240))
            painter.fillRect(0, 0, bar_w, h, gradient)

        # Peak indicator
        if self._peak > 0.01:
            peak_x = int(self._peak * w)
            painter.setPen(QColor(0xFF, 0xFF, 0xFF, 140))
            painter.drawLine(peak_x, 0, peak_x, h)
