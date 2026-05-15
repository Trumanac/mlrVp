"""Plugin Browser — dialog for scanning, searching, and selecting VST3/AU plugins."""

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox, QCheckBox,
    QPushButton, QTableWidget, QTableWidgetItem, QLabel,
    QHeaderView, QAbstractItemView, QListWidget, QFileDialog,
    QMessageBox,
)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QColor
from mlrvp.gui.scale import bind_fixed_width, bind_min_size

log = logging.getLogger(__name__)


# ── Scan worker thread ───────────────────────────────────────────────────────

class PluginScanWorker(QThread):
    """Background thread that scans for plugins and emits results progressively."""
    found = Signal(object)       # PluginInfo
    progress = Signal(int)       # total found so far
    finished_scan = Signal(int)  # total found

    def __init__(self, formats=None, extra_paths=None, parent=None):
        super().__init__(parent)
        self._formats = formats
        self._extra_paths = extra_paths

    def run(self):
        from mlrvp.audio.plugin_scanner import scan_plugins, save_cache
        results = scan_plugins(
            formats=self._formats,
            extra_paths=self._extra_paths,
            progress_callback=lambda n: self.progress.emit(n),
        )
        for info in results:
            self.found.emit(info)
        save_cache(results)
        self.finished_scan.emit(len(results))


# ── Dialog ───────────────────────────────────────────────────────────────────

_SS = """
QDialog { background: #1a1a1a; color: #cccccc; }
QLineEdit { background: #2a2a2a; color: #cccccc; border: 1px solid #555;
            border-radius: 3px; padding: 4px; font-size: 12px; }
QComboBox { background: #2a2a2a; color: #cccccc; border: 1px solid #555;
            border-radius: 3px; padding: 2px 6px; }
QComboBox QAbstractItemView { background: #2a2a2a; color: #cccccc; }
QPushButton { background: #333; color: #ccc; border: 1px solid #555;
              border-radius: 3px; padding: 4px 12px; font-weight: bold; }
QPushButton:hover { background: #444; }
QPushButton:pressed { background: #555; }
QTableWidget { background: #222; color: #ccc; gridline-color: #333;
               selection-background-color: #2a5a3a; border: 1px solid #444; }
QHeaderView::section { background: #2a2a2a; color: #aaa; border: none;
                        border-bottom: 1px solid #444; padding: 4px; }
QLabel { color: #999; }
"""


class PluginBrowserDialog(QDialog):
    """Resizable dialog for browsing and selecting audio plugins."""

    plugin_selected = Signal(str, str)   # (path, format)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plugin Browser — Foreign Exchange")
        bind_min_size(self, 600, 400)
        self.resize(720, 500)
        self.setStyleSheet(_SS)

        self._worker: PluginScanWorker | None = None
        self._all_rows: list = []  # list of PluginInfo objects

        self._build_ui()
        self._load_from_cache()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Header row: search + format filter + rescan
        hdr = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search plugins…")
        self._search.textChanged.connect(self._apply_filter)
        hdr.addWidget(self._search, stretch=1)

        self._fmt_filter = QComboBox()
        self._fmt_filter.addItems(["All", "VST3", "AU"])
        self._fmt_filter.currentTextChanged.connect(lambda _: self._apply_filter())
        hdr.addWidget(self._fmt_filter)

        self._fx_only = QCheckBox("Effects Only")
        self._fx_only.setChecked(True)
        self._fx_only.stateChanged.connect(lambda _: self._apply_filter())
        hdr.addWidget(self._fx_only)

        self._rescan_btn = QPushButton("Rescan")
        self._rescan_btn.clicked.connect(self._start_scan)
        hdr.addWidget(self._rescan_btn)

        self._paths_btn = QPushButton("Scan Paths…")
        self._paths_btn.clicked.connect(self._open_scan_paths_editor)
        hdr.addWidget(self._paths_btn)

        self._status = QLabel("")
        hdr.addWidget(self._status)
        root.addLayout(hdr)

        # Plugin table
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Vendor", "Type", "Format", "Path"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._on_double_click)
        root.addWidget(self._table, stretch=1)

        # Bottom row: load button
        bot = QHBoxLayout()
        bot.addStretch()
        self._load_btn = QPushButton("Load")
        bind_fixed_width(self._load_btn, 100)
        self._load_btn.clicked.connect(self._on_load_clicked)
        bot.addWidget(self._load_btn)
        root.addLayout(bot)

    # ── Data loading ─────────────────────────────────────────────────────

    def _load_from_cache(self):
        from mlrvp.audio.plugin_scanner import load_cache
        cached = load_cache()
        if cached:
            self._all_rows = list(cached)
            self._rebuild_table()
            self._status.setText(f"{len(cached)} plugins (cached)")
        else:
            self._status.setText("No cache — click Rescan")

    def _start_scan(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self._all_rows.clear()
        self._table.setRowCount(0)
        self._status.setText("Scanning…")
        self._rescan_btn.setEnabled(False)

        from mlrvp.audio.plugin_scanner import load_custom_scan_paths
        extra = load_custom_scan_paths()
        self._worker = PluginScanWorker(extra_paths=extra if extra else None)
        self._worker.found.connect(self._on_plugin_found)
        self._worker.progress.connect(
            lambda n: self._status.setText(f"Scanning… ({n} found)")
        )
        self._worker.finished_scan.connect(self._on_scan_finished)
        self._worker.start()

    def _on_plugin_found(self, info):
        self._all_rows.append(info)
        if self._matches_filter(info):
            self._append_table_row(info)

    def _on_scan_finished(self, total: int):
        self._update_status()
        self._rescan_btn.setEnabled(True)
        self._worker = None

    # ── Filtering ────────────────────────────────────────────────────────

    def _matches_filter(self, info) -> bool:
        search = self._search.text().strip().lower()
        fmt_f = self._fmt_filter.currentText()
        if fmt_f != "All" and info.fmt.upper() != fmt_f.upper():
            return False
        if search and search not in info.name.lower() and search not in info.vendor.lower():
            return False
        if self._fx_only.isChecked() and info.is_instrument:
            return False
        return True

    def _apply_filter(self, _text=None):
        self._table.setRowCount(0)
        for info in self._all_rows:
            if self._matches_filter(info):
                self._append_table_row(info)
        self._update_status()

    def _rebuild_table(self):
        self._apply_filter()

    @staticmethod
    def _friendly_type(info) -> str:
        """Human-readable type label from PluginInfo category."""
        if info.is_instrument:
            return "Instrument"
        cat = info.category
        if not cat:
            return ""
        parts = [p for p in cat.split("|") if p.lower() not in ("fx",)]
        return " \u00b7 ".join(parts) if parts else "Effect"

    def _append_table_row(self, info):
        sorting = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        row = self._table.rowCount()
        self._table.insertRow(row)

        name_item = QTableWidgetItem(info.name)
        if getattr(info, "qt_conflict", False):
            name_item = QTableWidgetItem("\u26a0 " + info.name)
            warn_color = QColor("#aa6622")
            name_item.setForeground(warn_color)
            name_item.setToolTip(
                "This plugin embeds its own Qt runtime and may crash "
                "when hosted inside this application."
            )
        self._table.setItem(row, 0, name_item)
        self._table.setItem(row, 1, QTableWidgetItem(info.vendor))
        self._table.setItem(row, 2, QTableWidgetItem(self._friendly_type(info)))
        self._table.setItem(row, 3, QTableWidgetItem(info.fmt.upper()))
        self._table.setItem(row, 4, QTableWidgetItem(info.path))
        self._table.setSortingEnabled(sorting)

    def _update_status(self):
        visible = self._table.rowCount()
        total = len(self._all_rows)
        hidden = total - visible
        if hidden > 0:
            self._status.setText(f"{visible} plugins ({hidden} hidden)")
        else:
            self._status.setText(f"{total} plugins")

    # ── Selection ────────────────────────────────────────────────────────

    def _selected_info(self):
        """Return the PluginInfo for the currently selected row, or None."""
        row = self._table.currentRow()
        if row < 0:
            return None
        path_item = self._table.item(row, 4)
        if path_item is None:
            return None
        path = path_item.text()
        for info in self._all_rows:
            if info.path == path:
                return info
        return None

    def _confirm_qt_conflict(self) -> bool:
        """Show a warning if selected plugin has a Qt conflict. Returns True to proceed."""
        info = self._selected_info()
        if info and getattr(info, "qt_conflict", False):
            reply = QMessageBox.warning(
                self,
                "Qt Compatibility Warning",
                f"\"{info.name}\" embeds its own Qt runtime which conflicts "
                "with this application's UI framework.\n\n"
                "Loading this plugin will very likely crash the app "
                "when audio is processed.\n\n"
                "Load anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes
        return True

    def _selected_plugin(self) -> tuple[str, str] | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        path_item = self._table.item(row, 4)
        fmt_item = self._table.item(row, 3)
        if path_item is None or fmt_item is None:
            return None
        return path_item.text(), fmt_item.text().lower()

    def _on_load_clicked(self):
        sel = self._selected_plugin()
        if sel:
            if not self._confirm_qt_conflict():
                return
            self.plugin_selected.emit(sel[0], sel[1])
            self.accept()

    def _on_double_click(self, index):
        sel = self._selected_plugin()
        if sel:
            if not self._confirm_qt_conflict():
                return
            self.plugin_selected.emit(sel[0], sel[1])
            self.accept()

    # ── Scan paths editor ────────────────────────────────────────────────

    def _open_scan_paths_editor(self):
        dlg = _ScanPathsDialog(self)
        dlg.exec()


# ── Scan paths dialog ────────────────────────────────────────────────────────

_SP_SS = """
QDialog { background: #1a1a1a; color: #cccccc; }
QListWidget { background: #222; color: #ccc; border: 1px solid #444; }
QListWidget::item:selected { background: #2a5a3a; }
QPushButton { background: #333; color: #ccc; border: 1px solid #555;
              border-radius: 3px; padding: 4px 12px; font-weight: bold; }
QPushButton:hover { background: #444; }
QLabel { color: #999; }
"""


class _ScanPathsDialog(QDialog):
    """Editor for custom plugin scan directories."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plugin Scan Paths")
        bind_min_size(self, 500, 300)
        self.resize(560, 340)
        self.setStyleSheet(_SP_SS)

        from mlrvp.audio.plugin_scanner import (
            load_custom_scan_paths, get_default_scan_paths,
        )

        layout = QVBoxLayout(self)

        # Default paths (read-only info)
        layout.addWidget(QLabel("Default scan directories (always included):"))
        defaults = get_default_scan_paths("vst3") + get_default_scan_paths("au")
        for dp in defaults:
            lbl = QLabel(f"  {dp}")
            lbl.setStyleSheet("color: #777; font-size: 11px;")
            layout.addWidget(lbl)
        if not defaults:
            lbl = QLabel("  (none found on this platform)")
            lbl.setStyleSheet("color: #555; font-size: 11px;")
            layout.addWidget(lbl)

        layout.addSpacing(8)
        layout.addWidget(QLabel("Custom scan directories:"))

        self._list = QListWidget()
        self._paths = load_custom_scan_paths()
        for p in self._paths:
            self._list.addItem(p)
        layout.addWidget(self._list, stretch=1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Folder…")
        add_btn.clicked.connect(self._add_path)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_path)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Close / save
        close_row = QHBoxLayout()
        close_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_and_close)
        close_row.addWidget(save_btn)
        layout.addLayout(close_row)

    def _add_path(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Plugin Directory")
        if folder:
            if folder not in self._paths:
                self._paths.append(folder)
                self._list.addItem(folder)

    def _remove_path(self):
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)
            del self._paths[row]

    def _save_and_close(self):
        from mlrvp.audio.plugin_scanner import save_custom_scan_paths
        save_custom_scan_paths(self._paths)
        self.accept()
