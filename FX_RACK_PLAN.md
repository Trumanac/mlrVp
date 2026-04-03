# FX Rack UI — Implementation Plan

## Status: ✅ FULLY IMPLEMENTED

All phases complete. The FX Rack is live and integrated into the application.

---

## Overview
The `EffectsView` was replaced with a skeuomorphic "Time-Capsule" FX Rack. Three independent
racks live behind **FX GROUP A** / **FX GROUP B** / **MASTER** vintage backlit tab buttons. Each rack is a
scrollable column of modular effect units that can be added, removed, and reordered.

**Limits**: Maximum 8 modules per rack, 20 total across all three groups.

---

## Architecture

### File Structure
```
mlrvp/gui/
  views/
    effects_view.py          # FX Rack view (Group A / Group B / Master tabs)
  widgets/
    rack_module_base.py      # Base class for all rack modules (1U=60px, 2U=120px)
    rack_container.py        # Scrollable rack with drag-drop, add/remove, reorder
  rack_modules/              # One file per effect module
    __init__.py              # Module registry (register_rack_module)
    digital_delay.py         # Déjà Vu Delay (1U)
    tape_delay.py            # Rewind (1U)
    bit_crusher.py           # Byte Me (1U)
    tube_overdrive.py        # Hot Snake (1U)
    multiband_filter.py      # Band Practice (2U, 5-band EQ)
    moog_filter.py           # The Ladder (1U, wood-cheek faceplate)
    phaser.py                # Snake Charmer (1U)
    chorus.py                # Parrot Talk (1U)
    flanger.py               # Jet Stream (1U)
    plate_spring_reverb.py   # Spring Fling (2U)
    hall_reverb.py           # Echo Chamber (2U)
    reel_to_reel.py          # Reel Deal (1U)
    cassette_tape.py         # Mixtape (1U)
    vhs.py                   # Tracking Shot (1U)
    vinyl.py                 # Wax Poetic (1U)
    compressor.py            # Thrust Issues — Sidechain Compressor (2U)
    glue_compressor.py       # Group Hug — Glue Bus Compressor (1U)
    dual_vu_meter.py         # Twin Peaks (1U)
    oscilloscope.py          # CRT:// Scope (1U)
mlrvp/audio/
  effects.py                 # DSP classes for all effects
mlrvp/config.py              # Dataclasses for all effect states
mlrvp/app.py                 # Signal wiring for all rack modules
```

### Signal Contract
`EffectsView` exposes:
- `delay_param_changed(str, object)`
- `tape_delay_param_changed(str, object)`
- `filter_a_param_changed(str, object)`
- `filter_b_param_changed(str, object)`
- `mod_param_changed(str, object)`
- `effect_route_changed(str, int)`
- `midi_map_toggled(bool)` — syncs with Setup → MIDI Learn

### Rack Groups

| Tab | Bus | Signal Path | Notes |
|-----|-----|-------------|-------|
| **FX GROUP A** | bus 1 | Per-group A FX chain | Delay, mod, lo-fi, dynamics, reverb |
| **FX GROUP B** | bus 2 | Per-group B FX chain | Same available modules as Group A |
| **MASTER** | bus 3 | Combined stereo output | Post-mix mastering chain |

### Rack Unit Heights
- **1U module** = 60 px
- **2U module** = 120 px
- **Rack rail** = 20 px left + 20 px right (`RACK_EAR_WIDTH = 20`)
- **Scrollbar**: Custom 8px dark QSS scrollbar on `RackContainer` — forces non-overlay rendering so the right ear is never covered. No viewport margin needed.

### Rack Ear Design (right ear)
- **Power toggle** (green `#33cc33` / gray `#444444` circle, upper half) — click to bypass/enable the effect. Emits `_enabled_param` signal. MIDI-mappable. State saved in preset.
- **Remove button** (red X, lower half) — click to remove module from rack.
- `_enabled_param` class attribute on each module defines the emitted param name (e.g. `phaser_enabled`, `chorus_enabled`; default `enabled`).
- `power_widget` property returns `self._right_ear` for MIDI registration.

---

## Module Registry (19 modules)

| Registry Name (menu display) | Class | Unit Height | DSP Status |
|------------------------------|-------|-------------|------------|
| Déjà Vu Delay (Digital Delay) | DigitalDelayModule | 1U | ✅ Wired |
| Rewind (Tape Delay) | TapeDelayModule | 1U | ✅ Wired |
| Byte Me (Bit Crusher) | BitCrusherModule | 1U | ✅ Wired |
| Hot Snake (Tube Overdrive) | TubeOverdriveModule | 1U | ✅ Wired |
| Band Practice (Multiband Filter) | MultibandFilterModule | 2U | ✅ Wired |
| The Ladder (Moog Filter) | MoogFilterModule | 1U | ✅ Wired |
| Snake Charmer (Phaser) | PhaserModule | 1U | ✅ Wired |
| Parrot Talk (Chorus) | ChorusModule | 1U | ✅ Wired |
| Jet Stream (Flanger) | FlangerModule | 1U | ✅ Wired |
| Spring Fling (Plate/Spring Reverb) | PlateSpringReverbModule | 2U | ✅ Wired |
| Echo Chamber (Hall Reverb) | HallReverbModule | 2U | ✅ Wired |
| Reel Deal (Reel-to-Reel) | ReelToReelModule | 1U | ✅ Wired |
| Mixtape (Cassette Tape) | MixtapeModule | 1U | ✅ Wired |
| Tracking Shot (VHS) | VHSModule | 1U | ✅ Wired |
| Wax Poetic (Vinyl) | VinylModule | 1U | ✅ Wired |
| Thrust Issues (Sidechain Compressor) | CompressorModule | 2U | ✅ Wired |
| Group Hug (Glue Compressor) | GlueCompressorModule | 1U | ✅ Wired |
| Twin Peaks (Dual VU Meter) | DualVUMeterModule | 1U | ✅ Wired (metering only) |
| CRT:// Scope (Oscilloscope) | OscilloscopeModule | 1U | ✅ Wired (display only) |

---

## MIDI Integration

- **MIDI MAP button** in the FX Rack header toggles MIDI mapping mode
- Green highlight border on all mappable controls when active
- Syncs bidirectionally with Setup → MIDI → Learn button
- New modules added while MIDI MAP is active automatically receive highlighting
- Click any highlighted control → move a MIDI CC → mapping created

---

## Performance Notes

- Pre-allocated `np.arange()` arrays in Chorus, Flanger, TapeDelay, and VinylProcessor DSP
- QPainter warning guards on all custom painting
- 512 samples @ 44100 Hz (11.6 ms audio callback)
- `math.fmod` phase wrapping on all LFO accumulators (TapeDelay, ReelToReel, CassetteTape, VHS, Vinyl)
- **Max effects**: 8 per rack, 20 total across all three groups (Group A + Group B + Master)

---

## Completed Phases

### Phase 1 — Foundation ✅
- RackModuleBase widget (rack ears, faceplate, enable/bypass, remove, MIDI highlight)
- RackContainer widget (QScrollArea, drag-drop reorder, add/remove menu)
- EffectsView rewrite (Group A/B tabs, merged signal routing)

### Phase 2 — Existing Effects ✅
- Digital Delay, Tape Delay, Multiband Filter, Moog Filter, Phaser, Chorus, Flanger

### Phase 3 — New Effects ✅
- Bit Crusher, Tube Overdrive, Plate/Spring Reverb, Hall/Room Reverb, Compressor
- Dual VU Meter, Oscilloscope (metering/visualization)

### Phase 4 — Integration & Polish ✅
- All signals wired in app.py for: Delay, Tape Delay, Multiband Filter (Band Practice), Moog Filter (The Ladder), Phaser, Chorus, Flanger
- Preset save/load includes full rack state for all 14 modules
- MIDI mapping mode integrated (MIDI MAP button + highlight overlay)
- Max effects limit enforced (8/rack, 12 total)
- Performance review completed
- Rack ear widened to 20px; viewport margin added (14px right)
- Power toggle on right rack ear — green/gray icon, MIDI-mappable, saved in preset
- Per-module `_enabled_param` routing (phaser_enabled, chorus_enabled, flanger_enabled, or `enabled`)
- Duplicate MIDI registration guard in `MidiMapManager.register_control()`
- `_register_fx_rack_mappable_controls()` extracted as reusable method, called on rack change
- `set_state()` signature relaxed to `*args, **kwargs` across all modules

### Phase 5 — Twin Peaks & Scope Improvements ✅
- **Twin Peaks VU modes**: VU (ballistic), PEAK (fast attack/slow decay), RMS (power average) — distinct ballistics per mode
- **Per-FX-bus metering**: VU meter and oscilloscope now read from their respective FX bus (A → FX1, B → FX2), not master output
- **Rack auto-routing**: `get_rack_sync_state()` reports module presence and preferred routes; engine syncs on rack change and startup

### Phase 6 — DSP Backends ✅ COMPLETE
All 19 modules have fully wired audio DSP implementations:
- **Byte Me** (`bit_crusher`) — vectorised zero-order hold + bit-depth quantisation
- **Hot Snake** (`tube_overdrive`) — asymmetric soft-clip with bias, drive, tone (biquad shelf), mix
- **Spring Fling** (`plate_reverb`) — Schroeder plate reverb (all-pass + comb network), pre-delay, damping LP, mix
- **Echo Chamber** (`hall_reverb`) — Freeverb-style hall/room/chamber/studio modes, diffusion, modulated comb network, mix
- **Reel Deal** (`reel_to_reel`) — saturation, wow/flutter, bias tone (peak shelf), speed-selectable noise floor
- **Mixtape** (`cassette_tape`) — soft-clip saturation, hiss, punch (transient shelf), warp delay, tone LP
- **Tracking Shot** (`vhs`) — wobble (chorus-style LFO), dropout noise bursts, chromatic aberration tone, noise
- **Wax Poetic** (`vinyl`) — crackle / pop scheduling, warmth (bass shelf + saturation), groove-wear HP loss, warped-record wow
- **Thrust Issues** (`compressor`) — RMS sidechain detector, VCA gain computer, HP sidechain filter, fast/slow program-dependent timing, stereo link
- **Group Hug** (`glue_compressor`) — peak/RMS glue compressor, attack/release selectors, switchable stepped sidechain HPF, stereo link
