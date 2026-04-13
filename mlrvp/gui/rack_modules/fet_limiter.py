"""All Buttons In — 2U Urei 1176LN-style FET peak limiter."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QFont, QLinearGradient, QPainterPath,
)

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob

# ── colour palette — 1176LN blackface: black anodized + silver silk-screen ───
_FACE    = "#0a0a0a"   # solid black anodized faceplate
_SILVER  = "#c0c0c0"   # silver silk-screen text / accents
_DIM     = "#555555"   # dim silver — secondary labels
_AMBER   = "#ffaa22"   # warm amber — VU needle / active ratio
_AMBER_D = "#553300"   # dim amber — inactive ratio
_SEP     = "#1a1a1a"   # separator line
_VU_CREAM = "#f5e6c8"  # warm cream VU meter background
_VU_DARK  = "#2a2018"  # dark frame around VU window
_NEEDLE   = "#cc2222"  # red needle


class _BoltLogo(QWidget):
    """Lightning bolt — FET / electricity symbol."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 24)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bolt = QPainterPath()
        bolt.moveTo(10, 0)
        bolt.lineTo(3, 10)
        bolt.lineTo(8, 10)
        bolt.lineTo(5, 24)
        bolt.lineTo(13, 12)
        bolt.lineTo(9, 12)
        bolt.lineTo(10, 0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_AMBER))
        p.drawPath(bolt)
        p.end()


# ── Analog VU needle meter — 1176-style gain reduction display ────────────────

class _VUNeedleMeter(QWidget):
    """Skeuomorphic analog VU meter with swinging needle for GR display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._FONT_SCALE = QFont("Arial", 5)
        self._FONT_GR = QFont("Arial", 6, QFont.Weight.Bold)
        self.setFixedSize(130, 80)
        self._gr_db = 0.0  # 0 = no reduction

    def set_gr(self, gr_db: float):
        self._gr_db = gr_db
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # ── Outer dark bezel ──
        p.setPen(QPen(QColor("#333333"), 1))
        p.setBrush(QColor(_VU_DARK))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 4, 4)

        # ── Inner cream VU window ──
        mx, my = 6, 5
        iw, ih = w - mx * 2, h - my * 2 - 8
        grad = QLinearGradient(mx, my, mx, my + ih)
        grad.setColorAt(0.0, QColor("#f8eed5"))
        grad.setColorAt(0.4, QColor(_VU_CREAM))
        grad.setColorAt(1.0, QColor("#e8d4a8"))
        p.setPen(QPen(QColor("#8a7a5a"), 1))
        p.setBrush(grad)
        p.drawRoundedRect(mx, my, iw, ih, 3, 3)

        # ── Geometry ──
        cx = w / 2
        cy = ih + my + 14          # pivot below the window
        needle_r = ih - 2          # needle length
        arc_r = needle_r + 2       # outer arc radius

        # Real 1176 GR scale: -20 dB (left) → 0 dB → +3 dB (right)
        # Marks are evenly spaced across the arc (like the real unit).
        # 130° = screen-left, 50° = screen-right
        a_left_deg, a_right_deg = 130.0, 50.0
        sweep = a_left_deg - a_right_deg  # 80°

        gr_marks = [-20, -10, -7, -5, -3, -1, 0, 1, 2, 3]
        n_marks = len(gr_marks)
        _mark_angles = {}  # db → radians
        for i, db in enumerate(gr_marks):
            frac = i / (n_marks - 1)  # 0.0 … 1.0 evenly
            _mark_angles[db] = math.radians(a_left_deg - frac * sweep)

        def _db_angle(db_val):
            """Map dB value to angle via linear interpolation between marks."""
            if db_val <= gr_marks[0]:
                return _mark_angles[gr_marks[0]]
            if db_val >= gr_marks[-1]:
                return _mark_angles[gr_marks[-1]]
            for j in range(len(gr_marks) - 1):
                lo, hi = gr_marks[j], gr_marks[j + 1]
                if lo <= db_val <= hi:
                    t = (db_val - lo) / (hi - lo)
                    a_lo = _mark_angles[lo]
                    a_hi = _mark_angles[hi]
                    return a_lo + t * (a_hi - a_lo)
            return _mark_angles[0]

        # ── Continuous arc line — dark for -20…0, red for 0…+3 ──
        _BRN = QColor("#4a3a2a")
        _RED = QColor("#cc2222")
        red_threshold = 0.0  # red zone starts at 0 dB

        n_seg = 50
        for i in range(n_seg):
            frac1 = i / n_seg
            frac2 = (i + 1) / n_seg
            db1 = gr_marks[0] + (gr_marks[-1] - gr_marks[0]) * frac1
            db2 = gr_marks[0] + (gr_marks[-1] - gr_marks[0]) * frac2
            color = _RED if (db1 + db2) / 2 >= red_threshold else _BRN
            a1, a2 = _db_angle(db1), _db_angle(db2)
            p.setPen(QPen(color, 1))
            p.drawLine(
                int(cx + arc_r * math.cos(a1)),
                int(cy - arc_r * math.sin(a1)),
                int(cx + arc_r * math.cos(a2)),
                int(cy - arc_r * math.sin(a2)),
            )

        # ── Tick marks + numbers — evenly spaced ──
        scale_font = self._FONT_SCALE
        p.setFont(scale_font)
        fm = p.fontMetrics()

        for db in gr_marks:
            angle = _mark_angles[db]
            color = _RED if db >= red_threshold else _BRN

            # Tick from arc inward (5 px)
            ox = cx + arc_r * math.cos(angle)
            oy = cy - arc_r * math.sin(angle)
            ix = cx + (arc_r - 5) * math.cos(angle)
            iy = cy - (arc_r - 5) * math.sin(angle)
            p.setPen(QPen(color, 1))
            p.drawLine(int(ox), int(oy), int(ix), int(iy))

            # Number above the arc
            lbl = f"+{db}" if db > 0 else str(db)
            tw = fm.horizontalAdvance(lbl)
            th = fm.ascent()
            lx = cx + (arc_r + 6) * math.cos(angle) - tw / 2
            ly = cy - (arc_r + 6) * math.sin(angle) + th / 3
            if my + 2 < ly < my + ih - 1:
                p.setPen(color)
                p.drawText(int(lx), int(ly), lbl)

        # ── "GR  dB" label below cream window, in dark bezel ──
        gr_font = self._FONT_GR
        p.setFont(gr_font)
        gr_text = "GR  dB"
        gtw = p.fontMetrics().horizontalAdvance(gr_text)
        p.setPen(QColor(_SILVER))
        p.drawText(int(cx - gtw / 2), h - 4, gr_text)

        # ── Needle — clip to cream window so it hides behind bezel + GR text ──
        p.save()
        p.setClipRect(QRectF(mx, my, iw, ih))
        # Map: 0 dB GR → -20 (rest/left), 20 dB GR → 0, 23 dB GR → +3
        vu_val = -20.0 + abs(self._gr_db)
        vu_val = max(-20.0, min(3.0, vu_val))
        needle_angle = _db_angle(vu_val)
        nx = cx + needle_r * math.cos(needle_angle)
        ny = cy - needle_r * math.sin(needle_angle)
        p.setPen(QPen(QColor(_NEEDLE), 1.5))
        p.drawLine(int(cx), int(cy), int(nx), int(ny))
        p.restore()

        # Pivot dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#222222"))
        p.drawEllipse(int(cx) - 3, int(cy) - 3, 6, 6)

        p.end()


# ── Ratio button column — vertical exclusive select like real 1176 ────────────

class _RatioButtons(QWidget):
    """Custom-painted vertical ratio selector — no QPushButton clipping issues."""
    _FONT_CELL = QFont("Arial", 6, QFont.Weight.Bold)

    _LABELS = ("20", "12", "8", "4", "ALL")
    _IDX_MAP = (3, 2, 1, 0, 4)  # display position → ratio_idx
    _CELL_H = 12
    _W = 28

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected = 3  # display position (default = "4" which is pos 3)
        self._callback = None
        self.setFixedSize(self._W, self._CELL_H * len(self._LABELS))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _select(self, pos: int):
        self._selected = pos
        self.update()
        if self._callback:
            self._callback(self._IDX_MAP[pos])

    def set_index(self, ratio_idx: int):
        for i, mapped in enumerate(self._IDX_MAP):
            if mapped == ratio_idx:
                self._selected = i
                break
        self.update()

    def on_change(self, cb):
        self._callback = cb

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = int(event.position().y()) // self._CELL_H
            pos = max(0, min(pos, len(self._LABELS) - 1))
            self._select(pos)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fnt = self._FONT_CELL
        p.setFont(fnt)
        fm = p.fontMetrics()
        for i, label in enumerate(self._LABELS):
            y = i * self._CELL_H
            r = QRectF(0, y, self._W, self._CELL_H)
            sel = (i == self._selected)
            # Background
            bg = QColor(_AMBER) if sel else QColor("#1a1a1a")
            border = QColor(_AMBER) if sel else QColor("#333333")
            p.setPen(QPen(border, 1))
            p.setBrush(bg)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 2, 2)
            # Text
            fg = QColor("#000000") if sel else QColor(_DIM)
            p.setPen(fg)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)
        p.end()


# ── Stepped selector — for ATTACK (7 pos) and RELEASE (5 pos) ───────────────

class _SteppedKnob(QWidget):
    """Stepped rotary selector with numbered positions."""

    def __init__(self, n_steps: int, default: int = 0,
                 color: str = _SILVER, parent=None):
        super().__init__(parent)
        self._n = n_steps
        self._idx = default
        self._callback = None
        self._color = color
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_index(self, idx: int):
        self._idx = max(0, min(idx, self._n - 1))
        self.update()

    def on_change(self, cb):
        self._callback = cb

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._idx = (self._idx + 1) % self._n
            self.update()
            if self._callback:
                self._callback(self._idx)
        elif event.button() == Qt.MouseButton.RightButton:
            self._idx = (self._idx - 1) % self._n
            self.update()
            if self._callback:
                self._callback(self._idx)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy, r = 14, 14, 10

        # Outer ring
        p.setPen(QPen(QColor(self._color), 1.5))
        p.setBrush(QColor("#1a1a1a"))
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Position dots around the arc
        import math
        start_angle = 220  # degrees, 7-o'clock position
        sweep = 280        # total sweep
        for i in range(self._n):
            angle = math.radians(start_angle - (sweep * i / max(self._n - 1, 1)))
            dx = cx + (r + 4) * math.cos(angle)
            dy = cy - (r + 4) * math.sin(angle)
            if i == self._idx:
                p.setBrush(QColor(_AMBER))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(int(dx) - 2, int(dy) - 2, 5, 5)
            else:
                p.setBrush(QColor(_DIM))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(int(dx) - 1, int(dy) - 1, 3, 3)

        # Pointer line
        angle = math.radians(start_angle - (sweep * self._idx / max(self._n - 1, 1)))
        lx = cx + (r - 4) * math.cos(angle)
        ly = cy - (r - 4) * math.sin(angle)
        p.setPen(QPen(QColor(_AMBER), 2))
        p.drawLine(cx, cy, int(lx), int(ly))
        p.end()


# ── Main module ──────────────────────────────────────────────────────────────

class FETLimiterModule(RackModuleBase):
    """2U 1176LN-style FET peak limiter rack module."""

    effect_type = "fet_limiter"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=2, faceplate_color=_FACE,
                         title="ALL BUTTONS IN", parent=parent)
        self._build_faceplate()

    # helper: labelled knob column
    @staticmethod
    def _knob_col(label, knob, color=_SILVER, below=True):
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
        if below:
            col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
            col.addWidget(lbl)
        else:
            col.addWidget(lbl)
            col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
        return col

    def _build_faceplate(self):
        # Single horizontal row — matches real 1176LN left-to-right layout
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(6)

        # ── INPUT knob (large) ────────────────────────────────────────────
        self._input = RotaryKnob(color=_SILVER)
        self._input.set_value(0.5)
        self._input.value_changed.connect(
            lambda v: self.param_changed.emit("input_gain", v)
        )
        self._input.setToolTip("Input drive — pushes signal into compression (0–40dB)")
        self._input.set_formatter(lambda v: f"{v * 40.0:.1f} dB")
        row.addLayout(self._knob_col("INPUT", self._input, _SILVER))

        row.addSpacing(2)

        # ── OUTPUT knob (large) ───────────────────────────────────────────
        self._output = RotaryKnob(color=_SILVER)
        self._output.set_value(0.5)
        self._output.value_changed.connect(
            lambda v: self.param_changed.emit("output_gain", v)
        )
        self._output.setToolTip("Output level / makeup gain (-20 to +20dB)")
        self._output.set_formatter(lambda v: f"{v * 40.0 - 20.0:+.1f} dB")
        row.addLayout(self._knob_col("OUTPUT", self._output, _SILVER))

        row.addSpacing(4)

        # ── ATTACK / RELEASE stacked (smaller stepped knobs) ─────────────
        ar_col = QVBoxLayout()
        ar_col.setSpacing(2)
        ar_col.setContentsMargins(0, 0, 0, 0)

        # ATTACK
        self._attack = _SteppedKnob(7, default=4, color=_SILVER)
        self._attack.on_change(
            lambda idx: self.param_changed.emit("attack_idx", idx)
        )
        self._attack.setToolTip("Attack time (7 steps): 1=fastest ~20µs → 7=slowest ~800µs")
        atk_lbl = QLabel("ATTACK")
        atk_lbl.setFixedHeight(9)
        atk_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        atk_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        ar_col.addWidget(atk_lbl)
        ar_col.addWidget(self._attack, alignment=Qt.AlignmentFlag.AlignCenter)

        # RELEASE
        self._release = _SteppedKnob(5, default=2, color=_SILVER)
        self._release.on_change(
            lambda idx: self.param_changed.emit("release_idx", idx)
        )
        self._release.setToolTip("Release time (5 steps): 1=50ms → 5=1.1s")
        ar_col.addWidget(self._release, alignment=Qt.AlignmentFlag.AlignCenter)
        rel_lbl = QLabel("RELEASE")
        rel_lbl.setFixedHeight(9)
        rel_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rel_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        ar_col.addWidget(rel_lbl)
        row.addLayout(ar_col)

        row.addSpacing(2)

        # ── RATIO vertical button column ─────────────────────────────────
        ratio_outer = QVBoxLayout()
        ratio_outer.setSpacing(0)
        ratio_outer.setContentsMargins(0, 0, 0, 0)
        ratio_lbl = QLabel("RATIO")
        ratio_lbl.setFixedHeight(8)
        ratio_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ratio_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 1px;"
        )
        ratio_outer.addWidget(ratio_lbl)
        self._ratio = _RatioButtons()
        self._ratio.on_change(
            lambda idx: self.param_changed.emit("ratio_idx", idx)
        )
        self._ratio.setToolTip(
            "Compression ratio: 4:1 / 8:1 / 12:1 / 20:1 / ALL (British mode)"
        )
        ratio_outer.addWidget(self._ratio)
        row.addLayout(ratio_outer)

        row.addSpacing(2)

        # ── VU needle meter ──────────────────────────────────────────────
        self._vu_meter = _VUNeedleMeter()
        row.addWidget(self._vu_meter, alignment=Qt.AlignmentFlag.AlignCenter)

        row.addSpacing(2)

        # ── Right title block ────────────────────────────────────────────
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 4, 0, 0)
        title_col.addWidget(_BoltLogo(), alignment=Qt.AlignmentFlag.AlignCenter)
        name_lbl = QLabel("ALL\nBUTTONS\nIN")
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_lbl.setStyleSheet(
            f"color: {_SILVER}; font-size: 8px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px; line-height: 1;"
        )
        title_col.addWidget(name_lbl)
        title_col.addStretch()
        sub_lbl = QLabel("FET\nLIMITER")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        self._faceplate_layout.addLayout(row)

    # ── State sync ────────────────────────────────────────────────────────────

    def set_state(self, state):
        """Sync GUI from a FETLimiterState dataclass."""
        self.set_power(state.enabled, emit=False)
        self._input.set_value(state.input_gain)
        self._output.set_value(state.output_gain)
        self._attack.set_index(state.attack_idx)
        self._release.set_index(state.release_idx)
        self._ratio.set_index(state.ratio_idx)

    def set_gr(self, gr_db: float):
        """Update the VU needle meter from engine data."""
        self._vu_meter.set_gr(gr_db)
