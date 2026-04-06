"""Mixer view — group faders, aux send, and master output in mlrVp.

Pixel-matched to the original mixer.maxpat / output.maxpat / return.maxpat
/ dac.maxpat layout.  Each group strip is ≈51 px wide, with a vertical
volume fader (multislider style), VU meter overlay, send fader, route menu,
mute/stop buttons, and a group-color icon header.
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QFrame, QComboBox, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QPainter, QColor, QPolygon, QPen
from PySide6.QtCore import QPoint

from mlrvp.config import NUM_GROUPS
from mlrvp.gui.styles import (
    GROUP_COLORS, BG_PANEL, BG_BLACK, SEPARATOR, FADER_SLIDER,
    SEND_FADER_COLOR, PURPLE, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_GOLD, ACCENT_BLUE, LOGO_MAGENTA, BG_MAIN,
    FX1_COLOR, FX2_COLOR, AUX_COLOR,
)
from mlrvp.gui.widgets.fader import VerticalFader
from mlrvp.gui.widgets.vu_meter import VUMeter


class _ParallelogramLogo(QWidget):
    """Draws the characteristic mlrV parallelogram logo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(50, 30)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(LOGO_MAGENTA))
        # Original LCD paintpoly: 18 9 53 9 43 30 8 30  (scaled to widget)
        sx = self.width() / 55
        sy = self.height() / 32
        poly = QPolygon([
            QPoint(int(18 * sx), int(9 * sy)),
            QPoint(int(53 * sx), int(9 * sy)),
            QPoint(int(43 * sx), int(30 * sy)),
            QPoint(int(8 * sx), int(30 * sy)),
        ])
        p.drawPolygon(poly)
        p.end()


class GroupChannel(QFrame):
    """Single group mixer channel strip — matches output.maxpat presentation."""

    volume_changed = Signal(int, float)
    send_changed = Signal(int, float)
    mute_toggled = Signal(int, bool)
    fx_route_changed = Signal(int, int)  # group, route (0=OFF, 1=FX1, 2=FX2)
    stop_requested = Signal(int)

    def __init__(self, group_idx: int, parent=None):
        super().__init__(parent)
        self._group = group_idx
        self.setMinimumWidth(50)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"GroupChannel {{ background: {BG_BLACK}; }}")

        color = GROUP_COLORS[group_idx % len(GROUP_COLORS)]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)

        # ── Group icon header (fpic / group-mixer.png equivalent) ──
        icon_label = QLabel(f"G{group_idx + 1}")
        icon_label.setFixedHeight(26)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(
            f"background: {color}; color: {BG_BLACK}; "
            f"font-weight: bold; font-size: 12px; border: none;"
        )
        layout.addWidget(icon_label)

        # ── VU meter + volume fader centered (VU | Fader | VU) ──
        fader_row = QHBoxLayout()
        fader_row.setSpacing(0)
        fader_row.setContentsMargins(0, 0, 0, 0)

        self._vu = VUMeter(Qt.Orientation.Vertical)
        self._vu.setFixedWidth(14)
        fader_row.addWidget(self._vu)

        self._vol_fader = VerticalFader(FADER_SLIDER)
        self._vol_fader.set_value(0.78)  # 127/163 ≈ default
        self._vol_fader.value_changed.connect(
            lambda v: self.volume_changed.emit(self._group, v)
        )
        fader_row.addWidget(self._vol_fader)

        self._vu_r = VUMeter(Qt.Orientation.Vertical)
        self._vu_r.setFixedWidth(14)
        fader_row.addWidget(self._vu_r)

        layout.addLayout(fader_row, stretch=1)

        # ── VOL label for main fader ──
        vol_label = QLabel("VOL")
        vol_label.setFixedHeight(11)
        vol_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vol_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 7px; font-weight: bold; border: none; padding: 0;"
        )
        layout.addWidget(vol_label)

        # ── Delay send fader (small, purple, centered) ──
        send_row = QHBoxLayout()
        send_row.setSpacing(0)
        send_row.setContentsMargins(0, 0, 0, 0)
        send_row.addStretch(1)
        self._send_fader = VerticalFader(SEND_FADER_COLOR)
        self._send_fader.set_value(0.0)
        self._send_fader.setFixedHeight(52)
        self._send_fader.value_changed.connect(
            lambda v: self.send_changed.emit(self._group, v)
        )
        send_row.addWidget(self._send_fader)
        send_row.addStretch(1)
        layout.addLayout(send_row)

        # ── FX route button (cycles OFF → FX1 → FX2) ──
        self._fx_route = 0  # 0=OFF, 1=FX1, 2=FX2
        self._fx_route_btn = QPushButton("OFF")
        self._fx_route_btn.setFixedHeight(17)
        self._fx_route_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(0,0,0,64); color: {SEPARATOR}; "
            f"font-weight: bold; font-size: 9px; border: 1px solid {SEPARATOR}; "
            f"border-radius: 0px; padding: 0px; }}"
        )
        self._fx_route_btn.clicked.connect(self._cycle_fx_route)
        layout.addWidget(self._fx_route_btn)

        # ── Mute button (textbutton MUTE) ──
        self._mute_btn = QPushButton("MUTE")
        self._mute_btn.setCheckable(True)
        self._mute_btn.setFixedHeight(20)
        self._mute_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {SEPARATOR}; "
            f"font-weight: bold; font-size: 8px; border: 1px solid #333; "
            f"padding: 0px 2px; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
            f"QPushButton:checked {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; "
            f"border-color: {ACCENT_GOLD}; }}"
        )
        self._mute_btn.toggled.connect(self._on_mute_toggled)
        layout.addWidget(self._mute_btn)


    def _on_mute_toggled(self, muted):
        self.mute_toggled.emit(self._group, muted)

    def _cycle_fx_route(self):
        """Cycle through OFF → FX1 → FX2."""
        self._fx_route = (self._fx_route + 1) % 3
        self._update_fx_route_display()
        self.fx_route_changed.emit(self._group, self._fx_route)

    def _update_fx_route_display(self):
        _FX_LABELS = ["OFF", "FX1", "FX2"]
        _FX_COLORS = [SEPARATOR, FX1_COLOR, FX2_COLOR]
        label = _FX_LABELS[self._fx_route]
        color = _FX_COLORS[self._fx_route]
        self._fx_route_btn.setText(label)
        if self._fx_route == 0:
            self._fx_route_btn.setStyleSheet(
                f"QPushButton {{ background: rgba(0,0,0,64); color: {SEPARATOR}; "
                f"font-weight: bold; font-size: 9px; border: 1px solid {SEPARATOR}; "
                f"border-radius: 0px; padding: 0px; }}"
            )
        else:
            self._fx_route_btn.setStyleSheet(
                f"QPushButton {{ background: {color}30; color: {color}; "
                f"font-weight: bold; font-size: 9px; border: 2px solid {color}; "
                f"border-radius: 0px; padding: 0px; }}"
                f"QPushButton:hover {{ background: {color}60; }}"
            )

    def set_fx_route(self, route: int):
        self._fx_route = route % 3
        self._update_fx_route_display()

    def set_level(self, level: float):
        self._vu.set_level(level)
        self._vu_r.set_level(level)

    def set_volume(self, vol: float):
        self._vol_fader.set_value(vol)

    def set_send(self, send: float):
        self._send_fader.set_value(send)

    def set_mute(self, muted: bool):
        self._mute_btn.blockSignals(True)
        self._mute_btn.setChecked(muted)
        self._mute_btn.blockSignals(False)


class MixerView(QWidget):
    """Full mixer panel: 4 groups + aux + master — matches mixer.maxpat."""

    group_volume_changed = Signal(int, float)
    group_send_changed = Signal(int, float)
    group_mute_toggled = Signal(int, bool)
    group_fx_route_changed = Signal(int, int)
    group_stop_requested = Signal(int)
    aux_volume_changed = Signal(float)
    aux_mute_toggled = Signal(bool)
    aux_fx_route_changed = Signal(int)  # route (0=OFF, 1=FX1, 2=FX2)
    aux_send_toggled = Signal(bool)   # kept for backward compat, unused
    aux_send_changed = Signal(float)
    master_volume_changed = Signal(float)
    dac_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"MixerView {{ background: {BG_PANEL}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 4, 1, 4)
        layout.setSpacing(4)

        # ── Channel strips row (capped height so faders stay usable) ──
        strip_row = QHBoxLayout()
        strip_row.setContentsMargins(0, 0, 0, 0)
        strip_row.setSpacing(3)

        # Group channels
        self._groups: list[GroupChannel] = []
        for i in range(NUM_GROUPS):
            gc = GroupChannel(i)
            gc.volume_changed.connect(self.group_volume_changed.emit)
            gc.send_changed.connect(self.group_send_changed.emit)
            gc.mute_toggled.connect(self.group_mute_toggled.emit)
            gc.fx_route_changed.connect(self.group_fx_route_changed.emit)
            gc.stop_requested.connect(self.group_stop_requested.emit)
            self._groups.append(gc)
            strip_row.addWidget(gc)

        # ── Separator ──
        sep = QFrame()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background: {SEPARATOR};")
        strip_row.addWidget(sep)

        # ── AUX return (return.maxpat) ──
        aux_frame = QFrame()
        aux_frame.setMinimumWidth(50)
        aux_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        aux_frame.setStyleSheet(f"background: {BG_BLACK};")
        aux_layout = QVBoxLayout(aux_frame)
        aux_layout.setContentsMargins(2, 2, 2, 2)
        aux_layout.setSpacing(1)

        # AUX icon (a-send-mixer.png equivalent)
        aux_icon = QLabel("AUX")
        aux_icon.setFixedHeight(26)
        aux_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        aux_icon.setStyleSheet(
            f"background: {AUX_COLOR}; color: white; "
            f"font-weight: bold; font-size: 12px; border: none;"
        )
        aux_layout.addWidget(aux_icon)

        aux_fader_row = QHBoxLayout()
        aux_fader_row.setSpacing(0)
        aux_fader_row.setContentsMargins(0, 0, 0, 0)

        self._aux_vu_l = VUMeter(Qt.Orientation.Vertical)
        self._aux_vu_l.setFixedWidth(14)
        aux_fader_row.addWidget(self._aux_vu_l)

        self._aux_fader = VerticalFader(FADER_SLIDER)
        self._aux_fader.set_value(0.78)
        self._aux_fader.value_changed.connect(self.aux_volume_changed.emit)
        aux_fader_row.addWidget(self._aux_fader)

        self._aux_vu_r = VUMeter(Qt.Orientation.Vertical)
        self._aux_vu_r.setFixedWidth(14)
        aux_fader_row.addWidget(self._aux_vu_r)

        aux_layout.addLayout(aux_fader_row, stretch=1)

        # AUX VOL label
        aux_vol_label = QLabel("VOL")
        aux_vol_label.setFixedHeight(11)
        aux_vol_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        aux_vol_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 7px; font-weight: bold; border: none; padding: 0;"
        )
        aux_layout.addWidget(aux_vol_label)

        # AUX send fader
        aux_send_row = QHBoxLayout()
        aux_send_row.setSpacing(0)
        aux_send_row.setContentsMargins(0, 0, 0, 0)
        aux_send_row.addStretch(1)
        self._aux_send_fader = VerticalFader(SEND_FADER_COLOR)
        self._aux_send_fader.set_value(0.0)
        self._aux_send_fader.setFixedHeight(52)
        self._aux_send_fader.value_changed.connect(self.aux_send_changed.emit)
        aux_send_row.addWidget(self._aux_send_fader)
        aux_send_row.addStretch(1)
        aux_layout.addLayout(aux_send_row)

        # AUX FX route button (cycles OFF → FX1 → FX2)
        self._aux_fx_route = 0
        self._aux_fx_route_btn = QPushButton("OFF")
        self._aux_fx_route_btn.setFixedHeight(17)
        self._aux_fx_route_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(0,0,0,64); color: {SEPARATOR}; "
            f"font-weight: bold; font-size: 9px; border: 1px solid {SEPARATOR}; "
            f"border-radius: 0px; padding: 0px; }}"
        )
        self._aux_fx_route_btn.clicked.connect(self._cycle_aux_fx_route)
        aux_layout.addWidget(self._aux_fx_route_btn)

        # AUX mute button
        self._aux_mute_btn = QPushButton("MUTE")
        self._aux_mute_btn.setCheckable(True)
        self._aux_mute_btn.setFixedHeight(20)
        self._aux_mute_btn.setStyleSheet(
            f"QPushButton {{ background: {BG_BLACK}; color: {SEPARATOR}; "
            f"font-weight: bold; font-size: 8px; border: 1px solid #333; "
            f"padding: 0px 2px; }}"
            f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
            f"QPushButton:checked {{ background: {ACCENT_GOLD}; color: {BG_BLACK}; "
            f"border-color: {ACCENT_GOLD}; }}"
        )
        self._aux_mute_btn.toggled.connect(self.aux_mute_toggled.emit)
        aux_layout.addWidget(self._aux_mute_btn)

        strip_row.addWidget(aux_frame)

        # ── Separator ──
        sep2 = QFrame()
        sep2.setFixedWidth(1)
        sep2.setStyleSheet(f"background: {SEPARATOR};")
        strip_row.addWidget(sep2)

        # ── Master / DAC (dac.maxpat) ──
        master_frame = QFrame()
        master_frame.setMinimumWidth(54)
        master_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        master_frame.setStyleSheet(f"background: {BG_BLACK};")
        master_layout = QVBoxLayout(master_frame)
        master_layout.setContentsMargins(2, 2, 2, 2)
        master_layout.setSpacing(1)

        # Master icon (M-mixer.png equivalent)
        master_icon = QLabel("M")
        master_icon.setFixedHeight(26)
        master_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        master_icon.setStyleSheet(
            f"background: white; color: {BG_BLACK}; "
            f"font-weight: bold; font-size: 14px; border: none;"
        )
        master_layout.addWidget(master_icon)

        master_fader_row = QHBoxLayout()
        master_fader_row.setSpacing(0)
        master_fader_row.setContentsMargins(0, 0, 0, 0)

        self._master_vu_l = VUMeter(Qt.Orientation.Vertical)
        self._master_vu_l.setFixedWidth(14)
        master_fader_row.addWidget(self._master_vu_l)

        self._master_fader = VerticalFader(FADER_SLIDER)
        self._master_fader.set_value(0.78)
        self._master_fader.value_changed.connect(self.master_volume_changed.emit)
        master_fader_row.addWidget(self._master_fader)

        self._master_vu_r = VUMeter(Qt.Orientation.Vertical)
        self._master_vu_r.setFixedWidth(14)
        master_fader_row.addWidget(self._master_vu_r)

        master_layout.addLayout(master_fader_row, stretch=1)

        # Master label
        master_route_label = QLabel("MSTR")
        master_route_label.setFixedHeight(17)
        master_route_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        master_route_label.setStyleSheet(
            "background: rgba(0,0,0,64); color: white; "
            "font-weight: bold; font-size: 9px; border: none;"
        )
        master_layout.addWidget(master_route_label)

        # DAC (on/off) button
        self._dac_btn = QPushButton("DAC")
        self._dac_btn.setCheckable(True)
        self._dac_btn.setFixedHeight(20)
        self._dac_btn.setStyleSheet(
            "QPushButton { background: white; color: black; "
            "font-weight: bold; font-size: 9px; border: none; padding: 0px 2px; }"
            "QPushButton:checked { background: rgba(0,0,0,128); color: white; }"
        )
        self._dac_btn.toggled.connect(self.dac_toggled.emit)
        master_layout.addWidget(self._dac_btn)

        strip_row.addWidget(master_frame)

        # Wrap strip_row in a container
        strip_container = QWidget()
        strip_container_layout = QHBoxLayout(strip_container)
        strip_container_layout.setContentsMargins(0, 0, 0, 0)
        strip_container_layout.addLayout(strip_row)
        strip_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout.addWidget(strip_container, stretch=1)

    def set_audio_enabled(self, enabled: bool):
        self._dac_btn.blockSignals(True)
        self._dac_btn.setChecked(not enabled)  # checked=dark=OFF, unchecked=white=ON
        self._dac_btn.blockSignals(False)

    def set_group_level(self, group: int, level: float):
        if 0 <= group < len(self._groups):
            self._groups[group].set_level(level)

    def set_master_level(self, left: float, right: float):
        self._master_vu_l.set_level(left)
        self._master_vu_r.set_level(right)

    def set_aux_level(self, level: float):
        self._aux_vu_l.set_level(level)
        self._aux_vu_r.set_level(level)

    def get_group_channel(self, idx: int) -> GroupChannel:
        return self._groups[idx]

    def set_group_fx_route(self, group: int, route: int):
        """Set FX route display for a group channel (syncs from effects view)."""
        if 0 <= group < len(self._groups):
            self._groups[group].set_fx_route(route)

    def _cycle_aux_fx_route(self):
        """Cycle AUX FX route: OFF → FX1 → FX2."""
        self._aux_fx_route = (self._aux_fx_route + 1) % 3
        self._update_aux_fx_route_display()
        self.aux_fx_route_changed.emit(self._aux_fx_route)

    def _update_aux_fx_route_display(self):
        _FX_LABELS = ["OFF", "FX1", "FX2"]
        _FX_COLORS = [SEPARATOR, FX1_COLOR, FX2_COLOR]
        label = _FX_LABELS[self._aux_fx_route]
        color = _FX_COLORS[self._aux_fx_route]
        self._aux_fx_route_btn.setText(label)
        if self._aux_fx_route == 0:
            self._aux_fx_route_btn.setStyleSheet(
                f"QPushButton {{ background: rgba(0,0,0,64); color: {SEPARATOR}; "
                f"font-weight: bold; font-size: 9px; border: 1px solid {SEPARATOR}; "
                f"border-radius: 0px; padding: 0px; }}"
            )
        else:
            self._aux_fx_route_btn.setStyleSheet(
                f"QPushButton {{ background: {color}30; color: {color}; "
                f"font-weight: bold; font-size: 9px; border: 2px solid {color}; "
                f"border-radius: 0px; padding: 0px; }}"
                f"QPushButton:hover {{ background: {color}60; }}"
            )

    def set_aux_fx_route(self, route: int):
        self._aux_fx_route = route % 3
        self._update_aux_fx_route_display()

    def set_aux_send(self, level: float):
        self._aux_send_fader.blockSignals(True)
        self._aux_send_fader.set_value(level)
        self._aux_send_fader.blockSignals(False)

    def set_aux_mute(self, muted: bool):
        self._aux_mute_btn.blockSignals(True)
        self._aux_mute_btn.setChecked(muted)
        self._aux_mute_btn.blockSignals(False)
