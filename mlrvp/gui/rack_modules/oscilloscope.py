"""CRT:// Scope — 2U oscilloscope with CRT-style real-time waveform display."""

import collections
import math
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QWidget
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QLinearGradient, QRadialGradient,
    QPainterPath,
)

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

_FACE = "#080808"
_PHOSPHOR = "#33ff66"         # Classic green phosphor
_DIM_PHOSPHOR = "#1a7733"
_GRID = "#1a3a1a"
_CRT_BG = "#020a02"
_BEZEL = "#222222"
_MODES = ["WAVEFORM", "LISSAJOUS"]
_BUFFER_LEN = 256


class _PulseLogo(QWidget):
    """Heartbeat/pulse waveform icon for the oscilloscope."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 18)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.moveTo(1, 9)
        path.lineTo(6, 9)
        path.lineTo(9, 2)
        path.lineTo(12, 16)
        path.lineTo(15, 5)
        path.lineTo(18, 9)
        path.lineTo(25, 9)
        p.setPen(QPen(QColor(_PHOSPHOR), 1.5))
        p.drawPath(path)
        p.end()


class _CRTDisplay(QWidget):
    """CRT-style oscilloscope screen with phosphor afterglow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 80)
        self._buffer_l = collections.deque([0.0] * _BUFFER_LEN, maxlen=_BUFFER_LEN)
        self._buffer_r = collections.deque([0.0] * _BUFFER_LEN, maxlen=_BUFFER_LEN)
        self._mode = 0       # 0=waveform, 1=lissajous
        self._zoom = 0.5     # vertical scale

    def push_samples(self, left_chunk: list[float], right_chunk: list[float]):
        """Push a chunk of audio samples (normalized -1..1)."""
        self._buffer_l.extend(left_chunk)
        self._buffer_r.extend(right_chunk)
        self.update()

    def set_mode(self, m: int):
        self._mode = m
        self.update()

    def set_zoom(self, v: float):
        self._zoom = max(0.1, min(2.0, v))

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # CRT bezel
        p.setPen(QPen(QColor(_BEZEL), 2))
        p.setBrush(QColor(_CRT_BG))
        p.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

        # Slight radial vignette
        vig = QRadialGradient(w / 2, h / 2, max(w, h) * 0.7)
        vig.setColorAt(0.0, QColor(0, 0, 0, 0))
        vig.setColorAt(1.0, QColor(0, 0, 0, 80))
        p.fillRect(3, 3, w - 6, h - 6, vig)

        # Grid lines
        p.setPen(QPen(QColor(_GRID), 0.5))
        gx_step = max(1, (w - 8) // 8)
        gy_step = max(1, (h - 8) // 4)
        for gx in range(4, w - 4, gx_step):
            p.drawLine(gx, 4, gx, h - 4)
        for gy in range(4, h - 4, gy_step):
            p.drawLine(4, gy, w - 4, gy)

        # Center cross (brighter)
        p.setPen(QPen(QColor(_GRID).lighter(150), 0.8))
        p.drawLine(4, h // 2, w - 4, h // 2)
        p.drawLine(w // 2, 4, w // 2, h - 4)

        inner_w = w - 8
        inner_h = h - 8
        buf_l = list(self._buffer_l)
        buf_r = list(self._buffer_r)
        n = len(buf_l)

        if self._mode == 0:
            # Waveform mode — draw L (bright) and R (dim)
            self._draw_waveform(p, buf_l, inner_w, inner_h, 4, 4,
                                QColor(_PHOSPHOR), 1.5)
            dim = QColor(_PHOSPHOR)
            dim.setAlpha(100)
            self._draw_waveform(p, buf_r, inner_w, inner_h, 4, 4,
                                dim, 1.0)
        else:
            # Lissajous mode — L vs R
            self._draw_lissajous(p, buf_l, buf_r, inner_w, inner_h, 4, 4)

        # Scanline overlay effect
        p.setPen(QPen(QColor(0, 0, 0, 25), 0.5))
        for sl in range(4, h - 4, 2):
            p.drawLine(4, sl, w - 4, sl)

        p.end()

    def _draw_waveform(self, p, buf, iw, ih, ox, oy, color, width):
        if len(buf) < 2:
            return
        path = QPainterPath()
        n = len(buf)
        n_1 = max(1, n - 1)
        zoom = self._zoom * 2.0
        for i, s in enumerate(buf):
            x = ox + (i / n_1) * iw
            y = oy + ih / 2 - s * zoom * ih / 2
            y = max(oy, min(oy + ih, y))
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        # Glow effect — draw wider, translucent first
        glow = QColor(color)
        glow.setAlpha(40)
        p.setPen(QPen(glow, width + 3))
        p.drawPath(path)
        p.setPen(QPen(color, width))
        p.drawPath(path)

    def _draw_lissajous(self, p, buf_l, buf_r, iw, ih, ox, oy):
        if len(buf_l) < 2:
            return
        zoom = self._zoom * 2.0
        path = QPainterPath()
        for i in range(len(buf_l)):
            x = ox + iw / 2 + buf_l[i] * zoom * iw / 2
            y = oy + ih / 2 - buf_r[i] * zoom * ih / 2
            x = max(ox, min(ox + iw, x))
            y = max(oy, min(oy + ih, y))
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        glow = QColor(_PHOSPHOR)
        glow.setAlpha(30)
        p.setPen(QPen(glow, 3))
        p.drawPath(path)
        p.setPen(QPen(QColor(_PHOSPHOR), 1.2))
        p.drawPath(path)


class OscilloscopeModule(RackModuleBase):
    """2U Oscilloscope with CRT-style real-time waveform display."""

    effect_type = "oscilloscope"

    def __init__(self, parent=None):
        super().__init__(unit_height=2, faceplate_color=_FACE,
                         title="CRT:// SCOPE", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(6)

        # CRT display — takes most of the width
        self._display = _CRTDisplay()
        row.addWidget(self._display, stretch=1)

        # Controls column
        ctrl = QVBoxLayout()
        ctrl.setSpacing(3)

        # Logo + title
        ctrl.addWidget(_PulseLogo())
        lbl = QLabel("CRT://")
        lbl.setStyleSheet(
            f"color: {_PHOSPHOR}; font-size: 9px; font-weight: bold;"
            "font-family: 'Consolas';"
        )
        ctrl.addWidget(lbl)
        type_lbl = QLabel("OSCILLOSCOPE")
        type_lbl.setStyleSheet(
            f"color: {_DIM_PHOSPHOR}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        ctrl.addWidget(type_lbl)

        # Mode selector
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(_MODES)
        self._mode_combo.setFixedSize(70, 18)
        self._mode_combo.setStyleSheet(
            f"QComboBox {{ background: #111; color: {_PHOSPHOR}; font-size: 8px;"
            "border: 1px solid #333; border-radius: 2px; }"
            f"QComboBox QAbstractItemView {{ background: #111; color: {_PHOSPHOR}; }}"
        )
        self._mode_combo.currentIndexChanged.connect(self._on_mode)
        self._mode_combo.setToolTip("Display mode — WAVEFORM shows L/R audio waveforms over time; LISSAJOUS shows stereo phase relationship")
        ctrl.addWidget(self._mode_combo)

        # Zoom knob
        z_lbl = QLabel("ZOOM")
        z_lbl.setStyleSheet(f"color: {_PHOSPHOR}; font-size: 7px;")
        ctrl.addWidget(z_lbl)
        self._zoom_knob = RotaryKnob(color=_PHOSPHOR)
        self._zoom_knob.set_value(0.5)
        self._zoom_knob.set_formatter(lambda v: f"{0.1 + v * 1.9:.1f}x")
        self._zoom_knob.value_changed.connect(
            lambda v: self._display.set_zoom(0.1 + v * 1.9))
        self._zoom_knob.setToolTip("Zoom — vertical amplitude scale; increase to see quieter signal detail (0.1–2.0x)")
        ctrl.addWidget(self._zoom_knob, alignment=Qt.AlignmentFlag.AlignCenter)
        ctrl.addStretch()

        row.addLayout(ctrl)
        self._faceplate_layout.addLayout(row)

    def _on_mode(self, idx):
        self._display.set_mode(idx)

    # --- Public API ---
    def push_samples(self, left_chunk: list[float], right_chunk: list[float]):
        self._display.push_samples(left_chunk, right_chunk)

    def set_state(self, state):
        """Oscilloscope is display-only — no persistent state needed."""
        pass
