"""Main window — assembles all views into the mlrVp interface."""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QStatusBar, QMenuBar,
    QMenu, QMessageBox, QFileDialog, QDialog, QSizePolicy,
    QTextBrowser, QPushButton, QButtonGroup, QLabel,
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QAction, QKeySequence, QIcon

from mlrvp.gui.views.channel_view import ChannelView
from mlrvp.gui.views.mixer_view import MixerView
from mlrvp.gui.views.effects_view import EffectsView
from mlrvp.gui.views.transport_view import TransportView
from mlrvp.gui.views.setup_view import SetupView
from mlrvp.gui.views.tools_view import ToolsView, LibraryPanel
from mlrvp.gui.views.master_filter_view import MasterFilterView
from mlrvp.gui.widgets.virtual_grid import VirtualGridWidget
from mlrvp.gui.widgets.fader import ControlDisplaySettings
from mlrvp.gui.styles import get_stylesheet, BG_BLACK


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


class VirtualGridWindow(QDialog):
    """Pop-out window for the virtual grid controller."""

    # Emitted when the user selects a new grid size via the size toggle buttons.
    # Only fires when no hardware grid is connected.
    grid_size_changed = Signal(int, int)  # rows, columns

    # Standard monome sizes: (label, rows, cols)
    _GRID_SIZES = [
        ("64\n8×8",   8,  8),
        ("128\n8×16", 8, 16),
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
            btn.setFixedHeight(32)
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
        bs = self._grid.button_size
        sp = self._grid.button_spacing
        grid_w = self._grid.columns * (bs + sp) + sp + 8
        grid_h = self._grid.rows * (bs + sp) + sp + 8
        # Add room for the size-toggle row (32px buttons + spacing + top/bottom margins)
        total_w = max(grid_w, 200)
        total_h = grid_h + 32 + 4 + 8  # grid + btn row + spacing + margins
        self.setFixedSize(total_w, total_h)

    def closeEvent(self, event):
        # Hide instead of destroying so it can be re-opened
        self.hide()
        event.ignore()


class MainWindow(QMainWindow):
    """Main application window matching the mlrV2 layout."""

    # Signals for app controller
    quit_requested = Signal()
    preset_file_open_requested = Signal(str)
    preset_file_save_requested = Signal(str)
    stop_all_requested = Signal()
    restart_requested = Signal()
    scale_change_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("mlrVp")
        self.setMinimumSize(1380, 600)
        self.resize(1620, 700)  # initial size — wide enough that no controls are squished

        # Apply dark theme
        self.setStyleSheet(get_stylesheet())

        # Virtual grid pop-out window (created but hidden)
        self._grid_window = VirtualGridWindow(self)
        self._has_hardware_grid = False

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

        self._side_tabs.setMinimumWidth(400)
        self._side_tabs.setMaximumWidth(600)

        # Right panel: plain layout — tabs on top, master filter + mixer pinned at bottom
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Tabs fill available space above the fixed bottom section
        right_layout.addWidget(self._side_tabs, stretch=1)

        # Master filter + mixer pinned at bottom
        self._master_filter_view = MasterFilterView()
        self._master_filter_view.setFixedHeight(52)
        right_layout.addWidget(self._master_filter_view)

        self._mixer_view = MixerView()
        right_layout.addWidget(self._mixer_view)

        right_panel.setMinimumWidth(400)
        right_panel.setMaximumWidth(600)
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
        """Resize window to fit all 8 rows without scrolling."""
        # Use the SAME formula as _auto_resize_for_rows so the initial size
        # matches the connected-grid size — prevents the window from "shrinking"
        # when the user disconnects/reconnects a grid or when no grid is found.
        self._auto_resize_for_rows(8)
        # Also enforce a hard minimum so any later resize call cannot shrink
        # the window below the 8-row layout (which would clip the channel view).
        self.setMinimumHeight(1100)

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
        except Exception:
            pass

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
        scale_menu = view_menu.addMenu("UI &Scale")
        from PySide6.QtGui import QActionGroup
        from mlrvp.config import get_ui_scale
        current_scale = get_ui_scale()
        scale_group = QActionGroup(self)
        for pct in (50, 75, 100, 125, 150, 175, 200):
            act = QAction(f"{pct}%", self)
            act.setCheckable(True)
            act.setChecked(abs(pct / 100.0 - current_scale) < 0.01)
            factor = pct / 100.0
            act.triggered.connect(lambda checked, f=factor: self._on_scale(f))
            scale_group.addAction(act)
            scale_menu.addAction(act)

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

        # Help menu
        help_menu = menubar.addMenu("&Help")

        howto_action = QAction("&How to Use", self)
        howto_action.triggered.connect(self._on_howto)
        help_menu.addAction(howto_action)

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

    def _on_scale(self, factor: float):
        from mlrvp.config import set_ui_scale
        set_ui_scale(factor)
        self.scale_change_requested.emit(factor)

    def _on_about(self):
        from mlrvp import __version__ as _ver
        QMessageBox.about(
            self, "About mlrVp",
            "<h2>mlrVp</h2>"
            "<p>A modern Python recreation of mlrV2.2</p>"
            "<p>Based on the original Max/MSP mlrV by<br>"
            "trent gill &amp; friends at monome.org</p>"
            "<p>Rebuilt with PySide6, sounddevice, and numpy.</p>"
            f"<p><b>Version {_ver}</b></p>"
        )

    def _on_howto(self):
        """Show a scrollable How-to-Use guide popup (non-modal — drag it around freely)."""
        if hasattr(self, '_howto_dialog') and self._howto_dialog is not None:
            self._howto_dialog.raise_()
            self._howto_dialog.activateWindow()
            return
        dlg = QDialog(self, Qt.WindowType.Window)
        dlg.setWindowTitle("mlrVp — How to Use")
        dlg.resize(700, 580)
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

    def set_hardware_grid_connected(self, connected: bool):
        """Track whether a hardware grid is connected.
        If no hardware grid, auto-show the virtual grid popout."""
        self._has_hardware_grid = connected
        self._grid_window.set_hardware_connected(connected)
        if not connected:
            self.set_virtual_grid_visible(True)

    def set_grid_dimensions(self, rows: int, columns: int):
        """Update virtual grid, channel view, and auto-resize for the row count."""
        self._grid_window.set_grid_dimensions(rows, columns)
        self._channel_view.set_visible_rows(rows)
        self._auto_resize_for_rows(rows)

    def _auto_resize_for_rows(self, row_count: int):
        """Resize window height to fit the given number of rows + mixer."""
        row_height = 100
        transport_h = 52
        mixer_h = 220
        status_h = 26
        margins = 24
        needed_h = transport_h + (row_count * row_height) + mixer_h + status_h + margins
        needed_h = max(needed_h, 1100)
        # Cap at 2000 to accommodate 16-row (256) grids without scrolling
        target_h = max(min(needed_h, 2000), self.height())
        # Keep current width but ensure we don't go below 1620 (initial default)
        target_w = max(self.width(), 1620)
        self.resize(target_w, target_h)

    def closeEvent(self, event):
        self._grid_window.close()
        self.quit_requested.emit()
        event.accept()
