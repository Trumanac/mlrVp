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
        # Antialiasing on rectangles is wasted (sharp pixel edges only),
        # and turning it off cuts paint cost noticeably for many cells.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)

        # Background — dark, matching monome hardware surround
        bg = QColor(BG_BLACK)
        painter.fillRect(self.rect(), bg)

        # Pre-build the press-overlay brush once
        press_overlay = QColor(255, 255, 255, 60)
        off_color = QColor(GRID_BUTTON_BG)

        # Local aliases (saves attribute lookups inside the inner loop)
        led_state = self._led_state
        led_rows = len(led_state)
        pressed = self._pressed
        bs = self.button_size
        sp = self.button_spacing
        step = bs + sp
        n_groups = len(GROUP_COLORS)
        cols = self.columns
        rows = self.rows
        draw_rect = painter.drawRect
        set_brush = painter.setBrush

        for y in range(rows):
            row_leds = led_state[y] if y < led_rows else None
            row_len = len(row_leds) if row_leds is not None else 0

            # Cache one QColor per brightness tier for THIS row
            base = QColor(GROUP_COLORS[self._row_colors[y] % n_groups])
            r, g, b = base.red(), base.green(), base.blue()
            tier_full = QColor(r, g, b, 255)
            tier_high = QColor(r, g, b, 180)
            tier_mid  = QColor(r, g, b, 100)
            tier_low  = QColor(r, g, b, 50)

            py = sp + y * step
            for x in range(cols):
                brightness = row_leds[x] if (row_leds is not None and x < row_len) else 0

                if brightness >= 12:
                    color = tier_full
                elif brightness >= 8:
                    color = tier_high
                elif brightness >= 4:
                    color = tier_mid
                elif brightness > 0:
                    color = tier_low
                else:
                    color = off_color

                px = sp + x * step
                rect = QRect(px, py, bs, bs)
                set_brush(color)
                draw_rect(rect)

                if (x, y) in pressed:
                    set_brush(press_overlay)
                    draw_rect(rect)

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
