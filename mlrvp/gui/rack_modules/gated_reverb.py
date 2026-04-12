"""Gate Crasher — 1U gated reverb with gate/reverse mode toggle."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

# ── colour palette — riveted steel / industrial door / amber ─────────────────
_FACE    = "#181816"   # dark steel
_STEEL   = "#888880"   # brushed steel
_AMBER   = "#dd8822"   # amber LED indicator
_RUST    = "#995522"   # rust accent
_DIM     = "#555550"   # dim label
_CREAM   = "#ccccbb"   # cream text


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


class _SteelDoorLogo(QWidget):
    """Riveted steel door / gate icon."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Door frame
        p.setPen(QPen(QColor(_STEEL), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(3, 2, 16, 18)
        # Cross bars
        p.setPen(QPen(QColor(_DIM), 0.8))
        p.drawLine(3, 11, 19, 11)
        p.drawLine(11, 2, 11, 20)
        # Rivets
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_AMBER))
        for x, y in [(5, 4), (17, 4), (5, 18), (17, 18)]:
            p.drawEllipse(x - 1, y - 1, 3, 3)
        p.end()


class GatedReverbModule(RackModuleBase):
    """1U Gate Crasher — gated/reverse reverb."""

    effect_type = "gate_reverb"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="GATE CRASHER", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(4)

        # Logo + title
        row.addWidget(_SteelDoorLogo())
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel("GATE CRASHER")
        name_lbl.setStyleSheet(
            f"color: {_AMBER}; font-size: 9px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px;"
        )
        title_col.addWidget(name_lbl)
        sub_lbl = QLabel("GATED REVERB")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        row.addStretch()

        # ROOM knob
        self._room = RotaryKnob(color=_STEEL)
        self._room.set_value(0.6)
        self._room.value_changed.connect(
            lambda v: self.param_changed.emit("room", round(v, 3))
        )
        self._room.setToolTip("Reverb room size — small to large")
        self._room.set_formatter(lambda v: ["Tight", "Small", "Medium", "Large", "Hall"][min(4, int(v * 5))])
        row.addLayout(_knob_col("ROOM", self._room, _STEEL))

        # GATE knob
        self._gate_time = RotaryKnob(color=_AMBER)
        self._gate_time.set_value(0.3)
        self._gate_time.value_changed.connect(
            lambda v: self.param_changed.emit("gate_time", round(v, 3))
        )
        self._gate_time.setToolTip("Gate duration — 50 ms to 3 seconds")
        self._gate_time.set_formatter(
            lambda v: f"{50 + v * 2950:.0f} ms" if v < 0.322 else f"{0.05 + v * 2.95:.2f} s"
        )
        row.addLayout(_knob_col("GATE", self._gate_time, _AMBER))

        # SLOPE knob
        self._slope = RotaryKnob(color=_RUST)
        self._slope.set_value(0.3)
        self._slope.value_changed.connect(
            lambda v: self.param_changed.emit("slope", round(v, 3))
        )
        self._slope.setToolTip("Gate closure shape — hard cut to soft fade")
        self._slope.set_formatter(lambda v: ["Hard", "Sharp", "Medium", "Soft", "Gentle"][min(4, int(v * 5))])
        row.addLayout(_knob_col("SLOPE", self._slope, _RUST))

        # PRE knob
        self._pre_delay = RotaryKnob(color=_CREAM)
        self._pre_delay.set_value(0.15)
        self._pre_delay.value_changed.connect(
            lambda v: self.param_changed.emit("pre_delay", round(v, 3))
        )
        self._pre_delay.setToolTip("Pre-delay — 0 to 100 ms")
        self._pre_delay.set_formatter(lambda v: f"{v * 100:.0f} ms")
        row.addLayout(_knob_col("PRE", self._pre_delay, _CREAM))

        # MODE toggle button
        self._mode = QPushButton("GATE")
        self._mode.setCheckable(True)
        self._mode.setChecked(False)
        self._mode.setFixedSize(42, 18)
        self._mode.clicked.connect(self._on_mode)
        self._refresh_mode()
        mode_col = QVBoxLayout()
        mode_col.setSpacing(0)
        mode_col.setContentsMargins(0, 0, 0, 0)
        mode_lbl = QLabel("MODE")
        mode_lbl.setFixedHeight(10)
        mode_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        mode_col.addWidget(mode_lbl)
        mode_col.addWidget(self._mode, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(mode_col)

        # MIX knob
        self._mix = RotaryKnob(color=_DIM)
        self._mix.set_value(0.35)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", round(v, 3))
        )
        self._mix.setToolTip("Wet/dry blend — 0 = clean, 1 = full reverb")
        row.addLayout(_knob_col("MIX", self._mix, _DIM))

        self._faceplate_layout.addLayout(row)

    def _on_mode(self):
        mode = 1 if self._mode.isChecked() else 0
        self.param_changed.emit("mode", mode)
        self._refresh_mode()

    def _refresh_mode(self):
        if self._mode.isChecked():
            self._mode.setText("REV")
            self._mode.setStyleSheet(
                f"QPushButton {{ background: {_RUST}; border: 1px solid {_AMBER};"
                f"border-radius: 3px; color: #fff; font-size: 7px; font-weight: bold; }}"
            )
        else:
            self._mode.setText("GATE")
            self._mode.setStyleSheet(
                f"QPushButton {{ background: #222220; border: 1px solid {_DIM};"
                f"border-radius: 3px; color: {_AMBER}; font-size: 7px; font-weight: bold; }}"
                f"QPushButton:hover {{ border-color: {_AMBER}; }}"
            )

    # ── State sync ────────────────────────────────────────────────────────

    def set_state(self, state):
        self.set_power(state.enabled, emit=False)
        self._room.set_value(state.room)
        self._gate_time.set_value(state.gate_time)
        self._slope.set_value(state.slope)
        self._pre_delay.set_value(state.pre_delay)
        self._mode.setChecked(state.mode == 1)
        self._refresh_mode()
        self._mix.set_value(state.mix)
