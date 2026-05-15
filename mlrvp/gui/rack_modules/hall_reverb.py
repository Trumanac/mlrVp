"""Echo Chamber — 2U hall/room/chamber reverb with LCD and jog wheel."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QComboBox
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPolygonF, QPainterPath
from PySide6.QtCore import QPointF

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.config import HallReverbState
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

_FACE = "#101018"
_LCD_BG = "#1a2a1a"
_LCD_FG = "#66cc88"
_DIM = "#336644"

ROOM_TYPES = ["Hall", "Room", "Chamber", "Studio"]


class _EchoArcsLogo(QWidget):
    """Concentric arc icon representing reverb/echo in a room."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 32, 28)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = 14, 16
        for i, r in enumerate([4, 7, 10, 13]):
            alpha = 220 - i * 50
            p.setPen(QPen(QColor(102, 204, 136, max(40, alpha)), 1.5))
            p.drawArc(cx - r, cy - r, r * 2, r * 2, 45 * 16, 90 * 16)
        p.end()


class _RoomLCD(QWidget):
    """Small color LCD showing 3D wireframe room shapes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 72, 40)
        self._room_type = 0  # 0=Hall, 1=Room, 2=Chamber

    def set_room_type(self, t: int):
        self._room_type = t % 4
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # LCD background
        p.fillRect(0, 0, w, h, QColor(_LCD_BG))
        p.setPen(QPen(QColor("#334433"), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        # Wireframe room
        p.setPen(QPen(QColor(_LCD_FG), 1))
        if self._room_type == 0:  # Hall — tall, vaulted shape
            pts = [QPointF(10, 35), QPointF(30, 10), QPointF(62, 10), QPointF(42, 35)]
            p.drawPolygon(QPolygonF(pts))
            p.drawLine(30, 10, 30, 28)
            p.drawLine(62, 10, 62, 28)
            # Extra depth lines for spaciousness
            pen2 = QPen(QColor(_LCD_FG), 1)
            pen2.setStyle(Qt.PenStyle.DotLine)
            p.setPen(pen2)
            p.drawLine(20, 22, 52, 22)
        elif self._room_type == 1:  # Room — wide, low ceiling
            pts = [QPointF(8, 32), QPointF(20, 12), QPointF(64, 12), QPointF(52, 32)]
            p.drawPolygon(QPolygonF(pts))
            p.drawLine(20, 12, 20, 26)
        elif self._room_type == 2:  # Chamber — narrow, irregular
            pts = [QPointF(18, 34), QPointF(28, 8), QPointF(52, 8), QPointF(44, 34)]
            p.drawPolygon(QPolygonF(pts))
            p.drawLine(28, 8, 28, 30)
            p.drawLine(52, 8, 52, 30)
        else:  # Studio — tight control room with angled walls
            pts = [QPointF(12, 33), QPointF(18, 14), QPointF(56, 14), QPointF(60, 33)]
            p.drawPolygon(QPolygonF(pts))
            # Studio acoustics: angled rear wall (absorption panels)
            p.drawLine(18, 14, 25, 22)
            p.drawLine(56, 14, 49, 22)
            # Diffuser marks
            p.setPen(QPen(QColor(_DIM), 1))
            for xi in [28, 34, 40, 46]:
                p.drawLine(xi, 14, xi, 20)

        # Label
        p.setPen(QPen(QColor(_LCD_FG), 1))
        p.setFont(QFont("Consolas", 7))
        p.drawText(4, h - 3, ROOM_TYPES[self._room_type % len(ROOM_TYPES)])
        p.end()


class _JogWheel(QWidget):
    """Large data-entry jog wheel."""

    from PySide6.QtCore import Signal
    value_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 44, 44)
        self._value = 0.5
        self._dragging = False
        self._drag_start_y = 0.0
        self._drag_start_val = 0.0

    def set_value(self, v: float):
        self._value = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, event):
        import math
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        r = min(w, h) // 2 - 3

        # Outer ring
        p.setPen(QPen(QColor("#555555"), 2))
        p.setBrush(QColor("#1a1a2a"))
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Grip notches
        p.setPen(QPen(QColor("#444444"), 1))
        for i in range(24):
            angle = math.radians(i * 15)
            x1 = cx + int((r - 3) * math.cos(angle))
            y1 = cy + int((r - 3) * math.sin(angle))
            x2 = cx + int((r - 1) * math.cos(angle))
            y2 = cy + int((r - 1) * math.sin(angle))
            p.drawLine(x1, y1, x2, y2)

        # Position marker
        angle = math.radians(225 - self._value * 270)
        mx = cx + int((r - 6) * math.cos(angle))
        my = cy - int((r - 6) * math.sin(angle))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_LCD_FG))
        p.drawEllipse(mx - 3, my - 3, 6, 6)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start_y = event.position().y()
            self._drag_start_val = self._value

    def mouseMoveEvent(self, event):
        if self._dragging:
            dy = self._drag_start_y - event.position().y()
            v = max(0.0, min(1.0, self._drag_start_val + dy / 120.0))
            if v != self._value:
                self._value = v
                self.value_changed.emit(v)
                self.update()

    def mouseReleaseEvent(self, event):
        self._dragging = False


class HallReverbModule(RackModuleBase):
    """2U Hall/Room/Chamber Reverb with LCD and jog wheel."""

    effect_type = "hall_reverb"

    # Knob attrs assigned via setattr loop in _build_faceplate
    _room_size: RotaryKnob
    _decay: RotaryKnob
    _diffusion: RotaryKnob
    _mix: RotaryKnob

    def __init__(self, parent=None):
        super().__init__(unit_height=2, faceplate_color=_FACE,
                         title="ECHO CHAMBER", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        top = QHBoxLayout()
        top.setContentsMargins(4, 0, 4, 0)
        top.setSpacing(6)

        # Logo
        top.addWidget(_EchoArcsLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        name_lbl = QLabel("ECHO CHAMBER")
        name_lbl.setStyleSheet(
            f"color: {_LCD_FG}; font-size: 10px; font-weight: bold;"
            "font-family: 'Consolas'; letter-spacing: 1px;"
        )
        title_col.addWidget(name_lbl)
        type_lbl = QLabel("HALL / ROOM / STUDIO REVERB")
        type_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        title_col.addWidget(type_lbl)
        top.addLayout(title_col)

        # Room type selector
        self._room_combo = QComboBox()
        self._room_combo.addItems(ROOM_TYPES)
        bind_fixed_height(self._room_combo, 18)
        self._room_combo.setStyleSheet(
            f"QComboBox {{ background: {_LCD_BG}; color: {_LCD_FG}; font-size: 9px;"
            "border: 1px solid #334433; }"
            f"QComboBox QAbstractItemView {{ background: {_LCD_BG}; color: {_LCD_FG}; }}"
        )
        self._room_combo.currentIndexChanged.connect(self._on_room_type)
        self._room_combo.setToolTip("Room type — selects the reverb character: Hall (large, spacious), Room (natural), Chamber (colored), Studio (tight)")
        top.addWidget(self._room_combo)
        top.addStretch()
        self._faceplate_layout.addLayout(top)

        # Bottom row: LCD + knobs + jog wheel
        bot = QHBoxLayout()
        bot.setContentsMargins(4, 0, 4, 2)
        bot.setSpacing(6)

        self._lcd = _RoomLCD()
        bot.addWidget(self._lcd)

        _TIPS = {
            "room_size":  "Room size — scales the virtual room dimensions; larger = longer, more spacious reverb",
            "decay":      "Decay time — how long the reverb tail rings before fading to silence (RT60)",
            "diffusion":  "Diffusion — density of early reflections; high = smooth wash, low = distinct echoes",
            "mix":        "Wet/dry blend — 0%=dry signal only, 100%=full reverb",
        }
        for name, default, param in [
            ("SIZE", 0.55, "room_size"),
            ("DECAY", 0.55, "decay"),
            ("DIFF", 0.70, "diffusion"),
            ("MIX", 0.25, "mix"),
        ]:
            knob = RotaryKnob(color=_LCD_FG)
            knob.set_value(default)
            knob.value_changed.connect(lambda v, p=param: self.param_changed.emit(p, v))
            setattr(self, f"_{param}", knob)
            knob.setToolTip(_TIPS.get(param, ""))
            col = QVBoxLayout(); col.setSpacing(0)
            col.addWidget(QLabel(name), alignment=Qt.AlignmentFlag.AlignCenter)
            col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
            bot.addLayout(col)

        # Jog wheel (decorative / room size alt control)
        self._jog = _JogWheel()
        self._jog.set_value(0.5)
        self._jog.value_changed.connect(
            lambda v: self.param_changed.emit("room_size", v)
        )
        self._jog.setToolTip("Jog wheel — fine-tune room size; synchronized with the SIZE knob")
        # Cross-sync: moving jog updates SIZE knob display and vice versa
        self._jog.value_changed.connect(
            lambda v: (self._room_size.blockSignals(True),
                       self._room_size.set_value(v),
                       self._room_size.blockSignals(False))
        )
        self._room_size.value_changed.connect(
            lambda v: (self._jog.blockSignals(True),
                       self._jog.set_value(v),
                       self._jog.blockSignals(False))
        )
        bot.addWidget(self._jog)

        bot.addStretch()
        self._faceplate_layout.addLayout(bot)

    def _on_room_type(self, idx: int):
        self._lcd.set_room_type(idx)
        self.param_changed.emit("room_type", idx)

    def set_state(self, state: HallReverbState):
        self.set_power(state.enabled, emit=False)
        self._room_combo.blockSignals(True)
        self._room_combo.setCurrentIndex(state.room_type)
        self._room_combo.blockSignals(False)
        self._lcd.set_room_type(state.room_type)
        self._room_size.set_value(state.room_size)
        self._decay.set_value(state.decay)
        self._diffusion.set_value(state.diffusion)
        self._mix.set_value(state.mix)
        self._jog.set_value(state.room_size)
