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
from PySide6.QtCore import Qt, Signal, QMimeData, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QAction

from mlrvp.config import (
    ChannelState, PlaybackMode, NUM_GROUPS, MAX_ROWS, PITCH_SCALE_NAMES,
    LIVE_STATE_EMPTY, LIVE_STATE_COUNTING_IN, LIVE_STATE_RECORDING,
    LIVE_STATE_OVERDUBBING, LIVE_STATE_ARMED_RERECORD, LIVE_STATE_PLAYING,
    LIVE_STATE_PENDING, LIVE_STATE_ARMED,
    LIVE_QUANTIZE_NAMES, LIVE_SOURCE_NAMES, LIVE_AFTER_REC_NAMES, LIVE_PLAY_START_NAMES,
    LIVE_SOURCE_INPUT,
    LIVE_ARM_TRIG_NAMES, LIVE_ARM_TRIG_AUDIO,
    SEQ_STEP_DIV_NAMES, SEQ_STEP_DIV_DEFAULT,
)
from mlrvp.gui.styles import (
    GROUP_COLORS, BG_PANEL, BG_BLACK, BG_MAIN, SEPARATOR,
    ACCENT_GOLD, TEXT_PRIMARY, TEXT_SECONDARY,
    _ARROW_UP, _ARROW_DOWN,
)
from mlrvp.gui.widgets.waveform import WaveformWidget
from mlrvp.gui.widgets.fader import HorizontalFader, RotaryKnob


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
    live_param_changed = Signal(int, str, object)  # row, param, value (LIVE-mode params)
    live_rec_requested = Signal(int, bool)        # row, is_hold (False=click, True=2s hold)
    live_export_requested = Signal(int)           # row
    live_to_slicer_requested = Signal(int)        # row — send recorded buffer to Slicer tool

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
        self._current_group_idx = 0
        self._live_is_playing = False
        self._active_groups = NUM_GROUPS  # updated by set_active_groups()

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

        # Alphabetically sorted mode labels with their PlaybackMode values
        self._mode_map = [
            ("LOOP", PlaybackMode.LOOP),
            ("LIVE", PlaybackMode.LIVE),
            ("PTCH", PlaybackMode.PITCH),
            ("RVRS", PlaybackMode.REVERSE),
            ("SHOT", PlaybackMode.ONESHOT),
            ("SLCE", PlaybackMode.SLICE),
            ("SQNC", PlaybackMode.SEQ),
            ("STEP", PlaybackMode.STEP),
        ]
        self._mode_combo = QComboBox()
        self._mode_combo.addItems([label for label, _ in self._mode_map])
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
        # MODE combo + REV toggle for SHOT mode
        self._rev_btn = QPushButton("REV")
        self._rev_btn.setCheckable(True)
        self._rev_btn.setChecked(False)
        self._rev_btn.setFixedSize(30, 20)
        self._rev_btn.setToolTip("Reverse one-shot — play backwards from the triggered position")
        self._rev_btn.setStyleSheet(
            f"QPushButton {{ background: #1a1a1a; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; font-weight: bold; border: 1px solid {SEPARATOR}; "
            f"padding: 0px; }}"
            f"QPushButton:checked {{ background: {ACCENT_GOLD}; color: #111; "
            f"border: 1px solid {ACCENT_GOLD}; }}"
        )
        self._rev_btn.toggled.connect(self._on_reverse_toggled)
        self._rev_btn.setVisible(False)  # shown only in SHOT mode

        self._hold_btn = QPushButton("HOLD")
        self._hold_btn.setCheckable(True)
        self._hold_btn.setChecked(False)
        self._hold_btn.setFixedSize(34, 20)
        self._hold_btn.setToolTip("Hold — loop one-shot while grid button is held")
        self._hold_btn.setStyleSheet(
            f"QPushButton {{ background: #1a1a1a; color: {TEXT_SECONDARY}; "
            f"font-size: 8px; font-weight: bold; border: 1px solid {SEPARATOR}; "
            f"padding: 0px; }}"
            f"QPushButton:checked {{ background: {ACCENT_GOLD}; color: #111; "
            f"border: 1px solid {ACCENT_GOLD}; }}"
        )
        self._hold_btn.toggled.connect(self._on_hold_toggled)
        self._hold_btn.setVisible(False)  # shown only in SHOT mode
        top_row.addWidget(_section("MODE", self._mode_combo, self._rev_btn, self._hold_btn))
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
        self._gain_fader.setFixedWidth(80)
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
        self._pitch_spin.setFixedWidth(56)
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
        self._pitch_loop_btn = QPushButton("LOOP")
        self._pitch_loop_btn.setFixedSize(38, 20)
        self._pitch_loop_btn.setCheckable(True)
        self._pitch_loop_btn.setToolTip("Loop sample continuously on each pitch trigger")
        self._pitch_loop_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {SEPARATOR}; "
            f"border: 1px solid {SEPARATOR}; font-size: 8px; font-weight: bold; "
            f"padding: 0px 2px; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
            f"QPushButton:checked {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        self._pitch_loop_btn.toggled.connect(
            lambda v: self.param_changed.emit(self._row, "pitch_loop", v)
        )
        self._scale_section = _section("SCALE", self._pitch_scale_combo, self._pitch_loop_btn)
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

        # SEQ step-division combo — visible only in SEQ mode
        self._seq_div_combo = QComboBox()
        self._seq_div_combo.addItems(SEQ_STEP_DIV_NAMES)
        self._seq_div_combo.setCurrentIndex(SEQ_STEP_DIV_DEFAULT)
        self._seq_div_combo.setFixedHeight(20)
        self._seq_div_combo.setMaximumWidth(64)
        self._seq_div_combo.setToolTip("SEQ step rate: how long each step lasts")
        self._seq_div_combo.setStyleSheet(
            f"QComboBox {{ font-size: 10px; padding: 2px 4px; color: {TEXT_PRIMARY}; }}"
            f"QComboBox QAbstractItemView {{ min-width: 70px; }}"
        )
        self._seq_div_combo.currentIndexChanged.connect(
            lambda idx: self.param_changed.emit(self._row, "seq_step_div", idx)
        )
        self._seq_div_section = _section("DIV", self._seq_div_combo)
        self._seq_div_section.setVisible(False)
        top_row.addWidget(self._seq_div_section)

        top_row.addWidget(_vdiv())

        # --- FILTER section: per-channel HP + LP rotary knobs ---
        def _hz_label_lp(v):
            freq = 20000.0 * (200.0 / 20000.0) ** v
            return f"{int(round(freq))} Hz" if freq < 1000 else f"{freq/1000:.1f} kHz"

        def _hz_label_hp(v):
            freq = 20.0 * (8000.0 / 20.0) ** v
            return f"{int(round(freq))} Hz" if freq < 1000 else f"{freq/1000:.1f} kHz"

        self._ch_hp_knob = RotaryKnob(color="#ff9944", default_value=0.0)
        self._ch_hp_knob.setFixedSize(28, 28)
        self._ch_hp_knob.set_value(0.0)
        self._ch_hp_knob.setToolTip("High-pass filter: 0=open (20 Hz), 1=closed (8 kHz)")
        self._ch_hp_knob.set_formatter(_hz_label_hp)
        self._ch_hp_knob.value_changed.connect(
            lambda v: self.param_changed.emit(self._row, "ch_hp", v)
        )
        _hp_w = QWidget()
        _hp_vb = QVBoxLayout(_hp_w)
        _hp_vb.setContentsMargins(0, 0, 0, 0)
        _hp_vb.setSpacing(0)
        _hp_vb.addWidget(self._ch_hp_knob, alignment=Qt.AlignmentFlag.AlignCenter)
        _hp_sub = QLabel("HP")
        _hp_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _hp_sub.setFixedHeight(8)
        _hp_sub.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 7px; background: transparent;")
        _hp_vb.addWidget(_hp_sub)

        self._ch_lp_knob = RotaryKnob(color="#79b1ff", inverted=True, default_value=0.0, drag_inverted=True)
        self._ch_lp_knob.setFixedSize(28, 28)
        self._ch_lp_knob.set_value(0.0)
        self._ch_lp_knob.setToolTip("Low-pass filter: 0=open (20 kHz), 1=closed (200 Hz)")
        self._ch_lp_knob.set_formatter(_hz_label_lp)
        self._ch_lp_knob.value_changed.connect(
            lambda v: self.param_changed.emit(self._row, "ch_lp", v)
        )
        _lp_w = QWidget()
        _lp_vb = QVBoxLayout(_lp_w)
        _lp_vb.setContentsMargins(0, 0, 0, 0)
        _lp_vb.setSpacing(0)
        _lp_vb.addWidget(self._ch_lp_knob, alignment=Qt.AlignmentFlag.AlignCenter)
        _lp_sub = QLabel("LP")
        _lp_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _lp_sub.setFixedHeight(8)
        _lp_sub.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 7px; background: transparent;")
        _lp_vb.addWidget(_lp_sub)

        top_row.addWidget(_section("FILTER", _hp_w, _lp_w))
        top_row.addWidget(_vdiv())

        # --- PAN section: per-channel stereo pan knob ---
        def _pan_label(v):
            pct = int(round(abs(v - 0.5) * 200))
            if pct < 2:
                return "C"
            return f"{'R' if v > 0.5 else 'L'}{pct}"

        self._pan_knob = RotaryKnob(color="#aaddaa", default_value=0.5)
        self._pan_knob.setFixedSize(28, 28)
        self._pan_knob.set_value(0.5)
        self._pan_knob.setToolTip("Pan: left (L) to right (R), double-click to reset to centre")
        self._pan_knob.set_formatter(_pan_label)
        self._pan_knob.value_changed.connect(
            lambda v: self.param_changed.emit(self._row, "pan", v)
        )
        top_row.addWidget(_section("PAN", self._pan_knob))
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

        self._solo_btn = QPushButton("SOLO")
        self._solo_btn.setFixedSize(36, 20)
        self._solo_btn.setCheckable(True)
        self._solo_btn.setToolTip("Solo this channel — silences all other channels")
        self._solo_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {SEPARATOR}; "
            f"border: 1px solid {SEPARATOR}; font-size: 8px; font-weight: bold; "
            f"padding: 0px 2px; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
            f"QPushButton:checked {{ background: #79b1ff; color: {BG_BLACK}; "
            f"border: 1px solid #79b1ff; }}"
        )
        self._solo_btn.toggled.connect(
            lambda v: self.param_changed.emit(self._row, "solo", v)
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
        top_row.addWidget(_section("CONTROL", self._mute_btn, self._solo_btn, self._stop_btn))
        top_row.addWidget(_vdiv())

        # ── LIVE looper section (visible only in LIVE mode) ──
        self._live_section = self._build_live_section(_section, _vdiv)
        self._live_section.setVisible(False)
        top_row.addWidget(self._live_section)

        layout.addLayout(top_row)

        # ── LIVE config row (collapsible, between control row and waveform) ──
        self._live_config_row = self._build_live_config_row(_section, _vdiv)
        self._live_config_row.setVisible(False)
        layout.addWidget(self._live_config_row)
        # Wire CFG toggle → config row visibility (both widgets now exist)
        self._live_cfg_btn.toggled.connect(self._live_config_row.setVisible)

        # ── Waveform display (full width below controls) ──
        self._waveform = WaveformWidget()
        self._waveform.setMinimumHeight(60)
        self._waveform.selection_changed.connect(
            lambda s, e: self.param_changed.emit(self._row, "selection", (s, e))
        )
        layout.addWidget(self._waveform, stretch=1)

    def _build_live_section(self, _section, _vdiv) -> QWidget:
        """Build the compact LIVE-mode inline controls: only the CFG toggle.

        REC, STATE, and all settings now live in the collapsible config row
        built by _build_live_config_row() and wired in __init__.
        """
        container = QWidget()
        hl = QHBoxLayout(container)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(2)

        # CFG toggle — shows/hides the per-strip LIVE settings panel
        self._live_cfg_btn = QPushButton("CFG")
        self._live_cfg_btn.setCheckable(True)
        self._live_cfg_btn.setFixedSize(30, 20)
        self._live_cfg_btn.setToolTip(
            "Show / hide LIVE settings (REC, source, bars, quantize, options, trim, after-rec, play-start)"
        )
        self._live_cfg_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 8px; font-weight: bold; "
            f"padding: 0px; }}"
            f"QPushButton:checked {{ background: #1a2000; color: {ACCENT_GOLD}; "
            f"border: 1px solid {ACCENT_GOLD}; }}"
        )
        hl.addWidget(_section("LIVE", self._live_cfg_btn))
        hl.addWidget(_vdiv())

        return container

    def _build_live_config_row(self, _section, _vdiv) -> QWidget:
        """Build the unified LIVE/SAMP settings panel (single collapsible row).

        Order: REC, STATE, SRC, TRIM (input only), BARS, QNTZ, OPTS, CNTI/NORM,
        AFTR, STRT, FADE, EXP. TRIM is hidden when SRC != INPUT (bus sources
        have no trim — group/master volume already controls level).
        """
        row = QWidget()
        row.setObjectName("liveConfigRow")
        row.setStyleSheet(
            f"QWidget#liveConfigRow {{ background: {BG_BLACK}; "
            f"border-bottom: 1px solid {SEPARATOR}; }}"
        )
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 1, 6, 1)
        hl.setSpacing(2)
        hl.addWidget(_vdiv())

        # ── REC button (moved from inline LIVE section) ──
        self._live_rec_btn = QPushButton("●REC")
        self._live_rec_btn.setFixedSize(40, 20)
        self._live_rec_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: #ff6666; "
            f"border: 1px solid #ff6666; font-size: 9px; font-weight: bold; "
            f"padding: 0px; }}"
            f"QPushButton:hover {{ background: #221111; }}"
            f"QPushButton:pressed {{ background: #441111; }}"
        )
        self._live_rec_btn.setToolTip(
            "Click: start/cancel recording (input or bus capture per SRC)\n"
            "Hold 2s while playing: arm re-record"
        )
        self._live_rec_held = False
        self._live_rec_hold_timer = QTimer(self)
        self._live_rec_hold_timer.setSingleShot(True)
        self._live_rec_hold_timer.timeout.connect(self._on_live_rec_hold_fired)
        self._live_rec_btn.pressed.connect(self._on_live_rec_pressed)
        self._live_rec_btn.released.connect(self._on_live_rec_released)
        hl.addWidget(_section("REC", self._live_rec_btn))
        hl.addWidget(_vdiv())

        # ── STATE badge (moved from inline LIVE section) ──
        self._live_status_label = QLabel("EMPTY")
        self._live_status_label.setFixedHeight(20)
        self._live_status_label.setFixedWidth(52)
        self._live_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._live_status_label.setStyleSheet(
            f"QLabel {{ color: {TEXT_SECONDARY}; background: {BG_BLACK}; "
            f"border: 1px solid {SEPARATOR}; font-size: 9px; font-weight: bold; "
            f"padding: 0px 4px; }}"
        )
        hl.addWidget(_section("STATE", self._live_status_label))
        hl.addWidget(_vdiv())

        # ── SRC: dropdown menu selecting capture source bus ──
        # A button that pops up a categorized QMenu (Input / Master / FX / Groups).
        # Group entries are filtered to the currently active group count via
        # set_active_groups() so users only see channels they can route to.
        self._live_src_btn = QPushButton(LIVE_SOURCE_NAMES[0])
        self._live_src_btn.setFixedSize(62, 20)
        self._live_src_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 9px; font-weight: bold; "
            f"padding: 0px 4px; text-align: center; }}"
            f"QPushButton:hover {{ border-color: {ACCENT_GOLD}; }}"
        )
        self._live_src_btn.setToolTip(
            "Capture source — INPUT records hardware input;\n"
            "MAIN / FX-A..D / GRP-N capture internal engine buses"
        )
        self._live_src_idx = 0
        self._live_src_btn.clicked.connect(self._on_live_src_clicked)
        hl.addWidget(_section("SRC", self._live_src_btn))
        hl.addWidget(_vdiv())

        # ── TRIG: what fires recording when ARMED (PAD or AUDIO threshold) ──
        self._live_trig_combo = QComboBox()
        self._live_trig_combo.addItems(LIVE_ARM_TRIG_NAMES)
        self._live_trig_combo.setFixedHeight(20)
        self._live_trig_combo.setFixedWidth(52)
        self._live_trig_combo.setStyleSheet(
            f"QComboBox {{ font-size: 9px; padding: 2px 4px; color: {TEXT_PRIMARY}; }}"
        )
        self._live_trig_combo.setToolTip(
            "PAD: a grid press triggers recording when ARMED\n"
            "AUDIO: source-bus level exceeds threshold triggers recording"
        )
        self._live_trig_combo.currentIndexChanged.connect(self._on_live_trig_changed)
        hl.addWidget(_section("TRIG", self._live_trig_combo))
        hl.addWidget(_vdiv())

        # ── THRSH: audio threshold in dBFS (hidden when TRIG=PAD) ──
        self._live_thrsh_spin = QSpinBox()
        self._live_thrsh_spin.setRange(-60, 0)
        self._live_thrsh_spin.setValue(-30)
        self._live_thrsh_spin.setSuffix("dB")
        self._live_thrsh_spin.setFixedWidth(60)
        self._live_thrsh_spin.setFixedHeight(20)
        self._live_thrsh_spin.setToolTip(
            "Audio trigger threshold (dBFS) \u2014 recording starts when source "
            "bus peak exceeds this level"
        )
        self._live_thrsh_spin.setStyleSheet(
            f"QSpinBox {{ font-size: 10px; color: {TEXT_PRIMARY}; "
            f"padding-left: 2px; background: {BG_BLACK}; border: 1px solid {SEPARATOR}; }}"
            f"QSpinBox::up-button {{ width: 14px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: top right; }}"
            f"QSpinBox::down-button {{ width: 14px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: bottom right; }}"
            f"QSpinBox::up-arrow {{ image: url({_ARROW_UP}); width: 8px; height: 5px; }}"
            f"QSpinBox::down-arrow {{ image: url({_ARROW_DOWN}); width: 8px; height: 5px; }}"
        )
        self._live_thrsh_spin.valueChanged.connect(
            lambda v: self.live_param_changed.emit(
                self._row, "arm_trig_thresh_db", float(v)
            )
        )
        self._live_thrsh_section = _section("THRSH", self._live_thrsh_spin)
        self._live_thrsh_vdiv = _vdiv()
        self._live_thrsh_section.setVisible(False)
        self._live_thrsh_vdiv.setVisible(False)
        hl.addWidget(self._live_thrsh_section)
        hl.addWidget(self._live_thrsh_vdiv)

        # ── TRIM (visible only when SRC=INPUT) ──
        self._live_trim_fader = HorizontalFader(default_value=0.5)  # 0.5 = 1.0 trim
        self._live_trim_fader.setFixedWidth(62)
        self._live_trim_fader.set_formatter(lambda pos: f"{pos * 2.0:.2f}x")
        self._live_trim_fader.value_changed.connect(
            lambda v: self.live_param_changed.emit(self._row, "input_trim", v * 2.0)
        )
        self._live_trim_section = _section("TRIM", self._live_trim_fader)
        self._live_trim_vdiv = _vdiv()
        hl.addWidget(self._live_trim_section)
        hl.addWidget(self._live_trim_vdiv)

        # ── BARS spin ──
        self._live_bars_spin = QSpinBox()
        self._live_bars_spin.setRange(1, 32)
        self._live_bars_spin.setValue(4)
        self._live_bars_spin.setFixedWidth(44)
        self._live_bars_spin.setFixedHeight(20)
        self._live_bars_spin.setToolTip("Loop length in bars")
        self._live_bars_spin.setStyleSheet(
            f"QSpinBox {{ font-size: 10px; color: {TEXT_PRIMARY}; "
            f"padding-left: 2px; background: {BG_BLACK}; border: 1px solid {SEPARATOR}; }}"
            f"QSpinBox::up-button {{ width: 14px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: top right; }}"
            f"QSpinBox::down-button {{ width: 14px; background: {BG_BLACK}; "
            f"border: none; border-left: 1px solid {SEPARATOR}; "
            f"subcontrol-origin: border; subcontrol-position: bottom right; }}"
            f"QSpinBox::up-arrow {{ image: url({_ARROW_UP}); width: 8px; height: 5px; }}"
            f"QSpinBox::down-arrow {{ image: url({_ARROW_DOWN}); width: 8px; height: 5px; }}"
        )
        self._live_bars_spin.valueChanged.connect(
            lambda v: self.live_param_changed.emit(self._row, "bars", v)
        )
        hl.addWidget(_section("BARS", self._live_bars_spin))
        hl.addWidget(_vdiv())

        # ── QUANTIZE combo ──
        self._live_quant_combo = QComboBox()
        self._live_quant_combo.addItems(LIVE_QUANTIZE_NAMES)
        self._live_quant_combo.setFixedHeight(20)
        self._live_quant_combo.setFixedWidth(70)
        self._live_quant_combo.setToolTip(
            "Record quantize: when to begin recording after pressing REC\n"
            "IMMEDIATE: start at once  |  BEAT: wait for next beat  |  BAR: wait for next bar"
        )
        self._live_quant_combo.setStyleSheet(
            f"QComboBox {{ font-size: 9px; padding: 2px 4px; color: {TEXT_PRIMARY}; }}"
        )
        self._live_quant_combo.currentIndexChanged.connect(
            lambda idx: self.live_param_changed.emit(self._row, "quantize", idx)
        )
        self._fit_combo_popup_width(self._live_quant_combo)
        hl.addWidget(_section("QNTZ", self._live_quant_combo))
        hl.addWidget(_vdiv())

        # ── THRU + XSELF (anti-feedback) checkboxes ──
        self._live_thru_cb = QCheckBox("THRU")
        self._live_xself_cb = QCheckBox("XSELF")
        for cb in (self._live_thru_cb, self._live_xself_cb):
            cb.setStyleSheet(
                f"QCheckBox {{ color: {TEXT_SECONDARY}; font-size: 9px; "
                f"background: transparent; }}"
            )
        self._live_thru_cb.setToolTip("Monitor input through the channel while recording")
        self._live_xself_cb.setToolTip(
            "Anti-feedback: subtract this row's audio from the captured bus"
        )
        self._live_xself_cb.setChecked(True)
        self._live_thru_cb.toggled.connect(
            lambda v: self.live_param_changed.emit(self._row, "thru", v)
        )
        self._live_xself_cb.toggled.connect(
            lambda v: self.live_param_changed.emit(self._row, "exclude_self", v)
        )
        hl.addWidget(_section("OPTS", self._live_thru_cb, self._live_xself_cb))
        hl.addWidget(_vdiv())

        # ── CNTI spinbox + NORM checkbox ──
        self._live_countin_spin = QSpinBox()
        self._live_countin_spin.setRange(0, 8)
        self._live_countin_spin.setValue(0)
        self._live_countin_spin.setFixedWidth(44)
        self._live_countin_spin.setFixedHeight(20)
        self._live_countin_spin.setToolTip("Count-in bars before recording starts (0 = none)")
        self._live_countin_spin.setStyleSheet(self._live_bars_spin.styleSheet())
        # CNTI is stored as bool (count_in) in LiveLoopState; treat any value > 0 as True.
        self._live_countin_spin.valueChanged.connect(
            lambda v: self.live_param_changed.emit(self._row, "count_in", bool(v))
        )
        self._live_norm_cb = QCheckBox("NORM")
        self._live_norm_cb.setStyleSheet(
            f"QCheckBox {{ color: {TEXT_SECONDARY}; font-size: 9px; "
            f"background: transparent; }}"
        )
        self._live_norm_cb.setToolTip("Peak-normalize the captured buffer at finalize")
        self._live_norm_cb.setChecked(True)
        self._live_norm_cb.toggled.connect(
            lambda v: self.live_param_changed.emit(self._row, "normalize", v)
        )
        hl.addWidget(_section("CNTI/NORM", self._live_countin_spin, self._live_norm_cb))
        hl.addWidget(_vdiv())

        # ── AFTR: after-record handoff policy ──
        self._live_aftr_combo = QComboBox()
        self._live_aftr_combo.addItems(LIVE_AFTER_REC_NAMES)
        self._live_aftr_combo.setCurrentIndex(2)  # REPL+STP default
        self._live_aftr_combo.setFixedHeight(20)
        self._live_aftr_combo.setFixedWidth(58)
        self._live_aftr_combo.setStyleSheet(self._live_quant_combo.styleSheet())
        self._live_aftr_combo.setToolTip(
            "After recording: LAYER (keep sources), REPLACE (mute sources),\n"
            "REPL+STP (stop sources), MANUAL (don't auto-trigger)"
        )
        self._live_aftr_combo.currentIndexChanged.connect(
            lambda idx: self.live_param_changed.emit(self._row, "after_rec", idx)
        )
        self._fit_combo_popup_width(self._live_aftr_combo)
        hl.addWidget(_section("AFTR", self._live_aftr_combo))
        hl.addWidget(_vdiv())

        # ── STRT: play-start quantize ──
        self._live_strt_combo = QComboBox()
        self._live_strt_combo.addItems(LIVE_PLAY_START_NAMES)
        self._live_strt_combo.setCurrentIndex(0)  # IMMED default
        self._live_strt_combo.setFixedHeight(20)
        self._live_strt_combo.setFixedWidth(58)
        self._live_strt_combo.setStyleSheet(self._live_quant_combo.styleSheet())
        self._live_strt_combo.setToolTip(
            "Play-start quantize for post-record trigger"
        )
        self._live_strt_combo.currentIndexChanged.connect(
            lambda idx: self.live_param_changed.emit(self._row, "play_start", idx)
        )
        self._fit_combo_popup_width(self._live_strt_combo)
        hl.addWidget(_section("STRT", self._live_strt_combo))
        hl.addWidget(_vdiv())

        # ── FADE: fade-in/out ms applied at finalize ──
        self._live_fade_spin = QSpinBox()
        self._live_fade_spin.setRange(0, 50)
        self._live_fade_spin.setValue(10)
        self._live_fade_spin.setSuffix("ms")
        self._live_fade_spin.setFixedWidth(50)
        self._live_fade_spin.setFixedHeight(20)
        self._live_fade_spin.setToolTip("Fade-in / fade-out applied to the captured buffer")
        self._live_fade_spin.setStyleSheet(self._live_bars_spin.styleSheet())
        self._live_fade_spin.valueChanged.connect(
            lambda v: self.live_param_changed.emit(self._row, "fade_ms", v)
        )
        hl.addWidget(_section("FADE", self._live_fade_spin))
        hl.addWidget(_vdiv())

        # ── EXPORT button ──
        self._live_export_btn = QPushButton("EXP")
        self._live_export_btn.setFixedSize(28, 20)
        self._live_export_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 9px; font-weight: bold; "
            f"padding: 0px; }}"
            f"QPushButton:hover {{ color: {ACCENT_GOLD}; border-color: {ACCENT_GOLD}; }}"
        )
        self._live_export_btn.setToolTip(
            "Export current loop \u2014 prompts for a name, saves to data/slices/Live/"
        )
        self._live_export_btn.clicked.connect(
            lambda: self.live_export_requested.emit(self._row)
        )
        hl.addWidget(_section("EXP", self._live_export_btn))
        hl.addWidget(_vdiv())

        # ── SLC: send current loop to the Slicer tool ──
        self._live_to_slicer_btn = QPushButton("SLC")
        self._live_to_slicer_btn.setFixedSize(28, 20)
        self._live_to_slicer_btn.setStyleSheet(self._live_export_btn.styleSheet())
        self._live_to_slicer_btn.setToolTip(
            "Send the current loop to the Slicer tool (auto-saves a copy in data/slices/Live/)"
        )
        self._live_to_slicer_btn.clicked.connect(
            lambda: self.live_to_slicer_requested.emit(self._row)
        )
        hl.addWidget(_section("SLC", self._live_to_slicer_btn))
        hl.addWidget(_vdiv())

        hl.addStretch()
        return row

    def _on_live_src_clicked(self):
        """SRC button clicked — pop up the categorized source menu."""
        from mlrvp.config import (
            LIVE_SOURCE_MAIN, LIVE_SOURCE_FX_BASE, LIVE_SOURCE_GRP_BASE,
        )
        menu = QMenu(self._live_src_btn)
        menu.setStyleSheet(
            f"QMenu {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 10px; padding: 2px; }}"
            f"QMenu::item {{ padding: 3px 18px 3px 18px; }}"
            f"QMenu::item:selected {{ background: #1a2000; color: {ACCENT_GOLD}; }}"
            f"QMenu::separator {{ height: 1px; background: {SEPARATOR}; "
            f"margin: 2px 4px; }}"
        )

        def _add(parent, label, src_idx):
            act = parent.addAction(label)
            if src_idx == self._live_src_idx:
                act.setCheckable(True)
                act.setChecked(True)
            act.triggered.connect(
                lambda _checked=False, i=src_idx: self._apply_live_src_idx(i, emit=True)
            )

        # Hardware input
        _add(menu, "INPUT (hardware)", LIVE_SOURCE_INPUT)
        menu.addSeparator()
        # Master
        _add(menu, "MAIN (master out)", LIVE_SOURCE_MAIN)
        menu.addSeparator()

        # FX submenu
        fx_menu = menu.addMenu("FX bus")
        fx_menu.setStyleSheet(menu.styleSheet())
        for i, name in enumerate(("FX-A", "FX-B", "FX-C", "FX-D")):
            _add(fx_menu, name, LIVE_SOURCE_FX_BASE + i)

        # Groups submenu — only the active groups are listed
        grp_menu = menu.addMenu("Group bus")
        grp_menu.setStyleSheet(menu.styleSheet())
        active = max(1, min(NUM_GROUPS, int(self._active_groups)))
        for g in range(active):
            _add(grp_menu, f"GRP-{g + 1}", LIVE_SOURCE_GRP_BASE + g)

        menu.exec(self._live_src_btn.mapToGlobal(
            self._live_src_btn.rect().bottomLeft()
        ))

    def _apply_live_src_idx(self, idx: int, emit: bool):
        """Update the SRC button label, TRIM visibility, and emit the change."""
        idx = max(0, min(len(LIVE_SOURCE_NAMES) - 1, int(idx)))
        self._live_src_idx = idx
        self._live_src_btn.setText(LIVE_SOURCE_NAMES[idx])
        is_input = (idx == LIVE_SOURCE_INPUT)
        # Color-code input vs bus-capture so it's obvious at a glance
        accent = TEXT_PRIMARY if is_input else "#88ccff"
        self._live_src_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {accent}; "
            f"border: 1px solid {accent if not is_input else SEPARATOR}; "
            f"font-size: 9px; font-weight: bold; padding: 0px 4px; "
            f"text-align: center; }}"
            f"QPushButton:hover {{ border-color: {ACCENT_GOLD}; }}"
        )
        if hasattr(self, "_live_trim_section"):
            self._live_trim_section.setVisible(is_input)
            self._live_trim_vdiv.setVisible(is_input)
        if emit:
            self.live_param_changed.emit(self._row, "source_bus", idx)

    def _on_live_trig_changed(self, idx: int):
        """TRIG combo changed — show/hide THRSH and emit param."""
        is_audio = (idx == LIVE_ARM_TRIG_AUDIO)
        if hasattr(self, "_live_thrsh_section"):
            self._live_thrsh_section.setVisible(is_audio)
            self._live_thrsh_vdiv.setVisible(is_audio)
        self.live_param_changed.emit(self._row, "arm_trig", idx)

    @staticmethod
    def _fit_combo_popup_width(combo: QComboBox):
        """Make the combo's popup wide enough to show the longest item.

        QComboBox truncates long items with '...' if the popup view inherits
        the body width.  Compute the longest item's pixel width using the
        combo's current font metrics and apply it to the view.
        """
        fm = combo.fontMetrics()
        longest = 0
        for i in range(combo.count()):
            longest = max(longest, fm.horizontalAdvance(combo.itemText(i)))
        # Add room for padding + scroll bar margin
        combo.view().setMinimumWidth(longest + 28)

    def _on_live_rec_pressed(self):
        """REC button pressed — start hold-detection timer."""
        self._live_rec_held = False
        self._live_rec_hold_timer.start(2000)  # 2 seconds

    def _on_live_rec_hold_fired(self):
        """2-second hold detected — emit hold signal and mark held."""
        self._live_rec_held = True
        self.live_rec_requested.emit(self._row, True)

    def _on_live_rec_released(self):
        """REC button released — emit click signal if not already fired as hold."""
        self._live_rec_hold_timer.stop()
        if not self._live_rec_held:
            self.live_rec_requested.emit(self._row, False)
        self._live_rec_held = False

    def set_live_loop_state(self, loop_state: str):
        """Update the LIVE status badge, REC button color, and group bar from app."""
        labels = {
            LIVE_STATE_EMPTY: ("EMPTY", TEXT_SECONDARY, BG_BLACK),
            LIVE_STATE_COUNTING_IN: ("CNT-IN", "#ffcc44", BG_BLACK),
            LIVE_STATE_RECORDING: ("REC", "#ff4444", "#330000"),
            LIVE_STATE_OVERDUBBING: ("OVERDUB", "#ff8844", "#331100"),
            LIVE_STATE_ARMED_RERECORD: ("RE-ARM", "#ffcc44", "#332200"),
            LIVE_STATE_PLAYING: ("PLAY", "#44ff44", BG_BLACK),
            LIVE_STATE_PENDING: ("WAIT", "#ffaa44", "#332200"),
            LIVE_STATE_ARMED: ("ARM", "#ff4444", "#220000"),
        }
        label, fg, bg = labels.get(loop_state, ("?", TEXT_SECONDARY, BG_BLACK))
        if hasattr(self, "_live_status_label"):
            self._live_status_label.setText(label)
            self._live_status_label.setStyleSheet(
                f"QLabel {{ color: {fg}; background: {bg}; "
                f"border: 1px solid {fg}; font-size: 9px; font-weight: bold; "
                f"padding: 0px 4px; }}"
            )
        self._live_is_playing = (loop_state == LIVE_STATE_PLAYING)
        self._refresh_group_bar()

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
        import os
        from mlrvp.gui.widgets.overflow_menu import make_menu, populate_menu, MENU_QSS

        menu = make_menu(self)

        # "No sample" always at the top, before any grouping.
        clear_action = menu.addAction("(no sample)")
        clear_action.setData("__clear__")

        if ChannelStrip._sample_library:
            menu.addSeparator()
            items = [
                (os.path.basename(fp), fp)
                for fp in ChannelStrip._sample_library
            ]
            populate_menu(menu, items,
                          on_select=lambda fp: self.sample_load_requested.emit(
                              self._row, fp))

        chosen = menu.exec(self._sample_name.mapToGlobal(
            self._sample_name.rect().bottomLeft()))
        if chosen and chosen.data() == "__clear__":
            self.sample_clear_requested.emit(self._row)

    @classmethod
    def _add_to_library(cls, filepath: str):
        """Add a filepath to the shared sample library (no duplicates)."""
        if filepath not in cls._sample_library:
            cls._sample_library.append(filepath)

    def _on_mode_changed(self, idx: int):
        """Handle mode combo change — show/hide mode-specific controls."""
        _, mode = self._mode_map[idx]
        is_pitch = mode == PlaybackMode.PITCH
        is_seq = mode == PlaybackMode.SEQ
        is_shot = mode == PlaybackMode.ONESHOT
        is_live = mode == PlaybackMode.LIVE
        # Show SCALE and ROOT sections only in PTCH mode
        self._scale_section.setVisible(is_pitch)
        self._pitch_mode_root.setVisible(is_pitch)
        # Hide standard PITCH (transpose) and STEP in PTCH mode
        self._pitch_section.setVisible(not is_pitch)
        # STEP replaced by DIV in SEQ mode
        self._steps_section.setVisible(not is_pitch and not is_seq)
        self._seq_div_section.setVisible(is_seq)
        # REV toggle only in SHOT mode
        self._rev_btn.setVisible(is_shot)
        self._hold_btn.setVisible(is_shot)
        # LIVE section only in LIVE mode; auto-expand config row on entry, collapse on exit
        if hasattr(self, "_live_section"):
            self._live_section.setVisible(is_live)
        if hasattr(self, "_live_config_row"):
            if is_live:
                self._live_cfg_btn.blockSignals(True)
                self._live_cfg_btn.setChecked(True)
                self._live_cfg_btn.blockSignals(False)
                self._live_config_row.setVisible(True)
            else:
                self._live_cfg_btn.blockSignals(True)
                self._live_cfg_btn.setChecked(False)
                self._live_cfg_btn.blockSignals(False)
                self._live_config_row.setVisible(False)
        if not is_shot:
            self._rev_btn.blockSignals(True)
            self._rev_btn.setChecked(False)
            self._rev_btn.blockSignals(False)
            self._hold_btn.blockSignals(True)
            self._hold_btn.setChecked(False)
            self._hold_btn.blockSignals(False)
            self.param_changed.emit(self._row, "reverse", False)
            self.param_changed.emit(self._row, "shot_hold", False)
        self.param_changed.emit(self._row, "mode", mode)

    def _on_reverse_toggled(self, checked: bool):
        self.param_changed.emit(self._row, "reverse", checked)

    def _on_hold_toggled(self, checked: bool):
        self.param_changed.emit(self._row, "shot_hold", checked)

    def _select_group(self, idx: int):
        for i, btn in enumerate(self._group_btns):
            btn.setChecked(i == idx)
        self._on_group_changed(idx)

    def set_active_groups(self, n: int):
        """Show/hide group selector buttons based on active group count."""
        self._active_groups = max(1, min(NUM_GROUPS, int(n)))
        for i, btn in enumerate(self._group_btns):
            btn.setVisible(i < n)

    def _on_group_changed(self, idx):
        self._current_group_idx = idx
        self._refresh_group_bar()
        self._waveform.set_group_color(idx)
        self.param_changed.emit(self._row, "group", idx)

    def _refresh_group_bar(self):
        """Apply the group bar style, lit up when the live loop is playing."""
        color = GROUP_COLORS[self._current_group_idx % len(GROUP_COLORS)]
        if self._live_is_playing:
            self._group_bar.setFixedWidth(6)
            self._group_bar.setStyleSheet(
                f"background: {color}; border-right: 2px solid #ffffff;"
            )
        else:
            self._group_bar.setFixedWidth(4)
            self._group_bar.setStyleSheet(f"background: {color};")

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
        combo_idx = next(i for i, (_, m) in enumerate(self._mode_map) if m == state.mode)
        self._mode_combo.setCurrentIndex(combo_idx)
        self._mode_combo.blockSignals(False)

        # REV toggle — visible only in SHOT mode
        is_shot = state.mode == PlaybackMode.ONESHOT
        self._rev_btn.blockSignals(True)
        self._rev_btn.setChecked(state.reverse and is_shot)
        self._rev_btn.blockSignals(False)
        self._rev_btn.setVisible(is_shot)

        # HOLD toggle — visible only in SHOT mode
        self._hold_btn.blockSignals(True)
        self._hold_btn.setChecked(state.shot_hold and is_shot)
        self._hold_btn.blockSignals(False)
        self._hold_btn.setVisible(is_shot)

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

        # SEQ step-division combo — replaces STEP in SEQ mode
        is_seq = state.mode == PlaybackMode.SEQ
        is_pitch = state.mode == PlaybackMode.PITCH
        self._seq_div_combo.blockSignals(True)
        self._seq_div_combo.setCurrentIndex(
            max(0, min(len(SEQ_STEP_DIV_NAMES) - 1, state.seq_step_div))
        )
        self._seq_div_combo.blockSignals(False)
        # DIV visible only in SEQ; STEP visible in all other non-PITCH modes
        self._seq_div_section.setVisible(is_seq)
        self._steps_section.setVisible(not is_pitch and not is_seq)
        # STEP spinner enabled only when visible (not locked by SEQ/PITCH)
        self._steps_spin.setEnabled(not is_pitch and not is_seq)

        # LIVE mode controls — sync from state.live
        is_live = state.mode == PlaybackMode.LIVE
        if hasattr(self, "_live_section"):
            self._live_section.setVisible(is_live)
            live = state.live
            self._live_bars_spin.blockSignals(True)
            self._live_bars_spin.setValue(int(live.bars))
            self._live_bars_spin.blockSignals(False)
            self._live_quant_combo.blockSignals(True)
            self._live_quant_combo.setCurrentIndex(
                max(0, min(len(LIVE_QUANTIZE_NAMES) - 1, int(live.quantize)))
            )
            self._live_quant_combo.blockSignals(False)
            self._live_thru_cb.blockSignals(True)
            self._live_thru_cb.setChecked(bool(live.thru))
            self._live_thru_cb.blockSignals(False)
            # CNTI is now a 0-8 spinbox: 0 = off, >0 = on (state model is bool)
            self._live_countin_spin.blockSignals(True)
            self._live_countin_spin.setValue(int(live.count_in))
            self._live_countin_spin.blockSignals(False)
            self._live_trim_fader.set_value(
                max(0.0, min(1.0, float(live.input_trim) / 2.0))
            )
            # SAMP / resample fields
            self._apply_live_src_idx(int(live.source_bus), emit=False)
            self._live_aftr_combo.blockSignals(True)
            self._live_aftr_combo.setCurrentIndex(
                max(0, min(len(LIVE_AFTER_REC_NAMES) - 1, int(live.after_rec)))
            )
            self._live_aftr_combo.blockSignals(False)
            self._live_strt_combo.blockSignals(True)
            self._live_strt_combo.setCurrentIndex(
                max(0, min(len(LIVE_PLAY_START_NAMES) - 1, int(live.play_start)))
            )
            self._live_strt_combo.blockSignals(False)
            self._live_norm_cb.blockSignals(True)
            self._live_norm_cb.setChecked(bool(live.normalize))
            self._live_norm_cb.blockSignals(False)
            self._live_xself_cb.blockSignals(True)
            self._live_xself_cb.setChecked(bool(live.exclude_self))
            self._live_xself_cb.blockSignals(False)
            self._live_fade_spin.blockSignals(True)
            self._live_fade_spin.setValue(int(live.fade_ms))
            self._live_fade_spin.blockSignals(False)
            # Trigger mode + threshold
            self._live_trig_combo.blockSignals(True)
            self._live_trig_combo.setCurrentIndex(
                max(0, min(len(LIVE_ARM_TRIG_NAMES) - 1, int(live.arm_trig)))
            )
            self._live_trig_combo.blockSignals(False)
            self._on_live_trig_changed(int(live.arm_trig))
            self._live_thrsh_spin.blockSignals(True)
            self._live_thrsh_spin.setValue(int(live.arm_trig_thresh_db))
            self._live_thrsh_spin.blockSignals(False)
            self.set_live_loop_state(str(live.loop_state))

        self._mute_btn.blockSignals(True)
        self._mute_btn.setChecked(state.mute)
        self._mute_btn.blockSignals(False)

        self._solo_btn.blockSignals(True)
        self._solo_btn.setChecked(state.solo)
        self._solo_btn.blockSignals(False)

        self._ch_lp_knob.set_value(state.ch_lp)
        self._ch_hp_knob.set_value(state.ch_hp)
        self._pan_knob.set_value(state.pan)

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
            self._pitch_loop_btn, self._rev_btn, self._hold_btn,
            self._ch_hp_knob, self._ch_lp_knob, self._pan_knob, self._solo_btn,
            self._seq_div_combo,
        ):
            w.setEnabled(enabled)
        for btn in self._group_btns:
            btn.setEnabled(enabled)


class ChannelView(QWidget):
    """Scrollable list of channel strips — the main performance view."""

    sample_load_requested = Signal(int, str)
    sample_clear_requested = Signal(int)  # row
    param_changed = Signal(int, str, object)
    live_param_changed = Signal(int, str, object)
    live_rec_requested = Signal(int, bool)
    live_export_requested = Signal(int)
    live_to_slicer_requested = Signal(int)

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
            strip.live_param_changed.connect(self.live_param_changed.emit)
            strip.live_rec_requested.connect(self.live_rec_requested.emit)
            strip.live_export_requested.connect(self.live_export_requested.emit)
            strip.live_to_slicer_requested.connect(self.live_to_slicer_requested.emit)
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

    def set_active_groups(self, n: int):
        """Propagate active group count to all channel strips."""
        for strip in self._strips:
            strip.set_active_groups(n)

    def get_strip(self, row: int) -> ChannelStrip:
        if 0 <= row < len(self._strips):
            return self._strips[row]
        return self._strips[0]
