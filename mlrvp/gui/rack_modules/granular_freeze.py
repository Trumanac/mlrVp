"""Snow Globe — 1U granular freeze with scatter, pitch spread, and freeze toggle."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

# ── colour palette — frosted glass / ice-blue / snowflake ────────────────────
_FACE    = "#0a1520"   # deep dark blue-black faceplate
_ICE     = "#88ccee"   # ice blue accent
_FROST   = "#bbddee"   # frost white-blue
_CRYSTAL = "#aaddff"   # crystal sparkle
_DIM     = "#446688"   # dim label
_WHITE   = "#ddeeff"   # near-white


def _knob_col(label: str, knob: RotaryKnob, color: str = _FROST) -> QVBoxLayout:
    col = QVBoxLayout()
    col.setSpacing(0)
    col.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label)
    bind_fixed_height(lbl, 10)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"color: {color}; font-size: 7px; font-weight: bold;"
        "font-family: 'Arial'; letter-spacing: 1px;"
    )
    col.addWidget(lbl)
    col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
    return col


class _SnowGlobeLogo(QWidget):
    """Snowflake / crystal icon — Snow Globe brand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 24, 24)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 12, 12
        # Six-pointed snowflake
        p.setPen(QPen(QColor(_ICE), 1.2))
        for angle_deg in range(0, 360, 60):
            rad = math.radians(angle_deg)
            x2 = cx + 9 * math.cos(rad)
            y2 = cy + 9 * math.sin(rad)
            from PySide6.QtCore import QPointF
            p.drawLine(QPointF(cx, cy), QPointF(x2, y2))
            # Small branch near tip
            bx = cx + 6 * math.cos(rad)
            by = cy + 6 * math.sin(rad)
            for off in (-30, 30):
                bx2 = bx + 3 * math.cos(math.radians(angle_deg + off))
                by2 = by + 3 * math.sin(math.radians(angle_deg + off))
                p.drawLine(QPointF(bx, by), QPointF(bx2, by2))
        # Centre dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_CRYSTAL))
        p.drawEllipse(cx - 2, cy - 2, 4, 4)
        p.end()


class GranularFreezeModule(RackModuleBase):
    """1U Snow Globe — granular freeze processor."""

    effect_type = "granular_freeze"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="SNOW GLOBE", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(4)

        # Logo + title
        row.addWidget(_SnowGlobeLogo())
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel("SNOW GLOBE")
        name_lbl.setStyleSheet(
            f"color: {_ICE}; font-size: 9px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px;"
        )
        title_col.addWidget(name_lbl)
        sub_lbl = QLabel("GRANULAR FREEZE")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        row.addStretch()

        # GRAIN knob
        self._grain_size = RotaryKnob(color=_ICE)
        self._grain_size.set_value(0.5)
        self._grain_size.value_changed.connect(
            lambda v: self.param_changed.emit("grain_size", round(v, 3))
        )
        self._grain_size.setToolTip("Grain length — 5 to 500 ms")
        self._grain_size.set_formatter(lambda v: f"{5 + v * 495:.0f} ms")
        row.addLayout(_knob_col("GRAIN", self._grain_size, _ICE))

        # SCATTER knob
        self._scatter = RotaryKnob(color=_FROST)
        self._scatter.set_value(0.3)
        self._scatter.value_changed.connect(
            lambda v: self.param_changed.emit("scatter", round(v, 3))
        )
        self._scatter.setToolTip("Grain start position scatter — 0 = no scatter, 1 = full buffer randomisation")
        self._scatter.set_formatter(lambda v: f"{v * 100:.0f} %")
        row.addLayout(_knob_col("SCATTER", self._scatter, _FROST))

        # SPREAD knob
        self._spread = RotaryKnob(color=_CRYSTAL)
        self._spread.set_value(0.2)
        self._spread.value_changed.connect(
            lambda v: self.param_changed.emit("spread", round(v, 3))
        )
        self._spread.setToolTip("Random pitch detune per grain — 0 = unison, 1 = ±12 semitones")
        self._spread.set_formatter(lambda v: f"±{v * 12:.1f} st")
        row.addLayout(_knob_col("SPREAD", self._spread, _CRYSTAL))

        # DENSITY knob
        self._density = RotaryKnob(color=_WHITE)
        self._density.set_value(0.6)
        self._density.value_changed.connect(
            lambda v: self.param_changed.emit("density", round(v, 3))
        )
        self._density.setToolTip("Grain overlap density — fewer grains (sparse) to full cloud")
        self._density.set_formatter(lambda v: f"{max(2, int(12 * (0.2 + v * 0.8)))} grains")
        row.addLayout(_knob_col("DENSITY", self._density, _WHITE))

        # FREEZE toggle button
        self._freeze = QPushButton("FREEZE")
        self._freeze.setCheckable(True)
        self._freeze.setChecked(False)
        bind_fixed_size(self._freeze, 42, 18)
        self._freeze.clicked.connect(self._on_freeze)
        self._refresh_freeze()
        freeze_col = QVBoxLayout()
        freeze_col.setSpacing(0)
        freeze_col.setContentsMargins(0, 0, 0, 0)
        freeze_lbl = QLabel("HOLD")
        bind_fixed_height(freeze_lbl, 10)
        freeze_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        freeze_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        freeze_col.addWidget(freeze_lbl)
        freeze_col.addWidget(self._freeze, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(freeze_col)

        # MIX knob
        self._mix = RotaryKnob(color=_DIM)
        self._mix.set_value(0.4)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", round(v, 3))
        )
        self._mix.setToolTip("Wet/dry blend — 0 = clean, 1 = full grain cloud")
        row.addLayout(_knob_col("MIX", self._mix, _DIM))

        self._faceplate_layout.addLayout(row)

    def _on_freeze(self):
        self.param_changed.emit("freeze", self._freeze.isChecked())
        self._refresh_freeze()

    def _refresh_freeze(self):
        on = self._freeze.isChecked()
        if on:
            self._freeze.setStyleSheet(
                f"QPushButton {{ background: {_ICE}; border: 1px solid {_CRYSTAL};"
                f"border-radius: 3px; color: #000; font-size: 7px; font-weight: bold; }}"
            )
        else:
            self._freeze.setStyleSheet(
                f"QPushButton {{ background: #112233; border: 1px solid {_DIM};"
                f"border-radius: 3px; color: {_DIM}; font-size: 7px; font-weight: bold; }}"
                f"QPushButton:hover {{ border-color: {_ICE}; }}"
            )

    # ── State sync ────────────────────────────────────────────────────────

    def set_state(self, state):
        self.set_power(state.enabled, emit=False)
        self._grain_size.set_value(state.grain_size)
        self._scatter.set_value(state.scatter)
        self._spread.set_value(state.spread)
        self._density.set_value(state.density)
        self._freeze.setChecked(state.freeze)
        self._refresh_freeze()
        self._mix.set_value(state.mix)
