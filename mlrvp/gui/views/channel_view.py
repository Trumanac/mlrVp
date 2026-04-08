"""Channel strip view — one row of controls per channel in mlrVp.

Pixel-matched to ch.maxpat presentation layout: each row has group color
indicator, waveform display (668×20 area), mode/group controls, gain fader,
speed control, and mute button.
"""

import math

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QFileDialog,
    QScrollArea, QFrame, QSizePolicy, QMenu
)
from PySide6.QtCore import Qt, Signal, QMimeData
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QAction

from mlrvp.config import ChannelState, PlaybackMode, NUM_GROUPS, MAX_ROWS, PITCH_SCALE_NAMES
from mlrvp.gui.styles import (
    GROUP_COLORS, BG_PANEL, BG_BLACK, BG_MAIN, SEPARATOR,
    ACCENT_GOLD, TEXT_PRIMARY, TEXT_SECONDARY,
    _ARROW_UP, _ARROW_DOWN,
)
from mlrvp.gui.widgets.waveform import WaveformWidget
from mlrvp.gui.widgets.fader import HorizontalFader


# ── Gain-fader dB helpers ────────────────────────────────────────────────
# Fader position 0.0 → silence (-inf dB)
# Fader position 0.8 → unity (0 dB)
# Fader position 1.0 → +6 dB boost
_UNITY_POS = 0.8
_BOOST_DB = 6.0


def _fader_to_gain(pos: float) -> float:
    """Convert 0-1 fader position to linear gain (audio taper)."""
    if pos <= 0.0:
        return 0.0
    if pos <= _UNITY_POS:
        t = pos / _UNITY_POS
        return t * t * t
    boost_db = (pos - _UNITY_POS) / (1.0 - _UNITY_POS) * _BOOST_DB
    return 10.0 ** (boost_db / 20.0)


def _gain_to_fader(gain: float) -> float:
    """Convert linear gain back to 0-1 fader position."""
    if gain <= 0.0:
        return 0.0
    if gain <= 1.0:
        return gain ** (1.0 / 3.0) * _UNITY_POS
    db = 20.0 * math.log10(gain)
    return min(1.0, _UNITY_POS + (db / _BOOST_DB) * (1.0 - _UNITY_POS))


def _gain_db_label(pos: float) -> str:
    """Format fader position as a dB string for the overlay."""
    if pos <= 0.0:
        return "-inf dB"
    gain = _fader_to_gain(pos)
    if gain <= 0.0:
        return "-inf dB"
    db = 20.0 * math.log10(gain)
    if db <= -60.0:
        return "-inf dB"
    return f"{db:+.1f} dB"


class ChannelStrip(QFrame):
    """Single channel strip — matches ch.maxpat presentation."""

    sample_load_requested = Signal(int, str)  # row, filepath
    sample_clear_requested = Signal(int)       # row
    param_changed = Signal(int, str, object)  # row, param_name, value

    # Class-level sample library shared across all strips
    _sample_library: list[str] = []  # list of filepaths

    def __init__(self, row: int, parent=None):
        super().__init__(parent)
        self._row = row
        self.setObjectName("channelStrip")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAcceptDrops(True)
        self.setStyleSheet(
            f"ChannelStrip {{ background: {BG_PANEL}; "
            f"border-bottom: 1px solid {SEPARATOR}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        # ── Top control row ──
        top_row = QHBoxLayout()
        top_row.setSpacing(2)
        top_row.setContentsMargins(0, 0, 0, 0)

        # ── Section layout helpers ───────────────────────────────────────────
        def _vdiv():
            div = QFrame()
            div.setFrameShape(QFrame.Shape.VLine)
            div.setFrameShadow(QFrame.Shadow.Plain)
            div.setStyleSheet(f"background: {SEPARATOR}; max-width: 1px;")
            div.setFixedWidth(1)
            return div

        def _section(label_text, *widgets, inner_spacing=2):
            sec = QWidget()
            vb = QVBoxLayout(sec)
            vb.setContentsMargins(2, 0, 2, 0)
            vb.setSpacing(0)
            lbl = QLabel(label_text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedHeight(10)
            lbl.setStyleSheet(
                f"color: {TEXT_SECONDARY}; font-size: 7px; background: transparent;"
            )
            vb.addWidget(lbl)
            hw = QWidget()
            hl = QHBoxLayout(hw)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(inner_spacing)
            for wgt in widgets:
                hl.addWidget(wgt)
            vb.addWidget(hw)
            return sec

        # Leading divider (left bookend)
        top_row.addWidget(_vdiv())

        # Group color indicator bar (left edge)
        self._group_bar = QFrame()
        self._group_bar.setFixedSize(4, 20)
        self._group_bar.setStyleSheet(f"background: {GROUP_COLORS[0]};")
        top_row.addWidget(self._group_bar)

        # Row number
        self._row_label = QLabel(f"{row + 1}")
        self._row_label.setFixedWidth(14)
        self._row_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._row_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-weight: bold; font-size: 11px; background: transparent;"
        )
        top_row.addWidget(self._row_label)

        # Clickable sample name label
        self._sample_name = QPushButton("(no sample)")
        self._sample_name.setFlat(True)
        self._sample_name.setFixedHeight(20)
        self._sample_name.setMinimumWidth(60)
        self._sample_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._sample_name.setStyleSheet(
            f"QPushButton {{ color: {TEXT_SECONDARY}; font-size: 9px; "
            f"text-align: left; padding: 0 4px; border: none; background: transparent; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; text-decoration: underline; }}"
        )
        self._sample_name.setToolTip("Click to select from loaded samples")
        self._sample_name.clicked.connect(self._show_sample_menu)

        self._load_btn = QPushButton("LOAD")
        self._load_btn.setFixedSize(40, 20)
        self._load_btn.setToolTip("Load sample")
        self._load_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {ACCENT_GOLD}; "
            f"border: 1px solid {SEPARATOR}; font-size: 9px; font-weight: bold; "
            f"padding: 0px 4px; }}"
        )
        self._load_btn.clicked.connect(self._on_load_click)

        # ── SAMPLE section (stretchy: sample name + load button) ──────────────
        _sample_sec = QWidget()
        _ss_vb = QVBoxLayout(_sample_sec)
        _ss_vb.setContentsMargins(2, 0, 2, 0)
        _ss_vb.setSpacing(0)
        _ss_lbl = QLabel("SAMPLE")
        _ss_lbl.setFixedHeight(10)
        _ss_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        _ss_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 7px; background: transparent;"
        )
        _ss_vb.addWidget(_ss_lbl)
        _ss_hw = QWidget()
        _ss_hl = QHBoxLayout(_ss_hw)
        _ss_hl.setContentsMargins(0, 0, 0, 0)
        _ss_hl.setSpacing(2)
        _ss_hl.addWidget(self._sample_name, stretch=1)
        _ss_hl.addWidget(self._load_btn)
        _ss_vb.addWidget(_ss_hw)
        top_row.addWidget(_sample_sec, stretch=1)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["LOOP", "RVRS", "SHOT", "SLCE", "SQNC", "PTCH"])
        self._mode_combo.setFixedHeight(20)
        self._mode_combo.setMaximumWidth(48)
        self._mode_combo.setStyleSheet(
            f"QComboBox {{ font-size: 10px; padding: 2px 4px; color: {TEXT_PRIMARY}; }}"
            f"QComboBox QAbstractItemView {{ min-width: 56px; }}"
        )
        self._mode_combo.currentIndexChanged.connect(
            lambda idx: self._on_mode_changed(idx)
        )

        top_row.addWidget(_vdiv())
        top_row.addWidget(_section("MODE", self._mode_combo))
        top_row.addWidget(_vdiv())

        # Group buttons — 4 colored squares matching mlrv GRP layout
        self._group_btns: list[QPushButton] = []
        for i in range(NUM_GROUPS):
            color = GROUP_COLORS[i % len(GROUP_COLORS)]
            btn = QPushButton(str(i + 1))
            btn.setFixedSize(18, 20)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setStyleSheet(
                f"QPushButton {{ background: #1a1a11; color: {color}; "
                f"border: 1px solid {color}; font-size: 9px; font-weight: bold; "
                f"padding: 0px; }}"
                f"QPushButton:checked {{ background: {color}; color: #111111; "
                f"border: 1px solid {color}; }}"
            )
            btn.clicked.connect(lambda _checked, idx=i: self._select_group(idx))
            self._group_btns.append(btn)
        top_row.addWidget(_section("GROUP", *self._group_btns, inner_spacing=1))

        top_row.addWidget(_vdiv())

        self._gain_fader = HorizontalFader(default_value=_UNITY_POS)
        self._gain_fader.set_value(_UNITY_POS)  # 0 dB unity
        self._gain_fader.setFixedWidth(105)
        self._gain_fader.set_formatter(_gain_db_label)
        self._gain_fader.value_changed.connect(
            lambda v: self.param_changed.emit(self._row, "gain", _fader_to_gain(v))
        )
        top_row.addWidget(_section("GAIN", self._gain_fader))

        top_row.addWidget(_vdiv())

        self._true_speed = 1.0
        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0.0, 8.0)
        self._speed_spin.setSingleStep(0.1)
        self._speed_spin.setValue(1.0)
        self._speed_spin.setDecimals(2)
        self._speed_spin.setFixedWidth(64)
        self._speed_spin.setFixedHeight(20)
        self._speed_spin.setSuffix("x")
        self._speed_spin.setStyleSheet(
            f"QDoubleSpinBox {{ font-size: 10px; color: {TEXT_PRIMARY}; "
            f"padding-left: 2px; background: {BG_BLACK}; border: 1px solid {SEPARATOR}; }}"
            f"QDoubleSpinBox::up-button {{ width: 16px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: top right; }}"
            f"QDoubleSpinBox::down-button {{ width: 16px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: bottom right; }}"
            f"QDoubleSpinBox::up-arrow {{ image: url({_ARROW_UP}); width: 8px; height: 5px; }}"
            f"QDoubleSpinBox::down-arrow {{ image: url({_ARROW_DOWN}); width: 8px; height: 5px; }}"
        )
        self._speed_spin.valueChanged.connect(self._on_speed_spin_changed)

        # Rate halve / double buttons
        rate_btn_style = (
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 9px; font-weight: bold; "
            f"padding: 0px; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; border-color: #79b1ff; }}"
        )
        self._half_btn = QPushButton("-½")
        self._half_btn.setFixedSize(28, 20)
        self._half_btn.setToolTip("Halve speed (double sample length)")
        self._half_btn.setStyleSheet(rate_btn_style)
        self._half_btn.clicked.connect(self._on_half_speed)

        self._double_btn = QPushButton("+2x")
        self._double_btn.setFixedSize(28, 20)
        self._double_btn.setToolTip("Double speed (halve sample length)")
        self._double_btn.setStyleSheet(rate_btn_style)
        self._double_btn.clicked.connect(self._on_double_speed)

        top_row.addWidget(_section("RATE", self._speed_spin, self._half_btn, self._double_btn))

        top_row.addWidget(_vdiv())

        # Pitch control — integer semitones, same arrow style as speed spinner
        self._pitch_spin = QSpinBox()
        self._pitch_spin.setRange(-24, 24)
        self._pitch_spin.setValue(0)
        self._pitch_spin.setFixedWidth(72)
        self._pitch_spin.setFixedHeight(20)
        self._pitch_spin.setSuffix(" st")
        self._pitch_spin.setToolTip("Transpose in semitones (–24 to +24)")
        self._pitch_spin.setStyleSheet(
            f"QSpinBox {{ font-size: 10px; color: {TEXT_PRIMARY}; "
            f"padding-left: 2px; background: {BG_BLACK}; border: 1px solid {SEPARATOR}; }}"
            f"QSpinBox::up-button {{ width: 16px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: top right; }}"
            f"QSpinBox::down-button {{ width: 16px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: bottom right; }}"
            f"QSpinBox::up-arrow {{ image: url({_ARROW_UP}); width: 8px; height: 5px; }}"
            f"QSpinBox::down-arrow {{ image: url({_ARROW_DOWN}); width: 8px; height: 5px; }}"
        )
        self._pitch_spin.valueChanged.connect(
            lambda v: self.param_changed.emit(self._row, "transpose", v)
        )
        self._pitch_section = _section("PITCH", self._pitch_spin)
        top_row.addWidget(self._pitch_section)

        # ROOT section — same spinner, visible only in PTCH mode (replaces PITCH section)
        self._pitch_root_spin = QSpinBox()
        self._pitch_root_spin.setRange(-24, 24)
        self._pitch_root_spin.setValue(0)
        self._pitch_root_spin.setFixedWidth(56)
        self._pitch_root_spin.setFixedHeight(20)
        self._pitch_root_spin.setSuffix(" st")
        self._pitch_root_spin.setToolTip("Root pitch offset in semitones")
        self._pitch_root_spin.setStyleSheet(
            f"QSpinBox {{ font-size: 10px; color: {TEXT_PRIMARY}; "
            f"padding-left: 2px; background: {BG_BLACK}; border: 1px solid {SEPARATOR}; }}"
            f"QSpinBox::up-button {{ width: 16px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: top right; }}"
            f"QSpinBox::down-button {{ width: 16px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: bottom right; }}"
            f"QSpinBox::up-arrow {{ image: url({_ARROW_UP}); width: 8px; height: 5px; }}"
            f"QSpinBox::down-arrow {{ image: url({_ARROW_DOWN}); width: 8px; height: 5px; }}"
        )
        self._pitch_root_spin.valueChanged.connect(
            lambda v: self.param_changed.emit(self._row, "pitch_root", v)
        )
        self._pitch_mode_root = _section("PITCH", self._pitch_root_spin)
        self._pitch_mode_root.setVisible(False)
        top_row.addWidget(self._pitch_mode_root)

        top_row.addWidget(_vdiv())

        # SCALE section — scale combo + HOLD toggle, visible only in PTCH mode
        self._pitch_scale_combo = QComboBox()
        self._pitch_scale_combo.addItems(PITCH_SCALE_NAMES)
        self._pitch_scale_combo.setFixedHeight(20)
        self._pitch_scale_combo.setMaximumWidth(72)
        self._pitch_scale_combo.setStyleSheet(
            f"QComboBox {{ font-size: 10px; padding: 2px 6px; color: {TEXT_PRIMARY}; }}"
            f"QComboBox QAbstractItemView {{ min-width: 80px; }}"
        )
        self._pitch_scale_combo.currentIndexChanged.connect(
            lambda idx: self.param_changed.emit(self._row, "pitch_scale", idx)
        )
        self._pitch_hold_btn = QPushButton("HOLD")
        self._pitch_hold_btn.setFixedSize(38, 20)
        self._pitch_hold_btn.setCheckable(True)
        self._pitch_hold_btn.setToolTip("Loop sample continuously on each pitch trigger")
        self._pitch_hold_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {SEPARATOR}; "
            f"border: 1px solid {SEPARATOR}; font-size: 8px; font-weight: bold; "
            f"padding: 0px 2px; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
            f"QPushButton:checked {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        self._pitch_hold_btn.toggled.connect(
            lambda v: self.param_changed.emit(self._row, "pitch_hold", v)
        )
        self._scale_section = _section("SCALE", self._pitch_scale_combo, self._pitch_hold_btn)
        self._scale_section.setVisible(False)
        top_row.addWidget(self._scale_section)

        # Step control — number of slices the sample is divided into
        self._steps_spin = QSpinBox()
        self._steps_spin.setRange(1, 32)
        self._steps_spin.setValue(8)
        self._steps_spin.setFixedWidth(52)
        self._steps_spin.setFixedHeight(20)
        self._steps_spin.setToolTip("Number of steps (slices) — maps grid buttons to sample positions")
        self._steps_spin.setStyleSheet(
            f"QSpinBox {{ font-size: 10px; color: {TEXT_PRIMARY}; "
            f"padding-left: 2px; background: {BG_BLACK}; border: 1px solid {SEPARATOR}; }}"
            f"QSpinBox::up-button {{ width: 16px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: top right; }}"
            f"QSpinBox::down-button {{ width: 16px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: bottom right; }}"
            f"QSpinBox::up-arrow {{ image: url({_ARROW_UP}); width: 8px; height: 5px; }}"
            f"QSpinBox::down-arrow {{ image: url({_ARROW_DOWN}); width: 8px; height: 5px; }}"
        )
        self._steps_spin.valueChanged.connect(
            lambda v: self.param_changed.emit(self._row, "steps", v)
        )
        self._steps_section = _section("STEP", self._steps_spin)
        top_row.addWidget(self._steps_section)

        top_row.addWidget(_vdiv())

        self._mute_btn = QPushButton("MUTE")
        self._mute_btn.setFixedSize(44, 20)
        self._mute_btn.setCheckable(True)
        self._mute_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {SEPARATOR}; "
            f"border: 1px solid {SEPARATOR}; font-size: 8px; font-weight: bold; "
            f"padding: 0px 4px; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
            f"QPushButton:checked {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        self._mute_btn.toggled.connect(
            lambda v: self.param_changed.emit(self._row, "mute", v)
        )

        self._stop_btn = QPushButton("STOP")
        self._stop_btn.setFixedSize(36, 20)
        self._stop_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {SEPARATOR}; "
            f"border: 1px solid {SEPARATOR}; font-size: 8px; font-weight: bold; "
            f"padding: 0px 2px; }}"
            f"QPushButton:hover {{ color: #ff6666; border-color: #ff6666; }}"
            f"QPushButton:pressed {{ background: #331111; color: #ff6666; }}"
        )
        self._stop_btn.setToolTip("Stop this channel")
        self._stop_btn.clicked.connect(
            lambda: self.param_changed.emit(self._row, "stop", True)
        )
        top_row.addWidget(_section("CONTROL", self._mute_btn, self._stop_btn))
        top_row.addWidget(_vdiv())

        layout.addLayout(top_row)

        # ── Waveform display (full width below controls) ──
        self._waveform = WaveformWidget()
        self._waveform.setMinimumHeight(60)
        self._waveform.selection_changed.connect(
            lambda s, e: self.param_changed.emit(self._row, "selection", (s, e))
        )
        layout.addWidget(self._waveform, stretch=1)

    def _on_load_click(self):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Load Sample — Channel {self._row + 1}",
            "", "Audio Files (*.wav *.aiff *.aif *.flac *.ogg);;All Files (*)"
        )
        if path:
            self._add_to_library(path)
            self.sample_load_requested.emit(self._row, path)

    def _show_sample_menu(self):
        """Show dropdown of all previously loaded samples plus a clear option."""
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {BG_PANEL}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {SEPARATOR}; }}"
            f"QMenu::item {{ padding: 4px 16px; }}"
            f"QMenu::item:selected {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        # Clear / no sample option
        clear_action = menu.addAction("(no sample)")
        clear_action.setData("__clear__")
        if ChannelStrip._sample_library:
            menu.addSeparator()
            for filepath in ChannelStrip._sample_library:
                import os
                name = os.path.basename(filepath)
                action = menu.addAction(name)
                action.setData(filepath)
        chosen = menu.exec(self._sample_name.mapToGlobal(
            self._sample_name.rect().bottomLeft()))
        if chosen:
            if chosen.data() == "__clear__":
                self.sample_clear_requested.emit(self._row)
            else:
                self.sample_load_requested.emit(self._row, chosen.data())

    @classmethod
    def _add_to_library(cls, filepath: str):
        """Add a filepath to the shared sample library (no duplicates)."""
        if filepath not in cls._sample_library:
            cls._sample_library.append(filepath)

    def _on_mode_changed(self, idx: int):
        """Handle mode combo change — show/hide PITCH-mode controls."""
        mode = PlaybackMode(idx)
        is_pitch = mode == PlaybackMode.PITCH
        # Show SCALE and ROOT sections only in PTCH mode
        self._scale_section.setVisible(is_pitch)
        self._pitch_mode_root.setVisible(is_pitch)
        # Hide standard PITCH (transpose) and STEP in PTCH mode
        self._pitch_section.setVisible(not is_pitch)
        self._steps_section.setVisible(not is_pitch)
        self.param_changed.emit(self._row, "mode", mode)

    def _select_group(self, idx: int):
        for i, btn in enumerate(self._group_btns):
            btn.setChecked(i == idx)
        self._on_group_changed(idx)

    def _on_group_changed(self, idx):
        color = GROUP_COLORS[idx % len(GROUP_COLORS)]
        self._group_bar.setStyleSheet(f"background: {color};")
        self._waveform.set_group_color(idx)
        self.param_changed.emit(self._row, "group", idx)

    def _on_speed_spin_changed(self, v):
        """Manual spinner edit — sync true speed to displayed value."""
        self._true_speed = v
        self.param_changed.emit(self._row, "speed", v)

    def _on_half_speed(self):
        """Halve the speed — doubles the playback length."""
        self._true_speed /= 2.0
        self._true_speed = max(self._speed_spin.minimum(),
                               min(self._speed_spin.maximum(), self._true_speed))
        clamped = self._true_speed
        self._speed_spin.blockSignals(True)
        self._speed_spin.setValue(clamped)
        self._speed_spin.blockSignals(False)
        self.param_changed.emit(self._row, "speed", clamped)

    def _on_double_speed(self):
        """Double the speed — halves the playback length."""
        self._true_speed *= 2.0
        self._true_speed = max(self._speed_spin.minimum(),
                               min(self._speed_spin.maximum(), self._true_speed))
        clamped = self._true_speed
        self._speed_spin.blockSignals(True)
        self._speed_spin.setValue(clamped)
        self._speed_spin.blockSignals(False)
        self.param_changed.emit(self._row, "speed", clamped)

    # --- Drag and drop ---

    _AUDIO_EXTENSIONS = {'.wav', '.aiff', '.aif', '.flac', '.ogg', '.mp3'}

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    from pathlib import Path
                    suffix = Path(url.toLocalFile()).suffix.lower()
                    if suffix in self._AUDIO_EXTENSIONS:
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                from pathlib import Path
                path = url.toLocalFile()
                suffix = Path(path).suffix.lower()
                if suffix in self._AUDIO_EXTENSIONS:
                    self._add_to_library(path)
                    self.sample_load_requested.emit(self._row, path)
                    event.acceptProposedAction()
                    return
        event.ignore()

    # --- State update methods ---

    def set_state(self, state: ChannelState):
        self._mode_combo.blockSignals(True)
        self._mode_combo.setCurrentIndex(int(state.mode))
        self._mode_combo.blockSignals(False)

        for i, btn in enumerate(self._group_btns):
            btn.setChecked(i == state.group)

        self._gain_fader.set_value(_gain_to_fader(state.gain))
        self._true_speed = state.speed
        self._speed_spin.blockSignals(True)
        self._speed_spin.setValue(state.speed)
        self._speed_spin.blockSignals(False)

        self._pitch_spin.blockSignals(True)
        self._pitch_spin.setValue(state.transpose)
        self._pitch_spin.blockSignals(False)

        self._steps_spin.blockSignals(True)
        self._steps_spin.setValue(state.steps)
        self._steps_spin.blockSignals(False)
        self._waveform.set_grid_columns(state.steps)
        # Lock steps to grid width in SEQ mode (SEQ always uses one step per column)
        self._steps_spin.setEnabled(state.mode not in (PlaybackMode.SEQ, PlaybackMode.PITCH))

        # PITCH mode controls
        self._pitch_scale_combo.blockSignals(True)
        self._pitch_scale_combo.setCurrentIndex(
            max(0, min(len(PITCH_SCALE_NAMES) - 1, state.pitch_scale))
        )
        self._pitch_scale_combo.blockSignals(False)
        self._pitch_root_spin.blockSignals(True)
        self._pitch_root_spin.setValue(state.pitch_root)
        self._pitch_root_spin.blockSignals(False)
        self._pitch_hold_btn.blockSignals(True)
        self._pitch_hold_btn.setChecked(state.pitch_hold)
        self._pitch_hold_btn.blockSignals(False)
        is_pitch = state.mode == PlaybackMode.PITCH
        self._scale_section.setVisible(is_pitch)
        self._pitch_mode_root.setVisible(is_pitch)
        self._pitch_section.setVisible(not is_pitch)
        self._steps_section.setVisible(not is_pitch)

        self._mute_btn.blockSignals(True)
        self._mute_btn.setChecked(state.mute)
        self._mute_btn.blockSignals(False)

        self._waveform.set_selection(state.sel_start, state.sel_end)
        self._waveform.set_group_color(state.group)
        self._waveform.set_playing(state.playing)
        self._on_group_changed(state.group)

    def set_waveform_data(self, data, filename=""):
        self._waveform.set_waveform(data, filename)
        self._sample_name.setText(filename)
        self._sample_name.setToolTip(filename or "Click to select from loaded samples")

    def set_playback_position(self, pos: float):
        self._waveform.set_playback_position(pos)

    def set_playing(self, playing: bool):
        self._waveform.set_playing(playing)

    def set_interactive(self, enabled: bool):
        """Enable or disable all interactive controls (used when audio is off)."""
        for w in (
            self._sample_name, self._load_btn, self._mode_combo,
            self._gain_fader, self._speed_spin, self._pitch_spin, self._steps_spin,
            self._half_btn, self._double_btn, self._mute_btn, self._stop_btn,
            self._waveform, self._pitch_scale_combo, self._pitch_root_spin,
            self._pitch_hold_btn,
        ):
            w.setEnabled(enabled)
        for btn in self._group_btns:
            btn.setEnabled(enabled)


class ChannelView(QWidget):
    """Scrollable list of channel strips — the main performance view."""

    sample_load_requested = Signal(int, str)
    sample_clear_requested = Signal(int)  # row
    param_changed = Signal(int, str, object)

    def __init__(self, num_rows: int = 7, parent=None):
        super().__init__(parent)
        self._strips: list[ChannelStrip] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self._strip_layout = QVBoxLayout(container)
        self._strip_layout.setContentsMargins(2, 2, 2, 2)
        self._strip_layout.setSpacing(1)

        for i in range(MAX_ROWS):
            strip = ChannelStrip(i)
            strip.sample_load_requested.connect(self.sample_load_requested.emit)
            strip.sample_clear_requested.connect(self.sample_clear_requested.emit)
            strip.param_changed.connect(self.param_changed.emit)
            self._strips.append(strip)
            self._strip_layout.addWidget(strip)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        self.set_visible_rows(num_rows)

        # Audio-disabled translucent overlay (floats above entire view)
        self._audio_overlay = QWidget(self)
        self._audio_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._audio_overlay.setStyleSheet("background: rgba(0, 0, 0, 170);")
        _ov_layout = QVBoxLayout(self._audio_overlay)
        _ov_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _ov_lbl = QLabel("AUDIO OFF")
        _ov_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _ov_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 14px; font-weight: bold;"
            "background: transparent; letter-spacing: 2px;"
        )
        _ov_layout.addWidget(_ov_lbl)
        self._audio_overlay.setVisible(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._audio_overlay.setGeometry(self.rect())

    def set_audio_enabled(self, enabled: bool):
        self._audio_overlay.setVisible(not enabled)
        if not enabled:
            self._audio_overlay.raise_()
        for strip in self._strips:
            strip.set_interactive(enabled)

    def set_visible_rows(self, count: int):
        for i, strip in enumerate(self._strips):
            strip.setVisible(i < count)

    def get_strip(self, row: int) -> ChannelStrip:
        return self._strips[row]
