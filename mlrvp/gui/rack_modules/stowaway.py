"""Foreign Exchange — 2U external plugin host (VST3 · AU)."""

from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath

from mlrvp.gui.widgets.rack_module_base import RackModuleBase
from mlrvp.gui.widgets.fader import RotaryKnob
from mlrvp.gui.scale import bind_fixed_height, bind_fixed_size

# ── Colour palette — dark forest / mint accent ──────────────────────────────
_FACE = "#091510"
_ACCENT = "#44ddaa"
_DIM = "#2a8866"
_WARM = "#f0c060"
_LED_BG = "#050f0a"
_BTN_BG = "#0d1f18"
_BTN_BORDER = "#1a3a2a"


# ── Logo ─────────────────────────────────────────────────────────────────────

class _GlobePinLogo(QWidget):
    """Globe outline with plug prongs — foreign exchange / world power."""

    def __init__(self, parent=None):
        super().__init__(parent)
        bind_fixed_size(self, 28, 28)

    def paintEvent(self, event):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(_ACCENT), 1.6)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        cx, cy, r = 14.0, 11.0, 9.0

        # Globe circle
        p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        # Longitude arc (vertical ellipse)
        path = QPainterPath()
        path.moveTo(cx, cy - r)
        path.cubicTo(cx + 5, cy - 4, cx + 5, cy + 4, cx, cy + r)
        path.cubicTo(cx - 5, cy + 4, cx - 5, cy - 4, cx, cy - r)
        p.drawPath(path)

        # Latitude arcs (two horizontal ellipses)
        for dy in (-3.0, 3.0):
            lat = QPainterPath()
            lat.moveTo(cx - r, cy + dy)
            lat.cubicTo(cx - 4, cy + dy - 2.5, cx + 4, cy + dy - 2.5,
                        cx + r, cy + dy)
            p.drawPath(lat)

        # Plug prongs below globe
        pen2 = QPen(QColor(_ACCENT), 2.0)
        p.setPen(pen2)
        p.drawLine(10, 22, 10, 27)
        p.drawLine(18, 22, 18, 27)
        p.end()


# ── LED plugin name display ─────────────────────────────────────────────────

class _PluginNameDisplay(QLabel):
    """Mint LED-style display showing the loaded plugin name."""

    def __init__(self, text: str = "— NO PLUGIN —", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bind_fixed_height(self, 20)
        self.setStyleSheet(
            f"background: {_LED_BG}; color: {_ACCENT}; font-family: 'Consolas';"
            "font-size: 11px; font-weight: bold; border: 1px solid #1a3a2a;"
            "border-radius: 2px; padding: 0 4px;"
        )


# ── Styled push-button ──────────────────────────────────────────────────────

def _make_btn(text: str, color: str = _ACCENT) -> QPushButton:
    btn = QPushButton(text)
    bind_fixed_height(btn, 20)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background: {_BTN_BG}; color: {color}; font-size: 9px;"
        f"  font-weight: bold; border: 1px solid {_BTN_BORDER}; border-radius: 2px;"
        f"  padding: 0 8px; }}"
        f"QPushButton:hover {{ background: #153025; border-color: {color}; }}"
    )
    return btn


def _knob_col(label: str, knob: RotaryKnob, color: str = _ACCENT) -> QVBoxLayout:
    """Wrap a knob with a label below it."""
    col = QVBoxLayout()
    col.setSpacing(0)
    col.setContentsMargins(0, 0, 0, 0)
    col.addWidget(knob, alignment=Qt.AlignmentFlag.AlignCenter)
    lbl = QLabel(label)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        f"color: {color}; font-size: 7px; font-weight: bold;"
        "background: transparent;"
    )
    bind_fixed_height(lbl, 10)
    col.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
    return col, lbl


# ── Rack module ──────────────────────────────────────────────────────────────

class ForeignExchangeModule(RackModuleBase):
    """2U rack module hosting a single external VST3/AU plugin."""

    effect_type = "stowaway"          # class-level fallback; overridden per instance
    _enabled_param = "stowaway_enabled"

    def __init__(self, slot_index: int = 0, parent=None):
        super().__init__(unit_height=2, faceplate_color=_FACE,
                         title="FOREIGN EXCHANGE", parent=parent)
        self._slot_index = slot_index
        self.effect_type = f"stowaway_{slot_index}"
        self._plugin_name = ""
        self._plugin_format = ""
        self._quick_map_params: list = [None, None, None, None]
        self._build_faceplate()

    @property
    def slot_index(self) -> int:
        return self._slot_index

    # ── Faceplate layout ─────────────────────────────────────────────────

    def _build_faceplate(self):
        layout = self._faceplate_layout  # already created by RackModuleBase

        # Row 1: logo + title + format badge
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self._logo = _GlobePinLogo()
        row1.addWidget(self._logo)
        title_lbl = QLabel("FOREIGN EXCHANGE")
        title_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 10px; font-weight: bold;"
            f" font-family: Consolas; background: transparent;"
        )
        row1.addWidget(title_lbl)
        row1.addStretch()
        self._fmt_badge = QLabel("VST3")
        self._fmt_badge.setStyleSheet(
            f"color: {_DIM}; font-size: 8px; font-weight: bold;"
            f" border: 1px solid {_DIM}; border-radius: 2px; padding: 0 3px;"
            f" background: transparent;"
        )
        row1.addWidget(self._fmt_badge)
        layout.addLayout(row1)

        # Row 2: LED plugin name display
        self._name_display = _PluginNameDisplay()
        layout.addWidget(self._name_display)

        # Row 3: LOAD and EDIT buttons
        row3 = QHBoxLayout()
        row3.setSpacing(4)
        self._load_btn = _make_btn("LOAD")
        self._edit_btn = _make_btn("EDIT", _WARM)
        self._load_btn.clicked.connect(self._on_load_clicked)
        self._edit_btn.clicked.connect(self._on_edit_clicked)
        row3.addWidget(self._load_btn)
        row3.addStretch()
        row3.addWidget(self._edit_btn)
        layout.addLayout(row3)

        # Row 4: Wet/Dry + P1–P4 quick-map knobs
        row4 = QHBoxLayout()
        row4.setSpacing(2)
        self._wet_dry_knob = RotaryKnob(color=_ACCENT)
        self._wet_dry_knob.set_value(1.0)
        self._wet_dry_knob.value_changed.connect(
            lambda v: self.param_changed.emit("wet_dry", (self._slot_index, v))
        )
        wd_col, _ = _knob_col("WET/DRY", self._wet_dry_knob, _ACCENT)
        row4.addLayout(wd_col)

        self._quick_knobs: list[RotaryKnob] = []
        self._quick_labels: list[QLabel] = []
        for i in range(4):
            knob = RotaryKnob(color=_WARM)
            knob.set_value(0.5)
            idx = i
            knob.value_changed.connect(
                lambda v, _i=idx: self.param_changed.emit(f"quick_{_i}", (self._slot_index, v))
            )
            knob.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            knob.customContextMenuRequested.connect(
                lambda pos, _i=idx, _k=knob: self._show_assign_menu(_i, _k)
            )
            self._quick_knobs.append(knob)
            col, lbl = _knob_col(f"P{i+1}", knob, _WARM)
            self._quick_labels.append(lbl)
            row4.addLayout(col)

        layout.addLayout(row4)

    # ── Actions ──────────────────────────────────────────────────────────

    def _on_load_clicked(self):
        self.param_changed.emit("load_plugin", self._slot_index)

    def _on_edit_clicked(self):
        self.param_changed.emit("open_editor", self._slot_index)

    def _show_assign_menu(self, knob_idx: int, knob: RotaryKnob):
        """Right-click on quick knob → assign a plugin parameter."""
        self.param_changed.emit("assign_quick_param", (knob_idx, self._slot_index))

    # ── State ────────────────────────────────────────────────────────────

    def set_state(self, state):
        self._plugin_name = ""
        self._plugin_format = state.plugin_format
        self._quick_map_params = list(state.quick_map_params)
        self._wet_dry_knob.blockSignals(True)
        self._wet_dry_knob.set_value(state.wet_dry)
        self._wet_dry_knob.blockSignals(False)
        self._update_format_badge(state.plugin_format)
        self.set_power(state.enabled, emit=False)
        if state.plugin_path:
            from pathlib import Path
            self._plugin_name = Path(state.plugin_path).stem
            self._name_display.setText(self._plugin_name)
        else:
            self._name_display.setText("— NO PLUGIN —")
        # Update quick-knob labels
        for i, lbl in enumerate(self._quick_labels):
            pname = self._quick_map_params[i] if i < len(self._quick_map_params) else None
            lbl.setText(pname[:8] if pname else f"P{i+1}")

    def set_plugin_loaded(self, name: str, fmt: str):
        """Called when a plugin finishes loading."""
        self._plugin_name = name
        self._plugin_format = fmt
        self._name_display.setText(name)
        self._update_format_badge(fmt)

    def set_loading(self):
        self._name_display.setText("LOADING…")

    def _update_format_badge(self, fmt: str):
        self._fmt_badge.setText(fmt.upper())

    def set_quick_knob_label(self, knob_idx: int, label: str):
        if 0 <= knob_idx < len(self._quick_labels):
            self._quick_labels[knob_idx].setText(label[:8] if label else f"P{knob_idx+1}")

    # ── Power override (include slot_index in value) ─────────────────

    def set_power(self, on: bool, emit: bool = True):
        if self._power_on == on:
            return
        self._power_on = on
        self._faceplate.setEnabled(on)
        self._right_ear.update()
        if emit:
            self.param_changed.emit(self._enabled_param, (self._slot_index, on))

    # ── MIDI mapping helpers ─────────────────────────────────────────────

    @property
    def power_widget(self):
        return self._right_ear

    @property
    def wet_dry_widget(self):
        return self._wet_dry_knob

    def get_quick_knob(self, idx: int):
        if 0 <= idx < len(self._quick_knobs):
            return self._quick_knobs[idx]
        return None
