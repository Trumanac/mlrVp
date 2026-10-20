"""Tools view — Record + Sample Slice tabs for mlrVp."""

import os
import sys
import math
import logging
import threading
import numpy as np
import soundfile as sf

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QFrame, QComboBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QCheckBox, QSizePolicy, QFileDialog,
    QMenu, QScrollArea, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QProgressBar, QListWidget, QAbstractItemView,
    QTreeWidget, QTreeWidgetItem, QStackedWidget, QSlider,
)
from PySide6.QtCore import Qt, Signal, QTimer, QMimeData, QUrl, QThread, QFile
from PySide6.QtGui import (
    QPainter, QColor, QPen, QMouseEvent, QWheelEvent, QDrag
)

from mlrvp.gui.styles import (
    ACCENT_GOLD, BG_BLACK, BG_PANEL, SEPARATOR, TEXT_SECONDARY, TEXT_PRIMARY,
    ACCENT_BLUE, RED, GREEN,
    ACCENT_BLUE_HOVER, ACCENT_GOLD_HOVER, STATUS_ERROR_HOVER,
)
from mlrvp.gui.widgets.fader import HorizontalFader
from mlrvp.gui.main_thread import run_on_main_thread

log = logging.getLogger("mlrvp")


TRASH_NAME = "Recycle Bin" if sys.platform == "win32" else "Trash"


def _trash_files(paths, with_sidecars: bool = False) -> list[str]:
    """Move files to the Recycle Bin / Trash instead of deleting them.

    With *with_sidecars*, each file's ``.json`` metadata sidecar goes too.
    Returns the paths that could not be moved (left untouched).
    """
    failed = []
    for path in paths:
        if os.path.exists(path) and not QFile.moveToTrash(path):
            failed.append(path)
            continue
        if with_sidecars:
            sidecar = os.path.splitext(path)[0] + ".json"
            if os.path.isfile(sidecar):
                QFile.moveToTrash(sidecar)
    if failed:
        log.warning("Could not move to %s: %s", TRASH_NAME, failed)
    return failed


def _confirm_trash(parent, title: str, paths) -> bool:
    names = [os.path.basename(p) for p in paths]
    msg = f"Move {len(names)} file(s) to the {TRASH_NAME}?\n\n" + "\n".join(names[:10])
    if len(names) > 10:
        msg += f"\n\u2026and {len(names) - 10} more"
    reply = QMessageBox.question(
        parent, title, msg,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


def _warn_trash_failures(parent, failed) -> None:
    if not failed:
        return
    names = "\n".join(os.path.basename(p) for p in failed[:10])
    QMessageBox.warning(
        parent, "Delete",
        f"Couldn't move {len(failed)} file(s) to the {TRASH_NAME} \u2014 they were "
        f"left in place:\n\n{names}",
    )


def _is_slices_subfolder(path: str) -> bool:
    """True only for a folder strictly inside the slices folder.

    Guards destructive folder actions: rejects the slices folder itself and
    look-alike siblings such as "Slices_backup" (a plain string-prefix
    check accepted both).
    """
    from pathlib import Path
    from mlrvp.config import get_slices_dir
    try:
        root = get_slices_dir().resolve()
        target = Path(path).resolve()
    except OSError:
        return False
    return target != root and root in target.parents

AUDIO_EXTENSIONS = {'.wav', '.aiff', '.aif', '.flac', '.ogg', '.mp3'}


class _AnalysisWorker(QThread):
    """Background worker that runs BPM + Key detection on a list of files.

    Emits `result(filepath, bpm, key)` per file (bpm may be NaN if undetected,
    key may be empty string). Emits `progress(done, total)` after each file
    and `finished()` when complete or cancelled.
    """

    progress = Signal(int, int)        # current, total
    result = Signal(str, float, str)   # filepath, bpm, key
    error = Signal(str, str)           # filepath, message

    def __init__(self, files: list[str], parent=None):
        super().__init__(parent)
        self._files = list(files)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from mlrvp.audio.analysis import detect_bpm_and_key
        except Exception:
            return
        total = len(self._files)
        for i, fp in enumerate(self._files):
            if self._cancelled:
                break
            try:
                bpm, key = detect_bpm_and_key(fp)
            except Exception as e:
                self.error.emit(fp, str(e))
                bpm, key = None, None
            self.result.emit(
                fp,
                float(bpm) if bpm is not None else float("nan"),
                key or "",
            )
            self.progress.emit(i + 1, total)


class SliceWaveformWidget(QWidget):
    """Interactive waveform display with draggable start/end markers."""

    selection_changed = Signal(float, float)  # start_frac, end_frac (0.0-1.0)
    region_clicked = Signal(int)  # region index clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_min_height(self, 80)
        bind_max_height(self, 140)
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
        p.setPen(QPen(QColor(GREEN), 2))
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
        bind_min_width(self._file_combo, 120)
        self._file_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._file_combo.currentIndexChanged.connect(self._on_file_selected)
        file_row.addWidget(self._file_combo)
        self._load_btn = QPushButton("LOAD")
        bind_fixed_size(self._load_btn, 50, 24)
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
        bind_fixed_width(self._start_spin, 80)
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
        bind_fixed_width(self._end_spin, 80)
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
        bind_fixed_width(self._dur_spin, 80)
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
        bind_fixed_size(self._zx_btn, 32, 22)
        self._zx_btn.setToolTip("Zero-crossing snap (auto-snap markers on release)")
        self._zx_btn.setStyleSheet(snap_btn_style)
        self._zx_btn.toggled.connect(self._on_zx_toggled)
        info_row.addWidget(self._zx_btn)

        # Grid snap toggle
        self._grid_btn = QPushButton("GRID")
        self._grid_btn.setCheckable(True)
        bind_fixed_size(self._grid_btn, 40, 22)
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
        bind_fixed_size(self._play_btn, 36, 26)
        self._play_btn.setStyleSheet(btn_style)
        self._play_btn.setToolTip("Play / Pause full sample (Space)")
        self._play_btn.clicked.connect(self._toggle_play)
        transport_row.addWidget(self._play_btn)

        self._stop_btn = QPushButton("\u25A0")  # stop square
        bind_fixed_size(self._stop_btn, 36, 26)
        self._stop_btn.setStyleSheet(btn_style)
        self._stop_btn.setToolTip("Stop playback")
        self._stop_btn.clicked.connect(self._stop_play)
        transport_row.addWidget(self._stop_btn)

        self._play_sel_btn = QPushButton("\u25B6 Sel")
        bind_fixed_size(self._play_sel_btn, 50, 26)
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
        bind_fixed_size(_tsep, 1, 22)
        _tsep.setStyleSheet(f"background: {SEPARATOR};")
        transport_row.addWidget(_tsep)

        # Bar-count selector for auto-loop
        self._bar_combo = QComboBox()
        bind_fixed_size(self._bar_combo, 60, 24)
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
        bind_fixed_size(self._auto_loop_btn, 46, 26)
        self._auto_loop_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_BLUE}; color: {BG_BLACK}; "
            f"font-weight: bold; font-size: 9px; border: none; }}"
            f"QPushButton:hover {{ background: {ACCENT_BLUE_HOVER}; }}"
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
        bind_fixed_size(_bsep, 1, 22)
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
        bind_fixed_size(self._bpm_spin, 58, 24)
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
        bind_fixed_size(self._cue_vol_fader, 80, 20)
        self._cue_vol_fader.set_value(0.5)  # 100 % (maps 0-1 → 0-200%)
        self._cue_vol_fader.setToolTip("Cue preview volume (0–200%)")
        self._cue_vol_fader.value_changed.connect(self._on_cue_vol_changed)
        cue_row.addWidget(self._cue_vol_fader)

        cue_dev_lbl = QLabel("Dev:")
        cue_dev_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9px;")
        cue_row.addWidget(cue_dev_lbl)

        self._cue_device_combo = QComboBox()
        bind_fixed_height(self._cue_device_combo, 22)
        bind_min_width(self._cue_device_combo, 100)
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
        bind_fixed_size(self._cue_ch_combo, 50, 22)
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
        bind_fixed_size(self._cue_mode_combo, 80, 22)
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
        bind_fixed_size(self._chop_mode_combo, 80, 22)
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
        bind_fixed_size(self._slices_spin, 50, 22)
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
        bind_fixed_size(self._sens_fader, 70, 20)
        self._sens_fader.set_value(0.5)
        self._sens_fader.setEnabled(False)
        self._sens_fader.setToolTip("Transient detection sensitivity (Transient mode only)")
        chop_row.addWidget(self._sens_fader)

        self._chop_btn = QPushButton("CHOP")
        bind_fixed_size(self._chop_btn, 50, 22)
        self._chop_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_BLUE}; color: {BG_BLACK}; "
            f"font-weight: bold; font-size: 9px; border: none; }}"
            f"QPushButton:hover {{ background: {ACCENT_BLUE_HOVER}; }}"
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
        bind_fixed_size(self._slice_btn, 90, 26)
        self._slice_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; "
            f"font-weight: bold; font-size: 10px; border: none; }}"
            f"QPushButton:hover {{ background: {ACCENT_GOLD_HOVER}; }}"
        )
        self._slice_btn.clicked.connect(self._do_slice)
        self._slice_btn.setToolTip(
            "Save the selected region as a WAV file to your Slices folder.\n"
            "With FIT enabled, the slice is resampled to an exact bar length."
        )
        slice_row.addWidget(self._slice_btn)

        self._export_all_btn = QPushButton("EXPORT ALL")
        bind_fixed_size(self._export_all_btn, 80, 26)
        self._export_all_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_BLUE}; color: {BG_BLACK}; "
            f"font-weight: bold; font-size: 9px; border: none; }}"
            f"QPushButton:hover {{ background: {ACCENT_BLUE_HOVER}; }}"
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
        bind_min_height(self._library, 80)
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

        # Use real playback position from the preview service when available
        sel_span = self._play_end_frac - self._play_start_frac
        if self._preview_service is not None and self._preview_service.is_playing:
            if self._loop_segment_len > 0:
                progress = self._preview_service.loop_progress
            else:
                progress = self._preview_service.playback_progress
            frac = self._play_start_frac + progress * sel_span
        else:
            # Fallback: estimate from timer (CUE+MAIN mode without service)
            self._play_pos += int(self._sample_rate * 0.030)
            if self._play_pos >= self._play_total:
                if self._loop_segment_len <= 0:
                    self._stop_play()
                    return
            if self._loop_segment_len > 0:
                pos_in_loop = self._play_pos % self._loop_segment_len
                frac = self._play_start_frac + (pos_in_loop / self._loop_segment_len) * sel_span
            else:
                frac = self._play_start_frac + (self._play_pos / max(self._play_total, 1)) * sel_span
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

                # Thread-safe progress update
                _exp, _tot = exported, len(regions)
                run_on_main_thread(lambda e=_exp, t=_tot: self._status.setText(
                    f"Exported {e}/{t} slices\u2026"
                ))

            if failed:
                return f"\u26A0 {len(failed)} slices failed \u2014 {exported}/{len(regions)} exported"
            return f"Exported {exported} slices to {out_dir}"

        def _run_export():
            # Always finish on the main thread so the EXPORT ALL button is
            # re-enabled even if the export raises unexpectedly.
            msg = "\u26A0 Export failed \u2014 see log"
            try:
                msg = _do_export()
            except Exception:
                log.exception("Slice export failed")
            run_on_main_thread(lambda: self._finish_export(msg))

        threading.Thread(target=_run_export, daemon=True).start()

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
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size, bind_fixed_width, bind_max_height, bind_max_width, bind_min_height, bind_min_width


class _SliceLibraryRow(QWidget):
    """Single row in the slice library: filename label (draggable) + BPM badge + delete button."""

    delete_requested = Signal(str)  # filepath
    load_to_channel = Signal(str, int)  # filepath, channel_index (0-based)

    def __init__(self, filepath: str, metadata: dict | None = None, parent=None):
        super().__init__(parent)
        self._filepath = filepath
        self._selected = False
        self._bpm: float | None = None
        self._key: str | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bind_fixed_height(self, 22)
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
        bind_fixed_width(self._bpm_lbl, 40)
        self._bpm_lbl.setStyleSheet(
            f"color: {ACCENT_GOLD}; font-size: 8px; font-weight: bold; background: transparent;"
        )
        hl.addWidget(self._bpm_lbl)

        # Key badge (shown if metadata sidecar has a key)
        self._key_lbl = QLabel("")
        bind_fixed_width(self._key_lbl, 40)
        self._key_lbl.setStyleSheet(
            f"color: {ACCENT_BLUE}; font-size: 8px; font-weight: bold; background: transparent;"
        )
        hl.addWidget(self._key_lbl)
        self._load_metadata(metadata)

        del_btn = QPushButton("\u2715")
        bind_fixed_size(del_btn, 18, 18)
        del_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_SECONDARY}; "
            f"border: none; font-size: 9px; padding: 0px; min-height: 0px; }}"
            f"QPushButton:hover {{ color: {STATUS_ERROR_HOVER}; background: transparent; }}"
        )
        del_btn.setToolTip(f"Delete {name}")
        del_btn.clicked.connect(self._on_delete)
        hl.addWidget(del_btn)

        self._update_bg()

    def _load_metadata(self, cached: dict | None = None):
        """Apply BPM + Key badges from pre-loaded metadata or read companion .json sidecar."""
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
                self._bpm = float(bpm)
                self._bpm_lbl.setText(f"{bpm:.0f}bpm")
            else:
                self._bpm = None
                self._bpm_lbl.setText("")
            key = meta.get("key")
            if key and isinstance(key, str):
                self._key = key
                self._key_lbl.setText(key)
            else:
                self._key = None
                self._key_lbl.setText("")
        else:
            self._bpm = None
            self._key = None
            self._bpm_lbl.setText("")
            self._key_lbl.setText("")

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
                "background: transparent;"
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
        from mlrvp.gui.widgets.overflow_menu import MENU_QSS
        menu.setStyleSheet(MENU_QSS)
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
        # Collect all selected filepaths from parent panel (multi-file drag)
        urls = [QUrl.fromLocalFile(self._filepath)]
        parent = self.parent()
        while parent and not isinstance(parent, SliceLibraryPanel):
            parent = parent.parent()
        if parent is not None:
            selected = [r._filepath for r in parent._rows if r.selected and r._filepath != self._filepath]
            if selected:
                urls = [QUrl.fromLocalFile(self._filepath)] + [
                    QUrl.fromLocalFile(p) for p in selected
                ]
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls(urls)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _on_delete(self):
        if not _confirm_trash(self, "Delete Slice", [self._filepath]):
            return
        failed = _trash_files([self._filepath], with_sidecars=True)
        if failed:
            _warn_trash_failures(self, failed)
            return
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
        bind_fixed_height(self._search_edit, 20)
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
        bind_fixed_height(self._sort_combo, 20)
        self._sort_combo.setStyleSheet(
            f"QComboBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 4px; }}"
        )
        self._sort_combo.addItem("Name A-Z", 0)
        self._sort_combo.addItem("Name Z-A", 1)
        self._sort_combo.addItem("Newest", 2)
        self._sort_combo.addItem("Oldest", 3)
        self._sort_combo.addItem("BPM \u2191", 4)
        self._sort_combo.addItem("BPM \u2193", 5)
        self._sort_combo.addItem("Key A-Z", 6)
        self._sort_combo.addItem("Key Z-A", 7)
        self._sort_combo.setCurrentIndex(2)  # Default: Newest
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        filter_row.addWidget(self._sort_combo)

        # Scan BPM + Key button
        self._scan_btn = QPushButton("Scan")
        bind_fixed_height(self._scan_btn, 20)
        self._scan_btn.setToolTip("Scan BPM + Key for all slices missing data")
        self._scan_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {ACCENT_BLUE}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ background: {SEPARATOR}; }}"
        )
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        filter_row.addWidget(self._scan_btn)

        # Batch delete button
        self._batch_del_btn = QPushButton("Delete")
        bind_fixed_height(self._batch_del_btn, 20)
        self._batch_del_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ color: {STATUS_ERROR_HOVER}; }}"
        )
        self._batch_del_btn.setVisible(False)
        self._batch_del_btn.clicked.connect(self._batch_delete)
        filter_row.addWidget(self._batch_del_btn)

        vb.addLayout(filter_row)

        # Filter row 2: Key + BPM range + Clear
        filter_row2 = QHBoxLayout()
        filter_row2.setContentsMargins(4, 0, 4, 2)
        filter_row2.setSpacing(4)

        _key_lbl = QLabel("Key:")
        _key_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 8px; background: transparent;"
        )
        filter_row2.addWidget(_key_lbl)

        self._key_filter = QComboBox()
        bind_fixed_height(self._key_filter, 20)
        self._key_filter.setStyleSheet(
            f"QComboBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 4px; }}"
        )
        self._key_filter.addItem("All Keys", "")
        self._key_filter.currentIndexChanged.connect(lambda _i: self._apply_filter())
        filter_row2.addWidget(self._key_filter, stretch=1)

        _bpm_lbl = QLabel("BPM:")
        _bpm_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 8px; background: transparent;"
        )
        filter_row2.addWidget(_bpm_lbl)

        self._bpm_min = QDoubleSpinBox()
        self._bpm_min.setRange(0.0, 400.0)
        self._bpm_min.setDecimals(0)
        self._bpm_min.setSingleStep(1)
        self._bpm_min.setValue(0)
        self._bpm_min.setSpecialValueText("Min")
        bind_fixed_height(self._bpm_min, 20)
        bind_fixed_width(self._bpm_min, 50)
        self._bpm_min.setStyleSheet(
            f"QDoubleSpinBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 0px 2px; }}"
        )
        self._bpm_min.valueChanged.connect(lambda _v: self._apply_filter())
        filter_row2.addWidget(self._bpm_min)

        _dash = QLabel("\u2013")
        _dash.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 8px; background: transparent;"
        )
        filter_row2.addWidget(_dash)

        self._bpm_max = QDoubleSpinBox()
        self._bpm_max.setRange(0.0, 400.0)
        self._bpm_max.setDecimals(0)
        self._bpm_max.setSingleStep(1)
        self._bpm_max.setValue(0)
        self._bpm_max.setSpecialValueText("Max")
        bind_fixed_height(self._bpm_max, 20)
        bind_fixed_width(self._bpm_max, 50)
        self._bpm_max.setStyleSheet(
            f"QDoubleSpinBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 0px 2px; }}"
        )
        self._bpm_max.valueChanged.connect(lambda _v: self._apply_filter())
        filter_row2.addWidget(self._bpm_max)

        self._clear_filter_btn = QPushButton("\u2715")
        bind_fixed_size(self._clear_filter_btn, 20, 20)
        self._clear_filter_btn.setToolTip("Clear filters")
        self._clear_filter_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; }}"
            f"QPushButton:hover {{ color: {ACCENT_GOLD}; }}"
        )
        self._clear_filter_btn.clicked.connect(self._clear_filters)
        filter_row2.addWidget(self._clear_filter_btn)

        vb.addLayout(filter_row2)

        # Scan progress (hidden when idle)
        self._scan_progress = QProgressBar()
        bind_fixed_height(self._scan_progress, 8)
        self._scan_progress.setTextVisible(False)
        self._scan_progress.setStyleSheet(
            f"QProgressBar {{ background: {BG_BLACK}; border: 1px solid {SEPARATOR}; }}"
            f"QProgressBar::chunk {{ background: {ACCENT_BLUE}; }}"
        )
        self._scan_progress.setVisible(False)
        vb.addWidget(self._scan_progress)
        self._scan_worker: _AnalysisWorker | None = None

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

        self._refresh_key_filter_options()
        self._apply_filter()

    def _refresh_key_filter_options(self):
        """Re-populate the key filter combo from currently loaded metadata."""
        prev = self._key_filter.currentData() if self._key_filter.count() > 0 else ""
        self._key_filter.blockSignals(True)
        self._key_filter.clear()
        self._key_filter.addItem("All Keys", "")
        keys = sorted({
            (m.get("key") or "").strip()
            for m in self._meta_cache.values()
            if m.get("key")
        })
        for k in keys:
            self._key_filter.addItem(k, k)
        # Restore previous selection if still present
        idx = self._key_filter.findData(prev) if prev else 0
        self._key_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self._key_filter.blockSignals(False)

    def _sort_files(self, files: list, slices_dir: str) -> list:
        def _safe_mtime(f):
            try:
                return os.path.getmtime(os.path.join(slices_dir, f))
            except OSError:
                return 0.0

        def _bpm_of(f):
            meta = self._meta_cache.get(os.path.join(slices_dir, f))
            if meta:
                bpm = meta.get("bpm")
                if bpm is not None:
                    try:
                        return float(bpm)
                    except (TypeError, ValueError):
                        pass
            return None

        def _key_of(f):
            meta = self._meta_cache.get(os.path.join(slices_dir, f))
            if meta:
                k = meta.get("key")
                if k:
                    return str(k)
            return None

        if self._sort_mode == 0:
            return sorted(files, key=str.lower)
        elif self._sort_mode == 1:
            return sorted(files, key=str.lower, reverse=True)
        elif self._sort_mode == 2:
            return sorted(files, key=_safe_mtime, reverse=True)
        elif self._sort_mode == 3:
            return sorted(files, key=_safe_mtime)
        elif self._sort_mode == 4:
            # BPM ascending; None last
            return sorted(files, key=lambda f: (_bpm_of(f) is None, _bpm_of(f) or 0.0, f.lower()))
        elif self._sort_mode == 5:
            # BPM descending; None last
            return sorted(files, key=lambda f: (_bpm_of(f) is None, -(_bpm_of(f) or 0.0), f.lower()))
        elif self._sort_mode == 6:
            # Key A-Z; None last
            return sorted(files, key=lambda f: (_key_of(f) is None, (_key_of(f) or "").lower(), f.lower()))
        elif self._sort_mode == 7:
            # Key Z-A; None last (still after detected keys)
            with_key = [f for f in files if _key_of(f)]
            without = [f for f in files if not _key_of(f)]
            with_key.sort(key=lambda f: ((_key_of(f) or "").lower(), f.lower()), reverse=True)
            without.sort(key=str.lower)
            return with_key + without
        return files

    def _on_sort_changed(self, _idx: int):
        self.refresh()

    def _on_search_text_changed(self, _text: str = ""):
        """Debounce search input to avoid filtering on every keystroke."""
        self._search_debounce.start()

    def _apply_filter(self, _text: str = ""):
        """Filter rows by name search + key + BPM range (AND-combined)."""
        query = self._search_edit.text().strip().lower()
        key_filter = self._key_filter.currentData() if hasattr(self, "_key_filter") else ""
        bpm_min = self._bpm_min.value() if hasattr(self, "_bpm_min") else 0.0
        bpm_max = self._bpm_max.value() if hasattr(self, "_bpm_max") else 0.0
        for row in self._rows:
            name = os.path.basename(row._filepath).lower()
            visible = True
            if query and query not in name:
                visible = False
            if visible and key_filter and row._key != key_filter:
                visible = False
            if visible and (bpm_min > 0 or bpm_max > 0):
                if row._bpm is not None:
                    if bpm_min > 0 and row._bpm < bpm_min:
                        visible = False
                    if bpm_max > 0 and row._bpm > bpm_max:
                        visible = False
                # Rows without BPM data always pass the BPM filter
            row.setVisible(visible)

    def _clear_filters(self):
        """Reset all filter controls to defaults."""
        self._search_edit.clear()
        self._key_filter.setCurrentIndex(0)
        self._bpm_min.setValue(0)
        self._bpm_max.setValue(0)
        self._apply_filter()

    # ── BPM + Key scan ────────────────────────────────────────────────
    def _on_scan_clicked(self):
        """Launch a background BPM+Key scan on slices missing data."""
        if self._scan_worker is not None and self._scan_worker.isRunning():
            # Cancel running scan
            self._scan_worker.cancel()
            self._scan_btn.setText("Scan")
            return
        targets: list[str] = []
        for row in self._rows:
            meta = self._meta_cache.get(row._filepath, {}) or {}
            if not meta.get("bpm") or not meta.get("key"):
                targets.append(row._filepath)
        if not targets:
            self._scan_progress.setVisible(False)
            return
        self._scan_progress.setRange(0, len(targets))
        self._scan_progress.setValue(0)
        self._scan_progress.setVisible(True)
        self._scan_btn.setText("Cancel")

        worker = _AnalysisWorker(targets, parent=self)
        worker.result.connect(self._on_scan_result)
        worker.progress.connect(self._on_scan_progress)
        worker.finished.connect(self._on_scan_finished)
        self._scan_worker = worker
        worker.start()

    def _on_scan_result(self, filepath: str, bpm: float, key: str):
        """Update sidecar JSON + cache + matching row badge."""
        import json as _json
        import math as _math
        meta = dict(self._meta_cache.get(filepath, {}) or {})
        if bpm is not None and not _math.isnan(bpm) and bpm > 0:
            meta["bpm"] = float(bpm)
        if key:
            meta["key"] = key
        self._meta_cache[filepath] = meta
        # Persist to sidecar
        try:
            sidecar = os.path.splitext(filepath)[0] + ".json"
            existing = {}
            if os.path.isfile(sidecar):
                try:
                    with open(sidecar, "r", encoding="utf-8") as f:
                        existing = _json.load(f) or {}
                except (ValueError, OSError):
                    existing = {}
            existing.update(meta)
            with open(sidecar, "w", encoding="utf-8") as f:
                _json.dump(existing, f, indent=2)
        except OSError:
            pass
        # Live-update matching row badge
        for row in self._rows:
            if row._filepath == filepath:
                row._load_metadata(meta)
                break

    def _on_scan_progress(self, current: int, total: int):
        self._scan_progress.setRange(0, total)
        self._scan_progress.setValue(current)

    def _on_scan_finished(self):
        self._scan_progress.setVisible(False)
        self._scan_btn.setText("Scan")
        self._refresh_key_filter_options()
        self._apply_filter()
        self._scan_worker = None

    def _on_delete(self, filepath: str):
        # The row already moved the file and its sidecar to the trash.
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
        paths = [r._filepath for r in selected]
        if not _confirm_trash(self, "Batch Delete", paths):
            return
        _warn_trash_failures(self, _trash_files(paths, with_sidecars=True))
        self.refresh()

    def add_file(self, filepath: str):
        """Add a single newly-saved slice without full refresh."""
        self.refresh()


# ---------------------------------------------------------------------------
# Library Tab — Slices + Samples explorer
# ---------------------------------------------------------------------------


class _NumericTableItem(QTableWidgetItem):
    """QTableWidgetItem that sorts numerically by stored UserRole float."""

    def __lt__(self, other):
        try:
            a = float(self.data(Qt.ItemDataRole.UserRole))
            b = float(other.data(Qt.ItemDataRole.UserRole))
            # NaN always sorts last regardless of direction
            import math as _m
            if _m.isnan(a) and _m.isnan(b):
                return False
            if _m.isnan(a):
                return False
            if _m.isnan(b):
                return True
            return a < b
        except (TypeError, ValueError):
            return super().__lt__(other)


class _LibraryTable(QTableWidget):
    """QTableWidget subclass with multi-file drag and Delete-key shortcut."""

    delete_pressed = Signal()
    rows_dragged = Signal(list)  # list[str] filepaths

    def __init__(self, columns: list[str], parent=None):
        super().__init__(0, len(columns), parent)
        self.setHorizontalHeaderLabels(columns)
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(20)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionsMovable(False)
        self.setShowGrid(False)
        self.setStyleSheet(
            f"QTableWidget {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; gridline-color: {SEPARATOR}; "
            f"alternate-background-color: #1a1a1a; }}"
            f"QHeaderView::section {{ background: {BG_PANEL}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; font-weight: bold; padding: 2px 4px; "
            f"border: 0px; border-right: 1px solid {SEPARATOR}; }}"
            f"QTableWidget::item:selected {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, supportedActions):
        rows = sorted({i.row() for i in self.selectedIndexes()})
        if not rows:
            return
        paths: list[str] = []
        for r in rows:
            # Filepath is always on column 1 (Name), never on the play column
            name_item = self.item(r, 1)
            if name_item is None:
                continue
            fp = name_item.data(Qt.ItemDataRole.UserRole + 1)
            if fp:
                paths.append(str(fp))
        if not paths:
            return
        self.rows_dragged.emit(paths)
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        # Show a label so the user knows what they're dragging
        from PySide6.QtGui import QPixmap, QPainter, QFont, QFontMetrics
        label = os.path.basename(paths[0])
        if len(paths) > 1:
            label = f"{len(paths)} files"
        fm_font = QFont()
        fm_font.setPointSize(8)
        fm = QFontMetrics(fm_font)
        w = fm.horizontalAdvance(label) + 16
        h = 18
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setFont(fm_font)
        p.fillRect(0, 0, w, h, QColor("#3a3a2a"))
        p.setPen(QColor("#c8aa5a"))
        p.drawText(8, 13, label)
        p.end()
        drag.setPixmap(pm)
        drag.setHotSpot(pm.rect().center())
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def sortByColumn(self, column: int, order):
        """Prevent sorting by the play-button column (col 0)."""
        if column == 0:
            return
        super().sortByColumn(column, order)


def _make_folder_style(item: QTreeWidgetItem):
    """Style a folder/directory tree node.

    Col 0 (COL_PLAY): shows the +/- expand indicator.
    Col 1 (COL_NAME): folder label, bold, bright accent colour.
    All columns get a subtle dark-tinted background.
    """
    from PySide6.QtGui import QFont as _QFont, QBrush as _QBrush
    # Col 0 — expand indicator (starts expanded → '−')
    item.setText(0, "\u2212")  # −
    item.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
    item.setForeground(0, QColor(ACCENT_GOLD))
    _ind_font = _QFont()
    _ind_font.setPointSize(10)
    _ind_font.setBold(True)
    item.setFont(0, _ind_font)
    # Col 1 — folder name, bold, visually distinct colour
    _name_font = _QFont()
    _name_font.setBold(True)
    _name_font.setPointSize(9)
    item.setFont(1, _name_font)
    item.setForeground(1, QColor("#7ecfea"))  # bright cyan-blue
    # Subtle row background tint for all columns
    _bg = _QBrush(QColor("#1a2530"))
    for _c in range(9):
        item.setBackground(_c, _bg)


class _LibraryFolderTree(QTreeWidget):
    """QTreeWidget showing sample library as a collapsible folder hierarchy.

    Columns match _LibraryTable (9 columns for the SAMPLES tab).
    Signals mirror those used by _LibraryTable for consistent wiring.
    """

    delete_pressed = Signal()
    play_requested = Signal(str)   # filepath — emitted on COL_PLAY click
    load_requested = Signal(str)   # filepath — emitted on item double-click
    files_move_requested = Signal(list, str)  # [src_paths], dest_dir

    def __init__(self, columns: list[str], drop_enabled: bool = False, parent=None):
        super().__init__(parent)
        self._drop_enabled = drop_enabled
        self.setHeaderLabels(columns)
        self.setColumnCount(len(columns))
        self.setSortingEnabled(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        if drop_enabled:
            self.setAcceptDrops(True)
            self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
            self.setDropIndicatorShown(True)
        else:
            self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setIndentation(8)
        header = self.header()
        header.setStretchLastSection(True)
        header.setSectionsMovable(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        self.setStyleSheet(
            f"QTreeWidget {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 11px; alternate-background-color: #1a1a1a; border: none; }}"
            f"QTreeWidget::item {{ padding: 1px 0; }}"
            f"QTreeWidget::item:selected {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
            # Suppress ALL default branch indicators and connector lines
            f"QTreeWidget::branch {{ background: {BG_BLACK}; image: none; border-image: none; }}"
            f"QTreeWidget::branch:has-siblings:!adjoins-item {{ image: none; border-image: none; }}"
            f"QTreeWidget::branch:has-siblings:adjoins-item {{ image: none; border-image: none; }}"
            f"QTreeWidget::branch:!has-children:!has-siblings:adjoins-item {{ image: none; border-image: none; }}"
            f"QTreeWidget::branch:closed:has-children:has-siblings {{ image: none; border-image: none; }}"
            f"QTreeWidget::branch:closed:has-children:!has-siblings {{ image: none; border-image: none; }}"
            f"QTreeWidget::branch:open:has-children:has-siblings {{ image: none; border-image: none; }}"
            f"QTreeWidget::branch:open:has-children:!has-siblings {{ image: none; border-image: none; }}"
            f"QHeaderView::section {{ background: {BG_PANEL}; color: {TEXT_SECONDARY}; "
            f"font-size: 9px; font-weight: bold; padding: 2px 4px; "
            f"border: 0px; border-right: 1px solid {SEPARATOR}; }}"
        )
        self.verticalHeader().setDefaultSectionSize(22) if hasattr(self, 'verticalHeader') else None
        self.itemClicked.connect(self._on_item_clicked)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.itemExpanded.connect(self._on_item_expanded)
        self.itemCollapsed.connect(self._on_item_collapsed)

    # ── Event handling ────────────────────────────────────────────────

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        if item.childCount() > 0:
            # Folder node: clicking the +/- indicator (col 0) toggles expand
            if column == 0:
                item.setExpanded(not item.isExpanded())
            return
        if column == 0:  # COL_PLAY
            fp = item.data(1, Qt.ItemDataRole.UserRole + 1)
            if fp:
                self.play_requested.emit(str(fp))

    def _on_item_expanded(self, item: QTreeWidgetItem):
        if item.childCount() > 0:
            item.setText(0, "\u2212")  # −

    def _on_item_collapsed(self, item: QTreeWidgetItem):
        if item.childCount() > 0:
            item.setText(0, "+")

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        if item.childCount() > 0:
            return  # Folder node — toggle expand instead
        fp = item.data(1, Qt.ItemDataRole.UserRole + 1)
        if fp:
            self.load_requested.emit(str(fp))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, supportedActions):
        paths: list[str] = []
        for item in self.selectedItems():
            if item.childCount() > 0:
                continue  # Skip folder nodes
            fp = item.data(1, Qt.ItemDataRole.UserRole + 1)
            if fp:
                paths.append(str(fp))
        if not paths:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        from PySide6.QtGui import QPixmap, QPainter, QFont, QFontMetrics
        label = os.path.basename(paths[0])
        if len(paths) > 1:
            label = f"{len(paths)} files"
        fm_font = QFont()
        fm_font.setPointSize(8)
        fm = QFontMetrics(fm_font)
        w = fm.horizontalAdvance(label) + 16
        h = 18
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setFont(fm_font)
        p.fillRect(0, 0, w, h, QColor("#3a3a2a"))
        p.setPen(QColor("#c8aa5a"))
        p.drawText(8, 13, label)
        p.end()
        drag.setPixmap(pm)
        drag.setHotSpot(pm.rect().center())
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    # ── Drop support (when drop_enabled=True) ─────────────────────────

    def dragEnterEvent(self, event):
        if self._drop_enabled and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if not self._drop_enabled or not event.mimeData().hasUrls():
            event.ignore()
            return
        target = self.itemAt(event.position().toPoint())
        # Walk up to a folder node
        while target and target.childCount() == 0:
            target = target.parent()
        if target is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not self._drop_enabled or not event.mimeData().hasUrls():
            event.ignore()
            return
        pos = event.position().toPoint()
        target = self.itemAt(pos)
        while target and target.childCount() == 0:
            target = target.parent()
        if target is None:
            event.ignore()
            return
        dest_dir = target.data(0, Qt.ItemDataRole.UserRole)
        if not dest_dir:
            event.ignore()
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_move_requested.emit(paths, str(dest_dir))
        event.acceptProposedAction()

    def selected_filepaths(self) -> list[str]:
        """Return filepaths for all selected leaf (sample) items."""
        out = []
        for item in self.selectedItems():
            if item.childCount() > 0:
                continue
            fp = item.data(1, Qt.ItemDataRole.UserRole + 1)
            if fp:
                out.append(str(fp))
        return out


class _LibraryTableBase(QWidget):
    """Shared base for SLICES/SAMPLES sub-tabs.

    Provides search + sort headers + Key/BPM filter row + scan progress.
    Subclasses implement `_refresh()`, `_columns()`, `_build_context_menu()`,
    `_on_delete_selected()`, and `_files_to_scan()`.
    """

    load_to_slicer = Signal(str)
    load_to_channel = Signal(str, int)  # filepath, ch_idx (-1 = active)
    preview_requested = Signal(str, float)  # filepath, volume 0.0–1.0
    preview_stop_requested = Signal()        # stop current preview

    # Column-index constants for shared columns (subclasses define rest)
    COL_NAME = 1
    COL_BPM = 2
    COL_KEY = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scan_worker: _AnalysisWorker | None = None
        self._row_filepaths: list[str] = []   # row_index → filepath (matches model order)
        self._meta: dict[str, dict] = {}      # filepath → metadata dict
        self._view_mode: str = "all"          # "all" or "folder"
        self._stack: QStackedWidget | None = None  # set by subclass if folder view supported
        self._previewing_fp: str = ""         # filepath currently being previewed ("" = none)

        vb = QVBoxLayout(self)
        vb.setContentsMargins(2, 2, 2, 2)
        vb.setSpacing(2)

        # Toolbar row 1: search + refresh + scan
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search\u2026")
        self._search_edit.setClearButtonEnabled(True)
        bind_fixed_height(self._search_edit, 20)
        bind_max_width(self._search_edit, 300)
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 1px 4px; }}"
        )
        self._search_edit.textChanged.connect(self._on_search_changed)
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(250)
        self._search_debounce.timeout.connect(self._apply_filter)
        row1.addWidget(self._search_edit)

        self._refresh_btn = QPushButton("Refresh")
        bind_fixed_size(self._refresh_btn, 50, 20)
        self._refresh_btn.setToolTip("Refresh sample list")
        self._refresh_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ color: {ACCENT_GOLD}; }}"
        )
        self._refresh_btn.clicked.connect(self.refresh)
        row1.addWidget(self._refresh_btn)

        self._scan_btn = QPushButton("Scan BPM")
        bind_fixed_size(self._scan_btn, 60, 20)
        self._scan_btn.setToolTip("Detect BPM and musical key for unscanned files")
        self._scan_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {ACCENT_BLUE}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ background: {SEPARATOR}; }}"
        )
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        row1.addWidget(self._scan_btn)

        self._batch_del_btn = QPushButton("Delete")
        bind_fixed_height(self._batch_del_btn, 20)
        self._batch_del_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ color: {STATUS_ERROR_HOVER}; }}"
        )
        self._batch_del_btn.setVisible(False)
        self._batch_del_btn.clicked.connect(self._on_delete_selected)
        row1.addWidget(self._batch_del_btn)
        self._toolbar_row1 = row1  # exposed so subclasses can insert extra buttons
        vb.addLayout(row1)

        # Filter row 2: Key + BPM range + Clear
        row2 = QHBoxLayout()
        row2.setSpacing(4)

        _kl = QLabel("Key:")
        _kl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 8px;")
        row2.addWidget(_kl)
        self._key_filter = QComboBox()
        bind_fixed_height(self._key_filter, 20)
        bind_fixed_width(self._key_filter, 90)
        self._key_filter.setStyleSheet(
            f"QComboBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 4px; }}"
        )
        self._key_filter.addItem("All Keys", "")
        self._key_filter.currentIndexChanged.connect(lambda _i: self._apply_filter())
        row2.addWidget(self._key_filter)

        _bl = QLabel("BPM:")
        _bl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 8px;")
        row2.addWidget(_bl)
        self._bpm_min = QDoubleSpinBox()
        self._bpm_min.setRange(0.0, 400.0)
        self._bpm_min.setDecimals(0)
        self._bpm_min.setSingleStep(1)
        self._bpm_min.setSpecialValueText("Min")
        bind_fixed_height(self._bpm_min, 20)
        bind_fixed_width(self._bpm_min, 58)
        self._bpm_min.setStyleSheet(
            f"QDoubleSpinBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 0px 2px; }}"
        )
        self._bpm_min.valueChanged.connect(lambda _v: self._apply_filter())
        row2.addWidget(self._bpm_min)
        _dash = QLabel("\u2013")
        _dash.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 8px;")
        row2.addWidget(_dash)
        self._bpm_max = QDoubleSpinBox()
        self._bpm_max.setRange(0.0, 400.0)
        self._bpm_max.setDecimals(0)
        self._bpm_max.setSingleStep(1)
        self._bpm_max.setSpecialValueText("Max")
        bind_fixed_height(self._bpm_max, 20)
        bind_fixed_width(self._bpm_max, 58)
        self._bpm_max.setStyleSheet(
            f"QDoubleSpinBox {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 0px 2px; }}"
        )
        self._bpm_max.valueChanged.connect(lambda _v: self._apply_filter())
        row2.addWidget(self._bpm_max)
        self._clear_filter_btn = QPushButton("Clear")
        bind_fixed_height(self._clear_filter_btn, 20)
        self._clear_filter_btn.setToolTip("Clear all filters")
        self._clear_filter_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ color: {ACCENT_GOLD}; }}"
        )
        self._clear_filter_btn.clicked.connect(self._clear_filters)
        row2.addWidget(self._clear_filter_btn)

        # Preview volume (right after Clear, group pushed left by trailing stretch)
        _vl = QLabel("Vol:")
        _vl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 8px;")
        row2.addWidget(_vl)
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(50)
        bind_fixed_width(self._vol_slider, 80)
        bind_fixed_height(self._vol_slider, 18)
        self._vol_slider.setToolTip("Preview playback volume")
        self._vol_slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ background: {SEPARATOR}; height: 4px; "
            f"border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ background: {ACCENT_GOLD}; width: 10px; height: 10px; "
            f"margin: -3px 0; border-radius: 5px; }}"
            f"QSlider::sub-page:horizontal {{ background: {ACCENT_GOLD}; height: 4px; "
            f"border-radius: 2px; }}"
        )
        row2.addWidget(self._vol_slider)

        # Stop-preview button
        self._stop_preview_btn = QPushButton("■")
        bind_fixed_size(self._stop_preview_btn, 22, 18)
        self._stop_preview_btn.setToolTip("Stop preview playback")
        self._stop_preview_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 10px; border: 1px solid {SEPARATOR}; padding: 0; }}"
            f"QPushButton:hover {{ color: {STATUS_ERROR_HOVER}; border-color: {STATUS_ERROR_HOVER}; }}"
            f"QPushButton:disabled {{ color: #333; border-color: #333; }}"
        )
        self._stop_preview_btn.setEnabled(False)
        self._stop_preview_btn.clicked.connect(self._on_stop_preview_clicked)
        row2.addWidget(self._stop_preview_btn)

        row2.addStretch(1)  # push all filter controls left
        self._toolbar_row2 = row2  # exposed for subclasses

        vb.addLayout(row2)

        # Table
        self._table = _LibraryTable(self._columns())
        self._table.itemDoubleClicked.connect(self._on_double_click)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.delete_pressed.connect(self._on_delete_selected)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        vb.addWidget(self._table, stretch=1)

        # Scan progress (hidden when idle)
        self._scan_progress = QProgressBar()
        bind_fixed_height(self._scan_progress, 8)
        self._scan_progress.setTextVisible(False)
        self._scan_progress.setStyleSheet(
            f"QProgressBar {{ background: {BG_BLACK}; border: 1px solid {SEPARATOR}; }}"
            f"QProgressBar::chunk {{ background: {ACCENT_BLUE}; }}"
        )
        self._scan_progress.setVisible(False)
        vb.addWidget(self._scan_progress)

    # ── Subclass overrides ────────────────────────────────────────────
    def _columns(self) -> list[str]:
        raise NotImplementedError

    def refresh(self):
        raise NotImplementedError

    def _files_to_scan(self) -> list[str]:
        """Return list of filepaths needing BPM/Key analysis (missing data)."""
        out = []
        for fp in self._row_filepaths:
            meta = self._meta.get(fp, {}) or {}
            if not meta.get("bpm") or not meta.get("key"):
                out.append(fp)
        return out

    def _on_scan_result_persist(self, filepath: str, bpm: float, key: str):
        """Subclass: persist BPM+Key for one file (sidecar JSON or shared cache)."""
        raise NotImplementedError

    def _build_context_menu(self, menu: QMenu, filepaths: list[str]) -> None:
        """Subclass: populate context menu with actions for the given selection."""
        raise NotImplementedError

    # ── Search / filter ───────────────────────────────────────────────
    def _on_search_changed(self, _text: str = ""):
        self._search_debounce.start()

    def _apply_filter(self):
        if self._view_mode == "folder":
            self._apply_filter_tree()
            return
        query = self._search_edit.text().strip().lower()
        key_filter = self._key_filter.currentData() or ""
        bpm_min = self._bpm_min.value()
        bpm_max = self._bpm_max.value()
        for r in range(self._table.rowCount()):
            visible = True
            name_item = self._table.item(r, self.COL_NAME)
            name = name_item.text().lower() if name_item else ""
            if query and query not in name:
                visible = False
            if visible and key_filter:
                key_item = self._table.item(r, self.COL_KEY)
                key_val = key_item.text() if key_item else ""
                if key_val != key_filter:
                    visible = False
            if visible and (bpm_min > 0 or bpm_max > 0):
                bpm_item = self._table.item(r, self.COL_BPM)
                if bpm_item is not None:
                    try:
                        bpm_val = float(bpm_item.data(Qt.ItemDataRole.UserRole))
                        import math as _m
                        if not _m.isnan(bpm_val):
                            if bpm_min > 0 and bpm_val < bpm_min:
                                visible = False
                            if bpm_max > 0 and bpm_val > bpm_max:
                                visible = False
                    except (TypeError, ValueError):
                        pass
            if visible:
                visible = self._extra_row_visible(r)
            self._table.setRowHidden(r, not visible)

    def _apply_filter_tree(self):
        """Override in subclasses that support folder view."""
        pass

    def _extra_row_visible(self, r: int) -> bool:
        """Hook: return False to additionally hide a row. Called after standard filters."""
        return True

    def _clear_filters(self):
        self._search_edit.clear()
        self._key_filter.setCurrentIndex(0)
        self._bpm_min.setValue(0)
        self._bpm_max.setValue(0)
        self._apply_filter()

    def _refresh_key_filter_options(self):
        prev = self._key_filter.currentData() or ""
        self._key_filter.blockSignals(True)
        self._key_filter.clear()
        self._key_filter.addItem("All Keys", "")
        keys = sorted({(m.get("key") or "").strip() for m in self._meta.values() if m.get("key")})
        for k in keys:
            self._key_filter.addItem(k, k)
        idx = self._key_filter.findData(prev) if prev else 0
        self._key_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self._key_filter.blockSignals(False)

    # ── Selection / actions ───────────────────────────────────────────
    def _selected_filepaths(self) -> list[str]:
        rows = sorted({i.row() for i in self._table.selectedIndexes()})
        out = []
        for r in rows:
            item = self._table.item(r, self.COL_NAME)
            if item is None:
                continue
            fp = item.data(Qt.ItemDataRole.UserRole + 1)
            if fp:
                out.append(str(fp))
        return out

    def _on_selection_changed(self):
        n = len({i.row() for i in self._table.selectedIndexes()})
        if n > 1:
            self._batch_del_btn.setText(f"Delete ({n})")
            self._batch_del_btn.setVisible(True)
        elif n == 1:
            self._batch_del_btn.setText("Delete")
            self._batch_del_btn.setVisible(True)
        else:
            self._batch_del_btn.setVisible(False)

    def _on_cell_clicked(self, row: int, col: int):
        if col == self.COL_PLAY:
            name_item = self._table.item(row, self.COL_NAME)
            if name_item:
                fp = str(name_item.data(Qt.ItemDataRole.UserRole + 1) or "")
                if not fp:
                    return
                if fp == self._previewing_fp:
                    # Clicking the same row again — stop
                    self._do_stop_preview()
                else:
                    self._previewing_fp = fp
                    self._stop_preview_btn.setEnabled(True)
                    self.preview_requested.emit(fp, self._vol_slider.value() / 100.0)

    def _on_stop_preview_clicked(self):
        self._do_stop_preview()

    def _do_stop_preview(self):
        """Emit stop signal and reset local playing state."""
        self._previewing_fp = ""
        self._stop_preview_btn.setEnabled(False)
        self.preview_stop_requested.emit()

    def notify_preview_stopped(self):
        """Called externally (e.g. from AppController) when playback ends naturally."""
        self._previewing_fp = ""
        self._stop_preview_btn.setEnabled(False)

    def _on_double_click(self, item: QTableWidgetItem):
        r = item.row()
        name_item = self._table.item(r, self.COL_NAME)
        if name_item is None:
            return
        fp = name_item.data(Qt.ItemDataRole.UserRole + 1)
        if fp:
            self._handle_double_click(str(fp))

    def _handle_double_click(self, filepath: str):
        """Subclass: react to default double-click action."""
        raise NotImplementedError

    def _on_context_menu(self, pos):
        paths = self._selected_filepaths()
        # If right-click was on a row that's not in the current selection,
        # use just that row instead.
        idx = self._table.indexAt(pos)
        if idx.isValid():
            name_item = self._table.item(idx.row(), self.COL_NAME)
            if name_item is not None:
                clicked_fp = name_item.data(Qt.ItemDataRole.UserRole + 1)
                if clicked_fp and clicked_fp not in paths:
                    self._table.clearSelection()
                    self._table.selectRow(idx.row())
                    paths = [str(clicked_fp)]
        if not paths:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: rgba(30, 30, 30, 230); color: {TEXT_PRIMARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 9px; }}"
            f"QMenu::item {{ padding: 4px 16px; }}"
            f"QMenu::item:selected {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        self._build_context_menu(menu, paths)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_delete_selected(self):
        """Subclass implements actual delete behavior."""
        pass

    # ── Scan ──────────────────────────────────────────────────────────
    def _on_scan_clicked(self):
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._scan_worker.cancel()
            self._scan_btn.setText("Scan BPM + Key")
            return
        targets = self._files_to_scan()
        if not targets:
            return
        self._scan_progress.setRange(0, len(targets))
        self._scan_progress.setValue(0)
        self._scan_progress.setVisible(True)
        self._scan_btn.setText("Cancel Scan")
        worker = _AnalysisWorker(targets, parent=self)
        worker.result.connect(self._on_scan_result)
        worker.progress.connect(self._on_scan_progress)
        worker.finished.connect(self._on_scan_finished)
        self._scan_worker = worker
        worker.start()

    def _on_scan_result(self, filepath: str, bpm: float, key: str):
        import math as _m
        meta = dict(self._meta.get(filepath, {}) or {})
        if not _m.isnan(bpm) and bpm > 0:
            meta["bpm"] = float(bpm)
        if key:
            meta["key"] = key
        self._meta[filepath] = meta
        self._on_scan_result_persist(filepath, bpm, key)
        # Live-update matching row
        for r in range(self._table.rowCount()):
            name_item = self._table.item(r, self.COL_NAME)
            if name_item is None:
                continue
            if name_item.data(Qt.ItemDataRole.UserRole + 1) == filepath:
                if "bpm" in meta:
                    bpm_item = self._table.item(r, self.COL_BPM)
                    if bpm_item is not None:
                        bpm_item.setText(f"{meta['bpm']:.0f}")
                        bpm_item.setData(Qt.ItemDataRole.UserRole, float(meta["bpm"]))
                if "key" in meta:
                    key_item = self._table.item(r, self.COL_KEY)
                    if key_item is not None:
                        key_item.setText(meta["key"])
                break

    def _on_scan_progress(self, current: int, total: int):
        self._scan_progress.setRange(0, total)
        self._scan_progress.setValue(current)

    def _on_scan_finished(self):
        self._scan_progress.setVisible(False)
        self._scan_btn.setText("Scan BPM + Key")
        self._refresh_key_filter_options()
        self._apply_filter()
        self._scan_worker = None


class _LibrarySlicesTab(_LibraryTableBase):
    """SLICES sub-tab — scans the slices folder with folder tree, favorites, tags, recents."""

    # Column indices: ▶ | Name | BPM | Key | Bars | Duration | Date | Tags
    COL_PLAY = 0
    COL_BARS = 4
    COL_DUR = 5
    COL_DATE = 6
    COL_TAGS = 7

    def _columns(self) -> list[str]:
        return ["▶", "Name", "BPM", "Key", "Bars", "Duration", "Date", "Tags"]

    def __init__(self, parent=None):
        from mlrvp.config import (get_slice_favorites, get_slice_recents,
                                   get_slice_tags, get_slice_hidden)
        super().__init__(parent)
        self._favorites: set = set(get_slice_favorites())
        self._recents: list = list(get_slice_recents())
        self._tags: dict = dict(get_slice_tags())
        self._hidden: set = set(get_slice_hidden())

        # ── Folder tree setup ─────────────────────────────────────────
        vb = self.layout()
        table_idx: int | None = None
        for _i in range(vb.count()):
            _item = vb.itemAt(_i)
            if _item and _item.widget() is self._table:
                table_idx = _i
                vb.takeAt(_i)
                break

        self._tree = _LibraryFolderTree(self._columns(), drop_enabled=True)
        self._tree.delete_pressed.connect(self._on_delete_selected)
        self._tree.play_requested.connect(self._on_tree_play_requested)
        self._tree.load_requested.connect(self._handle_double_click)
        self._tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.header().sectionClicked.connect(self._on_tree_header_clicked)
        self._tree.files_move_requested.connect(self._on_files_move_requested)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._table)
        self._stack.addWidget(self._tree)
        _insert_at = table_idx if table_idx is not None else max(0, vb.count() - 1)
        vb.insertWidget(_insert_at, self._stack, 1)

        # View toggle button
        self._folder_view_btn = QPushButton("All")
        bind_fixed_size(self._folder_view_btn, 36, 20)
        self._folder_view_btn.setCheckable(True)
        self._folder_view_btn.setToolTip("Switch between flat list and folder tree")
        self._folder_view_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; font-weight: bold; border: 1px solid {SEPARATOR}; padding: 0 4px; }}"
            f"QPushButton:hover {{ color: {ACCENT_GOLD}; }}"
            f"QPushButton:checked {{ color: {ACCENT_GOLD}; background: {SEPARATOR}; "
            f"border-color: {ACCENT_GOLD}; }}"
        )
        self._folder_view_btn.clicked.connect(self._on_toggle_view)
        self._toolbar_row1.insertWidget(2, self._folder_view_btn)

        # ★ Favs filter
        self._fav_filter_btn = QPushButton("★ Favs")
        self._fav_filter_btn.setCheckable(True)
        bind_fixed_size(self._fav_filter_btn, 52, 20)
        self._fav_filter_btn.setToolTip("Show only favorited slices")
        self._fav_filter_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ color: {ACCENT_GOLD}; }}"
            f"QPushButton:checked {{ color: {BG_BLACK}; background: {ACCENT_GOLD}; "
            f"border: 1px solid {ACCENT_GOLD}; }}"
        )
        self._fav_filter_btn.clicked.connect(self._apply_filter)
        self._toolbar_row1.addWidget(self._fav_filter_btn)

        # Recent filter
        self._recent_filter_btn = QPushButton("Recent")
        self._recent_filter_btn.setCheckable(True)
        bind_fixed_size(self._recent_filter_btn, 52, 20)
        self._recent_filter_btn.setToolTip("Show only recently used slices")
        self._recent_filter_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ color: {ACCENT_GOLD}; }}"
            f"QPushButton:checked {{ color: {BG_BLACK}; background: {ACCENT_GOLD}; "
            f"border: 1px solid {ACCENT_GOLD}; }}"
        )
        self._recent_filter_btn.clicked.connect(self._apply_filter)
        self._toolbar_row1.addWidget(self._recent_filter_btn)

        # Tag filter (in row2 before the trailing stretch)
        _ins = self._toolbar_row2.count() - 1
        _tl = QLabel("Tag:")
        _tl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 8px;")
        self._toolbar_row2.insertWidget(_ins, _tl)
        self._tag_filter_edit = QLineEdit()
        self._tag_filter_edit.setPlaceholderText("tag\u2026")
        self._tag_filter_edit.setClearButtonEnabled(True)
        bind_fixed_height(self._tag_filter_edit, 20)
        bind_max_width(self._tag_filter_edit, 110)
        self._tag_filter_edit.setStyleSheet(
            f"QLineEdit {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 1px 4px; }}"
        )
        self._tag_filter_edit.textChanged.connect(self._on_search_changed)
        self._toolbar_row2.insertWidget(_ins + 1, self._tag_filter_edit)

        # Tree sort state
        self._tree_sort_col: int = self.COL_NAME
        self._tree_sort_asc: bool = True

        # Default to folder view
        self._view_mode = "folder"
        self._folder_view_btn.setChecked(True)
        self._folder_view_btn.setText("Dir")
        self._stack.setCurrentIndex(1)

        self.refresh()

    def refresh(self):
        from mlrvp.config import get_slices_dir
        slices_dir = str(get_slices_dir())
        self._row_filepaths = []
        self._meta = {}
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        if not os.path.isdir(slices_dir):
            self._table.setSortingEnabled(True)
            return
        import json as _json
        seen: set[str] = set()
        for root, _dirs, files in os.walk(slices_dir):
            for fname in sorted(files):
                if os.path.splitext(fname)[1].lower() not in AUDIO_EXTENSIONS:
                    continue
                fpath = os.path.join(root, fname)
                if fpath in seen or fpath in self._hidden:
                    continue
                seen.add(fpath)
                meta = {}
                sidecar = os.path.splitext(fpath)[0] + ".json"
                if os.path.isfile(sidecar):
                    try:
                        with open(sidecar, "r", encoding="utf-8") as f:
                            meta = _json.load(f) or {}
                    except (ValueError, OSError):
                        meta = {}
                try:
                    mtime = os.path.getmtime(fpath)
                except OSError:
                    mtime = 0.0
                try:
                    info = sf.info(fpath)
                    duration = info.frames / float(info.samplerate or 1)
                except Exception:
                    duration = 0.0
                meta["_duration"] = duration
                self._meta[fpath] = meta
                self._row_filepaths.append(fpath)
                self._add_row(fpath, fname, meta, duration, mtime)
        self._table.setSortingEnabled(True)
        self._refresh_key_filter_options()
        self._apply_filter()
        h = self._table.horizontalHeader()
        h.setStretchLastSection(True)
        for c in range(self._table.columnCount()):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(self.COL_PLAY, 24)
        self._table.setColumnWidth(self.COL_NAME, 200)
        self._table.setColumnWidth(self.COL_BPM, 55)
        self._table.setColumnWidth(self.COL_KEY, 55)
        self._table.setColumnWidth(self.COL_BARS, 45)
        self._table.setColumnWidth(self.COL_DUR, 70)
        self._table.setColumnWidth(self.COL_TAGS, 120)
        if hasattr(self, "_tree"):
            self._build_tree()

    def _add_row(self, fpath: str, fname: str, meta: dict, duration: float, mtime: float):
        from datetime import datetime as _dt
        r = self._table.rowCount()
        self._table.insertRow(r)
        play_item = QTableWidgetItem("▶")
        play_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        play_item.setForeground(QColor(ACCENT_GOLD))
        play_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self._table.setItem(r, self.COL_PLAY, play_item)
        name_item = QTableWidgetItem(fname)
        name_item.setData(Qt.ItemDataRole.UserRole + 1, fpath)
        name_item.setToolTip(fpath)
        self._table.setItem(r, self.COL_NAME, name_item)
        bpm_val = meta.get("bpm")
        bpm_item = _NumericTableItem(f"{bpm_val:.0f}" if bpm_val else "")
        bpm_item.setData(Qt.ItemDataRole.UserRole, float(bpm_val) if bpm_val else float("nan"))
        bpm_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, self.COL_BPM, bpm_item)
        key_item = QTableWidgetItem(meta.get("key") or "")
        key_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, self.COL_KEY, key_item)
        bars_val = meta.get("bars") or _snap_to_bars(duration, meta.get("bpm"))
        bars_item = _NumericTableItem(str(int(bars_val)) if bars_val else "")
        bars_item.setData(Qt.ItemDataRole.UserRole, float(bars_val) if bars_val else float("nan"))
        bars_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, self.COL_BARS, bars_item)
        dur_text = f"{int(duration // 60)}:{duration % 60:04.1f}" if duration > 0 else ""
        dur_item = _NumericTableItem(dur_text)
        dur_item.setData(Qt.ItemDataRole.UserRole, float(duration) if duration > 0 else float("nan"))
        dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, self.COL_DUR, dur_item)
        try:
            date_text = _dt.fromtimestamp(mtime).strftime("%Y-%m-%d") if mtime > 0 else ""
        except (OSError, ValueError):
            date_text = ""
        date_item = _NumericTableItem(date_text)
        date_item.setData(Qt.ItemDataRole.UserRole, float(mtime))
        self._table.setItem(r, self.COL_DATE, date_item)
        tags_list = self._tags.get(fpath, [])
        tags_item = QTableWidgetItem(", ".join(tags_list))
        tags_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        tags_item.setForeground(QColor(TEXT_SECONDARY))
        self._table.setItem(r, self.COL_TAGS, tags_item)
        if fpath in self._favorites:
            name_item.setForeground(QColor(ACCENT_GOLD))

    def _add_tree_row(self, parent: QTreeWidgetItem, fpath: str, fname: str, meta: dict):
        """Add a leaf slice item to a tree node."""
        from PySide6.QtGui import QFont as _QFont
        from datetime import datetime as _dt
        item = QTreeWidgetItem(parent)
        item.setText(self.COL_PLAY, "\u25B6")
        item.setTextAlignment(self.COL_PLAY, Qt.AlignmentFlag.AlignCenter)
        item.setForeground(self.COL_PLAY, QColor(ACCENT_GOLD))
        _pf = _QFont()
        _pf.setPointSize(10)
        item.setFont(self.COL_PLAY, _pf)
        item.setText(self.COL_NAME, fname)
        item.setData(self.COL_NAME, Qt.ItemDataRole.UserRole + 1, fpath)
        item.setToolTip(self.COL_NAME, fpath)
        bpm_val = meta.get("bpm")
        item.setText(self.COL_BPM, f"{bpm_val:.0f}" if bpm_val else "")
        item.setData(self.COL_BPM, Qt.ItemDataRole.UserRole,
                     float(bpm_val) if bpm_val else float("nan"))
        item.setTextAlignment(self.COL_BPM, Qt.AlignmentFlag.AlignCenter)
        item.setText(self.COL_KEY, meta.get("key") or "")
        item.setTextAlignment(self.COL_KEY, Qt.AlignmentFlag.AlignCenter)
        duration = meta.get("_duration") or 0.0
        bars_val = meta.get("bars") or _snap_to_bars(duration, bpm_val)
        item.setText(self.COL_BARS, str(int(bars_val)) if bars_val else "")
        item.setData(self.COL_BARS, Qt.ItemDataRole.UserRole,
                     float(bars_val) if bars_val else float("nan"))
        item.setTextAlignment(self.COL_BARS, Qt.AlignmentFlag.AlignCenter)
        dur_text = f"{int(duration // 60)}:{duration % 60:04.1f}" if duration > 0 else ""
        item.setText(self.COL_DUR, dur_text)
        item.setData(self.COL_DUR, Qt.ItemDataRole.UserRole,
                     float(duration) if duration > 0 else float("nan"))
        item.setTextAlignment(self.COL_DUR, Qt.AlignmentFlag.AlignCenter)
        try:
            mtime = os.path.getmtime(fpath)
            date_text = _dt.fromtimestamp(mtime).strftime("%Y-%m-%d") if mtime > 0 else ""
        except (OSError, ValueError):
            date_text = ""
        item.setText(self.COL_DATE, date_text)
        item.setTextAlignment(self.COL_DATE, Qt.AlignmentFlag.AlignCenter)
        tags_list = self._tags.get(fpath, [])
        item.setText(self.COL_TAGS, ", ".join(tags_list))
        item.setForeground(self.COL_TAGS, QColor(TEXT_SECONDARY))
        if fpath in self._favorites:
            item.setForeground(self.COL_NAME, QColor(ACCENT_GOLD))

    def _handle_double_click(self, filepath: str):
        self.load_to_slicer.emit(filepath)

    # ── Context menu ──────────────────────────────────────────────────

    def _build_context_menu(self, menu: QMenu, filepaths: list[str]) -> None:
        n = len(filepaths)
        # Favorites
        if n == 1:
            fp = filepaths[0]
            is_fav = fp in self._favorites
            act_fav = menu.addAction("★ Remove from Favorites" if is_fav else "☆ Add to Favorites")
            act_fav.triggered.connect(lambda _c=False, p=fp: self._toggle_favorite([p]))
        else:
            act_fav = menu.addAction(f"☆ Add to Favorites ({n})")
            act_fav.triggered.connect(lambda _c=False: self._toggle_favorite(filepaths))
        # Tags (single only)
        if n == 1:
            fp = filepaths[0]
            cur_tags = self._tags.get(fp, [])
            tag_lbl = f"Tags: {', '.join(cur_tags)}" if cur_tags else "Edit Tags\u2026"
            act_tags = menu.addAction(tag_lbl)
            act_tags.triggered.connect(lambda _c=False, p=fp: self._edit_tags_dialog(p))
            if cur_tags:
                act_clr = menu.addAction("Clear Tags")
                act_clr.triggered.connect(lambda _c=False, p=fp: self._set_tags(p, []))
        menu.addSeparator()
        # Rename (single only)
        if n == 1:
            act_rename = menu.addAction("Rename\u2026")
            act_rename.triggered.connect(lambda _c=False, p=filepaths[0]: self._on_rename(p))
        # Load / Reveal
        if n == 1:
            act_load = menu.addAction("Load to Slicer")
            act_load.triggered.connect(lambda: self.load_to_slicer.emit(filepaths[0]))
            ch_menu = menu.addMenu("Load to Channel")
            for ch in range(8):
                a = ch_menu.addAction(f"Channel {ch + 1}")
                a.triggered.connect(lambda _c=False, c=ch, p=filepaths[0]:
                                    self.load_to_channel.emit(p, c))
            act_reveal = menu.addAction("Reveal in Explorer")
            act_reveal.triggered.connect(lambda: _reveal_in_explorer(filepaths[0]))
        menu.addSeparator()
        # Group into folder
        act_grp = menu.addAction(f"Group into Folder\u2026 ({n})" if n > 1
                                 else "Group into Folder\u2026")
        act_grp.triggered.connect(lambda _c=False: self._on_new_folder(filepaths))
        menu.addSeparator()
        # Remove from view (non-destructive)
        act_hide = menu.addAction(f"Remove from View ({n})" if n > 1 else "Remove from View")
        act_hide.triggered.connect(lambda _c=False: self._on_hide_selected(filepaths))
        # Delete from disk
        act_del = menu.addAction(f"Delete File\u2026 ({n})" if n > 1 else "Delete File\u2026")
        act_del.triggered.connect(lambda _c=False: self._on_disk_delete(filepaths))

    # ── Folder tree: tree-level context menu ──────────────────────────

    def _on_tree_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        # If clicking a folder node, offer folder actions
        if item and item.childCount() > 0:
            menu = QMenu(self)
            menu.setStyleSheet(
                f"QMenu {{ background: rgba(30,30,30,230); color: {TEXT_PRIMARY}; "
                f"border: 1px solid {SEPARATOR}; font-size: 9px; }}"
                f"QMenu::item {{ padding: 4px 16px; }}"
                f"QMenu::item:selected {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
            )
            dir_path = item.data(0, Qt.ItemDataRole.UserRole)
            act_ren = menu.addAction("Rename Folder\u2026")
            act_ren.triggered.connect(lambda _c=False, d=dir_path, i=item:
                                      self._on_rename_folder(d, i))
            act_del = menu.addAction("Delete Folder + Contents\u2026")
            act_del.triggered.connect(lambda _c=False, d=dir_path: self._on_delete_folder(d))
            menu.exec(self._tree.viewport().mapToGlobal(pos))
            return
        # Leaf (sample) selection
        paths = self._tree.selected_filepaths()
        if item and item.childCount() == 0:
            fp = item.data(self.COL_NAME, Qt.ItemDataRole.UserRole + 1)
            if fp and fp not in paths:
                self._tree.clearSelection()
                item.setSelected(True)
                paths = [str(fp)]
        if not paths:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: rgba(30,30,30,230); color: {TEXT_PRIMARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 9px; }}"
            f"QMenu::item {{ padding: 4px 16px; }}"
            f"QMenu::item:selected {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        self._build_context_menu(menu, paths)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # ── Delete / hide ─────────────────────────────────────────────────

    def _on_delete_selected(self):
        """Delete key: hide from view (non-destructive)."""
        paths = self._selected_filepaths()
        if not paths:
            return
        self._on_hide_selected(paths)

    def _on_hide_selected(self, paths: list[str]):
        """Remove slices from the library view without touching disk."""
        from mlrvp.config import save_slice_hidden
        for p in paths:
            self._hidden.add(p)
        save_slice_hidden(self._hidden)
        self.refresh()

    def _on_disk_delete(self, paths: list[str]):
        """Move slice files (and sidecars) to the Recycle Bin / Trash."""
        if not paths or not _confirm_trash(self, "Delete Slices", paths):
            return
        _warn_trash_failures(self, _trash_files(paths, with_sidecars=True))
        self.refresh()

    # ── Rename ────────────────────────────────────────────────────────

    def _on_rename(self, filepath: str):
        from PySide6.QtWidgets import QInputDialog
        old_name = os.path.splitext(os.path.basename(filepath))[0]
        ext = os.path.splitext(filepath)[1]
        text, ok = QInputDialog.getText(self, "Rename Slice", "New name:", text=old_name)
        if not ok or not text.strip():
            return
        new_name = text.strip()
        if not new_name.lower().endswith(ext.lower()):
            new_name += ext
        dest = os.path.join(os.path.dirname(filepath), new_name)
        if os.path.exists(dest):
            QMessageBox.warning(self, "Rename", f"A file named '{new_name}' already exists.")
            return
        try:
            os.rename(filepath, dest)
        except OSError as e:
            QMessageBox.warning(self, "Rename", str(e))
            return
        old_sidecar = os.path.splitext(filepath)[0] + ".json"
        new_sidecar = os.path.splitext(dest)[0] + ".json"
        if os.path.isfile(old_sidecar):
            try:
                os.rename(old_sidecar, new_sidecar)
            except OSError:
                pass
        self.refresh()

    # ── Folder management ─────────────────────────────────────────────

    def _on_new_folder(self, filepaths: list[str]):
        """Create a new subfolder inside slices dir and move the selected files into it."""
        import shutil
        from PySide6.QtWidgets import QInputDialog
        from mlrvp.config import get_slices_dir
        slices_dir = str(get_slices_dir())
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        folder = os.path.join(slices_dir, name.strip())
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "New Folder", str(e))
            return
        for src in filepaths:
            if not os.path.isfile(src):
                continue
            fname = os.path.basename(src)
            dest = os.path.join(folder, fname)
            try:
                shutil.move(src, dest)
                sidecar_src = os.path.splitext(src)[0] + ".json"
                sidecar_dst = os.path.splitext(dest)[0] + ".json"
                if os.path.isfile(sidecar_src):
                    shutil.move(sidecar_src, sidecar_dst)
            except OSError:
                pass
        self.refresh()

    def _on_rename_folder(self, dir_path: str, tree_item: QTreeWidgetItem):
        from PySide6.QtWidgets import QInputDialog
        if not _is_slices_subfolder(dir_path):
            return
        old_name = os.path.basename(dir_path)
        name, ok = QInputDialog.getText(self, "Rename Folder", "New name:", text=old_name)
        if not ok or not name.strip() or name.strip() == old_name:
            return
        if any(sep in name for sep in ("/", "\\")) or name.strip() in (".", ".."):
            QMessageBox.warning(self, "Rename Folder", "Folder names can't contain / or \\.")
            return
        dest = os.path.join(os.path.dirname(dir_path), name.strip())
        if os.path.exists(dest):
            QMessageBox.warning(self, "Rename Folder", f"'{name.strip()}' already exists.")
            return
        try:
            os.rename(dir_path, dest)
        except OSError as e:
            QMessageBox.warning(self, "Rename Folder", str(e))
            return
        self.refresh()

    def _on_delete_folder(self, dir_path: str):
        # Safety: only ever delete a real subfolder of the slices folder
        if not _is_slices_subfolder(dir_path):
            return
        name = os.path.basename(dir_path)
        reply = QMessageBox.question(
            self, "Delete Folder",
            f"Move folder '{name}' and everything in it to the {TRASH_NAME}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        _warn_trash_failures(self, _trash_files([dir_path]))
        self.refresh()

    # ── Favorites + Tags + Recents helpers ───────────────────────────

    def _toggle_favorite(self, filepaths: list[str]):
        any_not_fav = any(fp not in self._favorites for fp in filepaths)
        for fp in filepaths:
            if any_not_fav:
                self._favorites.add(fp)
            else:
                self._favorites.discard(fp)
        self._save_favorites()
        for fp in filepaths:
            self._refresh_fav_visual(fp, fp in self._favorites)
        self._apply_filter()

    def _edit_tags_dialog(self, filepath: str):
        from PySide6.QtWidgets import QInputDialog
        cur = ", ".join(self._tags.get(filepath, []))
        fname = os.path.basename(filepath)
        text, ok = QInputDialog.getText(
            self, "Edit Tags", f"Tags for {fname}\n(comma-separated):", text=cur
        )
        if not ok:
            return
        raw = [t.strip() for t in text.split(",") if t.strip()]
        self._set_tags(filepath, raw)

    def _set_tags(self, filepath: str, tags: list):
        if tags:
            self._tags[filepath] = tags
        else:
            self._tags.pop(filepath, None)
        self._save_tags()
        tags_text = ", ".join(tags)
        for r in range(self._table.rowCount()):
            name_item = self._table.item(r, self.COL_NAME)
            if name_item and str(name_item.data(Qt.ItemDataRole.UserRole + 1)) == filepath:
                ti = self._table.item(r, self.COL_TAGS)
                if ti:
                    ti.setText(tags_text)
                break
        self._update_tree_item_tags(filepath, tags_text)
        self._apply_filter()

    def _refresh_fav_visual(self, filepath: str, is_fav: bool):
        color = QColor(ACCENT_GOLD) if is_fav else QColor(TEXT_PRIMARY)
        for r in range(self._table.rowCount()):
            name_item = self._table.item(r, self.COL_NAME)
            if name_item and str(name_item.data(Qt.ItemDataRole.UserRole + 1)) == filepath:
                name_item.setForeground(color)
                break

        def _walk(item: QTreeWidgetItem):
            if item.childCount() > 0:
                for i in range(item.childCount()):
                    _walk(item.child(i))
            else:
                fp = item.data(self.COL_NAME, Qt.ItemDataRole.UserRole + 1)
                if str(fp) == filepath:
                    item.setForeground(self.COL_NAME, color)

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            _walk(root.child(i))

    def _update_tree_item_tags(self, filepath: str, tags_text: str):
        def _walk(item: QTreeWidgetItem):
            if item.childCount() > 0:
                for i in range(item.childCount()):
                    _walk(item.child(i))
            else:
                fp = item.data(self.COL_NAME, Qt.ItemDataRole.UserRole + 1)
                if str(fp) == filepath:
                    item.setText(self.COL_TAGS, tags_text)

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            _walk(root.child(i))

    def _track_recent(self, filepath: str):
        from mlrvp.config import SLICE_RECENTS_MAX
        if filepath in self._recents:
            self._recents.remove(filepath)
        self._recents.insert(0, filepath)
        if len(self._recents) > SLICE_RECENTS_MAX:
            self._recents = self._recents[:SLICE_RECENTS_MAX]
        self._save_recents()

    def _save_favorites(self):
        from mlrvp.config import save_slice_favorites
        save_slice_favorites(self._favorites)

    def _save_recents(self):
        from mlrvp.config import save_slice_recents
        save_slice_recents(self._recents)

    def _save_tags(self):
        from mlrvp.config import save_slice_tags
        save_slice_tags(self._tags)

    # ── Filter hooks ──────────────────────────────────────────────────

    def _extra_row_visible(self, r: int) -> bool:
        name_item = self._table.item(r, self.COL_NAME)
        if name_item is None:
            return True
        fp = str(name_item.data(Qt.ItemDataRole.UserRole + 1) or "")
        if not fp:
            return True
        if self._fav_filter_btn.isChecked() and fp not in self._favorites:
            return False
        if self._recent_filter_btn.isChecked() and fp not in self._recents:
            return False
        tag_q = self._tag_filter_edit.text().strip().lower()
        if tag_q:
            sample_tags = [t.lower() for t in self._tags.get(fp, [])]
            if not any(tag_q in t for t in sample_tags):
                return False
        return True

    def _clear_filters(self):
        self._search_edit.clear()
        self._key_filter.setCurrentIndex(0)
        self._bpm_min.setValue(0)
        self._bpm_max.setValue(0)
        self._fav_filter_btn.setChecked(False)
        self._recent_filter_btn.setChecked(False)
        self._tag_filter_edit.clear()
        self._apply_filter()

    # ── Folder view ───────────────────────────────────────────────────

    def _on_toggle_view(self, checked: bool):
        self._view_mode = "folder" if checked else "all"
        self._stack.setCurrentIndex(1 if checked else 0)
        self._folder_view_btn.setText("Dir" if checked else "All")
        self._apply_filter()

    def _apply_filter_tree(self):
        query = self._search_edit.text().strip().lower()
        key_filter = self._key_filter.currentData() or ""
        bpm_min = self._bpm_min.value()
        bpm_max = self._bpm_max.value()
        fav_only = self._fav_filter_btn.isChecked()
        recent_only = self._recent_filter_btn.isChecked()
        tag_q = self._tag_filter_edit.text().strip().lower()
        filter_active = bool(query or key_filter or bpm_min > 0 or bpm_max > 0
                             or fav_only or recent_only or tag_q)

        def _filter_item(item: QTreeWidgetItem) -> bool:
            if item.childCount() > 0:
                any_visible = False
                for i in range(item.childCount()):
                    if _filter_item(item.child(i)):
                        any_visible = True
                item.setHidden(not any_visible)
                if filter_active and any_visible:
                    item.setExpanded(True)
                return any_visible
            fp = item.data(self.COL_NAME, Qt.ItemDataRole.UserRole + 1)
            fp = str(fp) if fp else ""
            visible = True
            name = item.text(self.COL_NAME).lower()
            if query and query not in name:
                visible = False
            if visible and key_filter and item.text(self.COL_KEY) != key_filter:
                visible = False
            if visible and (bpm_min > 0 or bpm_max > 0):
                try:
                    bpm_val = float(item.data(self.COL_BPM, Qt.ItemDataRole.UserRole))
                    if not math.isnan(bpm_val):
                        if bpm_min > 0 and bpm_val < bpm_min:
                            visible = False
                        if bpm_max > 0 and bpm_val > bpm_max:
                            visible = False
                except (TypeError, ValueError):
                    pass
            if visible and fav_only and fp not in self._favorites:
                visible = False
            if visible and recent_only and fp not in self._recents:
                visible = False
            if visible and tag_q and fp:
                stags = [t.lower() for t in self._tags.get(fp, [])]
                if not any(tag_q in t for t in stags):
                    visible = False
            item.setHidden(not visible)
            return visible

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            _filter_item(root.child(i))

    def _build_tree(self):
        from mlrvp.config import get_slices_dir
        slices_dir = str(get_slices_dir())
        self._tree.clear()
        if not os.path.isdir(slices_dir):
            return
        root_item = QTreeWidgetItem(self._tree)
        _make_folder_style(root_item)
        root_item.setText(self.COL_NAME, "slices")
        root_item.setData(0, Qt.ItemDataRole.UserRole, slices_dir)
        root_item.setToolTip(self.COL_NAME, slices_dir)
        self._build_tree_dir(root_item, slices_dir)
        root_item.setExpanded(True)
        h = self._tree.header()
        h.setStretchLastSection(True)
        for c in range(self._tree.columnCount()):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        self._tree.setColumnWidth(self.COL_PLAY, 52)
        self._tree.setColumnWidth(self.COL_NAME, 200)
        self._tree.setColumnWidth(self.COL_BPM, 55)
        self._tree.setColumnWidth(self.COL_KEY, 55)
        self._tree.setColumnWidth(self.COL_BARS, 45)
        self._tree.setColumnWidth(self.COL_DUR, 70)
        self._tree.setColumnWidth(self.COL_TAGS, 120)

    def _build_tree_dir(self, parent: QTreeWidgetItem, dir_path: str):
        try:
            entries = sorted(os.scandir(dir_path),
                             key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                dir_item = QTreeWidgetItem(parent)
                _make_folder_style(dir_item)
                dir_item.setText(self.COL_NAME, entry.name)
                dir_item.setData(0, Qt.ItemDataRole.UserRole, entry.path)
                dir_item.setToolTip(self.COL_NAME, entry.path)
                self._build_tree_dir(dir_item, entry.path)
                if dir_item.childCount() > 0:
                    dir_item.setExpanded(False)
                else:
                    parent.removeChild(dir_item)
            elif entry.is_file():
                if os.path.splitext(entry.name)[1].lower() not in AUDIO_EXTENSIONS:
                    continue
                fpath = entry.path
                if fpath in self._hidden:
                    continue
                meta = self._meta.get(fpath, {}) or {}
                self._add_tree_row(parent, fpath, entry.name, meta)

    # ── Folder tree: selection + sort ────────────────────────────────

    def _on_tree_selection_changed(self):
        n = len(self._tree.selected_filepaths())
        if n > 0:
            self._batch_del_btn.setText(f"Delete ({n})" if n > 1 else "Delete")
            self._batch_del_btn.setVisible(True)
        else:
            self._batch_del_btn.setVisible(False)

    def _on_tree_header_clicked(self, col: int):
        if col == self.COL_PLAY:
            return
        if self._tree_sort_col == col:
            self._tree_sort_asc = not self._tree_sort_asc
        else:
            self._tree_sort_col = col
            self._tree_sort_asc = True
        order = (Qt.SortOrder.AscendingOrder if self._tree_sort_asc
                 else Qt.SortOrder.DescendingOrder)
        self._tree.header().setSortIndicator(col, order)
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            self._sort_tree_item(root.child(i), col, self._tree_sort_asc)

    def _sort_tree_item(self, item: QTreeWidgetItem, col: int, ascending: bool):
        if item.childCount() == 0:
            return
        folders = [item.child(i) for i in range(item.childCount())
                   if item.child(i).childCount() > 0]
        leaves = [item.child(i) for i in range(item.childCount())
                  if item.child(i).childCount() == 0]

        def _key(child_item):
            raw = child_item.data(col, Qt.ItemDataRole.UserRole)
            if raw is not None:
                try:
                    v = float(raw)
                    if not math.isnan(v):
                        return (0, v, "")
                    return (1, 0.0, "")
                except (TypeError, ValueError):
                    pass
            return (0, 0.0, child_item.text(col).lower())

        folders.sort(key=lambda x: x.text(1).lower())
        leaves.sort(key=_key, reverse=not ascending)
        item.takeChildren()
        for f in folders:
            item.addChild(f)
        for lv in leaves:
            item.addChild(lv)
        for f in folders:
            self._sort_tree_item(f, col, ascending)

    # ── Play / preview ────────────────────────────────────────────────

    def _on_tree_play_requested(self, fp: str):
        if fp == self._previewing_fp:
            self._do_stop_preview()
        else:
            self._previewing_fp = fp
            self._stop_preview_btn.setEnabled(True)
            self._track_recent(fp)
            self.preview_requested.emit(fp, self._vol_slider.value() / 100.0)

    def _on_cell_clicked(self, row: int, col: int):
        if col == self.COL_PLAY:
            name_item = self._table.item(row, self.COL_NAME)
            if name_item:
                fp = str(name_item.data(Qt.ItemDataRole.UserRole + 1) or "")
                if fp and fp != self._previewing_fp:
                    self._track_recent(fp)
        super()._on_cell_clicked(row, col)

    # ── Selected filepaths (view-aware) ───────────────────────────────

    def _selected_filepaths(self) -> list[str]:
        if self._view_mode == "folder":
            return self._tree.selected_filepaths()
        return super()._selected_filepaths()

    # ── Scan persist ──────────────────────────────────────────────────

    def _on_scan_result_persist(self, filepath: str, bpm: float, key: str):
        import json as _json
        import math as _m
        sidecar = os.path.splitext(filepath)[0] + ".json"
        existing = {}
        if os.path.isfile(sidecar):
            try:
                with open(sidecar, "r", encoding="utf-8") as f:
                    existing = _json.load(f) or {}
            except (ValueError, OSError):
                existing = {}
        if not _m.isnan(bpm) and bpm > 0:
            existing["bpm"] = float(bpm)
            duration = self._meta.get(filepath, {}).get("_duration", 0.0)
            bars = _snap_to_bars(duration, bpm)
            if bars:
                existing["bars"] = bars
        if key:
            existing["key"] = key
        try:
            with open(sidecar, "w", encoding="utf-8") as f:
                _json.dump(existing, f, indent=2)
        except OSError:
            pass

    # ── Files move (drag-drop) ────────────────────────────────────────

    def _on_files_move_requested(self, paths: list, dest_dir: str):
        """Move slice files (+ sidecars) to dest_dir via drag-drop in folder tree."""
        import shutil
        moved_any = False
        for src in paths:
            if not os.path.isfile(src):
                continue
            fname = os.path.basename(src)
            dest = os.path.join(dest_dir, fname)
            if os.path.abspath(src) == os.path.abspath(dest):
                continue
            try:
                shutil.move(src, dest)
                sidecar_src = os.path.splitext(src)[0] + ".json"
                sidecar_dst = os.path.splitext(dest)[0] + ".json"
                if os.path.isfile(sidecar_src):
                    shutil.move(sidecar_src, sidecar_dst)
                moved_any = True
            except OSError:
                pass
        if moved_any:
            self.refresh()

    # ── Update tree item meta after scan ─────────────────────────────

    def _update_tree_item_meta(self, filepath: str, meta: dict):
        def _walk(item: QTreeWidgetItem):
            if item.childCount() > 0:
                for i in range(item.childCount()):
                    _walk(item.child(i))
            else:
                fp = item.data(self.COL_NAME, Qt.ItemDataRole.UserRole + 1)
                if fp == filepath:
                    bpm_val = meta.get("bpm")
                    item.setText(self.COL_BPM, f"{bpm_val:.0f}" if bpm_val else "")
                    item.setData(self.COL_BPM, Qt.ItemDataRole.UserRole,
                                 float(bpm_val) if bpm_val else float("nan"))
                    item.setText(self.COL_KEY, meta.get("key") or "")

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            _walk(root.child(i))

    def _on_scan_result(self, filepath: str, bpm: float, key: str):
        super()._on_scan_result(filepath, bpm, key)
        self._update_tree_item_meta(filepath, self._meta.get(filepath, {}) or {})


def _snap_to_bars(duration_s: float, bpm) -> int | None:
    """Return bars count (nearest power-of-2: 1/2/4/8/16/32) from duration + BPM."""
    import math as _m
    try:
        bpm = float(bpm)
        if not duration_s or bpm <= 0:
            return None
        bars_f = duration_s * bpm / (4.0 * 60.0)
        if bars_f <= 0:
            return None
        exp = round(_m.log2(bars_f))
        exp = max(0, min(exp, 5))  # clamp 1..32
        return 2 ** exp
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _reveal_in_explorer(filepath: str):
    """Open the OS file explorer with the given file selected."""
    import sys
    import subprocess
    if not os.path.exists(filepath):
        return
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(filepath)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", filepath])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(filepath)])
    except OSError:
        pass


class _LibrarySamplesTab(_LibraryTableBase):
    """SAMPLES sub-tab — scans user-defined watched directories."""

    # Column indices: ▶ | Name | BPM | Key | Duration | Channels | SR | Size | Path | Tags
    COL_PLAY = 0
    COL_DUR = 4
    COL_CH = 5
    COL_SR = 6
    COL_SIZE = 7
    COL_PATH = 8
    COL_TAGS = 9

    def _columns(self) -> list[str]:
        return ["▶", "Name", "BPM", "Key", "Duration", "Ch", "SR", "Size", "Path", "Tags"]

    def __init__(self, parent=None):
        from mlrvp.config import get_sample_paths, get_sample_metadata_cache
        super().__init__(parent)
        self._watched_dirs: list[str] = list(get_sample_paths())
        self._cache: dict = dict(get_sample_metadata_cache())

        # Watched dirs section (collapsible)
        self._dirs_toggle = QPushButton("WATCHED DIRS \u25BE")
        self._dirs_toggle.setCheckable(True)
        self._dirs_toggle.setChecked(False)
        bind_fixed_height(self._dirs_toggle, 20)
        self._dirs_toggle.setStyleSheet(
            f"QPushButton {{ background: {BG_PANEL}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; font-weight: bold; border: 1px solid {SEPARATOR}; "
            f"padding: 1px 6px; text-align: left; }}"
            f"QPushButton:checked {{ background: {SEPARATOR}; }}"
        )
        self._dirs_toggle.clicked.connect(self._on_toggle_dirs)
        self.layout().addWidget(self._dirs_toggle)

        self._dirs_panel = QWidget()
        dvl = QVBoxLayout(self._dirs_panel)
        dvl.setContentsMargins(2, 2, 2, 2)
        dvl.setSpacing(2)
        self._dirs_list = QListWidget()
        bind_fixed_height(self._dirs_list, 80)
        self._dirs_list.setStyleSheet(
            f"QListWidget {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; }}"
        )
        for d in self._watched_dirs:
            self._dirs_list.addItem(d)
        dvl.addWidget(self._dirs_list)
        dirs_btn_row = QHBoxLayout()
        dirs_btn_row.setSpacing(4)
        add_btn = QPushButton("+ Add Dir")
        bind_fixed_height(add_btn, 20)
        add_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {ACCENT_GOLD}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ background: {SEPARATOR}; }}"
        )
        add_btn.clicked.connect(self._on_add_dir)
        dirs_btn_row.addWidget(add_btn)
        rem_btn = QPushButton("Remove")
        bind_fixed_height(rem_btn, 20)
        rem_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ color: {STATUS_ERROR_HOVER}; }}"
        )
        rem_btn.clicked.connect(self._on_remove_dir)
        dirs_btn_row.addWidget(rem_btn)
        dirs_btn_row.addStretch()
        dvl.addLayout(dirs_btn_row)
        self._dirs_panel.setVisible(False)
        self.layout().addWidget(self._dirs_panel)

        # ── Folder view setup ────────────────────────────────────────
        # Replace self._table in the base layout with a QStackedWidget so we
        # can swap between the flat table (All) and the tree (Folder) views.
        vb = self.layout()
        table_idx: int | None = None
        for _i in range(vb.count()):
            _item = vb.itemAt(_i)
            if _item and _item.widget() is self._table:
                table_idx = _i
                vb.takeAt(_i)
                break

        # Tree widget
        self._tree = _LibraryFolderTree(self._columns(), drop_enabled=True)
        self._tree.delete_pressed.connect(self._on_delete_selected)
        self._tree.play_requested.connect(self._on_tree_play_requested)
        self._tree.load_requested.connect(self._handle_double_click)
        self._tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.header().sectionClicked.connect(self._on_tree_header_clicked)
        self._tree.files_move_requested.connect(self._on_files_move_requested)

        # Stack: index 0 = flat table (All view), index 1 = tree (Folder view)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._table)
        self._stack.addWidget(self._tree)

        _insert_at = table_idx if table_idx is not None else max(0, vb.count() - 1)
        vb.insertWidget(_insert_at, self._stack, 1)

        # Toggle button: inserted into toolbar row 1 between ↻ and Scan
        self._folder_view_btn = QPushButton("All")
        bind_fixed_size(self._folder_view_btn, 36, 20)
        self._folder_view_btn.setCheckable(True)
        self._folder_view_btn.setToolTip("Switch between flat list (All) and folder tree view (Dir)")
        self._folder_view_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; font-weight: bold; border: 1px solid {SEPARATOR}; padding: 0 4px; }}"
            f"QPushButton:hover {{ color: {ACCENT_GOLD}; }}"
            f"QPushButton:checked {{ color: {ACCENT_GOLD}; background: {SEPARATOR}; border-color: {ACCENT_GOLD}; }}"
        )
        self._folder_view_btn.clicked.connect(self._on_toggle_view)
        # row1 layout order: search(0), refresh(1), [insert here at 2], scan(2→3), del(3→4)
        self._toolbar_row1.insertWidget(2, self._folder_view_btn)

        # ── Favorites + Tags ──────────────────────────────────────────
        from mlrvp.config import get_sample_favorites, get_sample_tags, get_sample_recents
        self._favorites: set = set(get_sample_favorites())
        self._tags: dict = dict(get_sample_tags())
        self._recents: list = list(get_sample_recents())

        # ★ Favs filter toggle (appended to toolbar row 1)
        self._fav_filter_btn = QPushButton("★ Favs")
        self._fav_filter_btn.setCheckable(True)
        bind_fixed_size(self._fav_filter_btn, 52, 20)
        self._fav_filter_btn.setToolTip("Show only favorited samples")
        self._fav_filter_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ color: {ACCENT_GOLD}; }}"
            f"QPushButton:checked {{ color: {BG_BLACK}; background: {ACCENT_GOLD}; "
            f"border: 1px solid {ACCENT_GOLD}; }}"
        )
        self._fav_filter_btn.clicked.connect(self._apply_filter)
        self._toolbar_row1.addWidget(self._fav_filter_btn)

        # Recent filter toggle
        self._recent_filter_btn = QPushButton("Recent")
        self._recent_filter_btn.setCheckable(True)
        bind_fixed_size(self._recent_filter_btn, 52, 20)
        self._recent_filter_btn.setToolTip("Show only recently used samples")
        self._recent_filter_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ color: {ACCENT_GOLD}; }}"
            f"QPushButton:checked {{ color: {BG_BLACK}; background: {ACCENT_GOLD}; "
            f"border: 1px solid {ACCENT_GOLD}; }}"
        )
        self._recent_filter_btn.clicked.connect(self._apply_filter)
        self._toolbar_row1.addWidget(self._recent_filter_btn)

        # Tag search filter (inserted before the stretch in toolbar row 2)
        _ins = self._toolbar_row2.count() - 1  # index of the trailing stretch
        _tl = QLabel("Tag:")
        _tl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 8px;")
        self._toolbar_row2.insertWidget(_ins, _tl)
        self._tag_filter_edit = QLineEdit()
        self._tag_filter_edit.setPlaceholderText("tag…")
        self._tag_filter_edit.setClearButtonEnabled(True)
        bind_fixed_height(self._tag_filter_edit, 20)
        bind_max_width(self._tag_filter_edit, 110)
        self._tag_filter_edit.setStyleSheet(
            f"QLineEdit {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 1px 4px; }}"
        )
        self._tag_filter_edit.textChanged.connect(self._on_search_changed)
        self._toolbar_row2.insertWidget(_ins + 1, self._tag_filter_edit)
        # ─────────────────────────────────────────────────────────────

        # Tree sort state
        self._tree_sort_col: int = self.COL_NAME
        self._tree_sort_asc: bool = True
        # ─────────────────────────────────────────────────────────────

        # Default to folder/Dir view with directories collapsed
        self._view_mode = "folder"
        self._folder_view_btn.setChecked(True)
        self._folder_view_btn.setText("Dir")
        self._stack.setCurrentIndex(1)

        self.refresh()

    def _on_tree_play_requested(self, fp: str):
        """Toggle play/stop when a tree row's play button is clicked."""
        if fp == self._previewing_fp:
            self._do_stop_preview()
        else:
            self._previewing_fp = fp
            self._stop_preview_btn.setEnabled(True)
            self._track_recent(fp)
            self.preview_requested.emit(fp, self._vol_slider.value() / 100.0)

    def _on_cell_clicked(self, row: int, col: int):
        """Override: track recent when the flat-table play button is clicked."""
        if col == self.COL_PLAY:
            name_item = self._table.item(row, self.COL_NAME)
            if name_item:
                fp = str(name_item.data(Qt.ItemDataRole.UserRole + 1) or "")
                if fp and fp != self._previewing_fp:
                    self._track_recent(fp)
        super()._on_cell_clicked(row, col)

    # ── Favorites + Tags: filter hooks ───────────────────────────────

    def _extra_row_visible(self, r: int) -> bool:
        """Show/hide row based on active favorites filter and tag filter."""
        name_item = self._table.item(r, self.COL_NAME)
        if name_item is None:
            return True
        fp = name_item.data(Qt.ItemDataRole.UserRole + 1)
        if not fp:
            return True
        fp = str(fp)
        if self._fav_filter_btn.isChecked() and fp not in self._favorites:
            return False
        if self._recent_filter_btn.isChecked() and fp not in self._recents:
            return False
        tag_q = self._tag_filter_edit.text().strip().lower()
        if tag_q:
            sample_tags = [t.lower() for t in self._tags.get(fp, [])]
            if not any(tag_q in t for t in sample_tags):
                return False
        return True

    def _clear_filters(self):
        """Override: also clear favorites filter and tag filter."""
        self._search_edit.clear()
        self._key_filter.setCurrentIndex(0)
        self._bpm_min.setValue(0)
        self._bpm_max.setValue(0)
        self._fav_filter_btn.setChecked(False)
        self._recent_filter_btn.setChecked(False)
        self._tag_filter_edit.clear()
        self._apply_filter()

    def _on_toggle_dirs(self):
        self._dirs_panel.setVisible(self._dirs_toggle.isChecked())
        self._dirs_toggle.setText(
            "WATCHED DIRS \u25B4" if self._dirs_toggle.isChecked() else "WATCHED DIRS \u25BE"
        )

    def _on_add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Add Sample Directory")
        if not d:
            return
        if d in self._watched_dirs:
            return
        self._watched_dirs.append(d)
        self._dirs_list.addItem(d)
        self._save_dirs()
        self.refresh()

    def _on_remove_dir(self):
        row = self._dirs_list.currentRow()
        if row < 0 or row >= len(self._watched_dirs):
            return
        del self._watched_dirs[row]
        self._dirs_list.takeItem(row)
        self._save_dirs()
        self.refresh()

    def _save_dirs(self):
        from mlrvp.config import save_sample_paths
        save_sample_paths(self._watched_dirs)

    def _save_cache(self):
        from mlrvp.config import save_sample_metadata_cache
        save_sample_metadata_cache(self._cache)

    def refresh(self):
        self._row_filepaths = []
        self._meta = {}
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        seen: set[str] = set()
        for base in self._watched_dirs:
            if not os.path.isdir(base):
                continue
            for root, _dirs, files in os.walk(base):
                for fn in files:
                    if os.path.splitext(fn)[1].lower() not in AUDIO_EXTENSIONS:
                        continue
                    fpath = os.path.join(root, fn)
                    if fpath in seen:
                        continue
                    seen.add(fpath)
                    cached = self._cache.get(fpath, {}) or {}
                    duration = cached.get("duration_sec") or 0.0
                    channels = cached.get("channels") or 0
                    samplerate = cached.get("samplerate") or 0
                    filesize = cached.get("filesize") or 0
                    if not duration or not channels or not samplerate:
                        try:
                            info = sf.info(fpath)
                            duration = info.frames / float(info.samplerate or 1)
                            channels = info.channels
                            samplerate = info.samplerate
                        except Exception:
                            duration, channels, samplerate = 0.0, 0, 0
                    if not filesize:
                        try:
                            filesize = os.path.getsize(fpath)
                        except OSError:
                            filesize = 0
                    cached.update({
                        "duration_sec": duration,
                        "channels": channels,
                        "samplerate": samplerate,
                        "filesize": filesize,
                    })
                    self._cache[fpath] = cached
                    self._meta[fpath] = cached
                    self._row_filepaths.append(fpath)
                    self._add_row(fpath, fn, cached, duration, channels, samplerate, filesize)
        self._save_cache()
        self._table.setSortingEnabled(True)
        self._refresh_key_filter_options()
        self._apply_filter()
        # All columns user-resizable; last column stretches to fill space
        h = self._table.horizontalHeader()
        h.setStretchLastSection(True)
        for c in range(self._table.columnCount()):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(self.COL_PLAY, 24)
        self._table.setColumnWidth(self.COL_NAME, 200)
        self._table.setColumnWidth(self.COL_BPM, 55)
        self._table.setColumnWidth(self.COL_KEY, 55)
        self._table.setColumnWidth(self.COL_DUR, 70)
        self._table.setColumnWidth(self.COL_CH, 35)
        self._table.setColumnWidth(self.COL_SR, 65)
        self._table.setColumnWidth(self.COL_SIZE, 65)
        # Build folder tree (reuses already-warmed self._meta — no extra I/O)
        if hasattr(self, "_tree"):
            self._build_tree()

    def _add_row(self, fpath: str, fname: str, meta: dict, duration: float,
                 channels: int, samplerate: int, filesize: int):
        r = self._table.rowCount()
        self._table.insertRow(r)
        # Play item (▶ text, click handled via cellClicked in base class)
        play_item = QTableWidgetItem("▶")
        play_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        play_item.setForeground(QColor(ACCENT_GOLD))
        play_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self._table.setItem(r, self.COL_PLAY, play_item)
        # Name
        name_item = QTableWidgetItem(fname)
        name_item.setData(Qt.ItemDataRole.UserRole + 1, fpath)
        name_item.setToolTip(fpath)
        self._table.setItem(r, self.COL_NAME, name_item)
        # BPM
        bpm_val = meta.get("bpm")
        bpm_item = _NumericTableItem(f"{bpm_val:.0f}" if bpm_val else "")
        bpm_item.setData(Qt.ItemDataRole.UserRole, float(bpm_val) if bpm_val else float("nan"))
        bpm_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, self.COL_BPM, bpm_item)
        # Key
        key_item = QTableWidgetItem(meta.get("key") or "")
        key_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, self.COL_KEY, key_item)
        # Duration
        dur_text = f"{int(duration // 60)}:{duration % 60:04.1f}" if duration > 0 else ""
        dur_item = _NumericTableItem(dur_text)
        dur_item.setData(Qt.ItemDataRole.UserRole, float(duration) if duration > 0 else float("nan"))
        dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, self.COL_DUR, dur_item)
        # Channels
        ch_item = _NumericTableItem(str(channels) if channels else "")
        ch_item.setData(Qt.ItemDataRole.UserRole, float(channels) if channels else float("nan"))
        ch_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, self.COL_CH, ch_item)
        # Sample rate
        sr_item = _NumericTableItem(f"{samplerate // 1000}k" if samplerate else "")
        sr_item.setData(Qt.ItemDataRole.UserRole, float(samplerate) if samplerate else float("nan"))
        sr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, self.COL_SR, sr_item)
        # Size
        size_item = _NumericTableItem(_human_size(filesize) if filesize else "")
        size_item.setData(Qt.ItemDataRole.UserRole, float(filesize) if filesize else 0.0)
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._table.setItem(r, self.COL_SIZE, size_item)
        # Path (folder only)
        folder = os.path.dirname(fpath)
        path_item = QTableWidgetItem(folder)
        path_item.setToolTip(fpath)
        self._table.setItem(r, self.COL_PATH, path_item)
        # Tags
        tags_list = self._tags.get(fpath, [])
        tags_item = QTableWidgetItem(", ".join(tags_list))
        tags_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        tags_item.setForeground(QColor(TEXT_SECONDARY))
        self._table.setItem(r, self.COL_TAGS, tags_item)
        # Favorite visual: gold name
        if fpath in self._favorites:
            name_item.setForeground(QColor(ACCENT_GOLD))

    def _on_scan_result_persist(self, filepath: str, bpm: float, key: str):
        import math as _m
        cached = dict(self._cache.get(filepath, {}) or {})
        if not _m.isnan(bpm) and bpm > 0:
            cached["bpm"] = float(bpm)
        if key:
            cached["key"] = key
        self._cache[filepath] = cached
        self._save_cache()

    def _on_scan_result(self, filepath: str, bpm: float, key: str):
        """Override: update table (via super) then also live-update tree item."""
        super()._on_scan_result(filepath, bpm, key)
        self._update_tree_item_meta(filepath, self._meta.get(filepath, {}) or {})

    # ── Folder view: toggle ───────────────────────────────────────────

    def _on_toggle_view(self, checked: bool):
        self._view_mode = "folder" if checked else "all"
        self._stack.setCurrentIndex(1 if checked else 0)
        self._folder_view_btn.setText("Dir" if checked else "All")
        self._apply_filter()

    # ── Folder view: tree selection / context menu ────────────────────

    def _on_tree_selection_changed(self):
        n = len(self._tree.selected_filepaths())
        if n > 1:
            self._batch_del_btn.setText(f"Delete ({n})")
            self._batch_del_btn.setVisible(True)
        elif n == 1:
            self._batch_del_btn.setText("Delete")
            self._batch_del_btn.setVisible(True)
        else:
            self._batch_del_btn.setVisible(False)

    def _on_tree_context_menu(self, pos):
        paths = self._tree.selected_filepaths()
        item = self._tree.itemAt(pos)
        if item and item.childCount() == 0:
            fp = item.data(self.COL_NAME, Qt.ItemDataRole.UserRole + 1)
            if fp and fp not in paths:
                self._tree.clearSelection()
                item.setSelected(True)
                paths = [str(fp)]
        if not paths:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: rgba(30, 30, 30, 230); color: {TEXT_PRIMARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 9px; }}"
            f"QMenu::item {{ padding: 4px 16px; }}"
            f"QMenu::item:selected {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        self._build_context_menu(menu, paths)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # ── Folder view: column sort ──────────────────────────────────────

    def _on_tree_header_clicked(self, col: int):
        if col == self.COL_PLAY:
            return
        if self._tree_sort_col == col:
            self._tree_sort_asc = not self._tree_sort_asc
        else:
            self._tree_sort_col = col
            self._tree_sort_asc = True
        order = (Qt.SortOrder.AscendingOrder if self._tree_sort_asc
                 else Qt.SortOrder.DescendingOrder)
        self._tree.header().setSortIndicator(col, order)
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            self._sort_tree_item(root.child(i), col, self._tree_sort_asc)

    def _sort_tree_item(self, item: QTreeWidgetItem, col: int, ascending: bool):
        """Sort immediate sample children of item by col; recurse into subfolders."""
        if item.childCount() == 0:
            return
        folders: list[QTreeWidgetItem] = []
        leaves: list[QTreeWidgetItem] = []
        for i in range(item.childCount()):
            child = item.child(i)
            if child.childCount() > 0:
                folders.append(child)
            else:
                leaves.append(child)

        def _leaf_key(child_item: QTreeWidgetItem):
            raw = child_item.data(col, Qt.ItemDataRole.UserRole)
            if raw is not None:
                try:
                    v = float(raw)
                    if not math.isnan(v):
                        return (0, v, "")
                    return (1, 0.0, "")   # NaN sorts last
                except (TypeError, ValueError):
                    pass
            return (0, 0.0, child_item.text(col).lower())

        folders.sort(key=lambda x: x.text(1).lower())  # sort by name (col 1 = COL_NAME)
        leaves.sort(key=_leaf_key, reverse=not ascending)

        # Re-insert in sorted order (folders first, then leaves)
        item.takeChildren()
        for f in folders:
            item.addChild(f)
        for lv in leaves:
            item.addChild(lv)

        for f in folders:
            self._sort_tree_item(f, col, ascending)

    # ── Folder view: filter ───────────────────────────────────────────

    def _apply_filter_tree(self):
        """Hide/show tree items to match active search, key, BPM, favorites, and tag filters."""
        query = self._search_edit.text().strip().lower()
        key_filter = self._key_filter.currentData() or ""
        bpm_min = self._bpm_min.value()
        bpm_max = self._bpm_max.value()
        fav_only = self._fav_filter_btn.isChecked()
        recent_only = self._recent_filter_btn.isChecked()
        tag_q = self._tag_filter_edit.text().strip().lower()
        filter_active = bool(query or key_filter or bpm_min > 0 or bpm_max > 0
                             or fav_only or recent_only or tag_q)

        def _filter_item(item: QTreeWidgetItem) -> bool:
            if item.childCount() > 0:
                # Folder node: visible if any child is visible
                any_visible = False
                for i in range(item.childCount()):
                    if _filter_item(item.child(i)):
                        any_visible = True
                item.setHidden(not any_visible)
                if filter_active and any_visible:
                    item.setExpanded(True)
                return any_visible
            # Leaf (sample) node
            fp = item.data(self.COL_NAME, Qt.ItemDataRole.UserRole + 1)
            fp = str(fp) if fp else ""
            visible = True
            name = item.text(self.COL_NAME).lower()
            if query and query not in name:
                visible = False
            if visible and key_filter:
                if item.text(self.COL_KEY) != key_filter:
                    visible = False
            if visible and (bpm_min > 0 or bpm_max > 0):
                try:
                    bpm_val = float(item.data(self.COL_BPM, Qt.ItemDataRole.UserRole))
                    if not math.isnan(bpm_val):
                        if bpm_min > 0 and bpm_val < bpm_min:
                            visible = False
                        if bpm_max > 0 and bpm_val > bpm_max:
                            visible = False
                except (TypeError, ValueError):
                    pass
            if visible and fav_only and fp not in self._favorites:
                visible = False
            if visible and recent_only and fp not in self._recents:
                visible = False
            if visible and tag_q and fp:
                sample_tags = [t.lower() for t in self._tags.get(fp, [])]
                if not any(tag_q in t for t in sample_tags):
                    visible = False
            item.setHidden(not visible)
            return visible

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            _filter_item(root.child(i))

    # ── Folder view: build tree ───────────────────────────────────────

    def _build_tree(self):
        """Populate _tree to mirror the watched-dir hierarchy on disk."""
        self._tree.clear()
        seen: set[str] = set()
        for base_dir in self._watched_dirs:
            if not os.path.isdir(base_dir):
                continue
            root_item = QTreeWidgetItem(self._tree)
            label = os.path.basename(base_dir) or base_dir
            _make_folder_style(root_item)          # sets col 0 indicator + col 1 styling
            root_item.setText(self.COL_NAME, label)  # folder name in Name column
            root_item.setData(0, Qt.ItemDataRole.UserRole, base_dir)
            root_item.setToolTip(self.COL_NAME, base_dir)
            self._build_tree_dir(root_item, base_dir, seen)
            root_item.setExpanded(False)
        # Match column widths to the flat table
        h = self._tree.header()
        h.setStretchLastSection(True)
        for c in range(self._tree.columnCount()):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        self._tree.setColumnWidth(self.COL_PLAY, 52)
        self._tree.setColumnWidth(self.COL_NAME, 200)
        self._tree.setColumnWidth(self.COL_BPM, 55)
        self._tree.setColumnWidth(self.COL_KEY, 55)
        self._tree.setColumnWidth(self.COL_DUR, 70)
        self._tree.setColumnWidth(self.COL_CH, 35)
        self._tree.setColumnWidth(self.COL_SR, 65)
        self._tree.setColumnWidth(self.COL_SIZE, 65)
        self._tree.setColumnWidth(self.COL_TAGS, 120)

    def _build_tree_dir(self, parent: QTreeWidgetItem, dir_path: str, seen: set):
        """Recursively add subdirectory and sample leaf nodes under parent."""
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                dir_item = QTreeWidgetItem(parent)
                _make_folder_style(dir_item)               # col 0 indicator + col 1 style
                dir_item.setText(self.COL_NAME, entry.name)  # name in Name column
                dir_item.setData(0, Qt.ItemDataRole.UserRole, entry.path)
                dir_item.setToolTip(self.COL_NAME, entry.path)
                self._build_tree_dir(dir_item, entry.path, seen)
                # Prune empty dirs; leave all others collapsed by default
                if dir_item.childCount() > 0:
                    dir_item.setExpanded(False)
                else:
                    parent.removeChild(dir_item)  # prune empty dirs
            elif entry.is_file():
                if os.path.splitext(entry.name)[1].lower() not in AUDIO_EXTENSIONS:
                    continue
                fpath = entry.path
                if fpath in seen:
                    continue
                seen.add(fpath)
                meta = self._meta.get(fpath, {}) or {}
                self._add_tree_row(parent, fpath, entry.name, meta)

    def _add_tree_row(self, parent: QTreeWidgetItem, fpath: str, fname: str, meta: dict):
        """Add a leaf sample item to a tree node."""
        item = QTreeWidgetItem(parent)
        # COL_PLAY — larger play button so it's easy to click
        from PySide6.QtGui import QFont as _QFont
        item.setText(self.COL_PLAY, "\u25B6")
        item.setTextAlignment(self.COL_PLAY, Qt.AlignmentFlag.AlignCenter)
        item.setForeground(self.COL_PLAY, QColor(ACCENT_GOLD))
        _play_font = _QFont()
        _play_font.setPointSize(10)
        item.setFont(self.COL_PLAY, _play_font)
        # COL_NAME
        item.setText(self.COL_NAME, fname)
        item.setData(self.COL_NAME, Qt.ItemDataRole.UserRole + 1, fpath)
        item.setToolTip(self.COL_NAME, fpath)
        # COL_BPM
        bpm_val = meta.get("bpm")
        item.setText(self.COL_BPM, f"{bpm_val:.0f}" if bpm_val else "")
        item.setData(self.COL_BPM, Qt.ItemDataRole.UserRole,
                     float(bpm_val) if bpm_val else float("nan"))
        item.setTextAlignment(self.COL_BPM, Qt.AlignmentFlag.AlignCenter)
        # COL_KEY
        item.setText(self.COL_KEY, meta.get("key") or "")
        item.setTextAlignment(self.COL_KEY, Qt.AlignmentFlag.AlignCenter)
        # COL_DUR
        duration = meta.get("duration_sec") or 0.0
        dur_text = f"{int(duration // 60)}:{duration % 60:04.1f}" if duration > 0 else ""
        item.setText(self.COL_DUR, dur_text)
        item.setData(self.COL_DUR, Qt.ItemDataRole.UserRole,
                     float(duration) if duration > 0 else float("nan"))
        item.setTextAlignment(self.COL_DUR, Qt.AlignmentFlag.AlignCenter)
        # COL_CH
        channels = meta.get("channels") or 0
        item.setText(self.COL_CH, str(channels) if channels else "")
        item.setData(self.COL_CH, Qt.ItemDataRole.UserRole,
                     float(channels) if channels else float("nan"))
        item.setTextAlignment(self.COL_CH, Qt.AlignmentFlag.AlignCenter)
        # COL_SR
        samplerate = meta.get("samplerate") or 0
        item.setText(self.COL_SR, f"{samplerate // 1000}k" if samplerate else "")
        item.setData(self.COL_SR, Qt.ItemDataRole.UserRole,
                     float(samplerate) if samplerate else float("nan"))
        item.setTextAlignment(self.COL_SR, Qt.AlignmentFlag.AlignCenter)
        # COL_SIZE
        filesize = meta.get("filesize") or 0
        item.setText(self.COL_SIZE, _human_size(filesize) if filesize else "")
        item.setData(self.COL_SIZE, Qt.ItemDataRole.UserRole,
                     float(filesize) if filesize else 0.0)
        item.setTextAlignment(self.COL_SIZE,
                              Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # COL_PATH — folder name only; full path in tooltip
        item.setText(self.COL_PATH, os.path.dirname(fpath))
        item.setToolTip(self.COL_PATH, fpath)
        # COL_TAGS
        tags_list = self._tags.get(fpath, [])
        item.setText(self.COL_TAGS, ", ".join(tags_list))
        item.setForeground(self.COL_TAGS, QColor(TEXT_SECONDARY))
        # Favorite visual: gold name
        if fpath in self._favorites:
            item.setForeground(self.COL_NAME, QColor(ACCENT_GOLD))

    def _update_tree_item_meta(self, filepath: str, meta: dict):
        """Find the leaf tree item for filepath and refresh its BPM/Key columns."""
        def _walk(item: QTreeWidgetItem):
            if item.childCount() > 0:
                for i in range(item.childCount()):
                    _walk(item.child(i))
            else:
                fp = item.data(self.COL_NAME, Qt.ItemDataRole.UserRole + 1)
                if fp == filepath:
                    bpm_val = meta.get("bpm")
                    item.setText(self.COL_BPM, f"{bpm_val:.0f}" if bpm_val else "")
                    item.setData(self.COL_BPM, Qt.ItemDataRole.UserRole,
                                 float(bpm_val) if bpm_val else float("nan"))
                    item.setText(self.COL_KEY, meta.get("key") or "")

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            _walk(root.child(i))

    # ── Override: selection aware of active view ──────────────────────

    def _selected_filepaths(self) -> list[str]:
        if self._view_mode == "folder":
            return self._tree.selected_filepaths()
        return super()._selected_filepaths()

    def _handle_double_click(self, filepath: str):
        # Default: load to slicer (use right-click "Load to Channel" for direct channel load)
        self.load_to_slicer.emit(filepath)

    def _build_context_menu(self, menu: QMenu, filepaths: list[str]) -> None:
        n = len(filepaths)
        # ── Favorites ────────────────────────────────────────────────
        if n == 1:
            fp = filepaths[0]
            is_fav = fp in self._favorites
            fav_label = "★ Remove from Favorites" if is_fav else "☆ Add to Favorites"
            act_fav = menu.addAction(fav_label)
            act_fav.triggered.connect(lambda _c=False, p=fp: self._toggle_favorite([p]))
        else:
            act_fav = menu.addAction(f"☆ Add to Favorites ({n})")
            act_fav.triggered.connect(lambda _c=False: self._toggle_favorite(filepaths))
        # ── Tags ─────────────────────────────────────────────────────
        if n == 1:
            fp = filepaths[0]
            cur_tags = self._tags.get(fp, [])
            tag_label = f"Tags: {', '.join(cur_tags)}" if cur_tags else "Edit Tags\u2026"
            act_tags = menu.addAction(tag_label)
            act_tags.triggered.connect(lambda _c=False, p=fp: self._edit_tags_dialog(p))
            if cur_tags:
                act_clear_tags = menu.addAction("Clear Tags")
                act_clear_tags.triggered.connect(lambda _c=False, p=fp: self._set_tags(p, []))
        menu.addSeparator()
        # ── Load / Reveal ─────────────────────────────────────────────
        if n == 1:
            act_load_slice = menu.addAction("Load to Slicer")
            act_load_slice.triggered.connect(lambda: self.load_to_slicer.emit(filepaths[0]))
            ch_menu = menu.addMenu("Load to Channel")
            for ch in range(8):
                a = ch_menu.addAction(f"Channel {ch + 1}")
                a.triggered.connect(lambda _c=False, c=ch, p=filepaths[0]: self.load_to_channel.emit(p, c))
            act_reveal = menu.addAction("Reveal in Explorer")
            act_reveal.triggered.connect(lambda: _reveal_in_explorer(filepaths[0]))
            menu.addSeparator()
        act_remove = menu.addAction(f"Remove from Library ({n})" if n > 1 else "Remove from Library")
        act_remove.triggered.connect(self._on_delete_selected)
        act_disk_del = menu.addAction(f"Delete File\u2026 ({n})" if n > 1 else "Delete File\u2026")
        act_disk_del.triggered.connect(lambda: self._on_disk_delete(filepaths))

    # ── Favorites + Tags: helpers ─────────────────────────────────────

    def _toggle_favorite(self, filepaths: list[str]):
        """Add to favorites if any are not favorited, else remove all from favorites."""
        any_not_fav = any(fp not in self._favorites for fp in filepaths)
        for fp in filepaths:
            if any_not_fav:
                self._favorites.add(fp)
            else:
                self._favorites.discard(fp)
        self._save_favorites()
        for fp in filepaths:
            self._refresh_fav_visual(fp, fp in self._favorites)
        self._apply_filter()

    def _edit_tags_dialog(self, filepath: str):
        """Open a dialog to edit comma-separated tags for a sample."""
        from PySide6.QtWidgets import QInputDialog
        cur = ", ".join(self._tags.get(filepath, []))
        fname = os.path.basename(filepath)
        text, ok = QInputDialog.getText(
            self, "Edit Tags", f"Tags for {fname}\n(comma-separated):", text=cur
        )
        if not ok:
            return
        raw = [t.strip() for t in text.split(",") if t.strip()]
        self._set_tags(filepath, raw)

    def _set_tags(self, filepath: str, tags: list):
        """Set tags for a filepath and persist."""
        if tags:
            self._tags[filepath] = tags
        else:
            self._tags.pop(filepath, None)
        self._save_tags()
        # Refresh the table row and tree item
        tags_text = ", ".join(tags)
        for r in range(self._table.rowCount()):
            name_item = self._table.item(r, self.COL_NAME)
            if name_item and str(name_item.data(Qt.ItemDataRole.UserRole + 1)) == filepath:
                tags_item = self._table.item(r, self.COL_TAGS)
                if tags_item:
                    tags_item.setText(tags_text)
                break
        self._update_tree_item_tags(filepath, tags_text)
        self._apply_filter()

    def _refresh_fav_visual(self, filepath: str, is_fav: bool):
        """Update the name cell foreground in the flat table and tree for the given filepath."""
        gold = QColor(ACCENT_GOLD)
        default = QColor(TEXT_PRIMARY)
        color = gold if is_fav else default
        for r in range(self._table.rowCount()):
            name_item = self._table.item(r, self.COL_NAME)
            if name_item and str(name_item.data(Qt.ItemDataRole.UserRole + 1)) == filepath:
                name_item.setForeground(color)
                break

        def _walk(item: QTreeWidgetItem):
            if item.childCount() > 0:
                for i in range(item.childCount()):
                    _walk(item.child(i))
            else:
                fp = item.data(self.COL_NAME, Qt.ItemDataRole.UserRole + 1)
                if str(fp) == filepath:
                    item.setForeground(self.COL_NAME, color)

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            _walk(root.child(i))

    def _update_tree_item_tags(self, filepath: str, tags_text: str):
        """Update the Tags column of the matching tree leaf."""
        def _walk(item: QTreeWidgetItem):
            if item.childCount() > 0:
                for i in range(item.childCount()):
                    _walk(item.child(i))
            else:
                fp = item.data(self.COL_NAME, Qt.ItemDataRole.UserRole + 1)
                if str(fp) == filepath:
                    item.setText(self.COL_TAGS, tags_text)

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            _walk(root.child(i))

    def _save_favorites(self):
        from mlrvp.config import save_sample_favorites
        save_sample_favorites(self._favorites)

    def _save_tags(self):
        from mlrvp.config import save_sample_tags
        save_sample_tags(self._tags)

    def _track_recent(self, filepath: str):
        """Push filepath to the front of recents and persist."""
        from mlrvp.config import SAMPLE_RECENTS_MAX
        if filepath in self._recents:
            self._recents.remove(filepath)
        self._recents.insert(0, filepath)
        if len(self._recents) > SAMPLE_RECENTS_MAX:
            self._recents = self._recents[:SAMPLE_RECENTS_MAX]
        self._save_recents()

    def _save_recents(self):
        from mlrvp.config import save_sample_recents
        save_sample_recents(self._recents)

    def _on_files_move_requested(self, paths: list, dest_dir: str):
        """Move sample files to dest_dir via drag-drop in folder tree."""
        import shutil
        moved_any = False
        for src in paths:
            if not os.path.isfile(src):
                continue
            fname = os.path.basename(src)
            dest = os.path.join(dest_dir, fname)
            if os.path.abspath(src) == os.path.abspath(dest):
                continue
            try:
                shutil.move(src, dest)
                if src in self._cache:
                    self._cache[dest] = self._cache.pop(src)
                    self._save_cache()
                moved_any = True
            except OSError:
                pass
        if moved_any:
            self.refresh()

    def _on_delete_selected(self):
        """Default Delete key action: remove from library (cache only, non-destructive)."""
        paths = self._selected_filepaths()
        if not paths:
            return
        for p in paths:
            self._cache.pop(p, None)
        self._save_cache()
        self.refresh()

    def _on_disk_delete(self, paths: list[str]):
        """Move files to the Recycle Bin / Trash (explicit confirmation)."""
        if not paths or not _confirm_trash(self, "Delete Files", paths):
            return
        failed = _trash_files(paths)
        for p in paths:
            if p not in failed:
                self._cache.pop(p, None)
        self._save_cache()
        self.refresh()
        _warn_trash_failures(self, failed)


def _human_size(n: int) -> str:
    """Format byte count as human-readable string."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


# ---------------------------------------------------------------------------
# Songs library tab
# ---------------------------------------------------------------------------

class _LibrarySongsTab(_LibraryTableBase):
    """SONGS sub-tab — scans user-defined song directories (default: the Songs folder)."""

    COL_PLAY = 0
    COL_DUR = 1
    COL_DATE = 2
    COL_SR = 3
    COL_SIZE = 4
    COL_PATH = 5

    # Override: BPM/Key columns don't exist for songs
    COL_BPM = -1
    COL_KEY = -1
    COL_NAME = 6   # will use path column's name portion internally but display Name at 0

    # Re-declare correctly for this subclass
    COL_NAME = 0

    def _columns(self) -> list[str]:
        return ["Name", "Duration", "Date", "SR", "Size", "Path"]

    # Column remapping (no BPM/Key/play-button column)
    COL_PLAY = -1
    COL_NAME = 0
    COL_DUR = 1
    COL_DATE = 2
    COL_SR = 3
    COL_SIZE = 4
    COL_PATH = 5

    def __init__(self, parent=None):
        from mlrvp.config import get_song_paths, get_song_metadata_cache
        super().__init__(parent)
        self._watched_dirs: list[str] = list(get_song_paths())
        self._cache: dict = dict(get_song_metadata_cache())

        # Watched dirs section (collapsible)
        self._dirs_toggle = QPushButton("WATCHED DIRS \u25BE")
        self._dirs_toggle.setCheckable(True)
        self._dirs_toggle.setChecked(False)
        bind_fixed_height(self._dirs_toggle, 20)
        self._dirs_toggle.setStyleSheet(
            f"QPushButton {{ background: {BG_PANEL}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; font-weight: bold; border: 1px solid {SEPARATOR}; "
            f"padding: 1px 6px; text-align: left; }}"
            f"QPushButton:checked {{ background: {SEPARATOR}; }}"
        )
        self._dirs_toggle.clicked.connect(self._on_toggle_dirs)
        self.layout().addWidget(self._dirs_toggle)

        self._dirs_panel = QWidget()
        dvl = QVBoxLayout(self._dirs_panel)
        dvl.setContentsMargins(2, 2, 2, 2)
        dvl.setSpacing(2)
        self._dirs_list = QListWidget()
        bind_fixed_height(self._dirs_list, 80)
        self._dirs_list.setStyleSheet(
            f"QListWidget {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; }}"
        )
        for d in self._watched_dirs:
            self._dirs_list.addItem(d)
        dvl.addWidget(self._dirs_list)
        dirs_btn_row = QHBoxLayout()
        dirs_btn_row.setSpacing(4)
        add_btn = QPushButton("+ Add Dir")
        bind_fixed_height(add_btn, 20)
        add_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {ACCENT_GOLD}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ background: {SEPARATOR}; }}"
        )
        add_btn.clicked.connect(self._on_add_dir)
        dirs_btn_row.addWidget(add_btn)
        rem_btn = QPushButton("Remove")
        bind_fixed_height(rem_btn, 20)
        rem_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; border: 1px solid {SEPARATOR}; padding: 1px 6px; }}"
            f"QPushButton:hover {{ color: {STATUS_ERROR_HOVER}; }}"
        )
        rem_btn.clicked.connect(self._on_remove_dir)
        dirs_btn_row.addWidget(rem_btn)
        dirs_btn_row.addStretch()
        dvl.addLayout(dirs_btn_row)
        self._dirs_panel.setVisible(False)
        self.layout().addWidget(self._dirs_panel)

        # Hide BPM/Key filter row — not relevant for songs
        self._scan_btn.setVisible(False)

        self.refresh()

    def _on_toggle_dirs(self, checked: bool):
        self._dirs_panel.setVisible(checked)
        self._dirs_toggle.setText(
            "WATCHED DIRS \u25B4" if checked else "WATCHED DIRS \u25BE"
        )

    def _on_add_dir(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Add Songs Directory")
        if d and d not in self._watched_dirs:
            self._watched_dirs.append(d)
            self._dirs_list.addItem(d)
            self._save_dirs()
            self.refresh()

    def _on_remove_dir(self):
        items = self._dirs_list.selectedItems()
        if not items:
            return
        for item in items:
            d = item.text()
            if d in self._watched_dirs:
                self._watched_dirs.remove(d)
            row = self._dirs_list.row(item)
            self._dirs_list.takeItem(row)
        self._save_dirs()
        self.refresh()

    def _save_dirs(self):
        from mlrvp.config import save_song_paths
        save_song_paths(self._watched_dirs)

    def _save_cache(self):
        from mlrvp.config import save_song_metadata_cache
        save_song_metadata_cache(self._cache)

    def refresh(self):
        self._row_filepaths = []
        self._meta = {}
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        seen: set[str] = set()
        for watched in self._watched_dirs:
            if not os.path.isdir(watched):
                continue
            for root, _dirs, files in os.walk(watched):
                for fname in sorted(files):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in {".wav", ".aiff", ".aif", ".flac"}:
                        continue
                    fpath = os.path.join(root, fname)
                    if fpath in seen:
                        continue
                    seen.add(fpath)
                    meta = self._cache.get(fpath) or {}
                    dirty = False
                    if not meta.get("duration_sec"):
                        try:
                            info = sf.info(fpath)
                            meta["duration_sec"] = info.frames / float(info.samplerate or 1)
                            meta["samplerate"] = info.samplerate
                            meta["channels"] = info.channels
                            meta["filesize"] = os.path.getsize(fpath)
                            dirty = True
                        except Exception:
                            meta.setdefault("duration_sec", 0.0)
                    try:
                        mtime = os.path.getmtime(fpath)
                    except OSError:
                        mtime = 0.0
                    self._cache[fpath] = meta
                    self._meta[fpath] = meta
                    self._row_filepaths.append(fpath)
                    self._add_row(fpath, fname, meta, meta.get("duration_sec", 0.0), mtime)
                    if dirty:
                        self._save_cache()
        self._table.setSortingEnabled(True)
        self._apply_filter()
        h = self._table.horizontalHeader()
        h.setStretchLastSection(True)
        for c in range(self._table.columnCount()):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(self.COL_NAME, 180)
        self._table.setColumnWidth(self.COL_DUR, 70)
        self._table.setColumnWidth(self.COL_DATE, 80)
        self._table.setColumnWidth(self.COL_SR, 55)
        self._table.setColumnWidth(self.COL_SIZE, 60)

    def _add_row(self, fpath: str, fname: str, meta: dict, duration: float, mtime: float):
        from datetime import datetime as _dt
        r = self._table.rowCount()
        self._table.insertRow(r)

        name_item = QTableWidgetItem(os.path.splitext(fname)[0])
        name_item.setData(Qt.ItemDataRole.UserRole + 1, fpath)
        name_item.setToolTip(fpath)
        self._table.setItem(r, self.COL_NAME, name_item)

        dur_text = f"{int(duration // 60)}:{duration % 60:04.1f}" if duration > 0 else ""
        dur_item = _NumericTableItem(dur_text)
        dur_item.setData(Qt.ItemDataRole.UserRole, float(duration) if duration > 0 else 0.0)
        dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, self.COL_DUR, dur_item)

        try:
            date_text = _dt.fromtimestamp(mtime).strftime("%Y-%m-%d") if mtime > 0 else ""
        except (OSError, ValueError):
            date_text = ""
        date_item = _NumericTableItem(date_text)
        date_item.setData(Qt.ItemDataRole.UserRole, float(mtime))
        self._table.setItem(r, self.COL_DATE, date_item)

        sr = meta.get("samplerate", 0)
        sr_item = _NumericTableItem(f"{sr // 1000}k" if sr else "")
        sr_item.setData(Qt.ItemDataRole.UserRole, float(sr))
        sr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, self.COL_SR, sr_item)

        fsize = meta.get("filesize", 0)
        size_item = _NumericTableItem(_human_size(fsize) if fsize else "")
        size_item.setData(Qt.ItemDataRole.UserRole, float(fsize))
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._table.setItem(r, self.COL_SIZE, size_item)

        path_item = QTableWidgetItem(fpath)
        path_item.setToolTip(fpath)
        path_item.setForeground(QColor(TEXT_SECONDARY))
        self._table.setItem(r, self.COL_PATH, path_item)

    def _handle_double_click(self, filepath: str):
        self.load_to_channel.emit(filepath, -1)

    def _on_delete_selected(self):
        fps = self._get_selected_filepaths()
        if not fps:
            return
        if not _confirm_trash(self, "Delete Songs", fps):
            return
        failed = _trash_files(fps)
        for p in fps:
            if p not in failed:
                self._cache.pop(p, None)
        self._save_cache()
        self.refresh()
        _warn_trash_failures(self, failed)

    def _build_context_menu(self, menu: QMenu, filepaths: list[str]) -> None:
        load_act = menu.addAction("Load to Channel")
        load_act.triggered.connect(
            lambda: self.load_to_channel.emit(filepaths[0], -1) if filepaths else None
        )
        menu.addSeparator()
        del_act = menu.addAction("Delete from Disk")
        del_act.triggered.connect(self._on_delete_selected)

    def _on_scan_result_persist(self, filepath: str, bpm: float, key: str):
        pass  # no BPM/Key analysis for songs

    def _extra_row_visible(self, row: int) -> bool:
        return True

    # Overrides for methods that reference BPM/Key which don't exist here
    def _refresh_key_filter_options(self):
        pass

    def _apply_filter(self):
        query = self._search_edit.text().strip().lower()
        for r in range(self._table.rowCount()):
            name_item = self._table.item(r, self.COL_NAME)
            name = name_item.text().lower() if name_item else ""
            visible = not query or query in name
            self._table.setRowHidden(r, not visible)

    def _files_to_scan(self) -> list[str]:
        return []

    def _get_selected_filepaths(self) -> list[str]:
        fps = []
        for item in self._table.selectedItems():
            if item.column() == self.COL_NAME:
                fp = item.data(Qt.ItemDataRole.UserRole + 1)
                if fp:
                    fps.append(fp)
        return fps


class LibraryPanel(QFrame):
    """Top-level Library tool tab — wraps SLICES + SAMPLES + SONGS sub-tabs."""

    load_to_slicer = Signal(str)
    load_to_channel = Signal(str, int)
    preview_requested = Signal(str, float)  # filepath, volume 0.0–1.0
    preview_stop_requested = Signal()        # stop current preview

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"LibraryPanel {{ background: {BG_BLACK}; "
            f"border: 1px solid {SEPARATOR}; }}"
        )
        vb = QVBoxLayout(self)
        vb.setContentsMargins(2, 2, 2, 2)
        vb.setSpacing(2)

        title = QLabel("SAMPLE LIBRARY")
        title.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 9px; font-weight: bold; "
            f"background: {BG_PANEL}; padding: 3px 6px;"
        )
        vb.addWidget(title)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._slices_tab = _LibrarySlicesTab()
        self._samples_tab = _LibrarySamplesTab()
        self._songs_tab = _LibrarySongsTab()
        self._tabs.addTab(self._samples_tab, "SAMPLES")
        self._tabs.addTab(self._slices_tab, "SLICES")
        self._tabs.addTab(self._songs_tab, "SONGS")
        vb.addWidget(self._tabs, stretch=1)

        # Bubble all signals
        for tab in (self._slices_tab, self._samples_tab):
            tab.load_to_slicer.connect(self.load_to_slicer.emit)
            tab.load_to_channel.connect(self.load_to_channel.emit)
            tab.preview_requested.connect(self.preview_requested.emit)
            tab.preview_stop_requested.connect(self.preview_stop_requested.emit)
        # Songs tab: only load_to_channel
        self._songs_tab.load_to_channel.connect(self.load_to_channel.emit)

    def refresh(self):
        self._slices_tab.refresh()
        self._samples_tab.refresh()

    @property
    def slices_tab(self) -> _LibrarySlicesTab:
        return self._slices_tab

    @property
    def samples_tab(self) -> _LibrarySamplesTab:
        return self._samples_tab

    @property
    def songs_tab(self) -> "_LibrarySongsTab":
        return self._songs_tab


class ToolsView(QWidget):
    """Tabbed tools panel: Record + Slice + Library."""

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
