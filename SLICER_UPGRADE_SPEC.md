# Slicer Upgrade — Product Specification

## Overview

Upgrade the sample slicer from an isolated preview widget into a cue-capable editing subsystem with its own audio output path, gain staging, persisted state, and modern chopping workflows.

**Architecture decision:** keep slicer cue playback *outside* the main `AudioEngine` ring-buffer path. The current engine is single-output and real-time-optimised; a dedicated `SlicerPreviewService` is lower risk for targeting a completely different hardware device. If a deeper engine-level cue bus is needed later, it can replace the service without changing the UI layer.

**Excluded from all phases:** full engine-integrated cue bus shared with performance channels, audience/cue crossfader, master-clock-synced dual-device playback, per-slice FX processing inside the cue path.

---

## Current Baseline

| Component | Location | Current behaviour |
|-----------|----------|-------------------|
| `SlicePanel` | `tools_view.py:349` | Interactive waveform editor, transient detection, FIT mode, BPM auto-detect, single slice export to `data/slices/` |
| Preview playback | `tools_view.py:665–683` | Fire-and-forget `sd.play()` using the main output device; no volume control, no channel mapping |
| Output device | `tools_view.py:651` | `set_output_device()` receives the main engine's device index from `app.py:2160`; no independent selection |
| Persistence | `config.py` / `manager.py` | No slicer-specific fields; output device is inherited from `AppConfig.output_device` |
| Slice library | `tools_view.py` (`SliceLibraryPanel`) | File list with drag-to-load and per-file delete; no metadata, no search |

---

## Phase 1 — Config & State Foundation

### Goal
Formalise slicer tool settings in `AppConfig` and the preset system so that UI work in later phases has a persisted backing store from day one.

### Deliverables

1. **New dataclass `SlicerState`** in `config.py`

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `cue_volume` | `float` | `1.0` | 0.0–2.0 preview gain (linear) |
| `cue_device` | `int` | `-1` | sounddevice output device index; -1 = system default |
| `cue_channel_offset` | `int` | `0` | first output channel on the cue device (0-based pair: 0 = ch 1/2, 2 = ch 3/4, …) |
| `cue_mode` | `int` | `0` | 0 = CUE ONLY, 1 = CUE + MAIN, 2 = OFF |
| `preview_loop` | `bool` | `True` | default loop state when previewing |
| `auto_chop_mode` | `int` | `0` | 0 = manual, 1 = transient, 2 = equal-division (future) |
| `naming_template` | `str` | `"{source}_slice{n:02d}"` | export filename template |

2. **New field on `AppConfig`**
```python
slicer: SlicerState = field(default_factory=SlicerState)
```

3. **Serialisation** — add `"slicer": { … }` block to `_serialize_config()`.

4. **Deserialisation** — add backward-compatible block to `apply_preset()`:
```python
slicer_data = cfg_data.get("slicer", {})
if slicer_data:
    for key in ["cue_volume", "cue_device", "cue_channel_offset",
                 "cue_mode", "preview_loop", "auto_chop_mode",
                 "naming_template"]:
        if key in slicer_data:
            _safe_setattr(config.slicer, key, slicer_data[key])
```

### UI states
*Phase 1 adds no new UI controls.* All values start at defaults and are only consumed once Phase 2 builds the cue UI. The `SlicePanel._loop_cb` initial state should read from `config.slicer.preview_loop` once the config exists.

### Failure cases
| Scenario | Expected behaviour |
|----------|--------------------|
| Old preset loaded (no `"slicer"` key) | `SlicerState()` defaults apply; no crash |
| Preset has unknown future slicer keys | `_safe_setattr` silently ignores them |
| `cue_device` value references a device index that no longer exists | Validated at use time (Phase 2); stored as-is in config |

### Acceptance criteria
- [ ] `SlicerState` dataclass exists with all listed fields and defaults.
- [ ] `AppConfig.slicer` is a `SlicerState` instance.
- [ ] `_serialize_config` outputs `"slicer"` dict with all fields.
- [ ] `apply_preset` restores slicer state from JSON.
- [ ] Loading a preset JSON that has no `"slicer"` key produces default `SlicerState` — no error.
- [ ] Loading a preset JSON with extra unknown slicer keys produces no error.
- [ ] Round-trip test: save preset → load preset → all slicer values match.

### Files to modify
| File | Change |
|------|--------|
| `config.py` | Add `SlicerState` dataclass + `slicer` field on `AppConfig` |
| `preset/manager.py` | Add serialisation block in `_serialize_config`, deserialisation block in `apply_preset` |

---

## Phase 2 — Cue Preview Service & UI

### Goal
Replace the fire-and-forget `sd.play()` call with a dedicated `SlicerPreviewService` that owns its own sounddevice output stream, supports a different hardware device from the main engine, applies cue gain, and maps to a selectable output channel pair. Add corresponding UI controls to `SlicePanel`.

### Deliverables

#### 2A — `SlicerPreviewService` (new module: `audio/slicer_preview.py`)

Responsibilities:
- Open / close a `sounddevice.OutputStream` targeting `cue_device` with the requested channel mapping.
- Accept a NumPy audio segment + loop flag; stream it through the callback.
- Apply `cue_volume` gain before writing to the output buffer.
- Expose `play(segment, sr, loop)`, `stop()`, `set_volume(float)`, `set_device(int, int)`, `is_playing -> bool`.
- Thread-safe: `play`/`stop` callable from the Qt GUI thread; callback runs on PortAudio C thread.
- Stream is opened lazily on first `play()` and torn down on `stop()` or device change; not kept alive when idle.

Internal design:
```
┌─────────────────────────────────────┐
│  SlicerPreviewService               │
│                                     │
│  _stream: sd.OutputStream | None    │
│  _buf: np.ndarray (pre-tiled loop)  │
│  _pos: int  (read cursor)           │
│  _volume: float                     │
│  _lock: threading.Lock              │
│  _device: int                       │
│  _channel_offset: int               │
│                                     │
│  callback(outdata, frames, …):      │
│    copy _buf[_pos:_pos+frames]      │
│    apply _volume in-place           │
│    advance _pos; stop if done       │
└─────────────────────────────────────┘
```

#### 2B — `SlicePanel` cue UI controls

New controls inserted as a **cue row** between the existing transport row and the slice controls row:

```
┌──────────────────────────────────────────────────────────────────┐
│  🔊 ──────────── │ Device: [Headphones (Realtek) ▼] │           │
│  CUE VOL  0–200% │ Ch: [1/2 ▼]  │ Mode: [CUE ONLY ▼] │ status│
└──────────────────────────────────────────────────────────────────┘
```

| Control | Widget | Binding |
|---------|--------|---------|
| CUE VOL | `HorizontalFader` (0–200, default 100) | `config.slicer.cue_volume` via `value / 100.0` |
| Device | `QComboBox` | `config.slicer.cue_device`; populated from `AudioEngine.get_devices()["output"]` |
| Ch | `QComboBox` | `config.slicer.cue_channel_offset`; items derived from selected device's `max_output_channels` |
| Mode | `QComboBox` | `config.slicer.cue_mode`; items: CUE ONLY (0), CUE + MAIN (1), OFF (2) |
| Status | `QLabel` | Shows device name when stream is open, error text on failure |

Signal wiring in `app.py`:
- `_populate_gui_devices()` also populates the slicer cue device combo.
- `_gui_audio_setting("output_device", …)` no longer auto-sets the slicer device; the slicer has its own selector now.
- Slicer cue device combo `currentIndexChanged` → update `config.slicer.cue_device` + call `preview_service.set_device(…)`.

### UI states

| State | Visual | Behaviour |
|-------|--------|-----------|
| **Idle** | Status shows selected device name or "No cue device". Volume fader enabled. Transport buttons normal. | No stream open. |
| **Playing** | Play button shows ⏸. Playhead animates in waveform. Status shows "Playing on {device}". | Stream open, callback streaming. |
| **Looping** | Same as Playing + Loop checkbox checked. | Cursor wraps at segment end. |
| **Device missing** | Status shows "⚠ Device not found — using default". Device combo highlights red. | Falls back to system default or stays silent depending on cue_mode. |
| **Device error** | Status shows "⚠ {error}". Transport buttons remain enabled. | Stream not opened; user can retry by pressing play again or selecting a different device. |
| **CUE OFF** | Volume fader greyed out. Play buttons still work (play through main only if CUE + MAIN; silent if CUE ONLY). | No cue stream opened. Main engine output unaffected. |
| **CUE + MAIN** | Both cue stream and `sd.play()` on main device fire. | Two simultaneous playbacks: one on cue device (with cue gain), one on main device (at unity). |

### Failure cases

| Scenario | Expected behaviour |
|----------|--------------------|
| Selected cue device is unplugged before play | `sd.OutputStream` constructor raises `PortAudioError`; catch it, show status "⚠ Device unavailable", do not crash, do not affect main engine |
| Selected cue device is unplugged during play | PortAudio callback receives error status; service calls `stop()`, shows "⚠ Device lost" in status |
| `cue_channel_offset` exceeds device channel count | Clamp to 0 and show "⚠ Ch pair reset to 1/2" in status |
| User changes device while playing | `stop()` current stream → open new stream on new device → `play()` same segment from beginning |
| User changes volume while playing | `_volume` updated atomically; next callback block picks it up |
| Main engine not running but user previews in CUE + MAIN mode | Cue stream plays normally; main-device playback uses `sd.play()` (fire-and-forget, same as today). No dependency on engine state. |
| `play()` called with empty segment | Early return, status shows "Nothing to preview" |
| Rapid retriggering (play pressed 10× in 1 second) | Each `play()` calls `stop()` first; only one stream is ever open |

### Acceptance criteria
- [ ] `SlicerPreviewService` opens a stream on a device different from `config.output_device` and audio is heard on that device.
- [ ] Changing cue volume fader while playing audibly changes cue output level within one block (<12 ms).
- [ ] Selecting channel pair 3/4 on a 4+ channel device routes audio to physical outputs 3/4, not 1/2.
- [ ] Unplugging the cue device mid-play stops playback cleanly; status shows warning; main engine output is unaffected.
- [ ] Switching cue device while playing restarts preview on the new device.
- [ ] CUE + MAIN mode plays audio on both the cue device and the main device simultaneously.
- [ ] CUE OFF mode silences cue output; play buttons do nothing unless CUE + MAIN is selected.
- [ ] Loop checkbox toggles looping; uncheck mid-play → playback stops at segment end.
- [ ] Preset save/load round-trips `cue_device`, `cue_volume`, `cue_channel_offset`, `cue_mode`.
- [ ] No audio glitches, xruns, or GIL contention on the main engine while the slicer cue stream is active.

### Files to modify / create

| File | Change |
|------|--------|
| `audio/slicer_preview.py` | **NEW** — `SlicerPreviewService` class |
| `gui/views/tools_view.py` | Add cue row UI; replace `sd.play()` calls in `_play_from` with `preview_service.play()` |
| `app.py` | Wire slicer cue device combo population; stop auto-mirroring main device into slicer; instantiate `SlicerPreviewService` and pass to `SlicePanel` |
| `config.py` | (already done in Phase 1) |
| `preset/manager.py` | (already done in Phase 1) |

---

## Phase 3 — Slice Audition & Batch Workflow

### Goal
Layer the highest-value editing workflows on top of the cue path: fast per-slice audition without needing to export, multi-slice auto-chop, and batch export with naming templates. This phase transforms the slicer from a one-at-a-time export tool into a modern sample-chopping workstation.

### Deliverables

#### 3A — Per-slice audition

| Feature | Behaviour |
|---------|-----------|
| **Click-to-audition on transient markers** | Single-click a transient marker in the waveform → preview the region from that transient to the next transient (or end of file) through the cue path. If already playing, retrigger immediately. |
| **Audition current selection** (existing ▶ Sel, enhanced) | Now routes through `SlicerPreviewService` with cue volume, device, and channel pair. Playhead animation unchanged. |
| **Keyboard shortcut: audition slice under cursor** | Hover mouse over waveform, press `Space` → preview the transient-bounded region under the cursor. No selection change needed. |
| **Keyboard shortcut: cycle slices** | `←` / `→` arrow keys step through auto-detected transient regions and auto-audition each one. Selection highlights the current region. |
| **Keyboard shortcut: audition from playhead** | `Enter` → preview from current playhead position to end of selection (or end of file). |

New internal model: `_transient_regions: list[tuple[int, int]]` — precomputed `(start_sample, end_sample)` pairs derived from `_transients` array. Updated whenever `_detect_transients()` is called or the user modifies the auto-chop parameters.

#### 3B — Auto-chop modes

Add a **chop section** below the transport row (collapsible or in a sub-group):

```
┌──────────────────────────────────────────────────────────────────┐
│  CHOP: [Manual ▼] │ Slices: 8 │ Sensitivity: ────── │ [CHOP]  │
└──────────────────────────────────────────────────────────────────┘
```

| Control | Widget | Behaviour |
|---------|--------|-----------|
| CHOP mode | `QComboBox` | Manual (default, current behaviour), Transient, Equal, BPM Grid |
| Slices count | `QSpinBox` 1–128 | Only active in Equal mode; sets number of equal divisions |
| Sensitivity | `HorizontalFader` 0–100 | Only active in Transient mode; maps to `_detect_transients()` threshold multiplier (low = more transients, high = fewer) |
| CHOP button | `QPushButton` | Runs the selected algo and populates `_slice_regions` |

**Transient mode:** Runs `_detect_transients()` with the user's sensitivity setting. Each transient-to-transient gap becomes a slice region. Regions are shown as alternating-shaded bands in the waveform.

**Equal mode:** Divides the full file (or current selection, if active) into N equal segments.

**BPM Grid mode:** Divides at beat boundaries using the BPM from the spinbox / auto-detect. Slice boundaries align to 1/4, 1/8, or 1/16 note grid (sub-selector or default to 1/4).

#### 3C — Batch export

| Feature | Behaviour |
|---------|-----------|
| **EXPORT ALL button** | Appears next to SLICE + SAVE when `_slice_regions` is populated. Exports every region as a separate WAV using the naming template from `config.slicer.naming_template`. FIT-mode resampling is applied per-slice if FIT is checked. |
| **Naming template** | Editable field replacing the current `_name_edit`. Supports `{source}`, `{n}` (1-based index), `{n:02d}` (zero-padded), `{bpm}`, `{bars}`. Default: `"{source}_slice{n:02d}"`. Persisted via `SlicerState.naming_template`. |
| **Progress feedback** | Status label shows `"Exported 5/16 slices…"` during batch export. Export runs on a background thread to avoid GUI freeze. |
| **Slice library auto-refresh** | `SliceLibraryPanel.refresh()` called once after all exports complete. |

### UI states

| State | Visual | Behaviour |
|-------|--------|-----------|
| **Manual chop (default)** | Slices count and Sensitivity greyed out. CHOP button hidden. Single green/red markers visible. EXPORT ALL hidden. | Behaves exactly like current slicer. |
| **Transient chop active** | Waveform shows alternating shaded bands at each transient region. Region count shown in status. Sensitivity fader active. EXPORT ALL visible. | Click any band to audition it. Drag band edges to fine-tune. |
| **Equal chop active** | Waveform shows N equal bands. Slices count spinner active. EXPORT ALL visible. | Click any band to audition. |
| **BPM Grid chop active** | Waveform shows beat-grid bands. Bar subdivision selector active. EXPORT ALL visible. | Click any band to audition. |
| **Exporting** | EXPORT ALL button text changes to "Exporting…" and is disabled. Status shows progress. Other controls remain interactive. | Background thread writes WAVs. |
| **Export complete** | EXPORT ALL re-enables. Status shows "Exported N slices to data/slices/". Library refreshes. | Files appear in slice library. |
| **Audition cycling** | Current region highlighted with ACCENT_GOLD border. Arrow keys step through regions. | Audition auto-plays each region on step. |

### Failure cases

| Scenario | Expected behaviour |
|----------|--------------------|
| Transient detection finds 0 transients | Status shows "No transients detected — try lowering sensitivity". EXPORT ALL stays hidden. |
| Equal chop with N > total frames | Clamp N to reasonable minimum (each slice ≥ 64 samples). Show warning. |
| Naming template produces duplicate filenames | Append `_dup1`, `_dup2` suffix before `.wav` extension. Show warning in status. |
| Background export interrupted (file write error) | Skip failed slice, continue with remaining. Show "⚠ {N} slices failed — see status" at end with filenames. |
| Disk full during batch export | Catch `OSError`, stop export, show "⚠ Disk full — {M} of {N} exported". Do not corrupt partially written files (use temp-then-rename pattern). |
| User changes chop mode while audition is playing | Stop current audition. Recompute regions for new mode. No crash. |

### Acceptance criteria
- [ ] Clicking a transient marker band auditions exactly that region through the cue device.
- [ ] `←`/`→` keys cycle through transient regions and auto-play each; selection highlights update.
- [ ] `Space` over the waveform auditions the region under the cursor without changing selection.
- [ ] Transient sensitivity slider produces visibly fewer/more detected regions in real time.
- [ ] Equal-division mode produces exactly N equal regions when N ≤ total frames / 64.
- [ ] BPM Grid mode aligns slice boundaries to beat positions at the detected BPM.
- [ ] EXPORT ALL writes N WAV files to `data/slices/` with names matching the template.
- [ ] FIT mode resampling is applied per-slice during batch export when FIT is checked.
- [ ] Batch export does not freeze the GUI; progress is shown in the status label.
- [ ] Slice library refreshes automatically after batch export.
- [ ] Naming template round-trips through preset save/load.
- [ ] Existing single-slice manual workflow (SLICE + SAVE) is completely unchanged.

### Files to modify / create

| File | Change |
|------|--------|
| `gui/views/tools_view.py` | Add chop row UI; add `_slice_regions` model; add audition key handlers; add EXPORT ALL button; add batch export logic (with `QThread` or `threading.Thread`) |
| `gui/views/tools_view.py` (`SliceWaveformWidget`) | Paint alternating region bands; handle click-to-audition on regions; handle region-edge drag |
| `config.py` | `SlicerState.auto_chop_mode`, `SlicerState.naming_template` already added in Phase 1 |
| `audio/slicer_preview.py` | No change — audition uses existing `play()` |

---

## Phase 4 — Precision Editing, Library Upgrade & Documentation

### Goal
Add the remaining editing tools that make the slicer competitive with standalone choppers, upgrade the slice library from a flat file list to a metadata-aware browser, and document the full cue workflow.

### Deliverables

#### 4A — Precision editing tools

| Feature | Widget / shortcut | Behaviour |
|---------|-------------------|-----------|
| **Numeric start entry** | `QDoubleSpinBox` (seconds, 3 decimals) | Two-way bound to green marker position. Typing a value moves the marker; dragging the marker updates the spinbox. |
| **Numeric end entry** | `QDoubleSpinBox` (seconds, 3 decimals) | Same as above for red marker. |
| **Numeric duration display** | `QLabel` (read-only) | `end - start`, updates live. Already exists as `_dur_label` — promoted to a `QDoubleSpinBox` so the user can set a desired duration and have the end marker auto-adjust. |
| **Zero-crossing snap** | `QPushButton "ZX"` + auto-apply option | Snaps both markers to nearest zero-crossing within ±64 samples. Uses existing `_snap_to_zero_crossing()`. Toggle-able: when enabled, every marker drag auto-snaps on release. |
| **BPM-grid snap** | `QPushButton "GRID"` toggle | When active, marker drags and numeric entries snap to nearest beat subdivision (1/4, 1/8, 1/16 — separate selector or uses bar combo). Uses `_get_target_bpm()` for grid math. |
| **Marker nudge** | `Shift+←` / `Shift+→` | Nudge active marker ±1 ms. `Ctrl+Shift+←/→` nudges ±1 sample. Active marker = whichever was last dragged or clicked. |
| **Undo / redo** | `Ctrl+Z` / `Ctrl+Shift+Z` | Stack of `(start_frac, end_frac)` tuples, max depth 50. Every marker move, auto-snap, or numeric entry pushes to the stack. |

#### 4B — Slice library upgrade

Current `SliceLibraryPanel` is a scrollable list of `_SliceLibraryRow` widgets. Upgrade to:

| Feature | Behaviour |
|---------|-----------|
| **Metadata sidecar** | When a slice is exported, write a companion `.json` alongside the WAV: `{ "source": "…", "bpm": 120.0, "bars": 2, "fit": true, "template": "…", "exported_at": "ISO-8601" }`. |
| **Search / filter** | `QLineEdit` at top of library panel. Filters rows by filename substring match. |
| **Sort** | `QComboBox`: Name (A-Z), Name (Z-A), Newest, Oldest. Default: Newest. |
| **BPM badge** | Each `_SliceLibraryRow` shows a small BPM label if metadata exists. |
| **Quick-load to channel** | Right-click slice → context menu → "Load to channel N" (1–16). Calls `app.load_sample(channel_idx, filepath)`. |
| **Batch delete** | Multi-select with `Ctrl+Click` → Delete button removes all selected. Confirmation dialog lists filenames. |

#### 4C — Documentation

Update `README.md` section "Slicing Samples with FIT Mode" and add:
- **Cue output setup** — how to select a different device, channel pair, and cue mode.
- **Auto-chop workflow** — Transient / Equal / BPM Grid modes and batch export.
- **Keyboard shortcuts reference table** for the slicer.
- **Platform notes** — Windows caveats for separate-device monitoring (WASAPI exclusive mode, ASIO multi-client limitations).

Update `main_window.py` help dialog with a brief "Slicer & Cue" section.

### UI states

| State | Visual | Behaviour |
|-------|--------|-----------|
| **Numeric entry active** | Spinbox has focus; waveform marker shows dashed outline at proposed position. | Typing moves marker live. Pressing Enter commits and pushes undo. |
| **ZX snap enabled** | ZX button highlighted ACCENT_GOLD. | Every marker release auto-snaps to zero-crossing. |
| **Grid snap enabled** | GRID button highlighted ACCENT_BLUE. | Every marker release and numeric entry snaps to beat grid. |
| **Undo available** | No visual indicator; `Ctrl+Z` is silently available. | Restores previous `(start, end)` pair. Playback stops if marker positions change. |
| **Redo available** | Same. | Moves forward in undo stack. |
| **Search active** | Library shows filtered subset. Clear button (✕) resets filter. | Rows that don't match are hidden, not deleted. |
| **Multi-select active** | Selected rows have ACCENT_BLUE background. Delete button shows count. | `Ctrl+Click` toggles individual rows. `Shift+Click` selects range. |

### Failure cases

| Scenario | Expected behaviour |
|----------|--------------------|
| Numeric entry value > file duration | Clamp to file duration. Show brief "clamped" tooltip. |
| Numeric start > numeric end | Swap start and end automatically. |
| Zero-crossing snap finds no crossing within window | Leave marker at current position. Show "No zero-crossing nearby" tooltip. |
| Undo stack empty | `Ctrl+Z` is a no-op. |
| Metadata sidecar file missing for old slices | Library row shows no BPM badge; search still works on filename. |
| Quick-load to channel that already has a sample | Normal sample-load behaviour (replaces existing sample with confirmation if the channel is playing). |
| Batch delete with files open in another process | Show "⚠ Could not delete {filename}" per failed file; delete the rest. |

### Acceptance criteria
- [ ] Numeric start/end spinboxes are two-way bound to waveform markers; editing one updates the other.
- [ ] Duration spinbox adjusts end marker to match desired duration.
- [ ] ZX snap moves markers to true zero-crossings; toggling off restores free drag.
- [ ] Grid snap aligns markers to beat subdivisions at the current BPM.
- [ ] `Shift+Arrow` nudges active marker by 1 ms; `Ctrl+Shift+Arrow` by 1 sample.
- [ ] Undo/redo restores and re-applies marker positions; stack depth ≤ 50.
- [ ] Metadata `.json` written alongside every exported WAV; contains source, BPM, bars, fit flag, timestamp.
- [ ] Library search filters rows by substring; clearing search restores full list.
- [ ] Library sort by name and date works correctly.
- [ ] Right-click → "Load to channel N" loads the WAV into the specified channel.
- [ ] Multi-select + delete removes selected files from disk and library.
- [ ] `README.md` documents cue output setup, auto-chop workflow, keyboard shortcuts, and platform notes.
- [ ] `main_window.py` help dialog includes Slicer & Cue section.

### Files to modify / create

| File | Change |
|------|--------|
| `gui/views/tools_view.py` | Add numeric start/end/duration spinboxes, ZX/GRID toggle buttons, undo/redo stack, keyboard shortcut handlers |
| `gui/views/tools_view.py` (`SliceWaveformWidget`) | Marker drag snap logic for ZX and Grid modes |
| `gui/views/tools_view.py` (`SliceLibraryPanel`) | Add search input, sort combo, metadata badge, context menu, multi-select, batch delete |
| `gui/views/tools_view.py` (`_SliceLibraryRow`) | Add BPM badge label, multi-select highlighting |
| `gui/views/tools_view.py` (`SlicePanel._do_slice`) | Write metadata `.json` alongside WAV on export |
| `app.py` | Wire "Load to channel N" context menu action to existing sample-load flow |
| `README.md` | Add cue output, auto-chop, shortcuts, platform notes sections |
| `gui/main_window.py` | Add "Slicer & Cue" section to help dialog |

---

## Cross-Phase Verification Matrix

| Test scenario | Phases | Method |
|--------------|--------|--------|
| Old preset (no `"slicer"` key) loads without error | 1+ | Unit test: load JSON without slicer key, assert defaults |
| New preset round-trips all slicer fields | 1+ | Unit test: save → load → compare |
| Cue plays on different device from main engine | 2+ | Manual: USB interface for main, laptop jack for cue |
| Cue device unplugged mid-play, main engine unaffected | 2 | Manual: unplug cue device, verify main output continues |
| Rapid retrigger (10× play in 1 sec) | 2+ | Manual stress test |
| Volume change during playback | 2+ | Manual: move fader while playing |
| Channel pair 3/4 routes to correct physical outputs | 2 | Manual: 4-output interface |
| Transient auto-chop produces sensible regions | 3 | Manual: load a drum break, verify slices at hits |
| Equal-division chop produces exactly N regions | 3 | Manual + assert in code |
| Batch export writes N files, library refreshes | 3 | Manual: export, check `data/slices/` |
| FIT resampling applied per-slice in batch export | 3 | Manual: verify all exported slices have identical frame count |
| Naming template with `{source}`, `{n:02d}`, `{bpm}` | 3 | Unit test: template rendering |
| Disk-full during batch export | 3 | Simulated: small RAM disk |
| Numeric entry clamps to file range | 4 | Manual: type out-of-range value |
| ZX snap + Grid snap toggle independently | 4 | Manual: enable each, drag markers |
| Undo/redo across marker drags and numeric edits | 4 | Manual: perform 5+ edits, undo all, redo all |
| Metadata sidecar written and read correctly | 4 | Unit test: export, read JSON, verify fields |
| Library search filters and clears correctly | 4 | Manual: type query, verify filter, clear |
| Multi-select batch delete | 4 | Manual: select 3 slices, delete, verify removal |
| No regressions: transient detection, FIT mode, BPM override, slice naming, library refresh, playhead animation | All | Regression suite after each phase |

---

## Appendix A — Keyboard Shortcuts Reference (after all phases)

| Shortcut | Scope | Action |
|----------|-------|--------|
| `Space` | Waveform hover | Audition region under cursor |
| `Enter` | Waveform | Play from playhead to end of selection |
| `←` / `→` | Waveform | Step to previous / next transient region and audition |
| `Shift+←` / `Shift+→` | Waveform | Nudge active marker ±1 ms |
| `Ctrl+Shift+←` / `Ctrl+Shift+→` | Waveform | Nudge active marker ±1 sample |
| `Ctrl+Z` | SlicePanel | Undo last marker/selection change |
| `Ctrl+Shift+Z` | SlicePanel | Redo |
| `Ctrl+E` | SlicePanel | Export all (batch) — only when regions are populated |
| `+` / `-` | Waveform | Zoom in / out (existing scroll-wheel behaviour extended) |

---

## Appendix B — `SlicerPreviewService` API summary

```python
class SlicerPreviewService:
    def __init__(self) -> None: ...
    def play(self, segment: np.ndarray, sr: int, *,
             loop: bool = False,
             device: int = -1,
             channel_offset: int = 0,
             volume: float = 1.0) -> None: ...
    def stop(self) -> None: ...
    def set_volume(self, volume: float) -> None: ...
    def set_device(self, device: int, channel_offset: int = 0) -> None: ...
    @property
    def is_playing(self) -> bool: ...
    @property
    def playback_progress(self) -> float: ...   # 0.0–1.0
    def close(self) -> None: ...                 # release all resources
```

---

## Appendix C — Slice metadata sidecar schema

```json
{
  "version": 1,
  "source": "drums_break_170bpm.wav",
  "bpm": 170.0,
  "bars": 2,
  "fit": true,
  "chop_mode": "transient",
  "slice_index": 3,
  "total_slices": 16,
  "naming_template": "{source}_slice{n:02d}",
  "exported_at": "2026-04-13T14:22:08Z"
}
```

Filename: same stem as the WAV + `.meta.json` (e.g. `drums_break_170bpm_slice03.meta.json`).

---

## Appendix D — Phase dependency graph

```
Phase 1 ─── Config & State Foundation
   │
   ▼
Phase 2 ─── Cue Preview Service & UI
   │
   ├──▶ Phase 3 ─── Slice Audition & Batch Workflow
   │
   └──▶ Phase 4A ── Precision Editing (can run in parallel with 3)
                │
                ▼
           Phase 4B ── Library Upgrade (depends on 3C metadata)
                │
                ▼
           Phase 4C ── Documentation (depends on all above)
```

Phase 3 and Phase 4A can be developed in parallel once Phase 2 is stable. Phase 4B depends on the metadata sidecar from Phase 3C. Phase 4C is last because it documents the final feature set.
