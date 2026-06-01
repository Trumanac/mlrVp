"""Funk Doctor — 1U Mutron III-style envelope filter / auto-wah."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

# ── colour palette — funky green/brown Mutron look ──────────────────────────
_FACE    = "#0e0f08"   # near-black olive faceplate
_ACCENT  = "#44bb44"   # funky green — primary accent
_DIM     = "#556633"   # dim olive — secondary labels
_WARM    = "#cc9944"   # warm brown/gold — secondary highlight
_SILVER  = "#999999"   # silver — knob labels
_SEP     = "#1a1c12"   # separator line


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


class _FunkRxLogo(QWidget):
    """Stylised Rx prescription — Funk Doctor brand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 22, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Draw "R" with QPainterPath
        rp = QPainterPath()
        rp.moveTo(3, 20)
        rp.lineTo(3, 2)
        rp.lineTo(12, 2)
        rp.cubicTo(17, 2, 17, 11, 12, 11)
        rp.lineTo(3, 11)
        p.setPen(QPen(QColor(_ACCENT), 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(rp)
        # "R" leg (diagonal)
        p.drawLine(10, 11, 17, 20)
        # "x" — small italic cross
        p.setPen(QPen(QColor(_WARM), 1.5))
        p.drawLine(14, 13, 20, 20)
        p.drawLine(20, 13, 14, 20)
        p.end()


class _ButtonGroup(QWidget):
    """Exclusive toggle button group."""

    def __init__(self, labels: list[str], accent: str = _ACCENT, parent=None):
        super().__init__(parent)
        self._btns: list[QPushButton] = []
        self._callback = None
        self._accent = accent
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        for i, label in enumerate(labels):
            btn = QPushButton(label)
            bind_fixed_size(btn, 32, 15)
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

    def set_index(self, idx: int):
        for i, btn in enumerate(self._btns):
            btn.blockSignals(True)
            btn.setChecked(i == idx)
            btn.blockSignals(False)
        self._update_styles()

    def on_change(self, cb):
        self._callback = cb

    def _update_styles(self):
        for btn in self._btns:
            if btn.isChecked():
                btn.setStyleSheet(
                    f"QPushButton {{ background: {self._accent}; border: 1px solid {self._accent};"
                    f"border-radius: 2px; color: #ffffff; font-size: 7px; font-weight: bold;"
                    f"padding: 0px 2px; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #1a1a1a; border: 1px solid #333;"
                    f"border-radius: 2px; color: {_DIM}; font-size: 7px; font-weight: bold;"
                    f"padding: 0px 2px; }}"
                    f"QPushButton:hover {{ border-color: {self._accent}; color: {self._accent}; }}"
                )


class AutoWahModule(RackModuleBase):
    """1U Auto-Wah — Mutron III inspired envelope filter."""

    effect_type = "auto_wah"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="FUNK DOCTOR", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(4)

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(_FunkRxLogo())
        name_lbl = QLabel("FUNK DOCTOR")
        name_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 9px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px;"
        )
        title_row.addWidget(name_lbl)
        title_col.addLayout(title_row)
        sub_lbl = QLabel("ENVELOPE FILTER")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)
        row.addSpacing(4)

        # SENSE knob — sensitivity 0–1
        self._sensitivity = RotaryKnob(color=_ACCENT)
        self._sensitivity.set_value(0.5)
        self._sensitivity.value_changed.connect(
            lambda v: self.param_changed.emit("sensitivity", v)
        )
        self._sensitivity.setToolTip("Envelope sensitivity — how much the input drives the filter")
        row.addLayout(_knob_col("SENSE", self._sensitivity, _ACCENT))

        # PEAK knob — resonance 0–1
        self._peak = RotaryKnob(color=_WARM)
        self._peak.set_value(0.6)
        self._peak.value_changed.connect(
            lambda v: self.param_changed.emit("peak", v)
        )
        self._peak.setToolTip("Filter resonance / Q — sharper peak means more 'wah'")
        row.addLayout(_knob_col("PEAK", self._peak, _WARM))

        # MODE selector (UP / DOWN)
        mode_col = QVBoxLayout()
        mode_col.setSpacing(0)
        mode_col.setContentsMargins(0, 0, 0, 0)
        mode_lbl = QLabel("MODE")
        bind_fixed_height(mode_lbl, 10)
        mode_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mode_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        mode_col.addWidget(mode_lbl)
        self._mode_sel = _ButtonGroup(["UP", "DN"], accent=_ACCENT)
        self._mode_sel.on_change(lambda idx: self.param_changed.emit("mode", idx))
        mode_col.addWidget(self._mode_sel)
        row.addLayout(mode_col)

        # FILTER selector (LP / BP / HP)
        filt_col = QVBoxLayout()
        filt_col.setSpacing(0)
        filt_col.setContentsMargins(0, 0, 0, 0)
        filt_lbl = QLabel("FILTER")
        bind_fixed_height(filt_lbl, 10)
        filt_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        filt_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        filt_col.addWidget(filt_lbl)
        self._filter_sel = _ButtonGroup(["LP", "BP", "HP"], accent=_WARM)
        self._filter_sel.on_change(
            lambda idx: self.param_changed.emit("filter_type", idx)
        )
        filt_col.addWidget(self._filter_sel)
        row.addLayout(filt_col)

        # MIX knob
        self._mix = RotaryKnob(color=_SILVER)
        self._mix.set_value(1.0)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", v)
        )
        self._mix.setToolTip("Wet/dry blend of the auto-wah effect")
        row.addLayout(_knob_col("MIX", self._mix, _SILVER))

        self._faceplate_layout.addLayout(row)

    # ── State sync ────────────────────────────────────────────────────────────

    def set_state(self, state):
        """Sync GUI from an AutoWahState dataclass."""
        self.set_power(state.enabled, emit=False)
        self._sensitivity.set_value(state.sensitivity)
        self._peak.set_value(state.peak)
        self._mode_sel.set_index(state.mode)
        self._filter_sel.set_index(state.filter_type)
        self._mix.set_value(state.mix)
