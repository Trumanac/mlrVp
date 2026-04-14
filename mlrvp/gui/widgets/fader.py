"""Fader / knob widgets for mlrVp — matched to original multislider styling.

Volume fader slider color: #8E8E8E (gray)
Send fader slider color: #A769ED (purple)
All corners sharp (no border-radius), black backgrounds.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QTimer, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QMouseEvent, QFont

from mlrvp.gui.styles import (
    BG_BLACK, BG_PANEL, SEPARATOR, FADER_SLIDER, FADER_BORDER, GROUP_COLORS,
    ACCENT_BLUE, ACCENT_GOLD
)

_MIDI_HIGHLIGHT_COLOR = QColor(ACCENT_BLUE)
_MIDI_LEARNING_COLOR = QColor(ACCENT_GOLD)


class ControlDisplaySettings:
    """Global flags for the control value overlay popup."""
    locked: bool = False                  # all controls show value overlay permanently
    disabled: bool = False                # never show overlays (overrides everything)
    notifications_suspended: bool = False # suppress during bulk preset loading


class _ValueDisplayMixin:
    """Mixin that adds a floating value overlay to custom-painted fader/knob widgets.

    Apply by:
      1. Adding _ValueDisplayMixin first in the base-class list.
      2. Calling self._init_value_display() at the END of __init__.
    """
    _FONT_OVERLAY = None

    def _init_value_display(self):
        self._FONT_OVERLAY = QFont("Segoe UI", 7, QFont.Weight.Bold)
        self._latched: bool = False
        self._popup_visible: bool = False
        self._formatter = lambda v: f"{int(round(v * 100))}%"
        self._popup_timer = QTimer(self)
        self._popup_timer.setSingleShot(True)
        self._popup_timer.setInterval(1200)
        self._popup_timer.timeout.connect(self._hide_popup)

    def set_formatter(self, fn):
        """Override the default '0-100%' display with a custom (float)->str callable."""
        self._formatter = fn

    def show_value_popup(self):
        """Externally trigger the popup (e.g. from a MIDI callback)."""
        if ControlDisplaySettings.disabled:
            return
        self._popup_visible = True
        self.update()
        if not self._latched and not ControlDisplaySettings.locked:
            self._popup_timer.start()

    def _hide_popup(self):
        self._popup_visible = False
        self.update()

    def _should_show_overlay(self) -> bool:
        if ControlDisplaySettings.disabled:
            return False
        return (self._dragging or self._latched
                or self._popup_visible or ControlDisplaySettings.locked)

    def _handle_right_click(self, event: QMouseEvent) -> bool:
        """Toggle per-control latch on right-click. Returns True if consumed."""
        if event.button() == Qt.MouseButton.RightButton:
            self._latched = not self._latched
            if self._latched:
                self._popup_timer.stop()
                self._popup_visible = True
            else:
                self._popup_timer.stop()
                self._popup_visible = False
            self.update()
            event.accept()
            return True
        return False

    def _draw_value_overlay(self, painter: QPainter, w: int, h: int):
        text = self._formatter(self._value)
        painter.setFont(self._FONT_OVERLAY)
        fm = painter.fontMetrics()
        tw = min(fm.horizontalAdvance(text) + 8, w - 2)
        th = fm.height() + 3
        rx = (w - tw) // 2
        ry = (h - th) // 2
        painter.setBrush(QColor(0, 0, 0, 185))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(rx, ry, tw, th), 3.0, 3.0)
        painter.setPen(QColor(255, 255, 255, 220))
        painter.drawText(QRectF(rx, ry, tw, th), Qt.AlignmentFlag.AlignCenter, text)



class VerticalFader(_ValueDisplayMixin, QWidget):
    """Vertical fader (slider) widget matching mlrV2 mixer style."""

    value_changed = Signal(float)  # 0.0 - 1.0

    def __init__(self, color: str = FADER_SLIDER, default_value: float = 0.75, parent=None):
        super().__init__(parent)
        self._value: float = default_value
        self._default_value: float = default_value
        self._dragging: bool = False
        self._color = QColor(color)
        self._midi_highlight: int = 0  # 0=off, 1=highlight, 2=learning
        self._ref_mark: float | None = None  # optional reference line (0-1 position)
        self.setMinimumSize(20, 80)
        self.setMaximumWidth(36)
        self._init_value_display()

    def set_reference_mark(self, pos: float | None):
        """Set a visual reference tick at *pos* (0-1 fader position), or None to clear."""
        self._ref_mark = pos
        self.update()

    @property
    def value(self) -> float:
        return self._value

    def set_value(self, val: float):
        self._value = max(0.0, min(1.0, val))
        self.update()
        if not ControlDisplaySettings.notifications_suspended:
            self.show_value_popup()

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()

        # Background
        painter.fillRect(self.rect(), QColor(BG_BLACK))

        # Track
        track_x = w // 2
        track_top = 4
        track_bot = h - 4
        track_h = track_bot - track_top

        painter.setPen(QPen(QColor(SEPARATOR), 1))
        painter.drawLine(track_x, track_top, track_x, track_bot)

        # Fill below handle
        handle_y = track_bot - int(self._value * track_h)
        fill_color = QColor(self._color)
        fill_color.setAlpha(100)
        painter.setPen(QPen(fill_color, 4))
        painter.drawLine(track_x, handle_y, track_x, track_bot)

        # Handle — sharp corners like original
        handle_w = w - 2
        handle_h = 6
        painter.setBrush(self._color)
        painter.setPen(QPen(QColor(FADER_BORDER), 1))
        painter.drawRect(
            1, handle_y - handle_h // 2,
            handle_w, handle_h
        )

        # Notch on handle
        painter.setPen(QPen(QColor(0, 0, 0, 80), 1))
        painter.drawLine(4, handle_y, w - 4, handle_y)

        # Reference mark (e.g. 0 dB unity line)
        if self._ref_mark is not None:
            ref_y = track_bot - int(self._ref_mark * track_h)
            painter.setPen(QPen(QColor(255, 255, 255, 140), 1))
            painter.drawLine(1, ref_y, w - 1, ref_y)

        # MIDI highlight border
        if self._midi_highlight:
            hc = _MIDI_LEARNING_COLOR if self._midi_highlight == 2 else _MIDI_HIGHLIGHT_COLOR
            painter.setPen(QPen(hc, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(1, 1, w - 2, h - 2)

        if self._should_show_overlay():
            self._draw_value_overlay(painter, w, h)

        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if self._handle_right_click(event):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_value(event.position().y())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self._update_value(event.position().y())

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self._value = self._default_value
        self.value_changed.emit(self._value)
        self.show_value_popup()
        self.update()

    def _update_value(self, y: float):
        track_top = 4
        track_bot = self.height() - 4
        track_h = track_bot - track_top
        if track_h <= 0:
            return
        val = 1.0 - (y - track_top) / track_h
        val = max(0.0, min(1.0, val))
        if val != self._value:
            self._value = val
            self.value_changed.emit(self._value)
            self.update()


class HorizontalFader(_ValueDisplayMixin, QWidget):
    """Horizontal fader widget."""

    value_changed = Signal(float)

    def __init__(self, color: str = FADER_SLIDER, default_value: float = 0.5, parent=None):
        super().__init__(parent)
        self._value: float = default_value
        self._default_value: float = default_value
        self._dragging: bool = False
        self._color = QColor(color)
        self._midi_highlight: int = 0  # 0=off, 1=highlight, 2=learning
        self.setMinimumSize(60, 18)
        self.setMaximumHeight(24)
        self._init_value_display()

    @property
    def value(self) -> float:
        return self._value

    def set_value(self, val: float):
        self._value = max(0.0, min(1.0, val))
        self.update()
        if not ControlDisplaySettings.notifications_suspended:
            self.show_value_popup()

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()

        # Background
        painter.fillRect(self.rect(), QColor(BG_BLACK))

        track_left = 4
        track_right = w - 4
        track_w = track_right - track_left
        track_y = h // 2

        # Track background
        painter.setPen(QPen(QColor(SEPARATOR), 1))
        painter.drawLine(track_left, track_y, track_right, track_y)

        # Fill
        handle_x = track_left + int(self._value * track_w)
        fill_color = QColor(self._color)
        fill_color.setAlpha(100)
        painter.setPen(QPen(fill_color, 4))
        painter.drawLine(track_left, track_y, handle_x, track_y)

        # Handle — sharp corners
        handle_w = 6
        handle_h = h - 2
        painter.setBrush(self._color)
        painter.setPen(QPen(QColor(FADER_BORDER), 1))
        painter.drawRect(
            handle_x - handle_w // 2, 1,
            handle_w, handle_h
        )

        # MIDI highlight border
        if self._midi_highlight:
            hc = _MIDI_LEARNING_COLOR if self._midi_highlight == 2 else _MIDI_HIGHLIGHT_COLOR
            painter.setPen(QPen(hc, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(1, 1, w - 2, h - 2)

        if self._should_show_overlay():
            self._draw_value_overlay(painter, w, h)

        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if self._handle_right_click(event):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_value(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self._update_value(event.position().x())

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self._value = self._default_value
        self.value_changed.emit(self._value)
        self.update()

    def _update_value(self, x: float):
        track_left = 4
        track_right = self.width() - 4
        track_w = track_right - track_left
        if track_w <= 0:
            return
        val = (x - track_left) / track_w
        val = max(0.0, min(1.0, val))
        if val != self._value:
            self._value = val
            self.value_changed.emit(self._value)
            self.update()


class RotaryKnob(_ValueDisplayMixin, QWidget):
    """Small rotary knob widget for effect parameters."""

    value_changed = Signal(float)

    def __init__(self, color: str = FADER_SLIDER, inverted: bool = False, parent=None):
        super().__init__(parent)
        self._value: float = 0.5
        self._dragging: bool = False
        self._drag_start_y: float = 0
        self._drag_start_val: float = 0
        self._color = QColor(color)
        self._inverted = inverted
        self._midi_highlight: int = 0  # 0=off, 1=highlight, 2=learning
        self.setFixedSize(32, 32)
        self._init_value_display()

    @property
    def value(self) -> float:
        return self._value

    def set_value(self, val: float):
        self._value = max(0.0, min(1.0, val))
        self.update()
        if not ControlDisplaySettings.notifications_suspended:
            self.show_value_popup()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        cx = w / 2
        cy = h / 2
        r = min(w, h) / 2 - 3

        import math

        # Outer ring / arc background
        start_angle = 225
        span_angle = -270

        painter.setPen(QPen(QColor(SEPARATOR), 3))
        painter.drawArc(int(cx - r), int(cy - r), int(r * 2), int(r * 2),
                        start_angle * 16, span_angle * 16)

        # Value arc
        if self._inverted:
            # Fill from the end (right) backwards — full at 0, empty at 1
            display_val = 1.0 - self._value
            end_angle = start_angle + span_angle  # -45 degrees (bottom-right)
            value_span = int(-span_angle * display_val)  # positive = counter-clockwise
            painter.setPen(QPen(self._color, 3))
            painter.drawArc(int(cx - r), int(cy - r), int(r * 2), int(r * 2),
                            end_angle * 16, value_span * 16)
        else:
            value_span = int(span_angle * self._value)
            painter.setPen(QPen(self._color, 3))
            painter.drawArc(int(cx - r), int(cy - r), int(r * 2), int(r * 2),
                            start_angle * 16, value_span * 16)

        # Indicator line
        angle_rad = math.radians(start_angle + span_angle * self._value)
        ix = cx + (r - 2) * math.cos(angle_rad)
        iy = cy - (r - 2) * math.sin(angle_rad)
        painter.setPen(QPen(QColor(255, 255, 255, 200), 2))
        painter.drawLine(int(cx), int(cy), int(ix), int(iy))

        # Center dot
        painter.setBrush(QColor(BG_PANEL))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(int(cx - 3), int(cy - 3), 6, 6)

        # MIDI highlight ring
        if self._midi_highlight:
            hc = _MIDI_LEARNING_COLOR if self._midi_highlight == 2 else _MIDI_HIGHLIGHT_COLOR
            painter.setPen(QPen(hc, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(1, 1, w - 2, h - 2)

        if self._should_show_overlay():
            self._draw_value_overlay(painter, w, h)

        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if self._handle_right_click(event):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start_y = event.position().y()
            self._drag_start_val = self._value

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            dy = self._drag_start_y - event.position().y()
            val = self._drag_start_val + dy / 100.0
            val = max(0.0, min(1.0, val))
            if val != self._value:
                self._value = val
                self.value_changed.emit(self._value)
                self.show_value_popup()
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self._value = 0.5
        self.value_changed.emit(self._value)
        self.update()
