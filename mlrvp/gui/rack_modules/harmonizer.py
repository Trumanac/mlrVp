"""Wrong Lane — 2U dual harmonizer (pitch shifter)."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import HarmonizerState
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size, bind_fixed_width

# ── colour palette — Eventide green/black ────────────────────────────────────
_FACE   = "#0a0f0a"   # near-black green faceplate
_GREEN  = "#00cc66"   # Eventide green accent
_DIM    = "#116633"   # dim green labels
_LCD    = "#003311"   # LCD background
_LCD_FG = "#33ff88"   # LCD text
_SEP    = "#0d2018"   # separator line
_KNOB   = "#00cc66"   # knob color
_KNOB_B = "#44bbff"   # Voice B accent


class _HLogo(QWidget):
    """Road-lane arrow icon for Wrong Lane."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 24, 32)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(_GREEN), 2)
        p.setPen(pen)
        # Left lane arrow (pointing up-left — the "wrong" lane)
        p.drawLine(8, 28, 8, 8)
        p.drawLine(8, 8, 3, 14)
        p.drawLine(8, 8, 13, 14)
        # Right lane arrow (pointing down — normal)
        pen.setColor(QColor(_DIM))
        p.setPen(pen)
        p.drawLine(18, 4, 18, 24)
        p.drawLine(18, 24, 13, 18)
        p.drawLine(18, 24, 23, 18)
        # Dashed center line
        pen.setColor(QColor("#335533"))
        pen.setWidth(1)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(13, 4, 13, 28)
        p.end()


# ── Algorithm selector ────────────────────────────────────────────────────────

_ALGO_NAMES = ["DUAL DETUNE", "UP SHIFT", "DOWN SHIFT", "PITCH DELAY"]
_ALGO_SHORT = ["DUAL", "UP", "DOWN", "PTCH"]


class _AlgoSelector(QWidget):
    """4-button exclusive selector for harmonizer algorithm."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._algo = 0
        self._callback = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self._buttons = []
        for i, name in enumerate(_ALGO_SHORT):
            btn = QPushButton(name)
            bind_fixed_height(btn, 16)
            bind_fixed_width(btn, 36)
            btn.clicked.connect(lambda checked, idx=i: self._select(idx))
            layout.addWidget(btn)
            self._buttons.append(btn)

        self._refresh_styles()

    def _refresh_styles(self):
        for i, btn in enumerate(self._buttons):
            if i == self._algo:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #0a2a1a; border: 1px solid {_GREEN};"
                    f"border-radius: 2px; color: {_GREEN}; font-size: 7px; font-weight: bold;"
                    f"letter-spacing: 1px; padding: 1px 2px; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #0a1a0f; border: 1px solid #1a3020;"
                    f"border-radius: 2px; color: #334433; font-size: 7px; font-weight: bold;"
                    f"letter-spacing: 1px; padding: 1px 2px; }}"
                    f"QPushButton:hover {{ background: #0d2518; color: {_DIM}; }}"
                )

    def _select(self, algo: int):
        self._algo = algo
        self._refresh_styles()
        if self._callback:
            self._callback(algo)

    def set_algo(self, algo: int):
        self._algo = algo
        self._refresh_styles()

    def on_change(self, callback):
        self._callback = callback


# ── Helpers ───────────────────────────────────────────────────────────────────

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

class HarmonizerModule(RackModuleBase):
    """2U dual harmonizer – \"Wrong Lane\" pitch shifter."""

    effect_type = "harmonizer"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=2, faceplate_color=_FACE,
                         title="WRONG LANE", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        # ── Row 1: logo + title + LCD + ALGO selector ────────────────────────
        row1 = QHBoxLayout()
        row1.setContentsMargins(4, 2, 4, 1)
        row1.setSpacing(5)

        row1.addWidget(_HLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 4, 0, 0)
        name_lbl = QLabel("WRONG LANE")
        name_lbl.setStyleSheet(
            f"color: {_GREEN}; font-size: 11px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 2px;"
        )
        title_col.addWidget(name_lbl)
        sub_lbl = QLabel("HARMONIZER")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 3px;"
        )
        title_col.addWidget(sub_lbl)
        row1.addLayout(title_col)

        row1.addStretch()

        # LCD display
        self._lcd = QLabel("DUAL DETUNE")
        bind_fixed_size(self._lcd, 90, 20)
        self._lcd.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lcd.setStyleSheet(
            f"background: {_LCD}; color: {_LCD_FG}; font-size: 9px;"
            "font-family: 'Courier New'; font-weight: bold; letter-spacing: 1px;"
            "border: 1px solid #1a3020; border-radius: 2px; padding: 1px;"
        )
        row1.addWidget(self._lcd)

        row1.addSpacing(4)

        # ALGO selector
        self._algo_sel = _AlgoSelector()
        self._algo_sel.on_change(self._on_algo)
        row1.addWidget(self._algo_sel)

        self._faceplate_layout.addLayout(row1)

        # ── Separator ─────────────────────────────────────────────────────────
        sep = QWidget()
        bind_fixed_height(sep, 1)
        sep.setStyleSheet(f"background: {_SEP};")
        self._faceplate_layout.addWidget(sep)

        # ── Row 2: Voice A + Voice B + FDBK + MIX ────────────────────────────
        row2 = QHBoxLayout()
        row2.setContentsMargins(4, 2, 4, 2)
        row2.setSpacing(4)

        # Voice A section
        va_lbl = QLabel("VOICE A")
        va_lbl.setStyleSheet(
            f"color: {_GREEN}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        bind_fixed_height(va_lbl, 10)
        row2.addWidget(va_lbl, alignment=Qt.AlignmentFlag.AlignBottom)

        self._semi_a = RotaryKnob(color=_KNOB)
        self._semi_a.set_value(0.5)  # center = 0 semi
        self._semi_a.value_changed.connect(
            lambda v: self.param_changed.emit("voice_a_semitones", int(round(v * 48 - 24)))
        )
        self._semi_a.setToolTip("Voice A — pitch shift in semitones (-24 to +24)")
        self._semi_a.set_formatter(lambda v: f"{int(round(v * 48 - 24)):+d} st")
        row2.addLayout(_knob_col("SEMI", self._semi_a, _GREEN))

        self._fine_a = RotaryKnob(color=_KNOB)
        self._fine_a.set_value(0.5)
        self._fine_a.value_changed.connect(
            lambda v: self.param_changed.emit("voice_a_cents", int(round(v * 100 - 50)))
        )
        self._fine_a.setToolTip("Voice A — fine tune in cents (-50 to +50)")
        self._fine_a.set_formatter(lambda v: f"{int(round(v * 100 - 50)):+d} ct")
        row2.addLayout(_knob_col("FINE", self._fine_a, _GREEN))

        self._delay_a = RotaryKnob(color=_KNOB)
        self._delay_a.set_value(0.0)
        self._delay_a.value_changed.connect(
            lambda v: self.param_changed.emit("voice_a_delay_ms", round(v * 250.0, 1))
        )
        self._delay_a.setToolTip("Voice A — pre-delay 0–250 ms")
        self._delay_a.set_formatter(lambda v: f"{v * 250.0:.0f} ms")
        row2.addLayout(_knob_col("DLY", self._delay_a, _GREEN))

        row2.addSpacing(6)

        # Voice B section
        vb_lbl = QLabel("VOICE B")
        vb_lbl.setStyleSheet(
            f"color: {_KNOB_B}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        bind_fixed_height(vb_lbl, 10)
        row2.addWidget(vb_lbl, alignment=Qt.AlignmentFlag.AlignBottom)

        self._semi_b = RotaryKnob(color=_KNOB_B)
        self._semi_b.set_value(0.5)
        self._semi_b.value_changed.connect(
            lambda v: self.param_changed.emit("voice_b_semitones", int(round(v * 48 - 24)))
        )
        self._semi_b.setToolTip("Voice B — pitch shift in semitones (-24 to +24)")
        self._semi_b.set_formatter(lambda v: f"{int(round(v * 48 - 24)):+d} st")
        row2.addLayout(_knob_col("SEMI", self._semi_b, _KNOB_B))

        self._fine_b = RotaryKnob(color=_KNOB_B)
        self._fine_b.set_value(0.5)
        self._fine_b.value_changed.connect(
            lambda v: self.param_changed.emit("voice_b_cents", int(round(v * 100 - 50)))
        )
        self._fine_b.setToolTip("Voice B — fine tune in cents (-50 to +50)")
        self._fine_b.set_formatter(lambda v: f"{int(round(v * 100 - 50)):+d} ct")
        row2.addLayout(_knob_col("FINE", self._fine_b, _KNOB_B))

        self._delay_b = RotaryKnob(color=_KNOB_B)
        self._delay_b.set_value(0.0)
        self._delay_b.value_changed.connect(
            lambda v: self.param_changed.emit("voice_b_delay_ms", round(v * 250.0, 1))
        )
        self._delay_b.setToolTip("Voice B — pre-delay 0–250 ms")
        self._delay_b.set_formatter(lambda v: f"{v * 250.0:.0f} ms")
        row2.addLayout(_knob_col("DLY", self._delay_b, _KNOB_B))

        row2.addSpacing(6)

        # FDBK
        self._fdbk = RotaryKnob(color="#ff8844")
        self._fdbk.set_value(0.0)
        self._fdbk.value_changed.connect(
            lambda v: self.param_changed.emit("feedback", round(v * 0.9, 3))
        )
        self._fdbk.setToolTip("Feedback — routes output back into the harmonizer (0–0.9)")
        self._fdbk.set_formatter(lambda v: f"{v * 90.0:.0f}%")
        row2.addLayout(_knob_col("FDBK", self._fdbk, "#ff8844"))

        # MIX
        self._mix = RotaryKnob(color="#aaaaaa")
        self._mix.set_value(0.5)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", round(v, 3))
        )
        self._mix.setToolTip("Wet/dry blend of the harmonizer effect")
        row2.addLayout(_knob_col("MIX", self._mix, "#aaaaaa"))

        self._faceplate_layout.addLayout(row2)

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_algo(self, algo: int):
        if not (0 <= algo < len(_ALGO_NAMES)):
            return
        self._lcd.setText(_ALGO_NAMES[algo])
        self.param_changed.emit("algorithm", algo)

    # ── State sync ────────────────────────────────────────────────────────────

    def set_state(self, state):
        """Sync GUI from a HarmonizerState dataclass."""
        self.set_power(state.enabled, emit=False)
        self._semi_a.set_value((state.voice_a_semitones + 24) / 48.0)
        self._fine_a.set_value((state.voice_a_cents + 50) / 100.0)
        self._delay_a.set_value(state.voice_a_delay_ms / 250.0)
        self._semi_b.set_value((state.voice_b_semitones + 24) / 48.0)
        self._fine_b.set_value((state.voice_b_cents + 50) / 100.0)
        self._delay_b.set_value(state.voice_b_delay_ms / 250.0)
        self._fdbk.set_value(state.feedback / 0.9 if state.feedback else 0.0)
        self._mix.set_value(state.mix)
        self._algo_sel.set_algo(state.algorithm)
        if 0 <= state.algorithm < len(_ALGO_NAMES):
            self._lcd.setText(_ALGO_NAMES[state.algorithm])
