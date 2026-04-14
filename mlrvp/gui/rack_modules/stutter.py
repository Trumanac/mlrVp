"""Broken Record — 1U stutter / glitch buffer-loop module."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QBrush

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

# ── colour palette — vinyl / cracked record ──────────────────────────────────
_FACE    = "#121012"   # near-black
_VINYL   = "#222222"   # dark groove
_RED     = "#cc3333"   # label red
_GROOVE  = "#666666"   # groove ring colour
_DIM     = "#555555"   # dim text
_CREAM   = "#ccccbb"   # knob labels

# ── Sync division names (index-aligned with SYNC_DIVISIONS in DSP) ───────────
SYNC_NAMES = [
    "1/32", "1/16t", "1/16", "1/8t", "1/8",
    "1/4t", "3/16", "1/4", "1/3", "3/8",
    "1/2", "3/4", "1 bar",
]


def _knob_col(label: str, knob: RotaryKnob, color: str = _CREAM) -> QVBoxLayout:
    col = QVBoxLayout()
    col.setSpacing(0)
    col.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label)
    lbl.setFixedHeight(10)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"color: {color}; font-size: 7px; font-weight: bold;"
        "font-family: 'Arial'; letter-spacing: 1px;"
    )
    col.addWidget(lbl)
    col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
    return col


class _VinylLogo(QWidget):
    """Broken vinyl record icon."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 11, 11
        # Outer disc
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_VINYL))
        p.drawEllipse(1, 1, 20, 20)
        # Groove rings
        p.setPen(QPen(QColor(_GROOVE), 0.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for r in (8, 6, 4):
            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)
        # Red label
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_RED))
        p.drawEllipse(cx - 3, cy - 3, 6, 6)
        # Spindle
        p.setBrush(QColor("#000"))
        p.drawEllipse(cx - 1, cy - 1, 2, 2)
        # Crack line
        p.setPen(QPen(QColor("#444444"), 0.8))
        p.drawLine(cx + 2, cy - 9, cx + 5, cy - 1)
        p.end()


class StutterModule(RackModuleBase):
    """1U Broken Record — stutter / glitch buffer looper."""

    effect_type = "stutter"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="BROKEN RECORD", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(4)

        # Logo + title
        row.addWidget(_VinylLogo())
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel("BROKEN RECORD")
        name_lbl.setStyleSheet(
            f"color: {_RED}; font-size: 9px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px;"
        )
        title_col.addWidget(name_lbl)
        sub_lbl = QLabel("STUTTER / GLITCH")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        row.addStretch()

        # SIZE knob
        self._size = RotaryKnob(color=_RED)
        self._size.set_value(0.3)
        self._size.value_changed.connect(
            lambda v: self.param_changed.emit("size", round(v, 3))
        )
        self._size.setToolTip("Loop size — 10 ms to 2 seconds (logarithmic)")
        self._size.set_formatter(
            lambda v: f"{10 * (200 ** v):.0f} ms" if 10 * (200 ** v) < 1000
            else f"{10 * (200 ** v) / 1000:.2f} s"
        )
        row.addLayout(_knob_col("SIZE", self._size, _RED))

        # RATE knob — tempo-synced beat divisions
        self._rate = RotaryKnob(color=_CREAM)
        self._rate.set_value(7 / 12)  # default = 1/4 note (index 7)
        self._rate.value_changed.connect(self._on_rate_changed)
        self._rate.setToolTip("Retrigger rate — tempo-synced beat division")
        self._rate.set_formatter(
            lambda v: SYNC_NAMES[max(0, min(round(v * 12), 12))]
        )
        row.addLayout(_knob_col("RATE", self._rate))

        # CHAOS knob
        self._chaos = RotaryKnob(color=_GROOVE)
        self._chaos.set_value(0.2)
        self._chaos.value_changed.connect(
            lambda v: self.param_changed.emit("chaos", round(v, 3))
        )
        self._chaos.setToolTip("Randomise loop start position — 0 = locked, 1 = wild")
        self._chaos.set_formatter(lambda v: f"{v * 100:.0f} %")
        row.addLayout(_knob_col("CHAOS", self._chaos, _GROOVE))

        # SMEAR knob
        self._smear = RotaryKnob(color=_DIM)
        self._smear.set_value(0.15)
        self._smear.value_changed.connect(
            lambda v: self.param_changed.emit("smear", round(v, 3))
        )
        self._smear.setToolTip("Crossfade at loop boundary — 0 = clicks, 1 = smooth wash")
        self._smear.set_formatter(lambda v: f"{v * 100:.0f} %")
        row.addLayout(_knob_col("SMEAR", self._smear, _DIM))

        # TRIGGER toggle
        self._trigger = QPushButton("OFF")
        self._trigger.setCheckable(True)
        self._trigger.setChecked(False)
        self._trigger.setFixedSize(42, 18)
        self._trigger.clicked.connect(self._on_trigger)
        self._refresh_trigger()
        trig_col = QVBoxLayout()
        trig_col.setSpacing(0)
        trig_col.setContentsMargins(0, 0, 0, 0)
        trig_lbl = QLabel("TRIG")
        trig_lbl.setFixedHeight(10)
        trig_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        trig_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        trig_col.addWidget(trig_lbl)
        trig_col.addWidget(self._trigger, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(trig_col)

        # MIX knob
        self._mix = RotaryKnob(color=_DIM)
        self._mix.set_value(0.5)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", round(v, 3))
        )
        self._mix.setToolTip("Wet/dry blend — 0 = clean, 1 = full stutter")
        row.addLayout(_knob_col("MIX", self._mix, _DIM))

        self._faceplate_layout.addLayout(row)

    def _on_rate_changed(self, value: float):
        """Map 0-1 knob to sync division index 0-12 and emit."""
        idx = max(0, min(round(value * (len(SYNC_NAMES) - 1)), len(SYNC_NAMES) - 1))
        self.param_changed.emit("rate", int(idx))

    def _on_trigger(self):
        active = self._trigger.isChecked()
        self.param_changed.emit("trigger", active)
        self._refresh_trigger()

    def _refresh_trigger(self):
        if self._trigger.isChecked():
            self._trigger.setText("LOOP")
            self._trigger.setStyleSheet(
                f"QPushButton {{ background: {_RED}; border: 1px solid #ff5555;"
                f"border-radius: 3px; color: #fff; font-size: 7px; font-weight: bold; }}"
            )
        else:
            self._trigger.setText("OFF")
            self._trigger.setStyleSheet(
                f"QPushButton {{ background: #1a1a1a; border: 1px solid {_DIM};"
                f"border-radius: 3px; color: {_DIM}; font-size: 7px; font-weight: bold; }}"
                f"QPushButton:hover {{ border-color: {_RED}; }}"
            )

    # ── State sync ────────────────────────────────────────────────────────

    def set_state(self, state):
        self.set_power(state.enabled, emit=False)
        self._size.set_value(state.size)
        # rate is an integer index 0-12; convert to 0-1 knob position
        idx = max(0, min(int(state.rate), len(SYNC_NAMES) - 1))
        self._rate.set_value(idx / max(1, len(SYNC_NAMES) - 1))
        self._chaos.set_value(state.chaos)
        self._smear.set_value(state.smear)
        self._trigger.setChecked(state.trigger)
        self._refresh_trigger()
        self._mix.set_value(state.mix)
