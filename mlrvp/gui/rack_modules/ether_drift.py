"""Ether Drift — 2U bipolar infinite phaser for vaporwave/slushwave."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QLinearGradient

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size, bind_fixed_width

# ── colour palette — deep-space teal, vaporwave aesthetic ────────────────────
_FACE    = "#080e1c"   # near-black midnight teal faceplate
_ACCENT  = "#00ddbb"   # bright teal/cyan — main accent
_DIM     = "#006655"   # dark teal — secondary labels
_GLOW    = "#66ffee"   # highlight teal — active LED / hover
_AMBER   = "#ffbb44"   # warm amber — SYNC LED on
_AMBER_D = "#664400"   # dim amber — SYNC LED off
_RISE    = "#0099ff"   # blue-teal — RISE / UP indicator
_FALL    = "#ff44aa"   # pink-magenta — FALL / DOWN indicator
_BOTH    = "#00ddbb"   # teal — BOTH indicator (same as accent)
_SEP     = "#0d2535"   # separator line color


# ── Logo widget ───────────────────────────────────────────────────────────────

class _DriftLogo(QWidget):
    """Signal splitting into two diverging curved paths — one rising, one falling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 32, 44)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        stem_x = 7
        mid_y = 22

        # Stem entry
        p.setPen(QPen(QColor(_ACCENT), 2))
        p.drawLine(2, mid_y, stem_x, mid_y)

        # RISE path — curves upward-right
        rise_path = QPainterPath()
        rise_path.moveTo(stem_x, mid_y)
        rise_path.cubicTo(stem_x + 6, mid_y, 20, mid_y - 14, 29, mid_y - 18)
        p.setPen(QPen(QColor(_RISE), 2))
        p.drawPath(rise_path)
        # Arrowhead: tip at (29, mid_y-18)
        p.setBrush(QColor(_RISE))
        p.setPen(Qt.PenStyle.NoPen)
        arr_r = QPainterPath()
        arr_r.moveTo(29, mid_y - 18)
        arr_r.lineTo(24, mid_y - 15)
        arr_r.lineTo(25, mid_y - 20)
        arr_r.closeSubpath()
        p.drawPath(arr_r)

        # FALL path — curves downward-right
        fall_path = QPainterPath()
        fall_path.moveTo(stem_x, mid_y)
        fall_path.cubicTo(stem_x + 6, mid_y, 20, mid_y + 14, 29, mid_y + 18)
        p.setPen(QPen(QColor(_FALL), 2))
        p.drawPath(fall_path)
        # Arrowhead: tip at (29, mid_y+18)
        p.setBrush(QColor(_FALL))
        p.setPen(Qt.PenStyle.NoPen)
        arr_f = QPainterPath()
        arr_f.moveTo(29, mid_y + 18)
        arr_f.lineTo(24, mid_y + 15)
        arr_f.lineTo(25, mid_y + 20)
        arr_f.closeSubpath()
        p.drawPath(arr_f)

        # Center dot at split point
        p.setBrush(QColor(_ACCENT))
        p.drawEllipse(stem_x - 2, mid_y - 2, 4, 4)

        p.end()


# ── Route selector (3-button exclusive group) ─────────────────────────────────

class _RouteSelector(QWidget):
    """Three-button exclusive selector for RISE / BOTH / FALL routing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._route = 0       # 0=BOTH, 1=UP, 2=DOWN
        self._callback = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        btn_ss_base = (
            "QPushButton {{ background: #0d1a2a; border: 1px solid #1a3040;"
            "border-radius: 2px; color: {c}; font-size: 7px; font-weight: bold;"
            "letter-spacing: 1px; padding: 1px 4px; }}"
            "QPushButton:hover {{ background: #152535; }}"
        )
        btn_ss_on = (
            "QPushButton {{ background: #0d3040; border: 1px solid {c};"
            "border-radius: 2px; color: {c}; font-size: 7px; font-weight: bold;"
            "letter-spacing: 1px; padding: 1px 4px; }}"
        )

        self._btn_up = QPushButton("RISE↑")
        bind_fixed_height(self._btn_up, 16)
        self._btn_up.clicked.connect(lambda: self._select(1))
        self._btn_up.setToolTip("RISE: trigger only on pitch going up")

        self._btn_both = QPushButton("BOTH")
        bind_fixed_height(self._btn_both, 16)
        self._btn_both.clicked.connect(lambda: self._select(0))
        self._btn_both.setToolTip("BOTH: trigger on any pitch direction")

        self._btn_down = QPushButton("FALL↓")
        bind_fixed_height(self._btn_down, 16)
        self._btn_down.clicked.connect(lambda: self._select(2))
        self._btn_down.setToolTip("FALL: trigger only on pitch going down")

        self._buttons = [self._btn_both, self._btn_up, self._btn_down]
        self._colors  = [_BOTH, _RISE, _FALL]
        for btn in self._buttons:
            layout.addWidget(btn)

        self._btn_ss_base = btn_ss_base
        self._btn_ss_on   = btn_ss_on
        self._refresh_styles()

    def _refresh_styles(self):
        # Map route → button index: BOTH=0, UP=1, DOWN=2
        active_btn_idx = self._route  # 0→btn_both, 1→btn_up, 2→btn_down
        for i, btn in enumerate([self._btn_both, self._btn_up, self._btn_down]):
            c = self._colors[i]
            if i == active_btn_idx:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #0d3040; border: 1px solid {c};"
                    f"border-radius: 2px; color: {c}; font-size: 7px; font-weight: bold;"
                    f"letter-spacing: 1px; padding: 1px 4px; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #0d1a2a; border: 1px solid #1a3040;"
                    f"border-radius: 2px; color: #334455; font-size: 7px; font-weight: bold;"
                    f"letter-spacing: 1px; padding: 1px 4px; }}"
                    f"QPushButton:hover {{ background: #152535; color: {c}; }}"
                )

    def _select(self, route: int):
        self._route = route
        self._refresh_styles()
        if self._callback:
            self._callback(route)

    def set_route(self, route: int):
        self._route = route
        self._refresh_styles()

    def on_change(self, callback):
        self._callback = callback


# ── Main module ───────────────────────────────────────────────────────────────

def _knob_col(label_text: str, knob: RotaryKnob, color: str) -> QVBoxLayout:
    """Helper: label above a knob, both center-aligned."""
    col = QVBoxLayout()
    col.setSpacing(0)
    col.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label_text)
    lbl.setStyleSheet(f"color: {color}; font-size: 7px; font-weight: bold;"
                      "font-family: 'Arial'; letter-spacing: 1px;")
    col.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
    col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
    return col


class EtherDriftModule(RackModuleBase):
    """2U Ether Drift — bipolar infinite phaser, vaporwave aesthetic."""

    effect_type = "ether_drift"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=2, faceplate_color=_FACE,
                         title="ETHER DRIFT", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        # ── Row 1: identity + main knobs ─────────────────────────────────────
        row1 = QHBoxLayout()
        row1.setContentsMargins(4, 2, 4, 1)
        row1.setSpacing(5)

        # Logo
        row1.addWidget(_DriftLogo())

        # Title block
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 4, 0, 0)
        name_lbl = QLabel("ETHER DRIFT")
        name_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 11px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px;"
        )
        title_col.addWidget(name_lbl)
        sub_lbl = QLabel("BIPOLAR PHASER")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 7px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 3px;"
        )
        title_col.addWidget(sub_lbl)
        row1.addLayout(title_col)

        row1.addStretch()

        # RATE knob
        self._rate = RotaryKnob(color=_ACCENT)
        self._rate.set_value(0.15)
        self._rate.value_changed.connect(
            lambda v: self.param_changed.emit("rate", v)
        )
        self._rate.setToolTip("LFO rate for the bipolar phaser sweep")
        row1.addLayout(_knob_col("RATE", self._rate, _ACCENT))

        # DEPTH knob
        self._depth = RotaryKnob(color="#4488ff")
        self._depth.set_value(0.45)
        self._depth.value_changed.connect(
            lambda v: self.param_changed.emit("depth", v)
        )
        self._depth.setToolTip("Depth of the phase sweep — higher = more intense spectral filtering")
        row1.addLayout(_knob_col("DEPTH", self._depth, "#4488ff"))

        # FDBK knob
        self._fdbk = RotaryKnob(color=_FALL)
        self._fdbk.set_value(0.25)
        self._fdbk.value_changed.connect(
            lambda v: self.param_changed.emit("feedback", v)
        )
        self._fdbk.setToolTip("Feedback amount — increases resonance and spectral coloring")
        row1.addLayout(_knob_col("FDBK", self._fdbk, _FALL))

        # SPREAD knob
        self._spread = RotaryKnob(color=_AMBER)
        self._spread.set_value(0.5)
        self._spread.value_changed.connect(
            lambda v: self.param_changed.emit("spread", v)
        )
        self._spread.setToolTip("Stereo spread — separates the LFO phase between left and right channels")
        row1.addLayout(_knob_col("SPREAD", self._spread, _AMBER))

        # MIX knob
        self._mix = RotaryKnob(color="#aaaaaa")
        self._mix.set_value(0.35)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", v)
        )
        self._mix.setToolTip("Wet/dry blend of the Ether Drift effect")
        row1.addLayout(_knob_col("MIX", self._mix, "#aaaaaa"))

        self._faceplate_layout.addLayout(row1)

        # ── Separator line ────────────────────────────────────────────────────
        sep = QWidget()
        bind_fixed_height(sep, 1)
        sep.setStyleSheet(f"background: {_SEP};")
        self._faceplate_layout.addWidget(sep)

        # ── Row 2: SYNC toggle + ROUTE selector + status label ────────────────
        row2 = QHBoxLayout()
        row2.setContentsMargins(4, 2, 4, 2)
        row2.setSpacing(8)

        # SYNC LED button
        self._sync_btn = QPushButton("⬡  SYNC")
        self._sync_btn.setCheckable(True)
        self._sync_btn.setChecked(True)
        bind_fixed_height(self._sync_btn, 20)
        bind_fixed_width(self._sync_btn, 64)
        self._sync_btn.toggled.connect(self._on_sync_toggled)
        self._update_sync_style(True)
        self._sync_btn.setToolTip("Sync the LFO rate to the current project BPM")
        row2.addWidget(self._sync_btn)

        # ROUTE selector
        self._route_sel = _RouteSelector()
        self._route_sel.on_change(self._on_route_change)
        row2.addWidget(self._route_sel)

        row2.addStretch()

        # Status label — shows RISE↑ / FALL↓ / RISE↑+FALL↓
        self._status_lbl = QLabel("RISE↑  +  FALL↓")
        self._status_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 8px; font-family: 'Courier New';"
            "letter-spacing: 1px;"
        )
        row2.addWidget(self._status_lbl)
        row2.addSpacing(6)

        self._faceplate_layout.addLayout(row2)

    # ── Internal handlers ─────────────────────────────────────────────────────

    def _on_sync_toggled(self, on: bool):
        self._update_sync_style(on)
        self.param_changed.emit("sync", on)

    def _update_sync_style(self, on: bool):
        if on:
            self._sync_btn.setStyleSheet(
                f"QPushButton {{ background: #1a2a1a; border: 1px solid {_AMBER};"
                f"border-radius: 3px; color: {_AMBER}; font-size: 8px; font-weight: bold;"
                f"letter-spacing: 1px; }}"
            )
        else:
            self._sync_btn.setStyleSheet(
                f"QPushButton {{ background: #0d1a2a; border: 1px solid {_AMBER_D};"
                f"border-radius: 3px; color: {_AMBER_D}; font-size: 8px; font-weight: bold;"
                f"letter-spacing: 1px; }}"
                f"QPushButton:hover {{ border-color: {_AMBER}; color: {_AMBER}; }}"
            )

    def _on_route_change(self, route: int):
        labels = ["RISE↑  +  FALL↓", "RISE↑ only", "FALL↓ only"]
        colors = [_DIM, _RISE, _FALL]
        if not (0 <= route < len(labels)):
            return
        self._status_lbl.setText(labels[route])
        self._status_lbl.setStyleSheet(
            f"color: {colors[route]}; font-size: 8px; font-family: 'Courier New';"
            "letter-spacing: 1px;"
        )
        self.param_changed.emit("route", route)

    # ── State sync ────────────────────────────────────────────────────────────

    def set_state(self, state):
        """Sync GUI from an EtherDriftState dataclass."""
        self.set_power(state.enabled, emit=False)
        self._rate.set_value(state.rate)
        self._depth.set_value(state.depth)
        self._fdbk.set_value(state.feedback)
        self._spread.set_value(state.spread)
        self._mix.set_value(state.mix)
        self._sync_btn.blockSignals(True)
        self._sync_btn.setChecked(state.sync)
        self._update_sync_style(state.sync)
        self._sync_btn.blockSignals(False)
        self._route_sel.set_route(state.route)
        self._on_route_change(state.route)

