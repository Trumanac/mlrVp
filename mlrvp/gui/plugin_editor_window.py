"""Plugin Editor Window — standalone window for native editor + parameter controls."""

import hashlib
import logging
import threading
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QTabWidget,
    QLabel, QPushButton, QScrollArea, QGridLayout,
)
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QFont

from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.gui.scale import bind_fixed_width, bind_min_size

log = logging.getLogger(__name__)

_BG = "#292929"
_ACCENT = "#44ddaa"
_WARM = "#f0c060"
_DIM = "#888888"

_SS = f"""
QMainWindow {{ background: {_BG}; }}
QWidget {{ background: {_BG}; color: #cccccc; }}
QTabWidget::pane {{ border: 1px solid #444; }}
QTabBar::tab {{ background: #333; color: #aaa; padding: 6px 16px;
               border: 1px solid #444; border-bottom: none; }}
QTabBar::tab:selected {{ background: {_BG}; color: {_ACCENT}; }}
QPushButton {{ background: #333; color: #ccc; border: 1px solid #555;
              border-radius: 3px; padding: 6px 16px; font-weight: bold; }}
QPushButton:hover {{ background: #444; }}
QLabel {{ background: transparent; }}
QScrollArea {{ border: none; }}
"""


class PluginEditorWindow(QMainWindow):
    """Standalone window for editing a loaded external plugin's parameters."""

    def __init__(self, engine, bus_key: str, slot_idx: int,
                 plugin_name: str = "Plugin", plugin_path: str = "",
                 plugin_format: str = "vst3", parent=None):
        super().__init__(parent)
        self._engine = engine
        self._bus_key = bus_key
        self._slot_idx = slot_idx
        self._plugin_name = plugin_name
        self._plugin_path = plugin_path
        self._plugin_format = plugin_format
        self._param_knobs: dict[str, RotaryKnob] = {}
        self._poll_blocked = False  # suppress poll while user is turning a knob
        self._close_event = threading.Event()  # used to close native editor programmatically

        self.setWindowTitle(f"FOREIGN EXCHANGE — {plugin_name}")
        self.setWindowFlags(Qt.WindowType.Window)
        bind_min_size(self, 400, 300)
        self.setStyleSheet(_SS)

        self._build_ui()
        self._restore_geometry()

        # Poll timer — sync knob values from plugin at 10 Hz
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll_params)
        self._poll_timer.start()

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)

        tabs = QTabWidget()
        root.addWidget(tabs)

        # Tab 1 — Native Editor
        native_tab = QWidget()
        native_layout = QVBoxLayout(native_tab)
        native_layout.setSpacing(12)
        native_layout.setContentsMargins(16, 16, 16, 16)

        info_font = QFont("Consolas", 10)

        for label_text in [
            f"Plugin: {self._plugin_name}",
            f"Format: {self._plugin_format.upper()}",
            f"Path: {self._plugin_path}",
        ]:
            lbl = QLabel(label_text)
            lbl.setFont(info_font)
            lbl.setStyleSheet(f"color: {_DIM};")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            native_layout.addWidget(lbl)

        native_layout.addSpacing(8)

        open_btn = QPushButton("Open Native Editor")
        bind_fixed_width(open_btn, 200)
        open_btn.setStyleSheet(
            f"QPushButton {{ background: #1a3a2a; color: {_ACCENT}; border: 1px solid {_ACCENT}; }}"
            f"QPushButton:hover {{ background: #255040; }}"
        )
        open_btn.clicked.connect(self._open_native_editor)
        native_layout.addWidget(open_btn)

        self._native_status = QLabel(
            "Native editor opens in a separate OS window via your plugin's own GUI."
        )
        self._native_status.setStyleSheet("color: #666; font-style: italic;")
        self._native_status.setWordWrap(True)
        native_layout.addWidget(self._native_status)
        native_layout.addStretch()

        tabs.addTab(native_tab, "NATIVE EDITOR")

        # Tab 2 — Parameters
        param_tab = QWidget()
        param_layout = QVBoxLayout(param_tab)
        param_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self._param_grid = QGridLayout(scroll_widget)
        self._param_grid.setSpacing(8)
        self._param_grid.setContentsMargins(8, 8, 8, 8)
        scroll.setWidget(scroll_widget)
        param_layout.addWidget(scroll)

        tabs.addTab(param_tab, "PARAMETERS")

        # Populate parameter knobs
        self._populate_params()

    def _populate_params(self):
        names = self._engine.get_plugin_slot_param_names(
            self._bus_key, self._slot_idx
        )
        params = self._engine.get_plugin_slot_params(
            self._bus_key, self._slot_idx
        )
        cols = 4
        for i, name in enumerate(names):
            row, col = divmod(i, cols)
            display_name = name[:12]
            knob = RotaryKnob(color=_WARM)
            knob.setToolTip(name)
            value = params.get(name, 0.5)
            knob.set_value(value)

            # Wire knob → plugin param
            param_name = name
            knob.value_changed.connect(
                lambda v, pn=param_name: self._on_knob_changed(pn, v)
            )

            # Knob + label column
            col_layout = QVBoxLayout()
            col_layout.setSpacing(1)
            col_layout.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
            name_lbl = QLabel(display_name)
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl.setStyleSheet("color: #999; font-size: 8px; background: transparent;")
            col_layout.addWidget(name_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
            self._param_grid.addLayout(col_layout, row, col, Qt.AlignmentFlag.AlignCenter)
            self._param_knobs[name] = knob

        if not names:
            lbl = QLabel("No parameters available.")
            lbl.setStyleSheet("color: #666; font-style: italic;")
            self._param_grid.addWidget(lbl, 0, 0)

    def _on_knob_changed(self, param_name: str, value: float):
        self._poll_blocked = True
        self._engine.set_plugin_slot_param(
            self._bus_key, self._slot_idx, param_name, value
        )
        # Unblock poll after a short delay so the user's change settles
        QTimer.singleShot(200, self._unblock_poll)

    def _unblock_poll(self):
        self._poll_blocked = False

    # ── Native editor ────────────────────────────────────────────────────

    def _open_native_editor(self):
        self._native_status.setText("Opening native editor window…")
        # Defer slightly so the status text paints before the blocking call
        QTimer.singleShot(50, self._do_open_native)

    def _do_open_native(self):
        # Reset close event in case it was set from a prior open
        self._close_event.clear()
        # show_editor() BLOCKS but runs a native message loop that keeps
        # Qt responsive on Windows.  It returns when the user closes the
        # editor window OR when _close_event.set() is called.
        ok = self._engine.show_plugin_slot_editor(
            self._bus_key, self._slot_idx, self._close_event,
        )
        if not ok:
            self._native_status.setText(
                "Native editor not supported for this plugin or platform."
            )
        else:
            self._native_status.setText(
                "Native editor closed."
            )

    # ── Param polling ────────────────────────────────────────────────────

    def _poll_params(self):
        if self._poll_blocked:
            return
        params = self._engine.get_plugin_slot_params(
            self._bus_key, self._slot_idx
        )
        for name, knob in self._param_knobs.items():
            val = params.get(name)
            if val is not None:
                knob.blockSignals(True)
                knob.set_value(val)
                knob.blockSignals(False)

    # ── Geometry persistence ─────────────────────────────────────────────

    def _settings_key(self) -> str:
        h = hashlib.md5(self._plugin_path.encode()).hexdigest()[:8]
        return f"PluginEditor_{h}"

    def _restore_geometry(self):
        settings = QSettings("mlrvp", "PluginEditorWindows")
        geo = settings.value(self._settings_key())
        if geo is not None:
            self.restoreGeometry(geo)

    def closeEvent(self, event):
        self._poll_timer.stop()
        # If a native editor window is open, close it via the event
        self._close_event.set()
        settings = QSettings("mlrvp", "PluginEditorWindows")
        settings.setValue(self._settings_key(), self.saveGeometry())
        super().closeEvent(event)
