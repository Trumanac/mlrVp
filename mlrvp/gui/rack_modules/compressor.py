"""Thrust Issues — API 2500-inspired sidechain compressor with THRUST circuit."""

import math
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QWidget, QComboBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QPainterPath, QBrush
)

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import CompressorState

# ── colour palette — near-black panel, API 2500 blue/white accent scheme ─────
_FACE     = "#0a0a0a"   # near-black panel
_WHITE    = "#dde8f5"   # cool white labels & markings
_BLU      = "#5a90d0"   # powder blue — section headers, knob arcs
_BLU_BRT  = "#88c0ee"   # bright blue — THRUST section accent
_BLU_DIM  = "#1e3550"   # dark blue — separators, sub-labels
_METER_BG = "#ffd070"   # amber-backlit meter face
_DARK_MTR = "#1a1400"   # meter border / footer
_NEEDLE   = "#1a1a1a"   # near-black needle
_LED_ON   = "#33ff33"
_LED_OFF  = "#113311"
_RED      = "#cc3322"   # needle red-zone

SC_SOURCES = ["Self", "Group 1", "Group 2", "Group 3", "Group 4"]


# ── Logo ─────────────────────────────────────────────────────────────────────

class _ThrustLogo(QWidget):
    """Twin forward-pointing chevrons — thrust / forward compression symbol."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(32, 26)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        # Back chevron — dark blue for depth
        p.setBrush(QColor(_BLU_DIM))
        c1 = QPainterPath()
        c1.moveTo(1, 3)
        c1.lineTo(13, 13)
        c1.lineTo(1, 23)
        c1.lineTo(5, 23)
        c1.lineTo(17, 13)
        c1.lineTo(5, 3)
        c1.closeSubpath()
        p.drawPath(c1)
        # Front chevron — powder blue
        p.setBrush(QColor(_BLU))
        c2 = QPainterPath()
        c2.moveTo(13, 3)
        c2.lineTo(25, 13)
        c2.lineTo(13, 23)
        c2.lineTo(17, 23)
        c2.lineTo(29, 13)
        c2.lineTo(17, 3)
        c2.closeSubpath()
        p.drawPath(c2)
        p.end()


# ── VU meter ─────────────────────────────────────────────────────────────────

class _APIVUMeter(QWidget):
    """Amber-backlit needle VU meter — API 2500 style."""

    def __init__(self, label: str = "GR", is_gr: bool = True, parent=None):
        super().__init__(parent)
        self._FONT_FOOTER = QFont("Arial", 5, QFont.Weight.Bold)
        self._FONT_SCALE = QFont("Arial", 4)
        self.setFixedSize(64, 62)
        self._level = 0.0
        self._label = label
        self._is_gr = is_gr

    def set_level(self, v: float):
        self._level = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bar_h = h - 9
        # Amber-backlit face
        p.setPen(QPen(QColor("#221800"), 1))
        p.setBrush(QColor(_METER_BG))
        p.drawRoundedRect(0, 0, w - 1, bar_h, 2, 2)
        # Footer label strip
        p.fillRect(0, bar_h, w, 9, QColor(_DARK_MTR))
        p.setPen(QColor(_WHITE))
        p.setFont(self._FONT_FOOTER)
        p.drawText(0, bar_h, w, 9, Qt.AlignmentFlag.AlignCenter, self._label)
        # Arc geometry: pivot bottom-center
        cx, cy = w // 2, bar_h - 4
        radius = bar_h - 10
        arc_left, arc_span = 148, 116
        # Scale marks
        if self._is_gr:
            marks = [("20", 0.0), ("10", 0.22), ("6", 0.45), ("3", 0.68), ("0", 1.0)]
        else:
            marks = [("-20", 0.0), ("-10", 0.25), ("-6", 0.48), ("0", 0.78), ("+3", 0.9)]
        p.setPen(QPen(QColor("#8b6a22"), 0.8))
        p.setFont(self._FONT_SCALE)
        for txt, frac in marks:
            angle = math.radians(arc_left - frac * arc_span)
            ox = cx + int(radius * math.cos(angle))
            oy = cy - int(radius * math.sin(angle))
            ix = cx + int((radius - 5) * math.cos(angle))
            iy = cy - int((radius - 5) * math.sin(angle))
            p.drawLine(ox, oy, ix, iy)
            tx = cx + int((radius - 10) * math.cos(angle)) - 5
            ty = cy - int((radius - 10) * math.sin(angle)) + 3
            p.drawText(tx, ty, txt)
        # Red-zone arc (right quarter)
        p.setPen(QPen(QColor(_RED), 1.3))
        for i in range(22, 30):
            frac = i / 29.0
            angle = math.radians(arc_left - frac * arc_span)
            p.drawPoint(cx + int(radius * math.cos(angle)),
                        cy - int(radius * math.sin(angle)))
        # Needle
        needle_angle = math.radians(arc_left - self._level * arc_span)
        p.setPen(QPen(QColor(_NEEDLE), 1.3))
        p.drawLine(cx, cy,
                   cx + int((radius - 4) * math.cos(needle_angle)),
                   cy - int((radius - 4) * math.sin(needle_angle)))
        # Pivot dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#333322"))
        p.drawEllipse(cx - 2, cy - 2, 4, 4)
        p.end()


# ── THRUST toggle button ──────────────────────────────────────────────────────

class _ThrustButton(QWidget):
    """Rectangular illuminated THRUST on/off toggle with green LED."""

    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._FONT_LABEL = QFont("Arial", 7, QFont.Weight.Bold)
        self.setFixedSize(60, 28)
        self._active = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_active(self, on: bool):
        self._active = bool(on)
        self.update()

    def mousePressEvent(self, event):
        self._active = not self._active
        self.toggled.emit(self._active)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        face = QColor("#0e1820") if self._active else QColor("#0a0e14")
        border = QColor(_BLU_BRT) if self._active else QColor(_BLU_DIM)
        p.setBrush(QBrush(face))
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 3, 3)
        led = QColor(_LED_ON) if self._active else QColor(_LED_OFF)
        p.setBrush(QBrush(led))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(5, h // 2 - 4, 8, 8)
        p.setPen(QColor(_BLU_BRT) if self._active else QColor(_BLU_DIM))
        p.setFont(self._FONT_LABEL)
        p.drawText(16, 0, w - 17, h,
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   "THRUST")
        p.end()


# ── Main module ───────────────────────────────────────────────────────────────

class CompressorModule(RackModuleBase):
    """THRUST ISSUES — sidechain bus compressor with THRUST high-pass circuit.

    The THRUST circuit applies a high-pass filter to the sidechain detect
    signal so that low-frequency content does not over-trigger the compressor,
    delivering a punchy, transient-forward character.  Toggle THRUST on/off
    and dial in the detect-filter frequency.
    """

    effect_type = "compressor"

    def __init__(self, parent=None):
        super().__init__(unit_height=2, faceplate_color=_FACE,
                         title="THRUST ISSUES", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        # ── Top row: logo | name | sub-label | stretch | SC selector ────────
        top = QHBoxLayout()
        top.setContentsMargins(4, 1, 4, 1)
        top.setSpacing(6)
        top.addWidget(_ThrustLogo())

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        name_lbl = QLabel("THRUST ISSUES")
        name_lbl.setStyleSheet(
            f"color: {_WHITE}; font-size: 10px; font-weight: 900;"
            "font-family: 'Arial Black'; letter-spacing: 2px;"
        )
        title_col.addWidget(name_lbl)
        sub_lbl = QLabel("SIDECHAIN COMPRESSOR")
        sub_lbl.setStyleSheet(
            f"color: {_BLU_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        title_col.addWidget(sub_lbl)
        top.addLayout(title_col)
        top.addStretch()

        sc_lbl = QLabel("SC SRC:")
        sc_lbl.setStyleSheet(f"color: {_BLU}; font-size: 7px; font-weight: bold;")
        top.addWidget(sc_lbl)
        self._sc_source = QComboBox()
        self._sc_source.addItems(SC_SOURCES)
        self._sc_source.setFixedHeight(18)
        self._sc_source.setStyleSheet(
            f"QComboBox {{ background: #0a1420; color: {_WHITE}; font-size: 8px;"
            f" border: 1px solid {_BLU_DIM}; }}"
            f"QComboBox QAbstractItemView {{ background: #0a1420; color: {_WHITE}; }}"
        )
        self._sc_source.currentIndexChanged.connect(
            lambda v: self.param_changed.emit("sidechain_source", v)
        )
        self._sc_source.setToolTip("Sidechain source — which group's audio triggers the compressor detect circuit")
        top.addWidget(self._sc_source)
        self._faceplate_layout.addLayout(top)

        # ── Bottom row: VU meters | COMPRESSOR section | THRUST section ──────
        bot = QHBoxLayout()
        bot.setContentsMargins(4, 0, 4, 2)
        bot.setSpacing(4)

        # VU meters with section header
        vu_col = QVBoxLayout()
        vu_col.setSpacing(1)
        vu_hdr = QLabel("V U")
        vu_hdr.setStyleSheet(
            f"color: {_BLU_DIM}; font-size: 5px; font-weight: bold; letter-spacing: 2px;"
        )
        vu_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vu_col.addWidget(vu_hdr)
        vu_row = QHBoxLayout(); vu_row.setSpacing(2); vu_row.setContentsMargins(0,0,0,0)
        self._gr_vu  = _APIVUMeter("GR",  is_gr=True)
        self._out_vu = _APIVUMeter("OUT", is_gr=False)
        vu_row.addWidget(self._gr_vu)
        vu_row.addWidget(self._out_vu)
        vu_col.addLayout(vu_row)
        bot.addLayout(vu_col)

        # Vertical separator
        sep = QWidget(); sep.setFixedWidth(1)
        sep.setStyleSheet(f"background: {_BLU_DIM};")
        bot.addWidget(sep)

        # COMPRESSOR section (5 knobs with header)
        comp_col = QVBoxLayout()
        comp_col.setSpacing(1)
        comp_hdr = QLabel("C O M P R E S S O R")
        comp_hdr.setStyleSheet(
            f"color: {_BLU}; font-size: 6px; font-weight: bold;"
            f" letter-spacing: 1px; border-bottom: 1px solid {_BLU_DIM}; padding-bottom: 1px;"
        )
        comp_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        comp_col.addWidget(comp_hdr)
        knobs_row = QHBoxLayout(); knobs_row.setSpacing(3); knobs_row.setContentsMargins(0,0,0,0)
        _COMP_TIPS = {
            "threshold":  "Compression threshold — signal below this dB level passes uncompressed",
            "ratio":      "Compression ratio — how much gain is reduced above the threshold",
            "attack":     "Attack time — how quickly the compressor responds to loud transients",
            "release":    "Release time — how quickly the compressor recovers after the signal drops",
            "makeup_gain": "Makeup gain — boosts the output to compensate for gain reduction",
        }
        for name, default, param, scale in [
            ("THRESH", 0.67, "threshold",  (-60.0,   0.0)),
            ("RATIO",  0.16, "ratio",       (1.0,   20.0)),
            ("ATK",    0.05, "attack",      (0.1,  200.0)),
            ("REL",    0.09, "release",     (10.0, 1000.0)),
            ("GAIN",   0.0,  "makeup_gain", (0.0,   30.0)),
        ]:
            knob = RotaryKnob(color=_BLU)
            knob.set_value(default)
            knob.value_changed.connect(
                lambda v, p=param, s=scale: self.param_changed.emit(
                    p, s[0] + v * (s[1] - s[0])
                )
            )
            knob.setToolTip(_COMP_TIPS.get(param, ""))
            setattr(self, f"_{param}", knob)
            c = QVBoxLayout(); c.setSpacing(0); c.setContentsMargins(0,0,0,0)
            lbl = QLabel(name)
            lbl.setStyleSheet(
                f"color: {_WHITE}; font-size: 6px; font-family: 'Arial';"
            )
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c.addWidget(lbl)
            c.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
            knobs_row.addLayout(c)
        comp_col.addLayout(knobs_row)
        self._threshold.set_formatter(lambda v: f"{-60.0 + v * 60.0:.1f} dB")
        self._ratio.set_formatter(lambda v: f"{1.0 + v * 19.0:.1f}:1")
        self._attack.set_formatter(lambda v: f"{0.1 + v * 199.9:.1f} ms")
        self._release.set_formatter(lambda v: f"{10.0 + v * 990.0:.0f} ms")
        self._makeup_gain.set_formatter(lambda v: f"{v * 30.0:.1f} dB")
        bot.addLayout(comp_col)

        # Vertical separator
        sep2 = QWidget(); sep2.setFixedWidth(1)
        sep2.setStyleSheet(f"background: {_BLU_DIM};")
        bot.addWidget(sep2)

        # THRUST section (button + freq knob with header)
        thrust_col = QVBoxLayout()
        thrust_col.setSpacing(1)
        thrust_hdr = QLabel("T H R U S T")
        thrust_hdr.setStyleSheet(
            f"color: {_BLU_BRT}; font-size: 6px; font-weight: bold;"
            f" letter-spacing: 1px; border-bottom: 1px solid {_BLU_DIM}; padding-bottom: 1px;"
        )
        thrust_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thrust_col.addWidget(thrust_hdr)
        thrust_ctrl = QHBoxLayout(); thrust_ctrl.setSpacing(3); thrust_ctrl.setContentsMargins(0,0,0,0)
        self._thrust_btn = _ThrustButton()
        self._thrust_btn.toggled.connect(
            lambda on: self.param_changed.emit("thrust_enabled", on)
        )
        self._thrust_btn.setToolTip(
            "THRUST circuit — high-pass filter on the sidechain detect signal\n"
            "prevents low-frequency content from over-triggering the compressor"
        )
        thrust_ctrl.addWidget(self._thrust_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        freq_c = QVBoxLayout(); freq_c.setSpacing(0); freq_c.setContentsMargins(0,0,0,0)
        freq_lbl = QLabel("FREQ")
        freq_lbl.setStyleSheet(
            f"color: {_BLU_BRT}; font-size: 6px; font-family: 'Arial';"
        )
        freq_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sc_hp = RotaryKnob(color=_BLU_BRT)
        self._sc_hp.set_value(0.13)
        self._sc_hp.set_formatter(lambda v: f"{int(20.0 + v * 1980.0)} Hz")
        self._sc_hp.value_changed.connect(
            lambda v: self.param_changed.emit("sc_hp_freq", 20.0 + v * 1980.0)
        )
        freq_c.addWidget(freq_lbl)
        freq_c.addWidget(self._sc_hp, alignment=Qt.AlignmentFlag.AlignCenter)
        thrust_ctrl.addLayout(freq_c)
        thrust_col.addLayout(thrust_ctrl)
        bot.addLayout(thrust_col)

        bot.addStretch()
        self._faceplate_layout.addLayout(bot)

    def set_state(self, state: CompressorState):
        self.set_power(state.enabled, emit=False)
        self._sc_source.blockSignals(True)
        self._sc_source.setCurrentIndex(state.sidechain_source)
        self._sc_source.blockSignals(False)
        self._threshold.set_value((state.threshold + 60) / 60)
        self._ratio.set_value((state.ratio - 1) / 19)
        self._attack.set_value((state.attack - 0.1) / 199.9)
        self._release.set_value((state.release - 10) / 990)
        self._makeup_gain.set_value(state.makeup_gain / 30)
        self._thrust_btn.set_active(getattr(state, 'thrust_enabled', True))
        # sc_hp_freq range 20–2000 Hz
        self._sc_hp.set_value((state.sc_hp_freq - 20.0) / 1980.0)

    def update_meters(self, level_l: float, level_r: float, gr_db: float = 0.0):
        """Push audio levels and gain reduction to the VU meters."""
        self._out_vu.set_level((level_l + level_r) * 0.5)
        self._gr_vu.set_level(min(1.0, abs(gr_db) / 20.0))

