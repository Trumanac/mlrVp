"""The Velvet Rope — 1U Drawmer DS201-style frequency-conscious noise gate."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

# ── colour palette — dark industrial steel / Drawmer-inspired ────────────────
_FACE    = "#0c0e12"   # near-black gunmetal faceplate
_ACCENT  = "#cc3333"   # Drawmer red — primary accent
_DIM     = "#553333"   # dim red — secondary labels
_AMBER   = "#dd8822"   # warm amber — hold indicator
_GREEN   = "#33cc44"   # bright green — gate open
_RED     = "#cc3333"   # red — gate closed
_SILVER  = "#999999"   # silver — knob labels
_SEP     = "#1a1c22"   # separator line


def _knob_col(label: str, knob: RotaryKnob, color: str = _SILVER) -> QVBoxLayout:
    """Vertical stack: label on top, knob below."""
    col = QVBoxLayout()
    col.setSpacing(1)
    col.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"color: {color}; font-size: 7px; font-weight: bold;"
        "font-family: 'Arial'; letter-spacing: 1px;"
    )
    col.addWidget(lbl)
    col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
    return col


class _VelvetRopeLogo(QWidget):
    """Two stanchion posts with a sagging velvet rope — The Velvet Rope brand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 20)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Gold stanchion posts
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_AMBER))
        p.drawRoundedRect(2, 5, 3, 13, 1, 1)
        p.drawRoundedRect(17, 5, 3, 13, 1, 1)
        # Post caps (spheres)
        p.drawEllipse(1, 2, 5, 5)
        p.drawEllipse(16, 2, 5, 5)
        # Velvet rope — sagging catenary curve
        rope = QPainterPath()
        rope.moveTo(4, 6)
        rope.cubicTo(8, 15, 14, 15, 18, 6)
        p.setPen(QPen(QColor(_ACCENT), 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(rope)
        p.end()


class _GateLED(QWidget):
    """Traffic-light gate status indicator: green=open, amber=hold, red=closed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._color = _RED

    def set_color(self, color: str):
        self._color = color
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(1, 1, 10, 10)
        # Highlight dot
        p.setBrush(QColor(255, 255, 255, 60))
        p.drawEllipse(3, 2, 4, 4)
        p.end()


class _KeyFilterSelector(QWidget):
    """3-button exclusive selector for key filter: OFF / LP / HP."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._btns = []
        self._callback = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        for i, label in enumerate(("OFF", "LP", "HP")):
            btn = QPushButton(label)
            btn.setFixedSize(32, 16)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda checked, idx=i: self._select(idx))
            self._btns.append(btn)
            lay.addWidget(btn)
        self._update_styles()

    def _select(self, idx: int):
        for i, btn in enumerate(self._btns):
            btn.setChecked(i == idx)
        self._update_styles()
        if self._callback:
            self._callback(idx)

    def set_filter(self, idx: int):
        for i, btn in enumerate(self._btns):
            btn.blockSignals(True)
            btn.setChecked(i == idx)
            btn.blockSignals(False)
        self._update_styles()

    def on_change(self, cb):
        self._callback = cb

    def _update_styles(self):
        for i, btn in enumerate(self._btns):
            if btn.isChecked():
                btn.setStyleSheet(
                    f"QPushButton {{ background: {_ACCENT}; border: 1px solid {_ACCENT};"
                    f"border-radius: 2px; color: #ffffff; font-size: 7px; font-weight: bold;"
                    f"padding: 0px 1px; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #1a1a1a; border: 1px solid #333;"
                    f"border-radius: 2px; color: {_DIM}; font-size: 7px; font-weight: bold;"
                    f"padding: 0px 1px; }}"
                    f"QPushButton:hover {{ border-color: {_ACCENT}; color: {_ACCENT}; }}"
                )


class NoiseGateModule(RackModuleBase):
    """1U Noise Gate — Drawmer DS201 inspired, frequency-conscious gating."""

    effect_type = "noise_gate"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="THE VELVET ROPE", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(4)

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 0)

        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(_VelvetRopeLogo())
        name_lbl = QLabel("THE VELVET ROPE")
        name_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 9px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px;"
        )
        title_row.addWidget(name_lbl)
        title_col.addLayout(title_row)
        sub_lbl = QLabel("NOISE GATE")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        # Gate LED
        self._gate_led = _GateLED()
        row.addWidget(self._gate_led)
        row.addSpacing(2)

        # THRESH knob — maps to -80..0 dB
        self._thresh = RotaryKnob(color=_ACCENT)
        self._thresh.set_value(0.5)  # -40 dB default
        self._thresh.value_changed.connect(self._on_thresh)
        self._thresh.setToolTip("Gate threshold (-80 to 0 dB)")
        self._thresh.set_formatter(lambda v: f"{-80.0 + v * 80.0:.0f} dB")
        row.addLayout(_knob_col("THRESH", self._thresh, _ACCENT))

        # ATTACK knob — maps to 0.02–20 ms
        self._attack = RotaryKnob(color=_SILVER)
        self._attack.set_value(0.05)
        self._attack.value_changed.connect(self._on_attack)
        self._attack.setToolTip("Gate attack time (0.02–20 ms)")
        self._attack.set_formatter(lambda v: f"{0.02 * 1000.0 ** v:.2f} ms")
        row.addLayout(_knob_col("ATK", self._attack, _SILVER))

        # HOLD knob — maps to 2–4000 ms
        self._hold = RotaryKnob(color=_AMBER)
        self._hold.set_value(0.12)
        self._hold.value_changed.connect(self._on_hold)
        self._hold.setToolTip("Gate hold time (2–4000 ms)")
        self._hold.set_formatter(lambda v: f"{2.0 * 2000.0 ** v:.0f} ms")
        row.addLayout(_knob_col("HOLD", self._hold, _AMBER))

        # RELEASE knob — maps to 5–4000 ms
        self._release = RotaryKnob(color=_SILVER)
        self._release.set_value(0.3)
        self._release.value_changed.connect(self._on_release)
        self._release.setToolTip("Gate release time (5–4000 ms)")
        self._release.set_formatter(lambda v: f"{5.0 * 800.0 ** v:.0f} ms")
        row.addLayout(_knob_col("REL", self._release, _SILVER))

        # RANGE knob — maps to 0..-80 dB
        self._range = RotaryKnob(color=_SILVER)
        self._range.set_value(1.0)  # full close by default
        self._range.value_changed.connect(self._on_range)
        self._range.setToolTip("Gate range — max attenuation (0 to -80 dB)")
        self._range.set_formatter(lambda v: f"{-80.0 * v:.0f} dB")
        row.addLayout(_knob_col("RANGE", self._range, _SILVER))

        # KEY FILTER selector
        kf_col = QVBoxLayout()
        kf_col.setSpacing(1)
        kf_col.setContentsMargins(0, 0, 0, 0)
        kf_lbl = QLabel("KEY")
        kf_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        kf_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        kf_col.addWidget(kf_lbl)
        self._key_sel = _KeyFilterSelector()
        self._key_sel.on_change(self._on_key_filter)
        kf_col.addWidget(self._key_sel)
        row.addLayout(kf_col)

        # KEY FREQ knob — maps to 25–22000 Hz (log)
        self._key_freq = RotaryKnob(color=_DIM)
        self._key_freq.set_value(0.35)
        self._key_freq.value_changed.connect(self._on_key_freq)
        self._key_freq.setToolTip("Key filter frequency (25–22000 Hz)")
        self._key_freq.set_formatter(lambda v: f"{25.0 * 880.0 ** v:.0f} Hz")
        row.addLayout(_knob_col("FREQ", self._key_freq, _DIM))

        self._faceplate_layout.addLayout(row)

    # ── Parameter scaling helpers ─────────────────────────────────────────────

    @staticmethod
    def _knob_to_threshold(v: float) -> float:
        """0-1 knob → -80..0 dB."""
        return -80.0 + v * 80.0

    @staticmethod
    def _knob_to_attack(v: float) -> float:
        """0-1 knob → 0.02–20 ms (log)."""
        import math
        return 0.02 * (1000.0 ** v)

    @staticmethod
    def _knob_to_hold(v: float) -> float:
        """0-1 knob → 2–4000 ms (log)."""
        import math
        return 2.0 * (2000.0 ** v)

    @staticmethod
    def _knob_to_release(v: float) -> float:
        """0-1 knob → 5–4000 ms (log)."""
        import math
        return 5.0 * (800.0 ** v)

    @staticmethod
    def _knob_to_range(v: float) -> float:
        """0-1 knob → 0 to -80 dB. 0=no atten, 1=full close."""
        return -80.0 * v

    @staticmethod
    def _knob_to_key_freq(v: float) -> float:
        """0-1 knob → 25–22000 Hz (log)."""
        return 25.0 * (880.0 ** v)

    # ── Inverse scaling (state→knob) ─────────────────────────────────────────

    @staticmethod
    def _threshold_to_knob(db: float) -> float:
        return max(0.0, min(1.0, (db + 80.0) / 80.0))

    @staticmethod
    def _attack_to_knob(ms: float) -> float:
        import math
        ms = max(0.02, min(20.0, ms))
        return math.log(ms / 0.02) / math.log(1000.0)

    @staticmethod
    def _hold_to_knob(ms: float) -> float:
        import math
        ms = max(2.0, min(4000.0, ms))
        return math.log(ms / 2.0) / math.log(2000.0)

    @staticmethod
    def _release_to_knob(ms: float) -> float:
        import math
        ms = max(5.0, min(4000.0, ms))
        return math.log(ms / 5.0) / math.log(800.0)

    @staticmethod
    def _range_to_knob(db: float) -> float:
        return max(0.0, min(1.0, db / -80.0))

    @staticmethod
    def _key_freq_to_knob(hz: float) -> float:
        import math
        hz = max(25.0, min(22000.0, hz))
        return math.log(hz / 25.0) / math.log(880.0)

    # ── Knob change handlers ─────────────────────────────────────────────────

    def _on_thresh(self, v: float):
        self.param_changed.emit("threshold", self._knob_to_threshold(v))

    def _on_attack(self, v: float):
        self.param_changed.emit("attack", self._knob_to_attack(v))

    def _on_hold(self, v: float):
        self.param_changed.emit("hold", self._knob_to_hold(v))

    def _on_release(self, v: float):
        self.param_changed.emit("release", self._knob_to_release(v))

    def _on_range(self, v: float):
        self.param_changed.emit("range_db", self._knob_to_range(v))

    def _on_key_filter(self, idx: int):
        self.param_changed.emit("key_filter", idx)

    def _on_key_freq(self, v: float):
        self.param_changed.emit("key_freq", self._knob_to_key_freq(v))

    # ── State sync ────────────────────────────────────────────────────────────

    def set_state(self, state):
        """Sync GUI from a NoiseGateState dataclass."""
        self.set_power(state.enabled, emit=False)
        self._thresh.set_value(self._threshold_to_knob(state.threshold))
        self._attack.set_value(self._attack_to_knob(state.attack))
        self._hold.set_value(self._hold_to_knob(state.hold))
        self._release.set_value(self._release_to_knob(state.release))
        self._range.set_value(self._range_to_knob(state.range_db))
        self._key_sel.set_filter(state.key_filter)
        self._key_freq.set_value(self._key_freq_to_knob(state.key_freq))
