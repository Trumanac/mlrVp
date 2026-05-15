"""Song Recorder panel — collapsible, pinned below the master mixer.

Visual: 4-track tape-deck inspired strip.

Header (24 px):  ▶/▼  ●  SONG RECORDER     |    elapsed (rec)  |  pos / total

Body (~140 px) — three zones separated by gold hairlines:

    LEFT   : ARM | REC | TAPE SPEED knob
    CENTER : scrolling waveform (with playhead, click-drag scrub when stopped)
             stacked stereo VU meters on the right edge
    BOTTOM : ⏮ ▶/⏸ ⏭ ■  |  filename QLineEdit  |  Save | Save As…

All transport buttons + the tape-speed knob are MIDI-mappable.
"""

from __future__ import annotations

import math
import time
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, Signal, QTimer, QPointF
from PySide6.QtGui import (
    QColor, QPainter, QBrush, QPen, QLinearGradient, QFont, QFontMetrics,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QSizePolicy, QSlider,
)

from mlrvp.gui.styles import (
    BG_PANEL, BG_BLACK, BG_LIGHT, SEPARATOR,
    TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_GOLD, RED, GREEN,
)
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.gui.widgets.vu_meter import VUMeter
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size, bind_fixed_width, bind_min_height


# Tape-speed knob mapping: knob 0..1 ↔ tape_speed 0.25..4.0 (logarithmic, 1.0 at centre).
_TAPE_MIN = 0.25
_TAPE_MAX = 4.0
_TAPE_LOG_RANGE = math.log(_TAPE_MAX / _TAPE_MIN)  # ln(16)


def _knob_to_tape(v: float) -> float:
    return _TAPE_MIN * math.exp(v * _TAPE_LOG_RANGE)


def _tape_to_knob(t: float) -> float:
    t = max(_TAPE_MIN, min(_TAPE_MAX, t))
    return math.log(t / _TAPE_MIN) / _TAPE_LOG_RANGE


def _fmt_mmss(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# REC / ARM LED indicator
# ---------------------------------------------------------------------------

class _RecLed(QWidget):
    """A small circular LED with two-state blink support.

    * set_active(True)  — solid red (armed or recording)
    * set_blink_bright(True/False) — alternates between dark-red and bright-pink
      when recording so it pulses visibly in the header.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._bright = False     # blink-bright phase
        bind_fixed_size(self, 10, 10)

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def set_blink_bright(self, bright: bool) -> None:
        if bright != self._bright:
            self._bright = bright
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._active:
            color = QColor("#ff6666") if self._bright else QColor(RED)
        else:
            color = QColor("#3a1111")
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 8, 8)


# ---------------------------------------------------------------------------
# Scrolling waveform with playhead and click-drag scrubbing
# ---------------------------------------------------------------------------

class _ScrollingWaveform(QWidget):
    """Tape-style waveform: the playhead is FIXED; the waveform scrolls past it.

    The full recording peaks are stored at ZOOM×resolution.  A window of
    1/ZOOM of the total duration is displayed, centred on _view_frac.
    Dragging the waveform (when stopped) scrolls the view and emits
    seek_requested.  During playback/recording set_playhead() auto-scrolls.
    """

    seek_requested = Signal(float)       # 0..1 fraction of full recording
    scrub_active = Signal(bool)           # True = drag started, False = released
    scrub_position = Signal(float)        # 0..1, emitted each move during drag

    PLAYHEAD_PX_FRAC = 0.55   # playhead at 55 % from the left edge (more tape-right feel)
    ZOOM = 4                   # show 1/ZOOM of the recording at a time

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_min_height(self, 56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background: {BG_BLACK}; border: 1px solid {SEPARATOR};")
        self._peaks: Optional[np.ndarray] = None   # full recording, len = width*ZOOM
        self._view_frac: float = 0.0               # fraction of recording at playhead
        self._scrubbable: bool = True
        self._dragging: bool = False
        self._drag_start_x: float = 0.0
        self._drag_start_frac: float = 0.0
        self._smooth_frac: float = 0.0    # exponentially smoothed drag fraction
        self._is_recording: bool = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    @property
    def needed_bins(self) -> int:
        """How many peaks bins to request from the backend for this zoom level."""
        return max(64, self.width() * self.ZOOM)

    def set_peaks(self, peaks: Optional[np.ndarray]) -> None:
        self._peaks = peaks
        self.update()

    def set_playhead(self, frac: float) -> None:
        """Auto-scroll the view to follow playback/recording position."""
        f = max(0.0, min(1.0, float(frac)))
        if f != self._view_frac:
            self._view_frac = f
            self.update()

    def set_scrubbable(self, on: bool) -> None:
        self._scrubbable = on
        if not self._dragging:
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if on else Qt.CursorShape.ArrowCursor
            )

    def set_recording(self, recording: bool) -> None:
        if self._is_recording != recording:
            self._is_recording = recording
            self.update()

    # ------------------------------------------------------------------ painting

    def paintEvent(self, _event):
        p = QPainter(self)
        w = self.width()
        h = self.height()
        cy = h / 2
        playhead_px = int(w * self.PLAYHEAD_PX_FRAC)

        # Tape-strip backdrop: dark vertical gradient
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor("#0a0a0a"))
        grad.setColorAt(0.5, QColor("#000000"))
        grad.setColorAt(1.0, QColor("#0a0a0a"))
        p.fillRect(0, 0, w, h, grad)

        # Centre line
        p.setPen(QPen(QColor(SEPARATOR), 1, Qt.PenStyle.DotLine))
        p.drawLine(0, int(cy), w, int(cy))

        # Scrolling waveform — only the window around _view_frac is visible
        if self._peaks is not None and self._peaks.size > 0:
            n = self._peaks.size
            center_bin = self._view_frac * (n - 1)
            half_h = h * 0.46
            colour = QColor(ACCENT_GOLD)
            colour.setAlpha(200)
            p.setPen(QPen(colour, 1))
            for x in range(w):
                bin_i = int(round(center_bin + (x - playhead_px)))
                if 0 <= bin_i < n:
                    amp = min(1.0, float(self._peaks[bin_i]))
                    y = int(half_h * amp)
                    if y > 0:
                        p.drawLine(x, int(cy - y), x, int(cy + y))

        # Fixed playhead line
        ph_color = QColor(RED) if self._is_recording else QColor(GREEN)
        p.setPen(QPen(ph_color, 2))
        p.drawLine(playhead_px, 0, playhead_px, h)

        # Locked overlay tint when not scrubbable
        if not self._scrubbable:
            p.fillRect(0, 0, w, h, QColor(0, 0, 0, 40))

        p.end()

    # ------------------------------------------------------------------ scrubbing (drag scrolls waveform)

    def mousePressEvent(self, event: QMouseEvent):
        if not self._scrubbable or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._peaks is None or self._peaks.size == 0:
            return
        self._dragging = True
        self._drag_start_x = event.position().x()
        self._drag_start_frac = self._view_frac
        self._smooth_frac = self._view_frac
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.scrub_active.emit(True)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self._dragging:  # once drag started, never abort mid-drag
            return
        n = self._peaks.size if self._peaks is not None else 1
        if n <= 0:
            return
        # Raw drag fraction — used only for velocity computation in app layer.
        # The waveform display follows audio position (set_playhead via poll loop),
        # same as FF/RW, so we don't update _view_frac here.
        dx = event.position().x() - self._drag_start_x
        raw_frac = max(0.0, min(1.0, self._drag_start_frac - dx / n))
        self.seek_requested.emit(raw_frac)
        self.scrub_position.emit(raw_frac)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            if self._scrubbable:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.scrub_active.emit(False)


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

class SongRecorderPanel(QWidget):
    """Collapsible Song Recorder panel.

    Signals
    -------
    arm_toggled(bool)
    rec_clicked()
    tape_speed_changed(float)        — final tape-speed value (0.25..4.0)
    save_clicked(str)                — payload is the current filename text
    save_as_clicked()
    play_clicked()
    rewind_clicked()
    ff_clicked()
    stop_clicked()
    seek_requested(float)            — 0.0..1.0 fraction (from waveform scrub)
    collapsed_changed(bool)
    """

    arm_toggled = Signal(bool)
    rec_clicked = Signal()
    clear_clicked = Signal()
    loop_toggled = Signal(bool)
    tape_speed_changed = Signal(float)
    save_clicked = Signal(str)
    save_as_clicked = Signal()
    play_clicked = Signal()
    seek_delta_requested = Signal(float)  # signed speed multiplier (e.g. 4.0 = FF 4×, -8.0 = RW 8×)
    transport_released = Signal()          # FF/RW button released
    scrub_active = Signal(bool)            # True = drag started, False = released
    scrub_position = Signal(float)         # 0..1 position emitted each drag move
    stop_clicked = Signal()                # single-click: pause at position
    stop_rewind_clicked = Signal()         # double-click: stop + rewind to start
    seek_requested = Signal(float)
    playback_volume_changed = Signal(float)  # 0.0..1.0
    collapsed_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SongRecorderPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"SongRecorderPanel {{ background: {BG_BLACK}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._build_header(root)
        self._build_body(root)

        # Blink timer for ARM (only animates when armed and not yet recording)
        self._blink_state = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(450)
        self._blink_timer.timeout.connect(self._on_blink_tick)

        # Transport hold timer — accelerating FF/RW
        self._transport_timer = QTimer(self)
        self._transport_timer.setInterval(80)
        self._transport_timer.timeout.connect(self._on_transport_tick)
        self._transport_dir: int = 0
        self._transport_hold_t: float = 0.0

        # Internal state
        self._armed = False
        self._recording = False
        self._playing = False

    # ------------------------------------------------------------------ header

    def _build_header(self, root: QVBoxLayout) -> None:
        self._header = QFrame()
        bind_fixed_height(self._header, 24)
        self._header.setStyleSheet(
            f"QFrame {{ background: {BG_PANEL}; "
            f"border-top: 1px solid {ACCENT_GOLD}; "
            f"border-bottom: 1px solid {SEPARATOR}; }}"
        )
        hdr = QHBoxLayout(self._header)
        hdr.setContentsMargins(6, 0, 6, 0)
        hdr.setSpacing(8)

        self._collapse_btn = QPushButton("▶")
        bind_fixed_size(self._collapse_btn, 18, 18)
        self._collapse_btn.setCheckable(True)
        self._collapse_btn.setChecked(False)   # collapsed by default
        self._collapse_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {TEXT_SECONDARY}; "
            f"font-size: 9px; border: none; padding: 0; }}"
            f"QPushButton:hover {{ color: {ACCENT_GOLD}; }}"
        )
        self._collapse_btn.clicked.connect(self._on_collapse_toggled)
        hdr.addWidget(self._collapse_btn)

        self._hdr_led = _RecLed()
        hdr.addWidget(self._hdr_led, 0, Qt.AlignmentFlag.AlignVCenter)

        title_lbl = QLabel("SONG RECORDER")
        title_lbl.setStyleSheet(
            f"color: {ACCENT_GOLD}; font-size: 9px; font-weight: bold; "
            f"letter-spacing: 1px; background: transparent; border: none;"
        )
        hdr.addWidget(title_lbl)

        hdr.addStretch()

        self._hdr_rec_lbl = QLabel("REC 00:00")
        self._hdr_rec_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 8px; font-weight: bold; "
            f"background: transparent; border: none; font-family: monospace;"
        )
        hdr.addWidget(self._hdr_rec_lbl)

        sep = QLabel(" • ")
        sep.setStyleSheet(
            f"color: {SEPARATOR}; font-size: 8px; background: transparent; border: none;"
        )
        hdr.addWidget(sep)

        self._hdr_pos_lbl = QLabel("00:00 / 00:00")
        self._hdr_pos_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 8px; font-weight: bold; "
            f"background: transparent; border: none; font-family: monospace;"
        )
        hdr.addWidget(self._hdr_pos_lbl)

        root.addWidget(self._header)

    # ------------------------------------------------------------------ body

    def _build_body(self, root: QVBoxLayout) -> None:
        self._body = QFrame()
        self._body.setStyleSheet(
            f"QFrame {{ background: {BG_PANEL}; border: none; "
            f"border-bottom: 1px solid {SEPARATOR}; }}"
        )
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(8, 6, 8, 6)
        body_lay.setSpacing(6)

        # ── Top zone: controls + waveform + meters ─────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.setContentsMargins(0, 0, 0, 0)

        # ── Left zone: ARM + REC + TAPE SPEED knob ────────────────────
        left_zone = self._build_left_zone()
        top_row.addWidget(left_zone, 0)

        # Vertical gold hairline separator
        top_row.addWidget(self._build_vsep())

        # ── Centre zone: waveform + VUs ─────────────────────────────
        centre_zone = self._build_centre_zone()
        top_row.addWidget(centre_zone, 1)

        body_lay.addLayout(top_row)

        # Horizontal hairline
        hr = QFrame()
        bind_fixed_height(hr, 1)
        hr.setStyleSheet(f"background: {SEPARATOR};")
        body_lay.addWidget(hr)

        # ── Bottom zone: transport + filename + save ───────────────
        bottom_row = self._build_bottom_row()
        body_lay.addLayout(bottom_row)

        self._body.setVisible(False)   # collapsed by default
        root.addWidget(self._body)

    def _build_vsep(self) -> QFrame:
        sep = QFrame()
        bind_fixed_width(sep, 1)
        sep.setStyleSheet(f"background: {ACCENT_GOLD};")
        return sep

    def _build_left_zone(self) -> QWidget:
        zone = QWidget()
        bind_fixed_width(zone, 170)
        v = QVBoxLayout(zone)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        # ARM + REC row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._arm_btn = QPushButton("ARM")
        self._arm_btn.setCheckable(True)
        bind_fixed_size(self._arm_btn, 54, 28)
        self._arm_btn.setToolTip(
            "ARM — recording starts on next grid trigger (any pad)"
        )
        self._apply_arm_style(False, False)
        self._arm_btn.toggled.connect(self._on_arm_toggled)
        btn_row.addWidget(self._arm_btn)

        self._rec_btn = QPushButton("● REC")
        bind_fixed_size(self._rec_btn, 72, 28)
        self._rec_btn.setToolTip("REC — start recording immediately")
        self._apply_rec_style(False)
        self._rec_btn.clicked.connect(self.rec_clicked.emit)
        btn_row.addWidget(self._rec_btn)

        v.addLayout(btn_row)

        # TAPE SPEED knob row
        knob_row = QHBoxLayout()
        knob_row.setSpacing(6)

        knob_lbl = QLabel("TAPE\nSPEED")
        knob_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        knob_lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 8px; font-weight: bold; "
            f"letter-spacing: 1px; background: transparent; border: none;"
        )
        knob_row.addWidget(knob_lbl)

        self._tape_knob = RotaryKnob(color=ACCENT_GOLD, default_value=0.5)
        self._tape_knob.set_value(0.5)   # 1.0 ratio
        self._tape_knob.setToolTip(
            "Tape speed (1.0 = unity)\n"
            "< 1.0 → recorded slow → pitch UP\n"
            "> 1.0 → recorded fast → pitch DOWN"
        )
        self._tape_knob.value_changed.connect(self._on_tape_knob_changed)
        knob_row.addWidget(self._tape_knob)

        self._tape_val_lbl = QLabel("1.00x")
        bind_fixed_width(self._tape_val_lbl, 44)
        self._tape_val_lbl.setStyleSheet(
            f"color: {ACCENT_GOLD}; font-size: 10px; font-weight: bold; "
            f"background: {BG_BLACK}; border: 1px solid {SEPARATOR}; "
            f"padding: 2px 4px; font-family: monospace;"
        )
        self._tape_val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        knob_row.addWidget(self._tape_val_lbl)

        knob_row.addStretch()
        v.addLayout(knob_row)

        return zone

    def _build_centre_zone(self) -> QWidget:
        zone = QWidget()
        h = QHBoxLayout(zone)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        self._wave = _ScrollingWaveform()
        self._wave.seek_requested.connect(self.seek_requested.emit)
        self._wave.scrub_active.connect(self.scrub_active.emit)
        self._wave.scrub_position.connect(self.scrub_position.emit)
        h.addWidget(self._wave, 1)

        # Stacked stereo VU meters (vertical, slim) on the right
        vu_holder = QWidget()
        vu_v = QVBoxLayout(vu_holder)
        vu_v.setContentsMargins(0, 0, 0, 0)
        vu_v.setSpacing(2)

        meters = QHBoxLayout()
        meters.setSpacing(2)
        self._vu_l = VUMeter(orientation=Qt.Orientation.Vertical)
        bind_fixed_size(self._vu_l, 8, 56)
        self._vu_r = VUMeter(orientation=Qt.Orientation.Vertical)
        bind_fixed_size(self._vu_r, 8, 56)
        meters.addWidget(self._vu_l)
        meters.addWidget(self._vu_r)
        vu_v.addLayout(meters)

        lbl = QLabel("L  R")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 7px; font-weight: bold; "
            f"background: transparent; border: none;"
        )
        vu_v.addWidget(lbl)

        h.addWidget(vu_holder, 0)
        return zone

    def _build_bottom_row(self) -> QHBoxLayout:
        BH = 26   # uniform button/control height for the entire bottom row
        row = QHBoxLayout()
        row.setSpacing(4)
        row.setContentsMargins(0, 0, 0, 0)

        # Transport buttons — all neutral colour (no blue) for clarity
        neutral_btn_style = (
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 11px; border: 1px solid {SEPARATOR}; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; border-color: {ACCENT_GOLD}; }}"
            f"QPushButton:pressed {{ background: {BG_LIGHT}; }}"
        )

        self._rw_btn = QPushButton("|<")
        bind_fixed_size(self._rw_btn, 30, BH)
        self._rw_btn.setToolTip("Hold to rewind — accelerates like tape")
        self._rw_btn.setStyleSheet(neutral_btn_style)
        self._rw_btn.pressed.connect(lambda: self._on_transport_press(-1))
        self._rw_btn.released.connect(self._on_transport_release)
        row.addWidget(self._rw_btn)

        self._play_btn = QPushButton("\u25b6")
        bind_fixed_size(self._play_btn, 34, BH)
        self._play_btn.setCheckable(True)
        self._play_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 11px; border: 1px solid {SEPARATOR}; padding: 0; }}"
            f"QPushButton:checked {{ color: {GREEN}; border-color: {GREEN}; "
            f"background: {BG_BLACK}; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; border-color: {ACCENT_GOLD}; }}"
        )
        self._play_btn.clicked.connect(self.play_clicked.emit)
        row.addWidget(self._play_btn)

        self._ff_btn = QPushButton(">|")
        bind_fixed_size(self._ff_btn, 30, BH)
        self._ff_btn.setToolTip("Hold to fast-forward — accelerates like tape")
        self._ff_btn.setStyleSheet(neutral_btn_style)
        self._ff_btn.pressed.connect(lambda: self._on_transport_press(1))
        self._ff_btn.released.connect(self._on_transport_release)
        row.addWidget(self._ff_btn)

        self._stop_btn = QPushButton("\u25a0")
        bind_fixed_size(self._stop_btn, 30, BH)
        self._stop_btn.setToolTip("Stop  |  Double-click = stop + rewind")
        self._stop_btn.setStyleSheet(neutral_btn_style)
        self._stop_btn._last_click_time = 0.0
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        row.addWidget(self._stop_btn)

        self._loop_btn = QPushButton("\u21ba")
        bind_fixed_size(self._loop_btn, 28, BH)
        self._loop_btn.setCheckable(True)
        self._loop_btn.setToolTip("Loop playback")
        self._loop_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 13px; border: 1px solid {SEPARATOR}; padding: 0; }}"
            f"QPushButton:checked {{ color: {ACCENT_GOLD}; border-color: {ACCENT_GOLD}; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; border-color: {ACCENT_GOLD}; }}"
        )
        self._loop_btn.toggled.connect(self.loop_toggled.emit)
        row.addWidget(self._loop_btn)

        # Volume slider
        vol_lbl = QLabel("\U0001f50a")
        bind_fixed_size(vol_lbl, 14, BH)
        vol_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vol_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 10px;")
        vol_lbl.setToolTip("Playback volume")
        row.addWidget(vol_lbl)
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(100)
        bind_fixed_size(self._vol_slider, 72, BH)
        self._vol_slider.setToolTip("Playback volume")
        self._vol_slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 4px; background: {SEPARATOR}; border-radius: 2px; }}"
            f"QSlider::sub-page:horizontal {{ background: {ACCENT_GOLD}; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ width: 10px; height: 10px; margin: -3px 0;"
            f" background: {TEXT_PRIMARY}; border-radius: 5px; }}"
        )
        self._vol_slider.valueChanged.connect(
            lambda v: self.playback_volume_changed.emit(v / 100.0)
        )
        row.addWidget(self._vol_slider)

        sp = QFrame()
        bind_fixed_size(sp, 1, BH)
        sp.setStyleSheet(f"background: {SEPARATOR};")
        row.addWidget(sp)

        self._filename_edit = QLineEdit()
        self._filename_edit.setPlaceholderText("recording filename…")
        bind_fixed_height(self._filename_edit, BH)
        self._filename_edit.setStyleSheet(
            f"QLineEdit {{ background: {BG_BLACK}; color: {TEXT_PRIMARY}; "
            f"font-size: 10px; border: 1px solid {SEPARATOR}; padding: 1px 6px; "
            f"font-family: monospace; }}"
            f"QLineEdit:focus {{ border-color: {ACCENT_GOLD}; }}"
        )
        row.addWidget(self._filename_edit, stretch=1)

        self._save_btn = QPushButton("Save")
        bind_fixed_size(self._save_btn, 48, BH)
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {ACCENT_GOLD}; "
            f"font-size: 9px; font-weight: bold; border: 1px solid {ACCENT_GOLD}; padding: 0; }}"
            f"QPushButton:hover {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; }}"
        )
        self._save_btn.clicked.connect(self._on_save_clicked)
        row.addWidget(self._save_btn)

        self._save_as_btn = QPushButton("Save As\u2026")
        bind_fixed_size(self._save_as_btn, 64, BH)
        self._save_as_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 9px; border: 1px solid {SEPARATOR}; padding: 0; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; border-color: {ACCENT_GOLD}; }}"
        )
        self._save_as_btn.clicked.connect(self.save_as_clicked.emit)
        row.addWidget(self._save_as_btn)

        self._clear_btn = QPushButton("New")
        bind_fixed_size(self._clear_btn, 44, BH)
        self._clear_btn.setToolTip("Discard recording and start fresh")
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
            f"font-size: 9px; font-weight: bold; border: 1px solid {SEPARATOR}; padding: 0 4px; }}"
            f"QPushButton:hover {{ color: #ff6666; border-color: #ff6666; }}"
            f"QPushButton:pressed {{ background: #1a0000; }}"
        )
        self._clear_btn.clicked.connect(self.clear_clicked.emit)
        row.addWidget(self._clear_btn)
        return row

    # ------------------------------------------------------------------ styling helpers

    def _apply_arm_style(self, armed: bool, blink_on: bool) -> None:
        """ARM button visual states: idle / armed-blinking / armed-solid."""
        if armed:
            if blink_on:
                bg = RED
                fg = "#ffffff"
                border = RED
            else:
                bg = BG_BLACK
                fg = RED
                border = RED
            self._arm_btn.setStyleSheet(
                f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border}; "
                f"font-size: 10px; font-weight: bold; letter-spacing: 1px; }}"
                f"QPushButton:hover {{ border-color: {ACCENT_GOLD}; }}"
            )
        else:
            # Idle: include a :checked rule so toggling on shows a visible highlight
            self._arm_btn.setStyleSheet(
                f"QPushButton {{ background: {BG_BLACK}; color: {TEXT_SECONDARY}; "
                f"border: 1px solid {SEPARATOR}; "
                f"font-size: 10px; font-weight: bold; letter-spacing: 1px; }}"
                f"QPushButton:checked {{ background: #331100; color: {ACCENT_GOLD}; "
                f"border: 1px solid {ACCENT_GOLD}; }}"
                f"QPushButton:hover {{ border-color: {ACCENT_GOLD}; }}"
            )

    def _apply_rec_style(self, recording: bool) -> None:
        """REC button visual states: idle (gold ring) / recording (solid red)."""
        if recording:
            self._rec_btn.setStyleSheet(
                f"QPushButton {{ background: {RED}; color: #ffffff; "
                f"border: 2px solid {RED}; font-size: 11px; font-weight: bold; "
                f"letter-spacing: 1px; }}"
            )
        else:
            self._rec_btn.setStyleSheet(
                f"QPushButton {{ background: {BG_BLACK}; color: {RED}; "
                f"border: 1px solid {RED}; font-size: 11px; font-weight: bold; "
                f"letter-spacing: 1px; }}"
                f"QPushButton:hover {{ background: #220000; }}"
            )

    # ------------------------------------------------------------------ slots

    def _on_collapse_toggled(self, checked: bool) -> None:
        self._body.setVisible(checked)
        self._collapse_btn.setText("▼" if checked else "▶")
        self.collapsed_changed.emit(not checked)

    def _on_arm_toggled(self, checked: bool) -> None:
        self.arm_toggled.emit(checked)

    def _on_tape_knob_changed(self, knob_val: float) -> None:
        speed = _knob_to_tape(knob_val)
        self._tape_val_lbl.setText(f"{speed:.2f}x")
        self.tape_speed_changed.emit(speed)

    def _on_stop_clicked(self) -> None:
        """Single click = stop at position; double-click = stop + rewind."""
        now = time.monotonic()
        last = getattr(self._stop_btn, '_last_click_time', 0.0)
        self._stop_btn._last_click_time = now
        if now - last < 0.4:
            # Double-click
            self.stop_rewind_clicked.emit()
        else:
            self.stop_clicked.emit()

    # ------------------------------------------------------------------ transport hold

    def _on_transport_press(self, direction: int) -> None:
        self._transport_dir = direction
        self._transport_hold_t = time.monotonic()
        self._transport_timer.start()

    def _on_transport_release(self) -> None:
        self._transport_timer.stop()
        self._transport_dir = 0
        self.transport_released.emit()

    def _on_transport_tick(self) -> None:
        """Called every 80 ms while RW/FF is held.  Speed ramps 2× → 30× over 3 s."""
        elapsed = time.monotonic() - self._transport_hold_t
        speed_x = 2.0 + (min(elapsed, 3.0) / 3.0) ** 2 * 28.0
        self.seek_delta_requested.emit(speed_x * self._transport_dir)  # signed speed multiplier

    def _on_save_clicked(self) -> None:
        self.save_clicked.emit(self._filename_edit.text().strip())

    def _on_blink_tick(self) -> None:
        self._blink_state = not self._blink_state
        if self._recording:
            # While recording: ARM stays solid red; LED pulses bright/dim
            self._hdr_led.set_active(True)
            self._hdr_led.set_blink_bright(self._blink_state)
        else:
            # While armed (not yet recording): ARM button blinks red
            self._apply_arm_style(self._armed, self._blink_state)
            self._hdr_led.set_active(self._armed and self._blink_state)
            self._hdr_led.set_blink_bright(False)

    # ------------------------------------------------------------------ public API

    def set_armed(self, armed: bool) -> None:
        self._armed = armed
        self._arm_btn.blockSignals(True)
        self._arm_btn.setChecked(armed)
        self._arm_btn.blockSignals(False)
        if armed and not self._recording:
            if not self._blink_timer.isActive():
                self._blink_state = True
                self._blink_timer.start()
            self._apply_arm_style(True, True)
        else:
            if not self._recording:
                self._blink_timer.stop()
            self._blink_state = False
            self._apply_arm_style(armed and self._recording, False)
        self._hdr_led.set_active(self._recording or armed)
        self._hdr_led.set_blink_bright(False)

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        self._apply_rec_style(recording)
        if recording:
            # Keep blink timer running — it drives the LED pulse during recording
            if not self._blink_timer.isActive():
                self._blink_state = True
                self._blink_timer.start()
        else:
            # Stop blink timer unless ARM is still active (waiting for REC)
            if not self._armed:
                self._blink_timer.stop()
            self._hdr_led.set_blink_bright(False)
        self._hdr_led.set_active(recording or self._armed)
        # Disable scrub while recording
        self._wave.set_recording(recording)
        self._wave.set_scrubbable(not recording and not self._playing)

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        self._play_btn.blockSignals(True)
        self._play_btn.setChecked(playing)
        self._play_btn.setText("⏸" if playing else "▶")
        self._play_btn.blockSignals(False)
        # Scrubbing is allowed in any play state; only recording disables it.

    def set_levels(self, peak_l: float, peak_r: float) -> None:
        self._vu_l.set_level(min(1.0, peak_l))
        self._vu_r.set_level(min(1.0, peak_r))

    def set_elapsed(self, seconds: float) -> None:
        """Recording elapsed time (header)."""
        self._hdr_rec_lbl.setText(f"REC {_fmt_mmss(seconds)}")

    def set_position_total(self, position_sec: float, total_sec: float) -> None:
        """Update header MM:SS / MM:SS playback position."""
        self._hdr_pos_lbl.setText(f"{_fmt_mmss(position_sec)} / {_fmt_mmss(total_sec)}")

    def set_waveform_peaks(self, peaks: Optional[np.ndarray]) -> None:
        self._wave.set_peaks(peaks)

    def set_playhead(self, frac: float) -> None:
        self._wave.set_playhead(frac)

    def set_filename(self, name: str) -> None:
        self._filename_edit.setText(name)

    def get_filename(self) -> str:
        return self._filename_edit.text().strip()

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapse_btn.blockSignals(True)
        self._collapse_btn.setChecked(not collapsed)
        self._collapse_btn.blockSignals(False)
        self._body.setVisible(not collapsed)
        self._collapse_btn.setText("▶" if collapsed else "▼")

    def set_tape_speed(self, speed: float) -> None:
        knob_val = _tape_to_knob(speed)
        self._tape_knob.set_value(knob_val)
        self._tape_val_lbl.setText(f"{speed:.2f}x")

    # ---- Mappable widgets exposed for MIDI registration ----
    @property
    def arm_widget(self) -> QPushButton:
        return self._arm_btn

    @property
    def rec_widget(self) -> QPushButton:
        return self._rec_btn

    @property
    def play_widget(self) -> QPushButton:
        return self._play_btn

    @property
    def stop_widget(self) -> QPushButton:
        return self._stop_btn

    @property
    def rewind_widget(self) -> QPushButton:
        return self._rw_btn

    @property
    def ff_widget(self) -> QPushButton:
        return self._ff_btn

    @property
    def tape_speed_widget(self) -> RotaryKnob:
        return self._tape_knob
