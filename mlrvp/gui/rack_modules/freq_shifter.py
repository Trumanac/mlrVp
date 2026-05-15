"""Lil Freq — 1U Bode / Eventide-style Single-Sideband Frequency Shifter."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (QPainter, QColor, QPen, QFont,
                            QLinearGradient, QRadialGradient)

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import FreqShifterState
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

# ── colour palette — dark navy + cyan ────────────────────────────────────────
_FACE    = "#0e1926"   # deep navy faceplate
_CYAN    = "#00d4ff"   # cyan accent — primary highlight
_TEAL    = "#1a8a9a"   # teal — secondary
_DIM     = "#2a5060"   # dim teal — background labels
_WHITE   = "#c0dce8"   # near-white text
_AMBER   = "#f0a020"   # amber for Hz readout


class _HzBadge(QWidget):
    """Stylized Hz badge icon — Lil Freq module."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 28, 16)

    def paintEvent(self, event):
        import math
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Rounded rectangle badge background
        p.setPen(QPen(QColor(_CYAN), 1.2))
        p.setBrush(QColor(0, 40, 60, 180))
        p.drawRoundedRect(1, 1, 26, 14, 3, 3)

        # "Hz" text — bold, slightly italic, cyan
        font = QFont("Arial Black", 7, QFont.Weight.Bold)
        font.setItalic(True)
        p.setFont(font)
        p.setPen(QColor(_CYAN))
        p.drawText(0, 0, 28, 16, Qt.AlignmentFlag.AlignCenter, "Hz")

        # Small tilde above — suggests waveform / frequency
        p.setPen(QPen(QColor(_AMBER), 1.0))
        cx = 14
        for i in range(10):
            x = cx - 4 + i
            y = int(2 + math.sin(i * 0.9) * 1.2)
            if i > 0:
                p.drawLine(x - 1, prev_y, x, y)
            prev_y = y

        p.end()


class _GlassDisplay(QWidget):
    """Inset glass-window Hz readout with glow effect."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 76, 22)
        self._text = "+0.0 Hz"
        self._font = QFont("Consolas", 9, QFont.Weight.Bold)
        # Fallback if Consolas missing
        self._font.setStyleHint(QFont.StyleHint.Monospace)

    def set_text(self, text: str):
        self._text = text
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = QRectF(0, 0, w, h)

        # ── Outer bevel (raised rim) ──
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#3a4a5a"))
        p.drawRoundedRect(r, 3, 3)

        # ── Inner recess (shadow) ──
        inner = r.adjusted(1, 1, -1, -1)
        p.setBrush(QColor("#080e16"))
        p.drawRoundedRect(inner, 2, 2)

        # ── Glass background gradient (top-lit) ──
        glass = inner.adjusted(1, 1, -1, -1)
        grad = QLinearGradient(glass.topLeft(), glass.bottomLeft())
        grad.setColorAt(0.0, QColor(20, 35, 50, 255))
        grad.setColorAt(0.3, QColor(8, 14, 22, 255))
        grad.setColorAt(1.0, QColor(12, 20, 30, 255))
        p.setBrush(grad)
        p.drawRoundedRect(glass, 2, 2)

        # ── Subtle glass highlight (top reflection) ──
        highlight = QRectF(glass.x() + 4, glass.y() + 1,
                           glass.width() - 8, 5)
        h_grad = QLinearGradient(highlight.topLeft(), highlight.bottomLeft())
        h_grad.setColorAt(0.0, QColor(140, 200, 255, 30))
        h_grad.setColorAt(1.0, QColor(140, 200, 255, 0))
        p.setBrush(h_grad)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(highlight, 1, 1)

        # ── Amber text glow ──
        glow_col = QColor(_AMBER)
        glow_col.setAlpha(40)
        p.setFont(self._font)
        p.setPen(glow_col)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(glass.adjusted(dx, dy, dx, dy),
                       Qt.AlignmentFlag.AlignCenter, self._text)

        # ── Main amber text ──
        p.setPen(QColor(_AMBER))
        p.drawText(glass, Qt.AlignmentFlag.AlignCenter, self._text)

        # ── Bottom bevel highlight ──
        p.setPen(QPen(QColor(80, 100, 120, 60), 0.5))
        p.drawLine(int(r.x() + 3), int(r.bottom() - 1),
                   int(r.right() - 3), int(r.bottom() - 1))

        p.end()


def _knob_col(label: str, knob: RotaryKnob, color: str = _WHITE) -> QVBoxLayout:
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


class FreqShifterModule(RackModuleBase):
    """1U Lil Freq — SSB Frequency Shifter."""

    effect_type = "freq_shifter"
    _enabled_param: str = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="LIL FREQ", parent=parent)
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
        title_row.addWidget(_HzBadge())
        title = QLabel("LIL FREQ")
        title.setStyleSheet(
            f"color: {_CYAN}; font-size: 9px; font-weight: bold;"
            "font-family: 'Arial Black'; letter-spacing: 2px;"
        )
        title_row.addWidget(title)
        title_col.addLayout(title_row)

        sub_row = QHBoxLayout()
        sub_row.setSpacing(6)
        sub_row.setContentsMargins(0, 0, 0, 0)
        sub = QLabel("FREQ SHIFTER")
        sub.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        sub_row.addWidget(sub)

        # Hz readout — glass window display
        self._hz_display = _GlassDisplay()
        sub_row.addWidget(self._hz_display)
        title_col.addLayout(sub_row)
        row.addLayout(title_col)

        # ── Knobs ──
        # SHIFT: center-detent, 0.5 = 0 Hz, 0 = -500, 1 = +500
        self._shift_knob = RotaryKnob(color=_CYAN)
        self._shift_knob.set_value(0.5)
        self._shift_knob.value_changed.connect(self._on_shift)
        self._shift_knob.setToolTip(
            "Frequency shift — center = 0 Hz, ±500 Hz range")
        self._shift_knob.set_formatter(lambda v: f"{(v - 0.5) * 1000.0:+.1f} Hz")
        row.addLayout(_knob_col("SHIFT", self._shift_knob, _CYAN))

        # FDBK: 0–0.9
        self._fdbk_knob = RotaryKnob(color=_TEAL)
        self._fdbk_knob.set_value(0.0)
        self._fdbk_knob.value_changed.connect(self._on_fdbk)
        self._fdbk_knob.setToolTip("Feedback — wet output fed back into input")
        self._fdbk_knob.set_formatter(lambda v: f"{v * 90.0:.0f}%")
        row.addLayout(_knob_col("FDBK", self._fdbk_knob, _TEAL))

        # MIX
        self._mix_knob = RotaryKnob(color=_WHITE)
        self._mix_knob.set_value(0.5)
        self._mix_knob.value_changed.connect(self._on_mix)
        self._mix_knob.setToolTip("Dry/Wet mix")
        row.addLayout(_knob_col("MIX", self._mix_knob, _WHITE))

        self._faceplate_layout.addLayout(row)

    # ── callbacks ──

    def _on_shift(self, v: float):
        hz = (v - 0.5) * 1000.0  # 0→-500, 0.5→0, 1→+500
        self._hz_display.set_text(f"{hz:+.1f} Hz")
        self.param_changed.emit("shift_hz", hz)

    def _on_fdbk(self, v: float):
        self.param_changed.emit("feedback", v * 0.9)

    def _on_mix(self, v: float):
        self.param_changed.emit("mix", v)

    # ── state restore ──

    def set_state(self, state: FreqShifterState):
        self.set_power(state.enabled, emit=False)
        # SHIFT: hz → knob 0–1
        knob_v = max(0.0, min(1.0, state.shift_hz / 1000.0 + 0.5))
        self._shift_knob.blockSignals(True)
        self._shift_knob.set_value(knob_v)
        self._shift_knob.blockSignals(False)
        self._hz_display.set_text(f"{state.shift_hz:+.1f} Hz")

        # FDBK: feedback 0–0.9 → knob 0–1
        self._fdbk_knob.blockSignals(True)
        self._fdbk_knob.set_value(max(0.0, min(1.0, state.feedback / 0.9)))
        self._fdbk_knob.blockSignals(False)

        # MIX
        self._mix_knob.blockSignals(True)
        self._mix_knob.set_value(max(0.0, min(1.0, state.mix)))
        self._mix_knob.blockSignals(False)
