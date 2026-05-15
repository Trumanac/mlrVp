"""Reel Space — 2U Space Echo-style tape delay.

Three virtual playback heads (H1/H2/H3 at 1×/2×/3× base delay),
seven selectable head-combination modes, wow/flutter, saturation,
tone filter, and a built-in spring reverb.
"""

import math

from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QWidget, QFrame,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import TapeDelayState
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size, bind_fixed_width

# ── RE-201 colour palette ────────────────────────────────────────────────────
_FACE       = "#111111"        # near-black body / leather texture base
_SILVER     = "#c8c8c8"        # brushed-aluminium header text
_DIM_SILVER = "#888888"        # dimmed silver for sub-labels
_GREEN_BG   = "#1c3a1c"        # RE-201 dark-green panel sections
_GREEN_LITE = "#2e6b2e"        # lighter green border/accent
_GREEN_TEXT = "#a8e6a8"        # pale green for knob labels on green bg
_OFF_WHITE  = "#e0e0d8"        # label text on silver/black areas
_CHROME     = "#b0b8c0"        # chrome/silver knob colour

# ── combo box options ────────────────────────────────────────────────────────
SYNC_NAMES = [
    "1/32", "1/16t", "1/16", "1/8t", "1/8",
    "1/4t", "3/16", "1/4", "1/3", "3/8",
    "1/2", "3/4", "1 bar",
]

HEAD_MODE_NAMES = [
    "H1  ●··",
    "H2  ·●·",
    "H3  ··●",
    "H1+H2  ●●·",
    "H1+H3  ●·●",
    "H2+H3  ·●●",
    "ALL  ●●●",
]




class _ReelSpaceLogo(QWidget):
    """Tape reel with an orbital dot — the 'Reel Space' emblem."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 36, 36)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = 17, 18
        silver = QColor(_SILVER)
        dim    = QColor(_DIM_SILVER)

        # ── outer orbital ring ───────────────────────────────────────────────
        pen = QPen(dim, 1.0, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - 16, cy - 16, 32, 32)

        # ── orbital dot ──────────────────────────────────────────────────────
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(silver)
        ox = cx + int(16.0 * math.cos(math.radians(-45)))
        oy = cy + int(16.0 * math.sin(math.radians(-45)))
        p.drawEllipse(ox - 3, oy - 3, 6, 6)

        # ── reel flange (outer circle) ───────────────────────────────────────
        pen2 = QPen(silver, 2.0)
        p.setPen(pen2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - 10, cy - 10, 20, 20)

        # ── spokes × 3 ───────────────────────────────────────────────────────
        p.setPen(QPen(silver, 1.5))
        for deg in (0, 120, 240):
            rad = math.radians(deg)
            p.drawLine(
                cx + int(3.5 * math.cos(rad)), cy + int(3.5 * math.sin(rad)),
                cx + int(9.0 * math.cos(rad)), cy + int(9.0 * math.sin(rad)),
            )

        # ── hub ─────────────────────────────────────────────────────────────
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(silver)
        p.drawEllipse(cx - 3, cy - 3, 6, 6)

        p.end()




class TapeDelayModule(RackModuleBase):
    """2U Reel Space — 3-head Space Echo delay with spring reverb."""

    effect_type = "tape_delay"

    def __init__(self, parent=None):
        super().__init__(unit_height=2, faceplate_color=_FACE,
                         title="REEL SPACE", parent=parent)
        self._build_faceplate()

    # ── build ────────────────────────────────────────────────────────────────

    def _build_faceplate(self):
        self._build_row1()
        self._build_row2()

    def _build_row1(self):
        """Silver header strip: logo, title, and head-mode selector."""
        # Wrap in a silver-aluminium strip to match the RE-201 header
        header = QFrame()
        header.setStyleSheet(
            f"background: #909090; border-bottom: 1px solid {_GREEN_LITE};"
        )
        row = QHBoxLayout(header)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(6)

        row.addWidget(_ReelSpaceLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title_col.setContentsMargins(0, 2, 0, 0)
        title = QLabel("REEL SPACE")
        title.setStyleSheet(
            f"color: #111111; font-size: 12px; font-weight: bold;"
            " font-family: 'Arial'; letter-spacing: 3px; background: transparent;"
        )
        title_col.addWidget(title)
        sub = QLabel("TAPE ECHO DELAY")
        sub.setStyleSheet(
            f"color: #444444; font-size: 7px; font-weight: bold;"
            " font-family: 'Arial'; letter-spacing: 2px; background: transparent;"
        )
        title_col.addWidget(sub)
        row.addLayout(title_col)

        row.addStretch()

        # Head mode selector — sits in a green sub-panel
        hm_panel = QFrame()
        hm_panel.setStyleSheet(
            f"background: {_GREEN_BG}; border: 1px solid {_GREEN_LITE}; border-radius: 2px;"
        )
        hm_col = QVBoxLayout(hm_panel)
        hm_col.setContentsMargins(6, 3, 6, 3)
        hm_col.setSpacing(2)
        hm_lbl = QLabel("HEAD MODE")
        hm_lbl.setStyleSheet(
            f"color: {_GREEN_TEXT}; font-size: 7px; font-weight: bold;"
            " font-family: 'Arial'; letter-spacing: 1px; background: transparent;"
        )
        hm_col.addWidget(hm_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        self._head_mode = QComboBox()
        self._head_mode.addItems(HEAD_MODE_NAMES)
        self._head_mode.setCurrentIndex(0)
        bind_fixed_height(self._head_mode, 20)
        bind_fixed_width(self._head_mode, 110)
        self._head_mode.setStyleSheet(
            f"QComboBox {{ background: #0f2a0f; color: {_GREEN_TEXT}; font-size: 9px;"
            f"  border: 1px solid {_GREEN_LITE}; border-radius: 2px; }}"
            f"QComboBox QAbstractItemView {{ background: #0f2a0f; color: {_GREEN_TEXT}; }}"
        )
        self._head_mode.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("head_mode", v)
        )
        self._head_mode.setToolTip("Head mode — selects which playback heads are active; multiple heads create richer, layered echoes")
        hm_col.addWidget(self._head_mode)
        row.addWidget(hm_panel)

        self._faceplate_layout.addWidget(header)

    def _build_row2(self):
        """Green controls panel: sync selector and all knobs."""
        # Outer green panel — matches RE-201 controls section
        panel = QFrame()
        panel.setStyleSheet(
            f"background: {_FACE}; border: 1px solid {_GREEN_LITE};"
        )
        row = QHBoxLayout(panel)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)

        # Sync / free time selector
        sync_col = QVBoxLayout()
        sync_col.setSpacing(2)
        sync_lbl = QLabel("TIME")
        sync_lbl.setStyleSheet(
            f"color: {_GREEN_TEXT}; font-size: 7px; font-weight: bold;"
            " letter-spacing: 1px; background: transparent;"
        )
        sync_col.addWidget(sync_lbl, alignment=Qt.AlignmentFlag.AlignCenter)
        self._sync = QComboBox()
        self._sync.addItems(SYNC_NAMES)
        self._sync.setCurrentIndex(5)
        bind_fixed_height(self._sync, 18)
        bind_fixed_width(self._sync, 68)
        self._sync.setStyleSheet(
            f"QComboBox {{ background: #0f2a0f; color: {_GREEN_TEXT}; font-size: 9px;"
            f"  border: 1px solid {_GREEN_LITE}; border-radius: 2px; }}"
            f"QComboBox QAbstractItemView {{ background: #0f2a0f; color: {_GREEN_TEXT}; }}"
        )
        self._sync.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("sync_time", v)
        )
        self._sync.setToolTip("Delay time — tempo-synced note division for the base delay time (1/32 to 1 bar)")
        sync_col.addWidget(self._sync)
        row.addLayout(sync_col)

        row.addStretch()

        # Knobs: param_name, label, default_value — green panel section
        _TIPS = {
            "feedback":    "Intensity (feedback) — how much delayed signal feeds back; high values produce many repeats",
            "wow_flutter": "Wow — slow tape speed fluctuation that creates pitch warble and vintage instability",
            "tone":        "Tone — treble roll-off on the feedback path; simulates tape head frequency response",
            "saturation":  "Saturation — tape compression and harmonic coloring that builds with each echo repeat",
            "gain":        "Echo level — output level of the delayed (echo) signal",
            "spring_mix":  "Spring reverb mix — built-in spring reverb blended with the echo; adds classic warm ambience",
        }
        for label, default, param in [
            ("INTENSITY", 0.40, "feedback"),
            ("WOW",       0.20, "wow_flutter"),
            ("TONE",      0.65, "tone"),
            ("SAT",       0.35, "saturation"),
            ("ECHO",      0.55, "gain"),
            ("SPRING",    0.00, "spring_mix"),
        ]:
            knob = RotaryKnob(color=_CHROME)
            knob.set_value(default)
            knob.value_changed.connect(lambda v, p=param: self.param_changed.emit(p, v))
            setattr(self, f"_{param}", knob)
            knob.setToolTip(_TIPS.get(param, ""))
            col = QVBoxLayout()
            col.setSpacing(1)
            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"color: {_GREEN_TEXT}; font-size: 7px; font-weight: bold;"
                " letter-spacing: 1px; background: transparent;"
            )
            col.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
            col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
            row.addLayout(col)

        self._faceplate_layout.addWidget(panel, stretch=1)

    # ── state sync ───────────────────────────────────────────────────────────

    def set_state(self, state: TapeDelayState):
        self.set_power(state.enabled, emit=False)

        self._head_mode.blockSignals(True)
        self._head_mode.setCurrentIndex(max(0, min(state.head_mode, len(HEAD_MODE_NAMES) - 1)))
        self._head_mode.blockSignals(False)

        self._sync.blockSignals(True)
        self._sync.setCurrentIndex(state.sync_time)
        self._sync.blockSignals(False)

        self._feedback.set_value(state.feedback)
        self._wow_flutter.set_value(state.wow_flutter)
        self._saturation.set_value(state.saturation)
        self._tone.set_value(state.tone)
        self._gain.set_value(state.gain)
        self._spring_mix.set_value(state.spring_mix)

