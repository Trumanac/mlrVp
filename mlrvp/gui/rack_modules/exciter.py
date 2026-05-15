"""Vitamin E — 1U Aphex Aural Exciter Type C harmonic enhancer."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import ExciterState
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

# ── colour palette — clinical white / sky blue Aphex look ────────────────────
_FACE    = "#e8edf2"   # near-white faceplate
_SKY     = "#2e9ad0"   # sky blue — primary accent
_DARK    = "#1a2a3a"   # dark navy text
_DIM     = "#6688aa"   # dim blue — secondary labels
_PILL    = "#d04040"   # red pill colour for logo accent
_WHITE   = "#ffffff"


class _VitaminLogo(QWidget):
    """Pill capsule with 'E' — Vitamin E brand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 24, 16)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Left half — red
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_PILL))
        p.drawRoundedRect(0, 1, 13, 14, 7, 7)
        # Right half — sky blue
        p.setBrush(QColor(_SKY))
        p.drawRoundedRect(11, 1, 13, 14, 7, 7)
        # Center seam line
        p.setPen(QPen(QColor(_DARK), 0.5))
        p.drawLine(12, 2, 12, 14)
        # "E" letter
        p.setPen(Qt.PenStyle.NoPen)
        font = QFont("Arial", 7, QFont.Weight.ExtraBold)
        p.setFont(font)
        p.setPen(QColor(_WHITE))
        p.drawText(0, 0, 24, 16, Qt.AlignmentFlag.AlignCenter, "E")
        p.end()


def _knob_col(label: str, knob: RotaryKnob, color: str = _DARK) -> QVBoxLayout:
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


class ExciterModule(RackModuleBase):
    """1U Vitamin E — Aphex Aural Exciter Type C."""

    effect_type = "exciter"
    _enabled_param: str = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="VITAMIN E", parent=parent)
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
        title_row.addWidget(_VitaminLogo())
        title = QLabel("VITAMIN E")
        title.setStyleSheet(
            f"color: {_SKY}; font-size: 9px; font-weight: bold;"
            "font-family: 'Arial Black'; letter-spacing: 2px;"
        )
        title_row.addWidget(title)
        title_col.addLayout(title_row)
        sub = QLabel("AURAL EXCITER")
        sub.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub)
        row.addLayout(title_col)

        # ── Knobs ──
        # TUNE: 0–1 maps exponentially to 1000–12000 Hz
        self._tune_knob = RotaryKnob(color=_SKY)
        self._tune_knob.set_value(0.6)   # ~5000 Hz
        self._tune_knob.value_changed.connect(self._on_tune)
        self._tune_knob.setToolTip(
            "Hi-path HPF frequency — where harmonics generation begins; "
            "1 kHz to 12 kHz (exponential)")
        self._tune_knob.set_formatter(lambda v: f"{1000.0 * 12.0 ** v / 1000.0:.1f} kHz")
        row.addLayout(_knob_col("TUNE", self._tune_knob, _SKY))

        self._mix_knob = RotaryKnob(color=_SKY)
        self._mix_knob.set_value(0.3)
        self._mix_knob.value_changed.connect(
            lambda v: self.param_changed.emit("mix", v))
        self._mix_knob.setToolTip("Harmonics blend — how much sparkle to add")
        row.addLayout(_knob_col("MIX", self._mix_knob))

        # LO TUNE: 0–1 maps exponentially to 50–1000 Hz
        self._lo_tune_knob = RotaryKnob(color=_PILL)
        self._lo_tune_knob.set_value(0.45)  # ~200 Hz
        self._lo_tune_knob.value_changed.connect(self._on_lo_tune)
        self._lo_tune_knob.setToolTip(
            "Low-end crossover frequency; 50–1000 Hz (exponential)")
        self._lo_tune_knob.set_formatter(lambda v: f"{50.0 * 20.0 ** v:.0f} Hz")
        row.addLayout(_knob_col("LO TUN", self._lo_tune_knob, _PILL))

        self._lo_mix_knob = RotaryKnob(color=_PILL)
        self._lo_mix_knob.set_value(0.2)
        self._lo_mix_knob.value_changed.connect(
            lambda v: self.param_changed.emit("lo_mix", v))
        self._lo_mix_knob.setToolTip("Low-end phase enhancement amount")
        row.addLayout(_knob_col("LO MIX", self._lo_mix_knob))

        self._output_knob = RotaryKnob()
        self._output_knob.set_value(0.5)  # center = 0 dB
        self._output_knob.value_changed.connect(
            lambda v: self.param_changed.emit("output", v * 12.0 - 6.0))
        self._output_knob.setToolTip("Output makeup gain; -6 to +6 dB")
        self._output_knob.set_formatter(lambda v: f"{v * 12.0 - 6.0:+.1f} dB")
        row.addLayout(_knob_col("OUTPUT", self._output_knob))

        self._faceplate_layout.addLayout(row)

    # ── callbacks ──

    def _on_tune(self, v: float):
        # 0–1 exponential to 1000–12000 Hz
        freq = 1000.0 * (12.0 ** v)
        self.param_changed.emit("tune", freq)

    def _on_lo_tune(self, v: float):
        # 0–1 exponential to 50–1000 Hz
        freq = 50.0 * (20.0 ** v)
        self.param_changed.emit("lo_tune", freq)

    # ── state restore ──

    def set_state(self, state: ExciterState):
        self.set_power(state.enabled, emit=False)
        import math
        # Reverse TUNE: v = log(freq/1000) / log(12)
        freq = max(1000.0, min(12000.0, state.tune))
        v = math.log(freq / 1000.0) / math.log(12.0)
        self._tune_knob.blockSignals(True)
        self._tune_knob.set_value(max(0.0, min(1.0, v)))
        self._tune_knob.blockSignals(False)

        # Reverse LO TUNE: v = log(freq/50) / log(20)
        lo_freq = max(50.0, min(1000.0, state.lo_tune))
        lo_v = math.log(lo_freq / 50.0) / math.log(20.0)
        self._lo_tune_knob.blockSignals(True)
        self._lo_tune_knob.set_value(max(0.0, min(1.0, lo_v)))
        self._lo_tune_knob.blockSignals(False)

        for param, val in [
            ("mix", state.mix),
            ("lo_mix", state.lo_mix),
            ("output", (state.output + 6.0) / 12.0),  # -6..+6 -> 0..1
        ]:
            knob = getattr(self, f"_{param}_knob")
            knob.blockSignals(True)
            knob.set_value(val)
            knob.blockSignals(False)
