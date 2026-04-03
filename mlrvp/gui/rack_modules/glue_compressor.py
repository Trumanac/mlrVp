"""Group Hug — SSL G-Bus inspired stereo bus glue compressor."""

import math
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QBrush

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import GlueCompressorState

# ── colours — SSL XLogic G-Bus aluminum panel ────────────────────────────────
_FACE       = "#b8bfc6"   # SSL aluminum gray panel (light!)
_SSL_B      = "#3388ee"   # SSL blue accent
_BDIM       = "#6699bb"   # dimmed blue
_DARK       = "#1a2030"   # dark text on light panel
_MGRAY      = "#7a8898"   # medium gray label
_METER_BG   = "#fffff8"   # meter face ivory
_NEEDLE     = "#cc2222"   # red meter needle
_RED        = "#dd3311"   # over-threshold color
_SEP        = "#8898aa"   # separator line

_RATIOS   = ["2:1", "4:1", "10:1"]
_ATTACKS  = ["0.1", "0.3", "1", "3", "10", "30"]   # ms
_RELEASES = ["0.1s", "0.3s", "0.6s", "AUTO"]


# ── Logo — interlocked rings representing group cohesion ─────────────────────

class _HugLogo(QWidget):
    """Two arcs hugging around a central point — SSL-style on aluminum panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 24)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(_SSL_B), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(0, 2, 16, 20), 60 * 16, 240 * 16)
        p.drawArc(QRectF(12, 2, 16, 20), (180 + 60) * 16, 240 * 16)
        p.setBrush(QBrush(QColor(_SSL_B)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(12, 10, 4, 4)
        p.end()


# ── Horizontal GR meter bar (typical SSL G-Bus style) ────────────────────────

class _SSLVUMeter(QWidget):
    """Ivory-face needle VU meter showing gain reduction — SSL G-Bus style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(80, 62)
        self._gr_db = 0.0

    def set_gr(self, gr_db: float):
        self._gr_db = max(-20.0, min(0.0, gr_db))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bar_h = h - 9
        # Ivory meter face
        p.setPen(QPen(QColor("#aab0b8"), 1))
        p.setBrush(QColor(_METER_BG))
        p.drawRoundedRect(0, 0, w - 1, bar_h, 2, 2)
        # Footer strip
        p.fillRect(0, bar_h, w, 9, QColor("#8090a0"))
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Arial", 5, QFont.Weight.Bold))
        p.drawText(0, bar_h, w, 9, Qt.AlignmentFlag.AlignCenter, "COMP GR")
        # Arc geometry
        cx, cy = w // 2, bar_h - 4
        radius = bar_h - 10
        arc_left, arc_span = 148, 116
        # GR: 0dB (no compression) = needle at right (frac=1.0)
        #     -20dB (heavy comp)   = needle at left  (frac=0.0)
        frac = max(0.0, 1.0 - abs(self._gr_db) / 20.0)
        # Scale marks
        marks = [("-20", 0.0), ("-10", 0.25), ("-6", 0.48), ("-3", 0.68), ("0", 1.0)]
        p.setPen(QPen(QColor("#333333"), 0.8))
        p.setFont(QFont("Arial", 4))
        for txt, mfrac in marks:
            angle = math.radians(arc_left - mfrac * arc_span)
            ox = cx + int(radius * math.cos(angle))
            oy = cy - int(radius * math.sin(angle))
            ix = cx + int((radius - 5) * math.cos(angle))
            iy = cy - int((radius - 5) * math.sin(angle))
            p.drawLine(ox, oy, ix, iy)
            tx = cx + int((radius - 10) * math.cos(angle)) - 5
            ty = cy - int((radius - 10) * math.sin(angle)) + 3
            p.drawText(tx, ty, txt)
        # Needle
        needle_angle = math.radians(arc_left - frac * arc_span)
        p.setPen(QPen(QColor(_NEEDLE), 1.5))
        p.drawLine(cx, cy,
                   cx + int((radius - 4) * math.cos(needle_angle)),
                   cy - int((radius - 4) * math.sin(needle_angle)))
        # Pivot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#333333"))
        p.drawEllipse(cx - 2, cy - 2, 4, 4)
        p.end()


# ── Step-button for attack / ratio / release ─────────────────────────────────

class _StepLabel(QWidget):
    """Click-through stepped selector — SSL-style on aluminum panel."""

    value_changed = Signal(int)

    def __init__(self, options: list, label: str = "", parent=None):
        super().__init__(parent)
        self._options = list(options)
        self._idx = 0
        self._label = label
        self.setFixedSize(38, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_index(self, idx: int):
        self._idx = max(0, min(len(self._options) - 1, idx))
        self.update()

    def mousePressEvent(self, event):
        self._idx = (self._idx + 1) % len(self._options)
        self.value_changed.emit(self._idx)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # Light face with SSL-blue border
        p.setBrush(QBrush(QColor("#d0d8e2")))
        p.setPen(QPen(QColor(_SSL_B), 1.5))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 3, 3)
        # Value in dark SSL-navy
        p.setPen(QColor(_DARK))
        p.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        p.drawText(0, 0, w, h - 8, Qt.AlignmentFlag.AlignCenter,
                   self._options[self._idx])
        # Sub-label in gray
        if self._label:
            p.setPen(QColor(_MGRAY))
            p.setFont(QFont("Arial", 5))
            p.drawText(0, h - 10, w, 10, Qt.AlignmentFlag.AlignCenter,
                       self._label)
        p.end()


# ── Main module ───────────────────────────────────────────────────────────────

class GlueCompressorModule(RackModuleBase):
    """GROUP HUG — SSL G-Bus inspired stereo bus glue compressor.

    Stepped attack, ratio, and release controls match the original hardware
    switch positions.  Soft-knee feedback detection delivers that classic
    "gel the mix together" glue character on the bus.
    """

    effect_type = "glue_compressor"

    # Declared here to satisfy static analysis — set dynamically in _build_faceplate
    _ratio_sel: "_StepLabel"
    _attack_sel: "_StepLabel"
    _release_sel: "_StepLabel"
    _hpf_sel: "_StepLabel"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="GROUP HUG", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        """Single-row 1U layout: logo | name | VU | sep | controls | HPF | stretch."""
        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(4)

        # Logo
        row.addWidget(_HugLogo())

        # Name column
        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        name_col.setContentsMargins(0, 0, 0, 0)
        name_lbl = QLabel("GROUP HUG")
        name_lbl.setStyleSheet(
            f"color: {_DARK}; font-size: 10px; font-weight: 900;"
            "font-family: 'Arial Black'; letter-spacing: 2px;"
        )
        name_col.addWidget(name_lbl)
        sub_lbl = QLabel("BUS GLUE COMPRESSOR")
        sub_lbl.setStyleSheet(
            f"color: {_MGRAY}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        name_col.addWidget(sub_lbl)
        name_col.addStretch()
        row.addLayout(name_col)

        # Separator
        sep = QWidget()
        sep.setFixedSize(1, 44)
        sep.setStyleSheet(f"background: {_SEP};")
        row.addWidget(sep)

        # VU meter (resized to fit 1U height)
        self._gr_meter = _SSLVUMeter()
        self._gr_meter.setFixedSize(60, 50)
        row.addWidget(self._gr_meter)

        # Separator
        sep2 = QWidget()
        sep2.setFixedSize(1, 44)
        sep2.setStyleSheet(f"background: {_SEP};")
        row.addWidget(sep2)

        # THRESHOLD knob (-30 to +15 dB)
        self._threshold = RotaryKnob(color=_SSL_B)
        self._threshold.set_value(0.40)
        self._threshold.set_formatter(lambda v: f"{-30.0 + v * 45.0:.1f} dB")
        self._threshold.value_changed.connect(
            lambda v: self.param_changed.emit("threshold", -30.0 + v * 45.0)
        )
        thresh_col = QVBoxLayout()
        thresh_col.setSpacing(1)
        thresh_col.setContentsMargins(0, 0, 0, 0)
        thresh_lbl = QLabel("THRESH")
        thresh_lbl.setStyleSheet(
            f"color: {_DARK}; font-size: 6px; font-weight: bold; font-family: 'Arial';"
        )
        thresh_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thresh_col.addWidget(thresh_lbl)
        thresh_col.addWidget(self._threshold, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(thresh_col)

        # Stepped selectors: ATK | RATIO | REL
        for options, attr, param, label in [
            (_ATTACKS,  "_attack_sel",  "attack_idx",  "ATK ms"),
            (_RATIOS,   "_ratio_sel",   "ratio_idx",   "RATIO"),
            (_RELEASES, "_release_sel", "release_idx", "RELEASE"),
        ]:
            sel = _StepLabel(options, label)
            sel.value_changed.connect(
                lambda v, pr=param: self.param_changed.emit(pr, v)
            )
            setattr(self, attr, sel)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.setContentsMargins(0, 0, 0, 0)
            col.addWidget(sel, alignment=Qt.AlignmentFlag.AlignCenter)
            row.addLayout(col)

        # MAKEUP GAIN knob (0–15 dB)
        self._makeup_gain = RotaryKnob(color=_SSL_B)
        self._makeup_gain.set_value(0.0)
        self._makeup_gain.set_formatter(lambda v: f"{v * 15.0:.1f} dB")
        self._makeup_gain.value_changed.connect(
            lambda v: self.param_changed.emit("makeup_gain", v * 15.0)
        )
        gain_col = QVBoxLayout()
        gain_col.setSpacing(1)
        gain_col.setContentsMargins(0, 0, 0, 0)
        gain_lbl = QLabel("MAKE-UP")
        gain_lbl.setStyleSheet(
            f"color: {_DARK}; font-size: 6px; font-weight: bold; font-family: 'Arial';"
        )
        gain_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gain_col.addWidget(gain_lbl)
        gain_col.addWidget(self._makeup_gain, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(gain_col)

        # Separator
        sep3 = QWidget()
        sep3.setFixedSize(1, 44)
        sep3.setStyleSheet(f"background: {_SEP};")
        row.addWidget(sep3)

        # HPF stepped selector — sidechain high-pass (SSL G-Comp positions)
        self._hpf_sel = _StepLabel(["OFF", "30", "60", "105", "125", "185"], "HPF Hz")
        self._hpf_sel.value_changed.connect(
            lambda v: self.param_changed.emit("hpf_idx", v)
        )
        hpf_col = QVBoxLayout()
        hpf_col.setSpacing(0)
        hpf_col.setContentsMargins(0, 0, 0, 0)
        hpf_col.addWidget(self._hpf_sel, alignment=Qt.AlignmentFlag.AlignCenter)
        row.addLayout(hpf_col)

        row.addStretch()
        self._faceplate_layout.addLayout(row)
    def set_state(self, state: GlueCompressorState):
        self.set_power(state.enabled, emit=False)
        self._threshold.set_value((state.threshold + 30.0) / 45.0)
        self._makeup_gain.set_value(state.makeup_gain / 15.0)
        self._ratio_sel.set_index(state.ratio_idx)
        self._attack_sel.set_index(state.attack_idx)
        self._release_sel.set_index(state.release_idx)
        self._hpf_sel.set_index(state.hpf_idx)

    def update_meters(self, level_l: float, level_r: float, gr_db: float = 0.0):
        """Push gain-reduction to the needle VU meter."""
        self._gr_meter.set_gr(gr_db)
