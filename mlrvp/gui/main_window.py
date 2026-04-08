"""Main window — assembles all views into the mlrVp interface."""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QStatusBar, QMenuBar,
    QMenu, QMessageBox, QFileDialog, QDialog, QSizePolicy,
    QTextBrowser
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QAction, QKeySequence, QIcon

from mlrvp.gui.views.channel_view import ChannelView
from mlrvp.gui.views.mixer_view import MixerView
from mlrvp.gui.views.effects_view import EffectsView
from mlrvp.gui.views.transport_view import TransportView
from mlrvp.gui.views.setup_view import SetupView
from mlrvp.gui.views.tools_view import ToolsView
from mlrvp.gui.views.master_filter_view import MasterFilterView
from mlrvp.gui.widgets.virtual_grid import VirtualGridWidget
from mlrvp.gui.widgets.fader import ControlDisplaySettings
from mlrvp.gui.styles import get_stylesheet, BG_BLACK


_HOWTO_HTML = """
<style>
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px; color: #ddd; background: #1a1a1a; }
h1 { color: #e6b84d; font-size: 20px; }
h2 { color: #4ea8d9; font-size: 15px; margin-top: 18px; border-bottom: 1px solid #333; padding-bottom: 4px; }
h3 { color: #ccc; font-size: 13px; margin-top: 12px; }
code { background: #2a2a2a; color: #e6b84d; padding: 1px 4px; }
ul { margin-left: 16px; }
li { margin-bottom: 4px; }
td, th { padding: 2px 8px; }
</style>

<h1>mlrVp — User Guide</h1>

<h2>Overview</h2>
<p>mlrVp is a grid-based sample looping instrument inspired by the original mlrV2.2 for monome.
Load audio, trigger and loop samples from a grid controller (or the on-screen virtual grid),
apply modular effects, and build setlists for live performance.</p>

<h2>Getting Started</h2>
<ol>
<li>Open <b>Setup → Audio</b> and select your output device, then click <b>Start Engine</b>.</li>
<li><b>Drag a WAV/AIFF/FLAC/OGG file</b> onto any channel row's waveform area to load it.</li>
<li><b>Set BPM</b> in the transport bar to match your material.</li>
<li>Click a cell on the <b>virtual grid</b> (or press a hardware grid button) to trigger playback.</li>
<li>Hit <b>SAVE</b> in the transport to store the current state as a preset.</li>
</ol>

<h2>Channel Strips (Left Panel)</h2>
<ul>
<li>Each row is one sample channel. The waveform shows playback position, active selection (bright), out-of-loop content (dim), and grid column dividers.</li>
<li><b>Drag a file</b> onto the waveform to load it. Dragging onto a <i>playing</i> row stages the new sample — the old audio keeps looping until the next trigger.</li>
<li><b>Mode</b> (LOOP / PTCH / RVRS / SHOT / SLCE / SQNC / STEP, alphabetical) — Loop: continuous loop; Pitch: each grid column plays at a different pitch based on the selected scale map; Reverse: reverse loop; Shot: one-shot to end; Slice: hold a button to play, hold two to latch a sub-loop; Sequence: step sequencer — toggle grid buttons to define active steps, the sample is divided into equal slices and each active step triggers its slice in sync with the clock; <b>Step</b>: press a button to play exactly the one slice it covers, once then stop — great for triggering individual hits or phrases without looping.</li>
<li><b>Group</b> (G1–G4) — only one row per group plays at a time. Triggering a new row in the same group auto-stops the current one.</li>
<li><b>Speed</b> spinner — playback rate (0.00–8.00×). <b>−½</b> halves, <b>+2×</b> doubles. Snap Speed to Rate (Setup → Grid) rounds to the nearest ½×/2× step on load.</li>
<li><b>Pitch</b> spinner — transpose in semitones (−24 to +24 st). Uses time-stretch pitch shifting so playback speed is unaffected. Computed in the background; audio switches seamlessly once ready.</li>
<li><b>Pitch mode controls</b> (visible in PTCH mode only):<br>
  &nbsp;&nbsp;• <b>PITCH</b> spinner — root offset in semitones (−24 to +24 st), shifts all scale pitches up or down.<br>
  &nbsp;&nbsp;• <b>SCALE</b> selector — choose from Major, Minor, Pentatonic, Chord, or Chromatic scale maps (defines the pitch per grid column).<br>
  &nbsp;&nbsp;• <b>HOLD</b> button — when active, the sample loops continuously on each pitch trigger instead of playing as a one-shot.</li>
</ul>

<h3>Pitch Scale Maps</h3>
<p>The SCALE selector determines which semitone intervals are assigned across the grid columns. All values are relative to the root (0 = unison). On an 8-column grid the first 8 entries are used; on 16 columns all 16 are used.</p>
<table>
<tr><th>Scale</th><th>Columns 1–8</th><th>Columns 9–16</th><th>Character</th></tr>
<tr><td><b>MAJOR</b></td><td>0, 2, 4, 5, 7, 9, 11, 12</td><td>14, 16, 17, 19, 21, 23, 24, 26</td><td>Two octaves of the major scale. Bright and familiar.</td></tr>
<tr><td><b>MINOR</b></td><td>0, 2, 3, 5, 7, 8, 10, 12</td><td>14, 15, 17, 19, 20, 22, 24, 26</td><td>Natural minor (Aeolian). Darker, moody tonality.</td></tr>
<tr><td><b>PENTA</b></td><td>0, 2, 4, 7, 9, 12, 14, 16</td><td>19, 21, 24, 26, 28, 31, 33, 36</td><td>Major pentatonic spanning ~3 octaves. No dissonance.</td></tr>
<tr><td><b>CHORD</b></td><td>0, 1, 2, 3, 5, 7, 9, 10</td><td>12, 13, 14, 15, 17, 19, 21, 22</td><td>Dense cluster spreading into wider intervals. Good for chord voicings.</td></tr>
<tr><td><b>CHROM</b></td><td>0, 1, 2, 3, 4, 5, 6, 7</td><td>8, 9, 10, 11, 12, 13, 14, 15</td><td>Every semitone in order. Maximum pitch resolution.</td></tr>
</table>
<p>The <b>root offset</b> (PITCH spinner, −24 to +24 st) shifts every column’s pitch by a fixed amount, transposing the entire keyboard.</p>

<ul>
<li><b>Step</b> spinner — number of equal slices (1–32) the sample is divided into for grid triggering. Locked to grid column count in SEQ mode.</li>
<li><b>Gain</b> fader sets per-channel volume. Drag the waveform's start/end handles to set the active playback/loop region.</li>
<li><b>MUTE</b> silences the channel without stopping it. <b>STOP</b> immediately ends playback.</li>
</ul>

<h2>Mixer (Right Panel → Mixer Tab)</h2>
<ul>
<li><b>G1–G4 faders</b> — volume per group. <b>Send fader</b> — wet/dry amount sent to the FX bus.</li>
<li><b>FX route</b> (OFF / FX1 / FX2) — choose which effect bus the group feeds.</li>
<li><b>AUX</b> — aux send bus volume, mute, and FX route.</li>
<li><b>Master fader</b> — final output level with stereo VU meter.</li>
<li><b>MUTE</b> and <b>STOP</b> per group. Transport bar <b>G1–G4</b> buttons are group stops; <b>ALL</b> stops everything.</li>
</ul>

<h2>Effects — FX Rack (Right Panel → Effects Tab)</h2>
<p>The Effects tab contains a modular <b>FX Rack</b> with three independent effect groups: <b>FX GROUP A</b>, <b>FX GROUP B</b>, and <b>FX GROUP M</b> (Master).
Click the backlit tab buttons to switch between them. Each group can hold up to 8 modules (20 total across all three groups).</p>

<h3>Adding / Removing Modules</h3>
<ul>
<li>Click <b>＋ ADD EFFECT</b> at the bottom of a rack. The menu shows all 20 modules with their effect type in parentheses — e.g. <i>Byte Me (Bit Crusher)</i>.</li>
<li>Each module's <b>right rack ear</b> has two icons:<br>
  &nbsp;&nbsp;• <b>Power button</b> (circle icon, top) — green = active, grey = bypassed. Click to toggle.<br>
  &nbsp;&nbsp;• <b>Remove ✕</b> (below power) — permanently removes the module.</li>
<li>Drag modules up/down to reorder them in the signal chain.</li>
</ul>

<h3>Available Modules</h3>
<ul>
<li><b>Déjà Vu Delay</b> — stereo digital delay, tempo-synced or free ms, feedback envelope, LP/HP filter</li>
<li><b>Rewind</b> — tape delay with wow/flutter, saturation, tone, sync</li>
<li><b>Band Practice</b> — 5-band multiband EQ</li>
<li><b>The Ladder</b> — 24 dB/oct resonant Moog-style ladder filter</li>
<li><b>Snake Charmer</b> — phaser | <b>Parrot Talk</b> — chorus | <b>Jet Stream</b> — flanger</li>
<li><b>Byte Me</b> — bit crusher with bit depth dial, sample rate reduction, and mix</li>
<li><b>Hot Snake</b> — tube overdrive with drive, tone, bias, and mix; glowing tube vent tracks drive level</li>
<li><b>Spring Fling</b> — plate/spring reverb with decay, pre-delay, damping, and mix</li>
<li><b>Echo Chamber</b> — hall/room reverb with room size, decay, diffusion, mix, and room type selector (jog wheel)</li>
<li><b>Reel Deal</b> — studio reel-to-reel tape emulation with saturation, flutter, bias, tone, tape speed selector</li>
<li><b>Mixtape</b> — cassette tape character: saturation, hiss, punch, warp, tone, mix</li>
<li><b>Tracking Shot</b> — VHS tape degradation: wobble, dropout, worn-tape character, noise, mix</li>
<li><b>Wax Poetic</b> — vinyl record character: crackle, pops, warmth, groove wear, mix</li>
<li><b>Thrust Issues</b> — sidechain-capable compressor with THRUST HPF circuit; threshold, ratio, attack, release, makeup gain, sidechain HPF frequency, SC source selector</li>
<li><b>Group Hug</b> — SSL G-Bus style glue compressor: threshold, ratio (2/4/10:1), attack (stepped ms), release (stepped), makeup gain, switchable sidechain HPF</li>
<li><b>Twin Peaks</b> — dual VU meter (VU / PEAK / RMS mode selector); reads per-FX-bus signal</li>
<li><b>CRT:// Scope</b> — oscilloscope; reads per-FX-bus waveform</li>
<li><b>Ether Drift</b> — bipolar infinite phaser. RATE sets sweep speed, DEPTH sets sweep range, FDBK adds resonance, SPREAD widens the stereo image, MIX blends dry/wet. SYNC locks both sweep directions to the same rate; ROUTE selects BOTH chains, RISE ↑ only, or FALL ↓ only.</li>
</ul>

<h3>FX Routing</h3>
<ul>
<li>Each mixer group routes to <b>OFF</b>, <b>FX1</b>, or <b>FX2</b>. Rack A processes FX1 signal; Rack B processes FX2. Rack M (Master) sits after both FX buses and processes the full mixed output before the master fader — ideal for glue compression, EQ, and metering.</li>
<li>The <b>Master Filter</b> (HP/LP with resonance and response curve) sits above the mixer and applies to the master output.</li>
</ul>

<h3>MIDI MAP Mode</h3>
<p>Click <b>MIDI MAP</b> in the FX Rack header to highlight all mappable controls (blue border).
Click any highlighted control then move a hardware knob/fader to create a mapping.
This is the same as Setup → MIDI → Learn — both stay in sync.</p>

<h2>Tools (Right Panel → Tools Tab)</h2>

<h3>Record</h3>
<p>Select a target <b>Channel</b> (1–16), input device, and length in bars (1–128). Enable <b>Loop recording</b> to overdub continuously or <b>Input thru</b> to monitor while armed.
Click <b>ARM</b> and recording starts on the next beat, writing directly into the selected channel's sample buffer.</p>

<h3>Sample Slicer</h3>
<ol>
<li><b>Drag an audio file</b> onto the waveform (or use the file dropdown).</li>
<li>Select a <b>bar count</b> (1, 2, 4, 8, 16) and optionally enter a BPM (or leave on auto-detect).</li>
<li>Click <b>AUTO</b> with <b>FIT</b> checked (default): start snaps to the nearest transient, end snaps to the nearest transient near the ideal bar boundary, both aligned to zero-crossings.</li>
<li>Click <b>SLICE + SAVE</b>: the region is resampled to the exact sample count for the chosen bar length and saved to <code>data/slices/</code>. Every slice at the same BPM and bar count will be exactly the same duration — perfect for seamless looping at 1.0× speed.</li>
<li>Uncheck <b>FIT</b> for classic mode: start snaps to a transient, end is calculated from BPM math only.</li>
<li>The <b>SLICES</b> library panel at the bottom lists all saved slices. Drag any entry onto a channel waveform to load it, or onto the slicer waveform to open it for editing. Click <b>✕</b> to delete (confirmation required).</li>
</ol>

<h2>Transport Bar</h2>
<ul>
<li><b>BPM</b> — type a value or use the spinbox arrows (20–300 BPM).</li>
<li><b>SYNC</b> — auto-detects BPM from the loaded samples and updates the tempo spinner.</li>
<li><b>TAP</b> — tap repeatedly to set BPM by feel. Shows current beat position (e.g. <code>2.3</code>) when idle.</li>
<li><b>Quantize</b> — Off, 1/4, 1/8, 1/16, Bar. Controls when grid triggers fire.</li>
<li><b>P1–P4 pattern recorders</b> — click to cycle idle → recording → playing. Click <b>−</b> / <b>+</b> arrows to change bar count freely between 1 and 32 bars (defaults: 4, 8, 16, 32). Hold the label 3 seconds to erase the pattern.</li>
<li><b>G1–G4 GROUP STOP</b> buttons — stop all channels in that group. <b>ALL</b> stops all channels.</li>
</ul>

<h2>Presets and Setlists</h2>
<ul>
<li>Type a name in the <b>PRESET</b> box and click <b>SAVE</b> (flashes green on success).</li>
<li>Click <b>◀ / ▶</b> to step through the current setlist. Click <b>NEW</b> to clear everything.</li>
<li>Click the <b>⚙</b> gear button to open the <b>Setlist Editor</b>: reorder presets by dragging, add from the library, rename, or delete. Switch between named setlists from the dropdown.</li>
<li>Presets store the full state: channels, samples, effects, mixer, MIDI mappings, routing.</li>
</ul>

<h2>Setup (Right Panel → Setup Tab)</h2>
<ul>
<li><b>Audio tab</b> — select input/output devices, start/stop the engine.</li>
<li><b>Grid tab</b> — connect Launchpad or monome, set active rows and columns, toggle virtual grid, enable tilt mapping.</li>
<li><b>MIDI tab</b> — add MIDI input controllers, click <b>Learn</b> to enter mapping mode. Each mapping row has Min/Max range and a Toggle checkbox. Tilt axis assignments appear here when a monome with accelerometer is connected.</li>
</ul>

<h2>Keyboard Shortcuts</h2>
<ul>
<li><code>Escape</code> — exit MIDI learn mode</li>
<li><code>Ctrl+Shift+S</code> — stop all audio</li>
<li><code>Ctrl+O</code> — open preset file</li>
<li><code>Ctrl+S</code> — save preset file</li>
<li><code>Ctrl+Q</code> — quit</li>
</ul>

<h2>View Menu</h2>
<ul>
<li><b>Show Virtual Grid</b> — opens the on-screen grid controller in a separate moveable window.</li>
<li><b>UI Scale</b> — resize the entire interface (50%–200%) to suit your monitor. The selected scale is saved and restored on next launch.</li>
<li><b>Lock Control Displays</b> — keeps the value overlay permanently visible on every knob and fader. Useful when you want a live read of all parameters at a glance. Off by default.</li>
<li><b>Disable Control Displays</b> — hides all value overlays entirely, including any you have latched on. Turning this off restores normal behaviour. Off by default.</li>
</ul>

<h2>Control Value Displays</h2>
<p>Every knob and fader in mlrVp shows a floating value overlay when you interact with it:</p>
<ul>
<li><b>Left-click and drag</b> — overlay appears while you hold and drag, then fades after 1.2 seconds on release.</li>
<li><b>MIDI CC change</b> — the knob or fader position updates visually and the value flashes for 1.2 seconds.</li>
<li><b>Right-click</b> — toggles a per-control latch: the overlay stays on permanently until you right-click again to dismiss it. Useful for monitoring a single parameter during a performance.</li>
<li>Use <b>View → Lock Control Displays</b> to latch all controls at once, or <b>View → Disable Control Displays</b> to turn them all off.</li>
</ul>

<h2>Tips</h2>
<ul>
<li>Use FIT mode in the slicer to chop a track into perfectly looping 2-bar or 4-bar segments — they all land on the grid at 1.0× speed.</li>
<li>Drag a sample onto a <i>playing</i> row to stage it: the old audio keeps looping until you press a grid button on that row.</li>
<li>Power-bypass an FX module by clicking the green circle icon on its right rack ear — keeps the knob settings intact without removing the module.</li>
<li>The Twin Peaks VU meter has a mode selector: VU (ballistic needle), PEAK (fast attack/slow decay), or RMS (power average).</li>
<li>Both MIDI MAP buttons (FX Rack header and Setup → MIDI → Learn) are the same mode and stay in sync.</li>
</ul>
"""


class VirtualGridWindow(QDialog):
    """Pop-out window for the virtual grid controller."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("mlrVp — Virtual Grid")
        self.setStyleSheet(f"background: {BG_BLACK};")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._grid = VirtualGridWidget(rows=8, columns=8)
        layout.addWidget(self._grid)
        self._update_size()

    @property
    def grid(self) -> VirtualGridWidget:
        return self._grid

    def set_grid_dimensions(self, rows: int, columns: int):
        self._grid.set_dimensions(rows, columns)
        self._update_size()

    def _update_size(self):
        bs = self._grid.button_size
        sp = self._grid.button_spacing
        w = self._grid.columns * (bs + sp) + sp + 8
        h = self._grid.rows * (bs + sp) + sp + 8
        self.setFixedSize(w, h)

    def closeEvent(self, event):
        # Hide instead of destroying so it can be re-opened
        self.hide()
        event.ignore()


class MainWindow(QMainWindow):
    """Main application window matching the mlrV2 layout."""

    # Signals for app controller
    quit_requested = Signal()
    preset_file_open_requested = Signal(str)
    preset_file_save_requested = Signal(str)
    stop_all_requested = Signal()
    restart_requested = Signal()
    scale_change_requested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("mlrVp")
        self.setMinimumSize(1300, 600)

        # Apply dark theme
        self.setStyleSheet(get_stylesheet())

        # Virtual grid pop-out window (created but hidden)
        self._grid_window = VirtualGridWindow(self)
        self._has_hardware_grid = False

        # --- Menu Bar ---
        self._build_menu_bar()

        # --- Central Widget ---
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Transport bar
        self._transport = TransportView()
        main_layout.addWidget(self._transport)

        # Main content splitter (channels | side panel)
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Channel strips
        self._channel_view = ChannelView(num_rows=8)
        self._main_splitter.addWidget(self._channel_view)

        # Right: Tabbed side panel (Effects, Tools, Setup)
        self._side_tabs = QTabWidget()
        self._side_tabs.setDocumentMode(True)

        self._effects_view = EffectsView()
        self._side_tabs.addTab(self._effects_view, "Effects")

        self._tools_view = ToolsView()
        self._side_tabs.addTab(self._tools_view, "Tools")

        self._setup_view = SetupView()
        self._side_tabs.addTab(self._setup_view, "Setup")

        self._side_tabs.setMinimumWidth(380)
        self._side_tabs.setMaximumWidth(560)

        # Right panel: plain layout — tabs on top, master filter + mixer pinned at bottom
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Tabs fill available space above the fixed bottom section
        right_layout.addWidget(self._side_tabs, stretch=1)

        # Master filter + mixer pinned at bottom
        self._master_filter_view = MasterFilterView()
        self._master_filter_view.setFixedHeight(52)
        right_layout.addWidget(self._master_filter_view)

        self._mixer_view = MixerView()
        right_layout.addWidget(self._mixer_view)

        right_panel.setMinimumWidth(380)
        right_panel.setMaximumWidth(560)
        self._main_splitter.addWidget(right_panel)

        # Set splitter proportions (channels get more space)
        self._main_splitter.setSizes([880, 520])
        main_layout.addWidget(self._main_splitter, stretch=1)

        # --- Status Bar ---
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("mlrVp — Ready")

        # --- UI refresh timer ---
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh)
        self._refresh_timer.start(33)  # ~30fps UI update

        # Auto-size to fit content
        self._auto_resize()

    def _auto_resize(self):
        """Resize window to fit all 8 rows without scrolling."""
        row_count = 8
        row_height = 80  # controls row ~24 + waveform ~50 + spacing
        transport_h = 44
        status_h = 22
        margins = 12
        needed_h = transport_h + (row_count * row_height) + status_h + margins
        needed_h = max(needed_h, 720)
        self.resize(1500, min(needed_h, 1100))

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_mixer_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_mixer_height()

    def _sync_mixer_height(self):
        """Set mixer to exactly 3 channel rows tall."""
        try:
            # Measure actual row height from the channel strips
            s0 = self._channel_view.get_strip(0)
            s1 = self._channel_view.get_strip(1)
            row_h = s1.y() - s0.y()  # pixel distance between row starts = 1 row
            if row_h > 0:
                self._mixer_view.setFixedHeight(row_h * 3)
        except Exception:
            pass

    def _build_menu_bar(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        load_preset_action = QAction("&Open Preset File...", self)
        load_preset_action.setShortcut(QKeySequence.StandardKey.Open)
        load_preset_action.triggered.connect(self._on_open_preset)
        file_menu.addAction(load_preset_action)

        save_preset_action = QAction("&Save Preset File...", self)
        save_preset_action.setShortcut(QKeySequence.StandardKey.Save)
        save_preset_action.triggered.connect(self._on_save_preset)
        file_menu.addAction(save_preset_action)

        file_menu.addSeparator()

        restart_action = QAction("&Restart", self)
        restart_action.triggered.connect(self._on_restart)
        file_menu.addAction(restart_action)

        stop_all_action = QAction("Stop &All Audio", self)
        stop_all_action.setShortcut("Ctrl+Shift+S")
        stop_all_action.triggered.connect(self._on_stop_all)
        file_menu.addAction(stop_all_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # View menu
        view_menu = menubar.addMenu("&View")

        self._toggle_grid_action = QAction("Show &Virtual Grid", self)
        self._toggle_grid_action.setCheckable(True)
        self._toggle_grid_action.setChecked(False)
        self._toggle_grid_action.toggled.connect(self._on_toggle_grid)
        view_menu.addAction(self._toggle_grid_action)

        view_menu.addSeparator()
        scale_menu = view_menu.addMenu("UI &Scale")
        from PySide6.QtGui import QActionGroup
        from mlrvp.config import get_ui_scale
        current_scale = get_ui_scale()
        scale_group = QActionGroup(self)
        for pct in (50, 75, 100, 125, 150, 175, 200):
            act = QAction(f"{pct}%", self)
            act.setCheckable(True)
            act.setChecked(abs(pct / 100.0 - current_scale) < 0.01)
            factor = pct / 100.0
            act.triggered.connect(lambda checked, f=factor: self._on_scale(f))
            scale_group.addAction(act)
            scale_menu.addAction(act)

        view_menu.addSeparator()

        self._lock_displays_action = QAction("&Lock Control Displays", self)
        self._lock_displays_action.setCheckable(True)
        self._lock_displays_action.setChecked(False)
        self._lock_displays_action.setToolTip(
            "Keep value overlays visible on all knobs and faders permanently")
        self._lock_displays_action.toggled.connect(
            lambda on: setattr(ControlDisplaySettings, 'locked', on))
        view_menu.addAction(self._lock_displays_action)

        self._disable_displays_action = QAction("&Disable Control Displays", self)
        self._disable_displays_action.setCheckable(True)
        self._disable_displays_action.setChecked(False)
        self._disable_displays_action.setToolTip(
            "Never show value overlays on knobs and faders")
        self._disable_displays_action.toggled.connect(self._on_disable_displays)
        view_menu.addAction(self._disable_displays_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        howto_action = QAction("&How to Use", self)
        howto_action.triggered.connect(self._on_howto)
        help_menu.addAction(howto_action)

        about_action = QAction("&About mlrVp", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _on_disable_displays(self, on: bool):
        ControlDisplaySettings.disabled = on
        # Force-repaint every custom control so visible overlays clear immediately
        for w in self.findChildren(QWidget):
            if hasattr(w, '_popup_visible'):
                if on:
                    # Also stop any pending auto-hide timers to keep state clean
                    if hasattr(w, '_popup_timer'):
                        w._popup_timer.stop()
                w.update()

    def _on_toggle_grid(self, visible: bool):
        if visible:
            self._grid_window.show()
            self._grid_window.raise_()
        else:
            self._grid_window.hide()

    def _on_open_preset(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Preset File", "",
            "mlrVp Presets (*.json);;All Files (*)"
        )
        if path:
            self.preset_file_open_requested.emit(path)

    def _on_save_preset(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Preset File", "",
            "mlrVp Presets (*.json);;All Files (*)"
        )
        if path:
            self.preset_file_save_requested.emit(path)

    def _on_stop_all(self):
        self.stop_all_requested.emit()

    def _on_restart(self):
        self.restart_requested.emit()

    def _on_scale(self, factor: float):
        from mlrvp.config import set_ui_scale
        set_ui_scale(factor)
        self.scale_change_requested.emit(factor)

    def _on_about(self):
        from mlrvp import __version__ as _ver
        QMessageBox.about(
            self, "About mlrVp",
            "<h2>mlrVp</h2>"
            "<p>A modern Python recreation of mlrV2.2</p>"
            "<p>Based on the original Max/MSP mlrV by<br>"
            "trent gill &amp; friends at monome.org</p>"
            "<p>Rebuilt with PySide6, sounddevice, and numpy.</p>"
            f"<p><b>Version {_ver}</b></p>"
        )

    def _on_howto(self):
        """Show a scrollable How-to-Use guide popup (non-modal — drag it around freely)."""
        if hasattr(self, '_howto_dialog') and self._howto_dialog is not None:
            self._howto_dialog.raise_()
            self._howto_dialog.activateWindow()
            return
        dlg = QDialog(self, Qt.WindowType.Window)
        dlg.setWindowTitle("mlrVp — How to Use")
        dlg.resize(700, 580)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.destroyed.connect(lambda: setattr(self, '_howto_dialog', None))
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(8, 8, 8, 8)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(_HOWTO_HTML)
        layout.addWidget(browser)

        self._howto_dialog = dlg
        dlg.show()

    def _on_refresh(self):
        """Periodic UI update — called by the app controller to push levels."""
        pass

    # --- Public accessors for app controller ---

    @property
    def transport(self) -> TransportView:
        return self._transport

    @property
    def channel_view(self) -> ChannelView:
        return self._channel_view

    @property
    def mixer_view(self) -> MixerView:
        return self._mixer_view

    @property
    def master_filter_view(self) -> MasterFilterView:
        return self._master_filter_view

    @property
    def effects_view(self) -> EffectsView:
        return self._effects_view

    @property
    def tools_view(self) -> ToolsView:
        return self._tools_view

    @property
    def setup_view(self) -> SetupView:
        return self._setup_view

    @property
    def virtual_grid(self) -> VirtualGridWidget:
        return self._grid_window.grid

    @property
    def grid_window(self) -> VirtualGridWindow:
        return self._grid_window

    def set_status(self, msg: str):
        self._status_bar.showMessage(msg)

    def set_virtual_grid_visible(self, visible: bool):
        if visible:
            self._grid_window.show()
            self._grid_window.raise_()
        else:
            self._grid_window.hide()
        self._toggle_grid_action.blockSignals(True)
        self._toggle_grid_action.setChecked(visible)
        self._toggle_grid_action.blockSignals(False)

    def set_hardware_grid_connected(self, connected: bool):
        """Track whether a hardware grid is connected.
        If no hardware grid, auto-show the virtual grid popout."""
        self._has_hardware_grid = connected
        if not connected:
            self.set_virtual_grid_visible(True)

    def set_grid_dimensions(self, rows: int, columns: int):
        """Update virtual grid and auto-resize the window for the row count."""
        self._grid_window.set_grid_dimensions(rows, columns)
        self._auto_resize_for_rows(rows)

    def _auto_resize_for_rows(self, row_count: int):
        """Resize window height to fit the given number of rows + mixer."""
        row_height = 100
        transport_h = 52
        mixer_h = 220
        status_h = 26
        margins = 24
        needed_h = transport_h + (row_count * row_height) + mixer_h + status_h + margins
        needed_h = max(needed_h, 1100)
        self.resize(self.width(), min(needed_h, 1400))

    def closeEvent(self, event):
        self._refresh_timer.stop()
        self._grid_window.close()
        self.quit_requested.emit()
        event.accept()
