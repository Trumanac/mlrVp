"""Virtual grid widget for mlrVp — on-screen monome grid emulator.

Square buttons with sharp corners matching the monome hardware aesthetic.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QPainter, QColor, QMouseEvent

from mlrvp.config import MAX_ROWS, MAX_COLUMNS, LED_OFF, LED_BRIGHT
from mlrvp.gui.styles import (
    GRID_LED_ON, GRID_LED_DIM, GRID_LED_OFF, GRID_BUTTON_BG, GROUP_COLORS,
    BG_BLACK
)


class VirtualGridWidget(QWidget):
    """Interactive virtual monome grid.

    Displays LED state and handles mouse button presses.
    Emits button_pressed(x, y, state) signals.
    """

    button_pressed = Signal(int, int, int)  # x, y, state (1=press, 0=release)

    def __init__(self, rows: int = 8, columns: int = 8, parent=None):
        super().__init__(parent)
        self.rows = rows
        self.columns = columns
        self._led_state = [[LED_OFF] * MAX_COLUMNS for _ in range(MAX_ROWS)]
        self._row_colors = [0] * MAX_ROWS  # group color index per row
        self._pressed = set()  # currently pressed buttons

        self.button_size = 36
        self.button_spacing = 3
        self.setMinimumSize(
            self.columns * (self.button_size + self.button_spacing) + self.button_spacing,
            self.rows * (self.button_size + self.button_spacing) + self.button_spacing,
        )
        self.setMouseTracking(False)

    def set_dimensions(self, rows: int, columns: int):
        self.rows = rows
        self.columns = columns
        self.setMinimumSize(
            self.columns * (self.button_size + self.button_spacing) + self.button_spacing,
            self.rows * (self.button_size + self.button_spacing) + self.button_spacing,
        )
        self.update()

    def set_led_state(self, led_state: list[list[int]]):
        self._led_state = led_state
        self.update()

    def set_row_color(self, row: int, group_index: int):
        if 0 <= row < MAX_ROWS:
            self._row_colors[row] = group_index

    def _button_rect(self, x: int, y: int) -> QRect:
        px = self.button_spacing + x * (self.button_size + self.button_spacing)
        py = self.button_spacing + y * (self.button_size + self.button_spacing)
        return QRect(px, py, self.button_size, self.button_size)

    def _xy_from_pos(self, pos) -> tuple[int, int]:
        x = int((pos.x() - self.button_spacing) / (self.button_size + self.button_spacing))
        y = int((pos.y() - self.button_spacing) / (self.button_size + self.button_spacing))
        return max(0, min(x, self.columns - 1)), max(0, min(y, self.rows - 1))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background — dark, matching monome hardware surround
        painter.fillRect(self.rect(), QColor(BG_BLACK))

        for y in range(self.rows):
            group_idx = self._row_colors[y] % len(GROUP_COLORS)
            group_color = QColor(GROUP_COLORS[group_idx])

            for x in range(self.columns):
                rect = self._button_rect(x, y)
                brightness = self._led_state[y][x] if y < len(self._led_state) and x < len(self._led_state[y]) else 0

                # Determine button color based on LED brightness
                if brightness >= 12:
                    color = QColor(group_color)
                    color.setAlpha(255)
                elif brightness >= 8:
                    color = QColor(group_color)
                    color.setAlpha(180)
                elif brightness >= 4:
                    color = QColor(group_color)
                    color.setAlpha(100)
                elif brightness > 0:
                    color = QColor(group_color)
                    color.setAlpha(50)
                else:
                    color = QColor(GRID_BUTTON_BG)

                # Draw button — sharp corners (no border-radius)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRect(rect)

                # Pressed state
                if (x, y) in self._pressed:
                    painter.setBrush(QColor(255, 255, 255, 60))
                    painter.drawRect(rect)

        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            x, y = self._xy_from_pos(event.position())
            self._pressed.add((x, y))
            self.button_pressed.emit(x, y, 1)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            x, y = self._xy_from_pos(event.position())
            self._pressed.discard((x, y))
            self.button_pressed.emit(x, y, 0)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        # Support drag across buttons
        pass
