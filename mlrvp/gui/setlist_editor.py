"""Setlist editor dialog for mlrVp.

Manages setlists (create/rename/delete) and their preset ordering.
Left panel shows the current setlist's preset order (drag to reorder).
Right panel shows the global preset library to add from.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QListWidget, QListWidgetItem,
    QFrame, QInputDialog, QMessageBox, QAbstractItemView
)

from mlrvp.config import NUM_SETLIST_SLOTS
from mlrvp.gui.styles import (
    ACCENT_GOLD, BG_BLACK, BG_PANEL, SEPARATOR, TEXT_SECONDARY, TEXT_PRIMARY
)


class SetlistEditorDialog(QDialog):
    """Modal dialog for managing setlists and their preset order."""

    setlist_changed = Signal()  # emitted when any data changes

    def __init__(self, preset_manager, parent=None):
        super().__init__(parent)
        self._pm = preset_manager
        self.setWindowTitle("Setlist Editor")
        self.setMinimumSize(580, 480)
        self.setStyleSheet(
            f"QDialog {{ background: {BG_PANEL}; color: {TEXT_PRIMARY}; }}"
        )
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Setlist selector row ---
        sl_row = QHBoxLayout()
        sl_row.setSpacing(4)

        sl_label = QLabel("Setlist:")
        sl_label.setStyleSheet(f"font-weight: bold; color: {TEXT_SECONDARY};")
        sl_row.addWidget(sl_label)

        self._sl_combo = QComboBox()
        self._sl_combo.setMinimumWidth(140)
        self._sl_combo.currentTextChanged.connect(self._on_setlist_selected)
        sl_row.addWidget(self._sl_combo, stretch=1)

        self._new_btn = QPushButton("New")
        self._new_btn.setFixedWidth(50)
        self._new_btn.clicked.connect(self._on_new_setlist)
        sl_row.addWidget(self._new_btn)

        self._rename_sl_btn = QPushButton("Rename")
        self._rename_sl_btn.setFixedWidth(60)
        self._rename_sl_btn.clicked.connect(self._on_rename_setlist)
        sl_row.addWidget(self._rename_sl_btn)

        self._del_sl_btn = QPushButton("Delete")
        self._del_sl_btn.setFixedWidth(56)
        self._del_sl_btn.clicked.connect(self._on_delete_setlist)
        sl_row.addWidget(self._del_sl_btn)

        layout.addLayout(sl_row)

        # --- Separator ---
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {SEPARATOR};")
        layout.addWidget(sep)

        # --- Two-panel layout: Setlist (left) | Library (right) ---
        panels = QHBoxLayout()
        panels.setSpacing(8)

        # -- Left panel: current setlist order --
        left = QVBoxLayout()
        left.setSpacing(4)
        left_label = QLabel("Setlist Order (drag to reorder):")
        left_label.setStyleSheet(f"font-weight: bold; color: {TEXT_SECONDARY};")
        left.addWidget(left_label)

        self._setlist_list = QListWidget()
        self._setlist_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._setlist_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._setlist_list.setStyleSheet(
            f"QListWidget {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 12px; }}"
            f"QListWidget::item {{ padding: 4px 8px; }}"
            f"QListWidget::item:selected {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        self._setlist_list.model().rowsMoved.connect(self._on_rows_moved)
        left.addWidget(self._setlist_list, stretch=1)

        left_btns = QHBoxLayout()
        left_btns.setSpacing(4)
        self._remove_btn = QPushButton("Remove")
        self._remove_btn.clicked.connect(self._on_remove_from_setlist)
        left_btns.addWidget(self._remove_btn)
        left_btns.addStretch()
        left.addLayout(left_btns)

        panels.addLayout(left, stretch=1)

        # -- Center: add/remove arrows --
        center = QVBoxLayout()
        center.setSpacing(4)
        center.addStretch()
        self._add_btn = QPushButton("\u25C0 Add")
        self._add_btn.setFixedWidth(60)
        self._add_btn.setToolTip("Add selected library preset to setlist")
        self._add_btn.clicked.connect(self._on_add_to_setlist)
        center.addWidget(self._add_btn)
        center.addStretch()
        panels.addLayout(center)

        # -- Right panel: global preset library --
        right = QVBoxLayout()
        right.setSpacing(4)
        right_label = QLabel("Preset Library:")
        right_label.setStyleSheet(f"font-weight: bold; color: {TEXT_SECONDARY};")
        right.addWidget(right_label)

        self._library_list = QListWidget()
        self._library_list.setStyleSheet(
            f"QListWidget {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 12px; }}"
            f"QListWidget::item {{ padding: 4px 8px; }}"
            f"QListWidget::item:selected {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        self._library_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        right.addWidget(self._library_list, stretch=1)

        lib_btns = QHBoxLayout()
        lib_btns.setSpacing(4)
        self._rename_preset_btn = QPushButton("Rename")
        self._rename_preset_btn.clicked.connect(self._on_rename_preset)
        lib_btns.addWidget(self._rename_preset_btn)
        self._del_preset_btn = QPushButton("Delete")
        self._del_preset_btn.clicked.connect(self._on_delete_preset)
        lib_btns.addWidget(self._del_preset_btn)
        lib_btns.addStretch()
        right.addLayout(lib_btns)

        panels.addLayout(right, stretch=1)
        layout.addLayout(panels, stretch=1)

        # --- Bottom buttons ---
        bot_row = QHBoxLayout()
        bot_row.addStretch()

        self._save_btn = QPushButton("Save Setlist")
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; "
            f"font-weight: bold; padding: 6px 16px; border: none; }}"
            f"QPushButton:hover {{ background: #e6c85e; }}"
        )
        self._save_btn.clicked.connect(self._on_save)
        bot_row.addWidget(self._save_btn)

        self._close_btn = QPushButton("Close")
        self._close_btn.setFixedWidth(60)
        self._close_btn.clicked.connect(self.accept)
        bot_row.addWidget(self._close_btn)

        layout.addLayout(bot_row)

    # ------------------------------------------------------------------ #
    #  Refresh                                                            #
    # ------------------------------------------------------------------ #

    def _refresh(self):
        """Reload setlist combo and both list panels."""
        self._sl_combo.blockSignals(True)
        self._sl_combo.clear()
        for name in self._pm.list_setlists():
            self._sl_combo.addItem(name)
        idx = self._sl_combo.findText(self._pm.setlist_name)
        if idx >= 0:
            self._sl_combo.setCurrentIndex(idx)
        self._sl_combo.blockSignals(False)
        self._refresh_setlist_panel()
        self._refresh_library_panel()

    def _refresh_setlist_panel(self):
        """Refresh the left panel (setlist order)."""
        self._setlist_list.clear()
        for i, name in enumerate(self._pm.setlist_presets):
            exists = self._pm.preset_exists(name)
            marker = "" if exists else " \u26A0"
            item = QListWidgetItem(f"{i+1}. {name}{marker}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._setlist_list.addItem(item)

    def _refresh_library_panel(self):
        """Refresh the right panel (global preset library)."""
        self._library_list.clear()
        for name in self._pm.list_presets():
            self._library_list.addItem(name)

    # ------------------------------------------------------------------ #
    #  Setlist CRUD                                                       #
    # ------------------------------------------------------------------ #

    def _on_setlist_selected(self, name: str):
        if name:
            self._pm.load_setlist(name)
            self._refresh_setlist_panel()

    def _on_new_setlist(self):
        name, ok = QInputDialog.getText(self, "New Setlist", "Setlist name:")
        if ok and name.strip():
            self._pm.create_setlist(name.strip())
            self._pm.load_setlist(name.strip())
            self._refresh()
            self.setlist_changed.emit()

    def _on_rename_setlist(self):
        old = self._sl_combo.currentText()
        name, ok = QInputDialog.getText(self, "Rename Setlist", "New name:", text=old)
        if ok and name.strip() and name.strip() != old:
            self._pm.rename_setlist(old, name.strip())
            self._refresh()
            self.setlist_changed.emit()

    def _on_delete_setlist(self):
        name = self._sl_combo.currentText()
        if name == "Default":
            QMessageBox.warning(self, "Cannot Delete",
                                "The Default setlist cannot be deleted.")
            return
        reply = QMessageBox.question(
            self, "Delete Setlist",
            f"Delete setlist '{name}'?\n(Presets in the library are not deleted.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._pm.delete_setlist(name)
            self._refresh()
            self.setlist_changed.emit()

    # ------------------------------------------------------------------ #
    #  Setlist preset management                                          #
    # ------------------------------------------------------------------ #

    def _on_add_to_setlist(self):
        """Add the selected library preset to the setlist."""
        item = self._library_list.currentItem()
        if not item:
            return
        if len(self._pm.setlist_presets) >= NUM_SETLIST_SLOTS:
            QMessageBox.warning(self, "Limit", "Maximum 32 presets per setlist.")
            return
        self._pm.add_to_setlist(item.text())
        self._refresh_setlist_panel()
        self.setlist_changed.emit()

    def _on_remove_from_setlist(self):
        """Remove the selected preset from the setlist (not from library)."""
        row = self._setlist_list.currentRow()
        if row < 0 or row >= len(self._pm.setlist_presets):
            return
        self._pm.remove_from_setlist(row)
        self._refresh_setlist_panel()
        self.setlist_changed.emit()

    def _on_rows_moved(self):
        """Handle drag-drop reorder in setlist panel."""
        new_order = []
        for i in range(self._setlist_list.count()):
            item = self._setlist_list.item(i)
            old_idx = item.data(Qt.ItemDataRole.UserRole)
            new_order.append(old_idx)
        if new_order:
            self._pm.reorder_setlist(new_order)
            self._refresh_setlist_panel()
            self.setlist_changed.emit()

    # ------------------------------------------------------------------ #
    #  Preset library management                                          #
    # ------------------------------------------------------------------ #

    def _on_rename_preset(self):
        """Rename a preset in the global library."""
        item = self._library_list.currentItem()
        if not item:
            return
        old = item.text()
        name, ok = QInputDialog.getText(self, "Rename Preset", "New name:", text=old)
        if ok and name.strip() and name.strip() != old:
            if self._pm.rename_preset(old, name.strip()):
                self._refresh()
                self.setlist_changed.emit()

    def _on_delete_preset(self):
        """Delete a preset from the global library."""
        item = self._library_list.currentItem()
        if not item:
            return
        name = item.text()
        reply = QMessageBox.question(
            self, "Delete Preset",
            f"Delete preset '{name}' from the library?\n"
            f"It will also be removed from any setlists that reference it.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._pm.delete_preset(name)
            # Remove from current setlist too
            self._pm.setlist_presets = [
                p for p in self._pm.setlist_presets if p != name
            ]
            self._pm.save_setlist()
            self._refresh()
            self.setlist_changed.emit()

    def _on_save(self):
        self._pm.save_setlist()
        self.setlist_changed.emit()
