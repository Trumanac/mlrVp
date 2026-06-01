"""Waveform display widget for mlrVp — matched to ch.maxpat display."""

import numpy as np
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtCore import Qt, Signal, QRectF, QLine
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient, QMouseEvent, QPixmap

from mlrvp.gui.scale import bind_min_height
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
        self._seq_steps: frozenset[int] = frozenset()  # SEQ-mode enabled steps
        self._wave_cache: QPixmap | None = None  # cached waveform bars pixmap

        bind_min_height(self, 60)
        self.setMouseTracking(True)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

    def set_waveform(self, data: np.ndarray | None, filename: str = ""):
        self._waveform_data = data
        self._filename = filename
        self._wave_cache = None
        self.update()

    def set_playback_position(self, pos: float):
        # Only repaint if the pixel position actually changes
        w = self.width() if self.width() > 0 else 1
        old_px = int(self._playback_pos * w)
        new_px = int(pos * w)
        self._playback_pos = pos
        if old_px != new_px:
            self.update()

    def set_selection(self, start: float, end: float):
        start = max(0.0, min(1.0, start))
        end = max(0.0, min(1.0, end))
        if start > end:
            start, end = end, start
        self._sel_start = start
        self._sel_end = end
        self.update()

    def set_group_color(self, idx: int):
        self._group_color_idx = idx % len(GROUP_COLORS)
        self._wave_cache = None
        self.update()

    def set_playing(self, playing: bool):
        if self._playing == playing:
            return
        self._playing = playing
        if not playing:
            self._active_column = -1
        self.update()

    def set_grid_columns(self, columns: int):
        """Set the number of grid columns for divider lines."""
        self._grid_columns = max(1, columns)
        self._wave_cache = None
        self.update()

    def set_active_column(self, col: int):
        """Set the currently playing column index (0-based), -1 for none."""
        if self._active_column == col:
            return
        self._active_column = col
        self.update()

    def set_seq_steps(self, steps: frozenset[int]):
        """Set enabled SEQ step indices for tint overlay. Pass frozenset() to clear."""
        if steps == self._seq_steps:
            return
        self._seq_steps = steps
        self.update()

    def _rebuild_wave_cache(self, w: int, h: int):
        """Build QPixmaps containing the static waveform bars.

        Two pixmaps are built:
          _wave_cache       — dim bars (alpha 50) on black + border
          _wave_cache_bright — bright bars (alpha 185) on transparent background

        Using two pixmaps avoids creating a per-pixel list of QLine objects in
        paintEvent on every repaint (the bright overlay is blitted with
        setClipRect instead).
        """
        # ---- dim cache ----
        pm = QPixmap(w, h)
        pm.fill(QColor(BG_BLACK))
        p = QPainter(pm)

        # ---- bright cache (transparent background) ----
        bright_pm = QPixmap(w, h)
        bright_pm.fill(Qt.GlobalColor.transparent)
        pb = QPainter(bright_pm)

        if self._waveform_data is not None and len(self._waveform_data) > 0:
            base_color = QColor(GROUP_COLORS[self._group_color_idx])
            num_points = len(self._waveform_data)

            # Vectorized: compute all bar heights at once
            px_arr = np.arange(w)
            indices = np.minimum((px_arr * num_points // w), num_points - 1)
            peaks = np.maximum(
                np.abs(self._waveform_data[indices, 0]),
                np.abs(self._waveform_data[indices, 1]),
            )
            y_tops = (h - (peaks * (h * 0.95)).astype(int)).astype(int)

            # Draw dim bars into dim cache
            dim_color = QColor(base_color)
            dim_color.setAlpha(50)
            p.setPen(QPen(dim_color, 1))
            lines_dim = [QLine(px, int(y_tops[px]), px, h) for px in range(w)]
            p.drawLines(lines_dim)

            # Draw bright bars into bright (transparent) cache
            bright_color = QColor(base_color)
            bright_color.setAlpha(185)
            pb.setPen(QPen(bright_color, 1))
            pb.drawLines(lines_dim)   # same geometry, different colour/alpha

        # Border on dim cache only (border is never overwritten by bright blit)
        p.setPen(QPen(QColor(SEPARATOR), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        p.end()
        pb.end()
        self._wave_cache = pm
        self._wave_cache_bright = bright_pm
        self._wave_cache_size = (w, h)

    def paintEvent(self, event):
        w = self.width()
        h = self.height()
        if w < 1 or h < 1:
            return

        # Rebuild cache if invalidated or widget resized
        if (self._wave_cache is None
                or getattr(self, '_wave_cache_size', None) != (w, h)):
            self._rebuild_wave_cache(w, h)

        painter = QPainter(self)

        # Blit cached waveform (bars + border)
        painter.drawPixmap(0, 0, self._wave_cache)

        sel_x1 = int(self._sel_start * w)
        sel_x2 = int(self._sel_end * w)
        sel_w = sel_x2 - sel_x1

        # Draw selection-region bright waveform bars on top of dim cache.
        # Blit the pre-built bright pixmap restricted to the selection clip —
        # this replaces the O(sel_width) QLine list creation that used to
        # run on every paintEvent at ~60 fps during playback.
        bright_pm = getattr(self, '_wave_cache_bright', None)
        if bright_pm is not None and sel_x1 < sel_x2:
            painter.setClipRect(sel_x1, 0, sel_x2 - sel_x1, h)
            painter.drawPixmap(0, 0, bright_pm)
            painter.setClipping(False)

        # SEQ mode: tint enabled (toggled-on) steps before dividers are drawn
        if self._seq_steps and self._grid_columns > 0 and sel_w > 0:
            col_w = sel_w / self._grid_columns
            step_color = QColor(GROUP_COLORS[self._group_color_idx])
            step_color.setAlpha(30)
            for sc in self._seq_steps:
                if 0 <= sc < self._grid_columns and sc != self._active_column:
                    sx = sel_x1 + int(sc * col_w)
                    sw = int(col_w) + 1
                    painter.fillRect(sx, 0, sw, h, step_color)

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
        sel_w = sel_x2 - sel_x1

        # Highlight active column within the selection region (changes often)
        # Use brighter alpha when a SEQ step is also enabled to stay prominent
        if self._active_column >= 0 and self._grid_columns > 0 and sel_w > 0:
            col_w = sel_w / self._grid_columns
            active_x = sel_x1 + int(self._active_column * col_w)
            active_w = int(col_w) + 1
            group_color = QColor(GROUP_COLORS[self._group_color_idx])
            group_color.setAlpha(65 if self._active_column in self._seq_steps else 40)
            painter.fillRect(active_x, 0, active_w, h, group_color)

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

        painter.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            w = self.width()
            if w < 1:
                return
            pos = event.position().x() / w
            # Check if near selection boundaries
            start_x = self._sel_start * w
            end_x = self._sel_end * w
            mx = event.position().x()

            if abs(mx - start_x) < 8:
                self._dragging = 'start'
            elif abs(mx - end_x) < 8:
                self._dragging = 'end'
            else:
                self.position_clicked.emit(pos)

    def mouseMoveEvent(self, event: QMouseEvent):
        w = self.width()
        if w < 1:
            return
        if self._dragging:
            pos = max(0.0, min(1.0, event.position().x() / w))
            if self._dragging == 'start':
                self._sel_start = min(pos, self._sel_end - 0.01)
            elif self._dragging == 'end':
                self._sel_end = max(pos, self._sel_start + 0.01)
            self.selection_changed.emit(self._sel_start, self._sel_end)
            self.update()
        else:
            # Change cursor near boundaries
            start_x = self._sel_start * w
            end_x = self._sel_end * w
            mx = event.position().x()
            if abs(mx - start_x) < 8 or abs(mx - end_x) < 8:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._dragging:
            self.selection_changed.emit(self._sel_start, self._sel_end)
            self.update()
        # Always clear drag state so a non-left release after a left press
        # (multi-button mice / tablets) cannot leave us stuck mid-drag.
        self._dragging = None
