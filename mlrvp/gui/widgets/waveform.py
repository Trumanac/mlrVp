"""Waveform display widget for mlrVp — matched to ch.maxpat display."""

import numpy as np
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient, QMouseEvent

from mlrvp.gui.styles import (
    BG_BLACK, BG_PANEL, SEPARATOR, ACCENT_BLUE, TEXT_SECONDARY, GROUP_COLORS
)


class WaveformWidget(QWidget):
    """Waveform display with playback position and selection overlay.

    Background is black (#000000) matching original waveform~ display.
    Selection boundaries in ACCENT_BLUE, group color for waveform bars.
    Shows column dividers to split the sample into grid-column-sized chunks.
    Highlights the currently playing column/chunk.
    """

    selection_changed = Signal(float, float)  # sel_start, sel_end (0-1)
    position_clicked = Signal(float)  # position (0-1)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._waveform_data: np.ndarray | None = None  # (n, 2) min/max
        self._playback_pos: float = 0.0
        self._sel_start: float = 0.0
        self._sel_end: float = 1.0
        self._group_color_idx: int = 0
        self._playing: bool = False
        self._dragging: str | None = None  # 'start', 'end', or None
        self._filename: str = ""
        self._grid_columns: int = 8  # number of grid columns for dividers
        self._active_column: int = -1  # currently playing column index

        self.setMinimumHeight(60)
        self.setMouseTracking(True)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

    def set_waveform(self, data: np.ndarray | None, filename: str = ""):
        self._waveform_data = data
        self._filename = filename
        self.update()

    def set_playback_position(self, pos: float):
        self._playback_pos = pos
        self.update()

    def set_selection(self, start: float, end: float):
        self._sel_start = max(0.0, min(1.0, start))
        self._sel_end = max(0.0, min(1.0, end))
        self.update()

    def set_group_color(self, idx: int):
        self._group_color_idx = idx % len(GROUP_COLORS)
        self.update()

    def set_playing(self, playing: bool):
        self._playing = playing
        if not playing:
            self._active_column = -1
        self.update()

    def set_grid_columns(self, columns: int):
        """Set the number of grid columns for divider lines."""
        self._grid_columns = max(1, columns)
        self.update()

    def set_active_column(self, col: int):
        """Set the currently playing column index (0-based), -1 for none."""
        self._active_column = col
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()

        # Background — black like original waveform~ display
        painter.fillRect(self.rect(), QColor(BG_BLACK))

        # Selection region pixel positions
        sel_x1 = int(self._sel_start * w)
        sel_x2 = int(self._sel_end * w)
        sel_w = sel_x2 - sel_x1

        # Highlight active column within the selection region
        if self._active_column >= 0 and self._grid_columns > 0 and sel_w > 0:
            col_w = sel_w / self._grid_columns
            active_x = sel_x1 + int(self._active_column * col_w)
            active_w = int(col_w) + 1
            group_color = QColor(GROUP_COLORS[self._group_color_idx])
            group_color.setAlpha(40)
            painter.fillRect(active_x, 0, active_w, h, group_color)

        # Draw waveform — two passes: dim outside selection, bright inside
        if self._waveform_data is not None and len(self._waveform_data) > 0:
            base_color = QColor(GROUP_COLORS[self._group_color_idx])
            num_points = len(self._waveform_data)

            # Pass 1: full width at dim alpha so out-of-loop content stays visible
            dim_color = QColor(base_color)
            dim_color.setAlpha(50)
            painter.setPen(QPen(dim_color, 1))
            for px in range(w):
                idx = min(int(px * num_points / w), num_points - 1)
                peak = max(abs(self._waveform_data[idx, 0]),
                           abs(self._waveform_data[idx, 1]))
                y_top = h - int(peak * h * 0.95)
                painter.drawLine(px, y_top, px, h)

            # Pass 2: selection region only at full alpha
            if sel_x1 < sel_x2:
                bright_color = QColor(base_color)
                bright_color.setAlpha(185)
                painter.setPen(QPen(bright_color, 1))
                painter.setClipRect(sel_x1, 0, sel_x2 - sel_x1, h)
                for px in range(sel_x1, min(sel_x2, w)):
                    idx = min(int(px * num_points / w), num_points - 1)
                    peak = max(abs(self._waveform_data[idx, 0]),
                               abs(self._waveform_data[idx, 1]))
                    y_top = h - int(peak * h * 0.95)
                    painter.drawLine(px, y_top, px, h)
                painter.setClipping(False)

        # Column divider lines within the selection area
        if self._grid_columns > 1 and sel_w > 0:
            div_pen = QPen(QColor(SEPARATOR), 1, Qt.PenStyle.DotLine)
            painter.setPen(div_pen)
            for c in range(1, self._grid_columns):
                dx = sel_x1 + int(c * sel_w / self._grid_columns)
                painter.drawLine(dx, 0, dx, h)

        # Selection boundaries — accent blue lines
        sel_pen = QPen(QColor(ACCENT_BLUE), 2)
        painter.setPen(sel_pen)
        painter.drawLine(sel_x1, 0, sel_x1, h)
        painter.drawLine(sel_x2, 0, sel_x2, h)

        # Playback position cursor
        if self._playing:
            pos_x = int(self._playback_pos * w)
            play_pen = QPen(QColor(255, 255, 255, 200), 2)
            painter.setPen(play_pen)
            painter.drawLine(pos_x, 0, pos_x, h)

        # Filename label
        if self._filename:
            painter.setPen(QColor(TEXT_SECONDARY))
            painter.drawText(4, 12, self._filename)

        # Border
        painter.setPen(QPen(QColor(SEPARATOR), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().x() / self.width()
            # Check if near selection boundaries
            start_x = self._sel_start * self.width()
            end_x = self._sel_end * self.width()
            mx = event.position().x()

            if abs(mx - start_x) < 8:
                self._dragging = 'start'
            elif abs(mx - end_x) < 8:
                self._dragging = 'end'
            else:
                self.position_clicked.emit(pos)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            pos = max(0.0, min(1.0, event.position().x() / self.width()))
            if self._dragging == 'start':
                self._sel_start = min(pos, self._sel_end - 0.01)
            elif self._dragging == 'end':
                self._sel_end = max(pos, self._sel_start + 0.01)
            self.selection_changed.emit(self._sel_start, self._sel_end)
            self.update()
        else:
            # Change cursor near boundaries
            start_x = self._sel_start * self.width()
            end_x = self._sel_end * self.width()
            mx = event.position().x()
            if abs(mx - start_x) < 8 or abs(mx - end_x) < 8:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._dragging:
            self.selection_changed.emit(self._sel_start, self._sel_end)
            self._dragging = None
