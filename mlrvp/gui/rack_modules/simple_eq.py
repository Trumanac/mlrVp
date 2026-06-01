"""Golden Ears — 1U Pultec-style 3-band parametric EQ."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QPen

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

# ── colour palette — warm gold/cream Pultec look ────────────────────────────
_FACE   = "#1c1810"   # dark warm cream faceplate
_GOLD   = "#d4a843"   # warm gold accent
_DIM    = "#8a7034"   # dim gold
_CREAM  = "#e8d8a8"   # cream highlight
_SILVER = "#999999"   # knob labels
_CENTER = "#556644"   # center detent indicator


def _knob_col(label: str, knob: RotaryKnob, color: str = _SILVER) -> QVBoxLayout:
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


class _EQCurveLogo(QWidget):
    """Stylised EQ frequency response curve icon."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 30, 20)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Draw a gentle EQ curve: low boost, mid cut, high boost
        pen = QPen(QColor(_GOLD), 1.5)
        p.setPen(pen)
        pts = [
            QPointF(0, 12), QPointF(4, 8), QPointF(8, 6),
            QPointF(12, 10), QPointF(15, 14), QPointF(18, 12),
            QPointF(22, 8), QPointF(26, 6), QPointF(30, 8),
        ]
        for i in range(len(pts) - 1):
            p.drawLine(pts[i], pts[i + 1])
        # Baseline
        p.setPen(QPen(QColor(_DIM), 0.5, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(0, 10), QPointF(30, 10))
        p.end()


class SimpleEQModule(RackModuleBase):
    """1U Golden Ears — Pultec-inspired 3-band EQ."""

    effect_type = "simple_eq"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="GOLDEN EARS", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(5)

        # Logo + title
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        title_row = QHBoxLayout()
        title_row.setSpacing(3)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(_EQCurveLogo())
        name_lbl = QLabel("GOLDEN EARS")
        name_lbl.setStyleSheet(
            f"color: {_GOLD}; font-size: 9px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px;"
        )
        title_row.addWidget(name_lbl)
        title_col.addLayout(title_row)
        sub_lbl = QLabel("3-BAND EQ")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        row.addStretch()

        # LOW knob (±12 dB, center-detent)
        self._low = RotaryKnob(color="#dd6633")
        self._low.set_value(0.5)  # 0.5 = 0 dB
        self._low.value_changed.connect(self._on_low)
        self._low.setToolTip("Low shelf ±12 dB")
        self._low.set_formatter(lambda v: f"{(v - 0.5) * 24.0:+.1f} dB")
        row.addLayout(_knob_col("LOW", self._low, "#dd6633"))

        # MID knob (±12 dB, center-detent)
        self._mid = RotaryKnob(color=_GOLD)
        self._mid.set_value(0.5)
        self._mid.value_changed.connect(self._on_mid)
        self._mid.setToolTip("Mid bell ±12 dB")
        self._mid.set_formatter(lambda v: f"{(v - 0.5) * 24.0:+.1f} dB")
        row.addLayout(_knob_col("MID", self._mid, _GOLD))

        # HIGH knob (±12 dB, center-detent)
        self._high = RotaryKnob(color="#88bbdd")
        self._high.set_value(0.5)
        self._high.value_changed.connect(self._on_high)
        self._high.setToolTip("High shelf ±12 dB")
        self._high.set_formatter(lambda v: f"{(v - 0.5) * 24.0:+.1f} dB")
        row.addLayout(_knob_col("HIGH", self._high, "#88bbdd"))

        row.addSpacing(4)

        # MIX knob
        self._mix = RotaryKnob(color=_SILVER)
        self._mix.set_value(1.0)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", round(v, 3))
        )
        self._mix.setToolTip("Wet/dry blend")
        row.addLayout(_knob_col("MIX", self._mix, _SILVER))

        self._faceplate_layout.addLayout(row)

    # ── Parameter scaling ─────────────────────────────────────────────────

    @staticmethod
    def _knob_to_db(v: float) -> float:
        """0-1 knob → -12 to +12 dB (linear, 0.5 = 0 dB)."""
        return (v - 0.5) * 24.0

    @staticmethod
    def _db_to_knob(db: float) -> float:
        return max(0.0, min(1.0, db / 24.0 + 0.5))

    def _on_low(self, v: float):
        self.param_changed.emit("low", round(self._knob_to_db(v), 1))

    def _on_mid(self, v: float):
        self.param_changed.emit("mid", round(self._knob_to_db(v), 1))

    def _on_high(self, v: float):
        self.param_changed.emit("high", round(self._knob_to_db(v), 1))

    # ── State sync ────────────────────────────────────────────────────────

    def set_state(self, state):
        self.set_power(state.enabled, emit=False)
        self._low.set_value(self._db_to_knob(state.low))
        self._mid.set_value(self._db_to_knob(state.mid))
        self._high.set_value(self._db_to_knob(state.high))
        self._mix.set_value(state.mix)
