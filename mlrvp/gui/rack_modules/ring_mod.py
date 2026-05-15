"""Ring Leader — 1U Ring Modulator rack module."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QConicalGradient

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import RingModState
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size, bind_fixed_width

# ── colour palette — dark metallic copper/bronze ─────────────────────────────
_FACE   = "#1a0f0a"   # dark bronze faceplate
_COPPER = "#cc7733"   # copper accent
_DIM    = "#885522"   # dim copper labels
_LED    = "#ff8844"   # LED orange
_SEP    = "#2a1a10"   # separator


class _RingLogo(QWidget):
    """Concentric ring icon — represents ring modulation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 28, 28)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 14, 14
        pen = QPen(QColor(_COPPER), 2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - 11, cy - 11, 22, 22)
        pen.setColor(QColor(_DIM))
        p.setPen(pen)
        p.drawEllipse(cx - 7, cy - 7, 14, 14)
        pen.setColor(QColor(_LED))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawEllipse(cx - 3, cy - 3, 6, 6)
        p.end()


# ── Shape selector ────────────────────────────────────────────────────────────

_SHAPE_NAMES = ["SINE", "TRI", "SQR"]


class _ShapeSelector(QWidget):
    """3-button exclusive selector for carrier waveform shape."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shape = 0
        self._callback = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self._buttons = []
        for i, name in enumerate(_SHAPE_NAMES):
            btn = QPushButton(name)
            bind_fixed_height(btn, 16)
            bind_fixed_width(btn, 32)
            btn.clicked.connect(lambda checked, idx=i: self._select(idx))
            layout.addWidget(btn)
            self._buttons.append(btn)

        self._refresh_styles()

    def _refresh_styles(self):
        for i, btn in enumerate(self._buttons):
            if i == self._shape:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #2a1a0a; border: 1px solid {_COPPER};"
                    f"border-radius: 2px; color: {_COPPER}; font-size: 7px; font-weight: bold;"
                    f"letter-spacing: 1px; padding: 1px 2px; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #1a0f08; border: 1px solid #3a2818;"
                    f"border-radius: 2px; color: #554433; font-size: 7px; font-weight: bold;"
                    f"letter-spacing: 1px; padding: 1px 2px; }}"
                    f"QPushButton:hover {{ background: #251508; color: {_DIM}; }}"
                )

    def _select(self, shape: int):
        self._shape = shape
        self._refresh_styles()
        if self._callback:
            self._callback(shape)

    def set_shape(self, shape: int):
        self._shape = shape
        self._refresh_styles()

    def on_change(self, callback):
        self._callback = callback


# ── Helper ────────────────────────────────────────────────────────────────────

def _knob_col(label: str, knob: RotaryKnob, color: str = _DIM) -> QVBoxLayout:
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


# ── Main module ───────────────────────────────────────────────────────────────

class RingModModule(RackModuleBase):
    """1U Ring Leader — ring modulator."""

    effect_type = "ring_mod"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="RING LEADER", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(5)

        row.addWidget(_RingLogo())

        # Title
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 0)
        name_lbl = QLabel("RING LEADER")
        name_lbl.setStyleSheet(
            f"color: {_COPPER}; font-size: 10px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 2px;"
        )
        title_col.addWidget(name_lbl)
        sub_lbl = QLabel("RING MOD")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 3px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        row.addStretch()

        # Shape selector
        self._shape_sel = _ShapeSelector()
        self._shape_sel.on_change(self._on_shape)
        row.addWidget(self._shape_sel)

        row.addSpacing(4)

        # FREQ knob — logarithmic 0.1–5000 Hz
        self._freq = RotaryKnob(color=_COPPER)
        self._freq.set_value(0.39)  # ~440 Hz on log scale
        self._freq.value_changed.connect(self._on_freq)
        self._freq.setToolTip("Carrier frequency — 0.1 Hz (tremolo) to 5000 Hz (metallic)")
        self._freq.set_formatter(lambda v: f"{0.1 * 50000.0 ** v:.1f} Hz" if 0.1 * 50000.0 ** v < 100 else f"{0.1 * 50000.0 ** v:.0f} Hz")
        row.addLayout(_knob_col("FREQ", self._freq, _COPPER))

        # DEPTH knob
        self._depth = RotaryKnob(color=_LED)
        self._depth.set_value(1.0)
        self._depth.value_changed.connect(
            lambda v: self.param_changed.emit("depth", round(v, 3))
        )
        self._depth.setToolTip("Modulation depth — 0 = bypass, 1 = full ring mod")
        row.addLayout(_knob_col("DEPTH", self._depth, _LED))

        # MIX knob
        self._mix = RotaryKnob(color="#aaaaaa")
        self._mix.set_value(0.5)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", round(v, 3))
        )
        self._mix.setToolTip("Wet/dry blend")
        row.addLayout(_knob_col("MIX", self._mix, "#aaaaaa"))

        self._faceplate_layout.addLayout(row)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_freq(self, v: float):
        # Log scale: 0.1 Hz to 5000 Hz
        import math
        hz = 0.1 * math.pow(50000.0, v)
        self.param_changed.emit("freq_hz", round(hz, 2))

    def _on_shape(self, shape: int):
        self.param_changed.emit("shape", shape)

    # ── State sync ────────────────────────────────────────────────────────────

    def set_state(self, state):
        """Sync GUI from a RingModState dataclass."""
        import math
        self.set_power(state.enabled, emit=False)
        # Reverse log scale: v = log(hz/0.1) / log(50000)
        hz = max(0.1, state.freq_hz)
        self._freq.set_value(math.log(hz / 0.1) / math.log(50000.0))
        self._depth.set_value(state.depth)
        self._mix.set_value(state.mix)
        self._shape_sel.set_shape(state.shape)
