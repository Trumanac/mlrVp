"""Twin Peaks — 1U dual VU meter with analog needle display."""

import math
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QWidget
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QLinearGradient, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase

_FACE = "#0e0e0e"
_NEEDLE = "#cc2222"
_SCALE = "#ccccaa"
_METER_BG = "#fffff0"
_GOLD = "#ccaa44"
_DIM = "#776622"
_MODES = ["VU", "PEAK", "RMS"]


class _PeaksLogo(QWidget):
    """Twin mountain peaks icon for the Twin Peaks VU meter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 24)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_GOLD))
        path = QPainterPath()
        path.moveTo(1, 22)
        path.lineTo(9, 6)
        path.lineTo(15, 14)
        path.lineTo(23, 2)
        path.lineTo(31, 22)
        path.closeSubpath()
        p.drawPath(path)
        p.end()


class _AnalogMeter(QWidget):
    """Single analog VU-style meter with needle and arc scale."""

    def __init__(self, label: str = "L", parent=None):
        super().__init__(parent)
        self.setFixedSize(110, 52)
        self._level = 0.0    # 0..1
        self._peak = 0.0
        self._label = label

    def set_level(self, v: float):
        # Convert linear amplitude → dBFS, then map to 0.0–1.0 on a -40…+6 dB scale
        db = 20.0 * math.log10(max(abs(v), 1e-6))
        frac = max(0.0, min(1.0, (db + 40.0) / 46.0))
        self._level = frac
        if frac > self._peak:
            self._peak = frac
        self.update()

    def decay_peak(self):
        self._peak = max(0.0, self._peak - 0.02)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Meter face background — warm off-white
        p.setPen(QPen(QColor("#555544"), 1))
        p.setBrush(QColor(_METER_BG))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 3, 3)

        # Arc geometry — pivot at bottom-center, needle sweeps upward
        cx, cy = w // 2, h - 3
        radius = h - 12
        arc_left = 150   # degrees (upper-left)
        arc_span = 120   # sweeps to 30° (upper-right)

        # Scale tick marks — fractions derived from dBFS: frac = (db + 40) / 46
        p.setPen(QPen(QColor("#333322"), 0.7))
        p.setFont(QFont("Arial", 5))
        db_marks = [("-40", 0.0), ("-20", 0.435), ("-10", 0.652),
                    ("0", 0.870), ("+6", 1.0)]
        for txt, frac in db_marks:
            angle = math.radians(arc_left - frac * arc_span)
            ox = cx + int((radius - 1) * math.cos(angle))
            oy = cy - int((radius - 1) * math.sin(angle))
            ix = cx + int((radius - 6) * math.cos(angle))
            iy = cy - int((radius - 6) * math.sin(angle))
            p.drawLine(ox, oy, ix, iy)
            tx = cx + int((radius - 11) * math.cos(angle)) - 5
            ty = cy - int((radius - 11) * math.sin(angle)) + 3
            p.drawText(tx, ty, txt)

        # Red zone arc (above 0 dBFS, frac 0.870 to 1.0)
        p.setPen(QPen(QColor("#cc3333"), 1.5))
        for frac in [i * 0.01 for i in range(87, 101)]:
            angle = math.radians(arc_left - frac * arc_span)
            ox = cx + int(radius * math.cos(angle))
            oy = cy - int(radius * math.sin(angle))
            p.drawPoint(ox, oy)

        # Needle
        needle_frac = self._level
        needle_angle = math.radians(arc_left - needle_frac * arc_span)
        nx = cx + int((radius - 3) * math.cos(needle_angle))
        ny = cy - int((radius - 3) * math.sin(needle_angle))
        p.setPen(QPen(QColor(_NEEDLE), 1.5))
        p.drawLine(cx, cy, nx, ny)

        # Peak hold needle (thin, lighter — drawn over main needle)
        if self._peak > 0.0:
            pk_angle = math.radians(arc_left - self._peak * arc_span)
            pk_x = cx + int((radius - 3) * math.cos(pk_angle))
            pk_y = cy - int((radius - 3) * math.sin(pk_angle))
            p.setPen(QPen(QColor("#ff8888"), 0.8))
            p.drawLine(cx, cy, pk_x, pk_y)

        # Pivot dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#333333"))
        p.drawEllipse(cx - 2, cy - 2, 4, 4)

        # Channel label
        p.setPen(QColor("#666655"))
        p.setFont(QFont("Arial", 7, QFont.Weight.Bold))
        p.drawText(4, h - 3, self._label)
        p.end()


class DualVUMeterModule(RackModuleBase):
    """1U Dual VU Meter with adjustable mode (VU / Peak / RMS)."""

    effect_type = "vu_meter"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="TWIN PEAKS", parent=parent)
        self._mode = 0   # 0=VU, 1=PEAK, 2=RMS
        self._vu_l = 0.0
        self._vu_r = 0.0
        self._rms_l = 0.0
        self._rms_r = 0.0
        self._peak_l = 0.0
        self._peak_r = 0.0
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(2, 1, 2, 1)
        row.setSpacing(4)

        # Logo
        row.addWidget(_PeaksLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 2)
        lbl = QLabel("TWIN PEAKS")
        lbl.setStyleSheet(
            f"color: {_GOLD}; font-size: 11px; font-weight: bold;"
            "font-family: 'Times New Roman'; font-style: italic;"
        )
        title_col.addWidget(lbl)
        type_lbl = QLabel("VU METER")
        type_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Left meter
        self._meter_l = _AnalogMeter("L")
        row.addWidget(self._meter_l)

        # Right meter
        self._meter_r = _AnalogMeter("R")
        row.addWidget(self._meter_r)

        # Mode selector
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(_MODES)
        self._mode_combo.setFixedSize(48, 18)
        self._mode_combo.setStyleSheet(
            "QComboBox { background: #1a1a1a; color: #ccaa44; font-size: 8px;"
            "border: 1px solid #444; border-radius: 2px; }"
            "QComboBox QAbstractItemView { background: #1a1a1a; color: #ccaa44; }"
        )
        self._mode_combo.currentIndexChanged.connect(self._on_mode_change)
        row.addWidget(self._mode_combo)

        self._faceplate_layout.addLayout(row)

    def _on_mode_change(self, idx: int):
        self._mode = idx
        self.param_changed.emit("mode", idx)

    # --- Public API for feeding levels from the audio engine ---
    def set_levels(self, left: float, right: float):
        l = max(0.0, min(1.0, left))
        r = max(0.0, min(1.0, right))

        if self._mode == 1:
            # PEAK mode: immediate peak with hold/decay feel.
            self._peak_l = max(l, self._peak_l * 0.96)
            self._peak_r = max(r, self._peak_r * 0.96)
            disp_l, disp_r = self._peak_l, self._peak_r
        elif self._mode == 2:
            # RMS mode: squared averaging to represent energy, not transients.
            alpha = 0.88
            self._rms_l = math.sqrt(alpha * (self._rms_l ** 2) + (1.0 - alpha) * (l ** 2))
            self._rms_r = math.sqrt(alpha * (self._rms_r ** 2) + (1.0 - alpha) * (r ** 2))
            disp_l, disp_r = self._rms_l, self._rms_r
        else:
            # VU mode: classic ballistic response (fast-ish attack, slower release).
            attack = 0.35
            release = 0.08
            self._vu_l += (l - self._vu_l) * (attack if l > self._vu_l else release)
            self._vu_r += (r - self._vu_r) * (attack if r > self._vu_r else release)
            disp_l, disp_r = self._vu_l, self._vu_r

        self._meter_l.set_level(disp_l)
        self._meter_r.set_level(disp_r)

    def decay_peaks(self):
        self._meter_l.decay_peak()
        self._meter_r.decay_peak()

    def set_state(self, state):
        """VU meter is display-only — no persistent state needed."""
        pass
