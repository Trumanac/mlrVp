"""Setup view — audio devices, MIDI, OSC, grid settings in mlrVp."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QGroupBox,
    QGridLayout, QTabWidget, QLineEdit, QFrame, QScrollArea,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QMenu, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QPalette, QAction

from mlrvp.config import (
    AppConfig,
    MAX_ROWS,
    MAX_COLUMNS,
    NUM_GROUPS,
    MONOME_BACKEND_AUTO,
    MONOME_BACKEND_SERIALOSC,
    MONOME_BACKEND_40H_DIRECT,
    MONOME_BACKEND_LEGACY_OSC,
    CR_SLOT_EMPTY,
    CR_SLOT_GROUP_STOP,
    CR_SLOT_PATTERN,
    CR_SLOT_ALL_STOP,
    CR_SLOT_GRID_MODE,
    CR_SLOT_PRESET_PREV,
    CR_SLOT_PRESET_NEXT,
    CR_PRESETS,
)
from mlrvp.gui.styles import ACCENT_BLUE, BG_BLACK, BG_PANEL, SEPARATOR, TEXT_SECONDARY, TEXT_PRIMARY
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size, bind_fixed_width, bind_min_width


class AudioSetupPanel(QFrame):
    """Audio device configuration."""

    setting_changed = Signal(str, object)
    link_toggled = Signal(bool)  # Ableton Link enable/disable

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

        # Multi-channel output
        self._mc_cb = QCheckBox("Multi-channel output (DAW stem recording)")
        self._mc_cb.setChecked(False)
        self._mc_cb.setToolTip(
            "Route each group to a separate stereo pair on the output device so\n"
            "a DAW (Ableton, Reaper…) can record groups as individual tracks.\n\n"
            "Requires a multi-channel output device or a free virtual router:\n"
            "  • Voicemeeter (free, Windows) — use as ASIO output device\n"
            "  • VB-Cable A+B / Banana / Potato (Windows)\n"
            "  • BlackHole (macOS, free)\n\n"
            "Channel layout (applies after restart):\n"
            "  1-2   Master (post-FX)\n"
            "  3-4   Group 1 (pre-FX, post-fader)\n"
            "  5-6   Group 2\n"
            "  …     (one stereo pair per active group)"
        )
        self._mc_cb.toggled.connect(
            lambda v: self.setting_changed.emit("multichannel_output", v)
        )
        layout.addWidget(self._mc_cb)

        # Rescan button
        self._rescan_btn = QPushButton("Rescan Audio Devices")
        bind_fixed_height(self._rescan_btn, 28)
        self._rescan_btn.setToolTip("Rescan and refresh the list of available audio devices")
        layout.addWidget(self._rescan_btn)

        # Ableton Link
        link_row = QWidget()
        link_hl = QHBoxLayout(link_row)
        link_hl.setContentsMargins(0, 4, 0, 0)
        link_hl.setSpacing(8)
        _link_lbl = QLabel("Ableton Link:")
        _link_lbl.setStyleSheet(f"color: {TEXT_PRIMARY};")
        link_hl.addWidget(_link_lbl)
        self._link_btn = QPushButton("—")
        self._link_btn.setCheckable(True)
        bind_fixed_size(self._link_btn, 60, 26)
        self._link_btn.setToolTip(
            "Ableton Link — sync tempo with Ableton Live and other Link-enabled apps\n"
            "on the same network.  Just click — no setup required."
        )
        self._link_btn.setStyleSheet(self._link_btn_style(False, 0))
        self._link_btn.toggled.connect(self._on_link_toggled)
        link_hl.addWidget(self._link_btn)
        link_hl.addStretch()
        layout.addWidget(link_row)

        layout.addStretch()

        # Internal device data — populated by set_devices()
        self._all_outputs: list[dict] = []   # [{"index":int,"name":str,"host_api":str}, ...]
        self._all_inputs: list[dict] = []
        self._host_apis: list[str] = []      # unique ordered API names

    # ── public API ──────────────────────────────────────────────────── #

    def _link_btn_style(self, enabled: bool, num_peers: int) -> str:
        if not enabled:
            return (
                f"QPushButton {{ font-size: 11px; font-weight: bold; color: {TEXT_SECONDARY}; "
                f"background: {BG_BLACK}; border: 1px solid {SEPARATOR}; padding: 0; }}"
                f"QPushButton:hover {{ border-color: #4A9EFF; color: #4A9EFF; }}"
                f"QPushButton:checked {{ background: {BG_BLACK}; }}"
            )
        if num_peers > 0:
            return (
                f"QPushButton {{ font-size: 11px; font-weight: bold; color: #0A0A0A; "
                f"background: #4A9EFF; border: 1px solid #4A9EFF; padding: 0; }}"
                f"QPushButton:checked {{ background: #4A9EFF; }}"
            )
        return (
            f"QPushButton {{ font-size: 11px; font-weight: bold; color: #4A9EFF; "
            f"background: {BG_BLACK}; border: 1px solid #4A9EFF; padding: 0; }}"
            f"QPushButton:checked {{ background: {BG_BLACK}; }}"
        )

    def _on_link_toggled(self, checked: bool):
        self._link_btn.setStyleSheet(self._link_btn_style(checked, 0))
        self.link_toggled.emit(checked)

    def set_link_state(self, enabled: bool, num_peers: int, available: bool = True):
        """Update the Link button appearance without triggering signals."""
        self._link_btn.blockSignals(True)
        self._link_btn.setChecked(enabled)
        if not available:
            self._link_btn.setEnabled(False)
            self._link_btn.setToolTip(
                "Ableton Link requires the aalink package.\n"
                "Install it with: pip install aalink"
            )
        else:
            self._link_btn.setEnabled(True)
        if enabled:
            self._link_btn.setText(str(num_peers) if num_peers > 0 else "·")
        else:
            self._link_btn.setText("—")
        self._link_btn.setStyleSheet(self._link_btn_style(enabled, num_peers))
        self._link_btn.blockSignals(False)

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

    def set_multichannel_output_state(self, enabled: bool):
        """Sync the multi-channel output checkbox without firing the signal."""
        self._mc_cb.blockSignals(True)
        self._mc_cb.setChecked(enabled)
        self._mc_cb.blockSignals(False)

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
    control_row_enabled_changed = Signal(bool)
    control_row_setting_changed = Signal(str, object)  # key, value

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

        # Active groups
        self._active_groups_combo = QComboBox()
        _valid_group_counts = [c for c in (4, 6, 8) if c <= NUM_GROUPS]
        for c in _valid_group_counts:
            self._active_groups_combo.addItem(str(c), c)
        self._active_groups_combo.setCurrentText(str(NUM_GROUPS))
        self._active_groups_combo.currentIndexChanged.connect(
            lambda _: self.setting_changed.emit(
                "active_groups",
                self._active_groups_combo.currentData()
            )
        )
        self._active_groups_combo.setToolTip(
            "Number of audio groups available.\n"
            "Lower values simplify the mixer and reduce memory.\n"
            "Minimum: 4.  Channels assigned to hidden groups still route correctly."
        )
        grid.addWidget(QLabel("Active Groups:"), 2, 0)
        grid.addWidget(self._active_groups_combo, 2, 1)

        # Monome backend
        self._backend_combo = QComboBox()
        self._backend_combo.addItem("Auto", MONOME_BACKEND_AUTO)
        self._backend_combo.addItem("serialosc", MONOME_BACKEND_SERIALOSC)
        self._backend_combo.addItem("40h direct", MONOME_BACKEND_40H_DIRECT)
        self._backend_combo.addItem("Legacy OSC", MONOME_BACKEND_LEGACY_OSC)
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        self._backend_combo.setToolTip(
            "Choose how mlrVp talks to monome hardware.\n"
            "Auto uses serialosc first and keeps the legacy backends available\n"
            "when you need them explicitly."
        )
        grid.addWidget(QLabel("Monome Backend:"), 3, 0)
        grid.addWidget(self._backend_combo, 3, 1)

        # Connected grid device
        self._grid_combo = QComboBox()
        self._grid_combo.addItem("Virtual Grid (built-in)")
        self._grid_combo.setToolTip("Select the hardware grid controller to connect to")
        grid.addWidget(QLabel("Grid Device:"), 4, 0)
        grid.addWidget(self._grid_combo, 4, 1)

        layout.addLayout(grid)

        # ── Grid size quick-presets ───────────────────────────────────────
        preset_label = QLabel("Quick Size:")
        preset_label.setStyleSheet("color: #aaa; font-size: 10px;")
        layout.addWidget(preset_label)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(4)
        preset_row.setContentsMargins(0, 0, 0, 0)

        _grid_presets = [
            ("8×8",   8,  8),
            ("8×16",  8, 16),
            ("16×8",  16,  8),
            ("16×16", 16, 16),
        ]
        self._grid_preset_btns: list[QPushButton] = []
        for label, rows, cols in _grid_presets:
            btn = QPushButton(label)
            bind_fixed_height(btn, 26)
            btn.setCheckable(True)
            btn.setToolTip(f"Set grid to {rows} rows × {cols} columns")
            btn.setStyleSheet(
                "QPushButton { background: #1a1a1a; color: #888; border: 1px solid #333;"
                "  font-size: 9px; padding: 0 4px; }"
                "QPushButton:checked { background: #2a4a2a; color: #aef; border: 1px solid #4af; }"
                "QPushButton:hover { background: #222; color: #ccc; }"
            )
            btn.clicked.connect(lambda _checked, r=rows, c=cols: self._on_grid_preset(r, c))
            preset_row.addWidget(btn)
            self._grid_preset_btns.append(btn)

        # Mark 8×8 as default
        self._grid_preset_btns[0].setChecked(True)

        layout.addLayout(preset_row)

        # Scan + connect buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._scan_btn = QPushButton("Scan for Controllers")
        bind_fixed_height(self._scan_btn, 28)
        self._scan_btn.clicked.connect(self.scan_requested.emit)
        self._scan_btn.setToolTip("Scan for connected hardware grid controllers (monome / MIDI)")
        btn_row.addWidget(self._scan_btn)

        self._connect_btn = QPushButton("Connect")
        bind_fixed_height(self._connect_btn, 28)
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

        # Control row enable
        self._control_row_chk = QCheckBox("Control Row")
        self._control_row_chk.setChecked(True)
        self._control_row_chk.setToolTip(
            "Reserve a row of hardware buttons for group stop and pattern triggers.\n"
            "Monome: configurable row (default: top).  Launchpad: CC row (hardware-fixed).\n"
            "When unchecked the physical row becomes a full data row — MIDI mappings\n"
            "for group stops and patterns still work from any MIDI controller."
        )
        self._control_row_chk.toggled.connect(self._on_control_row_toggled)
        layout.addWidget(self._control_row_chk)

        # ── Control row sub-settings (hidden when unchecked) ──
        self._control_row_container = QWidget()
        cr_layout = QVBoxLayout(self._control_row_container)
        cr_layout.setContentsMargins(16, 2, 0, 2)
        cr_layout.setSpacing(6)

        _cr_lbl_ss = "color: #aaa; font-size: 10px;"
        _cr_spin_ss = (
            "QSpinBox { background: #1a1a1a; color: #ccc; "
            "border: 1px solid #333; font-size: 9px; }"
        )

        # Control Row selector
        monome_row_row_widget = QWidget()
        monome_row_row_layout = QHBoxLayout(monome_row_row_widget)
        monome_row_row_layout.setContentsMargins(0, 0, 0, 0)
        monome_row_row_layout.setSpacing(4)
        monome_row_lbl = QLabel("Control Row:")
        monome_row_lbl.setStyleSheet(_cr_lbl_ss)
        self._monome_row_combo = QComboBox()
        self._monome_row_combo.addItem("Top (row 0)", 0)
        self._monome_row_combo.addItem("Bottom (last row)", -1)
        self._monome_row_combo.addItem("Custom", -99)
        self._monome_row_combo.setStyleSheet(
            "QComboBox { font-size: 10px; background: #1a1a1a; color: #ccc; "
            "border: 1px solid #333; }"
        )
        self._monome_row_combo.currentIndexChanged.connect(self._on_monome_row_combo_changed)
        self._monome_row_spin = QSpinBox()
        self._monome_row_spin.setRange(0, 15)
        self._monome_row_spin.setValue(0)
        bind_fixed_width(self._monome_row_spin, 52)
        self._monome_row_spin.setStyleSheet(_cr_spin_ss)
        self._monome_row_spin.setVisible(False)
        self._monome_row_spin.valueChanged.connect(
            lambda v: self.control_row_setting_changed.emit("monome_row", v)
        )
        monome_row_row_layout.addWidget(monome_row_lbl)
        monome_row_row_layout.addWidget(self._monome_row_combo)
        monome_row_row_layout.addWidget(self._monome_row_spin)
        monome_row_row_layout.addStretch()
        cr_layout.addWidget(monome_row_row_widget)

        # Preset selector row
        preset_row_widget = QWidget()
        preset_row_layout = QHBoxLayout(preset_row_widget)
        preset_row_layout.setContentsMargins(0, 0, 0, 0)
        preset_row_layout.setSpacing(4)
        preset_lbl = QLabel("Preset:")
        preset_lbl.setStyleSheet(_cr_lbl_ss)
        self._cr_preset_combo = QComboBox()
        for preset_name, _ in CR_PRESETS:
            self._cr_preset_combo.addItem(preset_name)
        self._cr_preset_combo.addItem("Custom")
        self._cr_preset_combo.setStyleSheet(
            "QComboBox { font-size: 10px; background: #1a1a1a; color: #ccc; "
            "border: 1px solid #333; min-width: 180px; }"
        )
        self._cr_preset_combo.currentIndexChanged.connect(self._on_cr_preset_changed)
        preset_row_layout.addWidget(preset_lbl)
        preset_row_layout.addWidget(self._cr_preset_combo)
        preset_row_layout.addStretch()
        cr_layout.addWidget(preset_row_widget)

        # Visual 8-button strip
        strip_lbl = QLabel("Button layout  (click to reassign):")
        strip_lbl.setStyleSheet(_cr_lbl_ss)
        cr_layout.addWidget(strip_lbl)

        strip_widget = QWidget()
        strip_layout = QHBoxLayout(strip_widget)
        strip_layout.setContentsMargins(0, 2, 0, 2)
        strip_layout.setSpacing(3)
        self._cr_buttons: list[QPushButton] = []
        for i in range(8):
            btn = QPushButton()
            bind_fixed_size(btn, 36, 36)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setToolTip(f"Button {i+1}: click to reassign")
            btn.clicked.connect(lambda checked=False, idx=i: self._on_cr_button_clicked(idx))
            self._cr_buttons.append(btn)
            strip_layout.addWidget(btn)
        strip_layout.addStretch()
        cr_layout.addWidget(strip_widget)

        # Default assignments (4 stops + 4 patterns)
        from mlrvp.config import _default_cr_assignments
        self._cr_assignments: list[dict] = _default_cr_assignments()
        self._refresh_cr_button_visuals()

        layout.addWidget(self._control_row_container)

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
        bind_min_width(self._default_combo, 120)
        self._default_combo.addItem("(blank)")
        self._refresh_defaults_list()
        def_row.addWidget(self._default_combo)

        self._save_default_btn = QPushButton("Save Default")
        bind_fixed_height(self._save_default_btn, 24)
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

    def set_grid_settings(
        self,
        row_count: int,
        grid_columns: int,
        show_virtual_grid: bool,
        snap_speed_to_rate: bool,
        monome_backend: str,
    ):
        self._rows_spin.blockSignals(True)
        self._rows_spin.setValue(row_count)
        self._rows_spin.blockSignals(False)

        self._cols_combo.blockSignals(True)
        self._cols_combo.setCurrentText(str(grid_columns))
        self._cols_combo.blockSignals(False)

        self._virtual_chk.blockSignals(True)
        self._virtual_chk.setChecked(bool(show_virtual_grid))
        self._virtual_chk.blockSignals(False)

        self._snap_speed_chk.blockSignals(True)
        self._snap_speed_chk.setChecked(bool(snap_speed_to_rate))
        self._snap_speed_chk.blockSignals(False)

        self._backend_combo.blockSignals(True)
        idx = self._backend_combo.findData(monome_backend)
        if idx < 0:
            idx = self._backend_combo.findData(MONOME_BACKEND_AUTO)
        self._backend_combo.setCurrentIndex(max(0, idx))
        self._backend_combo.blockSignals(False)

        self._sync_preset_btns(row_count, grid_columns)

    def set_tilt_enabled(self, enabled: bool):
        """Programmatically set the tilt checkbox (e.g. on monome connect)."""
        self._tilt_chk.blockSignals(True)
        self._tilt_chk.setChecked(enabled)
        self._tilt_chk.blockSignals(False)
        self.tilt_enabled_changed.emit(enabled)

    def set_control_row_config(
        self,
        enabled: bool,
        monome_row: int,
        assignments: list,
    ):
        """Programmatically restore control row settings (e.g. on preset load)."""
        self._control_row_chk.blockSignals(True)
        self._control_row_chk.setChecked(enabled)
        self._control_row_chk.blockSignals(False)
        self._control_row_container.setVisible(enabled)

        # Restore monome_row combo
        self._monome_row_combo.blockSignals(True)
        if monome_row == -2:   # Launchpad CC top row
            idx = self._monome_row_combo.findData(-2)
            if idx >= 0:
                self._monome_row_combo.setCurrentIndex(idx)
            else:
                self._monome_row_combo.setCurrentIndex(0)  # fallback: Top
            self._monome_row_spin.setVisible(False)
        elif monome_row == 0:
            self._monome_row_combo.setCurrentIndex(
                max(0, self._monome_row_combo.findData(0))
            )
            self._monome_row_spin.setVisible(False)
        elif monome_row == -1:
            self._monome_row_combo.setCurrentIndex(
                max(0, self._monome_row_combo.findData(-1))
            )
            self._monome_row_spin.setVisible(False)
        else:
            idx = self._monome_row_combo.findData(-99)
            self._monome_row_combo.setCurrentIndex(max(0, idx))
            self._monome_row_spin.setValue(monome_row)
            self._monome_row_spin.setVisible(True)
        self._monome_row_combo.blockSignals(False)

        # Restore assignments
        self._cr_assignments = list(assignments) if assignments else []
        while len(self._cr_assignments) < 8:
            self._cr_assignments.append({"type": CR_SLOT_EMPTY, "value": 0})
        self._cr_assignments = self._cr_assignments[:8]
        self._refresh_cr_button_visuals()
        self._sync_cr_preset_combo()

    def set_launchpad_connected(self, connected: bool):
        """Add or remove the 'Launchpad (CC top row)' option in the control row combo.

        When *connected* is True the LP option is inserted at position 0 and
        auto-selected so the default Launchpad behaviour (CC top row) is active.
        When *connected* is False the LP option is removed; if it was selected
        the combo falls back to 'Top (row 0)'.
        """
        from mlrvp.config import CR_MONOME_ROW_LAUNCHPAD_CC
        lp_item_data = CR_MONOME_ROW_LAUNCHPAD_CC  # -2
        existing_idx = self._monome_row_combo.findData(lp_item_data)

        if connected:
            if existing_idx < 0:  # not yet present
                self._monome_row_combo.blockSignals(True)
                self._monome_row_combo.insertItem(0, "Launchpad (CC top row)", lp_item_data)
                self._monome_row_combo.blockSignals(False)
            # Auto-select LP item (fires _on_monome_row_combo_changed → emits signal)
            lp_idx = self._monome_row_combo.findData(lp_item_data)
            if self._monome_row_combo.currentIndex() != lp_idx:
                self._monome_row_combo.setCurrentIndex(lp_idx)
        else:
            if existing_idx >= 0:
                was_selected = (self._monome_row_combo.currentIndex() == existing_idx)
                self._monome_row_combo.blockSignals(True)
                self._monome_row_combo.removeItem(existing_idx)
                if was_selected:
                    # Fall back to Top (row 0)
                    self._monome_row_combo.setCurrentIndex(
                        max(0, self._monome_row_combo.findData(0))
                    )
                self._monome_row_combo.blockSignals(False)
                if was_selected:
                    # Emit so app knows monome_row reset to 0
                    self.control_row_setting_changed.emit("monome_row", 0)

    def _on_control_row_toggled(self, enabled: bool):
        self._control_row_container.setVisible(enabled)
        self.control_row_enabled_changed.emit(enabled)

    def _on_monome_row_combo_changed(self, index: int):
        val = self._monome_row_combo.itemData(index)
        if val == -99:  # Custom
            self._monome_row_spin.setVisible(True)
            self.control_row_setting_changed.emit("monome_row", self._monome_row_spin.value())
        else:
            self._monome_row_spin.setVisible(False)
            self.control_row_setting_changed.emit("monome_row", val)

    # ── Visual 8-button strip helpers ──────────────────────────────────────────

    # Color scheme per slot type
    _CR_BTN_STYLES = {
        CR_SLOT_GROUP_STOP: (
            "background: #cc7700; color: #fff; border: 1px solid #ff9900; "
            "font-size: 8px; font-weight: bold;"
        ),
        CR_SLOT_PATTERN: (
            "background: #005fcc; color: #fff; border: 1px solid #3399ff; "
            "font-size: 8px; font-weight: bold;"
        ),
        CR_SLOT_ALL_STOP: (
            "background: #aa1111; color: #fff; border: 1px solid #ff3333; "
            "font-size: 8px; font-weight: bold;"
        ),
        CR_SLOT_GRID_MODE: (
            "background: #006633; color: #fff; border: 1px solid #00aa55; "
            "font-size: 8px; font-weight: bold;"
        ),
        CR_SLOT_PRESET_PREV: (
            "background: #4a0080; color: #fff; border: 1px solid #9933ff; "
            "font-size: 8px; font-weight: bold;"
        ),
        CR_SLOT_PRESET_NEXT: (
            "background: #005555; color: #fff; border: 1px solid #00cccc; "
            "font-size: 8px; font-weight: bold;"
        ),
        CR_SLOT_EMPTY: (
            "background: #222; color: #555; border: 1px solid #333; "
            "font-size: 8px;"
        ),
    }

    def _slot_label(self, slot: dict) -> str:
        t = slot.get("type", CR_SLOT_EMPTY)
        v = slot.get("value", 0)
        if t == CR_SLOT_GROUP_STOP:
            return f"GS{v+1}"
        if t == CR_SLOT_PATTERN:
            return f"P{v+1}"
        if t == CR_SLOT_ALL_STOP:
            return "ALL\nSTP"
        if t == CR_SLOT_GRID_MODE:
            return "MODE"
        if t == CR_SLOT_PRESET_PREV:
            return "\u25C4 PRE"
        if t == CR_SLOT_PRESET_NEXT:
            return "PRE \u25BA"
        return "—"

    def _refresh_cr_button_visuals(self):
        for i, btn in enumerate(self._cr_buttons):
            slot = self._cr_assignments[i] if i < len(self._cr_assignments) else {"type": CR_SLOT_EMPTY, "value": 0}
            t = slot.get("type", CR_SLOT_EMPTY)
            style = self._CR_BTN_STYLES.get(t, self._CR_BTN_STYLES[CR_SLOT_EMPTY])
            btn.setStyleSheet(f"QPushButton {{ {style} border-radius: 4px; }}")
            btn.setText(self._slot_label(slot))
            v = slot.get("value", 0)
            if t == CR_SLOT_GROUP_STOP:
                tip = f"Button {i+1}: Group Stop {v+1}"
            elif t == CR_SLOT_PATTERN:
                tip = f"Button {i+1}: Pattern {v+1}"
            elif t == CR_SLOT_ALL_STOP:
                tip = f"Button {i+1}: All Stop (stops all groups)"
            elif t == CR_SLOT_GRID_MODE:
                tip = f"Button {i+1}: Grid Mode Cycle"
            elif t == CR_SLOT_PRESET_PREV:
                tip = f"Button {i+1}: Preset Prev (navigate to previous preset)"
            elif t == CR_SLOT_PRESET_NEXT:
                tip = f"Button {i+1}: Preset Next (navigate to next preset)"
            else:
                tip = f"Button {i+1}: Unassigned"
            btn.setToolTip(tip + "\nClick to reassign")

    def _sync_cr_preset_combo(self):
        """Select the matching preset, or 'Custom' if none matches."""
        self._cr_preset_combo.blockSignals(True)
        for idx, (_, preset_assignments) in enumerate(CR_PRESETS):
            if self._cr_assignments == preset_assignments:
                self._cr_preset_combo.setCurrentIndex(idx)
                self._cr_preset_combo.blockSignals(False)
                return
        # Custom
        self._cr_preset_combo.setCurrentIndex(len(CR_PRESETS))
        self._cr_preset_combo.blockSignals(False)

    def _on_cr_preset_changed(self, index: int):
        if index < len(CR_PRESETS):
            _, assignments = CR_PRESETS[index]
            self._cr_assignments = [dict(s) for s in assignments]
            self._refresh_cr_button_visuals()
            self.control_row_setting_changed.emit("assignments", list(self._cr_assignments))
        # 'Custom' selected: no action, user will click individual buttons

    def _on_cr_button_clicked(self, btn_idx: int):
        """Show a context menu to reassign the clicked control row button."""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1a1a1a; color: #ccc; border: 1px solid #444; font-size: 10px; }"
            "QMenu::item:selected { background: #333; }"
        )

        # Group Stop sub-menu
        gs_menu = menu.addMenu("Group Stop")
        for g in range(8):
            act = QAction(f"Group Stop {g+1}", self)
            act.triggered.connect(
                lambda checked=False, g=g: self._assign_cr_slot(
                    btn_idx, {"type": CR_SLOT_GROUP_STOP, "value": g}
                )
            )
            gs_menu.addAction(act)

        # Pattern sub-menu
        pat_menu = menu.addMenu("Pattern")
        for p in range(8):
            act = QAction(f"Pattern {p+1}", self)
            act.triggered.connect(
                lambda checked=False, p=p: self._assign_cr_slot(
                    btn_idx, {"type": CR_SLOT_PATTERN, "value": p}
                )
            )
            pat_menu.addAction(act)

        # All Stop
        all_stop_act = QAction("All Stop", self)
        all_stop_act.triggered.connect(
            lambda: self._assign_cr_slot(btn_idx, {"type": CR_SLOT_ALL_STOP, "value": 0})
        )
        menu.addAction(all_stop_act)

        # Grid Mode Cycle
        grid_mode_act = QAction("Grid Mode Cycle", self)
        grid_mode_act.triggered.connect(
            lambda: self._assign_cr_slot(btn_idx, {"type": CR_SLOT_GRID_MODE, "value": 0})
        )
        menu.addAction(grid_mode_act)

        # Preset navigation
        menu.addSeparator()
        preset_prev_act = QAction("\u25C4 Preset Prev", self)
        preset_prev_act.triggered.connect(
            lambda: self._assign_cr_slot(btn_idx, {"type": CR_SLOT_PRESET_PREV, "value": 0})
        )
        menu.addAction(preset_prev_act)

        preset_next_act = QAction("Preset Next \u25BA", self)
        preset_next_act.triggered.connect(
            lambda: self._assign_cr_slot(btn_idx, {"type": CR_SLOT_PRESET_NEXT, "value": 0})
        )
        menu.addAction(preset_next_act)

        # Empty
        menu.addSeparator()
        empty_act = QAction("Unassigned (empty)", self)
        empty_act.triggered.connect(
            lambda: self._assign_cr_slot(btn_idx, {"type": CR_SLOT_EMPTY, "value": 0})
        )
        menu.addAction(empty_act)

        menu.exec(self._cr_buttons[btn_idx].mapToGlobal(
            self._cr_buttons[btn_idx].rect().bottomLeft()
        ))

    def _assign_cr_slot(self, btn_idx: int, slot: dict):
        self._cr_assignments[btn_idx] = slot
        self._refresh_cr_button_visuals()
        self._sync_cr_preset_combo()
        self.control_row_setting_changed.emit("assignments", list(self._cr_assignments))

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
            # Skip non-MIDI entries (e.g. monome 40h injected by the app
            # for display purposes — already connected via direct serial).
            if d.get("in_port", -1) < 0:
                return
            self.midi_grid_connect_requested.emit(d["in_port"], d["out_port"])

    def _on_grid_preset(self, rows: int, cols: int):
        """Quick-preset button clicked — emit both row_count and grid_columns."""
        self._rows_spin.blockSignals(True)
        self._rows_spin.setValue(rows)
        self._rows_spin.blockSignals(False)
        self._cols_combo.blockSignals(True)
        self._cols_combo.setCurrentText(str(cols))
        self._cols_combo.blockSignals(False)
        self._sync_preset_btns(rows, cols)
        self.setting_changed.emit("row_count", rows)
        self.setting_changed.emit("grid_columns", cols)

    def _sync_preset_btns(self, rows: int, cols: int):
        """Check the matching quick-preset button, uncheck the rest."""
        _presets = [(8, 8), (8, 16), (16, 8), (16, 16)]
        for btn, (r, c) in zip(self._grid_preset_btns, _presets):
            btn.blockSignals(True)
            btn.setChecked(r == rows and c == cols)
            btn.blockSignals(False)

    def _on_backend_changed(self, index: int):
        backend = self._backend_combo.itemData(index) or MONOME_BACKEND_AUTO
        self.setting_changed.emit("monome_backend", backend)

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

    def set_active_groups(self, n: int):
        """Update the Active Groups combo to reflect the current value."""
        self._active_groups_combo.blockSignals(True)
        idx = self._active_groups_combo.findData(n)
        if idx >= 0:
            self._active_groups_combo.setCurrentIndex(idx)
        self._active_groups_combo.blockSignals(False)


class MidiSetupPanel(QFrame):
    """MIDI configuration, learn mode, and active mapping list."""

    setting_changed = Signal(str, object)
    midi_learn_toggled = Signal(bool)
    mapping_range_changed = Signal(str, float, float)   # param_path, min, max
    mapping_removed = Signal(str)                        # param_path
    mapping_toggle_changed = Signal(str, bool)           # param_path, toggle
    tilt_mapping_changed = Signal(int, str)              # axis_index (0=x,1=y,2=z), target param_path
    tilt_range_changed = Signal(int, float, float)       # axis_index, min, max
    tilt_sensitivity_changed = Signal(int, float)        # axis_index, sensitivity multiplier
    midi_input_added = Signal(int)                       # port index
    midi_input_removed = Signal(int)                     # port index
    control_row_learn_requested = Signal(str)            # param_path
    grid_cc_learn_requested = Signal(int, int)           # row, col
    grid_cc_cleared = Signal(int, int)                   # row, col
    grid_cc_size_changed = Signal(int, int)              # rows, cols

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
        bind_fixed_height(add_input_btn, 24)
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
        bind_fixed_height(self._learn_btn, 28)
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
        bind_fixed_height(self._rescan_btn, 28)
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
        self._map_table.setMinimumHeight(60)
        self._map_table.setMaximumHeight(180)
        layout.addWidget(self._map_table)

        # Clear all button
        self._clear_all_btn = QPushButton("Clear All Mappings")
        bind_fixed_height(self._clear_all_btn, 24)
        self._clear_all_btn.setStyleSheet(
            "QPushButton { background: #3a1a1a; color: #cc4444; "
            "font-size: 9px; border: 1px solid #663333; }"
            "QPushButton:hover { background: #4a2a2a; }"
        )
        self._clear_all_btn.clicked.connect(self._on_clear_all)
        self._clear_all_btn.setToolTip("Remove all active MIDI CC mappings")
        layout.addWidget(self._clear_all_btn)

        # ── Control Row Actions (collapsible) ──
        cr_sep = QFrame()
        cr_sep.setFrameShape(QFrame.Shape.HLine)
        cr_sep.setStyleSheet("color: #444;")
        layout.addWidget(cr_sep)

        self._cr_toggle_btn = QPushButton("CONTROL ROW ACTIONS  \u25BE")
        self._cr_toggle_btn.setCheckable(True)
        self._cr_toggle_btn.setChecked(False)
        bind_fixed_height(self._cr_toggle_btn, 22)
        self._cr_toggle_btn.setStyleSheet(
            "QPushButton { background: #1a1a1a; color: #666; font-weight: bold; "
            "font-size: 10px; border: none; text-align: left; padding: 0 4px; }"
            "QPushButton:hover { color: #aaa; }"
            "QPushButton:checked { color: #aaa; }"
        )
        self._cr_toggle_btn.clicked.connect(self._on_toggle_control_row)
        layout.addWidget(self._cr_toggle_btn)

        self._control_row_panel = QWidget()
        cr_panel_layout = QVBoxLayout(self._control_row_panel)
        cr_panel_layout.setContentsMargins(0, 2, 0, 0)
        cr_panel_layout.setSpacing(4)

        cr_hint = QLabel(
            "Map MIDI inputs to group stop and pattern actions. "
            "Works for any controller — hardware control row optional."
        )
        cr_hint.setStyleSheet("font-size: 9px; color: #555; font-style: italic;")
        cr_hint.setWordWrap(True)
        cr_panel_layout.addWidget(cr_hint)

        self._cr_panel_layout = cr_panel_layout
        self._control_row_rows_container = QWidget()
        cr_panel_layout.addWidget(self._control_row_rows_container)

        self._control_row_panel.setVisible(False)
        layout.addWidget(self._control_row_panel)
        self._control_row_rows: list[dict] = []
        # Populated by set_control_row_actions() called from app

        # ── Tilt / Accelerometer mapping (hidden by default) ──
        self._tilt_container = QWidget()
        tilt_inner = QVBoxLayout(self._tilt_container)
        tilt_inner.setContentsMargins(0, 0, 0, 0)
        tilt_inner.setSpacing(4)

        tilt_sep = QFrame()
        tilt_sep.setFrameShape(QFrame.Shape.HLine)
        tilt_sep.setStyleSheet("color: #444;")
        tilt_inner.addWidget(tilt_sep)

        tilt_header_row = QHBoxLayout()
        tilt_header_row.setSpacing(8)
        tilt_label = QLabel("MONOME TILT / ACCELEROMETER")
        tilt_label.setStyleSheet("font-weight: bold; font-size: 10px; color: #888;")
        tilt_header_row.addWidget(tilt_label)
        self._tilt_live_label = QLabel("no data")
        self._tilt_live_label.setStyleSheet(
            "font-size: 9px; color: #555; font-style: italic;"
        )
        self._tilt_live_label.setToolTip(
            "Live tilt sensor values (normalized 0–1). "
            "Values change only if the accelerometer is connected and data is streaming."
        )
        tilt_header_row.addWidget(self._tilt_live_label)
        tilt_header_row.addStretch()
        tilt_inner.addLayout(tilt_header_row)

        tilt_grid = QGridLayout()
        tilt_grid.setSpacing(4)
        self._tilt_combos: list[QComboBox] = []
        self._tilt_min_spins: list[QDoubleSpinBox] = []
        self._tilt_max_spins: list[QDoubleSpinBox] = []
        self._tilt_sensitivity_spins: list[QDoubleSpinBox] = []
        _TILT_TOOLTIPS = [
            "Tilt X axis — map left/right tilt to a parameter",
            "Tilt Y axis — map forward/back tilt to a parameter",
        ]
        _spin_ss = (
            "QDoubleSpinBox { background: #1a1a1a; color: #ccc; "
            "border: 1px solid #333; font-size: 9px; }"
        )
        for i, axis in enumerate(["Tilt X", "Tilt Y"]):
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

            min_spin = QDoubleSpinBox()
            min_spin.setRange(-1000.0, 1000.0)
            min_spin.setDecimals(2)
            min_spin.setValue(0.0)
            min_spin.setSingleStep(0.05)
            bind_fixed_width(min_spin, 60)
            min_spin.setStyleSheet(_spin_ss)
            min_spin.setToolTip("Output value at rest (device held flat — no tilt)")
            min_spin.valueChanged.connect(lambda v, ax=i: self._on_tilt_range_changed(ax))

            max_spin = QDoubleSpinBox()
            max_spin.setRange(-1000.0, 1000.0)
            max_spin.setDecimals(2)
            max_spin.setValue(1.0)
            max_spin.setSingleStep(0.05)
            bind_fixed_width(max_spin, 60)
            max_spin.setStyleSheet(_spin_ss)
            max_spin.setToolTip("Output value at full tilt deflection")
            max_spin.valueChanged.connect(lambda v, ax=i: self._on_tilt_range_changed(ax))

            sens_spin = QDoubleSpinBox()
            sens_spin.setRange(0.1, 20.0)
            sens_spin.setDecimals(1)
            sens_spin.setValue(5.0)
            sens_spin.setSingleStep(0.5)
            bind_fixed_width(sens_spin, 70)
            sens_spin.setStyleSheet(_spin_ss)
            sens_spin.setToolTip(
                "Sensitivity — how strongly a small physical tilt moves the output.\n"
                "5.0 = a moderate tilt (~20% of sensor range) gives a full sweep.\n"
                "Higher = more responsive; lower = requires bigger tilts."
            )
            sens_spin.valueChanged.connect(lambda v, ax=i: self._on_tilt_sensitivity_changed(ax))

            min_lbl = QLabel("Min:")
            min_lbl.setStyleSheet("color: #666; font-size: 9px;")
            max_lbl = QLabel("Max:")
            max_lbl.setStyleSheet("color: #666; font-size: 9px;")
            sens_lbl = QLabel("Sens:")
            sens_lbl.setStyleSheet("color: #666; font-size: 9px;")

            tilt_grid.addWidget(lbl, i, 0)
            tilt_grid.addWidget(combo, i, 1)
            tilt_grid.addWidget(min_lbl, i, 2)
            tilt_grid.addWidget(min_spin, i, 3)
            tilt_grid.addWidget(max_lbl, i, 4)
            tilt_grid.addWidget(max_spin, i, 5)
            tilt_grid.addWidget(sens_lbl, i, 6)
            tilt_grid.addWidget(sens_spin, i, 7)
            self._tilt_combos.append(combo)
            self._tilt_min_spins.append(min_spin)
            self._tilt_max_spins.append(max_spin)
            self._tilt_sensitivity_spins.append(sens_spin)
        tilt_inner.addLayout(tilt_grid)

        self._tilt_container.setVisible(False)
        layout.addWidget(self._tilt_container)

        # ── Grid MIDI CC Map (collapsible) ──
        gm_sep = QFrame()
        gm_sep.setFrameShape(QFrame.Shape.HLine)
        gm_sep.setStyleSheet("color: #444;")
        layout.addWidget(gm_sep)

        self._gm_toggle_btn = QPushButton("GRID MIDI CC MAP  \u25BE")
        self._gm_toggle_btn.setCheckable(True)
        self._gm_toggle_btn.setChecked(False)
        bind_fixed_height(self._gm_toggle_btn, 22)
        self._gm_toggle_btn.setStyleSheet(
            "QPushButton { background: #1a1a1a; color: #666; font-weight: bold; "
            "font-size: 10px; border: none; text-align: left; padding: 0 4px; }"
            "QPushButton:hover { color: #aaa; }"
            "QPushButton:checked { color: #aaa; }"
        )
        self._gm_toggle_btn.clicked.connect(self._on_toggle_grid_cc_map)
        layout.addWidget(self._gm_toggle_btn)

        self._gm_panel = QWidget()
        gm_layout = QVBoxLayout(self._gm_panel)
        gm_layout.setContentsMargins(0, 2, 0, 2)
        gm_layout.setSpacing(4)

        gm_hint = QLabel(
            "Map a MIDI CC output to each grid button. "
            "Click a cell to MIDI learn, right-click to clear."
        )
        gm_hint.setStyleSheet("font-size: 9px; color: #555; font-style: italic;")
        gm_hint.setWordWrap(True)
        gm_layout.addWidget(gm_hint)

        # Row / col size + clear-all row
        gm_size_row = QHBoxLayout()
        gm_size_row.setSpacing(6)
        _lbl_ss = "color: #aaa; font-size: 10px;"
        _spin_ss2 = (
            "QSpinBox { background: #1a1a1a; color: #ccc; "
            "border: 1px solid #333; font-size: 9px; }"
        )
        rows_lbl = QLabel("Rows:")
        rows_lbl.setStyleSheet(_lbl_ss)
        gm_size_row.addWidget(rows_lbl)
        self._gm_rows_spin = QSpinBox()
        self._gm_rows_spin.setRange(1, MAX_ROWS)
        self._gm_rows_spin.setValue(8)
        bind_fixed_width(self._gm_rows_spin, 48)
        self._gm_rows_spin.setStyleSheet(_spin_ss2)
        self._gm_rows_spin.setToolTip("Number of grid rows shown in the CC map editor")
        self._gm_rows_spin.valueChanged.connect(self._on_grid_cc_size_changed)
        gm_size_row.addWidget(self._gm_rows_spin)
        cols_lbl = QLabel("Cols:")
        cols_lbl.setStyleSheet(_lbl_ss)
        gm_size_row.addWidget(cols_lbl)
        self._gm_cols_spin = QSpinBox()
        self._gm_cols_spin.setRange(1, MAX_COLUMNS)
        self._gm_cols_spin.setValue(8)
        bind_fixed_width(self._gm_cols_spin, 48)
        self._gm_cols_spin.setStyleSheet(_spin_ss2)
        self._gm_cols_spin.setToolTip("Number of grid columns shown in the CC map editor")
        self._gm_cols_spin.valueChanged.connect(self._on_grid_cc_size_changed)
        gm_size_row.addWidget(self._gm_cols_spin)
        gm_size_row.addStretch()
        gm_clear_all_btn = QPushButton("Clear All")
        bind_fixed_height(gm_clear_all_btn, 20)
        bind_fixed_width(gm_clear_all_btn, 58)
        gm_clear_all_btn.setStyleSheet(
            "QPushButton { background: #3a1a1a; color: #cc4444; "
            "font-size: 9px; border: 1px solid #663333; }"
            "QPushButton:hover { background: #4a2a2a; }"
        )
        gm_clear_all_btn.setToolTip("Remove all grid MIDI CC assignments")
        gm_clear_all_btn.clicked.connect(self._on_grid_cc_clear_all)
        gm_size_row.addWidget(gm_clear_all_btn)
        gm_layout.addLayout(gm_size_row)

        # Scroll area holding the grid of cell buttons
        self._gm_scroll = QScrollArea()
        self._gm_scroll.setWidgetResizable(True)
        self._gm_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._gm_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._gm_scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self._gm_scroll.setStyleSheet(
            "QScrollArea { background: #111; border: 1px solid #333; }")
        self._gm_scroll.setMinimumHeight(60)
        self._gm_scroll.setMaximumHeight(300)
        gm_layout.addWidget(self._gm_scroll)

        self._gm_grid_widget = QWidget()
        self._gm_grid_widget.setStyleSheet("background: #111;")
        self._gm_scroll.setWidget(self._gm_grid_widget)
        self._gm_grid_inner = QGridLayout(self._gm_grid_widget)
        self._gm_grid_inner.setSpacing(2)
        self._gm_grid_inner.setContentsMargins(4, 4, 4, 4)

        # Shared cell button stylesheets (stored on instance for _refresh_cell_style)
        self._gm_ss_empty = (
            "QPushButton { background: #1a1a1a; color: #444; border: 1px solid #222; "
            "font-size: 8px; padding: 1px; }"
            "QPushButton:hover { background: #252525; color: #888; border-color: #444; }"
        )
        self._gm_ss_mapped = (
            "QPushButton { background: #1a2a1a; color: #7fc97f; border: 1px solid #336633; "
            "font-size: 8px; padding: 1px; }"
            "QPushButton:hover { background: #243824; color: #99e999; }"
        )
        self._gm_ss_learning = (
            "QPushButton { background: #2a2200; color: #f5d97a; border: 1px solid #665500; "
            "font-size: 8px; padding: 1px; font-weight: bold; }"
        )

        self._gm_buttons: dict[tuple[int, int], QPushButton] = {}
        self._gm_learning_cell: tuple[int, int] | None = None
        self._gm_cc_map: dict[tuple[int, int], dict] = {}

        self._gm_panel.setVisible(False)
        layout.addWidget(self._gm_panel)

        # Build initial grid
        self._rebuild_grid_cc_editor(8, 8)

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

    def set_control_row_actions(self, num_groups: int, num_patterns: int):
        """Rebuild the control row action rows for the given counts.

        Called by the app whenever num_group_stops or num_patterns changes, and
        on startup / preset load. Clears existing rows then rebuilds them.
        """
        # Replace the container widget so we get a clean layout each time
        self._control_row_rows_container.setParent(None)
        self._control_row_rows_container.deleteLater()
        self._control_row_rows.clear()

        new_container = QWidget()
        grid = QGridLayout(new_container)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._control_row_rows_container = new_container
        self._cr_panel_layout.addWidget(new_container)

        _btn_ss = (
            "QPushButton { background: #2a2a2a; color: #aaa; border: 1px solid #444; "
            "font-size: 9px; padding: 1px 5px; }"
            "QPushButton:hover { background: #3a3a3a; color: #fff; }"
        )
        _clr_ss = (
            "QPushButton { background: transparent; color: #cc4444; "
            "font-weight: bold; border: none; font-size: 11px; padding: 0; }"
            "QPushButton:hover { color: #ff6666; }"
        )
        _cell_ss = (
            "QWidget#crCell { background: #1e1e1e; border: 1px solid #2a2a2a; "
            "border-radius: 3px; }"
        )

        entries = (
            [(f"Group Stop {i + 1}", f"transport.group_stop.{i}") for i in range(num_groups)]
            + [(f"Pattern {i + 1}", f"transport.pattern.{i}") for i in range(num_patterns)]
            + [("Grid Mode Cycle", "transport.grid_mode_cycle")]
            + [("\u25C4 Preset Prev", "transport.preset_prev"),
               ("Preset Next \u25BA", "transport.preset_next")]
        )

        for i, (label_text, param_path) in enumerate(entries):
            grid_row = i // 2
            grid_col = i % 2

            cell = QWidget()
            cell.setObjectName("crCell")
            cell.setStyleSheet(_cell_ss)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(5, 3, 3, 3)
            cell_layout.setSpacing(4)

            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #bbb; font-size: 10px;")
            lbl.setMinimumWidth(80)
            lbl.setSizePolicy(
                lbl.sizePolicy().horizontalPolicy(),
                lbl.sizePolicy().verticalPolicy(),
            )
            cell_layout.addWidget(lbl, stretch=1)

            assign_lbl = QLabel("\u2014")
            assign_lbl.setStyleSheet("color: #555; font-size: 9px; font-style: italic;")
            assign_lbl.setMinimumWidth(60)
            assign_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.addWidget(assign_lbl)

            learn_btn = QPushButton("Learn")
            bind_fixed_height(learn_btn, 18)
            bind_fixed_width(learn_btn, 40)
            learn_btn.setStyleSheet(_btn_ss)
            learn_btn.setToolTip(f"MIDI Learn: next incoming CC/Note \u2192 {label_text}")
            learn_btn.clicked.connect(
                lambda _checked=False, p=param_path: self.control_row_learn_requested.emit(p)
            )
            cell_layout.addWidget(learn_btn)

            clear_btn = QPushButton("\u2715")
            bind_fixed_size(clear_btn, 18, 18)
            clear_btn.setStyleSheet(_clr_ss)
            clear_btn.setToolTip(f"Clear MIDI mapping for {label_text}")
            clear_btn.clicked.connect(
                lambda _checked=False, p=param_path: self.mapping_removed.emit(p)
            )
            cell_layout.addWidget(clear_btn)

            grid.addWidget(cell, grid_row, grid_col)
            self._control_row_rows.append({
                "param_path": param_path,
                "assign_lbl": assign_lbl,
                "container": cell,
            })

        # If odd number of entries, fill last cell with a spacer so columns stay even
        if len(entries) % 2 == 1:
            grid.addWidget(QWidget(), len(entries) // 2, 1)

    def update_control_row_assignment(self, param_path: str, info: str):
        """Update the assignment label for one control row action row.

        Pass an empty string to show the 'not mapped' dash.
        """
        for row_info in self._control_row_rows:
            if row_info["param_path"] == param_path:
                if info:
                    row_info["assign_lbl"].setText(info)
                    row_info["assign_lbl"].setStyleSheet(
                        "color: #7fc97f; font-size: 9px;"
                    )
                else:
                    row_info["assign_lbl"].setText("\u2014")
                    row_info["assign_lbl"].setStyleSheet(
                        "color: #555; font-size: 9px; font-style: italic;"
                    )
                break

    def update_tilt_live(self, x: float, y: float):
        """Update live tilt value display (called from app when data arrives)."""
        self._tilt_live_label.setStyleSheet(
            "font-size: 9px; color: #7fc97f;"
        )
        self._tilt_live_label.setText(
            f"X={x:.2f}  Y={y:.2f}"
        )

    def _on_toggle_control_row(self, checked: bool):
        self._control_row_panel.setVisible(checked)
        self._cr_toggle_btn.setText(
            "CONTROL ROW ACTIONS  \u25B4" if checked else "CONTROL ROW ACTIONS  \u25BE"
        )

    # ── Grid MIDI CC Map helpers ──

    def _on_toggle_grid_cc_map(self, checked: bool):
        self._gm_panel.setVisible(checked)
        self._gm_toggle_btn.setText(
            "GRID MIDI CC MAP  \u25B4" if checked else "GRID MIDI CC MAP  \u25BE"
        )

    def _on_grid_cc_size_changed(self):
        rows = self._gm_rows_spin.value()
        cols = self._gm_cols_spin.value()
        self._rebuild_grid_cc_editor(rows, cols)
        self.grid_cc_size_changed.emit(rows, cols)

    def _rebuild_grid_cc_editor(self, rows: int, cols: int):
        """Destroy and recreate all cell buttons for the given grid dimensions."""
        for btn in self._gm_buttons.values():
            btn.setParent(None)
            btn.deleteLater()
        self._gm_buttons.clear()

        for r in range(rows):
            for c in range(cols):
                btn = QPushButton()
                btn.setFixedSize(42, 28)
                btn.setToolTip(
                    f"Row {r + 1}, Col {c + 1}\n"
                    "Click to MIDI learn, right-click to clear"
                )
                self._apply_cell_style(btn, r, c)
                btn.clicked.connect(
                    lambda _=False, row=r, col=c:
                        self.grid_cc_learn_requested.emit(row, col)
                )
                btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda _pos, row=r, col=c: self._on_grid_cc_ctx(row, col)
                )
                self._gm_grid_inner.addWidget(btn, r, c)
                self._gm_buttons[(r, c)] = btn

    def _apply_cell_style(self, btn: QPushButton, row: int, col: int):
        """Set button text and stylesheet to reflect current cell state."""
        key = (row, col)
        if self._gm_learning_cell == key:
            btn.setText("LEARN\n...")
            btn.setStyleSheet(self._gm_ss_learning)
        elif key in self._gm_cc_map:
            m = self._gm_cc_map[key]
            btn.setText(f"CC{m['cc']}\nCh{m['channel'] + 1}")
            btn.setStyleSheet(self._gm_ss_mapped)
        else:
            btn.setText(f"R{row + 1}\nC{col + 1}")
            btn.setStyleSheet(self._gm_ss_empty)

    def _on_grid_cc_ctx(self, row: int, col: int):
        """Right-click context menu on a grid CC cell."""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        clear_act = menu.addAction("Clear Mapping")
        act = menu.exec(self.cursor().pos())
        if act == clear_act:
            self.grid_cc_cleared.emit(row, col)

    def _on_grid_cc_clear_all(self):
        """Emit clear for every cell in the current grid."""
        rows = self._gm_rows_spin.value()
        cols = self._gm_cols_spin.value()
        for r in range(rows):
            for c in range(cols):
                self.grid_cc_cleared.emit(r, c)

    # ── Public API for Grid CC Map ──

    def set_grid_cc_size(self, rows: int, cols: int):
        """Update size spinboxes (without re-emitting) and rebuild the grid."""
        self._gm_rows_spin.blockSignals(True)
        self._gm_rows_spin.setValue(rows)
        self._gm_rows_spin.blockSignals(False)
        self._gm_cols_spin.blockSignals(True)
        self._gm_cols_spin.setValue(cols)
        self._gm_cols_spin.blockSignals(False)
        self._rebuild_grid_cc_editor(rows, cols)

    def update_grid_cc_assignment(self, row: int, col: int, channel: int, cc: int):
        """Store and display a new CC assignment for one cell."""
        key = (row, col)
        self._gm_cc_map[key] = {"channel": channel, "cc": cc}
        if self._gm_learning_cell == key:
            self._gm_learning_cell = None
        btn = self._gm_buttons.get(key)
        if btn:
            self._apply_cell_style(btn, row, col)

    def clear_grid_cc_assignment(self, row: int, col: int):
        """Remove CC assignment for one cell and reset its display."""
        key = (row, col)
        self._gm_cc_map.pop(key, None)
        btn = self._gm_buttons.get(key)
        if btn:
            self._apply_cell_style(btn, row, col)

    def set_grid_cc_learning(self, row: int, col: int):
        """Highlight a cell to show it is waiting for MIDI learn input."""
        prev = self._gm_learning_cell
        if prev and prev != (row, col):
            pb = self._gm_buttons.get(prev)
            if pb:
                self._gm_learning_cell = None
                self._apply_cell_style(pb, prev[0], prev[1])
        self._gm_learning_cell = (row, col)
        btn = self._gm_buttons.get((row, col))
        if btn:
            self._apply_cell_style(btn, row, col)

    def clear_grid_cc_learning(self):
        """Remove any active learning highlight."""
        if self._gm_learning_cell:
            r, c = self._gm_learning_cell
            self._gm_learning_cell = None
            btn = self._gm_buttons.get((r, c))
            if btn:
                self._apply_cell_style(btn, r, c)

    def refresh_grid_cc_map(self, cc_map: dict):
        """Reload all cell states from a serialised cc_map dict (key = 'r,c')."""
        self._gm_cc_map.clear()
        for key_str, val in cc_map.items():
            try:
                r_s, c_s = key_str.split(",", 1)
                self._gm_cc_map[(int(r_s), int(c_s))] = val
            except (ValueError, AttributeError):
                pass
        rows = self._gm_rows_spin.value()
        cols = self._gm_cols_spin.value()
        self._rebuild_grid_cc_editor(rows, cols)

    def _on_add_input(self):
        """Add a new MIDI input selector row."""
        row_layout = QHBoxLayout()
        row_layout.setSpacing(4)

        combo = QComboBox()
        combo.addItem("None")
        combo.addItems(self._available_inputs)
        bind_min_width(combo, 180)
        row_layout.addWidget(combo, stretch=1)

        remove_btn = QPushButton("✕")
        bind_fixed_size(remove_btn, 24, 22)
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

    def remove_mapping_row(self, param_path: str):
        """Remove the table row for param_path without emitting mapping_removed.

        Used when the handler has already cleaned up its internal state (e.g.
        when a new learn displaces an existing mapping, or when a re-learn
        replaces a row for the same param).
        """
        for r in range(self._map_table.rowCount()):
            item = self._map_table.item(r, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == param_path:
                self._map_table.removeRow(r)
                break

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
        bind_fixed_size(del_btn, 24, 22)
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

    def set_tilt_axis_state(self, axis: int, param_path: str,
                            min_val: float, max_val: float,
                            sensitivity: float = 5.0):
        """Restore a tilt axis target, range and sensitivity (e.g. on preset load)."""
        if axis >= len(self._tilt_combos):
            return
        combo = self._tilt_combos[axis]
        found = 0
        for i in range(combo.count()):
            if combo.itemData(i) == param_path:
                found = i
                break
        combo.blockSignals(True)
        combo.setCurrentIndex(found)
        combo.blockSignals(False)
        self._tilt_min_spins[axis].blockSignals(True)
        self._tilt_min_spins[axis].setValue(min_val)
        self._tilt_min_spins[axis].blockSignals(False)
        self._tilt_max_spins[axis].blockSignals(True)
        self._tilt_max_spins[axis].setValue(max_val)
        self._tilt_max_spins[axis].blockSignals(False)
        self._tilt_sensitivity_spins[axis].blockSignals(True)
        self._tilt_sensitivity_spins[axis].setValue(sensitivity)
        self._tilt_sensitivity_spins[axis].blockSignals(False)

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

    def _on_tilt_range_changed(self, axis: int):
        """Emit range change when a tilt min/max spinbox changes."""
        min_v = self._tilt_min_spins[axis].value()
        max_v = self._tilt_max_spins[axis].value()
        self.tilt_range_changed.emit(axis, min_v, max_v)

    def _on_tilt_sensitivity_changed(self, axis: int):
        """Emit sensitivity change when the Sens spinbox changes."""
        sens = self._tilt_sensitivity_spins[axis].value()
        self.tilt_sensitivity_changed.emit(axis, sens)

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

        # Meter output toggle
        self._meter_output_cb = QCheckBox("Send meter levels (OSC output)")
        self._meter_output_cb.setChecked(True)
        self._meter_output_cb.setToolTip(
            "Broadcast group and master peak levels at ~30 fps so external\n"
            "OSC clients (TouchOSC, Max for Live, SuperCollider) can display\n"
            "them. Uses addresses:\n"
            "  /mlrvp/meter/master       f f  (L, R)\n"
            "  /mlrvp/meter/group/<N>    f    (N = 1-based)\n"
            "  /mlrvp/meter/fx/<N>       f f  (N = 1-4)"
        )
        self._meter_output_cb.toggled.connect(
            lambda v: self.setting_changed.emit("osc_meter_output", v)
        )
        layout.addWidget(self._meter_output_cb)

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
        self._meter_output_cb.setEnabled(enabled)

    def set_enabled_state(self, enabled: bool):
        """Set the checkbox state without emitting (for preset load)."""
        self._enable_cb.blockSignals(True)
        self._enable_cb.setChecked(enabled)
        self._enable_cb.blockSignals(False)
        self._set_fields_enabled(enabled)

    def set_meter_output_state(self, enabled: bool):
        """Sync the meter output checkbox without emitting (for preset load)."""
        self._meter_output_cb.blockSignals(True)
        self._meter_output_cb.setChecked(enabled)
        self._meter_output_cb.blockSignals(False)


class SetupView(QWidget):
    """Tabbed setup panel: Audio, Grid, MIDI, OSC."""

    audio_setting_changed = Signal(str, object)
    link_toggled = Signal(bool)  # Ableton Link enable/disable
    grid_setting_changed = Signal(str, object)
    midi_setting_changed = Signal(str, object)
    midi_learn_toggled = Signal(bool)
    midi_mapping_range_changed = Signal(str, float, float)
    midi_mapping_removed = Signal(str)
    midi_mapping_toggle_changed = Signal(str, bool)
    midi_tilt_mapping_changed = Signal(int, str)  # axis, target param_path
    midi_tilt_range_changed = Signal(int, float, float)  # axis, min, max
    midi_tilt_sensitivity_changed = Signal(int, float)  # axis, sensitivity
    midi_input_added = Signal(int)     # port index
    midi_input_removed = Signal(int)   # port index
    osc_setting_changed = Signal(str, object)
    grid_scan_requested = Signal()
    midi_grid_connect_requested = Signal(int, int)
    midi_grid_disconnect_requested = Signal()
    save_default_requested = Signal(str)
    load_default_requested = Signal(str)
    tilt_enabled_changed = Signal(bool)
    control_row_enabled_changed = Signal(bool)
    control_row_setting_changed = Signal(str, object)   # key, value
    midi_control_row_learn_requested = Signal(str)      # param_path
    midi_grid_cc_learn_requested = Signal(int, int)     # row, col
    midi_grid_cc_cleared = Signal(int, int)             # row, col
    midi_grid_cc_size_changed = Signal(int, int)        # rows, cols

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        self._audio_panel = AudioSetupPanel()
        self._audio_panel.setting_changed.connect(self.audio_setting_changed.emit)
        self._audio_panel.link_toggled.connect(self.link_toggled.emit)
        tabs.addTab(self._audio_panel, "Audio")

        self._grid_panel = GridSetupPanel()
        self._grid_panel.setting_changed.connect(self.grid_setting_changed.emit)
        self._grid_panel.scan_requested.connect(self.grid_scan_requested.emit)
        self._grid_panel.midi_grid_connect_requested.connect(self.midi_grid_connect_requested.emit)
        self._grid_panel.midi_grid_disconnect_requested.connect(self.midi_grid_disconnect_requested.emit)
        self._grid_panel.save_default_requested.connect(self.save_default_requested.emit)
        self._grid_panel.load_default_requested.connect(self.load_default_requested.emit)
        self._grid_panel.tilt_enabled_changed.connect(self.tilt_enabled_changed.emit)
        self._grid_panel.control_row_enabled_changed.connect(self.control_row_enabled_changed.emit)
        self._grid_panel.control_row_setting_changed.connect(self.control_row_setting_changed.emit)
        _grid_scroll = QScrollArea()
        _grid_scroll.setWidget(self._grid_panel)
        _grid_scroll.setWidgetResizable(True)
        _grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _grid_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        _grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tabs.addTab(_grid_scroll, "Grid")

        self._midi_panel = MidiSetupPanel()
        self._midi_panel.setting_changed.connect(self.midi_setting_changed.emit)
        self._midi_panel.midi_learn_toggled.connect(self.midi_learn_toggled.emit)
        self._midi_panel.mapping_range_changed.connect(self.midi_mapping_range_changed.emit)
        self._midi_panel.mapping_removed.connect(self.midi_mapping_removed.emit)
        self._midi_panel.mapping_toggle_changed.connect(self.midi_mapping_toggle_changed.emit)
        self._midi_panel.tilt_mapping_changed.connect(self.midi_tilt_mapping_changed.emit)
        self._midi_panel.tilt_range_changed.connect(self.midi_tilt_range_changed.emit)
        self._midi_panel.tilt_sensitivity_changed.connect(self.midi_tilt_sensitivity_changed.emit)
        self._midi_panel.control_row_learn_requested.connect(self.midi_control_row_learn_requested.emit)
        self._midi_panel.grid_cc_learn_requested.connect(self.midi_grid_cc_learn_requested.emit)
        self._midi_panel.grid_cc_cleared.connect(self.midi_grid_cc_cleared.emit)
        self._midi_panel.grid_cc_size_changed.connect(self.midi_grid_cc_size_changed.emit)
        self._midi_panel.midi_input_added.connect(self.midi_input_added.emit)
        self._midi_panel.midi_input_removed.connect(self.midi_input_removed.emit)
        _midi_scroll = QScrollArea()
        _midi_scroll.setWidget(self._midi_panel)
        _midi_scroll.setWidgetResizable(True)
        _midi_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _midi_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        _midi_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tabs.addTab(_midi_scroll, "MIDI")

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

    def update_tilt_live(self, x: float, y: float):
        """Forward live tilt values to the MIDI panel display."""
        self._midi_panel.update_tilt_live(x, y)

    def set_link_state(self, enabled: bool, num_peers: int, available: bool = True):
        """Forward link state update to the Audio panel button."""
        self._audio_panel.set_link_state(enabled, num_peers, available)
