"""Table Tennis — 1U stereo ping-pong delay."""

import math

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QPen

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

# ── colour palette — sporty green / white table-tennis look ──────────────────
_FACE   = "#0a1a12"   # dark green faceplate
_GREEN  = "#44cc66"   # bright table green
_DIM    = "#226638"   # muted green
_WHITE  = "#e0e0e0"   # ball white
_NET    = "#888888"   # net gray
_ORANGE = "#ee8833"   # orange ball accent


def _knob_col(label: str, knob: RotaryKnob, color: str = _WHITE) -> QVBoxLayout:
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


class _PingPongLogo(QWidget):
    """Ping-pong table with bouncing ball path icon."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 28, 20)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Table outline
        p.setPen(QPen(QColor(_DIM), 1))
        p.drawRect(2, 4, 24, 12)
        # Net
        p.setPen(QPen(QColor(_NET), 1, Qt.PenStyle.DashLine))
        p.drawLine(14, 4, 14, 16)
        # Bouncing ball path (L → R → L)
        p.setPen(QPen(QColor(_ORANGE), 1.5))
        from PySide6.QtCore import QPointF
        pts = [QPointF(4, 14), QPointF(9, 6), QPointF(14, 14),
               QPointF(19, 6), QPointF(24, 14)]
        for i in range(len(pts) - 1):
            p.drawLine(pts[i], pts[i + 1])
        # Ball
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(_ORANGE))
        p.drawEllipse(22, 12, 4, 4)
        p.end()


class PingPongDelayModule(RackModuleBase):
    """1U Table Tennis — stereo ping-pong delay."""

    effect_type = "ping_pong_delay"
    _enabled_param = "enabled"

    def __init__(self, parent=None):
        super().__init__(unit_height=1, faceplate_color=_FACE,
                         title="TABLE TENNIS", parent=parent)
        self._build_faceplate()

    def _build_faceplate(self):
        row = QHBoxLayout()
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(5)

        # Logo + title
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title_col.setContentsMargins(0, 0, 0, 0)
        title_row = QHBoxLayout()
        title_row.setSpacing(3)
        title_row.addWidget(_PingPongLogo())
        name_lbl = QLabel("TABLE TENNIS")
        name_lbl.setStyleSheet(
            f"color: {_GREEN}; font-size: 9px; font-weight: bold;"
            "font-family: 'Courier New'; letter-spacing: 1px;"
        )
        title_row.addWidget(name_lbl)
        title_col.addLayout(title_row)
        sub_lbl = QLabel("PING-PONG DELAY")
        sub_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: 6px; font-weight: bold;"
            "font-family: 'Arial'; letter-spacing: 2px;"
        )
        title_col.addWidget(sub_lbl)
        row.addLayout(title_col)

        row.addStretch()

        # PING knob — left tap time 10–2000ms (log)
        self._ping = RotaryKnob(color=_GREEN)
        self._ping.set_value(0.42)
        self._ping.value_changed.connect(self._on_ping)
        self._ping.setToolTip("PING — left tap delay time (10–2000 ms)")
        self._ping.set_formatter(lambda v: f"{10.0 * 200.0 ** v:.0f} ms")
        row.addLayout(_knob_col("PING", self._ping, _GREEN))

        # LINK button — locks PING/PONG to master TIME
        self._link_btn = QPushButton("LINK")
        bind_fixed_size(self._link_btn, 28, 14)
        self._link_btn.setCheckable(True)
        self._link_btn.setChecked(True)
        self._linked = True
        self._link_btn.clicked.connect(self._on_link_toggle)
        self._refresh_link()
        link_col = QVBoxLayout()
        link_col.setSpacing(0)
        link_col.setContentsMargins(0, 0, 0, 0)
        link_col.addStretch()
        link_col.addWidget(self._link_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        link_col.addStretch()
        row.addLayout(link_col)

        # PONG knob — right tap time 10–2000ms (log)
        self._pong = RotaryKnob(color=_ORANGE)
        self._pong.set_value(0.42)
        self._pong.value_changed.connect(self._on_pong)
        self._pong.setToolTip("PONG — right tap delay time (10–2000 ms)")
        self._pong.set_formatter(lambda v: f"{10.0 * 200.0 ** v:.0f} ms")
        row.addLayout(_knob_col("PONG", self._pong, _ORANGE))

        # FEEDBACK knob — 0–95%
        self._feedback = RotaryKnob(color=_WHITE)
        self._feedback.set_value(0.47)
        self._feedback.value_changed.connect(
            lambda v: self.param_changed.emit("feedback", round(v * 0.95, 3))
        )
        self._feedback.setToolTip("Feedback amount (0–95%)")
        self._feedback.set_formatter(lambda v: f"{v * 95.0:.0f}%")
        row.addLayout(_knob_col("FDBK", self._feedback, _WHITE))

        # SPREAD knob — 0–1
        self._spread = RotaryKnob(color=_NET)
        self._spread.set_value(1.0)
        self._spread.value_changed.connect(
            lambda v: self.param_changed.emit("spread", round(v, 3))
        )
        self._spread.setToolTip("Stereo spread — 0=mono, 1=full ping-pong")
        row.addLayout(_knob_col("SPREAD", self._spread, _NET))

        # DAMP knob — 0–1
        self._damp = RotaryKnob(color=_DIM)
        self._damp.set_value(0.3)
        self._damp.value_changed.connect(
            lambda v: self.param_changed.emit("damping", round(v, 3))
        )
        self._damp.setToolTip("Feedback damping — higher = darker repeats")
        row.addLayout(_knob_col("DAMP", self._damp, _DIM))

        # MIX knob
        self._mix = RotaryKnob(color=_DIM)
        self._mix.set_value(0.35)
        self._mix.value_changed.connect(
            lambda v: self.param_changed.emit("mix", round(v, 3))
        )
        self._mix.setToolTip("Wet/dry blend")
        row.addLayout(_knob_col("MIX", self._mix, _DIM))

        self._faceplate_layout.addLayout(row)

    # ── Link button ───────────────────────────────────────────────────────

    def _refresh_link(self):
        if self._linked:
            self._link_btn.setStyleSheet(
                f"QPushButton {{ background: {_GREEN}; border: 1px solid {_WHITE};"
                f"border-radius: 2px; color: #000; font-size: 6px; font-weight: bold; }}"
            )
        else:
            self._link_btn.setStyleSheet(
                f"QPushButton {{ background: #1a1a12; border: 1px solid {_DIM};"
                f"border-radius: 2px; color: {_DIM}; font-size: 6px; font-weight: bold; }}"
                f"QPushButton:hover {{ border-color: {_GREEN}; }}"
            )

    def _on_link_toggle(self):
        self._linked = self._link_btn.isChecked()
        self._refresh_link()
        self.param_changed.emit("ping_pong_link", self._linked)
        if self._linked:
            # Sync both to ping value
            ms = self._knob_to_time(self._ping.value)
            self._pong.blockSignals(True)
            self._pong.set_value(self._ping.value)
            self._pong.blockSignals(False)
            self.param_changed.emit("time_ms", round(ms, 1))
            self.param_changed.emit("ping_ms", round(ms, 1))
            self.param_changed.emit("pong_ms", round(ms, 1))

    # ── Scaling ───────────────────────────────────────────────────────────

    @staticmethod
    def _knob_to_time(v: float) -> float:
        """0-1 → 10–2000 ms (log)."""
        return 10.0 * (200.0 ** v)

    @staticmethod
    def _time_to_knob(ms: float) -> float:
        ms = max(10.0, min(2000.0, ms))
        return math.log(ms / 10.0) / math.log(200.0)

    def _on_ping(self, v: float):
        ms = self._knob_to_time(v)
        self.param_changed.emit("ping_ms", round(ms, 1))
        if self._linked:
            self._pong.blockSignals(True)
            self._pong.set_value(v)
            self._pong.blockSignals(False)
            self.param_changed.emit("pong_ms", round(ms, 1))
            self.param_changed.emit("time_ms", round(ms, 1))

    def _on_pong(self, v: float):
        ms = self._knob_to_time(v)
        self.param_changed.emit("pong_ms", round(ms, 1))
        if self._linked:
            self._ping.blockSignals(True)
            self._ping.set_value(v)
            self._ping.blockSignals(False)
            self.param_changed.emit("ping_ms", round(ms, 1))
            self.param_changed.emit("time_ms", round(ms, 1))

    # ── State sync ────────────────────────────────────────────────────────

    def set_state(self, state):
        self.set_power(state.enabled, emit=False)
        self._linked = getattr(state, 'ping_pong_link', True)
        self._link_btn.blockSignals(True)
        self._link_btn.setChecked(self._linked)
        self._link_btn.blockSignals(False)
        self._refresh_link()
        if self._linked:
            kv = self._time_to_knob(state.time_ms)
            self._ping.set_value(kv)
            self._pong.set_value(kv)
        else:
            self._ping.set_value(self._time_to_knob(
                getattr(state, 'ping_ms', state.time_ms)))
            self._pong.set_value(self._time_to_knob(
                getattr(state, 'pong_ms', state.time_ms)))
        self._feedback.set_value(state.feedback / 0.95)
        self._spread.set_value(state.spread)
        self._damp.set_value(state.damping)
        self._mix.set_value(state.mix)
