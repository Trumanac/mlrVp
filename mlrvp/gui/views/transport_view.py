"""Transport bar — tempo, quantize, pattern recorders, presets in mlrVp.

Styled to match the original mlrV2 time.maxpat / pttrn.maxpat / preset.maxpat
controls: dark background, golden-yellow active states, compact layout.
"""

from PySide6.QtCore import Qt, Signal, QTimer, QPoint
from PySide6.QtGui import QPainter, QColor, QPolygon
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QComboBox, QFrame, QScrollArea
)
import time

from mlrvp.config import NUM_PATTERN_RECORDERS, NUM_GROUPS
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size, bind_fixed_width
from mlrvp.gui.styles import (
    ACCENT_GOLD, BG_BLACK, BG_PANEL, SEPARATOR, TEXT_SECONDARY, TEXT_PRIMARY,
    GROUP_COLORS, LOGO_MAGENTA,
    STATUS_RECORDING, STATUS_SUCCESS, STATUS_LEARNING_BG,
    STATUS_GRID_MODE, STATUS_GRID_MODE_BRIGHT, STATUS_GRID_MODE_HOVER, STATUS_GRID_MODE_PRESSED,
)


class _TransportLogo(QWidget):
    """Small parallelogram logo for the transport bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 50, 30)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(LOGO_MAGENTA))
        sx = self.width() / 55
        sy = self.height() / 32
        poly = QPolygon([
            QPoint(int(18 * sx), int(9 * sy)),
            QPoint(int(53 * sx), int(9 * sy)),
            QPoint(int(43 * sx), int(30 * sy)),
            QPoint(int(8 * sx), int(30 * sy)),
        ])
        p.drawPolygon(poly)
        p.end()


class PatternButton(QWidget):
    """Tri-state pattern recorder with bar-length up/down controls.

    Layout: [▼] [P1: 4] [▲]  — click label to cycle idle→recording→playing.
    Hold label 3 seconds to erase (clear) the pattern.
    Up/down arrows change bar count freely between 1 and 32 bars.
    Default starting values are 4, 8, 16, 32 for P1–P4."""

    state_changed = Signal(int, str)  # idx, state: 'idle'|'recording'|'playing'|'clear'
    length_changed = Signal(int, int)  # idx, new_length

    MIN_BARS = 1
    MAX_BARS = 32

    def __init__(self, idx: int, length: int, parent=None):
        super().__init__(parent)
        self._idx = idx
        self._length = length
        self._state = "idle"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        # Down arrow
        self._down_btn = QPushButton("-")
        bind_fixed_size(self._down_btn, 16, 36)
        self._down_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 12px; font-weight: bold; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
        )
        self._down_btn.clicked.connect(self._dec_length)
        self._down_btn.setToolTip("Decrease bar count")
        layout.addWidget(self._down_btn)

        # Main label button
        self._label_btn = QPushButton(f"P{idx+1}:{length}")
        bind_fixed_size(self._label_btn, 44, 36)
        self._label_btn.setCheckable(False)
        self._label_btn.setToolTip(
            "Click to cycle: idle → recording → playing\n"
            "Hold 3 seconds to clear the pattern"
        )
        self._label_btn.clicked.connect(self._cycle_state)
        layout.addWidget(self._label_btn)

        # Up arrow
        self._up_btn = QPushButton("+")
        bind_fixed_size(self._up_btn, 16, 36)
        self._up_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 12px; font-weight: bold; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
        )
        self._up_btn.clicked.connect(self._inc_length)
        self._up_btn.setToolTip("Increase bar count")
        layout.addWidget(self._up_btn)

        self._update_style()

        # Long-press to clear
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(3000)
        self._hold_timer.timeout.connect(self._on_hold_clear)
        self._held = False
        self._label_btn.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._label_btn:
            from PySide6.QtCore import QEvent
            if event.type() == QEvent.Type.MouseButtonPress:
                self._held = False
                self._hold_timer.start()
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._hold_timer.stop()
                if self._held:
                    self._held = False
                    return True  # absorb the click
        return super().eventFilter(obj, event)

    def _on_hold_clear(self):
        self._held = True
        self._state = "idle"
        self._update_style()
        self.state_changed.emit(self._idx, "clear")

    def _cycle_state(self):
        if self._state == "idle":
            self._state = "recording"
        elif self._state == "recording":
            self._state = "playing"
        else:
            self._state = "idle"
        self._update_style()
        self.state_changed.emit(self._idx, self._state)

    def _inc_length(self):
        self._length = min(self._length + 1, self.MAX_BARS)
        self._update_label()
        self.length_changed.emit(self._idx, self._length)

    def _dec_length(self):
        self._length = max(self._length - 1, self.MIN_BARS)
        self._update_label()
        self.length_changed.emit(self._idx, self._length)

    def _update_label(self):
        self._label_btn.setText(f"P{self._idx+1}:{self._length}")

    def set_state(self, state: str):
        self._state = state
        self._update_style()

    def _update_style(self):
        if self._state == "recording":
            self._label_btn.setStyleSheet(
                f"QPushButton {{ background: {STATUS_RECORDING}; color: white; "
                f"border: 1px solid {SEPARATOR}; border-radius: 0px; font-size: 9px; font-weight: bold; }}"
            )
        elif self._state == "playing":
            self._label_btn.setStyleSheet(
                f"QPushButton {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; "
                f"border: 1px solid {ACCENT_GOLD}; border-radius: 0px; font-size: 9px; font-weight: bold; }}"
            )
        else:
            self._label_btn.setStyleSheet(
                f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
                f"border: 1px solid {SEPARATOR}; border-radius: 0px; font-size: 9px; font-weight: bold; }}"
            )


class _ClickableCombo(QComboBox):
    """QComboBox that opens its dropdown when the line-edit area is clicked."""

    def __init__(self, parent=None):
        super().__init__(parent)

    def setEditable(self, editable):
        super().setEditable(editable)
        if editable and self.lineEdit():
            self.lineEdit().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.lineEdit() and event.type() == event.Type.MouseButtonPress:
            if self.count() > 0:
                self.showPopup()
            return True
        return super().eventFilter(obj, event)


class _GridIconButton(QPushButton):
    """Square button painted as a 2\u00d72 grid of cells (like a window with a cross).

    Yellow when checked (grid visible), dim when unchecked.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlat(True)
        self._update_style()

    def _update_style(self):
        if self.isChecked():
            bg = STATUS_LEARNING_BG
            border = ACCENT_GOLD
        else:
            bg = BG_BLACK
            border = SEPARATOR
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; border: 1px solid {border}; padding: 0; }}"
            f"QPushButton:hover {{ border: 1px solid {ACCENT_GOLD}; }}"
        )

    def nextCheckState(self):
        self.setChecked(not self.isChecked())
        self._update_style()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        color = QColor(ACCENT_GOLD) if self.isChecked() else QColor(TEXT_SECONDARY)
        p.setPen(color)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Outer rect (inset 5px)
        m = 5
        w, h = self.width(), self.height()
        p.drawRect(m, m, w - m * 2 - 1, h - m * 2 - 1)
        # Cross through the middle — 4 cells like a window pane
        cx, cy = w // 2, h // 2
        p.drawLine(cx, m, cx, h - m - 1)   # vertical divider
        p.drawLine(m, cy, w - m - 1, cy)   # horizontal divider
        p.end()


class TransportView(QWidget):
    """Transport bar with tempo, quantize, pattern recorders, and preset selector."""

    tempo_changed = Signal(float)
    quantize_changed = Signal(int)
    pattern_state_changed = Signal(int, str)
    pattern_length_changed = Signal(int, int)  # idx, new_length
    # Signals for the redesigned preset model
    preset_save_requested = Signal(str)        # preset name to save
    preset_recall_requested = Signal(int)      # setlist index
    preset_prev_requested = Signal()
    preset_next_requested = Signal()
    preset_new_requested = Signal()               # clear/reset to defaults
    preset_reload_requested = Signal()            # reload current preset
    setlist_changed = Signal(str)              # setlist name
    setlist_edit_requested = Signal()
    sync_bpm_requested = Signal()  # auto-detect BPM from loaded samples
    tap_tempo_requested = Signal(float)  # tapped BPM value
    group_stop_requested = Signal(int)  # group index
    stop_all_requested = Signal()
    grid_mode_cycle_requested = Signal()   # user clicked the grid mode button
    virtual_grid_toggle_requested = Signal()  # user clicked the virtual grid button

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"TransportView {{ background: {BG_PANEL}; }}")

        # Wrap content in a horizontal scroll area so controls aren't clipped
        # on narrower windows (e.g. when many sections are visible).
        _outer = QVBoxLayout(self)
        _outer.setContentsMargins(0, 0, 0, 0)
        _outer.setSpacing(0)

        _scroll = QScrollArea()
        _scroll.setWidgetResizable(True)
        _scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        _scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _scroll.setFrameShape(QFrame.Shape.NoFrame)
        _scroll.setStyleSheet(
            f"QScrollArea {{ background: {BG_PANEL}; border: none; }}"
            f"QScrollBar:horizontal {{ height: 6px; background: {BG_PANEL}; }}"
            f"QScrollBar::handle:horizontal {{ background: #555; border-radius: 3px; min-width: 20px; }}"
            f"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}"
        )
        _outer.addWidget(_scroll)

        _content = QWidget()
        _content.setStyleSheet(f"QWidget {{ background: {BG_PANEL}; }}")
        _scroll.setWidget(_content)

        layout = QHBoxLayout(_content)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(8)

        # --- Tempo ---
        tempo_frame = QFrame()
        tempo_layout = QVBoxLayout(tempo_frame)
        tempo_layout.setContentsMargins(0, 0, 0, 0)
        tempo_layout.setSpacing(1)

        tempo_label = QLabel("BPM")
        tempo_label.setStyleSheet(f"font-size: 9px; color: {TEXT_SECONDARY}; font-weight: bold;")
        tempo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tempo_layout.addWidget(tempo_label)

        tempo_spin_row = QHBoxLayout()
        tempo_spin_row.setSpacing(2)

        self._tempo_down_btn = QPushButton("-")
        bind_fixed_size(self._tempo_down_btn, 20, 22)
        self._tempo_down_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 14px; font-weight: bold; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
        )
        self._tempo_down_btn.setAutoRepeat(True)
        self._tempo_down_btn.setToolTip("Decrease BPM")
        self._tempo_down_btn.clicked.connect(lambda: self._tempo_spin.stepDown())
        tempo_spin_row.addWidget(self._tempo_down_btn)

        self._tempo_spin = QDoubleSpinBox()
        self._tempo_spin.setRange(20.0, 300.0)
        self._tempo_spin.setValue(120.0)
        self._tempo_spin.setDecimals(1)
        self._tempo_spin.setSingleStep(0.5)
        bind_fixed_width(self._tempo_spin, 62)
        bind_fixed_height(self._tempo_spin, 22)
        self._tempo_spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self._tempo_spin.setStyleSheet(
            f"font-size: 12px; font-weight: bold; background: {BG_BLACK}; "
            f"border: 1px solid {SEPARATOR}; color: {TEXT_PRIMARY};"
        )
        self._tempo_spin.valueChanged.connect(self.tempo_changed.emit)
        tempo_spin_row.addWidget(self._tempo_spin)

        self._tempo_up_btn = QPushButton("+")
        bind_fixed_size(self._tempo_up_btn, 20, 22)
        self._tempo_up_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 14px; font-weight: bold; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
        )
        self._tempo_up_btn.setAutoRepeat(True)
        self._tempo_up_btn.setToolTip("Increase BPM")
        self._tempo_up_btn.clicked.connect(lambda: self._tempo_spin.stepUp())
        tempo_spin_row.addWidget(self._tempo_up_btn)

        tempo_layout.addLayout(tempo_spin_row)

        layout.addWidget(tempo_frame)

        # --- SYNC (auto BPM detect) button ---
        sync_frame = QFrame()
        sync_vl = QVBoxLayout(sync_frame)
        sync_vl.setContentsMargins(0, 0, 0, 0)
        sync_vl.setSpacing(1)
        _sync_spacer_lbl = QLabel(" ")
        bind_fixed_height(_sync_spacer_lbl, 10)
        _sync_spacer_lbl.setStyleSheet("color: transparent; background: transparent;")
        sync_vl.addWidget(_sync_spacer_lbl)
        self._sync_btn = QPushButton("SYNC")
        bind_fixed_size(self._sync_btn, 36, 36)
        self._sync_btn.setToolTip("Auto-detect BPM from loaded samples")
        self._sync_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 9px; font-weight: bold; "
            f"padding: 0px; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; border-color: {ACCENT_GOLD}; }}"
        )
        self._sync_btn.clicked.connect(self.sync_bpm_requested.emit)
        sync_vl.addWidget(self._sync_btn)
        layout.addWidget(sync_frame)

        # --- Beat / Tap tempo button ---
        tap_frame = QFrame()
        tap_vl = QVBoxLayout(tap_frame)
        tap_vl.setContentsMargins(0, 0, 0, 0)
        tap_vl.setSpacing(1)
        _tap_label = QLabel("TAP")
        bind_fixed_height(_tap_label, 10)
        _tap_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _tap_label.setStyleSheet(
            f"font-size: 7px; color: {TEXT_SECONDARY}; font-weight: bold; background: transparent;"
        )
        tap_vl.addWidget(_tap_label)
        # Dual function: shows beat position normally, tap to set BPM
        self._tap_btn = QPushButton("1.1")
        bind_fixed_size(self._tap_btn, 44, 36)
        self._tap_btn.setToolTip("TAP: Click to tap tempo\nShows beat position when idle")
        self._tap_btn.setStyleSheet(
            f"QPushButton {{ font-size: 14px; font-weight: bold; color: {TEXT_PRIMARY}; "
            f"background: {BG_BLACK}; border: 1px solid {SEPARATOR}; padding: 0; }}"
            f"QPushButton:hover {{ border-color: {ACCENT_GOLD}; color: {ACCENT_GOLD}; }}"
            f"QPushButton:pressed {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        # Tap tempo state
        self._tap_times: list[float] = []
        self._tap_timeout = QTimer(self)
        self._tap_timeout.setSingleShot(True)
        self._tap_timeout.setInterval(2000)  # 2s idle resets tap state
        self._tap_timeout.timeout.connect(self._tap_reset)
        self._tap_flash_timer = QTimer(self)
        self._tap_flash_timer.setSingleShot(True)
        self._tap_flash_timer.setInterval(80)
        self._tap_flash_timer.timeout.connect(self._tap_flash_off)
        self._tap_active = False  # True while tapping (suppresses beat display)
        self._tap_btn.clicked.connect(self._on_tap)
        tap_vl.addWidget(self._tap_btn)
        layout.addWidget(tap_frame)

        # --- Quantize ---
        quant_frame = QFrame()
        quant_layout = QVBoxLayout(quant_frame)
        quant_layout.setContentsMargins(0, 0, 0, 0)
        quant_layout.setSpacing(1)

        quant_label = QLabel("QUANTIZE")
        quant_label.setStyleSheet(f"font-size: 8px; color: {TEXT_SECONDARY}; font-weight: bold;")
        quant_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        quant_layout.addWidget(quant_label)

        self._quant_combo = QComboBox()
        self._quant_combo.addItems(["Off", "1/4", "1/8", "1/16", "Bar"])
        self._quant_combo.setCurrentIndex(3)
        bind_fixed_width(self._quant_combo, 54)
        self._quant_combo.currentIndexChanged.connect(self.quantize_changed.emit)
        quant_layout.addWidget(self._quant_combo)

        layout.addWidget(quant_frame)

        # --- Grid Mode section ---
        grid_mode_frame = QFrame()
        grid_mode_vl = QVBoxLayout(grid_mode_frame)
        grid_mode_vl.setContentsMargins(0, 0, 0, 0)
        grid_mode_vl.setSpacing(1)
        grid_mode_label = QLabel("GRID MODE")
        grid_mode_label.setStyleSheet(
            f"font-size: 8px; color: {TEXT_SECONDARY}; font-weight: bold;"
        )
        grid_mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid_mode_vl.addWidget(grid_mode_label)
        self._grid_mode_btn = QPushButton("GRID")
        bind_fixed_size(self._grid_mode_btn, 50, 36)
        self._grid_mode_btn.setToolTip(
            "Click to cycle grid mode (GRID → MIX → …)\n"
            "GRID: normal playback / trigger mode\n"
            "MIX: grid-as-mixer-surface mode"
        )
        self._grid_mode_btn.setStyleSheet(
            f"QPushButton {{ font-size: 9px; font-weight: bold; color: {TEXT_SECONDARY}; "
            f"background: {BG_BLACK}; border: 1px solid {SEPARATOR}; padding: 0; }}"
            f"QPushButton:hover {{ border-color: {STATUS_GRID_MODE}; color: {STATUS_GRID_MODE_BRIGHT}; }}"
            f"QPushButton:pressed {{ background: {STATUS_GRID_MODE}; color: {BG_BLACK}; }}"
        )
        self._grid_mode_btn.clicked.connect(self.grid_mode_cycle_requested.emit)
        grid_mode_vl.addWidget(self._grid_mode_btn)
        layout.addWidget(grid_mode_frame)

        # Separator
        sep = QFrame()
        bind_fixed_width(sep, 1)
        sep.setStyleSheet(f"background: {SEPARATOR};")
        bind_fixed_height(sep, 36)
        layout.addWidget(sep)

        # --- Group stop buttons (G1–G4 + ALL) ---
        grp_stop_frame = QFrame()
        grp_stop_vl = QVBoxLayout(grp_stop_frame)
        grp_stop_vl.setContentsMargins(0, 0, 0, 0)
        grp_stop_vl.setSpacing(1)
        grp_stop_label = QLabel("GROUP STOP")
        grp_stop_label.setStyleSheet(f"font-size: 8px; color: {TEXT_SECONDARY}; font-weight: bold;")
        grp_stop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grp_stop_vl.addWidget(grp_stop_label)
        grp_stop_btns_row = QWidget()
        grp_stop_hl = QHBoxLayout(grp_stop_btns_row)
        grp_stop_hl.setContentsMargins(0, 0, 0, 0)
        grp_stop_hl.setSpacing(2)

        self._grp_stop_btns: list[QPushButton] = []

        def _wire_grp_stop(b, s_idle, s_active, grp, view):
            """Closure factory — guarantees each btn/style/group is captured correctly."""
            t = QTimer(b)
            t.setSingleShot(True)
            t.setInterval(200)
            t.timeout.connect(lambda: b.setStyleSheet(s_idle))
            b.clicked.connect(lambda: (
                b.setStyleSheet(s_active),
                t.start(),
                view.group_stop_requested.emit(grp),
            ))

        for gi in range(NUM_GROUPS):
            gc = GROUP_COLORS[gi]
            btn = QPushButton(f"G{gi+1}")
            bind_fixed_size(btn, 36, 36)
            btn.setToolTip(f"Stop group {gi+1}")
            idle_style = (
                f"QPushButton {{ background: {BG_BLACK}; color: {gc}; "
                f"font-weight: bold; font-size: 10px; border: 1px solid {gc}; }}"
                f"QPushButton:hover {{ background: {gc}; color: {BG_BLACK}; }}"
                f"QPushButton:pressed {{ background: {gc}; color: {BG_BLACK}; }}"
            )
            active_style = (
                f"QPushButton {{ background: {gc}; color: {BG_BLACK}; "
                f"font-weight: bold; font-size: 10px; border: 1px solid {gc}; }}"
                f"QPushButton:hover {{ background: {gc}; color: {BG_BLACK}; }}"
                f"QPushButton:pressed {{ background: {gc}; color: {BG_BLACK}; }}"
            )
            btn.setStyleSheet(idle_style)
            _wire_grp_stop(btn, idle_style, active_style, gi, self)
            self._grp_stop_btns.append(btn)
            grp_stop_hl.addWidget(btn)

        # ALL stop button
        _all_idle = (
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-weight: bold; font-size: 10px; border: 1px solid {TEXT_SECONDARY}; "
            f"padding: 0px; }}"
            f"QPushButton:hover {{ background: {TEXT_PRIMARY}; color: {BG_BLACK}; padding: 0px; }}"
        )
        _all_active = (
            f"QPushButton {{ background: {TEXT_PRIMARY}; color: {BG_BLACK}; "
            f"font-weight: bold; font-size: 10px; border: 1px solid {TEXT_SECONDARY}; "
            f"padding: 0px; }}"
        )
        self._all_stop_btn = QPushButton("ALL")
        bind_fixed_size(self._all_stop_btn, 36, 36)
        self._all_stop_btn.setToolTip("Stop all groups")
        self._all_stop_btn.setStyleSheet(_all_idle)
        _all_timer = QTimer(self)
        _all_timer.setSingleShot(True)
        _all_timer.setInterval(150)
        _all_timer.timeout.connect(lambda: self._all_stop_btn.setStyleSheet(_all_idle))
        self._all_stop_btn.clicked.connect(
            lambda: (
                self._all_stop_btn.setStyleSheet(_all_active),
                _all_timer.start(),
                self.stop_all_requested.emit()
            )
        )
        grp_stop_hl.addWidget(self._all_stop_btn)
        grp_stop_vl.addWidget(grp_stop_btns_row)
        layout.addWidget(grp_stop_frame)

        # Separator
        sep_g = QFrame()
        bind_fixed_width(sep_g, 1)
        sep_g.setStyleSheet(f"background: {SEPARATOR};")
        bind_fixed_height(sep_g, 36)
        layout.addWidget(sep_g)

        # --- Pattern Recorders ---
        pattern_frame = QFrame()
        pattern_vlayout = QVBoxLayout(pattern_frame)
        pattern_vlayout.setContentsMargins(0, 0, 0, 0)
        pattern_vlayout.setSpacing(1)
        pattern_label = QLabel("PATTERN")
        pattern_label.setStyleSheet(f"font-size: 9px; color: {TEXT_SECONDARY}; font-weight: bold;")
        pattern_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pattern_vlayout.addWidget(pattern_label)
        pattern_btns_row = QWidget()
        pattern_btns_layout = QHBoxLayout(pattern_btns_row)
        pattern_btns_layout.setContentsMargins(0, 0, 0, 0)
        pattern_btns_layout.setSpacing(2)
        self._pattern_btns: list[PatternButton] = []
        pattern_lengths = [2, 4, 8, 16]
        for i in range(NUM_PATTERN_RECORDERS):
            btn = PatternButton(i, pattern_lengths[i])
            btn.state_changed.connect(self.pattern_state_changed.emit)
            btn.length_changed.connect(self.pattern_length_changed.emit)
            self._pattern_btns.append(btn)
            pattern_btns_layout.addWidget(btn)
        pattern_vlayout.addWidget(pattern_btns_row)
        layout.addWidget(pattern_frame)

        # Separator
        sep2 = QFrame()
        bind_fixed_width(sep2, 1)
        sep2.setStyleSheet(f"background: {SEPARATOR};")
        bind_fixed_height(sep2, 36)
        layout.addWidget(sep2)

        # --- Setlist / Preset navigation ---
        setlist_frame = QFrame()
        setlist_layout = QHBoxLayout(setlist_frame)
        setlist_layout.setContentsMargins(0, 0, 0, 0)
        setlist_layout.setSpacing(3)

        # Setlist selector (label + combo)
        sl_col = QVBoxLayout()
        sl_col.setContentsMargins(0, 0, 0, 0)
        sl_col.setSpacing(0)
        sl_label = QLabel("SETLIST")
        sl_label.setStyleSheet(f"font-size: 9px; color: {TEXT_SECONDARY}; font-weight: bold;")
        sl_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl_col.addWidget(sl_label)

        self._setlist_combo = _ClickableCombo()
        self._setlist_combo.setEditable(True)
        self._setlist_combo.lineEdit().setReadOnly(True)
        self._setlist_combo.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)
        bind_fixed_width(self._setlist_combo, 90)
        bind_fixed_height(self._setlist_combo, 22)
        self._setlist_combo.currentTextChanged.connect(
            lambda txt: self.setlist_changed.emit(txt) if txt and txt != "<new>" else None
        )
        sl_col.addWidget(self._setlist_combo)

        # Edit button (opens setlist editor) — placed left of setlist dropdown
        self._edit_btn = QPushButton("+")
        bind_fixed_size(self._edit_btn, 22, 22)
        self._edit_btn.setToolTip("Open setlist editor")
        self._edit_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 14px; border: 1px solid {SEPARATOR}; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; border-color: {ACCENT_GOLD}; }}"
        )
        self._edit_btn.clicked.connect(self.setlist_edit_requested.emit)
        setlist_layout.addWidget(self._edit_btn, alignment=Qt.AlignmentFlag.AlignBottom)

        setlist_layout.addLayout(sl_col)

        # Prev button
        self._prev_btn = QPushButton("\u25C0")
        bind_fixed_size(self._prev_btn, 22, 22)
        self._prev_btn.setToolTip("Previous preset")
        self._prev_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 12px; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; border-color: {ACCENT_GOLD}; }}"
        )
        self._prev_btn.clicked.connect(self.preset_prev_requested.emit)
        setlist_layout.addWidget(self._prev_btn, alignment=Qt.AlignmentFlag.AlignBottom)

        # Preset name column (label + editable combo)
        pr_col = QVBoxLayout()
        pr_col.setContentsMargins(0, 0, 0, 0)
        pr_col.setSpacing(0)
        pr_label = QLabel("PRESET")
        pr_label.setStyleSheet(f"font-size: 9px; color: {TEXT_SECONDARY}; font-weight: bold;")
        pr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pr_col.addWidget(pr_label)

        self._preset_combo = _ClickableCombo()
        self._preset_combo.setEditable(True)
        bind_fixed_width(self._preset_combo, 130)
        bind_fixed_height(self._preset_combo, 22)
        self._preset_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._preset_combo.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preset_combo.lineEdit().setReadOnly(True)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        pr_col.addWidget(self._preset_combo)
        setlist_layout.addLayout(pr_col)

        # Next button
        self._next_btn = QPushButton("\u25B6")
        bind_fixed_size(self._next_btn, 22, 22)
        self._next_btn.setToolTip("Next preset")
        self._next_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"border: 1px solid {SEPARATOR}; font-size: 12px; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; border-color: {ACCENT_GOLD}; }}"
        )
        self._next_btn.clicked.connect(self.preset_next_requested.emit)
        setlist_layout.addWidget(self._next_btn, alignment=Qt.AlignmentFlag.AlignBottom)

        # Reload button — reload current preset
        self._reload_btn = QPushButton("\u21BB")
        bind_fixed_size(self._reload_btn, 22, 22)
        self._reload_btn.setToolTip("Reload current preset")
        self._reload_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 14px; font-weight: bold; border: 1px solid {SEPARATOR}; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; border-color: {ACCENT_GOLD}; }}"
        )
        self._reload_btn.clicked.connect(self.preset_reload_requested.emit)
        setlist_layout.addWidget(self._reload_btn, alignment=Qt.AlignmentFlag.AlignBottom)

        # Save button — saves current state as the named preset + adds to setlist
        self._save_btn = QPushButton("SAVE")
        bind_fixed_size(self._save_btn, 40, 22)
        self._save_btn.setToolTip("Save current state as this preset and add to setlist")
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {ACCENT_GOLD}; "
            f"font-size: 9px; font-weight: bold; border: 1px solid {ACCENT_GOLD}; padding: 0; }}"
            f"QPushButton:hover {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        self._save_btn.clicked.connect(self._on_save_clicked)
        setlist_layout.addWidget(self._save_btn, alignment=Qt.AlignmentFlag.AlignBottom)

        # New button — clears all channels, resets to defaults
        self._new_btn = QPushButton("NEW")
        bind_fixed_size(self._new_btn, 34, 22)
        self._new_btn.setToolTip("Clear all channels — start a fresh preset")
        self._new_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 9px; font-weight: bold; border: 1px solid {SEPARATOR}; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; border-color: {ACCENT_GOLD}; }}"
        )
        self._new_btn.clicked.connect(self.preset_new_requested.emit)
        setlist_layout.addWidget(self._new_btn, alignment=Qt.AlignmentFlag.AlignBottom)

        layout.addWidget(setlist_frame)

        layout.addStretch()

        # ── Virtual Grid toggle — pinned far right ────────────────────────
        # Custom painted button: 2×2 grid of squares (window-with-cross look).
        vg_frame = QFrame()
        vg_vl = QVBoxLayout(vg_frame)
        vg_vl.setContentsMargins(0, 0, 0, 0)
        vg_vl.setSpacing(1)
        vg_label = QLabel("GRID")
        vg_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bind_fixed_height(vg_label, 10)
        vg_label.setStyleSheet(
            f"font-size: 7px; color: {TEXT_SECONDARY}; font-weight: bold; background: transparent;"
        )
        vg_vl.addWidget(vg_label)

        self._vg_btn = _GridIconButton(parent=vg_frame)
        bind_fixed_size(self._vg_btn, 36, 36)
        self._vg_btn.setCheckable(True)
        self._vg_btn.setChecked(False)
        self._vg_btn.setToolTip("Show / hide the virtual on-screen grid controller")
        self._vg_btn.clicked.connect(self.virtual_grid_toggle_requested.emit)
        vg_vl.addWidget(self._vg_btn)

        layout.addWidget(vg_frame)

        self._midi_map_active = False  # suppresses set_group_active style updates during MIDI map mode
        self._grp_stop_style_cache: dict[int, tuple[bool, str]] = {}

        # Internal flag to avoid signal loops
        self._updating_presets = False

    # ------------------------------------------------------------------ #
    #  Setlist / Preset helpers                                           #
    # ------------------------------------------------------------------ #

    def _on_preset_selected(self, index: int):
        """User selected a preset from the dropdown."""
        if not self._updating_presets and index >= 0:
            self.preset_recall_requested.emit(index)

    def _on_save_clicked(self):
        """User clicked SAVE — emit signal to trigger the save flow."""
        self.preset_save_requested.emit(self._preset_combo.currentText().strip())

    def set_virtual_grid_active(self, active: bool):
        """Sync the grid toggle button's visual state with grid visibility."""
        self._vg_btn.blockSignals(True)
        self._vg_btn.setChecked(active)
        self._vg_btn.blockSignals(False)
        self._vg_btn._update_style()
        self._vg_btn.update()

    def flash_save_button(self):
        """Briefly flash the SAVE button green to confirm a save."""
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background: {STATUS_SUCCESS}; color: {BG_BLACK}; "
            f"font-size: 9px; font-weight: bold; border: 1px solid {STATUS_SUCCESS}; padding: 0; }}"
        )
        QTimer.singleShot(600, self._reset_save_style)

    def _reset_save_style(self):
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {ACCENT_GOLD}; "
            f"font-size: 9px; font-weight: bold; border: 1px solid {ACCENT_GOLD}; padding: 0; }}"
            f"QPushButton:hover {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )

    def populate_setlist_combo(self, names: list[str], current: str = ""):
        self._setlist_combo.blockSignals(True)
        self._setlist_combo.clear()
        self._setlist_combo.addItems(names)
        if current:
            idx = self._setlist_combo.findText(current)
            if idx >= 0:
                self._setlist_combo.setCurrentIndex(idx)
                self._set_setlist_text_dim(False)
            else:
                # No matching setlist — show <new> placeholder
                self._setlist_combo.setCurrentIndex(-1)
                self._setlist_combo.setEditText("<new>")
                self._set_setlist_text_dim(True)
        elif not names:
            self._setlist_combo.setCurrentIndex(-1)
            self._setlist_combo.setEditText("<new>")
            self._set_setlist_text_dim(True)
        else:
            self._set_setlist_text_dim(False)
        self._setlist_combo.blockSignals(False)

    def populate_preset_combo(self, presets: list[str], active: int = -1):
        """Populate preset dropdown from setlist preset names."""
        self._updating_presets = True
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        for name in presets:
            self._preset_combo.addItem(name)
        if 0 <= active < self._preset_combo.count():
            self._preset_combo.setCurrentIndex(active)
            self._set_preset_text_dim(False)
        else:
            # No active preset — show <new> without selecting a list entry
            self._preset_combo.setCurrentIndex(-1)
            self._preset_combo.setEditText("<new>")
            self._set_preset_text_dim(True)
        self._preset_combo.blockSignals(False)
        self._updating_presets = False

    def set_active_preset(self, index: int):
        self._updating_presets = True
        self._preset_combo.blockSignals(True)
        if 0 <= index < self._preset_combo.count():
            self._preset_combo.setCurrentIndex(index)
            self._set_preset_text_dim(False)
        else:
            self._preset_combo.setCurrentIndex(-1)
            self._preset_combo.setEditText("<new>")
            self._set_preset_text_dim(True)
        self._preset_combo.blockSignals(False)
        self._updating_presets = False

    def _set_setlist_text_dim(self, dim: bool):
        """Toggle the setlist combo line-edit text between dim and normal."""
        clr = SEPARATOR if dim else TEXT_PRIMARY
        self._setlist_combo.setStyleSheet(
            f"QComboBox QLineEdit {{ color: {clr}; }}"
            f"QComboBox QAbstractItemView {{ color: {TEXT_PRIMARY}; }}"
        )

    def _set_preset_text_dim(self, dim: bool):
        """Toggle the preset combo line-edit text between dim and normal."""
        clr = SEPARATOR if dim else TEXT_PRIMARY
        self._preset_combo.setStyleSheet(
            f"QComboBox QLineEdit {{ color: {clr}; }}"
            f"QComboBox QAbstractItemView {{ color: {TEXT_PRIMARY}; }}"
        )

    def set_tempo(self, bpm: float):
        self._tempo_spin.blockSignals(True)
        self._tempo_spin.setValue(bpm)
        self._tempo_spin.blockSignals(False)

    def set_beat_position(self, bar: int, beat: int):
        if not self._tap_active:
            self._tap_btn.setText(f"{bar}.{beat}")

    def set_quantize(self, mode: int):
        self._quant_combo.blockSignals(True)
        self._quant_combo.setCurrentIndex(mode)
        self._quant_combo.blockSignals(False)

    def set_pattern_state(self, idx: int, state: str):
        if 0 <= idx < len(self._pattern_btns):
            self._pattern_btns[idx].set_state(state)

    # ------------------------------------------------------------------ #
    #  Tap tempo                                                          #
    # ------------------------------------------------------------------ #

    def _on_tap(self):
        """Record a tap and compute BPM from inter-tap intervals."""
        now = time.perf_counter()
        self._tap_active = True
        self._tap_timeout.start()  # restart 2s idle timeout
        if self._tap_times and (now - self._tap_times[-1]) > 2.0:
            self._tap_times.clear()  # gap too long — start fresh
        self._tap_times.append(now)
        if len(self._tap_times) > 16:
            self._tap_times = self._tap_times[-16:]
        # Flash feedback
        self._tap_btn.setStyleSheet(
            f"QPushButton {{ font-size: 14px; font-weight: bold; color: {BG_BLACK}; "
            f"background: {ACCENT_GOLD}; border: 1px solid {ACCENT_GOLD}; padding: 0; }}"
            f"QPushButton:hover {{ border-color: {ACCENT_GOLD}; }}"
        )
        self._tap_flash_timer.start()
        if len(self._tap_times) >= 2:
            intervals = [self._tap_times[i+1] - self._tap_times[i]
                         for i in range(len(self._tap_times) - 1)]
            avg = sum(intervals) / len(intervals)
            bpm = 60.0 / avg
            bpm = max(20.0, min(300.0, bpm))
            self._tap_btn.setText(f"{bpm:.1f}")
            self.tap_tempo_requested.emit(bpm)
        else:
            self._tap_btn.setText("TAP")

    def _tap_flash_off(self):
        self._tap_btn.setStyleSheet(
            f"QPushButton {{ font-size: 14px; font-weight: bold; color: {ACCENT_GOLD}; "
            f"background: {BG_BLACK}; border: 1px solid {ACCENT_GOLD}; padding: 0; }}"
            f"QPushButton:hover {{ border-color: {ACCENT_GOLD}; color: {ACCENT_GOLD}; }}"
            f"QPushButton:pressed {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )

    def _tap_reset(self):
        """Called after 2s idle — clear tap state and restore beat display."""
        self._tap_times.clear()
        self._tap_active = False
        self._tap_btn.setStyleSheet(
            f"QPushButton {{ font-size: 14px; font-weight: bold; color: {TEXT_PRIMARY}; "
            f"background: {BG_BLACK}; border: 1px solid {SEPARATOR}; padding: 0; }}"
            f"QPushButton:hover {{ border-color: {ACCENT_GOLD}; color: {ACCENT_GOLD}; }}"
            f"QPushButton:pressed {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )

    def set_midi_map_active(self, active: bool):
        """Suppress group stop style updates while MIDI map highlight is shown."""
        self._midi_map_active = active
        if not active:
            # The overlay restored its own saved styles — re-apply ours.
            self._grp_stop_style_cache.clear()

    def set_group_active(self, group: int, active: bool):
        """Update group stop button to show filled color when active."""
        if self._midi_map_active:
            return  # don't overwrite MIDI map highlights
        if 0 <= group < len(self._grp_stop_btns):
            gc = GROUP_COLORS[group]
            # Called for every group ~30x/s from the UI timer — only restyle
            # on change (setStyleSheet re-polishes and stalls the GUI thread).
            if self._grp_stop_style_cache.get(group) == (active, gc):
                return
            self._grp_stop_style_cache[group] = (active, gc)
            btn = self._grp_stop_btns[group]
            if active:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {gc}; color: {BG_BLACK}; "
                    f"font-weight: bold; font-size: 10px; border: 1px solid {gc}; }}"
                    f"QPushButton:hover {{ background: {BG_BLACK}; color: {gc}; }}"
                    f"QPushButton:pressed {{ background: {BG_BLACK}; color: {gc}; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {BG_BLACK}; color: {gc}; "
                    f"font-weight: bold; font-size: 10px; border: 1px solid {gc}; }}"
                    f"QPushButton:hover {{ background: {gc}; color: {BG_BLACK}; }}"
                    f"QPushButton:pressed {{ background: {gc}; color: {BG_BLACK}; }}"
                )

    def set_active_groups(self, n: int):
        """Show/hide group stop buttons based on active group count."""
        for i, btn in enumerate(self._grp_stop_btns):
            btn.setVisible(i < n)

    def set_grid_mode(self, mode: str):
        """Update the Grid Mode button label and highlight style."""
        label = mode.upper()
        if mode == "grid":
            self._grid_mode_btn.setText("GRID")
            self._grid_mode_btn.setStyleSheet(
                f"QPushButton {{ font-size: 9px; font-weight: bold; color: {TEXT_SECONDARY}; "
                f"background: {BG_BLACK}; border: 1px solid {SEPARATOR}; padding: 0; }}"
                f"QPushButton:hover {{ border-color: {STATUS_GRID_MODE}; color: {STATUS_GRID_MODE_BRIGHT}; }}"
                f"QPushButton:pressed {{ background: {STATUS_GRID_MODE}; color: {BG_BLACK}; }}"
            )
        else:
            self._grid_mode_btn.setText(label)
            self._grid_mode_btn.setStyleSheet(
                f"QPushButton {{ font-size: 9px; font-weight: bold; color: {BG_BLACK}; "
                f"background: {STATUS_GRID_MODE}; border: 1px solid {STATUS_GRID_MODE_BRIGHT}; padding: 0; }}"
                f"QPushButton:hover {{ background: {STATUS_GRID_MODE_HOVER}; }}"
                f"QPushButton:pressed {{ background: {STATUS_GRID_MODE_PRESSED}; }}"
            )
