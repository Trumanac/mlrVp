"""Base class for all skeuomorphic FX rack modules."""

from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QWidget
)
from PySide6.QtCore import Qt, Signal, QMimeData, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient, QDrag, QPixmap


# Rack dimensions
RACK_EAR_WIDTH = 20
RACK_1U_HEIGHT = 60
RACK_2U_HEIGHT = 120
SCREW_RADIUS = 3

# Power icon colours
_PWR_ON = QColor("#33cc33")
_PWR_OFF = QColor("#444444")


class RackModuleBase(QFrame):
    """Base frame for a single rack-mount effect module.

    Subclasses must:
      - call ``_build_faceplate(layout)`` to set up their controls
      - implement ``set_state(state)``
    """

    param_changed = Signal(str, object)
    remove_requested = Signal()

    # Subclasses can set this to customise the param name emitted on power toggle
    # e.g. "phaser_enabled", "chorus_enabled".  Default is "enabled".
    _enabled_param: str = "enabled"

    def __init__(self, unit_height: int = 1, faceplate_color: str = "#1e1e1e",
                 title: str = "MODULE", parent=None):
        super().__init__(parent)
        self._unit_height = unit_height
        self._faceplate_color = QColor(faceplate_color)
        self._title = title
        self._power_on = True
        self._midi_highlight = False

        h = RACK_1U_HEIGHT if unit_height == 1 else RACK_2U_HEIGHT
        self.setFixedHeight(h)
        self.setMinimumWidth(250)

        # Main horizontal: left-ear | faceplate | right-ear
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Left rack ear
        self._left_ear = _RackEar(side="left", module=self)
        self._left_ear.setFixedWidth(RACK_EAR_WIDTH)
        outer.addWidget(self._left_ear)

        # Faceplate area — subclasses populate this
        self._faceplate = QWidget()
        self._faceplate.setStyleSheet(f"background: {faceplate_color};")
        self._faceplate_layout = QVBoxLayout(self._faceplate)
        self._faceplate_layout.setContentsMargins(4, 2, 4, 2)
        self._faceplate_layout.setSpacing(1)
        outer.addWidget(self._faceplate, stretch=1)

        # Right rack ear
        self._right_ear = _RackEar(side="right", module=self)
        self._right_ear.setFixedWidth(RACK_EAR_WIDTH)
        outer.addWidget(self._right_ear)

    # ------------------------------------------------------------------
    @property
    def unit_height(self) -> int:
        return self._unit_height

    @property
    def enabled(self) -> bool:
        return self._power_on

    def set_power(self, on: bool, emit: bool = True):
        """Set power state.  Called by the rack-ear toggle or programmatically."""
        if self._power_on == on:
            return
        self._power_on = on
        self._faceplate.setEnabled(on)
        self._right_ear.update()
        if emit:
            self.param_changed.emit(self._enabled_param, on)

    # Keep old name as alias
    def set_enabled(self, on: bool):
        self.set_power(on, emit=False)

    @property
    def power_widget(self) -> QWidget:
        """Widget used for power-control MIDI mapping/highlighting."""
        return self._right_ear

    def set_midi_highlight(self, on: bool):
        self._midi_highlight = on
        self.update()

    # Subclass API -------------------------------------------------
    def set_state(self, *args, **kwargs):
        """Override to restore module controls from saved state."""
        raise NotImplementedError

    def paintEvent(self, event):
        """Draw MIDI mapping overlay if active."""
        super().paintEvent(event)
        if self._midi_highlight:
            p = QPainter(self)
            if not p.isActive():
                return
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor("#39ff14"), 1.5))
            p.setBrush(QColor(57, 255, 20, 25))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 3, 3)
            p.end()


class _RackEar(QWidget):
    """Painted rack-ear strip with screw holes, power toggle, and remove button."""

    def __init__(self, side: str, module: RackModuleBase, parent=None):
        super().__init__(module if parent is None else parent)
        self._side = side
        self._module = module
        self._drag_start: QPoint | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        if side == "right":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif side == "left":
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    # --- geometry helpers ------------------------------------------------
    def _power_center(self) -> tuple[int, int]:
        """Centre of the power icon (right ear only)."""
        return self.width() // 2, self.height() // 2 - 8

    def _x_center(self) -> tuple[int, int]:
        """Centre of the remove-X icon (right ear only)."""
        return self.width() // 2, self.height() // 2 + 8

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Ear background — dark brushed metal
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0.0, QColor("#2a2a2a"))
        grad.setColorAt(0.5, QColor("#3a3a3a"))
        grad.setColorAt(1.0, QColor("#2a2a2a"))
        p.fillRect(0, 0, w, h, grad)

        # Screws at top and bottom
        p.setPen(QPen(QColor("#555555"), 1))
        p.setBrush(QColor("#444444"))
        cx = w // 2
        for y in (8, h - 8):
            p.drawEllipse(cx - SCREW_RADIUS, y - SCREW_RADIUS,
                          SCREW_RADIUS * 2, SCREW_RADIUS * 2)
            # Phillips cross
            p.setPen(QPen(QColor("#333333"), 1))
            p.drawLine(cx - 2, y, cx + 2, y)
            p.drawLine(cx, y - 2, cx, y + 2)
            p.setPen(QPen(QColor("#555555"), 1))

        if self._side == "right":
            # --- Power icon (above centre) ---
            px, py = self._power_center()
            color = _PWR_ON if self._module._power_on else _PWR_OFF
            r = 4  # icon radius
            p.setPen(QPen(color, 1.3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            # Open circle (gap at top)
            # Qt angles: 0°=3 o'clock, positive=CCW.
            # Gap of 40° centered at 90° (12 o'clock/top): from 70° to 110°.
            # Arc starts at 110°, spans 300° CCW, ends at 70°.
            start_angle = 110 * 16
            span_angle  = 300 * 16
            p.drawArc(px - r, py - r, r * 2, r * 2, start_angle, span_angle)
            # Vertical stroke through gap
            p.drawLine(px, py - r - 1, px, py - 1)

            # --- Remove X (below centre) ---
            xx, xy = self._x_center()
            p.setPen(QPen(QColor("#aa3333"), 1.5))
            p.drawLine(xx - 3, xy - 3, xx + 3, xy + 3)
            p.drawLine(xx - 3, xy + 3, xx + 3, xy - 3)

        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._side == "left":
                self._drag_start = event.position().toPoint()
                return
            if self._side == "right":
                ey = event.position().y()
                # Power toggle zone
                _, py = self._power_center()
                if abs(ey - py) < 9:
                    self._module.set_power(not self._module._power_on)
                    return
                # Remove zone
                _, xy = self._x_center()
                if abs(ey - xy) < 9:
                    self._module.remove_requested.emit()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._side == "left"
            and self._drag_start is not None
            and event.buttons() & Qt.MouseButton.LeftButton):
            dist = (event.position().toPoint() - self._drag_start).manhattanLength()
            if dist >= 8:
                self._start_drag()
                self._drag_start = None
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def _start_drag(self):
        """Initiate a QDrag operation for reordering this module in the rack."""
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-rack-module-id", str(id(self._module)).encode())
        drag.setMimeData(mime)
        # Create a semi-transparent pixmap of the module
        pixmap = self._module.grab()
        faded = QPixmap(pixmap.size())
        faded.fill(QColor(0, 0, 0, 0))
        p = QPainter(faded)
        p.setOpacity(0.6)
        p.drawPixmap(0, 0, pixmap)
        p.end()
        drag.setPixmap(faded)
        drag.setHotSpot(QPoint(10, self._module.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)
