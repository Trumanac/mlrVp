"""Tools view — Record + Sample Slice tabs for mlrVp."""

import os
import math
import threading
import numpy as np
import soundfile as sf

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QFrame, QComboBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QCheckBox, QGridLayout, QSizePolicy, QFileDialog,
    QMenu, QScrollArea, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QTimer, QMimeData, QUrl
from PySide6.QtGui import QPainter, QColor, QPen, QMouseEvent, QWheelEvent, QDrag

from mlrvp.gui.styles import (
    ACCENT_GOLD, BG_BLACK, BG_PANEL, SEPARATOR, TEXT_SECONDARY, TEXT_PRIMARY,
    ACCENT_BLUE, RED
)
from mlrvp.gui.widgets.fader import HorizontalFader

AUDIO_EXTENSIONS = {'.wav', '.aiff', '.aif', '.flac', '.ogg', '.mp3'}


class SliceWaveformWidget(QWidget):
    """Interactive waveform display with draggable start/end markers."""

    selection_changed = Signal(float, float)  # start_frac, end_frac (0.0-1.0)
    region_clicked = Signal(int)  # region index clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(80)
        self.setMaximumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._mono: np.ndarray | None = None  # raw mono audio for dynamic rendering
        self._peaks: list[float] = []  # coarse overview peaks
        self._transients: np.ndarray = np.array([], dtype=int)  # sample indices
        self._regions: list[tuple[int, int]] = []  # (start_sample, end_sample) for chop bands
        self._current_region: int = -1  # highlighted region index
        self._start = 0.0
        self._end = 1.0
        self._playhead = 0.0
        self._dragging = None  # 'start', 'end', 'slide', or None
        self._panning = False  # right-click drag pan
        self._pan_last_x = 0
        self._num_frames = 0
        self._sample_rate = 44100
        self._locked_span = 0.0  # >0 means fixed selection width (fraction)
        # Zoom viewport (fraction of full sample visible)
        self._view_start = 0.0  # left edge of viewport (0.0-1.0)
        self._view_end = 1.0    # right edge of viewport (0.0-1.0)

    def set_audio(self, data: np.ndarray, sr: int):
        """Load audio data and store raw mono for dynamic-detail rendering."""
        self._sample_rate = sr
        self._num_frames = len(data)
        mono = data.mean(axis=1) if data.ndim == 2 else data
        self._mono = mono.astype(np.float32)
        # Coarse overview peaks (for quick full-view drawing)
        num_bins = max(self.width(), 400)
        bin_size = max(1, len(mono) // num_bins)
        self._peaks = []
        for i in range(0, len(mono), bin_size):
            chunk = mono[i:i + bin_size]
            self._peaks.append(float(np.abs(chunk).max()))
        self._start = 0.0
        self._end = 1.0
        self._playhead = 0.0
        self._view_start = 0.0
        self._view_end = 1.0
        self.update()

    def set_transients(self, indices: np.ndarray):
        """Store transient sample-indices for overlay display."""
        self._transients = indices
        self.update()

    def set_playhead(self, frac: float):
        self._playhead = max(0.0, min(1.0, frac))
        self.update()

    def clear(self):
        self._mono = None
        self._peaks = []
        self._transients = np.array([], dtype=int)
        self._regions = []
        self._current_region = -1
        self._num_frames = 0
        self._start = 0.0
        self._end = 1.0
        self._playhead = 0.0
        self._view_start = 0.0
        self._view_end = 1.0
        self.update()

    @property
    def start_frac(self) -> float:
        return self._start

    @property
    def end_frac(self) -> float:
        return self._end

    def set_selection(self, start: float, end: float):
        self._start = max(0.0, min(1.0, start))
        self._end = max(0.0, min(1.0, end))
        self.update()
        self.selection_changed.emit(self._start, self._end)

    def set_regions(self, regions: list):
        """Set chop region list: [(start_sample, end_sample), ...]."""
        self._regions = regions
        self._current_region = -1
        self.update()

    def set_current_region(self, idx: int):
        self._current_region = idx
        self.update()

    def region_at_frac(self, frac: float) -> int:
        """Return the region index at the given fraction, or -1."""
        if not self._regions or self._num_frames == 0:
            return -1
        sample = int(frac * self._num_frames)
        for i, (rs, re) in enumerate(self._regions):
            if rs <= sample < re:
                return i
        return -1

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2.0

        # Background
        p.fillRect(self.rect(), QColor(BG_BLACK))

        if self._mono is None or len(self._mono) == 0:
            p.setPen(QColor(TEXT_SECONDARY))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drop audio file here or click LOAD")
            p.end()
            return

        vs = self._view_start
        ve = self._view_end
        vspan = max(ve - vs, 1e-9)
        total = len(self._mono)

        def frac_to_x(f: float) -> int:
            return int((f - vs) / vspan * w)

        def x_to_frac(x: float) -> float:
            return vs + (x / max(w, 1)) * vspan

        # Draw selection region highlight
        sx = frac_to_x(self._start)
        ex = frac_to_x(self._end)
        p.fillRect(sx, 0, ex - sx, h, QColor(ACCENT_BLUE + "25"))

        # ── Chop region bands (alternating shading) ──
        if self._regions and total > 0:
            colors = [QColor("#ffffff10"), QColor("#ffffff1a")]
            highlight_color = QColor(ACCENT_GOLD + "40")
            for ri, (rs, re) in enumerate(self._regions):
                rf_s = rs / total
                rf_e = re / total
                if rf_e < vs or rf_s > ve:
                    continue
                rx0 = frac_to_x(rf_s)
                rx1 = frac_to_x(rf_e)
                if ri == self._current_region:
                    p.fillRect(rx0, 0, rx1 - rx0, h, highlight_color)
                else:
                    p.fillRect(rx0, 0, rx1 - rx0, h, colors[ri % 2])
                # Region boundary line
                p.setPen(QPen(QColor("#ffffff30"), 1))
                p.drawLine(rx0, 0, rx0, h)

        # ── Waveform — dynamic detail based on zoom ──
        pen_normal = QPen(QColor(SEPARATOR), 1)
        pen_selected = QPen(QColor(ACCENT_BLUE), 1)

        # Visible sample range
        s_start = max(0, int(vs * total))
        s_end = min(total, int(ve * total))
        visible_samples = s_end - s_start

        if visible_samples <= 0:
            pass
        elif visible_samples <= w * 2:
            # Zoomed in: draw per-sample waveform lines
            for px in range(w):
                frac = x_to_frac(px)
                idx = int(frac * total)
                idx = max(s_start, min(s_end - 1, idx))
                val = float(self._mono[idx])
                bar_h = abs(val) * mid * 0.9
                p.setPen(pen_selected if self._start <= frac <= self._end else pen_normal)
                p.drawLine(px, int(mid - bar_h), px, int(mid + bar_h))
        else:
            # Zoomed out — vectorized peak computation per pixel
            edges = np.linspace(vs, ve, w + 1)
            indices = np.clip((edges * total).astype(int), 0, total)
            for px in range(w):
                i0 = indices[px]
                i1 = max(i0 + 1, indices[px + 1])
                peak = float(np.abs(self._mono[i0:i1]).max())
                bar_h = peak * mid * 0.9
                mid_frac = (edges[px] + edges[px + 1]) * 0.5
                p.setPen(pen_selected if self._start <= mid_frac <= self._end else pen_normal)
                p.drawLine(px, int(mid - bar_h), px, int(mid + bar_h))

        # ── Transient markers (density-adaptive) ──
        if len(self._transients) > 0:
            pen_t = QPen(QColor("#ff6633"), 1, Qt.PenStyle.DotLine)
            p.setPen(pen_t)
            # Minimum pixel spacing between shown transients
            min_px_gap = 8
            min_frac_gap = (min_px_gap / max(w, 1)) * vspan
            last_shown_frac = -1.0
            for tidx in self._transients:
                tfrac = tidx / total
                if tfrac < vs or tfrac > ve:
                    continue
                if tfrac - last_shown_frac < min_frac_gap:
                    continue
                tx = frac_to_x(tfrac)
                p.drawLine(tx, 0, tx, h)
                last_shown_frac = tfrac

        # Start marker (green line)
        p.setPen(QPen(QColor("#44cc44"), 2))
        p.drawLine(sx, 0, sx, h)

        # End marker (red line)
        p.setPen(QPen(QColor(RED), 2))
        p.drawLine(ex, 0, ex, h)

        # Playhead
        if self._playhead > 0:
            px = frac_to_x(self._playhead)
            p.setPen(QPen(QColor(TEXT_PRIMARY), 1, Qt.PenStyle.DashLine))
            p.drawLine(px, 0, px, h)

        p.end()

    def mousePressEvent(self, event: QMouseEvent):
        if self._mono is None:
            return
        x = event.position().x()
        w = self.width()
        vs = self._view_start
        ve = self._view_end
        vspan = max(ve - vs, 1e-9)

        # Right-click: start panning
        if event.button() == Qt.MouseButton.RightButton:
            self._panning = True
            self._pan_last_x = x
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        frac = vs + (x / max(w, 1)) * vspan

        # If chop regions are active, check for region click first
        if self._regions:
            ri = self.region_at_frac(frac)
            if ri >= 0:
                self._current_region = ri
                self.update()
                self.region_clicked.emit(ri)
                return

        sx_px = (self._start - vs) / vspan * w
        ex_px = (self._end - vs) / vspan * w

        if self._locked_span > 0:
            self._dragging = 'slide'
            self._slide_offset = frac - self._start
        else:
            if abs(x - sx_px) < abs(x - ex_px) and abs(x - sx_px) < 15:
                self._dragging = 'start'
            elif abs(x - ex_px) < 15:
                self._dragging = 'end'
            elif frac < (self._start + self._end) / 2:
                self._dragging = 'start'
            else:
                self._dragging = 'end'
        self._apply_drag(frac)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._mono is None:
            return
        x = event.position().x()
        w = max(self.width(), 1)
        vs = self._view_start
        ve = self._view_end
        vspan = max(ve - vs, 1e-9)

        # Right-click pan
        if self._panning:
            dx = x - self._pan_last_x
            frac_shift = -(dx / w) * vspan
            new_vs = vs + frac_shift
            new_ve = ve + frac_shift
            if new_vs < 0.0:
                new_vs = 0.0
                new_ve = min(vspan, 1.0)
            if new_ve > 1.0:
                new_ve = 1.0
                new_vs = max(1.0 - vspan, 0.0)
            self._view_start = new_vs
            self._view_end = new_ve
            self._pan_last_x = x
            self.update()
            return

        # Left-click drag selection
        if self._dragging:
            frac = vs + (x / w) * vspan
            self._apply_drag(frac)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton and self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        # Notify parent of which marker was last dragged (for active-marker tracking)
        if self._dragging in ('start', 'end'):
            parent = self.parent()
            while parent and not isinstance(parent, SlicePanel):
                parent = parent.parent()
            if parent:
                parent._active_marker = self._dragging
                # Apply ZX/Grid snap on release
                if parent._zx_snap_enabled or parent._grid_snap_enabled:
                    frac = self._start if self._dragging == 'start' else self._end
                    if parent._grid_snap_enabled:
                        frac = parent._snap_frac_to_grid(frac)
                    if parent._zx_snap_enabled:
                        frac = parent._snap_frac_to_zx(frac)
                    if self._dragging == 'start':
                        self._start = min(frac, self._end - 0.001)
                    else:
                        self._end = max(frac, self._start + 0.001)
                    self.update()
                    self.selection_changed.emit(self._start, self._end)
        self._dragging = None

    def _apply_drag(self, frac: float):
        frac = max(0.0, min(1.0, frac))
        if self._dragging == 'slide' and self._locked_span > 0:
            # Slide the fixed-size selection
            new_start = frac - self._slide_offset
            new_start = max(0.0, min(1.0 - self._locked_span, new_start))
            self._start = new_start
            self._end = new_start + self._locked_span
        elif self._dragging == 'start':
            self._start = min(frac, self._end - 0.001)
        elif self._dragging == 'end':
            self._end = max(frac, self._start + 0.001)
        self.update()
        self.selection_changed.emit(self._start, self._end)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS:
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS:
                # Parent SlicePanel handles loading
                parent = self.parent()
                while parent and not isinstance(parent, SlicePanel):
                    parent = parent.parent()
                if parent:
                    parent.load_file(path)
                break

    def wheelEvent(self, event: QWheelEvent):
        """Scroll-wheel zoom centered on mouse position, clamped to 0.0–1.0."""
        if self._mono is None:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        x = event.position().x()
        w = max(self.width(), 1)
        vs = self._view_start
        ve = self._view_end
        vspan = ve - vs
        # Fraction within viewport where mouse is
        mouse_frac = vs + (x / w) * vspan
        zoom_factor = 0.85 if delta > 0 else 1.0 / 0.85
        new_span = max(0.005, min(1.0, vspan * zoom_factor))
        # Anchor zoom on mouse position
        ratio = (mouse_frac - vs) / max(vspan, 1e-9)
        new_vs = mouse_frac - ratio * new_span
        new_ve = new_vs + new_span
        # Clamp to 0.0–1.0
        if new_vs < 0.0:
            new_vs = 0.0
            new_ve = min(new_span, 1.0)
        if new_ve > 1.0:
            new_ve = 1.0
            new_vs = max(1.0 - new_span, 0.0)
        self._view_start = new_vs
        self._view_end = new_ve
        self.update()

    def keyPressEvent(self, event):
        """Forward keyboard shortcuts to parent SlicePanel."""
        parent = self.parent()
        while parent and not isinstance(parent, SlicePanel):
            parent = parent.parent()
        if parent and hasattr(parent, '_waveform_key'):
            if parent._waveform_key(event):
                event.accept()
                return
        super().keyPressEvent(event)


class SlicePanel(QFrame):
    """Sample slicing tool — load, set regions, slice and save."""

    slice_saved = Signal(str)  # emits filepath of saved slice
    load_to_channel = Signal(str, int)  # filepath, channel_index (0-based)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._audio_data = None  # np.ndarray
        self._sample_rate = 44100
        self._source_path = ""
        self._playing = False
        self._fit_speed_ratio = 1.0   # speed correction from FIT mode
        self._ideal_bar_samples = 0   # exact sample count for perfect bars
        self._fit_active = False      # whether last AUTO used FIT
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(30)
        self._play_timer.timeout.connect(self._update_playhead)
        self._play_pos = 0
        self._loop_segment_len = 0
        self._buf_gen = 0  # tracks preview service buffer generation
        self._output_device: int = -1  # -1 = default; set from app config
        self._preview_service = None  # set via set_preview_service()
        self._slice_regions: list[tuple[int, int]] = []  # (start_sample, end_sample) pairs
        self._current_region_idx: int = -1  # selected region for audition cycling
        self._chop_mode: int = 0  # 0=Manual, 1=Transient, 2=Equal, 3=BPM Grid
        self._undo_stack: list[tuple[float, float]] = []  # (start_frac, end_frac)
        self._redo_stack: list[tuple[float, float]] = []
        self._active_marker: str = 'end'  # 'start' or 'end' — last dragged/clicked
        self._zx_snap_enabled: bool = False
        self._grid_snap_enabled: bool = False
        self._suppress_undo: bool = False  # prevent undo push during programmatic moves

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        # Title
        title = QLabel("SAMPLE SLICER")
        title.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {TEXT_SECONDARY};")
        layout.addWidget(title)

        # File selector row
        file_row = QHBoxLayout()
        file_row.setSpacing(4)
        self._file_combo = QComboBox()
        self._file_combo.setMinimumWidth(120)
        self._file_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._file_combo.currentIndexChanged.connect(self._on_file_selected)
        file_row.addWidget(self._file_combo)
        self._load_btn = QPushButton("LOAD")
        self._load_btn.setFixedSize(50, 24)
        self._load_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-weight: bold; font-size: 9px; border: 1px solid {SEPARATOR}; }}"
            f"QPushButton:hover {{ background: #333; }}"
        )
        self._load_btn.clicked.connect(self._on_load_click)
        file_row.addWidget(self._load_btn)
        layout.addLayout(file_row)

        # Waveform display
        self._waveform = SliceWaveformWidget()
        self._waveform.selection_changed.connect(self._on_selection_changed)
        self._waveform.region_clicked.connect(self._on_region_clicked)
        layout.addWidget(self._waveform, stretch=1)

        # Time info row — numeric spinboxes + ZX/GRID snap toggles
        info_row = QHBoxLayout()
        info_row.setSpacing(6)
        spin_style = (
            f"QDoubleSpinBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 1px 2px; }}"
        )
        lbl_style = f"color: {TEXT_SECONDARY}; font-size: 9px;"

        _sl = QLabel("Start:")
        _sl.setStyleSheet(lbl_style)
        info_row.addWidget(_sl)
        self._start_spin = QDoubleSpinBox()
        self._start_spin.setDecimals(3)
        self._start_spin.setSuffix("s")
        self._start_spin.setRange(0.0, 9999.0)
        self._start_spin.setSingleStep(0.001)
        self._start_spin.setFixedWidth(80)
        self._start_spin.setStyleSheet(spin_style)
        self._start_spin.setToolTip("Selection start time in seconds (3 decimal places)")
        self._start_spin.valueChanged.connect(self._on_start_spin_changed)
        info_row.addWidget(self._start_spin)

        _el = QLabel("End:")
        _el.setStyleSheet(lbl_style)
        info_row.addWidget(_el)
        self._end_spin = QDoubleSpinBox()
        self._end_spin.setDecimals(3)
        self._end_spin.setSuffix("s")
        self._end_spin.setRange(0.0, 9999.0)
        self._end_spin.setSingleStep(0.001)
        self._end_spin.setFixedWidth(80)
        self._end_spin.setStyleSheet(spin_style)
        self._end_spin.setToolTip("Selection end time in seconds")
        self._end_spin.valueChanged.connect(self._on_end_spin_changed)
        info_row.addWidget(self._end_spin)

        _dl = QLabel("Dur:")
        _dl.setStyleSheet(lbl_style)
        info_row.addWidget(_dl)
        self._dur_spin = QDoubleSpinBox()
        self._dur_spin.setDecimals(3)
        self._dur_spin.setSuffix("s")
        self._dur_spin.setRange(0.0, 9999.0)
        self._dur_spin.setSingleStep(0.001)
        self._dur_spin.setFixedWidth(80)
        self._dur_spin.setStyleSheet(spin_style)
        self._dur_spin.setToolTip("Selection duration in seconds")
        self._dur_spin.valueChanged.connect(self._on_dur_spin_changed)
        info_row.addWidget(self._dur_spin)

        # ZX snap toggle
        snap_btn_style = (
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-weight: bold; font-size: 9px; border: 1px solid {SEPARATOR}; "
            f"padding: 1px 6px; }}"
            f"QPushButton:hover {{ background: #333; }}"
        )
        self._zx_btn = QPushButton("ZX")
        self._zx_btn.setCheckable(True)
        self._zx_btn.setFixedSize(32, 22)
        self._zx_btn.setToolTip("Zero-crossing snap (auto-snap markers on release)")
        self._zx_btn.setStyleSheet(snap_btn_style)
        self._zx_btn.toggled.connect(self._on_zx_toggled)
        info_row.addWidget(self._zx_btn)

        # Grid snap toggle
        self._grid_btn = QPushButton("GRID")
        self._grid_btn.setCheckable(True)
        self._grid_btn.setFixedSize(40, 22)
        self._grid_btn.setToolTip("BPM-grid snap (snap markers to beat subdivisions)")
        self._grid_btn.setStyleSheet(snap_btn_style)
        self._grid_btn.toggled.connect(self._on_grid_toggled)
        info_row.addWidget(self._grid_btn)

        info_row.addStretch()
        layout.addLayout(info_row)

        # Transport row
        transport_row = QHBoxLayout()
        transport_row.setSpacing(4)
        btn_style = (
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-weight: bold; font-size: 10px; border: 1px solid {SEPARATOR}; "
            f"padding: 2px 8px; }}"
            f"QPushButton:hover {{ background: #333; }}"
        )
        self._play_btn = QPushButton("\u25B6")  # play triangle
        self._play_btn.setFixedSize(36, 26)
        self._play_btn.setStyleSheet(btn_style)
        self._play_btn.setToolTip("Play / Pause full sample (Space)")
        self._play_btn.clicked.connect(self._toggle_play)
        transport_row.addWidget(self._play_btn)

        self._stop_btn = QPushButton("\u25A0")  # stop square
        self._stop_btn.setFixedSize(36, 26)
        self._stop_btn.setStyleSheet(btn_style)
        self._stop_btn.setToolTip("Stop playback")
        self._stop_btn.clicked.connect(self._stop_play)
        transport_row.addWidget(self._stop_btn)

        self._play_sel_btn = QPushButton("\u25B6 Sel")
        self._play_sel_btn.setFixedSize(50, 26)
        self._play_sel_btn.setStyleSheet(btn_style)
        self._play_sel_btn.setToolTip(
            "Play the selected region through the cue output.\n"
            "With Loop on, moving the selection updates the loop\n"
            "region at the end of the current iteration."
        )
        self._play_sel_btn.clicked.connect(self._play_selection)
        transport_row.addWidget(self._play_sel_btn)

        # Loop toggle
        self._loop_cb = QCheckBox("Loop")
        self._loop_cb.setChecked(True)
        self._loop_cb.setToolTip(
            "Loop playback continuously.\n"
            "Moving the selection while looping queues the new region;\n"
            "it takes effect when the current loop iteration ends."
        )
        self._loop_cb.setStyleSheet(
            f"QCheckBox {{ color: {TEXT_PRIMARY}; font-size: 9px; spacing: 3px; }}"
        )
        transport_row.addWidget(self._loop_cb)

        # Separator
        _tsep = QFrame()
        _tsep.setFixedSize(1, 22)
        _tsep.setStyleSheet(f"background: {SEPARATOR};")
        transport_row.addWidget(_tsep)

        # Bar-count selector for auto-loop
        self._bar_combo = QComboBox()
        self._bar_combo.setFixedSize(60, 24)
        self._bar_combo.setStyleSheet(
            f"QComboBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 1px 2px; }}"
        )
        self._bar_combo.addItem("None", 0)
        for bars in (1, 2, 4, 8, 16):
            self._bar_combo.addItem(f"{bars} bar" if bars == 1 else f"{bars} bars", bars)
        self._bar_combo.setCurrentIndex(0)  # default None
        self._bar_combo.setToolTip("Target bar count for AUTO snap and SLICE + SAVE")
        self._bar_combo.currentIndexChanged.connect(self._on_bar_changed)
        transport_row.addWidget(self._bar_combo)

        # Auto Loop button — snaps selection to musical loop
        self._auto_loop_btn = QPushButton("AUTO")
        self._auto_loop_btn.setFixedSize(46, 26)
        self._auto_loop_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_BLUE}; color: {BG_BLACK}; "
            f"font-weight: bold; font-size: 9px; border: none; }}"
            f"QPushButton:hover {{ background: #5ca0d3; }}"
        )
        self._auto_loop_btn.setToolTip("Auto-snap selection to nearest transients for a perfect musical loop")
        self._auto_loop_btn.clicked.connect(self._auto_loop)
        transport_row.addWidget(self._auto_loop_btn)

        # FIT checkbox — snap end to transient + resample to exact bar length
        self._fit_cb = QCheckBox("FIT")
        self._fit_cb.setChecked(True)
        self._fit_cb.setToolTip(
            "Snap both start AND end to transients, then resample on save\n"
            "so the slice is exactly the right length for seamless looping."
        )
        self._fit_cb.setStyleSheet(
            f"QCheckBox {{ color: {ACCENT_GOLD}; font-size: 9px; font-weight: bold; spacing: 3px; }}"
        )
        transport_row.addWidget(self._fit_cb)

        # Separator
        _bsep = QFrame()
        _bsep.setFixedSize(1, 22)
        _bsep.setStyleSheet(f"background: {SEPARATOR};")
        transport_row.addWidget(_bsep)

        # BPM override spinbox (0 = auto-detect)
        bpm_lbl = QLabel("BPM:")
        bpm_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        transport_row.addWidget(bpm_lbl)
        self._bpm_spin = QDoubleSpinBox()
        self._bpm_spin.setRange(0.0, 300.0)
        self._bpm_spin.setDecimals(1)
        self._bpm_spin.setValue(0.0)
        self._bpm_spin.setSpecialValueText("auto")
        self._bpm_spin.setSingleStep(0.5)
        self._bpm_spin.setFixedSize(58, 24)
        self._bpm_spin.setToolTip("Set target BPM (0 = auto-detect from audio)")
        self._bpm_spin.setStyleSheet(
            f"QDoubleSpinBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 1px 2px; }}"
        )
        transport_row.addWidget(self._bpm_spin)

        transport_row.addStretch()
        layout.addLayout(transport_row)

        # ── Cue output row ───────────────────────────────────────
        cue_row = QHBoxLayout()
        cue_row.setSpacing(4)

        cue_vol_label = QLabel("VOL")
        cue_vol_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9px; font-weight: bold;"
        )
        cue_row.addWidget(cue_vol_label)

        self._cue_vol_fader = HorizontalFader(
            color=ACCENT_BLUE, default_value=0.5
        )
        self._cue_vol_fader.setFixedSize(80, 20)
        self._cue_vol_fader.set_value(0.5)  # 100 % (maps 0-1 → 0-200%)
        self._cue_vol_fader.setToolTip("Cue preview volume (0–200%)")
        self._cue_vol_fader.value_changed.connect(self._on_cue_vol_changed)
        cue_row.addWidget(self._cue_vol_fader)

        cue_dev_lbl = QLabel("Dev:")
        cue_dev_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        cue_row.addWidget(cue_dev_lbl)

        self._cue_device_combo = QComboBox()
        self._cue_device_combo.setFixedHeight(22)
        self._cue_device_combo.setMinimumWidth(100)
        self._cue_device_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._cue_device_combo.setStyleSheet(
            f"QComboBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 1px 2px; }}"
        )
        self._cue_device_combo.currentIndexChanged.connect(self._on_cue_device_changed)
        self._cue_device_combo.setToolTip("Select the audio device for cue/headphone preview")
        cue_row.addWidget(self._cue_device_combo)

        ch_lbl = QLabel("Ch:")
        ch_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        cue_row.addWidget(ch_lbl)

        self._cue_ch_combo = QComboBox()
        self._cue_ch_combo.setFixedSize(50, 22)
        self._cue_ch_combo.setStyleSheet(
            f"QComboBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 1px 2px; }}"
        )
        self._cue_ch_combo.addItem("1/2", 0)
        self._cue_ch_combo.setToolTip("Output channel pair on the cue device")
        self._cue_ch_combo.currentIndexChanged.connect(self._on_cue_ch_changed)
        cue_row.addWidget(self._cue_ch_combo)

        mode_lbl = QLabel("Mode:")
        mode_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        cue_row.addWidget(mode_lbl)

        self._cue_mode_combo = QComboBox()
        self._cue_mode_combo.setFixedSize(80, 22)
        self._cue_mode_combo.setStyleSheet(
            f"QComboBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 1px 2px; }}"
        )
        self._cue_mode_combo.addItem("CUE ONLY", 0)
        self._cue_mode_combo.addItem("CUE+MAIN", 1)
        self._cue_mode_combo.addItem("OFF", 2)
        self._cue_mode_combo.setToolTip(
            "CUE ONLY: preview on cue device only\n"
            "CUE+MAIN: play on both cue and main output\n"
            "OFF: disable preview playback"
        )
        self._cue_mode_combo.currentIndexChanged.connect(self._on_cue_mode_changed)
        cue_row.addWidget(self._cue_mode_combo)

        self._cue_status = QLabel("")
        self._cue_status.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        cue_row.addWidget(self._cue_status)

        cue_row.addStretch()
        layout.addLayout(cue_row)

        # ── Chop row ─────────────────────────────────────────────
        chop_row = QHBoxLayout()
        chop_row.setSpacing(4)

        chop_lbl = QLabel("SLICE MODE:")
        chop_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9px; font-weight: bold;"
        )
        chop_row.addWidget(chop_lbl)

        self._chop_mode_combo = QComboBox()
        self._chop_mode_combo.setFixedSize(80, 22)
        self._chop_mode_combo.setStyleSheet(
            f"QComboBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 1px 2px; }}"
        )
        self._chop_mode_combo.addItem("Manual", 0)
        self._chop_mode_combo.addItem("Transient", 1)
        self._chop_mode_combo.addItem("Equal", 2)
        self._chop_mode_combo.addItem("BPM Grid", 3)
        self._chop_mode_combo.setToolTip(
            "Manual: click waveform to set markers by hand\n"
            "Transient: auto-detect transient hits\n"
            "Equal: divide into N equal-length slices\n"
            "BPM Grid: divide at beat boundaries"
        )
        self._chop_mode_combo.currentIndexChanged.connect(self._on_chop_mode_changed)
        chop_row.addWidget(self._chop_mode_combo)

        slices_lbl = QLabel("Slices:")
        slices_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        chop_row.addWidget(slices_lbl)

        self._slices_spin = QSpinBox()
        self._slices_spin.setRange(2, 128)
        self._slices_spin.setValue(8)
        self._slices_spin.setFixedSize(50, 22)
        self._slices_spin.setStyleSheet(
            f"QSpinBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 1px 2px; }}"
        )
        self._slices_spin.setEnabled(False)
        self._slices_spin.setToolTip("Number of equal slices (Equal mode only)")
        chop_row.addWidget(self._slices_spin)

        sens_lbl = QLabel("Sens:")
        sens_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        chop_row.addWidget(sens_lbl)

        self._sens_fader = HorizontalFader(color=ACCENT_GOLD, default_value=0.5)
        self._sens_fader.setFixedSize(70, 20)
        self._sens_fader.set_value(0.5)
        self._sens_fader.setEnabled(False)
        self._sens_fader.setToolTip("Transient detection sensitivity (Transient mode only)")
        chop_row.addWidget(self._sens_fader)

        self._chop_btn = QPushButton("CHOP")
        self._chop_btn.setFixedSize(50, 22)
        self._chop_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_BLUE}; color: {BG_BLACK}; "
            f"font-weight: bold; font-size: 9px; border: none; }}"
            f"QPushButton:hover {{ background: #5ca0d3; }}"
        )
        self._chop_btn.clicked.connect(self._run_chop)
        self._chop_btn.setToolTip("Run auto-chop with the selected mode and settings")
        self._chop_btn.setVisible(False)
        chop_row.addWidget(self._chop_btn)

        self._region_count_lbl = QLabel("")
        self._region_count_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        chop_row.addWidget(self._region_count_lbl)

        chop_row.addStretch()
        layout.addLayout(chop_row)

        # Slice controls row
        slice_row = QHBoxLayout()
        slice_row.setSpacing(4)
        slice_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("slice_name or template: {source}_slice{n:02d}")
        self._name_edit.setToolTip(
            "Name for the saved slice. Use {source} for the source filename\n"
            "and {n:02d} for auto-numbering (e.g. kick_slice01, kick_slice02)."
        )
        self._name_edit.setStyleSheet(
            f"background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {SEPARATOR}; padding: 2px 4px;"
        )
        slice_row.addWidget(self._name_edit, stretch=1)

        self._slice_btn = QPushButton("SLICE + SAVE")
        self._slice_btn.setFixedSize(90, 26)
        self._slice_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; "
            f"font-weight: bold; font-size: 10px; border: none; }}"
            f"QPushButton:hover {{ background: #e6ca6b; }}"
        )
        self._slice_btn.clicked.connect(self._do_slice)
        self._slice_btn.setToolTip(
            "Save the selected region as a WAV file to data/slices/.\n"
            "With FIT enabled, the slice is resampled to an exact bar length."
        )
        slice_row.addWidget(self._slice_btn)

        self._export_all_btn = QPushButton("EXPORT ALL")
        self._export_all_btn.setFixedSize(80, 26)
        self._export_all_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_BLUE}; color: {BG_BLACK}; "
            f"font-weight: bold; font-size: 9px; border: none; }}"
            f"QPushButton:hover {{ background: #5ca0d3; }}"
        )
        self._export_all_btn.clicked.connect(self._export_all_slices)
        self._export_all_btn.setToolTip("Export all chop regions as separate WAV files")
        self._export_all_btn.setVisible(False)
        slice_row.addWidget(self._export_all_btn)
        layout.addLayout(slice_row)

        # Status
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        layout.addWidget(self._status)

        # Slice library at bottom
        self._library = SliceLibraryPanel()
        self._library.load_requested.connect(self.load_file)
        self._library.load_to_channel.connect(self._on_library_load_to_channel)
        self._library.setMinimumHeight(80)
        layout.addWidget(self._library, stretch=1)

        # Refresh library when a new slice is saved
        self.slice_saved.connect(lambda _: self._library.refresh())

    def load_file(self, path: str):
        """Load an audio file for slicing."""
        if not os.path.isfile(path):
            return
        try:
            data, sr = sf.read(path, dtype='float32', always_2d=True)
        except Exception as e:
            self._status.setText(f"Error: {e}")
            return
        self._audio_data = data
        self._sample_rate = sr
        self._source_path = path
        self._waveform.set_audio(data, sr)
        self._waveform.set_transients(self._detect_transients())
        # Clear any previous chop regions
        self._clear_regions()
        # Re-run chop if a non-manual mode is active
        if self._chop_mode != 0:
            self._run_chop()
        # Add to combo if not present
        name = os.path.basename(path)
        found = False
        for i in range(self._file_combo.count()):
            if self._file_combo.itemData(i) == path:
                self._file_combo.setCurrentIndex(i)
                found = True
                break
        if not found:
            self._file_combo.blockSignals(True)
            self._file_combo.addItem(name, path)
            self._file_combo.setCurrentIndex(self._file_combo.count() - 1)
            self._file_combo.blockSignals(False)
        dur = len(data) / sr
        self._status.setText(f"Loaded: {name} ({dur:.2f}s, {sr}Hz)")
        self._auto_name()
        self._on_selection_changed(0.0, 1.0)

    def _on_file_selected(self, idx: int):
        if idx < 0:
            return
        path = self._file_combo.itemData(idx)
        if path and path != self._source_path:
            self.load_file(path)

    def _on_load_click(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Sample for Slicing", "",
            "Audio Files (*.wav *.aiff *.aif *.flac *.ogg);;All Files (*)"
        )
        if path:
            self.load_file(path)

    def add_loaded_sample(self, path: str):
        """Add a channel-loaded sample to the file dropdown (if not already present)."""
        if not path or not os.path.isfile(path):
            return
        for i in range(self._file_combo.count()):
            if self._file_combo.itemData(i) == path:
                return  # already in list
        name = os.path.basename(path)
        self._file_combo.blockSignals(True)
        self._file_combo.addItem(name, path)
        self._file_combo.blockSignals(False)

    def _on_selection_changed(self, start_frac: float, end_frac: float):
        if self._audio_data is None:
            return
        dur_total = len(self._audio_data) / self._sample_rate
        start_sec = start_frac * dur_total
        end_sec = end_frac * dur_total
        # Block signals to avoid re-entrant spinbox→marker loop
        for sb in (self._start_spin, self._end_spin, self._dur_spin):
            sb.blockSignals(True)
        self._start_spin.setValue(start_sec)
        self._end_spin.setValue(end_sec)
        self._dur_spin.setValue(end_sec - start_sec)
        for sb in (self._start_spin, self._end_spin, self._dur_spin):
            sb.blockSignals(False)
        # Push undo
        if not self._suppress_undo:
            self._push_undo(start_frac, end_frac)

        # Queue new loop region if currently playing with loop enabled
        if (self._playing and self._loop_cb.isChecked()
                and self._preview_service is not None
                and self._audio_data is not None):
            start_idx = int(start_frac * len(self._audio_data))
            end_idx = int(end_frac * len(self._audio_data))
            segment = self._audio_data[start_idx:end_idx]
            if len(segment) > 0:
                self._preview_service.update_loop_segment(segment)

    # ── Numeric spinbox handlers ──────────────────────────────────────

    def _on_start_spin_changed(self, val: float):
        if self._audio_data is None:
            return
        dur_total = len(self._audio_data) / self._sample_rate
        frac = max(0.0, min(val / dur_total, 1.0))
        end_frac = self._waveform.end_frac
        if frac >= end_frac:
            frac, end_frac = end_frac, frac
        self._active_marker = 'start'
        if self._grid_snap_enabled:
            frac = self._snap_frac_to_grid(frac)
        if self._zx_snap_enabled:
            frac = self._snap_frac_to_zx(frac)
        # Post-snap validation: ensure start < end
        if frac >= end_frac:
            frac = max(0.0, end_frac - 0.001)
        self._waveform.set_selection(frac, end_frac)

    def _on_end_spin_changed(self, val: float):
        if self._audio_data is None:
            return
        dur_total = len(self._audio_data) / self._sample_rate
        frac = max(0.0, min(val / dur_total, 1.0))
        start_frac = self._waveform.start_frac
        if frac <= start_frac:
            frac, start_frac = start_frac, frac
        self._active_marker = 'end'
        if self._grid_snap_enabled:
            frac = self._snap_frac_to_grid(frac)
        if self._zx_snap_enabled:
            frac = self._snap_frac_to_zx(frac)
        # Post-snap validation: ensure end > start
        if frac <= start_frac:
            frac = min(1.0, start_frac + 0.001)
        self._waveform.set_selection(start_frac, frac)

    def _on_dur_spin_changed(self, val: float):
        if self._audio_data is None:
            return
        dur_total = len(self._audio_data) / self._sample_rate
        start_sec = self._start_spin.value()
        new_end = start_sec + val
        end_frac = max(0.0, min(new_end / dur_total, 1.0))
        start_frac = self._waveform.start_frac
        self._active_marker = 'end'
        if self._grid_snap_enabled:
            end_frac = self._snap_frac_to_grid(end_frac)
        if self._zx_snap_enabled:
            end_frac = self._snap_frac_to_zx(end_frac)
        if end_frac <= start_frac:
            end_frac = start_frac + 0.001
        self._waveform.set_selection(start_frac, end_frac)

    # ── ZX / Grid snap toggles ────────────────────────────────────────

    def _on_zx_toggled(self, checked: bool):
        self._zx_snap_enabled = checked
        self._zx_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_GOLD if checked else BG_BLACK}; "
            f"color: {'#000' if checked else TEXT_SECONDARY}; "
            f"font-weight: bold; font-size: 9px; border: 1px solid {SEPARATOR}; "
            f"padding: 1px 6px; }}"
            f"QPushButton:hover {{ background: #333; }}"
        )

    def _on_grid_toggled(self, checked: bool):
        self._grid_snap_enabled = checked
        self._grid_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_BLUE if checked else BG_BLACK}; "
            f"color: {'#000' if checked else TEXT_SECONDARY}; "
            f"font-weight: bold; font-size: 9px; border: 1px solid {SEPARATOR}; "
            f"padding: 1px 6px; }}"
            f"QPushButton:hover {{ background: #333; }}"
        )

    def _snap_frac_to_zx(self, frac: float) -> float:
        """Snap a fractional position to the nearest zero-crossing."""
        if self._audio_data is None:
            return frac
        mono = self._waveform._mono  # reuse cached mono from waveform widget
        if mono is None:
            return frac
        pos = int(frac * len(mono))
        snapped = self._snap_to_zero_crossing(pos, mono, window=64)
        return snapped / len(mono)

    def _snap_frac_to_grid(self, frac: float) -> float:
        """Snap a fractional position to the nearest beat subdivision."""
        if self._audio_data is None:
            return frac
        bpm = self._get_target_bpm()
        if bpm <= 0:
            return frac
        sr = self._sample_rate
        total = len(self._audio_data)
        # Snap to 1/4 note grid
        beat_samples = (60.0 / bpm) * sr
        pos = frac * total
        nearest_beat = round(pos / beat_samples) * beat_samples
        return max(0.0, min(nearest_beat / total, 1.0))

    # ── Undo / Redo ───────────────────────────────────────────────────

    def _push_undo(self, start_frac: float, end_frac: float):
        """Push current selection to undo stack."""
        entry = (start_frac, end_frac)
        if self._undo_stack and self._undo_stack[-1] == entry:
            return  # no-op duplicate
        self._undo_stack.append(entry)
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _undo(self):
        if len(self._undo_stack) < 2:
            return  # need at least current + previous
        current = self._undo_stack.pop()
        self._redo_stack.append(current)
        prev = self._undo_stack[-1]
        self._suppress_undo = True
        self._waveform.set_selection(prev[0], prev[1])
        self._suppress_undo = False

    def _redo(self):
        if not self._redo_stack:
            return
        entry = self._redo_stack.pop()
        self._undo_stack.append(entry)
        self._suppress_undo = True
        self._waveform.set_selection(entry[0], entry[1])
        self._suppress_undo = False

    # ── Marker nudge ──────────────────────────────────────────────────

    def _nudge_marker(self, samples: int):
        """Nudge the active marker by the given number of samples."""
        if self._audio_data is None:
            return
        total = len(self._audio_data)
        delta_frac = samples / total
        start_f = self._waveform.start_frac
        end_f = self._waveform.end_frac
        if self._active_marker == 'start':
            start_f = max(0.0, min(start_f + delta_frac, end_f - 0.001))
        else:
            end_f = max(start_f + 0.001, min(end_f + delta_frac, 1.0))
        self._waveform.set_selection(start_f, end_f)

    def _on_bar_changed(self, idx: int):
        """When bar count changes, lock selection to that size or unlock."""
        bar_count = self._bar_combo.currentData()
        if bar_count == 0 or self._audio_data is None:
            # None — unlock
            self._waveform._locked_span = 0.0
            return
        bpm = self._get_target_bpm()
        sr = self._sample_rate
        total_frames = len(self._audio_data)
        bar_samples = (60.0 / bpm) * 4.0 * sr
        loop_samples = int(bar_count * bar_samples)
        span_frac = min(loop_samples / total_frames, 1.0)
        self._waveform._locked_span = span_frac
        # Snap current selection to this size, centered on current start
        new_start = self._waveform.start_frac
        new_end = new_start + span_frac
        if new_end > 1.0:
            new_start = max(0.0, 1.0 - span_frac)
            new_end = new_start + span_frac
        self._waveform.set_selection(new_start, min(new_end, 1.0))
        dur = loop_samples / sr
        self._status.setText(f"{bar_count} bar(s) @ {bpm:.1f} BPM = {dur:.3f}s (locked)")

    def set_output_device(self, device: int):
        """Set the sounddevice output device index used for preview playback."""
        self._output_device = device

    def set_slicer_state(self, slicer_state):
        """Apply persisted SlicerState to UI controls."""
        self._loop_cb.setChecked(slicer_state.preview_loop)
        self._output_device = slicer_state.cue_device
        # Cue volume: fader 0-1 maps to 0-200%
        self._cue_vol_fader.set_value(slicer_state.cue_volume / 2.0)
        # Cue mode
        idx = self._cue_mode_combo.findData(slicer_state.cue_mode)
        if idx >= 0:
            self._cue_mode_combo.setCurrentIndex(idx)
        # Cue device and channel are set during device population

    def set_preview_service(self, service):
        """Inject the SlicerPreviewService instance."""
        self._preview_service = service

    def populate_cue_devices(self, devices: list):
        """Populate the cue device combo. *devices* is a list of (index, name)."""
        self._cue_device_combo.blockSignals(True)
        self._cue_device_combo.clear()
        self._cue_device_combo.addItem("Default", -1)
        for dev_idx, dev_name in devices:
            self._cue_device_combo.addItem(dev_name, dev_idx)
        self._cue_device_combo.blockSignals(False)

    def select_cue_device(self, device_id: int):
        """Select the cue device by its sounddevice index."""
        for i in range(self._cue_device_combo.count()):
            if self._cue_device_combo.itemData(i) == device_id:
                self._cue_device_combo.setCurrentIndex(i)
                self._update_cue_channel_list()
                return

    def _update_cue_channel_list(self):
        """Refresh the channel-pair combo for the selected cue device."""
        import sounddevice as sd
        dev = self._cue_device_combo.currentData()
        self._cue_ch_combo.blockSignals(True)
        self._cue_ch_combo.clear()
        try:
            if dev is not None and dev >= 0:
                info = sd.query_devices(dev, 'output')
            else:
                info = sd.query_devices(kind='output')
            max_ch = int(info['max_output_channels'])
        except Exception:
            max_ch = 2
        for offset in range(0, max_ch, 2):
            pair_end = min(offset + 2, max_ch)
            label = f"{offset + 1}/{pair_end}"
            self._cue_ch_combo.addItem(label, offset)
        self._cue_ch_combo.blockSignals(False)

    def _on_cue_vol_changed(self, val: float):
        """Fader 0-1 → cue volume 0-2."""
        volume = val * 2.0
        if self._preview_service is not None:
            self._preview_service.set_volume(volume)

    def _on_cue_device_changed(self, _idx: int):
        dev = self._cue_device_combo.currentData()
        if dev is None:
            dev = -1
        self._output_device = dev
        self._update_cue_channel_list()
        ch_offset = self._cue_ch_combo.currentData() or 0
        if self._preview_service is not None:
            self._preview_service.set_device(dev, ch_offset)

    def _on_cue_ch_changed(self, _idx: int):
        ch_offset = self._cue_ch_combo.currentData() or 0
        dev = self._cue_device_combo.currentData()
        if dev is None:
            dev = -1
        if self._preview_service is not None:
            self._preview_service.set_device(dev, ch_offset)

    def _on_cue_mode_changed(self, _idx: int):
        mode = self._cue_mode_combo.currentData()
        is_off = (mode == 2)
        self._cue_vol_fader.setEnabled(not is_off)

    # ── Chop mode logic ──────────────────────────────────────────

    def _on_chop_mode_changed(self, _idx: int):
        mode = self._chop_mode_combo.currentData()
        self._chop_mode = mode
        is_manual = (mode == 0)
        is_transient = (mode == 1)
        is_equal = (mode == 2)

        self._slices_spin.setEnabled(is_equal)
        self._sens_fader.setEnabled(is_transient)
        self._chop_btn.setVisible(not is_manual)

        if is_manual:
            self._clear_regions()
        else:
            self._run_chop()

    def _clear_regions(self):
        self._slice_regions = []
        self._current_region_idx = -1
        self._waveform.set_regions([])
        self._region_count_lbl.setText("")
        self._export_all_btn.setVisible(False)

    def _run_chop(self):
        """Execute the auto-chop algorithm for the current mode."""
        if self._audio_data is None:
            self._status.setText("No audio loaded.")
            return
        mode = self._chop_mode
        total = len(self._audio_data)

        if mode == 1:
            # Transient-based
            regions = self._chop_transient()
        elif mode == 2:
            # Equal division
            regions = self._chop_equal(total)
        elif mode == 3:
            # BPM grid
            regions = self._chop_bpm_grid(total)
        else:
            return

        self._slice_regions = regions
        self._current_region_idx = -1
        self._waveform.set_regions(regions)

        n = len(regions)
        if n == 0:
            self._region_count_lbl.setText("No regions")
            self._export_all_btn.setVisible(False)
            if mode == 1:
                self._status.setText("No transients detected \u2014 try lowering sensitivity")
        else:
            self._region_count_lbl.setText(f"{n} slices")
            self._export_all_btn.setVisible(True)
            self._status.setText(f"{n} regions ready")

    def _chop_transient(self) -> list:
        """Chop at transient boundaries with sensitivity control."""
        sensitivity = self._sens_fader.value  # 0-1 → multiplier
        transients = self._detect_transients_with_sensitivity(sensitivity)
        total = len(self._audio_data)
        if len(transients) == 0:
            return []
        regions = []
        # First region: start-of-file to first transient
        if transients[0] > 0:
            regions.append((0, int(transients[0])))
        # Transient-to-transient
        for i in range(len(transients) - 1):
            regions.append((int(transients[i]), int(transients[i + 1])))
        # Last transient to end
        if transients[-1] < total:
            regions.append((int(transients[-1]), total))
        return regions

    def _chop_equal(self, total: int) -> list:
        """Divide into N equal segments."""
        n = self._slices_spin.value()
        min_len = 64
        n = min(n, max(1, total // min_len))
        if n < 1:
            return []
        chunk = total // n
        regions = []
        for i in range(n):
            start = i * chunk
            end = (i + 1) * chunk if i < n - 1 else total
            regions.append((start, end))
        return regions

    def _chop_bpm_grid(self, total: int) -> list:
        """Divide at beat boundaries."""
        bpm = self._get_target_bpm()
        sr = self._sample_rate
        beat_samples = int((60.0 / max(1.0, bpm)) * sr)  # quarter note
        if beat_samples < 64:
            return []
        regions = []
        pos = 0
        while pos < total:
            end = min(pos + beat_samples, total)
            regions.append((pos, end))
            pos = end
        return regions

    def _detect_transients_with_sensitivity(self, sensitivity: float) -> np.ndarray:
        """Like _detect_transients but with adjustable threshold multiplier.

        sensitivity 0.0 → low threshold (more transients)
        sensitivity 1.0 → high threshold (fewer transients)
        """
        if self._audio_data is None:
            return np.array([], dtype=int)
        mono = self._audio_data.mean(axis=1) if self._audio_data.ndim == 2 else self._audio_data
        hop = 512
        n_frames = len(mono) // hop
        if n_frames < 4:
            return np.array([], dtype=int)

        envelope = np.array([
            np.sum(mono[i * hop:(i + 1) * hop] ** 2) for i in range(n_frames)
        ])
        kernel = np.ones(5) / 5.0
        smoothed = np.convolve(envelope, kernel, mode='same')
        onset = np.diff(smoothed)
        onset = np.maximum(onset, 0.0)

        # Sensitivity: 0.0 → multiplier 0.5 (loose), 1.0 → multiplier 4.0 (strict)
        multiplier = 0.5 + sensitivity * 3.5
        threshold = np.median(onset) + multiplier * np.std(onset)
        if threshold <= 0:
            threshold = np.max(onset) * 0.3

        peaks = []
        for i in range(1, len(onset) - 1):
            if onset[i] > threshold and onset[i] >= onset[i - 1] and onset[i] >= onset[i + 1]:
                peaks.append((i + 1) * hop)
        return np.array(peaks, dtype=int)

    def _on_region_clicked(self, region_idx: int):
        """Audition a region when clicked in the waveform."""
        if region_idx < 0 or region_idx >= len(self._slice_regions):
            return
        self._current_region_idx = region_idx
        rs, re = self._slice_regions[region_idx]
        total = len(self._audio_data)
        self._play_from(rs / total, re / total)

    def _audition_current_region(self):
        """Play the current region through the cue path."""
        if self._current_region_idx < 0 or not self._slice_regions:
            return
        ri = self._current_region_idx
        if ri >= len(self._slice_regions):
            return
        rs, re = self._slice_regions[ri]
        total = len(self._audio_data)
        self._waveform.set_current_region(ri)
        self._play_from(rs / total, re / total)

    def _step_region(self, delta: int):
        """Step to next/prev region and auto-audition."""
        if not self._slice_regions:
            return
        n = len(self._slice_regions)
        if self._current_region_idx < 0:
            self._current_region_idx = 0 if delta > 0 else n - 1
        else:
            self._current_region_idx = (self._current_region_idx + delta) % n
        ri = self._current_region_idx
        rs, re = self._slice_regions[ri]
        total = len(self._audio_data)
        # Update selection to match region
        self._waveform.set_current_region(ri)
        self._waveform.set_selection(rs / total, re / total)
        self._audition_current_region()

    def _waveform_key(self, event) -> bool:
        """Handle keyboard shortcuts from the waveform widget.

        Returns True if the event was handled.
        """
        from PySide6.QtCore import Qt
        key = event.key()
        mods = event.modifiers()
        has_shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        has_ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)

        # Ctrl+Z / Ctrl+Shift+Z: undo / redo
        if has_ctrl and key == Qt.Key.Key_Z:
            if has_shift:
                self._redo()
            else:
                self._undo()
            return True

        # Shift+Arrow: nudge active marker ±1ms
        # Ctrl+Shift+Arrow: nudge active marker ±1 sample
        if has_shift and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            if self._audio_data is not None:
                if has_ctrl:
                    samples = 1 if key == Qt.Key.Key_Right else -1
                else:
                    samples = int(self._sample_rate * 0.001)
                    if key == Qt.Key.Key_Left:
                        samples = -samples
                self._nudge_marker(samples)
                return True

        # Space: audition region under cursor (or toggle play)
        if key == Qt.Key.Key_Space:
            if self._slice_regions and self._audio_data is not None:
                cursor = self._waveform.mapFromGlobal(self._waveform.cursor().pos())
                w = max(self._waveform.width(), 1)
                vs = self._waveform._view_start
                ve = self._waveform._view_end
                vspan = max(ve - vs, 1e-9)
                frac = vs + (cursor.x() / w) * vspan
                ri = self._waveform.region_at_frac(frac)
                if ri >= 0:
                    self._current_region_idx = ri
                    self._waveform.set_current_region(ri)
                    self._on_region_clicked(ri)
                    return True
            self._toggle_play()
            return True

        # Left/Right: step through regions (no modifiers)
        if key == Qt.Key.Key_Left and self._slice_regions and not has_shift:
            self._step_region(-1)
            return True
        if key == Qt.Key.Key_Right and self._slice_regions and not has_shift:
            self._step_region(1)
            return True

        # Enter: play from current playhead to end of selection
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._audio_data is not None:
                self._play_from(
                    max(self._waveform._playhead, self._waveform.start_frac),
                    self._waveform.end_frac,
                )
                return True

        return False

    def _toggle_play(self):
        if self._playing:
            self._stop_play()
        else:
            self._play_from(0.0, 1.0)

    def _play_selection(self):
        if self._audio_data is not None:
            self._play_from(self._waveform.start_frac, self._waveform.end_frac)

    def _play_from(self, start_frac: float, end_frac: float):
        if self._audio_data is None:
            return
        self._stop_play()

        cue_mode = self._cue_mode_combo.currentData()  # 0=CUE, 1=CUE+MAIN, 2=OFF
        if cue_mode == 2:
            # CUE OFF — do nothing
            return

        try:
            start_idx = int(start_frac * len(self._audio_data))
            end_idx = int(end_frac * len(self._audio_data))
            segment = self._audio_data[start_idx:end_idx]
            if len(segment) == 0:
                self._cue_status.setText("Nothing to preview")
                return
            loop = self._loop_cb.isChecked()
            self._loop_segment_len = len(segment) if loop else 0

            # Cue stream via preview service
            if self._preview_service is not None and cue_mode in (0, 1):
                try:
                    self._preview_service.play(segment, self._sample_rate, loop)
                    dev = self._cue_device_combo.currentText() or "default"
                    self._cue_status.setText(f"Playing on {dev}")
                except Exception as e:
                    self._cue_status.setText(f"\u26A0 {e}")

            # CUE+MAIN — also fire on main device
            if cue_mode == 1:
                import sounddevice as sd
                play_seg = segment
                if loop:
                    reps = max(1, min(10, int(30.0 * self._sample_rate / max(len(segment), 1))))
                    play_seg = np.tile(segment, (reps, 1)) if segment.ndim == 2 else np.tile(segment, reps)
                _device = self._output_device if self._output_device >= 0 else None
                sd.play(play_seg, self._sample_rate, device=_device)

            self._playing = True
            self._play_start_frac = start_frac
            self._play_end_frac = end_frac
            self._play_pos = 0
            self._play_total = len(self._audio_data[start_idx:end_idx])
            if self._preview_service is not None:
                self._buf_gen = self._preview_service.buf_generation
            self._play_timer.start()
            self._play_btn.setText("\u23F8")  # pause symbol
        except Exception as e:
            self._status.setText(f"Play error: {e}")

    def _stop_play(self):
        if self._preview_service is not None:
            self._preview_service.stop()
            self._cue_status.setText("")
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        self._playing = False
        self._play_timer.stop()
        self._play_btn.setText("\u25B6")
        self._waveform.set_playhead(0)

    def _update_playhead(self):
        if not self._playing or self._audio_data is None:
            self._stop_play()
            return
        # Check if preview service finished
        if self._preview_service is not None and not self._preview_service.is_playing:
            cue_mode = self._cue_mode_combo.currentData()
            if cue_mode == 0:
                # CUE ONLY — service done means playback done
                self._stop_play()
                return

        # Detect pending-buffer swap: update tracking to new loop region
        if self._preview_service is not None and self._loop_segment_len > 0:
            gen = self._preview_service.buf_generation
            if gen != self._buf_gen:
                self._buf_gen = gen
                new_len = self._preview_service.current_buf_len
                if new_len > 0:
                    # Adopt the current waveform selection as the new play region
                    self._play_start_frac = self._waveform.start_frac
                    self._play_end_frac = self._waveform.end_frac
                    self._loop_segment_len = new_len
                    self._play_total = new_len
                    self._play_pos = 0

        self._play_pos += int(self._sample_rate * 0.030)
        if self._play_pos >= self._play_total:
            if self._loop_segment_len <= 0:
                self._stop_play()
                return
        # Wrap position within single loop iteration for playhead display
        sel_span = self._play_end_frac - self._play_start_frac
        if self._loop_segment_len > 0:
            pos_in_loop = self._play_pos % self._loop_segment_len
            frac = self._play_start_frac + (pos_in_loop / self._loop_segment_len) * sel_span
        else:
            frac = self._play_start_frac + (self._play_pos / self._play_total) * sel_span
        self._waveform.set_playhead(frac)

    def _auto_name(self):
        """Generate automatic name from source file + slice number."""
        if not self._source_path:
            return
        base = os.path.splitext(os.path.basename(self._source_path))[0]
        from mlrvp.config import get_slices_dir
        out_dir = str(get_slices_dir())
        num = 1
        while True:
            candidate = f"{base}_slice{num:02d}"
            path = os.path.join(out_dir, candidate + ".wav")
            if not os.path.exists(path):
                break
            num += 1
        self._name_edit.setText(f"{base}_slice{num:02d}")

    # ── Auto-loop: transient detection + BPM-aware snapping ──────────

    def _detect_transients(self) -> np.ndarray:
        """Return sample indices of detected transients in loaded audio."""
        if self._audio_data is None:
            return np.array([], dtype=int)
        mono = self._audio_data.mean(axis=1) if self._audio_data.ndim == 2 else self._audio_data
        hop = 512
        n_frames = len(mono) // hop
        if n_frames < 4:
            return np.array([], dtype=int)

        # Energy envelope per frame
        envelope = np.array([
            np.sum(mono[i * hop:(i + 1) * hop] ** 2) for i in range(n_frames)
        ])

        # Smooth with small kernel
        kernel = np.ones(5) / 5.0
        smoothed = np.convolve(envelope, kernel, mode='same')

        # Positive first-difference (onset strength)
        onset = np.diff(smoothed)
        onset = np.maximum(onset, 0.0)

        # Adaptive threshold
        threshold = np.median(onset) + 1.5 * np.std(onset)
        if threshold <= 0:
            threshold = np.max(onset) * 0.3

        # Pick local peaks above threshold
        peaks = []
        for i in range(1, len(onset) - 1):
            if onset[i] > threshold and onset[i] >= onset[i - 1] and onset[i] >= onset[i + 1]:
                peaks.append((i + 1) * hop)
        return np.array(peaks, dtype=int)

    def _estimate_bpm(self) -> float:
        """Estimate BPM via onset-envelope autocorrelation (60-200 BPM)."""
        if self._audio_data is None:
            return 120.0
        mono = self._audio_data.mean(axis=1) if self._audio_data.ndim == 2 else self._audio_data
        hop = 512
        n_frames = len(mono) // hop
        if n_frames < 16:
            return 120.0

        envelope = np.array([
            np.sum(mono[i * hop:(i + 1) * hop] ** 2) for i in range(n_frames)
        ])
        onset = np.maximum(np.diff(envelope), 0.0)

        fps = self._sample_rate / hop
        min_lag = max(1, int(fps * 60.0 / 200.0))
        max_lag = min(int(fps * 60.0 / 60.0), len(onset) // 2)
        if min_lag >= max_lag:
            return 120.0

        # Autocorrelation over the BPM-relevant lag range
        corr = np.correlate(onset[:4096], onset[:4096], mode='full')
        mid = len(corr) // 2
        corr = corr[mid + min_lag: mid + max_lag + 1]
        if len(corr) == 0:
            return 120.0

        best_lag = min_lag + int(np.argmax(corr))
        bpm = 60.0 * fps / best_lag
        return round(bpm, 1)

    def _get_target_bpm(self) -> float:
        """Return user-set BPM or auto-detected BPM."""
        manual = self._bpm_spin.value()
        if manual > 0:
            return manual
        bpm = self._estimate_bpm()
        # Show detected value in the spinbox as hint (without triggering signals)
        self._bpm_spin.blockSignals(True)
        self._bpm_spin.setSuffix(f"  ({bpm:.1f})")
        self._bpm_spin.blockSignals(False)
        return bpm

    def _snap_to_zero_crossing(self, pos: int, mono: np.ndarray, window: int = 64) -> int:
        """Snap a sample position to the nearest zero-crossing within ±window."""
        for offset in range(window):
            idx = pos + offset
            if idx + 1 < len(mono) and mono[idx] * mono[idx + 1] <= 0:
                return idx
            idx = pos - offset
            if idx >= 0 and idx + 1 < len(mono) and mono[idx] * mono[idx + 1] <= 0:
                return idx
        return pos

    def _auto_loop(self):
        """Snap selection to nearest transient for a perfect musical loop.

        If FIT is checked, snaps BOTH start and end to transients and
        marks the slice for resampling on save so it's exactly bar-aligned.
        """
        if self._audio_data is None:
            self._status.setText("No audio loaded.")
            return

        total_frames = len(self._audio_data)
        sr = self._sample_rate
        bpm = self._get_target_bpm()
        bar_count = self._bar_combo.currentData()
        fit_mode = self._fit_cb.isChecked()

        # If None, default to 2 bars and lock
        if bar_count == 0:
            bar_count = 2
            for i in range(self._bar_combo.count()):
                if self._bar_combo.itemData(i) == 2:
                    self._bar_combo.blockSignals(True)
                    self._bar_combo.setCurrentIndex(i)
                    self._bar_combo.blockSignals(False)
                    break

        bar_samples = (60.0 / max(1.0, bpm)) * 4.0 * sr
        ideal_samples = max(1, int(bar_count * bar_samples))
        span_frac = min(ideal_samples / max(1, total_frames), 1.0)

        if ideal_samples >= total_frames:
            self._waveform._locked_span = span_frac
            self._waveform.set_selection(0.0, 1.0)
            self._fit_active = False
            self._status.setText(f"Auto: {bpm:.1f} BPM — {bar_count} bar(s) exceeds file length")
            self._auto_name()
            return

        # Detect transients
        transients = self._detect_transients()

        # Current selection start as anchor
        sel_start = int(self._waveform.start_frac * total_frames)

        # Snap start to nearest transient
        if len(transients) > 0:
            dists = np.abs(transients.astype(np.int64) - sel_start)
            snap_start = int(transients[np.argmin(dists)])
        else:
            snap_start = sel_start

        mono = self._audio_data.mean(axis=1) if self._audio_data.ndim == 2 else self._audio_data

        if fit_mode and len(transients) > 0:
            # ── FIT MODE: snap end to nearest transient too ──
            ideal_end = snap_start + ideal_samples

            # Find transients near the ideal end (within ±1 beat)
            beat_window = int(bar_samples / 2)
            end_candidates = transients[
                np.abs(transients.astype(np.int64) - ideal_end) < beat_window
            ]
            if len(end_candidates) > 0:
                # Pick the candidate closest to ideal end
                snap_end = int(end_candidates[
                    np.argmin(np.abs(end_candidates.astype(np.int64) - ideal_end))
                ])
            else:
                # No transient nearby — fall back to ideal end
                snap_end = ideal_end

            # Zero-crossing on both ends
            snap_start = self._snap_to_zero_crossing(snap_start, mono)
            snap_end = self._snap_to_zero_crossing(snap_end, mono)

            # Ensure valid range
            if snap_end <= snap_start:
                snap_end = snap_start + ideal_samples

            # If end exceeds audio, shift back
            if snap_end > total_frames:
                snap_end = total_frames
                snap_start = max(0, snap_end - ideal_samples)
                if len(transients) > 0:
                    nearby = transients[
                        np.abs(transients.astype(np.int64) - snap_start) < beat_window
                    ]
                    if len(nearby) > 0:
                        snap_start = int(nearby[np.argmin(np.abs(nearby - snap_start))])
                        snap_start = self._snap_to_zero_crossing(snap_start, mono)

            actual_samples = snap_end - snap_start
            speed_ratio = actual_samples / ideal_samples if ideal_samples > 0 else 1.0
            self._fit_speed_ratio = speed_ratio
            self._ideal_bar_samples = ideal_samples
            self._fit_active = True

            # Unlock span so the selection shows the actual transient region
            actual_frac = actual_samples / total_frames
            self._waveform._locked_span = actual_frac

            start_frac = snap_start / total_frames
            end_frac = snap_end / total_frames
            self._waveform.set_selection(start_frac, end_frac)

            dur = actual_samples / sr
            cents = abs(1200.0 * np.log2(speed_ratio)) if speed_ratio > 0 else 0
            self._status.setText(
                f"FIT: {bpm:.1f} BPM, {bar_count} bar(s), {dur:.3f}s → "
                f"{ideal_samples / sr:.3f}s on save ({speed_ratio:.4f}x, {cents:.0f}¢)"
            )
        else:
            # ── STANDARD MODE (original behaviour) ──
            self._fit_active = False
            self._waveform._locked_span = span_frac

            snap_end = snap_start + ideal_samples

            # If end exceeds audio, shift back
            if snap_end > total_frames:
                snap_start = max(0, total_frames - ideal_samples)
                snap_end = snap_start + ideal_samples
                if len(transients) > 0:
                    beat_window = int(bar_samples / 4)
                    nearby = transients[np.abs(transients - snap_start) < beat_window]
                    if len(nearby) > 0:
                        snap_start = int(nearby[np.argmin(np.abs(nearby - snap_start))])
                        snap_end = snap_start + ideal_samples

            snap_end = min(snap_end, total_frames)

            # Zero-crossing snap on start
            snap_start = self._snap_to_zero_crossing(snap_start, mono)
            snap_end = snap_start + ideal_samples
            snap_end = min(max(snap_end, snap_start + 1), total_frames)

            start_frac = snap_start / total_frames
            end_frac = snap_end / total_frames
            self._waveform.set_selection(start_frac, end_frac)

            dur = (snap_end - snap_start) / sr
            self._status.setText(f"Auto: {bpm:.1f} BPM, {bar_count} bar(s), {dur:.3f}s")

        self._auto_name()

    def _do_slice(self):
        """Slice the selected region and save to WAV.

        In FIT mode, resamples the segment to the exact ideal bar length
        so it loops perfectly at 1.0x playback speed.
        """
        if self._audio_data is None:
            self._status.setText("No audio loaded.")
            return
        name = self._name_edit.text().strip()
        if not name:
            self._status.setText("Enter a name for the slice.")
            return
        if not name.lower().endswith('.wav'):
            name += '.wav'

        # Save into the project slices directory
        from mlrvp.config import get_slices_dir
        out_dir = str(get_slices_dir())
        out_path = os.path.join(out_dir, name)

        start_idx = int(self._waveform.start_frac * len(self._audio_data))
        end_idx = int(self._waveform.end_frac * len(self._audio_data))
        segment = self._audio_data[start_idx:end_idx]

        # FIT mode: resample to exact bar length
        if self._fit_active and self._ideal_bar_samples > 0:
            from scipy.signal import resample
            actual_len = len(segment)
            target_len = self._ideal_bar_samples
            if actual_len != target_len and actual_len > 0:
                segment = resample(segment, target_len, axis=0).astype(np.float32)

        try:
            sf.write(out_path, segment, self._sample_rate, subtype='PCM_16')
        except Exception as e:
            self._status.setText(f"Save error: {e}")
            return

        # Write metadata sidecar
        import json
        from datetime import datetime
        meta_path = os.path.splitext(out_path)[0] + '.json'
        meta = {
            "source": os.path.basename(self._source_path) if self._source_path else "",
            "bpm": self._get_target_bpm(),
            "bars": self._bar_combo.currentData() or 0,
            "fit": self._fit_active,
            "template": self._name_edit.text().strip(),
            "exported_at": datetime.now().isoformat(),
        }
        try:
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(meta, f, indent=2)
        except OSError:
            pass

        dur = len(segment) / self._sample_rate
        fit_tag = " [FIT]" if self._fit_active else ""
        self._status.setText(f"Saved: {name} ({dur:.3f}s){fit_tag}")
        self.slice_saved.emit(out_path)
        # Add to file combo for re-use
        self._file_combo.blockSignals(True)
        self._file_combo.addItem(os.path.basename(out_path), out_path)
        self._file_combo.blockSignals(False)
        # Advance auto name
        self._auto_name()

    def _export_all_slices(self):
        """Batch export all chop regions as individual WAV files."""
        if not self._slice_regions or self._audio_data is None:
            self._status.setText("No regions to export.")
            return

        from mlrvp.config import get_slices_dir
        out_dir = str(get_slices_dir())
        source_base = os.path.splitext(os.path.basename(self._source_path))[0] if self._source_path else "slice"
        template = self._name_edit.text().strip()
        if not template:
            template = "{source}_slice{n:02d}"

        bpm = self._get_target_bpm()
        regions = list(self._slice_regions)
        fit_active = self._fit_active
        ideal_bar_samples = self._ideal_bar_samples
        sr = self._sample_rate
        audio = self._audio_data
        bars = self._bar_combo.currentData() or 0  # capture on main thread

        self._export_all_btn.setEnabled(False)
        self._export_all_btn.setText("Exporting\u2026")

        def _do_export():
            exported = 0
            failed = []
            used_names = set()
            for i, (rs, re) in enumerate(regions):
                try:
                    # Build name from template
                    name = template.format(
                        source=source_base, n=i + 1, bpm=bpm,
                        bars=bars,
                    )
                except (KeyError, IndexError, ValueError):
                    name = f"{source_base}_slice{i + 1:02d}"

                if not name.lower().endswith('.wav'):
                    name += '.wav'

                # De-duplicate names
                if name in used_names:
                    base_n, ext = os.path.splitext(name)
                    dup = 1
                    while f"{base_n}_dup{dup}{ext}" in used_names:
                        dup += 1
                    name = f"{base_n}_dup{dup}{ext}"
                used_names.add(name)

                out_path = os.path.join(out_dir, name)
                segment = audio[rs:re]

                if fit_active and ideal_bar_samples > 0:
                    try:
                        from scipy.signal import resample
                        actual_len = len(segment)
                        if actual_len != ideal_bar_samples and actual_len > 0:
                            segment = resample(segment, ideal_bar_samples, axis=0).astype(np.float32)
                    except Exception:
                        pass

                # Write via temp file then rename for crash safety
                tmp_path = out_path + ".tmp"
                try:
                    sf.write(tmp_path, segment, sr, subtype='PCM_16')
                    if os.path.exists(out_path):
                        os.remove(out_path)
                    os.rename(tmp_path, out_path)
                    exported += 1
                    # Write metadata sidecar
                    import json as _json
                    from datetime import datetime as _dt
                    meta_path = os.path.splitext(out_path)[0] + '.json'
                    meta = {
                        "source": source_base,
                        "bpm": bpm,
                        "bars": bars,
                        "fit": fit_active,
                        "template": template,
                        "exported_at": _dt.now().isoformat(),
                    }
                    try:
                        with open(meta_path, 'w', encoding='utf-8') as mf:
                            _json.dump(meta, mf, indent=2)
                    except OSError:
                        pass
                except OSError as e:
                    failed.append((name, str(e)))
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass

                # Thread-safe progress update via QTimer
                _exp, _tot = exported, len(regions)
                QTimer.singleShot(0, lambda e=_exp, t=_tot: self._status.setText(
                    f"Exported {e}/{t} slices\u2026"
                ))

            # Final UI updates must run on the main thread
            if failed:
                msg = f"\u26A0 {len(failed)} slices failed \u2014 {exported}/{len(regions)} exported"
            else:
                msg = f"Exported {exported} slices to data/slices/"
            QTimer.singleShot(0, lambda: self._finish_export(msg))

        threading.Thread(target=_do_export, daemon=True).start()

    def _finish_export(self, msg: str):
        """Called on the main thread after background export completes."""
        self._status.setText(msg)
        self._export_all_btn.setText("EXPORT ALL")
        self._export_all_btn.setEnabled(True)
        self._library.refresh()

    def add_library_file(self, filepath: str):
        """Add a file to the combo dropdown (called externally)."""
        for i in range(self._file_combo.count()):
            if self._file_combo.itemData(i) == filepath:
                return
        self._file_combo.addItem(os.path.basename(filepath), filepath)

    def _on_library_load_to_channel(self, filepath: str, ch_idx: int):
        """Forward load-to-channel request from the library."""
        self.load_to_channel.emit(filepath, ch_idx)


# RecorderPanel is imported from effects_view
from mlrvp.gui.views.effects_view import RecorderPanel


class _SliceLibraryRow(QWidget):
    """Single row in the slice library: filename label (draggable) + BPM badge + delete button."""

    delete_requested = Signal(str)  # filepath
    load_to_channel = Signal(str, int)  # filepath, channel_index (0-based)

    def __init__(self, filepath: str, metadata: dict | None = None, parent=None):
        super().__init__(parent)
        self._filepath = filepath
        self._selected = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(22)
        self.setAcceptDrops(False)

        hl = QHBoxLayout(self)
        hl.setContentsMargins(2, 0, 2, 0)
        hl.setSpacing(4)

        name = os.path.basename(filepath)
        self._lbl = QLabel(name)
        self._lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 9px; background: transparent;"
        )
        self._lbl.setToolTip(filepath)
        self._lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        hl.addWidget(self._lbl, stretch=1)

        # BPM badge (shown if metadata sidecar exists)
        self._bpm_lbl = QLabel("")
        self._bpm_lbl.setFixedWidth(40)
        self._bpm_lbl.setStyleSheet(
            f"color: {ACCENT_GOLD}; font-size: 8px; font-weight: bold; background: transparent;"
        )
        hl.addWidget(self._bpm_lbl)
        self._load_metadata(metadata)

        del_btn = QPushButton("\u2715")
        del_btn.setFixedSize(18, 18)
        del_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_SECONDARY}; "
            f"border: none; font-size: 9px; padding: 0px; min-height: 0px; }}"
            f"QPushButton:hover {{ color: #ff6666; background: transparent; }}"
        )
        del_btn.setToolTip(f"Delete {name}")
        del_btn.clicked.connect(self._on_delete)
        hl.addWidget(del_btn)

        self._update_bg()

    def _load_metadata(self, cached: dict | None = None):
        """Apply BPM badge from pre-loaded metadata or read companion .json sidecar."""
        meta = cached
        if meta is None:
            import json
            meta_path = os.path.splitext(self._filepath)[0] + '.json'
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                except (json.JSONDecodeError, OSError):
                    meta = None
        if meta:
            bpm = meta.get("bpm")
            if bpm and bpm > 0:
                self._bpm_lbl.setText(f"{bpm:.0f}bpm")

    @property
    def selected(self) -> bool:
        return self._selected

    @selected.setter
    def selected(self, val: bool):
        self._selected = val
        self._update_bg()

    def _update_bg(self):
        if self._selected:
            self.setStyleSheet(
                f"background: {ACCENT_GOLD};"
            )
            self._lbl.setStyleSheet(
                f"color: {BG_BLACK}; font-size: 9px; font-weight: bold; background: transparent;"
            )
        else:
            self.setStyleSheet(
                f"background: transparent;"
            )
            self._lbl.setStyleSheet(
                f"color: {TEXT_PRIMARY}; font-size: 9px; background: transparent;"
            )

    # ── Context menu ──────────────────────────────────────────────────
    def contextMenuEvent(self, event):
        # Select only this row on right-click
        parent = self.parent()
        while parent and not isinstance(parent, SliceLibraryPanel):
            parent = parent.parent()
        if parent and hasattr(parent, '_on_row_clicked'):
            parent._on_row_clicked(self, ctrl=False, shift=False)

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: rgba(30, 30, 30, 210); color: {TEXT_PRIMARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 9px; }}"
            f"QMenu::item {{ padding: 4px 16px; }}"
            f"QMenu::item:selected {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        for ch in range(16):
            action = menu.addAction(f"Load to channel {ch + 1}")
            action.setData(ch)
        chosen = menu.exec(event.globalPos())
        if chosen is not None:
            ch_idx = chosen.data()
            self.load_to_channel.emit(self._filepath, ch_idx)

    # ── Drag support ──────────────────────────────────────────────────
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
            # Notify parent for multi-select
            mods = event.modifiers()
            ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            parent = self.parent()
            while parent and not isinstance(parent, SliceLibraryPanel):
                parent = parent.parent()
            if parent and hasattr(parent, '_on_row_clicked'):
                parent._on_row_clicked(self, ctrl=ctrl, shift=shift)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        dist = (event.position().toPoint() - self._drag_start).manhattanLength()
        if dist < 8:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self._filepath)])
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _on_delete(self):
        name = os.path.basename(self._filepath)
        reply = QMessageBox.question(
            self, "Delete Slice",
            f"Delete '{name}'?\nThis will permanently remove the file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                os.remove(self._filepath)
            except OSError:
                pass
            self.delete_requested.emit(self._filepath)


class SliceLibraryPanel(QFrame):
    """Scrollable list of saved slices with search, sort, and multi-select."""

    load_requested = Signal(str)  # drag-to-slice-editor target
    load_to_channel = Signal(str, int)  # filepath, channel_index

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[_SliceLibraryRow] = []
        self._sort_mode = 0  # 0=Name A-Z, 1=Name Z-A, 2=Newest, 3=Oldest
        self._meta_cache: dict[str, dict] = {}  # filepath → metadata dict cache
        self.setStyleSheet(
            f"SliceLibraryPanel {{ background: {BG_BLACK}; "
            f"border: 1px solid {SEPARATOR}; }}"
        )

        vb = QVBoxLayout(self)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet(f"background: {BG_PANEL};")
        hdr_hl = QHBoxLayout(hdr)
        hdr_hl.setContentsMargins(4, 2, 4, 2)
        hdr_lbl = QLabel("SLICES")
        hdr_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 8px; font-weight: bold; background: transparent;"
        )
        hdr_hl.addWidget(hdr_lbl)
        hdr_hl.addStretch()
        _hint = QLabel("drag to load")
        _hint.setStyleSheet(
            f"color: {SEPARATOR}; font-size: 7px; background: transparent;"
        )
        hdr_hl.addWidget(_hint)
        vb.addWidget(hdr)

        # Search + sort row
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(4, 2, 4, 2)
        filter_row.setSpacing(4)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search\u2026")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setFixedHeight(20)
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 4px; }}"
        )
        self._search_edit.textChanged.connect(self._on_search_text_changed)
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(250)
        self._search_debounce.timeout.connect(self._apply_filter)
        filter_row.addWidget(self._search_edit, stretch=1)

        self._sort_combo = QComboBox()
        self._sort_combo.setFixedHeight(20)
        self._sort_combo.setStyleSheet(
            f"QComboBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 4px; }}"
        )
        self._sort_combo.addItem("Name A-Z", 0)
        self._sort_combo.addItem("Name Z-A", 1)
        self._sort_combo.addItem("Newest", 2)
        self._sort_combo.addItem("Oldest", 3)
        self._sort_combo.setCurrentIndex(2)  # Default: Newest
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        filter_row.addWidget(self._sort_combo)

        # Batch delete button
        self._batch_del_btn = QPushButton("Delete")
        self._batch_del_btn.setFixedHeight(20)
        self._batch_del_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ color: #ff6666; }}"
        )
        self._batch_del_btn.setVisible(False)
        self._batch_del_btn.clicked.connect(self._batch_delete)
        filter_row.addWidget(self._batch_del_btn)

        vb.addLayout(filter_row)

        # Scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )

        self._inner = QWidget()
        self._inner.setStyleSheet(f"background: {BG_BLACK};")
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setContentsMargins(0, 0, 0, 0)
        self._inner_layout.setSpacing(1)
        self._inner_layout.addStretch()

        self._scroll.setWidget(self._inner)
        vb.addWidget(self._scroll, stretch=1)

        self.refresh()

    def refresh(self):
        """Re-read the slices directory and rebuild the list."""
        from mlrvp.config import get_slices_dir
        slices_dir = str(get_slices_dir())

        # Remove existing rows (keep the stretch at end)
        while self._inner_layout.count() > 1:
            item = self._inner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows.clear()

        if not os.path.isdir(slices_dir):
            return

        files = [
            f for f in os.listdir(slices_dir)
            if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
        ]
        # Sort
        self._sort_mode = self._sort_combo.currentData() or 0
        files = self._sort_files(files, slices_dir)

        # Pre-load metadata for all files in one pass
        import json as _json
        new_meta_cache: dict[str, dict] = {}
        for fname in files:
            fpath = os.path.join(slices_dir, fname)
            meta_path = os.path.splitext(fpath)[0] + '.json'
            if fpath in self._meta_cache:
                new_meta_cache[fpath] = self._meta_cache[fpath]
            elif os.path.isfile(meta_path):
                try:
                    with open(meta_path, 'r', encoding='utf-8') as _mf:
                        new_meta_cache[fpath] = _json.load(_mf)
                except (ValueError, OSError):
                    pass
        self._meta_cache = new_meta_cache

        for fname in files:
            fpath = os.path.join(slices_dir, fname)
            row = _SliceLibraryRow(fpath, metadata=self._meta_cache.get(fpath))
            row.delete_requested.connect(self._on_delete)
            row.load_to_channel.connect(self._on_load_to_channel)
            self._rows.append(row)
            self._inner_layout.insertWidget(self._inner_layout.count() - 1, row)

        self._apply_filter()

    def _sort_files(self, files: list, slices_dir: str) -> list:
        def _safe_mtime(f):
            try:
                return os.path.getmtime(os.path.join(slices_dir, f))
            except OSError:
                return 0.0
        if self._sort_mode == 0:
            return sorted(files, key=str.lower)
        elif self._sort_mode == 1:
            return sorted(files, key=str.lower, reverse=True)
        elif self._sort_mode == 2:
            return sorted(files, key=_safe_mtime, reverse=True)
        elif self._sort_mode == 3:
            return sorted(files, key=_safe_mtime)
        return files

    def _on_sort_changed(self, _idx: int):
        self.refresh()

    def _on_search_text_changed(self, _text: str = ""):
        """Debounce search input to avoid filtering on every keystroke."""
        self._search_debounce.start()

    def _apply_filter(self, _text: str = ""):
        """Filter rows by search text (substring match on filename)."""
        query = self._search_edit.text().strip().lower()
        for row in self._rows:
            name = os.path.basename(row._filepath).lower()
            row.setVisible(not query or query in name)

    def _on_delete(self, filepath: str):
        # Also remove metadata sidecar
        meta = os.path.splitext(filepath)[0] + '.json'
        if os.path.isfile(meta):
            try:
                os.remove(meta)
            except OSError:
                pass
        self.refresh()

    def _on_load_to_channel(self, filepath: str, ch_idx: int):
        self.load_to_channel.emit(filepath, ch_idx)

    def _on_row_clicked(self, row: _SliceLibraryRow, ctrl: bool = False,
                        shift: bool = False):
        """Handle click with Ctrl (toggle) and Shift (range) modifiers."""
        visible = [r for r in self._rows if r.isVisible()]
        if shift and hasattr(self, '_last_clicked_row') and self._last_clicked_row in visible:
            # Range select between anchor and clicked row
            anchor_idx = visible.index(self._last_clicked_row)
            cur_idx = visible.index(row) if row in visible else -1
            if cur_idx >= 0:
                lo, hi = sorted((anchor_idx, cur_idx))
                if not ctrl:
                    for r in self._rows:
                        r.selected = False
                for i in range(lo, hi + 1):
                    visible[i].selected = True
        elif ctrl:
            row.selected = not row.selected
            self._last_clicked_row = row
        else:
            for r in self._rows:
                r.selected = False
            row.selected = True
            self._last_clicked_row = row
        sel_count = sum(1 for r in self._rows if r.selected)
        if sel_count > 0:
            self._batch_del_btn.setText(f"Delete ({sel_count})")
            self._batch_del_btn.setVisible(True)
        else:
            self._batch_del_btn.setVisible(False)

    def _batch_delete(self):
        """Delete all selected rows after confirmation."""
        selected = [r for r in self._rows if r.selected]
        if not selected:
            return
        names = [os.path.basename(r._filepath) for r in selected]
        msg = f"Delete {len(names)} slice(s)?\n" + "\n".join(names[:10])
        if len(names) > 10:
            msg += f"\n\u2026and {len(names) - 10} more"
        reply = QMessageBox.question(
            self, "Batch Delete",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for r in selected:
            try:
                os.remove(r._filepath)
            except OSError:
                pass
            meta = os.path.splitext(r._filepath)[0] + '.json'
            if os.path.isfile(meta):
                try:
                    os.remove(meta)
                except OSError:
                    pass
        self.refresh()

    def add_file(self, filepath: str):
        """Add a single newly-saved slice without full refresh."""
        self.refresh()


class ToolsView(QWidget):
    """Tabbed tools panel: Record + Slice."""

    recorder_param_changed = Signal(str, object)
    record_toggled = Signal(bool)
    slice_saved = Signal(str)  # filepath of saved slice

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        # Record tab
        self._recorder_panel = RecorderPanel()
        self._recorder_panel.param_changed.connect(self.recorder_param_changed.emit)
        self._recorder_panel.record_toggled.connect(self.record_toggled.emit)
        tabs.addTab(self._recorder_panel, "Record")

        # Slice tab
        self._slice_panel = SlicePanel()
        self._slice_panel.slice_saved.connect(self.slice_saved.emit)
        tabs.addTab(self._slice_panel, "Slice")

        layout.addWidget(tabs)
        self._tabs = tabs

    @property
    def recorder_panel(self) -> RecorderPanel:
        return self._recorder_panel

    @property
    def slice_panel(self) -> SlicePanel:
        return self._slice_panel
