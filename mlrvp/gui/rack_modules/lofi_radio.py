"""Tin Can — 1U lo-fi radio / telephone bandpass filter."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

# ── colour palette — vintage radio: olive/rust/cream ─────────────────────────
_FACE   = "#141210"   # dark olive brown
_RUST   = "#bb6633"   # rust orange
_OLIVE  = "#888844"   # olive accent
_DIM    = "#665533"   # dim label
_CREAM  = "#ccbb88"   # cream dial marks
_RED    = "#cc4433"   # tuning indicator


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


class _RadioLogo(QWidget):
    """Vintage radio dial icon with antenna."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        from PySide6.QtCore import QPointF
        # Radio body
        p.setPen(QPen(QColor(_RUST), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(2, 8, 20, 12, 2, 2)
        # Speaker grille lines
        p.setPen(QPen(QColor(_DIM), 0.5))
        for y in range(10, 19, 2):
            p.drawLine(4, y, 12, y)
        # Dial
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_CREAM))
        p.drawRect(14, 10, 6, 3)
        # Antenna
        p.setPen(QPen(QColor(_OLIVE), 1.0))
        p.drawLine(QPointF(18, 8), QPointF(20, 2))
        # Tuning dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_RED))
        p.drawEllipse(20, 1, 3, 3)
        p.end()


class _BandSelector(QWidget):
    """Radio band preset selector."""

    _BANDS = ["AM", "TEL", "TRAN", "CUST"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._idx = 0
        self._callback = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self._btns = []
        for i, label in enumerate(self._BANDS):
            btn = QPushButton(label)
            btn.setFixedSize(28, 14)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.clicked.connect(lambda checked, idx=i: self._select(idx))
            self._btns.append(btn)
            lay.addWidget(btn)
        self._refresh()

    def _select(self, idx: int):
        self._idx = idx
        for i, btn in enumerate(self._btns):
            btn.setChecked(i == idx)
        self._refresh()
        if self._callback:
            self._callback(idx)

    def set_index(self, idx: int):
        self._idx = max(0, min(3, idx))
        for i, btn in enumerate(self._btns):
            btn.blockSignals(True)
            btn.setChecked(i == self._idx)
            btn.blockSignals(False)
        self._refresh()

    def on_change(self, cb):
        self._callback = cb

    def _refresh(self):
        for i, btn in enumerate(self._btns):
            if btn.isChecked():
                btn.setStyleSheet(
                    f"QPushButton {{ background: {_RUST}; border: 1px solid {_CREAM};"
                    f"border-radius: 2px; color: #fff; font-size: 7px; font-weight: bold; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #1a1612; border: 1px solid #443322;"
                    f"border-radius: 2px; color: {_DIM}; font-size: 7px; font-weight: bold; }}"
                    f"QPushButton:hover {{ border-color: {_RUST}; }}"
                )


class LoFiRadioModule(RackModuleBase):
    """1U Tin Can — lo-fi radio / telephone filter."""

    effect_type = "lofi_radio"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="TIN CAN", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(4)

        # Logo + title
        row.addWidget(_RadioLogo())
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel("TIN CAN")
        name_lbl.setStyleSheet(
            f"color: {_RUST}; font-size: 10px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 2px;"
        )
        title_col.addWidget(name_lbl)
        sub_lbl = QLabel("LO-FI RADIO")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        row.addStretch()

        # BAND selector
        band_col = QVBoxLayout()
        band_col.setSpacing(0)
        band_col.setContentsMargins(0, 0, 0, 0)
        band_lbl = QLabel("BAND")
        band_lbl.setFixedHeight(10)
        band_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        band_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        band_col.addWidget(band_lbl)
        self._band_sel = _BandSelector()
        self._band_sel.on_change(self._on_band_change)
        band_col.addWidget(self._band_sel)
        row.addLayout(band_col)

        row.addSpacing(2)

        # LO CUT knob — visible in Custom mode (20–2000 Hz, log)
        self._lo_cut = RotaryKnob(color=_OLIVE)
        self._lo_cut.set_value(0.35)  # ~300 Hz default
        self._lo_cut.value_changed.connect(self._on_lo_cut)
        self._lo_cut.setToolTip("Low cut frequency (20–2000 Hz)")
        self._lo_cut.set_formatter(lambda v: f"{20.0 * 100.0 ** v:.0f} Hz")
        self._lo_col = _knob_col("LO", self._lo_cut, _OLIVE)
        row.addLayout(self._lo_col)

        # HI CUT knob — visible in Custom mode (500–16000 Hz, log)
        self._hi_cut = RotaryKnob(color=_CREAM)
        self._hi_cut.set_value(0.55)  # ~3400 Hz default
        self._hi_cut.value_changed.connect(self._on_hi_cut)
        self._hi_cut.setToolTip("High cut frequency (500–16000 Hz)")
        self._hi_cut.set_formatter(lambda v: f"{500.0 * 32.0 ** v:.0f} Hz")
        self._hi_col = _knob_col("HI", self._hi_cut, _CREAM)
        row.addLayout(self._hi_col)

        # NOISE knob
        self._noise = RotaryKnob(color=_OLIVE)
        self._noise.set_value(0.15)
        self._noise.value_changed.connect(
            lambda v: self.param_changed.emit("noise", round(v, 3))
        )
        self._noise.setToolTip("Static / hiss amount (bandpass filtered)")
        row.addLayout(_knob_col("NOISE", self._noise, _OLIVE))

        # DIST knob
        self._dist = RotaryKnob(color=_RUST)
        self._dist.set_value(0.2)
        self._dist.value_changed.connect(
            lambda v: self.param_changed.emit("distortion", round(v, 3))
        )
        self._dist.setToolTip("Speaker breakup distortion")
        row.addLayout(_knob_col("DIST", self._dist, _RUST))

        # MIX knob
        self._mix = RotaryKnob(color=_CREAM)
        self._mix.set_value(1.0)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", round(v, 3))
        )
        self._mix.setToolTip("Wet/dry blend")
        row.addLayout(_knob_col("MIX", self._mix, _CREAM))

        self._faceplate_layout.addLayout(row)

        # Initially hide LO/HI knobs (not in Custom mode)
        self._set_cut_knobs_visible(False)

    # ── Cut knob visibility ───────────────────────────────────────────────

    def _set_cut_knobs_visible(self, visible: bool):
        self._lo_cut.setVisible(visible)
        self._hi_cut.setVisible(visible)
        # Also hide the labels above the knobs
        for i in range(self._lo_col.count()):
            w = self._lo_col.itemAt(i).widget()
            if w:
                w.setVisible(visible)
        for i in range(self._hi_col.count()):
            w = self._hi_col.itemAt(i).widget()
            if w:
                w.setVisible(visible)

    def _on_band_change(self, idx: int):
        self.param_changed.emit("band", idx)
        self._set_cut_knobs_visible(idx == 3)  # Custom mode

    # ── Log scaling for cut knobs ─────────────────────────────────────────

    @staticmethod
    def _knob_to_lo(v: float) -> float:
        """0–1 → 20–2000 Hz (log)."""
        return 20.0 * (100.0 ** v)

    @staticmethod
    def _lo_to_knob(hz: float) -> float:
        hz = max(20.0, min(2000.0, hz))
        return math.log(hz / 20.0) / math.log(100.0)

    @staticmethod
    def _knob_to_hi(v: float) -> float:
        """0–1 → 500–16000 Hz (log)."""
        return 500.0 * (32.0 ** v)

    @staticmethod
    def _hi_to_knob(hz: float) -> float:
        hz = max(500.0, min(16000.0, hz))
        return math.log(hz / 500.0) / math.log(32.0)

    def _on_lo_cut(self, v: float):
        hz = self._knob_to_lo(v)
        self.param_changed.emit("lo_cut", round(hz, 1))

    def _on_hi_cut(self, v: float):
        hz = self._knob_to_hi(v)
        self.param_changed.emit("hi_cut", round(hz, 1))

    # ── State sync ────────────────────────────────────────────────────────

    def set_state(self, state):
        self.set_power(state.enabled, emit=False)
        self._band_sel.set_index(state.band)
        self._set_cut_knobs_visible(state.band == 3)
        self._lo_cut.set_value(self._lo_to_knob(state.lo_cut))
        self._hi_cut.set_value(self._hi_to_knob(state.hi_cut))
        self._noise.set_value(state.noise)
        self._dist.set_value(state.distortion)
        self._mix.set_value(state.mix)
