"""Tools view — Record + Sample Slice tabs for mlrVp."""

import os
import math
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

AUDIO_EXTENSIONS = {'.wav', '.aiff', '.aif', '.flac', '.ogg', '.mp3'}


class SliceWaveformWidget(QWidget):
    """Interactive waveform display with draggable start/end markers."""

    selection_changed = Signal(float, float)  # start_frac, end_frac (0.0-1.0)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(80)
        self.setMaximumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAcceptDrops(True)
        self._mono: np.ndarray | None = None  # raw mono audio for dynamic rendering
        self._peaks: list[float] = []  # coarse overview peaks
        self._transients: np.ndarray = np.array([], dtype=int)  # sample indices
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


class SlicePanel(QFrame):
    """Sample slicing tool — load, set regions, slice and save."""

    slice_saved = Signal(str)  # emits filepath of saved slice

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
        layout.addWidget(self._waveform, stretch=1)

        # Time info row
        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        self._start_label = QLabel("Start: 0.000s")
        self._end_label = QLabel("End: 0.000s")
        self._dur_label = QLabel("Dur: 0.000s")
        for lbl in (self._start_label, self._end_label, self._dur_label):
            lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
            info_row.addWidget(lbl)
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
        self._play_btn.clicked.connect(self._toggle_play)
        transport_row.addWidget(self._play_btn)

        self._stop_btn = QPushButton("\u25A0")  # stop square
        self._stop_btn.setFixedSize(36, 26)
        self._stop_btn.setStyleSheet(btn_style)
        self._stop_btn.clicked.connect(self._stop_play)
        transport_row.addWidget(self._stop_btn)

        self._play_sel_btn = QPushButton("\u25B6 Sel")
        self._play_sel_btn.setFixedSize(50, 26)
        self._play_sel_btn.setStyleSheet(btn_style)
        self._play_sel_btn.clicked.connect(self._play_selection)
        transport_row.addWidget(self._play_sel_btn)

        # Loop toggle
        self._loop_cb = QCheckBox("Loop")
        self._loop_cb.setChecked(True)
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

        # Slice controls row
        slice_row = QHBoxLayout()
        slice_row.setSpacing(4)
        slice_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("slice_name")
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
        slice_row.addWidget(self._slice_btn)
        layout.addLayout(slice_row)

        # Status
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        layout.addWidget(self._status)

        # Slice library at bottom
        self._library = SliceLibraryPanel()
        self._library.load_requested.connect(self.load_file)
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
        self._start_label.setText(f"Start: {start_sec:.3f}s")
        self._end_label.setText(f"End: {end_sec:.3f}s")
        self._dur_label.setText(f"Dur: {end_sec - start_sec:.3f}s")

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
        try:
            import sounddevice as sd
            start_idx = int(start_frac * len(self._audio_data))
            end_idx = int(end_frac * len(self._audio_data))
            segment = self._audio_data[start_idx:end_idx]
            loop = self._loop_cb.isChecked()
            if loop:
                # Build looped buffer (repeat enough for ~30s or 10 repeats)
                reps = max(1, min(10, int(30.0 * self._sample_rate / max(len(segment), 1))))
                self._loop_segment_len = len(segment)
                segment = np.tile(segment, (reps, 1)) if segment.ndim == 2 else np.tile(segment, reps)
            else:
                self._loop_segment_len = 0
            sd.play(segment, self._sample_rate)
            self._playing = True
            self._play_start_frac = start_frac
            self._play_end_frac = end_frac
            self._play_pos = 0
            self._play_total = len(segment)
            self._play_timer.start()
            self._play_btn.setText("\u23F8")  # pause symbol
        except Exception as e:
            self._status.setText(f"Play error: {e}")

    def _stop_play(self):
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
        self._play_pos += int(self._sample_rate * 0.030)
        if self._play_pos >= self._play_total:
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

    def add_library_file(self, filepath: str):
        """Add a file to the combo dropdown (called externally)."""
        for i in range(self._file_combo.count()):
            if self._file_combo.itemData(i) == filepath:
                return
        self._file_combo.addItem(os.path.basename(filepath), filepath)


# RecorderPanel is imported from effects_view
from mlrvp.gui.views.effects_view import RecorderPanel


class _SliceLibraryRow(QWidget):
    """Single row in the slice library: filename label (draggable) + delete button."""

    delete_requested = Signal(str)  # filepath

    def __init__(self, filepath: str, parent=None):
        super().__init__(parent)
        self._filepath = filepath
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

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(18, 18)
        del_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_SECONDARY}; "
            f"border: none; font-size: 9px; padding: 0px; min-height: 0px; }}"
            f"QPushButton:hover {{ color: #ff6666; background: transparent; }}"
        )
        del_btn.setToolTip(f"Delete {name}")
        del_btn.clicked.connect(self._on_delete)
        hl.addWidget(del_btn)

        self.setStyleSheet(
            f"_SliceLibraryRow {{ background: transparent; }}"
            f"_SliceLibraryRow:hover {{ background: #222; }}"
        )

    # ── Drag support ──────────────────────────────────────────────────
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
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
    """Scrollable list of saved slices from the slices directory."""

    load_requested = Signal(str)  # drag-to-slice-editor target

    def __init__(self, parent=None):
        super().__init__(parent)
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

        if not os.path.isdir(slices_dir):
            return

        files = sorted(
            f for f in os.listdir(slices_dir)
            if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
        )
        for fname in files:
            fpath = os.path.join(slices_dir, fname)
            row = _SliceLibraryRow(fpath)
            row.delete_requested.connect(self._on_delete)
            self._inner_layout.insertWidget(self._inner_layout.count() - 1, row)

    def _on_delete(self, filepath: str):
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
