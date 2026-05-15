"""Virtual grid widget for mlrVp — on-screen monome grid emulator.

Square buttons with sharp corners matching the monome hardware aesthetic.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QPainter, QColor, QMouseEvent

from mlrvp.config import MAX_ROWS, MAX_COLUMNS, LED_OFF, LED_BRIGHT
from mlrvp.gui.scale import sp, scale_manager
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
        self._col_colors: list | None = None  # per-column color override (mix mode)
        self._pressed = set()  # currently pressed buttons

        # Base sizes (un-scaled).  Effective values come through sp().
        self._base_button_size = 36
        self._base_button_spacing = 3
        self.button_size = sp(self._base_button_size)
        self.button_spacing = sp(self._base_button_spacing)
        self.setMinimumSize(
            self.columns * (self.button_size + self.button_spacing) + self.button_spacing,
            self.rows * (self.button_size + self.button_spacing) + self.button_spacing,
        )
        self.setMouseTracking(False)
        # Re-apply sizes when the global UI scale changes
        scale_manager.changed.connect(self._on_scale_changed)

        # Pre-cache QColor objects per (group_index, tier).  Without this,
        # paintEvent allocated up to 5 QColors per lit cell in mix mode
        # (per-column colouring branch) — 64 cells × 5 = 320 allocations
        # per paint, all holding the GIL on the main thread long enough to
        # cause audible dips when the audio DSP worker had to wait on the
        # GIL.  Pre-caching reduces the inner loop to dict lookups only.
        n_groups = len(GROUP_COLORS)
        # _color_tiers[group_idx] = (tier_full, tier_high, tier_mid, tier_low)
        self._color_tiers: list[tuple] = []
        for gi in range(n_groups):
            base = QColor(GROUP_COLORS[gi])
            r, g, b = base.red(), base.green(), base.blue()
            self._color_tiers.append((
                QColor(r, g, b, 255),
                QColor(r, g, b, 180),
                QColor(r, g, b, 100),
                QColor(r, g, b, 50),
            ))
        self._off_color = QColor(GRID_BUTTON_BG)
        self._press_overlay = QColor(255, 255, 255, 60)
        self._armed_overlay = QColor(255, 160, 0, 110)  # amber — armed/pending state
        self._bg_color = QColor(BG_BLACK)
        self._armed: set[tuple[int, int]] = set()  # buttons queued for simultaneous fire
        # Pre-built rect grid (rebuilt on resize / dimension change)
        self._rect_cache: list[list[QRect]] = []
        self._build_rect_cache()

    def _build_rect_cache(self):
        bs = self.button_size
        sp = self.button_spacing
        step = bs + sp
        self._rect_cache = [
            [QRect(sp + x * step, sp + y * step, bs, bs)
             for x in range(MAX_COLUMNS)]
            for y in range(MAX_ROWS)
        ]

    def set_dimensions(self, rows: int, columns: int):
        self.rows = rows
        self.columns = columns
        self.setMinimumSize(
            self.columns * (self.button_size + self.button_spacing) + self.button_spacing,
            self.rows * (self.button_size + self.button_spacing) + self.button_spacing,
        )
        self._build_rect_cache()
        self.update()

    def _on_scale_changed(self, _factor: float):
        """Re-apply scaled button sizes when the global UI scale changes."""
        self.button_size = sp(self._base_button_size)
        self.button_spacing = sp(self._base_button_spacing)
        self.setMinimumSize(
            self.columns * (self.button_size + self.button_spacing) + self.button_spacing,
            self.rows * (self.button_size + self.button_spacing) + self.button_spacing,
        )
        self._build_rect_cache()
        self.update()

    def set_led_state(self, led_state: list[list[int]]):
        self._led_state = led_state
        self.update()

    def set_row_color(self, row: int, group_index: int):
        if 0 <= row < MAX_ROWS:
            self._row_colors[row] = group_index

    def set_col_colors(self, col_colors: list | None):
        """Set per-column color overrides (list of group indices, one per column).

        Pass None to revert to normal per-row coloring.
        Used by mix mode so each fader column shows its group colour.
        Idempotent: if the new value matches the current one, nothing
        happens (avoids redundant paintEvent on every mix-mode update).
        """
        if self._col_colors == col_colors:
            return
        self._col_colors = col_colors
        self.update()

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
        painter.fillRect(self.rect(), self._bg_color)

        # Local aliases (saves attribute lookups inside the inner loop).
        # Crucially, ALL QColor / QRect objects are pre-cached in __init__,
        # so the inner loop allocates nothing.  Previously this loop created
        # up to 320 QColor objects per paint in mix mode, which held the GIL
        # long enough on the main thread to dip the audio engine.
        led_state = self._led_state
        led_rows = len(led_state)
        pressed = self._pressed
        armed = self._armed
        n_groups = len(self._color_tiers)
        cols = self.columns
        rows = self.rows
        draw_rect = painter.drawRect
        set_brush = painter.setBrush
        col_colors = self._col_colors  # None in normal mode, list[int] in mix mode
        row_colors = self._row_colors
        color_tiers = self._color_tiers
        off_color = self._off_color
        press_overlay = self._press_overlay
        armed_overlay = self._armed_overlay
        rect_cache = self._rect_cache

        for y in range(rows):
            row_leds = led_state[y] if y < led_rows else None
            row_len = len(row_leds) if row_leds is not None else 0
            row_rects = rect_cache[y] if y < len(rect_cache) else None
            if row_rects is None:
                continue

            # Per-row colour tiers (used when not in column-colour mode)
            if col_colors is None:
                row_tier = color_tiers[row_colors[y] % n_groups]
            else:
                row_tier = None  # resolved per-cell below

            for x in range(cols):
                brightness = row_leds[x] if (row_leds is not None and x < row_len) else 0

                if brightness > 0:
                    if col_colors is not None:
                        ci = col_colors[x] % n_groups if x < len(col_colors) else 0
                        tier = color_tiers[ci]
                    else:
                        tier = row_tier
                    if brightness >= 12:
                        color = tier[0]
                    elif brightness >= 8:
                        color = tier[1]
                    elif brightness >= 4:
                        color = tier[2]
                    else:
                        color = tier[3]
                else:
                    color = off_color

                rect = row_rects[x]
                set_brush(color)
                draw_rect(rect)

                if (x, y) in pressed:
                    set_brush(press_overlay)
                    draw_rect(rect)
                elif (x, y) in armed:
                    set_brush(armed_overlay)
                    draw_rect(rect)

        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            x, y = self._xy_from_pos(event.position())
            # Fire all armed buttons simultaneously with the clicked button.
            # Avoid double-firing if the clicked button itself was armed.
            armed_snapshot = list(self._armed)
            self._armed.clear()
            for ax, ay in armed_snapshot:
                if (ax, ay) != (x, y):
                    self._pressed.add((ax, ay))
                    self.button_pressed.emit(ax, ay, 1)
            self._pressed.add((x, y))
            self.button_pressed.emit(x, y, 1)
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            x, y = self._xy_from_pos(event.position())
            # Toggle armed state on right-click (right-click again to disarm).
            if (x, y) in self._armed:
                self._armed.discard((x, y))
            else:
                self._armed.add((x, y))
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            # Release all co-fired buttons at once.
            for bx, by in list(self._pressed):
                self.button_pressed.emit(bx, by, 0)
            self._pressed.clear()
            self.update()

    def contextMenuEvent(self, event):
        # Suppress the default right-click context menu so arming works cleanly.
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        # Support drag across buttons
        pass
