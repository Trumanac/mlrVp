"""VU meter widget for mlrVp — monochrome gray matching original meter~ objects.

Original meters use a single gray color (0.258824 = #424242) at varying alpha
levels for different intensity zones.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QLinearGradient
import weakref

from mlrvp.gui.styles import BG_BLACK, BG_PANEL, VU_COLOR


# ── Shared decay timer for all VU meters ──────────────────────────────
_all_meters: list = []
_shared_timer: QTimer | None = None


def _tick_all_meters():
    # Iterate a snapshot in case _unregister_meter modifies the list
    for m in list(_all_meters):
        try:
            m._decay()
        except RuntimeError:
            # C++ object already deleted
            _unregister_meter(m)


def _register_meter(meter):
    global _shared_timer
    _all_meters.append(meter)
    if _shared_timer is None:
        from PySide6.QtWidgets import QApplication
        _shared_timer = QTimer(QApplication.instance())
        _shared_timer.timeout.connect(_tick_all_meters)
        _shared_timer.start(30)


def _unregister_meter(meter):
    global _shared_timer
    try:
        _all_meters.remove(meter)
    except ValueError:
        pass
    if not _all_meters and _shared_timer is not None:
        _shared_timer.stop()
        _shared_timer = None


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

        # Cache gradients (created once on first paint when size is known)
        self._cached_gradient = None
        self._cached_gradient_size = (0, 0)

        _register_meter(self)
        _weak = weakref.ref(self)
        self.destroyed.connect(lambda: _unregister_meter(_weak()) if _weak() is not None else None)

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
            if self._cached_gradient_size != (w, h):
                gradient = QLinearGradient(0, h, 0, 0)
                gradient.setColorAt(0.0, QColor(0x42, 0x42, 0x42, 80))
                gradient.setColorAt(0.3, QColor(0x42, 0x42, 0x42, 130))
                gradient.setColorAt(0.6, QColor(0x42, 0x42, 0x42, 180))
                gradient.setColorAt(0.85, QColor(0x42, 0x42, 0x42, 220))
                gradient.setColorAt(1.0, QColor(0x42, 0x42, 0x42, 240))
                self._cached_gradient = gradient
                self._cached_gradient_size = (w, h)
            painter.fillRect(0, h - bar_h, w, bar_h, self._cached_gradient)

        # Peak indicator
        if self._peak > 0.01:
            peak_y = int(h - self._peak * h)
            painter.setPen(QColor(0xFF, 0xFF, 0xFF, 140))
            painter.drawLine(0, peak_y, w, peak_y)

    def _paint_horizontal(self, painter: QPainter, w: int, h: int):
        bar_w = int(self._level * w)
        if bar_w > 0:
            if self._cached_gradient_size != (w, h):
                gradient = QLinearGradient(0, 0, w, 0)
                gradient.setColorAt(0.0, QColor(0x42, 0x42, 0x42, 80))
                gradient.setColorAt(0.3, QColor(0x42, 0x42, 0x42, 130))
                gradient.setColorAt(0.6, QColor(0x42, 0x42, 0x42, 180))
                gradient.setColorAt(0.85, QColor(0x42, 0x42, 0x42, 220))
                gradient.setColorAt(1.0, QColor(0x42, 0x42, 0x42, 240))
                self._cached_gradient = gradient
                self._cached_gradient_size = (w, h)
            painter.fillRect(0, 0, bar_w, h, self._cached_gradient)

        # Peak indicator
        if self._peak > 0.01:
            peak_x = int(self._peak * w)
            painter.setPen(QColor(0xFF, 0xFF, 0xFF, 140))
            painter.drawLine(peak_x, 0, peak_x, h)
