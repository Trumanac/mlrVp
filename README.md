# mlrVp

**A modern Python reimplementation of mlrV2 — the classic sampling instrument for grid-based music controllers.**

mlrVp is a standalone, cross-platform application that recreates the legendary mlrV sampling workflow without requiring Max/MSP. It supports monome grids, Novation Launchpads (all models), generic MIDI controllers, and can operate entirely via its built-in virtual grid.

## Downloads

[![Latest Release](https://img.shields.io/github/v/release/Trumanac/mlrVp?include_prereleases&label=latest%20release)](https://github.com/Trumanac/mlrVp/releases/latest)

| Platform | Download |
|----------|----------|
| Windows  | [mlrVp-windows.zip](https://github.com/Trumanac/mlrVp/releases/latest/download/mlrVp-windows.zip) |
| macOS    | [mlrVp-macos.zip](https://github.com/Trumanac/mlrVp/releases/latest/download/mlrVp-macos.zip) |
| Linux    | [mlrVp-linux.zip](https://github.com/Trumanac/mlrVp/releases/latest/download/mlrVp-linux.zip) |

> **[All releases & changelogs →](https://github.com/Trumanac/mlrVp/releases)**

### Installation
1. Download the zip for your platform above
2. Extract to a folder of your choice
3. Run `mlrVp.exe` (Windows), `mlrVp.app` (macOS), or `./mlrVp` (Linux)

> **macOS:** If you get a security warning, go to System Settings → Privacy & Security → Allow.
> **Linux:** You may need `chmod +x mlrVp` before running.

## Link to original sourcecode:
https://github.com/Monome-Community/mlrV2.2-serialosc-edition

## Features

### Core
- **Grid-based sample triggering**: Load audio files and trigger them from rows on a monome grid, Launchpad, any MIDI controller, or the on-screen virtual grid
- **Up to 16 rows** with independent sample playback, each synced to a master tempo
- **4 group buses** with volume/send faders, mute, solo, and FX routing (OFF / FX1 / FX2 / FX3 / FX4)
- **Group-exclusive playback**: Only one row per group can play at a time — triggering a new row automatically stops any other playing row in the same group (allows simultaneous playback across different groups)
- **Playback modes**: Loop, Pitch, Reverse, Shot (one-shot), Slice (hold-to-play with 2-button loop), SEQ (step sequencer), Step — **Step** plays the slice under the pressed button once then stops; **Pitch** plays each grid column at a different pitch using the selected scale map with **duration-preserving pitch shifting** (no speed change)
- **Per-channel controls**: Speed (halve/double + continuous), gain, attack/release envelope, start/end selection, transpose (time-stretch-free pitch shift, −24 to +24 semitones), steps (1–32 for SEQ/slice mapping), velocity, filter sweep
- **Pitch mode extras**: 5 musical scale maps (Major, Minor, Pentatonic, Chord, Chromatic), root offset (−24 to +24 st), and a **HOLD** toggle that loops the sample continuously instead of one-shot behaviour. Pitch-shifted audio is pre-computed in the background using a phase vocoder — each button plays at its scale degree pitch while keeping the original sample duration and tempo
- **Snap Speed to Rate**: When loading a sample, speed auto-snaps to the nearest ½× / 2× step of 1.0 (e.g. 0.25×, 0.5×, 1.0×, 2.0×, 4.0×, 8.0×) for clean grid-synced loops — toggle on/off in Setup → Grid
- **Live sample staging**: Dragging a new sample onto a playing row updates the waveform display immediately but keeps the old audio and grid LEDs looping — the new sample loads on the next trigger, group stop, or ALL stop
- **Waveform display** with playback cursor, selection markers, grid column dividers, active column highlight, group color coding, and two-pass dim/bright rendering (out-of-loop content shown dim, active loop region shown bright)
- **Crossfade retrigger**: Equal-power cosine crossfade (~5.8 ms) for click-free re-triggering and pitch changes
- **Background pitch scale cache**: Pitch mode pre-computes shifted audio for each scale degree asynchronously — triggers play instantly from cache with zero real-time DSP cost; cache updates automatically when scale, root, transpose, or sample changes

### Effects & Processing
- **Modular FX Rack UI**: Skeuomorphic rack with five independent effect groups (**FX 1 / FX 2 / FX 3 / FX 4 / Master**) accessible via tab buttons — add, remove, and reorder effect modules freely; each module has a **power toggle** (green/gray icon) on its right rack ear to enable or bypass the effect without removing it
- **40 rack effect modules** with unique vintage-inspired faceplates:
  - **Déjà Vu Delay** — stereo digital delay with tempo-synced beat divisions or free ms mode, feedback envelope, LP/HP feedback filters, gain
  - **Rewind** — vintage tape delay with wow/flutter, saturation, tone shaping, and sync
  - **Band Practice** — 5-band multiband EQ with sweepable frequency bands
  - **The Ladder** — 24 dB/oct resonant ladder filter with cutoff, resonance, and drive (wood-cheek faceplate)
  - **Snake Charmer** — phaser with rate, depth, feedback, and mix
  - **Parrot Talk** — chorus with rate, depth, feedback, and mix
  - **Jet Stream** — flanger with rate, depth, feedback, and mix
  - **Byte Me** — bit crusher with bit depth and sample rate reduction, mix
  - **Hot Snake** — tube overdrive with drive, tone, bias, and mix
  - **Spring Fling** — plate/spring reverb with decay, damping, pre-delay, and mix
  - **Echo Chamber** — hall/room reverb with room size, decay, diffusion, mix, and room type selector
  - **Reel Deal** — studio reel-to-reel tape with saturation, flutter, bias, tone, and tape speed selector
  - **Mixtape** — home cassette tape character with saturation, hiss, punch, warp, and tone
  - **Tracking Shot** — VHS tape degradation with wobble, dropout, worn-tape character, and noise
  - **Wax Poetic** — vinyl record character with crackle, pops, warmth, and groove wear
  - **Thrust Issues** — sidechain compressor with threshold, ratio, attack, release, makeup gain, and sidechain HPF
  - **Group Hug** — glue bus compressor with threshold, ratio, attack, release, makeup gain, and switchable sidechain HPF
  - **Twin Peaks** — dual analog VU meter with selectable **VU** (ballistic), **PEAK** (fast attack/slow decay), and **RMS** (power average) modes; reads per-FX-bus signal levels
  - **CRT:// Scope** — oscilloscope for waveform visualization; reads per-FX-bus waveform data
  - **Ether Drift** — bipolar infinite phaser for vaporwave/slushwave textures; two allpass chains (RISE ↑ sawtooth / FALL ↓ inverted) with stereo spread, SYNC lock, ROUTE selector (BOTH / UP / DOWN), rate, depth, feedback, and dry/wet mix
  - **The Velvet Rope** — noise gate with threshold, attack, hold, release, range, and key frequency sidechain filter
  - **Heartquake** — tremolo with rate, depth, stereo spread, and mix
  - **Funk Doctor** — envelope auto-wah filter (Mutron III-style) with sensitivity, frequency, depth, speed, and mix
  - **All Buttons In** — FET limiter with input drive, output, attack, release, and mix
  - **Vitamin E** — aural exciter with high-frequency tune, low-frequency tune, output level, and mix
  - **Punch Card** — transient shaper with attack boost/cut, sustain boost/cut, output, and mix
  - **The Bode Plot** — single-sideband frequency shifter with shift amount (Hz), feedback, and mix
  - **Ring Leader** — ring modulator with carrier frequency, shape, and mix
  - **Spin Cycle** — rotary speaker simulator with speed, drive, horn/drum balance, and mix
  - **Personal Space** — stereo widener with width, center, low-frequency mono crossover, and mix
  - **Wrong Lane** — dual-voice harmonizer with semitone shift, fine detune, delay, and feedback per voice, plus mix
  - **Golden Ears** — 3-band parametric EQ with low, mid, and high gain controls
  - **Table Tennis** — ping-pong stereo delay with independent left/right times, feedback, and mix
  - **Stained Glass** — shimmer reverb with decay, shimmer pitch-shift amount, damping, and mix
  - **Tin Can** — lo-fi radio emulation with noise, wobble, lo-cut/hi-cut filters, and mix
  - **Pendulum** — auto-panner with rate, depth, shape, and mix
  - **Wool Over** — fuzz with drive, tone, gate, and mix
  - **Snow Globe** — granular freeze processor with grain size, scatter, pitch spread, density, freeze toggle, and mix
  - **Gate Crasher** — gated/reverse reverb with room size, gate time, slope, pre-delay, gate/reverse mode toggle, and mix
  - **Broken Record** — stutter/glitch buffer looper with loop size, retrigger rate, chaos, smear, trigger toggle, and mix
- **Max 8 modules per rack, 40 total** across all five groups
- **4 independent FX send buses** (FX1–FX4) plus a post-mix **Master** bus; each mixer group routes independently to any bus or OFF
- **Master HP/LP filter** with resonance control and response curve display
- **Per-channel sweepable filter** (lowpass ↔ highpass)
- **MIDI MAP mode** in the FX rack — highlights all mappable controls for quick MIDI CC assignment

### Transport & Presets
- **Master clock** (20–300 BPM) with beat/bar position tracking
- **Quantized triggering**: None (immediate), 1/4, 1/8, 1/16 note, or 1 bar
- **4 pattern recorders** (4/8/16/32 bars) for capturing and looping grid gestures
- **Global preset library**: Save any number of named presets to `data/presets/` — each stores the full application state (channels, samples, effects, MIDI mappings, routing)
- **Ordered setlists**: Build setlists from the preset library — reorder, add, remove presets without affecting the library
- **Live preset recall**: Switch presets mid-performance with smart transitions — effects and mixer update instantly; playing channels keep their audio looping until retriggered
- **Editable preset names**: Type directly in the preset combo box to name new presets
- **Save feedback**: SAVE button flashes green to confirm a successful save
- **NEW button**: Clear all channels and reset to a blank state for building a fresh preset
- **Prev/Next navigation**: Step through setlist presets with arrow buttons (wraps around)
- **Setlist editor dialog**: Two-panel layout — left shows the current setlist order (drag to reorder, remove), right shows the full preset library (rename, delete)
- **File import/export**: Save and load individual preset files for sharing

### Tools
- **Live audio recording** into sample buffers with configurable length (bars + beats), loop mode, and input monitoring
- **Slice editor** with interactive waveform, automatic transient detection, draggable markers, zoom/pan, and fixed-width selection mode
- **FIT mode slicer**: Snaps both start and end of a slice to transients, then resamples on save to the exact bar length — guarantees seamless looping and identical durations across all slices at a given BPM
- **BPM override**: Manual BPM entry in the slicer for precise bar-length calculations (auto-detect by default)
- **Dedicated cue output**: Preview slices on a separate audio device/channel pair with independent volume — headphone monitoring while the audience hears the main output
- **Live loop region update**: While looping a selection, moving the markers queues the new region — it transitions seamlessly at the end of the current loop iteration. Stop and re-press to jump to the new selection immediately
- **Auto-chop (Slice Mode)**: Manual, Transient, Equal, and BPM Grid chop modes with batch export of all regions, metadata sidecar files, and sensitivity control
- **Precision editing**: Numeric start/end/duration spinboxes, zero-crossing snap (ZX), BPM-grid snap (GRID), marker nudge (±1ms / ±1 sample), and undo/redo (Ctrl+Z / Ctrl+Shift+Z)
- **Contextual tooltips**: Hover over any slicer control for a description of what it does
- **Scrollable Slices Library**: All saved slices listed with search, sort (name/date), BPM badges, right-click "Load to channel", multi-select batch delete, and drag-to-load

### Controllers
- **Monome grids** — four connection backends, selectable in Setup → Grid:
  - **Auto** (default): probes serialosc first; falls back to 40h-direct if an FTDI monome is detected but serialosc cannot hand-shake it (common with pre-2010 kit hardware on Windows)
  - **serialosc** — modern monome grids (2010 and newer) using the serialosc daemon over OSC/UDP. Supports any grid size (64 / 128 / 256 / arc-style). LED brightness levels (dim / med / bright) fully supported. Tilt/accelerometer available on grids that have the sensor
  - **40h direct** — legacy 2007–2009 kit/40h/black-rubber-64 hardware that uses the binary 256/m64 serial protocol over FTDI USB. serialosc 1.4.x cannot enumerate these devices; this backend bypasses serialosc entirely and talks directly to the COM port at 9600 baud. LEDs are binary on/off (the 40h has no hardware dimming). Tilt (X/Y ADC) is supported
  - **Legacy OSC (monomeserial)** — for grids managed by the pre-serialosc [monomeserial](https://github.com/monome/monomeserial) bridge application. Communicates over OSC/UDP using monomeserial's default port layout (send to 8080, receive on 8000). See `run_monomeserial.bat` for a pre-configured launcher
- **Novation Launchpads**: Mini, S, MK1, MK2, X, MK3, Pro, Pro MK3 — with per-group color LED feedback
- **Generic MIDI grids**: Any note-on/off based controller
- **Built-in virtual grid** (always available)
- **OSC networking** — remote control and visual mirroring via `python-osc`; button presses are forwarded to `/mlrvp/grid/key x y state` and playback positions to `/mlrvp/position row pos` (serialosnoopy-style mirroring)

### MIDI
- **Full MIDI Learn** system — map any CC or Note to any parameter
- **MIDI MAP button** in the FX Rack view — same functionality as Setup → MIDI Learn but accessible while browsing effects; both buttons stay in sync
- **Multiple MIDI input controllers**: Connect several MIDI controllers simultaneously for mapping
- **Toggle vs Momentary mode**: Per-mapping toggle checkbox — toggle mode flips on/off on each press, momentary mode acts only while held
- **105+ mappable controls**: Every fader, knob, checkbox, and button across channels, groups, effects, delay, master filter, mixer, and FX routing
- **Mapping list table** in the Setup → MIDI tab showing all active mappings with CC, channel, min/max range, and toggle mode
- **Per-parameter CC mapping** with configurable min/max value range (supports reversed ranges)
- **Visual learn indicators** and overlay for mapped controls
- **Mappings saved/restored** with presets (slot-based and file-based)
- **Monome tilt/accelerometer mapping**: Assign X/Y/Z tilt axes to any parameter (auto-enabled on monome connect)

### Interface
- **Dark professional UI** matching the original mlrV aesthetic
- **Per-group color coding** (golden, green, salmon, teal)
- **VU meters** with peak indication on groups and master
- **Control value displays**: All knobs and faders show a floating value overlay while being moved or adjusted via MIDI. Right-click any knob or fader to permanently latch its display on; right-click again to clear it. Two View menu options control this globally:
  - **Lock Control Displays** — keeps value overlays visible on every knob and fader at all times
  - **Disable Control Displays** — hides all overlays entirely (overrides latch and drag behaviour)
- **UI Scale** — resize the entire interface from 50% to 200% via View → UI Scale
- **Cross-platform**: Windows, macOS, Linux

---

## Quick Start (all platforms)

### Prerequisites

- **Python 3.10 or newer**: Download from [python.org](https://www.python.org/downloads/)
  - On Windows: check **"Add Python to PATH"** during install
  - On macOS: `brew install python` or download from python.org
  - On Linux: `sudo apt install python3 python3-pip python3-venv` (Debian/Ubuntu) or your distro's package manager

### Option A — One-click launch scripts

**Windows:**
```
Double-click run.bat
```

**macOS:**
```
Right-click open run.command in Finder
```
(If blocked, right-click → Open, or run `chmod +x run.command` first.)

**Linux:**
```bash
chmod +x run.sh
./run.sh
```

These scripts will automatically create a virtual environment, install all dependencies, and launch mlrVp.

### Option B — Manual setup

```bash
# 1. Create a virtual environment
python -m venv .venv

# 2. Activate it
#    Windows (PowerShell):
.venv\Scripts\Activate.ps1
#    Windows (cmd):
.venv\Scripts\activate.bat
#    macOS / Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run mlrVp
python -m mlrvp
```

### Option C — Install as a package (pip)

```bash
pip install .
mlrvp
```

This installs mlrVp system-wide (or into a venv) and makes the `mlrvp` command available.

---

## Building a Standalone Executable

If you want a single self-contained app that doesn't require Python to be installed:

```bash
# Install build dependencies
pip install pyinstaller

# Build (folder mode — recommended)
python build.py

# Or build as a single file
python build.py --onefile

# Windows only: build + create an installer (requires Inno Setup)
python build.py --installer
```

Output goes to `dist/mlrVp/`. On macOS this produces `mlrVp.app`, on Windows `mlrVp.exe`, on Linux `mlrVp`.

### Regenerating Icons

To regenerate application icons (`.png`, `.ico`, `.icns`) from the Python source:

```bash
pip install Pillow
python generate_icon.py
```

---

## Platform Notes

### Windows
- ASIO drivers recommended for low-latency audio (download from your audio interface manufacturer, or use [ASIO4ALL](https://www.asio4all.org/) for onboard audio)
- The `sounddevice` package uses PortAudio which supports WASAPI, DirectSound, and ASIO
- **Monome grids:** install [serialosc](https://github.com/monome/serialosc/releases) for modern grids. For original 40h/64 kit hardware, use the **40h direct** backend (requires FTDI VCP driver + `pyserial`). For monomeserial-managed grids, use `run_monomeserial.bat`

### macOS
- CoreAudio is used automatically — no extra drivers needed
- If you get a security warning on first launch, go to System Settings → Privacy & Security → Allow

### Linux
- Requires ALSA or PulseAudio/PipeWire (usually pre-installed)
- You may need: `sudo apt install libportaudio2` (Debian/Ubuntu) or `sudo dnf install portaudio` (Fedora)
- For low latency, configure JACK audio

---

## Requirements

- **Python 3.10+**
- **Audio interface** (ASIO recommended on Windows)
- **Grid controller** (optional — virtual grid included)
  - Monome grid — any size (see [Monome Hardware Support](#monome-hardware-support) below)
  - Novation Launchpad (any model)
  - Any MIDI controller with note-on/off

### Python Dependencies

| Package | Purpose |
|---------|---------|
| PySide6 | Qt6 GUI framework |
| numpy | Audio buffer math |
| sounddevice | Cross-platform audio I/O |
| soundfile | Audio file loading (WAV, AIFF, FLAC, OGG) |
| python-rtmidi | MIDI I/O for Launchpad and MIDI Learn |
| python-osc | OSC networking |
| pymonome | Monome grid communication (serialosc backend, optional) |
| pyserial | Legacy 40h direct serial backend (optional) |
| resampy | High-quality sample rate conversion |
| scipy | Signal processing (filters) |
| librosa | Time-stretch-free pitch shifting |
| numba | JIT-compiled DSP kernels for real-time audio processing |
---

## Monome Hardware Support

mlrVp supports every generation of monome grid hardware through four selectable backends. Choose the right one in **Setup → Grid → Monome Backend**.

### Modern grids — serialosc (2010 and newer)

Any monome grid released after 2010 (grayscale 64 / 128 / 256 / walnut editions, all current production models) communicates via the [serialosc](https://github.com/monome/serialosc) daemon.

**Requirements:**
- Install serialosc from the [monome/serialosc releases page](https://github.com/monome/serialosc/releases)
- On Windows: serialosc runs as a background service. If it ever gets stuck, open Task Manager → Services → right-click `serialosc` → Start (or run `net start serialosc` as Administrator)
- The `pymonome` Python package must be installed (`pip install pymonome`)

**Backend:** set to **Auto** or **serialosc**. mlrVp auto-discovers connected devices and lists them in the Grid Device dropdown.

**Features fully supported:** variable LED brightness (off / dim / med / bright), tilt/accelerometer (on grids that have the sensor), any grid size.

---

### Original 40h / black-rubber-64 kit grids — 40h direct (2007–2009)

The original "40h kit" and early black-rubber 64 boards speak a binary serial protocol (the "256/m64" protocol from `libmonome`) over an FTDI USB-Serial converter chip. **serialosc 1.4.x on Windows does not recognise these devices** — the handshake times out and the grid never appears. mlrVp's 40h-direct backend bypasses serialosc entirely.

**Requirements:**
- Install the [FTDI VCP driver](https://ftdichip.com/drivers/vcp-drivers/) so the device appears as a COM port (e.g. `COM7`)
- Install `pyserial`: `pip install pyserial`
- If serialosc is installed and running, the 40h-direct backend will request an elevated stop of the serialosc service so it can claim exclusive access to the COM port

**Backend:** set to **40h direct**. mlrVp scans for FTDI VID/PID `0403:6001` ports automatically; if only one is found it connects on first scan.

**Limitations:**
- LEDs are binary on/off only — the 40h hardware has no PWM dimming. mlrVp thresholds any brightness value ≥ 1 to ON so that dim sub-loop backgrounds and step-sequencer dots remain visible
- Row/column bulk writes (`0x70|y` / `0x80|x`) are **not used** — empirical testing on kit firmware confirmed they are silently ignored. mlrVp uses individual per-LED `0x20 [(x<<4)|y]` commands instead
- No LED brightness levels; all grid visual feedback is binary

**Environment variables for advanced tuning:**

| Variable | Default | Effect |
|----------|---------|--------|
| `MLRVP_40H_COM` | auto-detect | Force a specific COM port (e.g. `COM7`) |
| `MLRVP_40H_BAUD` | `9600` | Override serial baud rate |
| `MLRVP_40H_TILT_SMOOTH` | `0.3` | EMA smoothing factor for tilt axes (0 = raw, 1 = frozen) |

---

### Pre-serialosc grids managed by monomeserial — Legacy OSC

[monomeserial](https://github.com/monome/monomeserial) is the original bridge application used before serialosc existed (circa 2007–2010). If your grid is already managed by a running copy of monomeserial, use the **Legacy OSC** backend to connect without needing serialosc at all.

monomeserial exposes the grid as OSC over UDP:

| Direction | Default port | What it carries |
|-----------|-------------|-----------------|
| mlrVp → monomeserial | **8080** | LED set, clear, prefix queries |
| monomeserial → mlrVp | **8000** | Button press/release events |

mlrVp probes `/sys/report` on startup to discover the active prefix, device type (`40h` / `64` / `128` / `256`), and serial number. The `run_monomeserial.bat` launcher pre-configures all environment variables and calls `run.bat`.

**Backend:** set to **Legacy OSC** (or use `run_monomeserial.bat` which sets `MLRVP_MONOMESERIAL=1`).

**Environment variables:**

| Variable | Default | Effect |
|----------|---------|--------|
| `MLRVP_MONOMESERIAL` | `0` | Set to `1` to force Legacy OSC backend on launch |
| `MLRVP_MONOMESERIAL_HOST` | `127.0.0.1` | IP where monomeserial is running |
| `MLRVP_MONOMESERIAL_SEND_PORT` | `8080` | Port mlrVp sends LED commands to |
| `MLRVP_MONOMESERIAL_RECV_PORT` | `8000` | Port mlrVp listens on for button events |

---

### Backend selection summary

| Hardware | Era | Recommended backend | serialosc needed? | pyserial needed? |
|----------|-----|---------------------|-------------------|-----------------|
| Current production monome grids | 2010+ | **Auto** or **serialosc** | Yes | No |
| Grayscale 64 / walnut editions | 2011+ | **Auto** or **serialosc** | Yes | No |
| Original 40h kit / black-rubber 64 | 2007–2009 | **40h direct** | No | Yes |
| Any grid via monomeserial bridge | Pre-2010 | **Legacy OSC** | No | No |

The **Auto** backend probes serialosc first. If the daemon is running and lists a device, it connects via serialosc. If serialosc is not running (or the device doesn't enumerate — common with 40h kit hardware), Auto then scans for FTDI COM ports and tries 40h-direct.

---

## OSC Remote Control

In addition to hardware grid communication, mlrVp exposes an OSC interface for remote control and visual mirroring (matching the `serialosnoopy` feature from the original mlrV2.2):

- **Receive port**: 8001 (default) — accepts any registered handler address
- **Send port**: 8000 (default), configurable via `OscHandler`
- **Button mirror**: every grid button press/release is forwarded to `/mlrvp/grid/key x y state`
- **Playback position**: channel positions are published to `/mlrvp/position row pos`

This lets a second computer (or a visualiser like Processing/Max/TouchDesigner) mirror the grid state in real time.

---

## Project Structure

```
MLRVp/
├── mlrvp/                  # Main package
│   ├── __init__.py
│   ├── __main__.py         # python -m mlrvp entry
│   ├── main.py             # CLI entry point
│   ├── app.py              # Application orchestrator
│   ├── config.py           # Configuration + data classes
│   ├── audio/              # Audio engine
│   │   ├── engine.py       # Real-time audio callback + ring buffer
│   │   ├── channel.py      # Per-row sample playback
│   │   ├── mixer.py        # Group buses + FX routing + master
│   │   ├── effects.py      # Delay, FX buses, filters, modulation
│   │   ├── recorder.py     # Live audio recording
│   │   ├── sample.py       # Sample loading + buffering
│   │   └── _dsp_kernels.py # Numba JIT-compiled DSP kernels
│   ├── transport/          # Tempo + timing
│   │   ├── clock.py        # Master clock
│   │   └── pattern.py      # Pattern recording/playback
│   ├── grid/               # Grid controllers
│   │   ├── controller.py   # Grid controller hub — serialosc, 40h-direct, legacy OSC, MIDI
│   │   ├── midi_grid.py    # Launchpad + MIDI grid support
│   │   ├── monome40h.py    # 40h direct binary serial backend (legacy kit hardware)
│   │   └── monomeserial.py # Legacy OSC backend (monomeserial bridge)
│   ├── midi/               # MIDI I/O
│   │   └── handler.py      # MIDI learn + CC mapping
│   ├── osc/                # OSC networking
│   │   └── handler.py      # OSC send/receive
│   ├── preset/             # Preset management
│   │   └── manager.py      # Save/load/recall + setlist
│   ├── resources/          # Icons and assets
│   │   ├── arrow_down.svg  # Spinbox down-arrow icon
│   │   ├── arrow_up.svg    # Spinbox up-arrow icon
│   │   ├── icon.icns / .ico / .png
│   │   ├── icon_diagonal.icns / .ico / .png
│   │   └── icon_red.icns / .ico / .png
│   └── gui/                # PySide6 interface
│       ├── styles.py       # Colors, fonts, stylesheet
│       ├── main_window.py  # Main window layout
│       ├── midi_map_overlay.py  # MIDI learn overlay
│       ├── setlist_editor.py    # Setlist editor dialog
│       ├── views/          # Panels
│       │   ├── channel_view.py
│       │   ├── mixer_view.py
│       │   ├── effects_view.py  # FX Rack (FX 1 / FX 2 / FX 3 / FX 4 / Master)
│       │   ├── master_filter_view.py
│       │   ├── transport_view.py
│       │   ├── tools_view.py
│       │   └── setup_view.py
│       ├── widgets/        # Custom widgets
│       │   ├── waveform.py
│       │   ├── fader.py
│       │   ├── vu_meter.py
│       │   ├── virtual_grid.py
│       │   ├── rack_container.py   # Scrollable rack + drag-drop
│       │   └── rack_module_base.py # Base class for rack modules
│       └── rack_modules/   # Skeuomorphic effect modules
│           ├── __init__.py         # Module registry
│           ├── digital_delay.py    # Déjà Vu Delay (1U)
│           ├── tape_delay.py       # Rewind (1U)
│           ├── multiband_filter.py # Band Practice (2U)
│           ├── moog_filter.py      # The Ladder (1U)
│           ├── phaser.py           # Snake Charmer (1U)
│           ├── chorus.py           # Parrot Talk (1U)
│           ├── flanger.py          # Jet Stream (1U)
│           ├── bit_crusher.py      # Byte Me (1U)
│           ├── tube_overdrive.py   # Hot Snake (1U)
│           ├── reel_to_reel.py     # Reel Deal (1U)
│           ├── cassette_tape.py    # Mixtape (1U)
│           ├── vhs.py              # Tracking Shot (1U)
│           ├── vinyl.py            # Wax Poetic (1U)
│           ├── plate_spring_reverb.py # Spring Fling (2U)
│           ├── hall_reverb.py      # Echo Chamber (2U)
│           ├── compressor.py       # Thrust Issues — Sidechain Compressor (2U)
│           ├── glue_compressor.py  # Group Hug — Glue Bus Compressor (1U)
│           ├── ether_drift.py      # Ether Drift — Bipolar Infinite Phaser (1U)
│           ├── noise_gate.py       # The Velvet Rope — Noise Gate (1U)
│           ├── tremolo.py          # Heartquake — Tremolo (1U)
│           ├── auto_wah.py         # Funk Doctor — Auto-Wah (1U)
│           ├── fet_limiter.py      # All Buttons In — FET Limiter (1U)
│           ├── exciter.py          # Vitamin E — Aural Exciter (1U)
│           ├── transient_shaper.py # Punch Card — Transient Shaper (1U)
│           ├── freq_shifter.py     # The Bode Plot — Freq Shifter (1U)
│           ├── ring_mod.py         # Ring Leader — Ring Modulator (1U)
│           ├── rotary.py           # Spin Cycle — Rotary Speaker (1U)
│           ├── stereo_widener.py   # Personal Space — Stereo Widener (1U)
│           ├── harmonizer.py       # Wrong Lane — Dual Harmonizer (1U)
│           ├── simple_eq.py        # Golden Ears — 3-Band EQ (1U)
│           ├── ping_pong_delay.py  # Table Tennis — Ping-Pong Delay (1U)
│           ├── shimmer_reverb.py   # Stained Glass — Shimmer Reverb (1U)
│           ├── lofi_radio.py       # Tin Can — Lo-Fi Radio (1U)
│           ├── auto_pan.py         # Pendulum — Auto Pan (1U)
│           ├── fuzz.py             # Wool Over — Fuzz (1U)
│           ├── granular_freeze.py  # Snow Globe — Granular Freeze (1U)
│           ├── gated_reverb.py     # Gate Crasher — Gated Reverb (1U)
│           ├── stutter.py          # Broken Record — Stutter (1U)
│           ├── dual_vu_meter.py    # Twin Peaks (1U)
│           └── oscilloscope.py     # CRT:// Scope (1U)
├── data/                   # Runtime data
│   ├── presets/            # Global preset library (JSON)
│   ├── setlists/           # Setlist files (JSON)
│   ├── slices/             # Sliced samples (WAV)
│   ├── defaults/           # Saved default states
│   └── ui_scale.txt        # UI scaling factor
├── requirements.txt        # pip dependencies
├── pyproject.toml          # PEP 621 package metadata
├── build.py                # PyInstaller build script
├── generate_icon.py        # Icon generation utility
├── mlrvp.spec              # PyInstaller spec file
├── run.bat                 # Windows launcher
├── run.command             # macOS launcher
├── run.sh                  # Linux launcher
├── run_monomeserial.bat    # Windows launcher pre-configured for monomeserial Legacy OSC mode
└── README.md
```

---

## How-To Guide

### Channel Strip Overview

Each channel row consists of a control bar at the top and a **waveform display** below it. The control bar is divided into labeled sections separated by vertical divider lines, running left to right:

| Section | Contents | Description |
|---------|----------|-------------|
| *(left edge)* | Color bar + row number | Group color indicator and 1-based channel number |
| **SAMPLE** | Sample name label (clickable) + **LOAD** button | Shows loaded filename; click name to pick from history, LOAD to browse files |
| **MODE** | Dropdown: LOOP / PTCH / RVRS / SHOT / SLCE / SQNC / STEP | Playback mode selector (alphabetical order) |
| **GROUP** | Buttons 1–4 | Assigns the channel to a group (one active at a time) |
| **GAIN** | Horizontal fader | Channel output level (0.0–1.0) |
| **RATE** | Speed spinner (0.00–8.00×) + **-½** + **+2×** | Playback speed; halve or double with shortcut buttons |
| **PITCH** | Semitone spinner (−24 to +24 st) | Transpose pitch shift (all modes). In PTCH mode, acts as root offset — shifts all scale pitches up or down |
| **SCALE** *(PTCH mode)* | Scale dropdown + **HOLD** toggle | Scale map (Major / Minor / Penta / Chord / Chromatic) and hold-to-loop toggle (visible in PTCH mode only) |
| **STEP** | Integer spinner (1–32) | Slice-mapping resolution (locked to grid columns in SEQ mode) |
| **CONTROL** | **MUTE** + **STOP** | MUTE silences channel output without stopping playback; STOP immediately ends playback |

### Loading Samples

There are several ways to load audio files into channel rows:

1. **Drag and drop** an audio file (WAV, AIFF, FLAC, OGG, MP3) from your file manager onto any channel strip's waveform area.
2. **Click LOAD** on any channel strip to open a file browser dialog.
3. **Click the sample name label** to pick from previously loaded samples (a quick-access dropdown menu).
4. **Preset recall** — switching presets automatically loads the samples stored in that preset.
5. **Drag from the Slices Library** (Tools → Slice tab) directly onto any channel strip waveform.

#### Live Sample Staging

When you drag a new sample onto a **row that is already playing**:

- The **waveform display** updates immediately to show the new sample.
- The **speed spinner** updates to reflect the new sample's calculated rate.
- The **old audio continues playing** — the grid LEDs keep looping the old sample.
- The **½× and 2× rate buttons** and speed spinner adjust the *new* sample's speed (not the old one still playing).
- The new sample **loads into the engine on the next trigger** — press a grid button on that row to switch.
- Alternatively, hitting the row's **group stop** or **ALL stop** clears the old audio, and the next trigger will play the new sample.

When you drag a sample onto an **idle row** (not playing), it loads directly and is ready for immediate triggering.

#### Speed and the Snap-to-Rate Feature

When a sample is loaded, mlrVp calculates a playback speed so the sample fits the current grid columns at the master tempo. By default, **Snap Speed to Rate** is disabled, if you use this feature it rounds the calculated speed to the nearest power-of-two rate:

| Calculated | Snapped |
|-----------|---------|
| 0.18×     | 0.25×   |
| 0.37×     | 0.5×    |
| 0.74×     | 1.0×    |
| 1.3×      | 1.0×    |
| 3.1×      | 4.0×    |

This feature ensures loops align cleanly to the grid. To enable snapping (round the calculated speed to the nearest ½×/2× step), check **Snap Speed to Rate (½x / 2x)** in Setup → Grid.

The **-½** and **+2×** buttons on each channel strip halve or double the current speed, letting you quickly find the right loop length reguardless if snap is enabled or not.

### Waveform and Selection

The waveform widget below each channel's control row shows the full loaded sample with several visual elements:

- **Bright region** — the active loop/playback selection. Audio between the selection start and end markers is what the channel plays in the current mode.
- **Dim region** — audio outside the selection is visible but grayed out, letting you orient the selection relative to the full file.
- **Playback cursor** — a vertical line that sweeps across the active region as the sample plays.
- **Grid dividers** — faint vertical lines splitting the waveform into equal columns matching the current grid column count.
- **Active column highlight** — the column currently triggering is lit with the group color.

#### Setting the Selection

- **Drag the left handle** (left edge of the bright region) to set the loop start point.
- **Drag the right handle** (right edge) to set the loop end point.
- Dragging inside the waveform (not on handles) pans the view when zoomed in.
- The selection is saved with each preset and restored on recall.

### Transpose (Pitch Shift)

Each channel strip has a **PITCH** spinbox (−24 to +24 semitones). Adjusting transpose **does not change playback speed** — the sample is time-stretch-pitch-shifted using `librosa` in a background thread, so it plays at the same rate regardless of semitone offset.

- While the pitch-shift is being computed (typically < 1 second for normal-length samples), the channel plays the original, unshifted audio and then seamlessly switches once ready.
- Setting transpose back to **0 st** reverts to the original audio immediately with no computation needed.
- Transpose is saved and restored with presets.

### STEP Control

The **STEP** spinbox (1–32) on each channel strip controls how many equal slices the sample selection is divided into when a grid button is pressed:

- **Default (matches grid columns)**: Each button press triggers the corresponding 1/N slice — identical to the classic mlrV behaviour.
- **Higher step count than grid columns**: Multiple slices are mapped to each button using the floor formula `slice_index = floor((button / grid_width) × steps)` — finer subdivision without needing extra buttons.
- **Lower step count**: Multiple buttons map to the same slice (coarser triggering).
- In **SEQ mode**, the STEP count is automatically locked to the grid column count and cannot be edited independently.

### Using SEQ Mode

SEQ mode turns a channel row into a step sequencer that steps through equal sample slices in sync with the master clock.

#### Setup

1. Set the channel **MODE** dropdown to **SEQ**.
2. The **STEP** spinner is automatically locked to the current grid column count (e.g. 8 steps on an 8-column grid, 16 on a 16-column grid).
3. Load a sample — the sample selection is automatically divided into equal slices, one per step.

#### Programming Steps

On the grid (hardware or virtual), each button in a SEQ row acts as a step toggle:

- **Lit button** = active step — that slice of the sample triggers when the clock reaches it.
- **Dark button** = skipped step — silence for that beat.
- **Press any button** to toggle it on or off at any time, including while the sequencer is running.

The sequencer runs left to right through all columns continuously, synchronized to the master BPM and QUANTIZE setting.

#### Tips

- For a one-bar sample at 4/4, an 8-step SEQ gives each step a half-beat (eighth note); a 16-step SEQ gives each step a sixteenth note.
- Combine multiple SEQ rows on different groups to layer independent step-sequenced loops playing simultaneously.
- Adjust the waveform selection start and end points to shift which region of the audio is distributed across steps.
- Unlike other modes, the STEP spinner is read-only in SEQ mode — change the grid column count in Setup → Grid to adjust step count.

### Group-Exclusive Playback

Rows are assigned to one of 4 groups (yellow, green, red, blue). **Only one row per group can play at a time:**

- If row 1 and row 2 are both in Group 1 and row 1 is playing, pressing a button on row 2 will **automatically stop row 1** and start row 2.
- Rows in **different** groups play simultaneously — you can have up to 4 samples playing at once (one per group).
- This applies to all playback modes (Loop, Pitch, Reverse, Shot, Slice, SEQ, Step) and to pattern recorder playback.
- To change a row's group, use the group selector in the channel strip.

#### Stopping Playback

- **GROUP STOP buttons** (G1–G4) in the transport bar stop all channels in that group. All four group stop buttons plus an **ALL** button are arranged in a horizontal row under a single **GROUP STOP** label.
- **ALL** button stops every playing channel across all groups.
- **TAP** button lets you tap-set the master tempo. Each tap updates the BPM.
- On the **hardware grid** controller, the top row buttons 1–4 are group stops.
- Grid LEDs clear when a group/ALL stop is pressed.

### Transport Bar

The transport bar runs along the top of the main window. From left to right:

| Control | Function |
|---------|----------|
| **BPM** spinner + **−** / **+** buttons | Master tempo (20–300 BPM). Click the spinner and type a value, or use the ± buttons (auto-repeating). |
| **SYNC** button | Auto-detects BPM from the loaded samples and updates the tempo spinner instantly. |
| **TAP** button | Tap tempo — click repeatedly to set the BPM by feel. Shows current beat position (e.g. `2.3`) when idle; resets after 2 seconds of no tapping. |
| **QUANTIZE** dropdown | Trigger quantization — Off (immediate), 1/4, 1/8, 1/16, or Bar. Quantized triggers wait for the next grid boundary before starting playback. |
| **PATTERN** buttons (P1–P4) | Pattern recorders — see Pattern Recorders section below. Click **−** / **+** to change bar count freely from 1 to 32 bars (defaults: 4, 8, 16, 32). Hold the label 3 seconds to erase. |
| **GROUP STOP** (G1–G4 + ALL) | Stops all channels in the specified group, or ALL to stop everything at once. |
| **PRESET** section | Preset selector dropdown, SAVE, NEW, ◀ ▶ navigation, and ⚙ setlist manager. |

### Presets and Setlists

mlrVp uses a two-tier system: a **global preset library** and **ordered setlists**.

#### Preset Library

- Presets are standalone JSON files stored in `data/presets/`.
- Each preset captures the **complete application state**: all channel settings (samples, speed, mode, group, gain, selection, envelope), effects, mixer levels, FX routing, MIDI mappings, and transport settings.
- Presets are **reusable** — the same preset can appear in multiple setlists.

#### Creating and Saving Presets

1. Set up your channels, effects, and mixer the way you want.
2. Type a name in the **PRESET** combo box (it's editable — just click and type).
3. Click **SAVE** — the button flashes green to confirm.
4. The preset is saved to the library and added to the current setlist.
5. To overwrite an existing preset, select it and click SAVE again.

#### Recalling Presets

- **Click a preset** in the dropdown to recall it with a live transition.
- Use the **◀ / ▶ arrow buttons** to step through the setlist (wraps around).
- During live recall:
  - Effects, mixer, and transport settings update **immediately**.
  - Playing channels **keep their current audio looping** — they are not interrupted.
  - New samples are **staged** and load on the next grid trigger for that row.
  - Idle channels load their new samples immediately.

#### Starting Fresh

- Click the **NEW** button to clear all channels and reset to a blank state.
- All playback stops, all waveforms clear, and the active preset is deselected.
- You can then load new samples and save as a new preset.

#### Managing Setlists

1. Click the **⚙** (gear) button to open the **Setlist Editor**.
2. **Left panel — Setlist Order**: Shows the presets in the current setlist. Drag to reorder. Click **Remove** to take a preset out of the setlist (it stays in the library).
3. **Right panel — Preset Library**: Shows all saved presets. Select one and click **◀ Add** to insert it into the setlist.
4. **Rename** or **Delete** presets from the library using buttons in the right panel.
5. Use the **Setlist** dropdown at the top to create, switch, rename, or delete setlists.
6. The **Default** setlist is created automatically on first run.

⚠ If a setlist references a preset that has been deleted from the library, it shows a warning marker.

#### File Import/Export

- **File → Open Preset** loads a preset from any location on disk.
- **File → Save Preset** exports the current state to a standalone JSON file for sharing.

### Audio Device Setup

To configure your audio hardware, open **Setup → Audio**:

1. Use the **Driver Type** dropdown to filter devices by host API — select **ASIO**, **WASAPI**, **CoreAudio**, **ALSA**, or leave at **All Drivers** to see every available device.
2. Choose your **Output Device** from the filtered list (or leave at Default).
3. Choose your **Input Device** if you plan to record audio.
4. Set **Sample Rate** and **Buffer Size** — lower buffer sizes reduce latency at the cost of increased CPU load.
5. Click **Start Engine** to open the audio stream. The status label shows the active device and sample rate.
6. Click **Restart** (File → Restart) at any time to reinitialize the audio engine with the current settings.

> **Windows tip**: Use the Driver Type filter to select **ASIO** for lowest latency. If no ASIO devices appear, install [ASIO4ALL](https://www.asio4all.org/) for onboard audio.

### Grid Controller Setup

#### Connecting a Controller

1. Open **Setup → Grid**.
2. Click **Scan for Controllers** to detect connected Grid Controllers.
3. The status label shows the connected device and grid size.
OR
1. Select the device from the dropdown and click **Connect**.
2. The status label shows the connected device and grid size.

A **Virtual Grid** is always available — check **Show Virtual Grid** to pop out the on-screen grid window.

#### Grid Settings

- **Active Rows**: 1–16 rows (default 8).
- **Grid Columns**: 8 or 16 columns per row.
- **Snap Speed to Rate (½x / 2x)**: When checked, sample load speed snaps to the nearest power-of-two rate. Off by default — uncheck to use raw calculated speed.
- **Show Virtual Grid**: Toggle the on-screen virtual grid window.
- **Tilt Control Mapping**: Enable monome accelerometer axis mapping.

#### Hardware Grid Layout

On a Launchpad or monome:

| Row | Function |
|-----|----------|
| **Top (control)** | Buttons 1–4: Group stops. Buttons 5–8: Pattern recorders. |
| **Rows 1–8** | Sample trigger columns. Each row = one channel. Press a column to trigger playback from that position. |

- LEDs show playback position (bright). Content outside the active loop region is shown dim on the waveform display.
- In **SEQ mode**, lit buttons indicate active steps; pressing a button toggles that step on/off.
- Group stop LEDs light when their group has playing channels.
- Pattern LEDs: bright = recording/playing, dim = has events, off = empty.

### Virtual Grid (Pop Out)

The built-in **Virtual Grid** is an on-screen replica of a hardware grid controller. It's always available, even without any external hardware connected, and behaves identically to a monome or Launchpad for triggering, pattern recording, and LED feedback.

#### Opening the Virtual Grid

- Go to **View → Show Virtual Grid** — a floating window pops out as a separate, moveable panel.
- Alternatively, in **Setup → Grid**, check **Show Virtual Grid**.
- If **no hardware grid** is detected when the app starts, the Virtual Grid opens automatically.

#### Using the Virtual Grid

- **Click any button** on the grid to trigger that column on that row, exactly as you would press a physical pad on a monome or Launchpad.
- **Click and release** — pressing sends a "button down" event; releasing sends "button up." Modes like **Slice** (hold-to-play) respond to both press and release.
- The top row acts as the **control row** (same as hardware): buttons 1–4 are group stops, buttons 5–8 are pattern recorders.
- All remaining rows correspond to channel strips 1–N, where N is the configured Active Rows count.

#### LED Feedback

- Each button reflects the same LED state that would display on a hardware grid:
  - **Bright** — active playback column or active step (SEQ mode).
  - **Dim** — content present (e.g. a loop region or a recorded pattern event) but not the current position.
  - **Off** — no content / idle.
- Button colors follow the **group color** assignment for each row (golden, green, salmon, teal), matching the channel strip and mixer group colors.
- LEDs update in real time at the UI refresh rate (~30 fps) so the playback cursor sweeps across the grid smoothly.

#### Resizing and Configuration

- The grid dimensions are controlled by **Active Rows** and **Grid Columns** in Setup → Grid. Changes resize the Virtual Grid immediately.
- The Virtual Grid window can be **moved freely** on screen — position it alongside the main window or on a second monitor.
- The window respects the global **UI Scale** setting (View → UI Scale), so at 150% scale the grid buttons grow proportionally.

#### Tips

- The Virtual Grid is useful for **testing setups** without hardware, building presets on a laptop, or as a visual monitor of what's happening on your physical grid.
- You can use the Virtual Grid and a hardware grid **simultaneously** — both send identical events, and LED feedback is mirrored to both.
- In **SEQ mode**, clicking a grid button toggles that step on/off — the Virtual Grid shows active steps as lit buttons, just like hardware.

### MIDI Mapping

mlrVp features a comprehensive MIDI CC mapping system that lets you control every parameter from an external MIDI controller.

#### Learning a Mapping

1. Open the **Setup** panel → **MIDI** tab.
2. Click **+ Add MIDI Input** to add one or more MIDI controllers.
3. Click **Learn** to enter MIDI Map Mode — all mappable controls highlight.
4. **Click** the on-screen control you want to map (e.g. a fader, knob, or button).
5. **Move** a knob, fader, or button on your MIDI controller — the mapping is created instantly.
6. The new mapping appears in the **Mapping List** table below the Learn button.
7. Click **Learn** again (or press Escape) to exit Map Mode.

> **Tip**: You can also enter MIDI Map Mode directly from the FX Rack by clicking the **MIDI MAP** button in the effects header — both buttons stay in sync.

#### Toggle vs Momentary

Each mapping row has a **Toggle** checkbox:
- **Unchecked (momentary)**: Control is active while held — Note Off or CC 0 resets to minimum.
- **Checked (toggle)**: Each press flips the parameter between its max and min values — useful for mute buttons, effect enables, etc.

#### Editing Ranges

Each mapping row has **Min** and **Max** columns (default 0.00 → 1.00):
- Set **Min = 0.50, Max = 1.00** to limit a fader to the upper half of its range.
- Set **Min = 1.00, Max = 0.00** to *reverse* the control direction.
- For tempo mapping, the 0–1 range maps to 20–300 BPM, so Min = 0.33 / Max = 0.67 gives ~113–220 BPM.
- Changes take effect immediately — no need to re-learn.

#### Removing Mappings

- Click the **✕** button on any mapping row to delete it.
- Click **Clear All** to remove every mapping at once.

#### Saving & Loading

- Mappings are **saved with presets** — each preset in the library retains its own independent set of MIDI mappings.
- File → Save/Open preserves all MIDI assignments in the exported preset file.

#### Mappable Controls

All of the following are available for MIDI mapping:

| Category | Parameters |
|----------|------------|
| **Channel Strips** (×16) | Gain, Speed, Mute |
| **Group Buses** (×4) | Volume, Send, Mute, FX Route |
| **Transport** | Tempo |
| **Déjà Vu Delay** | Enable, Sync, Feedback, FB Attack, FB Release, Range Lo, Range Hi, Gain, LP/HP Enable, LP/HP Freq |
| **Rewind** (Tape Delay) | Enable, Sync, Feedback, Wow/Flutter, Saturation, Tone, Gain |
| **Band Practice** (EQ) | Enable, Band 1–5 Gain, Band 1–5 Freq |
| **The Ladder** (Filter) | Enable, Cutoff, Resonance, Drive |
| **Snake Charmer** (Phaser) | Enable, Rate, Depth, Feedback, Mix |
| **Parrot Talk** (Chorus) | Enable, Rate, Depth, Feedback, Mix |
| **Jet Stream** (Flanger) | Enable, Rate, Depth, Feedback, Mix |
| **Byte Me** (Bit Crusher) | Enable, Bit Depth, Sample Rate Reduction, Mix |
| **Hot Snake** (Overdrive) | Enable, Drive, Tone, Bias, Mix |
| **Reel Deal** (Reel-to-Reel) | Enable, Saturation, Flutter, Bias, Tone, Speed |
| **Mixtape** (Cassette Tape) | Enable, Saturation, Hiss, Punch, Warp, Tone |
| **Tracking Shot** (VHS) | Enable, Wobble, Dropout, Character, Noise |
| **Wax Poetic** (Vinyl) | Enable, Crackle, Warmth, Groove Wear |
| **Spring Fling** (Reverb) | Enable, Decay, Damping, Pre-Delay, Mix |
| **Echo Chamber** (Reverb) | Enable, Room Size, Decay, Diffusion, Mix |
| **Thrust Issues** (Compressor) | Enable, Threshold, Ratio, Attack, Release, Makeup, Sidechain HPF |
| **Group Hug** (Glue Compressor) | Enable, Threshold, Ratio, Attack, Release, Makeup, HPF Selector |
| **The Velvet Rope** (Noise Gate) | Enable, Threshold, Attack, Hold, Release, Range, Key Frequency |
| **Heartquake** (Tremolo) | Enable, Rate, Depth, Spread, Mix |
| **Funk Doctor** (Auto-Wah) | Enable, Sensitivity, Frequency, Depth, Speed, Mix |
| **All Buttons In** (FET Limiter) | Enable, Input, Output, Attack, Release, Mix |
| **Vitamin E** (Exciter) | Enable, Tune, Lo Tune, Output, Mix |
| **Punch Card** (Transient Shaper) | Enable, Attack, Sustain, Output, Mix |
| **The Bode Plot** (Freq Shifter) | Enable, Shift, Feedback, Mix |
| **Ring Leader** (Ring Mod) | Enable, Frequency, Shape, Mix |
| **Spin Cycle** (Rotary) | Enable, Speed, Drive, Balance, Mix |
| **Personal Space** (Stereo Widener) | Enable, Width, Center, Lo Mono, Mix |
| **Wrong Lane** (Harmonizer) | Enable, Semi A, Fine A, Delay A, Semi B, Fine B, Delay B, Feedback, Mix |
| **Golden Ears** (Simple EQ) | Enable, Low, Mid, High, Mix |
| **Table Tennis** (Ping-Pong Delay) | Enable, Ping, Pong, Feedback, Mix |
| **Stained Glass** (Shimmer Reverb) | Enable, Decay, Shimmer, Damping, Mix |
| **Tin Can** (Lo-Fi Radio) | Enable, Noise, Wobble, Lo Cut, Hi Cut, Mix |
| **Pendulum** (Auto Pan) | Enable, Rate, Depth, Shape, Mix |
| **Wool Over** (Fuzz) | Enable, Drive, Tone, Gate, Mix |
| **Snow Globe** (Granular Freeze) | Enable, Grain Size, Scatter, Spread, Density, Freeze, Mix |
| **Gate Crasher** (Gated Reverb) | Enable, Room, Gate Time, Slope, Pre-Delay, Mode, Mix |
| **Broken Record** (Stutter) | Enable, Size, Rate, Chaos, Smear, Trigger, Mix |
| **Effect Routing** | Delay FX Route, Mod FX Route |
| **Master Filter** | HP, LP |
| **Mixer** | Aux Volume, Aux Mute, Aux FX Route, Master Volume |

### Monome Tilt / Accelerometer

If you have a monome grid with a built-in accelerometer:

1. When the monome connects, the **Tilt Control Mapping** checkbox in Setup → Grid is automatically enabled.
2. The tilt mapping section appears at the bottom of Setup → MIDI.
3. Use the **Tilt X**, **Tilt Y**, and **Tilt Z** dropdowns to assign each axis to any mappable parameter (e.g. filter cutoff, delay feedback, phaser rate).
4. Tilt values are normalized 0.0–1.0 and mapped through the parameter's callback, so they respond identically to MIDI CC input.
5. To disable tilt, uncheck **Tilt Control Mapping** in the Grid tab.

### Slicing Samples with FIT Mode

The Slice tab in Tools provides a transient-aware slicer that produces perfectly loopable, fixed-length samples:

1. **Load a sample** via the file dropdown or drag-and-drop onto the waveform.
2. **Select a bar count** (1, 2, 4, 8, or 16 bars) from the dropdown.
3. **(Optional) Set BPM** — type in a known BPM, or leave at "auto" to let the slicer detect it from the audio.
4. **Click AUTO** with FIT checked (on by default):
   - The **start** snaps to the nearest transient.
   - The **end** snaps to the nearest transient close to the ideal bar boundary.
   - Both ends are aligned to zero-crossings (no clicks).
5. **Click SLICE + SAVE** — the segment is resampled to the **exact** sample count for the selected bar length, then saved as a WAV in `data/slices/`.

**Why this matters:**
- Every slice at the same BPM and bar count will be **exactly the same duration** — they loop seamlessly at 1.0× speed.
- The resampling correction is typically < 1% (a few cents of pitch shift), which is imperceptible.
- The status bar shows the correction ratio and cents offset so you can verify.

**Without FIT** (uncheck the FIT checkbox): The slicer uses the original behaviour — start snaps to a transient, end is calculated purely from BPM math (may not land on a transient).

#### Slices Library

At the bottom of the Slice tab is a scrollable **SLICES** library panel listing every WAV saved in `data/slices/`:

- **Drag any entry** from the library onto a channel strip waveform to load it for playback.
- **Drag any entry** onto the slicer waveform to open it in the Slice editor.
- **Click ✕** on any entry to permanently delete the file (a confirmation dialog appears before deleting).
- **Search**: Type in the search box at the top of the library to filter slices by filename.
- **Sort**: Use the dropdown to sort by Name (A-Z / Z-A) or date (Newest / Oldest).
- **BPM badge**: Slices exported with metadata show their BPM next to the filename.
- **Right-click → Load to channel N**: Sends the slice directly to a specific channel (1–16).
- **Multi-select**: Hold `Ctrl` and click multiple slices, then click **Delete** to batch-remove them.
- The library updates automatically whenever a new slice is saved.

#### Cue Output Setup

The slicer has a **dedicated cue output** so you can preview slices on headphones while the main audio plays through the audience speakers:

1. In the cue row (below the transport controls), select a **Device** from the dropdown — this can be a completely separate audio interface from the main output.
2. Choose a **Channel pair** (e.g. 1/2 or 3/4) if your device has more than 2 outputs.
3. Set the **Mode**:
   - **CUE ONLY** — preview only through the cue device (audience hears nothing).
   - **CUE + MAIN** — preview through both cue and main (useful during soundcheck).
   - **OFF** — disable cue output entirely.
4. Adjust the **VOL** fader (0–200%) independently of the main output.

All cue settings are saved with presets and restored on recall.

#### Loop Region Updates

When playing a selection with **Loop** enabled, you can move the selection markers to a different region while the audio keeps playing. The new loop region is queued and **transitions seamlessly at the end of the current loop iteration** — the old region plays through to its end, then the new region begins. This lets you audition different sections without hearing a jump or pop mid-loop.

If you **stop** playback first, then adjust the selection and press **▶ Sel**, it plays the new selection immediately.

#### Auto-Chop Workflow

The slicer can automatically divide a sample into regions for batch export:

1. **Select a slice mode** from the dropdown:
   - **Manual** (default) — standard single-marker workflow, no auto-chop.
   - **Transient** — detects transients in the audio and creates a region between each pair. Adjust the **Sensitivity** fader (more left = more regions, more right = fewer).
   - **Equal** — divides the sample (or current selection) into **N** equal parts. Set the count with the **Slices** spinner.
   - **BPM Grid** — divides at beat boundaries using the BPM from the spinbox or auto-detect.
2. **Click CHOP** to run the algorithm. Regions appear as alternating shaded bands in the waveform.
3. **Click any band** to audition it through the cue device. Use `←`/`→` arrow keys to step through regions.
4. **Click EXPORT ALL** to save every region as a separate WAV file in `data/slices/`. FIT-mode resampling is applied per-slice if FIT is checked.
5. A metadata `.json` sidecar is written alongside each exported WAV containing source filename, BPM, bar count, FIT flag, and timestamp.

#### Precision Editing

- **Numeric spinboxes** for Start, End, and Duration (seconds, 3 decimal places) — type a value to move the corresponding marker, or drag the marker and the spinbox updates automatically.
- **Duration spinbox** adjusts the end marker to match the desired duration.
- **ZX button** (zero-crossing snap) — when toggled on (gold highlight), every marker drag auto-snaps to the nearest zero-crossing within ±64 samples. Eliminates clicks at slice boundaries.
- **GRID button** (BPM-grid snap) — when toggled on (blue highlight), markers snap to the nearest quarter-note beat boundary at the current BPM.

#### Slicer Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Space` | Audition the region under the cursor (or toggle play if no regions) |
| `←` / `→` | Step through chop regions and auto-audition each |
| `Shift+←` / `Shift+→` | Nudge the active marker by ±1 ms |
| `Ctrl+Shift+←` / `Ctrl+Shift+→` | Nudge the active marker by ±1 sample |
| `Enter` | Play from current playhead to end of selection |
| `Ctrl+Z` | Undo last marker move |
| `Ctrl+Shift+Z` | Redo last undone marker move |

The **active marker** (start or end) is whichever was last dragged or edited via a spinbox.

#### Platform Notes

- **Windows (WASAPI)**: When using a separate cue device, both the main and cue outputs must use **Shared** mode (not Exclusive). If your DAW or another app holds Exclusive mode on a device, cue playback on that device will fail silently.
- **Windows (ASIO)**: Most ASIO drivers only allow one application at a time. If you need both main and cue on the same ASIO interface, use different channel pairs on the same device rather than two separate devices.
- **macOS (CoreAudio)**: Separate devices work out of the box. For aggregate devices, create them in Audio MIDI Setup and select the aggregate as your main or cue device.

### Effects and Mixer

#### FX Rack

The effects section uses a modular **FX Rack** interface with five independent effect groups accessible via the **FX 1** / **FX 2** / **FX 3** / **FX 4** / **MASTER** tab buttons. FX 1–4 each process their own dedicated send bus; the Master group processes the combined stereo output post-mix.

Each FX group has a curated default loadout:
- **FX 1** — Clean Studio: Compressor → Multiband Filter → Delay
- **FX 2** — Tape Warmth: Glue Compressor → Reel-to-Reel → Tape Delay
- **FX 3** — Space & Mod: Hall Reverb → Plate Reverb → Chorus
- **FX 4** — Lo-Fi / Destroy: Bit Crusher → Tube Overdrive → Vinyl
- **Master** — Mastering: Glue Compressor → Multiband Filter → Oscilloscope

##### Adding and Removing Effects

- Click the **＋ ADD EFFECT** button at the bottom of a rack to open a menu of all 40 available effect modules. Each entry shows the effect type in parentheses — e.g. *Byte Me (Bit Crusher)*, *Echo Chamber (Hall Reverb)*.
- Select a module to insert it into the rack.
- Each module's **right rack ear** has two controls:
  - **Power button** (green/gray circle icon) — toggles the effect on or bypass. Bypassed modules pass audio through unprocessed; their state is still saved with the preset. Power can also be MIDI-mapped.
  - **Remove button** (red ✕) — permanently removes the module from the rack.
- **Limit**: Maximum 8 modules per rack, 40 total across all five groups.

##### Available Modules

| Module | Type | Description |
|--------|------|-------------|
| **Déjà Vu Delay** | Delay | Digital stereo delay — tempo-synced divisions or free ms, feedback envelope, LP/HP filters |
| **Rewind** | Delay | Vintage tape delay — wow/flutter, saturation, tone, sync modes |
| **Band Practice** | EQ | 5-band multiband EQ with sweepable frequency bands |
| **The Ladder** | Filter | 24 dB/oct resonant ladder filter with drive (wood-cheek faceplate) |
| **Snake Charmer** | Modulation | Phaser with rate, depth, feedback, and mix |
| **Parrot Talk** | Modulation | Chorus with rate, depth, feedback, and mix |
| **Jet Stream** | Modulation | Flanger with rate, depth, feedback, and mix |
| **Byte Me** | Distortion | Bit crusher — bit depth, sample rate reduction, mix |
| **Hot Snake** | Distortion | Tube overdrive — drive, tone, bias, mix |
| **Reel Deal** | Lo-Fi / Tape | Studio reel-to-reel — saturation, flutter, bias, tone, tape speed |
| **Mixtape** | Lo-Fi / Tape | Home cassette tape — saturation, hiss, punch, warp, tone |
| **Tracking Shot** | Lo-Fi / Media | VHS degradation — wobble, dropout, worn character, noise |
| **Wax Poetic** | Lo-Fi / Media | Vinyl record — crackle, pops, warmth, groove wear |
| **Spring Fling** | Reverb | Plate/spring reverb — decay, damping, pre-delay, mix |
| **Echo Chamber** | Reverb | Hall/room reverb — room size, decay, diffusion, mix, room type |
| **Thrust Issues** | Dynamics | Sidechain compressor — threshold, ratio, attack, release, makeup, HPF sidechain filter |
| **Group Hug** | Dynamics | Glue bus compressor — threshold, ratio, attack, release, makeup, switchable sidechain HPF |
| **Twin Peaks** | Metering | Dual VU meter — selectable VU (ballistic), PEAK (fast/slow decay), or RMS (power average) mode; reads per-FX-bus signal |
| **CRT:// Scope** | Metering | Oscilloscope for waveform visualization; reads per-FX-bus waveform data |
| **Ether Drift** | Modulation | Bipolar infinite phaser — two allpass chains (RISE/FALL sawtooth LFOs) with stereo spread, SYNC lock, route selector, rate, depth, feedback, and mix |
| **The Velvet Rope** | Dynamics | Noise gate — threshold, attack, hold, release, range, key frequency sidechain filter |
| **Heartquake** | Modulation | Tremolo — rate, depth, stereo spread, mix |
| **Funk Doctor** | Filter | Envelope auto-wah filter — sensitivity, frequency, depth, speed, mix |
| **All Buttons In** | Dynamics | FET limiter — input drive, output, attack, release, mix |
| **Vitamin E** | Enhancement | Aural exciter — high-frequency tune, low-frequency tune, output, mix |
| **Punch Card** | Enhancement | Transient shaper — attack boost/cut, sustain boost/cut, output, mix |
| **The Bode Plot** | Modulation | Frequency shifter (SSB) — shift (Hz), feedback, mix |
| **Ring Leader** | Modulation | Ring modulator — carrier frequency, shape, mix |
| **Spin Cycle** | Modulation | Rotary speaker — speed, drive, horn/drum balance, mix |
| **Personal Space** | Enhancement | Stereo widener — width, center, low-frequency mono crossover, mix |
| **Wrong Lane** | Pitch | Dual harmonizer — semitone shift, fine detune, delay, and feedback per voice, mix |
| **Golden Ears** | EQ | 3-band parametric EQ — low, mid, and high gain controls |
| **Table Tennis** | Delay | Ping-pong stereo delay — independent left/right times, feedback, mix |
| **Stained Glass** | Reverb | Shimmer reverb — decay, shimmer pitch-shift amount, damping, mix |
| **Tin Can** | Lo-Fi | Lo-fi radio — noise, wobble, lo-cut/hi-cut filters, mix |
| **Pendulum** | Modulation | Auto-panner — rate, depth, shape, mix |
| **Wool Over** | Distortion | Fuzz — drive, tone, gate, mix |
| **Snow Globe** | Pitch / Texture | Granular freeze processor — grain size, scatter, pitch spread, density, freeze toggle, mix |
| **Gate Crasher** | Reverb | Gated/reverse reverb — room size, gate time, slope, pre-delay, gate/reverse mode toggle, mix |
| **Broken Record** | Glitch | Stutter/glitch buffer looper — loop size, retrigger rate, chaos, smear, trigger toggle, mix |

##### MIDI MAP Mode

Click the **MIDI MAP** button in the FX Rack header to enter MIDI mapping mode. All mappable rack controls (knobs, faders, switches, and **power toggles**) highlight with a green border. Click any highlighted control, then move a knob/fader on your MIDI controller to create a mapping. This is the same system as Setup → MIDI → Learn — both buttons stay synchronized.

#### FX Routing

mlrVp has **four independent pre-mix FX send buses** (FX1–FX4) plus a **Master** bus. Each mixer group routes to its own bus via the FX route button — click to cycle through:

- **OFF** — dry signal only, no effects processing.
- **FX1** — routes through the FX 1 rack (golden, Group 1 color).
- **FX2** — routes through the FX 2 rack (green, Group 2 color).
- **FX3** — routes through the FX 3 rack (salmon, Group 3 color).
- **FX4** — routes through the FX 4 rack (teal, Group 4 color).

The route button displays the active bus label with its group color as the font and border on a contrasting dark background for easy reading.

The **Master** FX rack processes the combined stereo output post-mix and is always active regardless of send routing.

The **Delay** and **Modulation** effects each have their own route selector to choose which FX bus they belong to.

#### Mixer

- **4 Group faders**: Volume and Aux Send per group, plus Mute and FX Route buttons.
- **Aux bus**: Volume, Mute, and FX Route for the auxiliary (delay send) bus.
- **Master volume**: Final output level with stereo VU meter.
- **Group VU meters**: Per-group peak level display.

#### Master Filter

The top filter section provides a global HP/LP filter with resonance control and visual frequency response curve. This applies to the master output.

### Pattern Recorders

Pattern recorders capture grid gestures (button presses) and play them back in a loop:

1. **Press a pattern button** (P1–P4 in the transport, or top-row buttons 5–8 on the grid).
2. **First press** starts recording — perform your grid gestures.
3. **Second press** stops recording and starts playback — the pattern loops.
4. **Third press** stops playback.
5. **Hold** the pattern label for 3 seconds to erase/clear the pattern.

Pattern lengths are freely configurable from **1 to 32 bars** using the **−** / **+** arrows flanking each pattern label. The four recorders default to 4, 8, 16, and 32 bars respectively.

### Recording Audio

The Tools → Record tab lets you capture audio output:

1. Select a **length** in bars + beats.
2. Click **ARM** to arm recording.
3. Recording starts on the next beat (quantized).
4. When the length is reached, recording stops automatically.
5. The recorded buffer can be loaded into a channel row.

### Playback Modes

Each channel can operate in one of seven modes, selected via the MODE section in the channel strip. The dropdown is listed in alphabetical order:

| Mode | Label | Behaviour |
|------|-------|-----------|
| **Loop** | LOOP | Plays forward in a continuous loop between selection start and end markers. |
| **Pitch** | PTCH | Each grid column plays the sample at a different pitch based on the selected scale map. Uses rate-based pitch shifting (higher pitch = faster playback). Choose a scale (Major, Minor, Pentatonic, Chord, Chromatic), set a root offset (−24 to +24 st) to transpose the whole keyboard, and toggle **HOLD** to loop the sample continuously instead of one-shot. All columns light DIM; the active column lights BRIGHT. |
| **Reverse** | RVRS | Plays backward through the selection in a continuous loop. |
| **Shot** | SHOT | One-shot playback — plays once from the triggered position to the end, then stops. |
| **Slice** | SLCE | Hold-to-play mode. Hold one button to play from that column. Hold two buttons simultaneously to create a latched sub-loop between them. Press any button while latched to stop. |
| **SEQ** | SQNC | Step sequencer mode. Toggle grid columns on/off to define active steps. The sample is divided into equal slices and each active step triggers its slice in order, synced to the master clock. The STEP count is locked to the grid column count in SEQ mode. |
| **Step** | STEP | Slice-trigger one-shot. Each button press plays exactly the one slice it covers (based on current STEP count), from start to end, then stops automatically. Great for triggering individual drum hits or phrases without looping. |

#### Pitch Scale Maps

The SCALE selector in PTCH mode determines which semitone intervals are assigned across the grid columns. All values are in semitones relative to the root (0 = unison). On an 8-column grid, columns use the first 8 entries; on 16 columns, all 16 are used.

| Scale | Columns 1–8 | Columns 9–16 | Character |
|-------|------------|-------------|----------|
| **MAJOR** | 0, 2, 4, 5, 7, 9, 11, 12 | 14, 16, 17, 19, 21, 23, 24, 26 | Two octaves of the major scale (Ionian). Bright, familiar, covers the most common melodic intervals. |
| **MINOR** | 0, 2, 3, 5, 7, 8, 10, 12 | 14, 15, 17, 19, 20, 22, 24, 26 | Two octaves of the natural minor scale (Aeolian). Darker tonality, good for moody or cinematic textures. |
| **PENTA** | 0, 2, 4, 7, 9, 12, 14, 16 | 19, 21, 24, 26, 28, 31, 33, 36 | Major pentatonic spanning ~3 octaves. Wider intervals, no dissonance — everything sounds good together. |
| **CHORD** | 0, 1, 2, 3, 5, 7, 9, 10 | 12, 13, 14, 15, 17, 19, 21, 22 | Dense chromatic cluster in the lower range, spreading into wider intervals. Useful for building chord voicings and clusters. |
| **CHROM** | 0, 1, 2, 3, 4, 5, 6, 7 | 8, 9, 10, 11, 12, 13, 14, 15 | Pure chromatic — every semitone in order. Maximum pitch resolution, one octave per 12 columns. |

The **root offset** (PITCH spinner, −24 to +24 st) shifts every column’s pitch up or down by a fixed amount, effectively transposing the entire keyboard.

### UI Scale and View Options

The entire interface can be resized via **View → UI Scale** in the menu bar. Choose from 50% to 200% — the setting persists across sessions (saved to `data/ui_scale.txt`).

The **View** menu also provides two global options for floating control value overlays (the labels that appear when you drag a knob or fader):

- **Lock Control Displays** — keeps the value label visible on every knob and fader at all times.
- **Disable Control Displays** — hides all overlays entirely, including while dragging (overrides any latched displays).

You can also **right-click** any individual knob or fader to permanently latch or unlatch its own display independently of the global setting.

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Escape** | Exit MIDI learn mode |
| **Ctrl+Shift+S** | Stop all audio |
| **Ctrl+O** | Open preset file |
| **Ctrl+S** | Save preset file |
| **Ctrl+Q** | Quit |

### File Menu

| Action | Description |
|--------|-------------|
| **File → Open Preset File…** | Load a preset from any location on disk (same as Ctrl+O). |
| **File → Save Preset File…** | Export the current state as a standalone JSON preset file (same as Ctrl+S). |
| **File → Restart** | Reinitialize the audio engine and reload all settings — useful after changing audio device settings or recovering from an audio dropout. |
| **File → Stop All Audio** | Immediately stop all playing channels and pattern recorders. |
| **File → Quit** | Close mlrVp (same as Ctrl+Q). |

---

## Credits

mlrVp is a spiritual successor to mlrV2.2 by Trent Gill (galapagoose) and Michael Felix (%), itself a refactoring of Brian Crabtree's original mlr for the monome platform. The serialosc edition was maintained by Myles Borins (TheAlphaNerd).

## License

MIT License
