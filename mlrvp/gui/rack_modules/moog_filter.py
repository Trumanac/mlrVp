"""The Ladder — 1U 24dB/oct ladder filter with cutoff, resonance, and drive."""

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import (
    RotaryKnob, _ValueDisplayMixin, ControlDisplaySettings,
    _MIDI_HIGHLIGHT_COLOR, _MIDI_LEARNING_COLOR,
)
from mlrvp.config import FilterBState
from mlrvp.gui.scale import bind_fixed_size, bind_fixed_width

_FACE = "#f0ede4"   # Moog-style off-white panel
_DARK = "#1a1a1a"
_WOOD = "#5a3a1a"
_DIM = "#666666"


class _LadderLogo(QWidget):
    """Ladder-rung icon for The Ladder filter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 18, 28)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(_DARK), 2))
        # Side rails
        p.drawLine(4, 2, 4, 26)
        p.drawLine(14, 2, 14, 26)
        # Rungs
        for y in [6, 12, 18, 24]:
            p.drawLine(4, y, 14, y)
        p.end()


class _WoodCheek(QWidget):
    """Decorative wooden end-cheek accent — narrow for 1U."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_width(self, 6)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        w, h = self.width(), self.height()
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0.0, QColor("#6b4423"))
        grad.setColorAt(0.3, QColor("#8b6914"))
        grad.setColorAt(0.7, QColor("#6b4423"))
        grad.setColorAt(1.0, QColor("#5a3a1a"))
        p.fillRect(0, 0, w, h, grad)
        # Wood grain lines
        p.setPen(QPen(QColor(0, 0, 0, 30), 0.5))
        for y in range(0, h, 4):
            p.drawLine(0, y, w, y)
        p.end()


class _CompactKnob(_ValueDisplayMixin, QWidget):
    """Moog-style knurled knob sized for 1U (38x38)."""

    value_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 38, 38)
        self._value = 0.8
        self._dragging = False
        self._drag_start_y = 0.0
        self._drag_start_val = 0.0
        self._midi_highlight: int = 0
        self._init_value_display()

    def set_value(self, v: float):
        self._value = max(0.0, min(1.0, v))
        self.update()
        if not ControlDisplaySettings.notifications_suspended:
            self.show_value_popup()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        import math
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 2

        # Outer ring
        p.setPen(QPen(QColor("#333333"), 1.5))
        p.setBrush(QColor("#1a1a1a"))
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Knurled edge
        p.setPen(QPen(QColor("#444444"), 0.5))
        for i in range(24):
            angle = math.radians(i * 15)
            x1 = cx + int((r - 1) * math.cos(angle))
            y1 = cy + int((r - 1) * math.sin(angle))
            x2 = cx + int(r * math.cos(angle))
            y2 = cy + int(r * math.sin(angle))
            p.drawLine(x1, y1, x2, y2)

        # Inner cap
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2a2a2a"))
        ir = r - 4
        p.drawEllipse(cx - ir, cy - ir, ir * 2, ir * 2)

        # Value indicator
        angle = math.radians(225 - self._value * 270)
        ix = cx + int((r - 7) * math.cos(angle))
        iy = cy - int((r - 7) * math.sin(angle))
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.drawLine(cx, cy, ix, iy)

        # MIDI highlight ring
        if self._midi_highlight:
            hc = _MIDI_LEARNING_COLOR if self._midi_highlight == 2 else _MIDI_HIGHLIGHT_COLOR
            p.setPen(QPen(hc, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(1, 1, w - 2, h - 2)

        if self._should_show_overlay():
            self._draw_value_overlay(p, w, h)

        p.end()

    def mousePressEvent(self, event):
        if self._handle_right_click(event):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start_y = event.position().y()
            self._drag_start_val = self._value

    def mouseMoveEvent(self, event):
        if self._dragging:
            dy = self._drag_start_y - event.position().y()
            v = max(0.0, min(1.0, self._drag_start_val + dy / 100.0))
            if v != self._value:
                self._value = v
                self.value_changed.emit(v)
                self.show_value_popup()
                self.update()

    def mouseReleaseEvent(self, event):
        self._dragging = False


class MoogFilterModule(RackModuleBase):
    """1U The Ladder — 24dB/oct resonant filter with drive."""

    effect_type = "filter_b"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="THE LADDER", parent=parent)
        self._faceplate.setStyleSheet(f"background: {_FACE};")
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(4)

        # Left wood cheek
        row.addWidget(_WoodCheek())

        # Logo
        row.addWidget(_LadderLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 2, 0, 2)
        title = QLabel("THE LADDER")
        title.setStyleSheet(
            f"color: {_DARK}; font-size: 11px; font-weight: bold;"
            "font-family: 'Arial Black';"
        )
        title_col.addWidget(title)
        type_lbl = QLabel("24dB RESONANT FILTER")
        type_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        title_col.addWidget(type_lbl)
        row.addLayout(title_col)

        # Cutoff — larger Moog knob
        lbl_c = QLabel("CUTOFF")
        lbl_c.setStyleSheet(f"color: {_DARK}; font-size: 7px; letter-spacing: 1px;")
        row.addWidget(lbl_c)
        self._cutoff = _CompactKnob()
        self._cutoff.set_value(0.8)
        self._cutoff.set_formatter(lambda v: f"{int(round(20 + v * 19980))} Hz")
        self._cutoff.value_changed.connect(
            lambda v: self.param_changed.emit("cutoff", 20 + v * 19980)
        )
        self._cutoff.setToolTip("Low-pass cutoff frequency — Moog 4-pole ladder filter (20 Hz–20 kHz)")
        row.addWidget(self._cutoff)

        # Resonance — standard RotaryKnob
        lbl_r = QLabel("RES")
        lbl_r.setStyleSheet(f"color: {_DARK}; font-size: 7px; letter-spacing: 1px;")
        row.addWidget(lbl_r)
        self._resonance = RotaryKnob(color=_DARK)
        self._resonance.set_value(0.3)
        self._resonance.value_changed.connect(
            lambda v: self.param_changed.emit("resonance", v)
        )
        self._resonance.setToolTip("Resonance — Q at the cutoff point; self-oscillates at maximum (use carefully)")
        row.addWidget(self._resonance)

        # Drive — standard RotaryKnob
        lbl_d = QLabel("DRIVE")
        lbl_d.setStyleSheet(f"color: {_DARK}; font-size: 7px; letter-spacing: 1px;")
        row.addWidget(lbl_d)
        self._drive = RotaryKnob(color=_DARK)
        self._drive.set_value(0.0)
        self._drive.value_changed.connect(
            lambda v: self.param_changed.emit("drive", v)
        )
        self._drive.setToolTip("Input drive — overdrives the filter input for warm saturation and harmonic coloring")
        row.addWidget(self._drive)

        row.addStretch()

        # Right wood cheek
        row.addWidget(_WoodCheek())

        self._faceplate_layout.addLayout(row)

    def set_state(self, state: FilterBState):
        self.set_power(state.enabled, emit=False)
        self._cutoff.set_value((state.cutoff - 20) / 19980)
        self._resonance.set_value(state.resonance)
        self._drive.set_value(state.drive)
