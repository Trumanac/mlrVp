"""Punch Card — 1U SPL Transient Designer 4 envelope shaper."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import TransientShaperState
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

# ── colour palette — off-white / green SPL style ────────────────────────────
_FACE  = "#f0ece4"   # warm off-white faceplate
_GREEN = "#3a7a3a"   # SPL green accent
_DARK  = "#2a2a2a"   # dark text
_DIM   = "#7a8a7a"   # dim green-grey labels
_HOLE  = "#1a1a1a"   # punch-card hole colour
_WHITE = "#ffffff"


class _PunchCardLogo(QWidget):
    """Punch card pattern — rows of small holes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 24, 16)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Card background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#d8d0c4"))
        p.drawRoundedRect(1, 1, 22, 14, 2, 2)
        # Punch holes — 3 rows × 5 cols (staggered)
        p.setBrush(QColor(_HOLE))
        for row in range(3):
            for col in range(5):
                if (row + col) % 2 == 0:
                    x = 3 + col * 4
                    y = 3 + row * 4
                    p.drawEllipse(x, y, 2, 2)
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


class TransientShaperModule(RackModuleBase):
    """1U Punch Card — SPL Transient Designer 4."""

    effect_type = "transient_shaper"
    _enabled_param: str = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="PUNCH CARD", parent=parent)
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
        title_row.addWidget(_PunchCardLogo())
        title = QLabel("PUNCH CARD")
        title.setStyleSheet(
            f"color: {_GREEN}; font-size: 9px; font-weight: bold;"
            "font-family: 'Arial Black'; letter-spacing: 2px;"
        )
        title_row.addWidget(title)
        title_col.addLayout(title_row)
        sub = QLabel("TRANSIENT DESIGNER")
        sub.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub)
        row.addLayout(title_col)

        # ── ATTACK knob (bipolar: 0.5 = center = 0.0) ──
        self._attack_knob = RotaryKnob(color=_GREEN)
        self._attack_knob.set_value(0.5)
        self._attack_knob.value_changed.connect(self._on_attack)
        self._attack_knob.setToolTip(
            "Attack shape: center=neutral, left=soften, right=punch")
        self._attack_knob.set_formatter(lambda v: f"{(v * 2.0 - 1.0) * 100.0:+.0f}%")
        row.addLayout(_knob_col("ATTACK", self._attack_knob, _GREEN))

        # ── SUSTAIN knob (bipolar: 0.5 = center = 0.0) ──
        self._sustain_knob = RotaryKnob(color=_GREEN)
        self._sustain_knob.set_value(0.5)
        self._sustain_knob.value_changed.connect(self._on_sustain)
        self._sustain_knob.setToolTip(
            "Sustain shape: center=neutral, left=gate, right=extend")
        self._sustain_knob.set_formatter(lambda v: f"{(v * 2.0 - 1.0) * 100.0:+.0f}%")
        row.addLayout(_knob_col("SUSTAIN", self._sustain_knob, _GREEN))

        # ── OUTPUT knob ──
        self._output_knob = RotaryKnob()
        self._output_knob.set_value(0.5)
        self._output_knob.value_changed.connect(
            lambda v: self.param_changed.emit("output", v * 12.0 - 6.0))
        self._output_knob.setToolTip("Output makeup gain; -6 to +6 dB")
        self._output_knob.set_formatter(lambda v: f"{v * 12.0 - 6.0:+.1f} dB")
        row.addLayout(_knob_col("OUTPUT", self._output_knob))

        # ── LINK toggle ──
        self._link_btn = QPushButton("LINK")
        self._link_btn.setCheckable(True)
        self._link_btn.setChecked(True)
        bind_fixed_size(self._link_btn, 36, 16)
        self._link_btn.setStyleSheet(
            f"QPushButton {{ background: {_DIM}; color: {_WHITE};"
            "  font-size: 7px; font-weight: bold; border-radius: 3px;"
            "  font-family: 'Arial'; }"
            f"QPushButton:checked {{ background: {_GREEN}; }}"
        )
        self._link_btn.toggled.connect(
            lambda v: self.param_changed.emit("stereo_link", v))
        self._link_btn.setToolTip("Stereo link — average L/R envelopes")
        link_col = QVBoxLayout()
        link_col.setSpacing(0)
        link_col.setContentsMargins(0, 4, 0, 0)
        link_col.addWidget(self._link_btn,
                           alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(link_col)

        self._faceplate_layout.addLayout(row)

    # ── callbacks ──

    def _on_attack(self, v: float):
        # 0–1 knob → -1..+1 bipolar
        self.param_changed.emit("attack_shape", v * 2.0 - 1.0)

    def _on_sustain(self, v: float):
        self.param_changed.emit("sustain_shape", v * 2.0 - 1.0)

    # ── state restore ──

    def set_state(self, state: TransientShaperState):
        self.set_power(state.enabled, emit=False)
        # Attack: -1..+1 → 0..1 knob
        self._attack_knob.blockSignals(True)
        self._attack_knob.set_value((state.attack_shape + 1.0) / 2.0)
        self._attack_knob.blockSignals(False)

        self._sustain_knob.blockSignals(True)
        self._sustain_knob.set_value((state.sustain_shape + 1.0) / 2.0)
        self._sustain_knob.blockSignals(False)

        self._output_knob.blockSignals(True)
        self._output_knob.set_value((state.output + 6.0) / 12.0)
        self._output_knob.blockSignals(False)

        self._link_btn.blockSignals(True)
        self._link_btn.setChecked(state.stereo_link)
        self._link_btn.blockSignals(False)
