"""Setup view — audio devices, MIDI, OSC, grid settings in mlrVp."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QGroupBox,
    QGridLayout, QTabWidget, QLineEdit, QFrame, QScrollArea,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Qt, Signal

from mlrvp.config import AppConfig, MAX_ROWS
from mlrvp.gui.styles import ACCENT_BLUE, BG_BLACK, BG_PANEL, SEPARATOR, TEXT_SECONDARY, TEXT_PRIMARY


class AudioSetupPanel(QFrame):
    """Audio device configuration."""

    setting_changed = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        grid = QGridLayout()
        grid.setSpacing(6)

        # Driver type (host API)
        self._driver_combo = QComboBox()
        self._driver_combo.addItem("All Drivers")
        self._driver_combo.currentIndexChanged.connect(self._on_driver_changed)
        self._driver_combo.setToolTip(
            "Select a driver type to filter the device lists\n"
            "(e.g. ASIO, WASAPI, CoreAudio, ALSA)"
        )
        grid.addWidget(QLabel("Driver Type:"), 0, 0)
        grid.addWidget(self._driver_combo, 0, 1)

        # Output device
        self._output_combo = QComboBox()
        self._output_combo.addItem("Default Output")
        self._output_combo.currentIndexChanged.connect(self._on_output_changed)
        self._output_combo.setToolTip("Audio output device used for playback")
        grid.addWidget(QLabel("Output Device:"), 1, 0)
        grid.addWidget(self._output_combo, 1, 1)

        # Input device
        self._input_combo = QComboBox()
        self._input_combo.addItem("Default Input")
        self._input_combo.currentIndexChanged.connect(self._on_input_changed)
        self._input_combo.setToolTip("Audio input device used for recording and monitoring")
        grid.addWidget(QLabel("Input Device:"), 2, 0)
        grid.addWidget(self._input_combo, 2, 1)

        # Sample rate
        self._sr_combo = QComboBox()
        self._sr_combo.addItems(["44100", "48000", "88200", "96000"])
        self._sr_combo.currentTextChanged.connect(
            lambda v: self.setting_changed.emit("sample_rate", int(v))
        )
        self._sr_combo.setToolTip(
            "Sample rate in Hz — affects audio quality and CPU load\n"
            "(requires app restart to take effect)"
        )
        grid.addWidget(QLabel("Sample Rate:"), 3, 0)
        grid.addWidget(self._sr_combo, 3, 1)

        # Buffer size
        self._buf_combo = QComboBox()
        self._buf_combo.addItems(["64", "128", "256", "512", "1024", "2048"])
        self._buf_combo.setCurrentText("256")
        self._buf_combo.currentTextChanged.connect(
            lambda v: self.setting_changed.emit("buffer_size", int(v))
        )
        self._buf_combo.setToolTip(
            "Audio buffer size — lower = less latency, higher = more stable\n"
            "(requires app restart to take effect)"
        )
        grid.addWidget(QLabel("Buffer Size:"), 4, 0)
        grid.addWidget(self._buf_combo, 4, 1)

        layout.addLayout(grid)

        # Rescan button
        self._rescan_btn = QPushButton("Rescan Audio Devices")
        self._rescan_btn.setFixedHeight(28)
        self._rescan_btn.setToolTip("Rescan and refresh the list of available audio devices")
        layout.addWidget(self._rescan_btn)

        layout.addStretch()

        # Internal device data — populated by set_devices()
        self._all_outputs: list[dict] = []   # [{"index":int,"name":str,"host_api":str}, ...]
        self._all_inputs: list[dict] = []
        self._host_apis: list[str] = []      # unique ordered API names

    # ── public API ──────────────────────────────────────────────────── #

    def set_devices(self, output_devs: list[dict], input_devs: list[dict],
                    host_apis: list[str] | None = None):
        """Populate driver type, output and input combos.

        *output_devs* / *input_devs*: list of dicts with at least
        ``{"index": int, "name": str, "host_api": str}``.
        *host_apis*: ordered list of host API names; if *None* they are
        derived from the device lists.
        """
        self._all_outputs = list(output_devs)
        self._all_inputs = list(input_devs)

        # Derive host API list if not provided
        if host_apis is None:
            seen: set[str] = set()
            apis: list[str] = []
            for d in output_devs + input_devs:
                api = d.get("host_api", "")
                if api and api not in seen:
                    seen.add(api)
                    apis.append(api)
            host_apis = apis
        self._host_apis = list(host_apis)

        # Populate driver combo
        self._driver_combo.blockSignals(True)
        prev_driver = self._driver_combo.currentText()
        self._driver_combo.clear()
        self._driver_combo.addItem("All Drivers")
        for api in self._host_apis:
            self._driver_combo.addItem(api)
        # Restore previous selection if it still exists
        idx = self._driver_combo.findText(prev_driver)
        self._driver_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._driver_combo.blockSignals(False)

        self._refresh_device_combos()

    def set_driver(self, api_name: str):
        """Programmatically select a driver type by name."""
        idx = self._driver_combo.findText(api_name)
        if idx < 0:
            idx = 0
        self._driver_combo.blockSignals(True)
        self._driver_combo.setCurrentIndex(idx)
        self._driver_combo.blockSignals(False)
        self._refresh_device_combos()

    def select_output_device(self, device_index: int):
        """Select an output device by its sounddevice index."""
        for i, d in enumerate(self._filtered_outputs()):
            if d["index"] == device_index:
                self._output_combo.blockSignals(True)
                self._output_combo.setCurrentIndex(i + 1)  # +1 for "Default"
                self._output_combo.blockSignals(False)
                return
        # Not found — reset to default
        self._output_combo.blockSignals(True)
        self._output_combo.setCurrentIndex(0)
        self._output_combo.blockSignals(False)

    def select_input_device(self, device_index: int):
        """Select an input device by its sounddevice index."""
        for i, d in enumerate(self._filtered_inputs()):
            if d["index"] == device_index:
                self._input_combo.blockSignals(True)
                self._input_combo.setCurrentIndex(i + 1)
                self._input_combo.blockSignals(False)
                return
        self._input_combo.blockSignals(True)
        self._input_combo.setCurrentIndex(0)
        self._input_combo.blockSignals(False)

    @property
    def rescan_button(self) -> QPushButton:
        return self._rescan_btn

    # ── private helpers ─────────────────────────────────────────────── #

    def _selected_driver(self) -> str:
        """Return the currently selected driver name, or '' for all."""
        idx = self._driver_combo.currentIndex()
        if idx <= 0:
            return ""
        return self._driver_combo.currentText()

    def _filtered_outputs(self) -> list[dict]:
        driver = self._selected_driver()
        if not driver:
            return self._all_outputs
        return [d for d in self._all_outputs if d.get("host_api") == driver]

    def _filtered_inputs(self) -> list[dict]:
        driver = self._selected_driver()
        if not driver:
            return self._all_inputs
        return [d for d in self._all_inputs if d.get("host_api") == driver]

    def _refresh_device_combos(self):
        """Rebuild output/input combos for the selected driver type."""
        out_devs = self._filtered_outputs()
        in_devs = self._filtered_inputs()

        self._output_combo.blockSignals(True)
        self._output_combo.clear()
        self._output_combo.addItem("Default Output")
        for d in out_devs:
            self._output_combo.addItem(d["name"])
        self._output_combo.blockSignals(False)

        self._input_combo.blockSignals(True)
        self._input_combo.clear()
        self._input_combo.addItem("Default Input")
        for d in in_devs:
            self._input_combo.addItem(d["name"])
        self._input_combo.blockSignals(False)

    def _on_driver_changed(self, _idx: int):
        driver = self._selected_driver()
        self.setting_changed.emit("host_api", driver)
        self._refresh_device_combos()
        # Reset device selection to default when driver changes
        self.setting_changed.emit("output_device", -1)
        self.setting_changed.emit("input_device", -1)

    def _on_output_changed(self, combo_idx: int):
        if combo_idx <= 0:
            self.setting_changed.emit("output_device", -1)
        else:
            devs = self._filtered_outputs()
            if combo_idx - 1 < len(devs):
                self.setting_changed.emit("output_device", devs[combo_idx - 1]["index"])

    def _on_input_changed(self, combo_idx: int):
        if combo_idx <= 0:
            self.setting_changed.emit("input_device", -1)
        else:
            devs = self._filtered_inputs()
            if combo_idx - 1 < len(devs):
                self.setting_changed.emit("input_device", devs[combo_idx - 1]["index"])


class GridSetupPanel(QFrame):
    """Grid / controller configuration."""

    setting_changed = Signal(str, object)
    midi_grid_connect_requested = Signal(int, int)  # in_port, out_port
    midi_grid_disconnect_requested = Signal()
    scan_requested = Signal()
    save_default_requested = Signal(str)  # name
    tilt_enabled_changed = Signal(bool)  # tilt checkbox toggled
    load_default_requested = Signal(str)  # name

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        grid = QGridLayout()
        grid.setSpacing(6)

        # Row count
        self._rows_spin = QSpinBox()
        self._rows_spin.setRange(1, MAX_ROWS)
        self._rows_spin.setValue(8)
        self._rows_spin.valueChanged.connect(
            lambda v: self.setting_changed.emit("row_count", v)
        )
        self._rows_spin.setToolTip("Number of active rows on the grid — each row controls one channel")
        grid.addWidget(QLabel("Active Rows:"), 0, 0)
        grid.addWidget(self._rows_spin, 0, 1)

        # Columns
        self._cols_combo = QComboBox()
        self._cols_combo.addItems(["8", "16"])
        self._cols_combo.setCurrentText("8")
        self._cols_combo.currentTextChanged.connect(
            lambda v: self.setting_changed.emit("grid_columns", int(v))
        )
        self._cols_combo.setToolTip("Grid width — 8 or 16 buttons per row")
        grid.addWidget(QLabel("Grid Columns:"), 1, 0)
        grid.addWidget(self._cols_combo, 1, 1)

        # Connected grid device
        self._grid_combo = QComboBox()
        self._grid_combo.addItem("Virtual Grid (built-in)")
        self._grid_combo.setToolTip("Select the hardware grid controller to connect to")
        grid.addWidget(QLabel("Grid Device:"), 2, 0)
        grid.addWidget(self._grid_combo, 2, 1)

        layout.addLayout(grid)

        # Scan + connect buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._scan_btn = QPushButton("Scan for Controllers")
        self._scan_btn.setFixedHeight(28)
        self._scan_btn.clicked.connect(self.scan_requested.emit)
        self._scan_btn.setToolTip("Scan for connected hardware grid controllers (monome / MIDI)")
        btn_row.addWidget(self._scan_btn)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setFixedHeight(28)
        self._connect_btn.clicked.connect(self._on_connect_click)
        self._connect_btn.setToolTip("Connect to the selected hardware grid controller")
        btn_row.addWidget(self._connect_btn)

        layout.addLayout(btn_row)

        # Connection status label
        self._status_label = QLabel("No hardware grid connected")
        self._status_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._status_label)

        # Virtual grid toggle
        self._virtual_chk = QCheckBox("Show Virtual Grid")
        self._virtual_chk.setChecked(False)
        self._virtual_chk.toggled.connect(
            lambda v: self.setting_changed.emit("show_virtual_grid", v)
        )
        self._virtual_chk.setToolTip("Show an on-screen virtual grid for testing without physical hardware")
        layout.addWidget(self._virtual_chk)

        # Snap speed to nearest 0.5x/2x rate
        self._snap_speed_chk = QCheckBox("Snap Speed to Rate (½x / 2x)")
        self._snap_speed_chk.setChecked(False)
        self._snap_speed_chk.setToolTip(
            "When loading a sample, auto-adjust speed to the nearest\n"
            "power-of-two rate (…0.25x, 0.5x, 1.0x, 2.0x, 4.0x…)\n"
            "closest to 1.0x so the loop fits the grid cleanly."
        )
        self._snap_speed_chk.toggled.connect(
            lambda v: self.setting_changed.emit("snap_speed_to_rate", v)
        )
        layout.addWidget(self._snap_speed_chk)

        # Tilt / accelerometer enable
        self._tilt_chk = QCheckBox("Tilt Control Mapping")
        self._tilt_chk.setChecked(False)
        self._tilt_chk.setToolTip(
            "Enable accelerometer/tilt mapping section in the MIDI tab.\n"
            "Auto-enabled when a monome grid is connected."
        )
        self._tilt_chk.toggled.connect(self.tilt_enabled_changed.emit)
        layout.addWidget(self._tilt_chk)

        # ── Default Presets section ──
        sep_def = QFrame()
        sep_def.setFrameShape(QFrame.Shape.HLine)
        sep_def.setStyleSheet("color: #444;")
        layout.addWidget(sep_def)

        def_label = QLabel("DEFAULT STATE")
        def_label.setStyleSheet("font-weight: bold; font-size: 10px; color: #888;")
        layout.addWidget(def_label)

        def_row = QHBoxLayout()
        self._default_combo = QComboBox()
        self._default_combo.setMinimumWidth(120)
        self._default_combo.addItem("(blank)")
        self._refresh_defaults_list()
        def_row.addWidget(self._default_combo)

        self._save_default_btn = QPushButton("Save Default")
        self._save_default_btn.setFixedHeight(24)
        self._save_default_btn.clicked.connect(self._on_save_default)
        self._save_default_btn.setToolTip("Save the current app state as the startup default")
        def_row.addWidget(self._save_default_btn)

        layout.addLayout(def_row)

        def_hint = QLabel("Default state loads on startup. Chose (blank) for empty.")
        def_hint.setStyleSheet("color: #666; font-size: 9px;")
        def_hint.setWordWrap(True)
        layout.addWidget(def_hint)

        layout.addStretch()

        # Internal tracking of scanned devices
        self._scanned_devices: list[dict] = []

    @property
    def scan_button(self) -> QPushButton:
        return self._scan_btn

    def set_grid_devices(self, devices: list[str]):
        self._grid_combo.blockSignals(True)
        self._grid_combo.clear()
        self._grid_combo.addItem("Virtual Grid (built-in)")
        self._grid_combo.addItems(devices)
        self._grid_combo.blockSignals(False)

    def set_scanned_midi_devices(self, devices: list[dict]):
        """Update combo with scanned MIDI grid devices."""
        self._scanned_devices = devices
        self._grid_combo.blockSignals(True)
        self._grid_combo.clear()
        self._grid_combo.addItem("Virtual Grid (built-in)")
        for d in devices:
            self._grid_combo.addItem(f"{d['name']} ({d['model']})")
        self._grid_combo.blockSignals(False)

    def set_tilt_enabled(self, enabled: bool):
        """Programmatically set the tilt checkbox (e.g. on monome connect)."""
        self._tilt_chk.blockSignals(True)
        self._tilt_chk.setChecked(enabled)
        self._tilt_chk.blockSignals(False)
        self.tilt_enabled_changed.emit(enabled)

    def set_connection_status(self, connected: bool, device_name: str = ""):
        if connected:
            self._status_label.setText(f"Connected: {device_name}")
            self._status_label.setStyleSheet("color: #44cc44; font-size: 10px;")
            self._connect_btn.setText("Disconnect")
            # Select the connected device in the combo dropdown
            for i in range(1, self._grid_combo.count()):
                if device_name in self._grid_combo.itemText(i):
                    self._grid_combo.blockSignals(True)
                    self._grid_combo.setCurrentIndex(i)
                    self._grid_combo.blockSignals(False)
                    break
        else:
            self._status_label.setText("No hardware grid connected")
            self._status_label.setStyleSheet("color: #888; font-size: 10px;")
            self._connect_btn.setText("Connect")

    def _on_connect_click(self):
        if self._connect_btn.text() == "Disconnect":
            self.midi_grid_disconnect_requested.emit()
            return
        idx = self._grid_combo.currentIndex()
        if idx <= 0:
            return  # Virtual grid selected, nothing to connect
        device_idx = idx - 1  # offset for "Virtual Grid" entry
        if device_idx < len(self._scanned_devices):
            d = self._scanned_devices[device_idx]
            self.midi_grid_connect_requested.emit(d["in_port"], d["out_port"])

    def _refresh_defaults_list(self):
        """Scan the defaults directory and populate the combo."""
        from mlrvp.config import get_defaults_dir
        self._default_combo.blockSignals(True)
        current = self._default_combo.currentText()
        self._default_combo.clear()
        self._default_combo.addItem("(blank)")
        defaults_dir = get_defaults_dir()
        for f in sorted(defaults_dir.glob("*.json")):
            self._default_combo.addItem(f.stem)
        # Restore previous selection if still present
        idx = self._default_combo.findText(current)
        if idx >= 0:
            self._default_combo.setCurrentIndex(idx)
        self._default_combo.blockSignals(False)

    def _on_save_default(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save Default", "Default name:")
        if ok and name.strip():
            self.save_default_requested.emit(name.strip())
            self._refresh_defaults_list()
            # Select the newly saved default
            idx = self._default_combo.findText(name.strip())
            if idx >= 0:
                self._default_combo.setCurrentIndex(idx)

    def get_selected_default(self) -> str:
        """Return the selected default name, or empty string for blank."""
        text = self._default_combo.currentText()
        return "" if text == "(blank)" else text


class MidiSetupPanel(QFrame):
    """MIDI configuration, learn mode, and active mapping list."""

    setting_changed = Signal(str, object)
    midi_learn_toggled = Signal(bool)
    mapping_range_changed = Signal(str, float, float)   # param_path, min, max
    mapping_removed = Signal(str)                        # param_path
    mapping_toggle_changed = Signal(str, bool)           # param_path, toggle
    tilt_mapping_changed = Signal(int, str)              # axis_index (0=x,1=y,2=z), target param_path
    midi_input_added = Signal(int)                       # port index
    midi_input_removed = Signal(int)                     # port index

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── MIDI Inputs (multiple) ──
        in_label = QLabel("MIDI INPUTS")
        in_label.setStyleSheet("font-weight: bold; font-size: 10px; color: #888;")
        layout.addWidget(in_label)

        self._inputs_container = QVBoxLayout()
        self._inputs_container.setSpacing(4)
        self._midi_input_rows: list[dict] = []  # [{combo, remove_btn, port}]
        self._available_inputs: list[str] = []
        layout.addLayout(self._inputs_container)

        add_input_btn = QPushButton("+ Add MIDI Input")
        add_input_btn.setFixedHeight(24)
        add_input_btn.setStyleSheet(
            "QPushButton { background: #1a2a1a; color: #66aa66; "
            "font-size: 9px; border: 1px solid #336633; }"
            "QPushButton:hover { background: #2a3a2a; }"
        )
        add_input_btn.clicked.connect(self._on_add_input)
        add_input_btn.setToolTip("Add another MIDI input port to receive controller messages from")
        layout.addWidget(add_input_btn)

        # ── MIDI Output (single) ──
        out_grid = QGridLayout()
        out_grid.setSpacing(6)
        self._midi_out_combo = QComboBox()
        self._midi_out_combo.addItem("None")
        self._midi_out_combo.currentIndexChanged.connect(
            lambda v: self.setting_changed.emit("midi_out_device", v - 1)
        )
        self._midi_out_combo.setToolTip("MIDI output device for sending note/CC feedback to external hardware")
        out_grid.addWidget(QLabel("MIDI Output:"), 0, 0)
        out_grid.addWidget(self._midi_out_combo, 0, 1)
        layout.addLayout(out_grid)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        # MIDI Learn
        self._learn_btn = QPushButton("MIDI Learn")
        self._learn_btn.setCheckable(True)
        self._learn_btn.setFixedHeight(28)
        self._learn_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_PANEL}; border: 1px solid #444444;"
            f"border-radius: 0px; color: {TEXT_SECONDARY}; font-weight: bold; }}"
            f"QPushButton:hover {{ background: #2a2a2a; color: {TEXT_PRIMARY}; }}"
            f"QPushButton:checked {{ background: {ACCENT_BLUE}; border-color: {ACCENT_BLUE}; color: {BG_BLACK}; }}"
        )
        self._learn_btn.toggled.connect(self.midi_learn_toggled.emit)
        self._learn_btn.setToolTip(
            "Toggle MIDI Learn mode — move a hardware knob or fader\n"
            "to assign it to the last control you clicked in the app"
        )
        btn_row.addWidget(self._learn_btn)

        # Rescan
        self._rescan_btn = QPushButton("Rescan MIDI Devices")
        self._rescan_btn.setFixedHeight(28)
        self._rescan_btn.setToolTip("Rescan and refresh the list of available MIDI devices")
        btn_row.addWidget(self._rescan_btn)

        layout.addLayout(btn_row)

        # ── Mapping list table ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #444;")
        layout.addWidget(sep)

        map_label = QLabel("ACTIVE MIDI MAPPINGS")
        map_label.setStyleSheet("font-weight: bold; font-size: 10px; color: #888;")
        layout.addWidget(map_label)

        self._map_table = QTableWidget(0, 7)
        self._map_table.setHorizontalHeaderLabels(
            ["Parameter", "CC", "Ch", "Min", "Max", "Toggle", ""]
        )
        self._map_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._map_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._map_table.setAlternatingRowColors(True)
        self._map_table.setStyleSheet(
            "QTableWidget { background: #1a1a1a; color: #ccc; "
            "gridline-color: #333; font-size: 10px; }"
            "QHeaderView::section { background: #222; color: #aaa; "
            "font-size: 9px; padding: 2px; border: 1px solid #333; }"
            "QTableWidget::item:alternate { background: #1e1e1e; }"
        )
        header = self._map_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self._map_table.setColumnWidth(1, 40)
        self._map_table.setColumnWidth(2, 30)
        self._map_table.setColumnWidth(3, 60)
        self._map_table.setColumnWidth(4, 60)
        self._map_table.setColumnWidth(5, 46)
        self._map_table.setColumnWidth(6, 28)
        self._map_table.verticalHeader().setVisible(False)
        self._map_table.setToolTip(
            "Active MIDI CC mappings — each row shows a mapped parameter with its CC number,\n"
            "channel, value range, and toggle mode. Use Min/Max spinboxes to scale the range."
        )
        layout.addWidget(self._map_table, stretch=1)

        # Clear all button
        self._clear_all_btn = QPushButton("Clear All Mappings")
        self._clear_all_btn.setFixedHeight(24)
        self._clear_all_btn.setStyleSheet(
            "QPushButton { background: #3a1a1a; color: #cc4444; "
            "font-size: 9px; border: 1px solid #663333; }"
            "QPushButton:hover { background: #4a2a2a; }"
        )
        self._clear_all_btn.clicked.connect(self._on_clear_all)
        self._clear_all_btn.setToolTip("Remove all active MIDI CC mappings")
        layout.addWidget(self._clear_all_btn)

        # ── Tilt / Accelerometer mapping (hidden by default) ──
        self._tilt_container = QWidget()
        tilt_inner = QVBoxLayout(self._tilt_container)
        tilt_inner.setContentsMargins(0, 0, 0, 0)
        tilt_inner.setSpacing(4)

        tilt_sep = QFrame()
        tilt_sep.setFrameShape(QFrame.Shape.HLine)
        tilt_sep.setStyleSheet("color: #444;")
        tilt_inner.addWidget(tilt_sep)

        tilt_label = QLabel("MONOME TILT / ACCELEROMETER")
        tilt_label.setStyleSheet("font-weight: bold; font-size: 10px; color: #888;")
        tilt_inner.addWidget(tilt_label)

        tilt_grid = QGridLayout()
        tilt_grid.setSpacing(4)
        self._tilt_combos: list[QComboBox] = []
        _TILT_TOOLTIPS = [
            "Tilt X axis — map left/right tilt to a parameter",
            "Tilt Y axis — map forward/back tilt to a parameter",
            "Tilt Z axis — map up/down tilt to a parameter",
        ]
        for i, axis in enumerate(["Tilt X", "Tilt Y", "Tilt Z"]):
            lbl = QLabel(axis + ":")
            lbl.setStyleSheet("color: #aaa; font-size: 10px;")
            combo = QComboBox()
            combo.addItem("None")
            combo.setStyleSheet(
                "QComboBox { font-size: 10px; background: #1a1a1a; color: #ccc; "
                "border: 1px solid #333; }"
            )
            combo.currentIndexChanged.connect(
                lambda idx, ax=i: self._on_tilt_changed(ax, idx))
            combo.setToolTip(_TILT_TOOLTIPS[i])
            tilt_grid.addWidget(lbl, i, 0)
            tilt_grid.addWidget(combo, i, 1)
            self._tilt_combos.append(combo)
        tilt_inner.addLayout(tilt_grid)

        self._tilt_container.setVisible(False)
        layout.addWidget(self._tilt_container)

    def set_midi_devices(self, inputs: list[str], outputs: list[str]):
        self._available_inputs = list(inputs)
        # Update existing input row combos
        for row_info in self._midi_input_rows:
            if row_info is None:
                continue
            combo = row_info["combo"]
            prev = combo.currentIndex()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("None")
            combo.addItems(inputs)
            if 0 <= prev < combo.count():
                combo.setCurrentIndex(prev)
            combo.blockSignals(False)

        self._midi_out_combo.blockSignals(True)
        self._midi_out_combo.clear()
        self._midi_out_combo.addItem("None")
        self._midi_out_combo.addItems(outputs)
        self._midi_out_combo.blockSignals(False)

    def set_tilt_visible(self, visible: bool):
        """Show or hide the tilt mapping section."""
        self._tilt_container.setVisible(visible)

    def _on_add_input(self):
        """Add a new MIDI input selector row."""
        row_layout = QHBoxLayout()
        row_layout.setSpacing(4)

        combo = QComboBox()
        combo.addItem("None")
        combo.addItems(self._available_inputs)
        combo.setMinimumWidth(180)
        row_layout.addWidget(combo, stretch=1)

        remove_btn = QPushButton("✕")
        remove_btn.setFixedSize(24, 22)
        remove_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #cc4444; "
            "font-weight: bold; border: none; font-size: 11px; }"
            "QPushButton:hover { color: #ff6666; }"
        )
        row_layout.addWidget(remove_btn)

        container = QWidget()
        container.setLayout(row_layout)
        self._inputs_container.addWidget(container)

        row_info = {"combo": combo, "remove_btn": remove_btn, "container": container, "port": -1}
        self._midi_input_rows.append(row_info)

        idx = len(self._midi_input_rows) - 1
        combo.currentIndexChanged.connect(
            lambda v, i=idx: self._on_input_combo_changed(i, v))
        remove_btn.clicked.connect(
            lambda checked=False, i=idx: self._on_remove_input(i))

    def _on_input_combo_changed(self, row_idx: int, combo_value: int):
        """User selected a different MIDI input port for this row."""
        port = combo_value - 1  # -1 = None
        if row_idx < len(self._midi_input_rows) and self._midi_input_rows[row_idx] is not None:
            old_port = self._midi_input_rows[row_idx]["port"]
            if old_port >= 0:
                self.midi_input_removed.emit(old_port)
            self._midi_input_rows[row_idx]["port"] = port
            if port >= 0:
                self.midi_input_added.emit(port)

    def _on_remove_input(self, row_idx: int):
        """Remove a MIDI input row."""
        if row_idx < len(self._midi_input_rows) and self._midi_input_rows[row_idx] is not None:
            row_info = self._midi_input_rows[row_idx]
            port = row_info["port"]
            if port >= 0:
                self.midi_input_removed.emit(port)
            row_info["container"].setParent(None)
            row_info["container"].deleteLater()
            self._midi_input_rows[row_idx] = None  # Mark as removed

    @property
    def rescan_button(self) -> QPushButton:
        return self._rescan_btn

    # ── Mapping table management ──

    def refresh_mappings(self, mappings: list[dict]):
        """Rebuild the table from a list of mapping dicts.

        Each dict: {param_path, label, channel, cc, min_val, max_val}
        """
        self._map_table.setRowCount(0)
        for m in mappings:
            self._add_mapping_row(m)

    def add_mapping(self, m: dict):
        """Append a single mapping row."""
        self._add_mapping_row(m)

    def _add_mapping_row(self, m: dict):
        row = self._map_table.rowCount()
        self._map_table.insertRow(row)

        # Parameter label
        param_item = QTableWidgetItem(m.get("label", m["param_path"]))
        param_item.setData(Qt.ItemDataRole.UserRole, m["param_path"])
        self._map_table.setItem(row, 0, param_item)

        # CC number
        cc_item = QTableWidgetItem(str(m["cc"]))
        cc_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._map_table.setItem(row, 1, cc_item)

        # Channel
        ch_item = QTableWidgetItem(str(m["channel"] + 1))
        ch_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._map_table.setItem(row, 2, ch_item)

        # Min spinbox
        min_spin = QDoubleSpinBox()
        min_spin.setRange(-1000.0, 1000.0)
        min_spin.setDecimals(2)
        min_spin.setValue(m.get("min_val", 0.0))
        min_spin.setSingleStep(0.05)
        min_spin.setStyleSheet(
            "QDoubleSpinBox { background: #1a1a1a; color: #ccc; "
            "border: 1px solid #333; font-size: 9px; }"
        )
        path = m["param_path"]
        min_spin.valueChanged.connect(
            lambda v, p=path: self._on_range_changed(p))
        self._map_table.setCellWidget(row, 3, min_spin)

        # Max spinbox
        max_spin = QDoubleSpinBox()
        max_spin.setRange(-1000.0, 1000.0)
        max_spin.setDecimals(2)
        max_spin.setValue(m.get("max_val", 1.0))
        max_spin.setSingleStep(0.05)
        max_spin.setStyleSheet(
            "QDoubleSpinBox { background: #1a1a1a; color: #ccc; "
            "border: 1px solid #333; font-size: 9px; }"
        )
        max_spin.valueChanged.connect(
            lambda v, p=path: self._on_range_changed(p))
        self._map_table.setCellWidget(row, 4, max_spin)

        # Toggle checkbox
        toggle_chk = QCheckBox()
        toggle_chk.setChecked(m.get("toggle", False))
        toggle_chk.setToolTip("Toggle mode (on/off) vs momentary (held)")
        toggle_chk.setStyleSheet(
            "QCheckBox { background: transparent; }"
            "QCheckBox::indicator { width: 14px; height: 14px; }"
        )
        toggle_chk.toggled.connect(
            lambda v, p=path: self.mapping_toggle_changed.emit(p, v))
        self._map_table.setCellWidget(row, 5, toggle_chk)

        # Delete button
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(24, 22)
        del_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #cc4444; "
            "font-weight: bold; border: none; font-size: 11px; }"
            "QPushButton:hover { color: #ff6666; }"
        )
        del_btn.clicked.connect(lambda checked=False, p=path: self._on_delete(p))
        self._map_table.setCellWidget(row, 6, del_btn)

    def _on_range_changed(self, param_path: str):
        """Find the row for this param_path and emit the new range."""
        for r in range(self._map_table.rowCount()):
            item = self._map_table.item(r, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == param_path:
                min_w = self._map_table.cellWidget(r, 3)
                max_w = self._map_table.cellWidget(r, 4)
                if min_w and max_w:
                    self.mapping_range_changed.emit(
                        param_path, min_w.value(), max_w.value())
                break

    def _on_delete(self, param_path: str):
        """Remove a mapping row and emit signal."""
        for r in range(self._map_table.rowCount()):
            item = self._map_table.item(r, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == param_path:
                self._map_table.removeRow(r)
                break
        self.mapping_removed.emit(param_path)

    def _on_clear_all(self):
        """Remove every mapping."""
        while self._map_table.rowCount() > 0:
            item = self._map_table.item(0, 0)
            if item:
                self.mapping_removed.emit(
                    item.data(Qt.ItemDataRole.UserRole))
            self._map_table.removeRow(0)

    def set_tilt_targets(self, targets: list[tuple[str, str]]):
        """Populate tilt combo boxes with available target parameters.

        targets: list of (param_path, label) tuples.
        """
        for combo in self._tilt_combos:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("None", "")
            for path, label in targets:
                combo.addItem(label, path)
            combo.blockSignals(False)

    def _on_tilt_changed(self, axis: int, combo_index: int):
        """User changed tilt axis target assignment."""
        combo = self._tilt_combos[axis]
        target = combo.itemData(combo_index) or ""
        self.tilt_mapping_changed.emit(axis, target)


class OscSetupPanel(QFrame):
    """OSC configuration."""

    setting_changed = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Enable checkbox
        self._enable_cb = QCheckBox("Enable OSC")
        self._enable_cb.setChecked(False)
        self._enable_cb.toggled.connect(self._on_enable_toggled)
        self._enable_cb.setToolTip("Enable OSC communication for external control (e.g. TouchOSC, SuperCollider)")
        layout.addWidget(self._enable_cb)

        grid = QGridLayout()
        grid.setSpacing(6)

        # Host
        self._host_edit = QLineEdit("127.0.0.1")
        self._host_edit.editingFinished.connect(
            lambda: self.setting_changed.emit("osc_host", self._host_edit.text())
        )
        self._host_edit.setToolTip("Target host IP address for outgoing OSC messages")
        grid.addWidget(QLabel("OSC Host:"), 0, 0)
        grid.addWidget(self._host_edit, 0, 1)

        # Send port
        self._send_port = QSpinBox()
        self._send_port.setRange(1024, 65535)
        self._send_port.setValue(8000)
        self._send_port.valueChanged.connect(
            lambda v: self.setting_changed.emit("osc_send_port", v)
        )
        self._send_port.setToolTip("UDP port for sending OSC messages to the host")
        grid.addWidget(QLabel("Send Port:"), 1, 0)
        grid.addWidget(self._send_port, 1, 1)

        # Receive port
        self._recv_port = QSpinBox()
        self._recv_port.setRange(1024, 65535)
        self._recv_port.setValue(8001)
        self._recv_port.valueChanged.connect(
            lambda v: self.setting_changed.emit("osc_recv_port", v)
        )
        self._recv_port.setToolTip("UDP port to listen on for incoming OSC messages")
        grid.addWidget(QLabel("Receive Port:"), 2, 0)
        grid.addWidget(self._recv_port, 2, 1)

        layout.addLayout(grid)
        layout.addStretch()

        # Apply initial enabled state to controls
        self._set_fields_enabled(False)

    def _on_enable_toggled(self, checked: bool):
        self._set_fields_enabled(checked)
        self.setting_changed.emit("osc_enabled", checked)

    def _set_fields_enabled(self, enabled: bool):
        self._host_edit.setEnabled(enabled)
        self._send_port.setEnabled(enabled)
        self._recv_port.setEnabled(enabled)

    def set_enabled_state(self, enabled: bool):
        """Set the checkbox state without emitting (for preset load)."""
        self._enable_cb.blockSignals(True)
        self._enable_cb.setChecked(enabled)
        self._enable_cb.blockSignals(False)
        self._set_fields_enabled(enabled)


class SetupView(QWidget):
    """Tabbed setup panel: Audio, Grid, MIDI, OSC."""

    audio_setting_changed = Signal(str, object)
    grid_setting_changed = Signal(str, object)
    midi_setting_changed = Signal(str, object)
    midi_learn_toggled = Signal(bool)
    midi_mapping_range_changed = Signal(str, float, float)
    midi_mapping_removed = Signal(str)
    midi_mapping_toggle_changed = Signal(str, bool)
    midi_tilt_mapping_changed = Signal(int, str)  # axis, target param_path
    midi_input_added = Signal(int)     # port index
    midi_input_removed = Signal(int)   # port index
    osc_setting_changed = Signal(str, object)
    grid_scan_requested = Signal()
    midi_grid_connect_requested = Signal(int, int)
    midi_grid_disconnect_requested = Signal()
    save_default_requested = Signal(str)
    load_default_requested = Signal(str)
    tilt_enabled_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        self._audio_panel = AudioSetupPanel()
        self._audio_panel.setting_changed.connect(self.audio_setting_changed.emit)
        tabs.addTab(self._audio_panel, "Audio")

        self._grid_panel = GridSetupPanel()
        self._grid_panel.setting_changed.connect(self.grid_setting_changed.emit)
        self._grid_panel.scan_requested.connect(self.grid_scan_requested.emit)
        self._grid_panel.midi_grid_connect_requested.connect(self.midi_grid_connect_requested.emit)
        self._grid_panel.midi_grid_disconnect_requested.connect(self.midi_grid_disconnect_requested.emit)
        self._grid_panel.save_default_requested.connect(self.save_default_requested.emit)
        self._grid_panel.load_default_requested.connect(self.load_default_requested.emit)
        self._grid_panel.tilt_enabled_changed.connect(self.tilt_enabled_changed.emit)
        tabs.addTab(self._grid_panel, "Grid")

        self._midi_panel = MidiSetupPanel()
        self._midi_panel.setting_changed.connect(self.midi_setting_changed.emit)
        self._midi_panel.midi_learn_toggled.connect(self.midi_learn_toggled.emit)
        self._midi_panel.mapping_range_changed.connect(self.midi_mapping_range_changed.emit)
        self._midi_panel.mapping_removed.connect(self.midi_mapping_removed.emit)
        self._midi_panel.mapping_toggle_changed.connect(self.midi_mapping_toggle_changed.emit)
        self._midi_panel.tilt_mapping_changed.connect(self.midi_tilt_mapping_changed.emit)
        self._midi_panel.midi_input_added.connect(self.midi_input_added.emit)
        self._midi_panel.midi_input_removed.connect(self.midi_input_removed.emit)
        tabs.addTab(self._midi_panel, "MIDI")

        self._osc_panel = OscSetupPanel()
        self._osc_panel.setting_changed.connect(self.osc_setting_changed.emit)
        tabs.addTab(self._osc_panel, "OSC")

        layout.addWidget(tabs)

    @property
    def audio_panel(self) -> AudioSetupPanel:
        return self._audio_panel

    @property
    def grid_panel(self) -> GridSetupPanel:
        return self._grid_panel

    @property
    def midi_panel(self) -> MidiSetupPanel:
        return self._midi_panel

    @property
    def osc_panel(self) -> OscSetupPanel:
        return self._osc_panel

    def set_midi_learn_active(self, on: bool):
        """Sync the MIDI Learn button state from outside."""
        self._midi_panel._learn_btn.blockSignals(True)
        self._midi_panel._learn_btn.setChecked(on)
        self._midi_panel._learn_btn.blockSignals(False)
