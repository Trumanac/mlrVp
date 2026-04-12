"""Personal Space — 1U Aphex 124A M-S Stereo Widener."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import StereoWidenerState

# ── colour palette — clean steel blue ────────────────────────────────────────
_FACE    = "#1a2a3e"   # dark steel faceplate
_STEEL   = "#5b8fb9"   # steel blue accent
_BRIGHT  = "#8ec8e8"   # bright blue — knob highlights
_DIM     = "#3a5a7a"   # dim blue — secondary labels
_WHITE   = "#d0dce8"   # near-white text
_ARROW   = "#e8a030"   # warm amber for spread arrows


class _SpreadLogo(QWidget):
    """L/R spread arrows logo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 16)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(_ARROW), 1.5)
        p.setPen(pen)
        # Left arrow
        cx, cy = 14, 8
        p.drawLine(cx - 2, cy, cx - 10, cy)
        p.drawLine(cx - 10, cy, cx - 7, cy - 3)
        p.drawLine(cx - 10, cy, cx - 7, cy + 3)
        # Right arrow
        p.drawLine(cx + 2, cy, cx + 10, cy)
        p.drawLine(cx + 10, cy, cx + 7, cy - 3)
        p.drawLine(cx + 10, cy, cx + 7, cy + 3)
        # Center dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_BRIGHT))
        p.drawEllipse(cx - 2, cy - 2, 4, 4)
        p.end()


def _knob_col(label: str, knob: RotaryKnob, color: str = _WHITE) -> QVBoxLayout:
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


class StereoWidenerModule(RackModuleBase):
    """1U Personal Space — Aphex 124A M-S Stereo Widener."""

    effect_type = "stereo_widener"
    _enabled_param: str = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="PERSONAL SPACE", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        # ── Title block ──
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 2)

        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(_SpreadLogo())
        title = QLabel("PERSONAL SPACE")
        title.setStyleSheet(
            f"color: {_BRIGHT}; font-size: 9px; font-weight: bold;"
            "font-family: 'Arial Black'; letter-spacing: 2px;"
        )
        title_row.addWidget(title)
        title_col.addLayout(title_row)
        sub = QLabel("STEREO WIDENER")
        sub.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub)
        row.addLayout(title_col)

        # ── Knobs ──
        # WIDTH: 0–1 knob → 0–2 value (0=mono, 0.5=unity, 1.0=max width)
        self._width_knob = RotaryKnob(color=_BRIGHT)
        self._width_knob.set_value(0.5)  # 1.0 = unity
        self._width_knob.value_changed.connect(self._on_width)
        self._width_knob.setToolTip(
            "Stereo width — 0 = mono, center = unity, max = 2x side")
        self._width_knob.set_formatter(lambda v: f"{v * 2.0:.1f}x")
        row.addLayout(_knob_col("WIDTH", self._width_knob, _BRIGHT))

        # CENTER: 0–1 knob → 0–2 value (mid level)
        self._center_knob = RotaryKnob(color=_STEEL)
        self._center_knob.set_value(0.5)  # 1.0 = unity
        self._center_knob.value_changed.connect(self._on_center)
        self._center_knob.setToolTip(
            "Mid (center) level — 0 = mute center, center = unity")
        self._center_knob.set_formatter(lambda v: f"{v * 2.0:.1f}x")
        row.addLayout(_knob_col("CENTER", self._center_knob, _STEEL))

        # LO-MONO: 0–1 exponential to 20–500 Hz
        self._lo_mono_knob = RotaryKnob(color=_ARROW)
        self._lo_mono_knob.set_value(0.46)  # ~120 Hz
        self._lo_mono_knob.value_changed.connect(self._on_lo_mono)
        self._lo_mono_knob.setToolTip(
            "Bass mono crossover — frequencies below this stay mono; "
            "20–500 Hz (exponential)")
        self._lo_mono_knob.set_formatter(lambda v: f"{20.0 * 25.0 ** v:.0f} Hz")
        row.addLayout(_knob_col("LO-MONO", self._lo_mono_knob, _ARROW))

        self._faceplate_layout.addLayout(row)

    # ── callbacks ──

    def _on_width(self, v: float):
        self.param_changed.emit("width", v * 2.0)

    def _on_center(self, v: float):
        self.param_changed.emit("center", v * 2.0)

    def _on_lo_mono(self, v: float):
        freq = 20.0 * (25.0 ** v)
        self.param_changed.emit("low_mono_freq", freq)

    # ── state restore ──

    def set_state(self, state: StereoWidenerState):
        import math

        self._width_knob.blockSignals(True)
        self._width_knob.set_value(max(0.0, min(1.0, state.width / 2.0)))
        self._width_knob.blockSignals(False)

        self._center_knob.blockSignals(True)
        self._center_knob.set_value(max(0.0, min(1.0, state.center / 2.0)))
        self._center_knob.blockSignals(False)

        # Reverse lo_mono: v = log(freq/20) / log(25)
        freq = max(20.0, min(500.0, state.low_mono_freq))
        v = math.log(freq / 20.0) / math.log(25.0)
        self._lo_mono_knob.blockSignals(True)
        self._lo_mono_knob.set_value(max(0.0, min(1.0, v)))
        self._lo_mono_knob.blockSignals(False)
