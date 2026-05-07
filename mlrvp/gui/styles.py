"""Dark theme stylesheet for mlrVp — pixel-matched to mlrV2.2 aesthetics.

All color values extracted directly from the original .maxpat JSON sources.
"""
from pathlib import Path

_RES_DIR = Path(__file__).resolve().parent.parent / "resources"
_ARROW_UP = (_RES_DIR / "arrow_up.svg").as_posix()
_ARROW_DOWN = (_RES_DIR / "arrow_down.svg").as_posix()

# ── Exact color palette from the original Max/MSP patches ──
# Main patcher background: bgcolor [0.160784, 0.160784, 0.160784, 1.0]
BG_MAIN = "#292929"
# Dark panel / card background: bgcolor [0.101961, 0.101961, 0.101961, 1.0]
BG_PANEL = "#1a1a1a"
# Black (button bg, waveform bg): [0.0, 0.0, 0.0, 1.0]
BG_BLACK = "#000000"
# Separator line: [0.34902, 0.34902, 0.34902, 1.0]
SEPARATOR = "#595959"
# Teal transparent overlay: [0.17, 0.33, 0.38, 0.0]
TEAL_OVERLAY = "#2b5461"

# Legacy aliases used across the codebase
BG_DARK = BG_MAIN
BG_MEDIUM = BG_PANEL
BG_LIGHT = "#333333"
BG_HIGHLIGHT = SEPARATOR

# ── Text colors (from textcolor fields in .maxpat) ──
TEXT_PRIMARY = "#ffffff"       # [1.0, 1.0, 1.0, 1.0]
TEXT_SECONDARY = "#c4c4c4"    # [0.768627, 0.768627, 0.768627, 1.0]
TEXT_DIM = "#dbdbdb"           # [0.858824, 0.858824, 0.858824, 1.0]

# ── Accent / highlight colors ──
# Button on-color (golden yellow): bgoncolor [0.960784, 0.85098, 0.478431, 1.0]
ACCENT_GOLD = "#f5d97a"
# Highlight blue: hltcolor [0.47451, 0.694118, 1.0]
ACCENT_BLUE = "#79b1ff"
# Legacy aliases
ACCENT = ACCENT_GOLD
ACCENT_BRIGHT = ACCENT_BLUE

# Utility colors
RED = "#cc3333"
GREEN = "#44cc44"
YELLOW = "#cccc44"
ORANGE = "#cc8844"
PURPLE = "#a769ed"     # AUX send button [0.654902, 0.411765, 0.929412, 1.0]

# FX bus colors — aligned with the effect rack tab accent colors
FX1_COLOR = "#dec261"  # GROUP 1 golden — FX Group A
FX2_COLOR = "#82e875"  # GROUP 2 green — FX Group B
FX3_COLOR = "#e88273"  # GROUP 3 salmon — FX Group C
FX4_COLOR = "#40b5cc"  # GROUP 4 teal — FX Group D

# AUX channel color
AUX_COLOR = "#888888"  # medium grey

# Logo magenta: frgb 198 0 89
LOGO_MAGENTA = "#c60059"

# ── Group colors (exact values from mixer.maxpat bpatcher args) ──
# Group 1: [0.87, 0.76, 0.38] — golden/amber
# Group 2: [0.51, 0.91, 0.46] — green
# Group 3: [0.91, 0.51, 0.45] — salmon/red
# Group 4: [0.25, 0.71, 0.8]  — teal/cyan
GROUP_COLORS = [
    "#dec261",   # Group 1 — golden/amber
    "#82e875",   # Group 2 — green
    "#e88273",   # Group 3 — salmon/red
    "#40b5cc",   # Group 4 — teal/cyan
    "#c478f0",   # Group 5 — lavender/purple
    "#f0a030",   # Group 6 — warm orange
    "#e060bf",   # Group 7 — hot pink/magenta
    "#3880f2",   # Group 8 — cobalt blue
]

# ── Grid button colors ──
GRID_LED_ON = "#dec261"
GRID_LED_DIM = "#3a3520"
GRID_LED_OFF = "#1a1a11"
GRID_BUTTON_BG = "#222218"

# ── VU meter color (monochrome gray in original) ──
# meter~ colors: all [0.258824, 0.258824, 0.258824, alpha]
VU_COLOR = "#424242"

# ── Fader colors from multislider objects ──
FADER_SLIDER = "#8e8e8e"      # slidercolor [0.556863, 0.556863, 0.556863, 1.0]
FADER_BORDER = "#636363"      # bordercolor [0.388235, 0.388235, 0.388235, 1.0]
SEND_FADER_COLOR = "#888888"  # send fader: grey to match aux theme


def get_stylesheet() -> str:
    """Build the complete application stylesheet."""
    return STYLESHEET


STYLESHEET = f"""
QMainWindow {{
    background-color: {BG_MAIN};
}}

QWidget {{
    background-color: {BG_MAIN};
    color: {TEXT_PRIMARY};
    font-family: "Helvetica Neue", "Segoe UI", "SF Pro", sans-serif;
    font-size: 11px;
}}

QLabel {{
    background: transparent;
    color: {TEXT_PRIMARY};
    padding: 1px;
}}

QLabel[class="header"] {{
    font-size: 13px;
    font-weight: bold;
    color: {ACCENT_BLUE};
}}

QLabel[class="dim"] {{
    color: {TEXT_SECONDARY};
}}

QPushButton {{
    background-color: {BG_BLACK};
    color: {TEXT_PRIMARY};
    border: 1px solid {SEPARATOR};
    border-radius: 0px;
    padding: 4px 10px;
    min-height: 20px;
    font-family: "Helvetica Neue", "Segoe UI", sans-serif;
    font-weight: bold;
    font-size: 9px;
}}

QPushButton:hover {{
    background-color: {BG_LIGHT};
    border-color: {ACCENT_BLUE};
}}

QPushButton:pressed {{
    background-color: {ACCENT_GOLD};
    color: {BG_BLACK};
}}

QPushButton:checked {{
    background-color: {ACCENT_GOLD};
    color: {BG_BLACK};
    border-color: {ACCENT_GOLD};
}}

QPushButton[class="record"] {{
    background-color: {BG_LIGHT};
}}

QPushButton[class="record"]:checked {{
    background-color: {RED};
    color: white;
}}

QPushButton[class="group0"] {{ border-left: 3px solid {GROUP_COLORS[0]}; }}
QPushButton[class="group1"] {{ border-left: 3px solid {GROUP_COLORS[1]}; }}
QPushButton[class="group2"] {{ border-left: 3px solid {GROUP_COLORS[2]}; }}
QPushButton[class="group3"] {{ border-left: 3px solid {GROUP_COLORS[3]}; }}

QSlider::groove:horizontal {{
    border: 1px solid {SEPARATOR};
    height: 6px;
    background: {BG_PANEL};
    border-radius: 0px;
}}

QSlider::handle:horizontal {{
    background: {FADER_SLIDER};
    border: 1px solid {FADER_BORDER};
    width: 14px;
    margin: -4px 0;
    border-radius: 0px;
}}

QSlider::groove:vertical {{
    border: 1px solid {SEPARATOR};
    width: 6px;
    background: {BG_PANEL};
    border-radius: 0px;
}}

QSlider::handle:vertical {{
    background: {FADER_SLIDER};
    border: 1px solid {FADER_BORDER};
    height: 14px;
    margin: 0 -4px;
    border-radius: 0px;
}}

QComboBox {{
    background-color: rgba(0, 0, 0, 64);
    color: {TEXT_PRIMARY};
    border: none;
    border-radius: 0px;
    padding: 3px 8px;
    min-height: 17px;
    font-weight: bold;
    font-size: 9px;
}}

QComboBox::drop-down {{
    border: none;
    width: 0px;
}}

QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    selection-background-color: {ACCENT_GOLD};
    selection-color: {BG_BLACK};
    border: 1px solid {SEPARATOR};
}}

QSpinBox, QDoubleSpinBox {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    border: 1px solid {SEPARATOR};
    border-radius: 0px;
    padding: 2px 6px;
    min-height: 20px;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button {{
    width: 16px;
    background: {BG_LIGHT};
    border: none;
    border-left: 1px solid {SEPARATOR};
    subcontrol-origin: border;
    subcontrol-position: top right;
}}

QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 16px;
    background: {BG_LIGHT};
    border: none;
    border-left: 1px solid {SEPARATOR};
    subcontrol-origin: border;
    subcontrol-position: bottom right;
}}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({_ARROW_UP});
    width: 8px;
    height: 5px;
}}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({_ARROW_DOWN});
    width: 8px;
    height: 5px;
}}

QLineEdit {{
    background-color: {BG_PANEL};
    color: {TEXT_PRIMARY};
    border: 1px solid {SEPARATOR};
    border-radius: 0px;
    padding: 2px 6px;
    min-height: 20px;
}}

QGroupBox {{
    border: 1px solid {SEPARATOR};
    border-radius: 0px;
    margin-top: 8px;
    padding-top: 14px;
    font-weight: bold;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {ACCENT_BLUE};
}}

QTabWidget::pane {{
    border: 1px solid #555555;
    border-top: none;
    background: {BG_MAIN};
}}

QTabBar::tab {{
    background: {BG_PANEL};
    color: {TEXT_SECONDARY};
    border: 1px solid #444444;
    border-bottom: none;
    padding: 5px 16px;
    margin-right: 2px;
    border-top-left-radius: 3px;
    border-top-right-radius: 3px;
    font-weight: bold;
    letter-spacing: 1px;
}}

QTabBar::tab:selected {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #444444, stop:0.4 #333333, stop:1 #222222);
    color: {ACCENT_GOLD};
    border-color: #666666;
    border-bottom: none;
}}

QScrollArea {{
    background-color: {BG_MAIN};
    border: none;
}}

QScrollBar:vertical {{
    background: {BG_PANEL};
    width: 8px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: {SEPARATOR};
    min-height: 20px;
    border-radius: 0px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QStatusBar {{
    background: {BG_PANEL};
    color: {TEXT_SECONDARY};
    border-top: 1px solid {SEPARATOR};
    font-size: 10px;
}}

QTabBar::tab:hover {{
    background: #2a2a2a;
    color: {TEXT_PRIMARY};
}}

QScrollBar:vertical {{
    background: {BG_MEDIUM};
    width: 10px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: {BG_HIGHLIGHT};
    min-height: 20px;
    border-radius: 5px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar:horizontal {{
    background: {BG_MEDIUM};
    height: 10px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background: {BG_HIGHLIGHT};
    min-width: 20px;
    border-radius: 5px;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QMenuBar {{
    background: {BG_MEDIUM};
    color: {TEXT_PRIMARY};
    border-bottom: 1px solid {BG_HIGHLIGHT};
    padding: 2px 0px;
    font-size: 12px;
}}

QMenuBar::item {{
    padding: 4px 10px;
}}

QMenuBar::item:selected {{
    background: {ACCENT};
    color: {BG_DARK};
}}

QMenu {{
    background: {BG_MEDIUM};
    color: {TEXT_PRIMARY};
    border: 1px solid {BG_HIGHLIGHT};
    padding: 4px 0px;
}}

QMenu::item {{
    padding: 6px 24px 6px 12px;
}}

QMenu::item:selected {{
    background: {ACCENT};
    color: {BG_DARK};
}}

QProgressBar {{
    border: 1px solid {BG_HIGHLIGHT};
    border-radius: 3px;
    background: {BG_MEDIUM};
    text-align: center;
    color: {TEXT_PRIMARY};
}}

QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 2px;
}}

QStatusBar {{
    background: {BG_MEDIUM};
    color: {TEXT_SECONDARY};
    border-top: 1px solid {BG_HIGHLIGHT};
}}

QToolTip {{
    background-color: {BG_MEDIUM};
    color: {TEXT_PRIMARY};
    border: 1px solid {ACCENT};
    padding: 4px;
}}
"""
