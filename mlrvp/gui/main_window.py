"""Main window — assembles all views into the mlrVp interface."""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QStatusBar,
    QFileDialog, QDialog, QMessageBox,
    QTextBrowser, QPushButton, QButtonGroup, QLabel, QSlider,
    QDialogButtonBox,
)
from PySide6.QtCore import Qt, Signal, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QPixmap, QDesktopServices
import logging

_log = logging.getLogger(__name__)

ISSUES_URL = "https://github.com/Trumanac/mlrVp/issues/new/choose"

from mlrvp.gui.views.channel_view import ChannelView
from mlrvp.gui.views.mixer_view import MixerView
from mlrvp.gui.views.effects_view import EffectsView
from mlrvp.gui.views.transport_view import TransportView
from mlrvp.gui.views.setup_view import SetupView
from mlrvp.gui.views.tools_view import ToolsView, LibraryPanel
from mlrvp.gui.views.master_filter_view import MasterFilterView
from mlrvp.gui.widgets.virtual_grid import VirtualGridWidget
from mlrvp.gui.widgets.fader import ControlDisplaySettings
from mlrvp.gui.widgets.song_recorder_panel import SongRecorderPanel
from mlrvp.gui.styles import get_stylesheet, BG_BLACK
from mlrvp.gui.scale import (
    sp,
    bind_fixed_height,
    bind_min_width, bind_max_width, bind_min_size,
    scale_manager,
)


def _load_howto_html() -> str:
    """Load the How-to-Use HTML from the bundled resources file.

    Reads on demand (only when the user opens the dialog) so we don't
    pay the file-read cost or pin ~22 KB of HTML in memory at import time.
    """
    try:
        from importlib.resources import files
        return files("mlrvp.resources").joinpath("howto.html").read_text(encoding="utf-8")
    except Exception as exc:
        return f"<h2>Error loading help</h2><p>{exc}</p>"


class ScaleDialog(QDialog):
    """Slider-based UI scale picker with live preview.

    Range: 75%-200% in 25% steps.  Emits :attr:`factor_changed` whenever the
    slider value changes so the controller can apply the new factor live.
    Clicking Cancel reverts to the factor in effect when the dialog opened.
    """

    MIN_PCT = 75
    MAX_PCT = 200
    STEP_PCT = 25

    factor_changed = Signal(float)

    def __init__(self, current_factor: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("UI Scale")
        self.setModal(True)
        self.setMinimumWidth(sp(360))

        # Snap incoming factor to nearest valid step
        current_pct = int(round(current_factor * 100 / self.STEP_PCT)) * self.STEP_PCT
        current_pct = max(self.MIN_PCT, min(self.MAX_PCT, current_pct))
        self._last_emitted_pct = current_pct

        # Coalesce rapid slider changes into a single apply (avoids freeze
        # when dragging across multiple snap stops).  While the user is
        # actively dragging we do NOT apply yet — only the label updates
        # live.  The actual scale is committed on slider release or after
        # a short idle period for keyboard / wheel input.
        self._emit_timer = QTimer(self)
        self._emit_timer.setSingleShot(True)
        self._emit_timer.setInterval(180)  # ms — idle window for non-drag input
        self._emit_timer.timeout.connect(self._emit_pending)
        self._pending_pct: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        info = QLabel(
            "Adjust the overall UI scale.\n"
            "Changes preview live; click OK to keep them or Cancel to revert."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self._value_label = QLabel(f"UI Scale: {current_pct}%")
        self._value_label.setAlignment(Qt.AlignCenter)
        f = self._value_label.font()
        f.setBold(True)
        self._value_label.setFont(f)
        layout.addWidget(self._value_label)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setMinimum(self.MIN_PCT)
        self._slider.setMaximum(self.MAX_PCT)
        self._slider.setSingleStep(self.STEP_PCT)
        self._slider.setPageStep(self.STEP_PCT)
        self._slider.setTickInterval(self.STEP_PCT)
        self._slider.setTickPosition(QSlider.TicksBelow)
        self._slider.setValue(current_pct)
        self._slider.valueChanged.connect(self._on_slider_changed)
        # When the user releases the slider, flush any pending apply
        # immediately so the final value lands without waiting for the
        # debounce timer.
        self._slider.sliderReleased.connect(self._emit_pending)
        layout.addWidget(self._slider)

        # Tick labels row (75 100 125 150 175 200)
        ticks = QHBoxLayout()
        ticks.setContentsMargins(2, 0, 2, 0)
        for pct in range(self.MIN_PCT, self.MAX_PCT + 1, self.STEP_PCT):
            lbl = QLabel(f"{pct}%")
            lbl.setAlignment(Qt.AlignCenter)
            ticks.addWidget(lbl, 1)
        layout.addLayout(ticks)

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        reset_btn = btns.addButton("Reset to 100%", QDialogButtonBox.ResetRole)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        reset_btn.clicked.connect(lambda: self._slider.setValue(100))
        layout.addWidget(btns)

    def _on_slider_changed(self, value: int):
        # Snap to nearest STEP_PCT
        snapped = int(round(value / self.STEP_PCT)) * self.STEP_PCT
        snapped = max(self.MIN_PCT, min(self.MAX_PCT, snapped))
        if snapped != value:
            self._slider.blockSignals(True)
            self._slider.setValue(snapped)
            self._slider.blockSignals(False)
        self._value_label.setText(f"UI Scale: {snapped}%")
        if snapped == self._last_emitted_pct:
            self._pending_pct = None
            self._emit_timer.stop()
            return
        self._pending_pct = snapped
        # If the user is dragging the handle, wait for sliderReleased
        # rather than running the heavy stylesheet rebuild mid-drag.
        if self._slider.isSliderDown():
            self._emit_timer.stop()
            return
        # Keyboard arrows / mouse wheel / clicks on the track: debounce
        self._emit_timer.start()

    def _emit_pending(self):
        if self._pending_pct is None:
            return
        pct = self._pending_pct
        self._pending_pct = None
        if pct == self._last_emitted_pct:
            return
        self._last_emitted_pct = pct
        self.factor_changed.emit(pct / 100.0)

    def accept(self):
        # Make sure the final value is committed even if the debounce timer
        # hasn't fired yet.
        if self._emit_timer.isActive():
            self._emit_timer.stop()
        self._emit_pending()
        super().accept()

    def selected_factor(self) -> float:
        return self._slider.value() / 100.0


class VirtualGridWindow(QDialog):
    """Pop-out window for the virtual grid controller."""

    # Emitted when the user selects a new grid size via the size toggle buttons.
    # Only fires when no hardware grid is connected.
    grid_size_changed = Signal(int, int)  # rows, columns

    # Standard monome sizes: (label, rows, cols)
    _GRID_SIZES = [
        ("64\n8×8",    8,  8),
        ("128\n8×16",  8, 16),
        ("128\n16×8", 16,  8),
        ("256\n16×16", 16, 16),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("mlrVp — Virtual Grid")
        self.setStyleSheet(f"background: {BG_BLACK};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Size toggle row ──────────────────────────────────────────────
        size_row = QHBoxLayout()
        size_row.setSpacing(4)
        size_row.setContentsMargins(0, 0, 0, 0)

        self._size_btn_group = QButtonGroup(self)
        self._size_btn_group.setExclusive(True)
        self._size_buttons: list[QPushButton] = []

        for i, (label, rows, cols) in enumerate(self._GRID_SIZES):
            btn = QPushButton(label)
            btn.setCheckable(True)
            bind_fixed_height(btn, 32)
            btn.setStyleSheet(
                "QPushButton { background: #1a1a1a; color: #888; border: 1px solid #333;"
                "  font-size: 9px; padding: 0; }"
                "QPushButton:checked { background: #2a4a2a; color: #aef; border: 1px solid #4af; }"
                "QPushButton:hover { background: #222; }"
                "QPushButton:disabled { color: #444; }"
            )
            btn.clicked.connect(lambda checked, r=rows, c=cols: self._on_size_btn(r, c))
            self._size_btn_group.addButton(btn, i)
            self._size_buttons.append(btn)
            size_row.addWidget(btn)

        # Check the first button (64 / 8×8) by default
        self._size_buttons[0].setChecked(True)

        layout.addLayout(size_row)

        # ── Grid widget ──────────────────────────────────────────────────
        self._grid = VirtualGridWidget(rows=8, columns=8)
        layout.addWidget(self._grid)

        self._hardware_connected = False
        self._update_size()
        # Re-fit when the global UI scale changes (the grid widget itself
        # also re-applies its button size on this signal — connection order
        # ensures it has already updated by the time we read button_size).
        scale_manager.changed.connect(lambda _f: self._update_size())

    def _on_size_btn(self, rows: int, cols: int):
        """Size preset button clicked — emit only when no hardware grid."""
        if not self._hardware_connected:
            self.grid_size_changed.emit(rows, cols)

    @property
    def grid(self) -> VirtualGridWidget:
        return self._grid

    def set_grid_dimensions(self, rows: int, columns: int):
        """Update the virtual grid size and sync the toggle buttons."""
        self._grid.set_dimensions(rows, columns)
        self._update_size()
        # Sync button selection to the nearest matching size preset
        for i, (_, r, c) in enumerate(self._GRID_SIZES):
            if r == rows and c == columns:
                self._size_buttons[i].blockSignals(True)
                self._size_buttons[i].setChecked(True)
                self._size_buttons[i].blockSignals(False)
                break

    def set_hardware_connected(self, connected: bool):
        """Disable size toggle buttons when a hardware grid is driving the layout."""
        self._hardware_connected = connected
        for btn in self._size_buttons:
            btn.setEnabled(not connected)

    def _update_size(self):
        # Pull live-scaled button geometry directly from the grid widget.
        # We read the *base* sizes and re-apply sp() ourselves so this works
        # regardless of signal connection order with the grid's own slot.
        bs = sp(self._grid._base_button_size)
        spacing = sp(self._grid._base_button_spacing)
        # Keep the grid widget in sync (idempotent if its own slot already ran)
        self._grid.button_size = bs
        self._grid.button_spacing = spacing
        grid_w = self._grid.columns * (bs + spacing) + spacing + sp(8)
        grid_h = self._grid.rows * (bs + spacing) + spacing + sp(8)
        total_w = max(grid_w, sp(200))
        # button row (32) + extra spacing (4) + outer margins (8)
        total_h = grid_h + sp(32) + sp(4) + sp(8)
        # Clear any prior fixed constraint before setting the new one so Qt
        # always honours the new size (setFixedSize is no-op when the value
        # is identical to the cached min/max — which it isn't here, but the
        # explicit reset is defensive against odd states after style reload).
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setFixedSize(total_w, total_h)

    def closeEvent(self, event):
        # Hide instead of destroying so it can be re-opened
        self.hide()
        event.ignore()


class MainWindow(QMainWindow):
    """Main application window matching the mlrV2 layout."""

    # Signals for app controller
    quit_requested = Signal()
    close_requested = Signal()   # fired first — controller may show a save dialog
    preset_file_open_requested = Signal(str)
    preset_file_save_requested = Signal(str)
    stop_all_requested = Signal()
    restart_requested = Signal()
    scale_change_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)

        from mlrvp import __version__
        self.setWindowTitle(f"mlrVp {__version__}")
        bind_min_size(self, 1380, 600)
        self.resize(sp(1620), sp(700))  # initial size — wide enough that no controls are squished

        # Apply dark theme (regenerated whenever scale changes by the controller)
        from mlrvp.gui import scale as _ui_scale
        self.setStyleSheet(get_stylesheet(_ui_scale.get_scale()))

        # Virtual grid pop-out window (created but hidden)
        self._grid_window = VirtualGridWindow(self)
        self._has_hardware_grid = False
        self._allow_close = False   # set by AppController after save-dialog confirmed

        # --- Menu Bar ---
        self._build_menu_bar()

        # --- Central Widget ---
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Transport bar
        self._transport = TransportView()
        main_layout.addWidget(self._transport)

        # Main content splitter (channels | side panel)
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Channel strips
        self._channel_view = ChannelView(num_rows=8)
        self._main_splitter.addWidget(self._channel_view)

        # Right: Tabbed side panel (Effects, Tools, Setup)
        self._side_tabs = QTabWidget()
        self._side_tabs.setDocumentMode(True)

        self._effects_view = EffectsView()
        self._side_tabs.addTab(self._effects_view, "Effects")

        self._library_panel = LibraryPanel()
        self._side_tabs.addTab(self._library_panel, "Library")

        self._tools_view = ToolsView()
        self._side_tabs.addTab(self._tools_view, "Tools")

        self._setup_view = SetupView()
        self._side_tabs.addTab(self._setup_view, "Setup")

        bind_min_width(self._side_tabs, 400)
        bind_max_width(self._side_tabs, 600)

        # Right panel: plain layout — tabs on top, master filter + mixer pinned at bottom
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Tabs fill available space above the fixed bottom section
        right_layout.addWidget(self._side_tabs, stretch=1)

        # Master filter + mixer pinned at bottom
        self._master_filter_view = MasterFilterView()
        bind_fixed_height(self._master_filter_view, 52)
        right_layout.addWidget(self._master_filter_view)

        self._mixer_view = MixerView()
        right_layout.addWidget(self._mixer_view)

        self._song_recorder_panel = SongRecorderPanel()
        right_layout.addWidget(self._song_recorder_panel)

        bind_min_width(right_panel, 400)
        bind_max_width(right_panel, 600)
        self._main_splitter.addWidget(right_panel)

        # Set splitter proportions (channels get more space)
        self._main_splitter.setSizes([1020, 560])
        main_layout.addWidget(self._main_splitter, stretch=1)

        # --- Status Bar ---
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("mlrVp — Ready")

        # Auto-size to fit content
        self._auto_resize()

    def _auto_resize(self):
        """Set initial window size to fit 8 rows comfortably."""
        self._auto_resize_for_rows(8)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_mixer_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_mixer_height()

    def _sync_mixer_height(self):
        """Set mixer to exactly 3 channel rows tall."""
        try:
            # Measure actual row height from the channel strips
            s0 = self._channel_view.get_strip(0)
            s1 = self._channel_view.get_strip(1)
            row_h = s1.y() - s0.y()  # pixel distance between row starts = 1 row
            if row_h > 0:
                target = row_h * 3
                # Guard against recursive resize: setFixedHeight triggers a
                # relayout which can re-fire resizeEvent -> _sync_mixer_height.
                if self._mixer_view.maximumHeight() != target:
                    self._mixer_view.setFixedHeight(target)
        except Exception as e:
            _log.debug("_sync_mixer_height: %s", e)

    def _build_menu_bar(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        load_preset_action = QAction("&Open Preset File...", self)
        load_preset_action.setShortcut(QKeySequence.StandardKey.Open)
        load_preset_action.triggered.connect(self._on_open_preset)
        file_menu.addAction(load_preset_action)

        save_preset_action = QAction("&Save Preset File...", self)
        save_preset_action.setShortcut(QKeySequence.StandardKey.Save)
        save_preset_action.triggered.connect(self._on_save_preset)
        file_menu.addAction(save_preset_action)

        file_menu.addSeparator()

        import_data_action = QAction("&Import Data from Previous Version…", self)
        import_data_action.setToolTip(
            "Copy presets, setlists, racks, settings and recordings from an "
            "older mlrVp folder (existing files are never overwritten)")
        import_data_action.triggered.connect(self._on_import_data)
        file_menu.addAction(import_data_action)

        open_data_action = QAction("Open &Data Folder", self)
        open_data_action.setToolTip("Show where presets, setlists, racks and settings are stored")
        open_data_action.triggered.connect(self._on_open_data_folder)
        file_menu.addAction(open_data_action)

        file_menu.addSeparator()

        restart_action = QAction("&Restart", self)
        restart_action.triggered.connect(self._on_restart)
        file_menu.addAction(restart_action)

        stop_all_action = QAction("Stop &All Audio", self)
        stop_all_action.setShortcut("Ctrl+Shift+S")
        stop_all_action.triggered.connect(self._on_stop_all)
        file_menu.addAction(stop_all_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # View menu
        view_menu = menubar.addMenu("&View")

        self._toggle_grid_action = QAction("Show &Virtual Grid", self)
        self._toggle_grid_action.setCheckable(True)
        self._toggle_grid_action.setChecked(False)
        self._toggle_grid_action.toggled.connect(self._on_toggle_grid)
        view_menu.addAction(self._toggle_grid_action)

        view_menu.addSeparator()
        scale_action = QAction("UI &Scale\u2026", self)
        scale_action.setToolTip("Adjust the UI scale factor (live, no restart)")
        scale_action.triggered.connect(self._on_open_scale_dialog)
        view_menu.addAction(scale_action)

        view_menu.addSeparator()

        self._lock_displays_action = QAction("&Lock Control Displays", self)
        self._lock_displays_action.setCheckable(True)
        self._lock_displays_action.setChecked(False)
        self._lock_displays_action.setToolTip(
            "Keep value overlays visible on all knobs and faders permanently")
        self._lock_displays_action.toggled.connect(
            lambda on: setattr(ControlDisplaySettings, 'locked', on))
        view_menu.addAction(self._lock_displays_action)

        self._disable_displays_action = QAction("&Disable Control Displays", self)
        self._disable_displays_action.setCheckable(True)
        self._disable_displays_action.setChecked(False)
        self._disable_displays_action.setToolTip(
            "Never show value overlays on knobs and faders")
        self._disable_displays_action.toggled.connect(self._on_disable_displays)
        view_menu.addAction(self._disable_displays_action)

        # Lock and Disable are mutually exclusive: checking one unchecks the other
        _disp_group = QActionGroup(self)
        _disp_group.setExclusive(False)  # manual mutual exclusion (allow both unchecked)
        _disp_group.addAction(self._lock_displays_action)
        _disp_group.addAction(self._disable_displays_action)
        self._lock_displays_action.toggled.connect(
            lambda on: self._disable_displays_action.setChecked(False) if on else None)
        self._disable_displays_action.toggled.connect(
            lambda on: self._lock_displays_action.setChecked(False) if on else None)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        howto_action = QAction("&How to Use", self)
        howto_action.triggered.connect(self._on_howto)
        help_menu.addAction(howto_action)

        help_menu.addSeparator()

        open_logs_action = QAction("Open &Log Folder", self)
        open_logs_action.setToolTip("Show mlrvp.log — attach it when reporting a bug")
        open_logs_action.triggered.connect(self._on_open_log_folder)
        help_menu.addAction(open_logs_action)

        report_bug_action = QAction("&Report a Bug…", self)
        report_bug_action.triggered.connect(self._on_report_bug)
        help_menu.addAction(report_bug_action)

        help_menu.addSeparator()

        about_action = QAction("&About mlrVp", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _on_disable_displays(self, on: bool):
        ControlDisplaySettings.disabled = on
        # Force-repaint every custom control so visible overlays clear immediately
        for w in self.findChildren(QWidget):
            if hasattr(w, '_popup_visible'):
                if on:
                    # Also stop any pending auto-hide timers to keep state clean
                    if hasattr(w, '_popup_timer'):
                        w._popup_timer.stop()
                w.update()

    def _on_toggle_grid(self, visible: bool):
        if visible:
            self._grid_window.show()
            self._grid_window.raise_()
        else:
            self._grid_window.hide()

    def _on_open_preset(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Preset File", "",
            "mlrVp Presets (*.json);;All Files (*)"
        )
        if path:
            self.preset_file_open_requested.emit(path)

    def _on_save_preset(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Preset File", "",
            "mlrVp Presets (*.json);;All Files (*)"
        )
        if path:
            self.preset_file_save_requested.emit(path)

    def _on_stop_all(self):
        self.stop_all_requested.emit()

    def _on_restart(self):
        self.restart_requested.emit()

    def _on_open_data_folder(self):
        from mlrvp.config import get_config_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_config_dir())))

    def _on_open_log_folder(self):
        from mlrvp.config import get_logs_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_logs_dir())))

    def _on_report_bug(self):
        QDesktopServices.openUrl(QUrl(ISSUES_URL))

    def _on_import_data(self):
        from pathlib import Path
        from mlrvp.datadirs import import_data_from
        folder = QFileDialog.getExistingDirectory(
            self, "Select the folder of your previous mlrVp version")
        if not folder:
            return
        try:
            found, copied = import_data_from(Path(folder))
        except OSError as e:
            QMessageBox.warning(
                self, "Import Data",
                f"Import stopped part-way:\n{e}\n\nFiles copied so far were kept.")
            return
        if found is None:
            QMessageBox.information(
                self, "Import Data",
                "No mlrVp data was found in that folder.\n\n"
                "Pick the folder you extracted the old version into "
                "(the one containing mlrVp.exe, mlrVp.app or mlrVp).")
            return
        if copied == 0:
            QMessageBox.information(
                self, "Import Data",
                "Nothing new to import — everything in that folder is already here.")
            return
        reply = QMessageBox.question(
            self, "Import Data",
            f"Imported {copied} file(s) from:\n{found}\n\n"
            "Existing files were not overwritten.\n"
            "Restart mlrVp now to load everything?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._on_restart()

    def _on_scale(self, factor: float):
        """Emit a scale change request.  The controller applies it live."""
        self.scale_change_requested.emit(factor)

    def _on_open_scale_dialog(self):
        """Open the live UI scale dialog.

        Changes are applied live as the slider moves; clicking Cancel
        restores the original factor.
        """
        from mlrvp.gui import scale as ui_scale
        original = ui_scale.get_scale()
        dlg = ScaleDialog(original, self)
        # Live preview: every slider change applies the factor immediately
        dlg.factor_changed.connect(self._on_scale)
        if dlg.exec() != QDialog.Accepted:
            # User cancelled — revert to the original factor
            self._on_scale(original)

    def _on_about(self):
        from mlrvp import __version__ as _ver
        import os

        dlg = QDialog(self)
        dlg.setWindowTitle("About mlrVp")
        dlg.setFixedWidth(sp(420))

        outer = QHBoxLayout(dlg)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(20)

        # Logo on the left
        icon_path = os.path.join(os.path.dirname(__file__), "..", "resources", "icon.png")
        icon_path = os.path.normpath(icon_path)
        if os.path.exists(icon_path):
            pix = QPixmap(icon_path).scaled(
                96, 96,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon_label = QLabel()
            icon_label.setPixmap(pix)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            outer.addWidget(icon_label)

        # Text on the right
        text_label = QLabel(
            "<h2 style='margin:0 0 6px 0'>mlrVp</h2>"
            "<p style='margin:0 0 4px 0'>A modern Python recreation of mlrV2.2</p>"
            "<p style='margin:0 0 4px 0'>Based on the original Max/MSP mlrV by<br>"
            "trent gill &amp; friends at monome.org</p>"
            "<p style='margin:0 0 4px 0'>Rebuilt with PySide6, sounddevice, and numpy.</p>"
            f"<p><b>Version {_ver}</b></p>"
        )
        text_label.setWordWrap(True)
        text_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        outer.addWidget(text_label, 1)

        dlg.exec()

    def _on_howto(self):
        """Show a scrollable How-to-Use guide popup (non-modal — drag it around freely)."""
        if hasattr(self, '_howto_dialog') and self._howto_dialog is not None:
            self._howto_dialog.raise_()
            self._howto_dialog.activateWindow()
            return
        dlg = QDialog(self, Qt.WindowType.Window)
        dlg.setWindowTitle("mlrVp — How to Use")
        dlg.resize(sp(700), sp(580))
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.destroyed.connect(lambda: setattr(self, '_howto_dialog', None))
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(8, 8, 8, 8)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(_load_howto_html())
        layout.addWidget(browser)

        self._howto_dialog = dlg
        dlg.show()

    # --- Public accessors for app controller ---

    @property
    def transport(self) -> TransportView:
        return self._transport

    @property
    def channel_view(self) -> ChannelView:
        return self._channel_view

    @property
    def mixer_view(self) -> MixerView:
        return self._mixer_view

    @property
    def master_filter_view(self) -> MasterFilterView:
        return self._master_filter_view

    @property
    def song_recorder_panel(self) -> SongRecorderPanel:
        return self._song_recorder_panel

    @property
    def effects_view(self) -> EffectsView:
        return self._effects_view

    @property
    def library_panel(self) -> LibraryPanel:
        return self._library_panel

    @property
    def tools_view(self) -> ToolsView:
        return self._tools_view

    @property
    def setup_view(self) -> SetupView:
        return self._setup_view

    @property
    def virtual_grid(self) -> VirtualGridWidget:
        return self._grid_window.grid

    @property
    def grid_window(self) -> VirtualGridWindow:
        return self._grid_window

    def set_status(self, msg: str):
        self._status_bar.showMessage(msg)

    def set_virtual_grid_visible(self, visible: bool):
        if visible:
            self._grid_window.show()
            self._grid_window.raise_()
        else:
            self._grid_window.hide()
        self._toggle_grid_action.blockSignals(True)
        self._toggle_grid_action.setChecked(visible)
        self._toggle_grid_action.blockSignals(False)
        # Sync the transport bar grid button
        self._transport.set_virtual_grid_active(visible)

    def set_hardware_grid_connected(self, connected: bool):
        """Track whether a hardware grid is connected.
        If no hardware grid, auto-show the virtual grid popout."""
        self._has_hardware_grid = connected
        self._grid_window.set_hardware_connected(connected)
        if not connected:
            self.set_virtual_grid_visible(True)

    def set_grid_dimensions(self, rows: int, columns: int):
        """Update virtual grid and channel view for the row count."""
        self._grid_window.set_grid_dimensions(rows, columns)
        self._channel_view.set_visible_rows(rows)

    def _auto_resize_for_rows(self, row_count: int):
        """Set the initial window height to comfortably show row_count rows.

        Only called at startup (via _auto_resize).  Changing the grid size
        after startup no longer resizes the window — the ChannelView's
        internal QScrollArea handles overflow rows instead.
        """
        row_height = 100
        transport_h = 52
        mixer_h = 220
        status_h = 26
        margins = 24
        needed_h = transport_h + (row_count * row_height) + mixer_h + status_h + margins
        needed_h = max(min(needed_h, 2000), 1100)
        target_h = sp(needed_h)
        target_w = max(self.width(), sp(1620))
        self.resize(target_w, target_h)

    def confirm_close(self):
        """Called by AppController to actually close the window after dialog."""
        self._allow_close = True
        self.close()

    def closeEvent(self, event):
        if self._allow_close:
            self._grid_window.close()
            self.quit_requested.emit()
            event.accept()
        else:
            self.close_requested.emit()
            event.ignore()


# ---------------------------------------------------------------------------
# First-run welcome dialog
# ---------------------------------------------------------------------------

class WelcomeDialog(QDialog):
    """Shown once on a fresh install to orient new users."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to mlrVp")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        text = QLabel(
            "<h2>Welcome to mlrVp!</h2>"
            "<p>mlrVp is a grid-based live sampler. Here's how to get started:</p>"
            "<ol>"
            "<li><b>Setup → Audio</b> — choose your audio output device and click "
            "<b>Start Engine</b>.</li>"
            "<li><b>Drag a WAV/AIFF/FLAC file</b> onto any channel row's waveform "
            "area to load a sample.</li>"
            "<li>Click a cell on the <b>virtual grid</b> (or a hardware button) to "
            "trigger playback.</li>"
            "<li>Open <b>Help → How to Use</b> for the full user guide.</li>"
            "</ol>"
            "<p>No grid controller? The on-screen virtual grid works standalone — "
            "open it from <b>View → Show Virtual Grid</b>.</p>"
        )
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(text)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def maybe_show_welcome(parent, startup_settings_path: str) -> None:
    """Show WelcomeDialog on first run.

    Uses a ``welcome_shown`` flag inside startup_settings.json so the dialog
    doesn't re-appear after the user has seen it — even when the file ships
    pre-populated as a default.
    """
    import json
    import os
    path = startup_settings_path
    # Read existing settings (if any) and check the welcome_shown flag.
    data: dict = {}
    if os.path.exists(path):
        try:
            data = json.loads(open(path, encoding="utf-8").read())
        except Exception:
            pass
    if data.get("welcome_shown"):
        return
    dlg = WelcomeDialog(parent)
    dlg.exec()
    # Persist the flag so the dialog doesn't appear again.
    data["welcome_shown"] = True
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass
