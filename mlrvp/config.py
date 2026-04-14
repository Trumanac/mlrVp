"""Global configuration and constants for mlrVp."""

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
import json


class PlaybackMode(IntEnum):
    LOOP = 0       # forward loop (was NORMAL)
    REVERSE = 1    # reverse playback with loop
    ONESHOT = 2    # play once then stop
    SLICE = 3      # hold-to-play, supports 2-button slice loop
    SEQ = 4        # step sequencer — grid buttons toggle steps, sample triggers per step
    PITCH = 5      # pitched one-shot — each button plays the sample at a different pitch
    STEP = 6       # play the slice under the pressed button once then stop


class QuantizeMode(IntEnum):
    NONE = 0
    QUARTER = 1      # 1/4 note
    EIGHTH = 2       # 1/8 note
    SIXTEENTH = 3    # 1/16 note
    BAR = 4          # 1 bar


MAX_ROWS = 16
MAX_COLUMNS = 16
NUM_GROUPS = 4
NUM_PATTERN_RECORDERS = 4
NUM_PRESETS = 32
NUM_SETLIST_SLOTS = 32
SAMPLE_RATE = 44100
BUFFER_SIZE = 512

# Default group colors — exact RGB from mixer.maxpat bpatcher args
DEFAULT_GROUP_COLORS = [
    (0.87, 0.76, 0.38),   # Group 1 — golden/amber
    (0.51, 0.91, 0.46),   # Group 2 — green
    (0.91, 0.51, 0.45),   # Group 3 — salmon/red
    (0.25, 0.71, 0.80),   # Group 4 — teal/cyan
]

# Grid LED brightness levels
LED_OFF = 0
LED_DIM = 4
LED_MED = 8
LED_BRIGHT = 15

# Pitch mode scale definitions — each list has 16 semitone offsets,
# sliced to [:grid_columns] at runtime for 8/16/etc layouts.
PITCH_SCALE_NAMES = ["MAJOR", "MINOR", "PENTA", "CHORD", "CHROM"]
PITCH_SCALE_MAPS: dict[int, list[int]] = {
    0: [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24, 26],        # MAJOR — 2 octaves diatonic
    1: [0, 2, 3, 5, 7, 8, 10, 12, 14, 15, 17, 19, 20, 22, 24, 26],        # MINOR — natural minor
    2: [0, 2, 4, 7, 9, 12, 14, 16, 19, 21, 24, 26, 28, 31, 33, 36],       # PENTA — pentatonic 3 oct
    3: [0, 1, 2, 3, 5, 7, 9, 10, 12, 13, 14, 15, 17, 19, 21, 22],         # CHORD — common roots ×2 oct
    4: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],            # CHROM — chromatic
}


@dataclass
class ChannelState:
    """State for a single channel / row."""
    speed: float = 1.0
    normal_speed: float = 1.0  # BPM-calculated speed; preserved across SEQ mode switches
    mode: PlaybackMode = PlaybackMode.LOOP
    slave: bool = False
    filename: str = ""
    gain: float = 1.0
    group: int = 0
    reverse: bool = False
    steps: int = 8
    transpose: int = 0
    octave: int = 0
    sel_start: float = 0.0
    sel_end: float = 1.0
    mute: bool = False
    attack: float = 0.005
    release: float = 0.01
    pitch: float = 1.0
    velocity: float = 1.0
    filter_val: float = 0.0  # -1 to 1, 0 = bypass, <0 = LP, >0 = HP
    pitch_scale: int = 0     # index into PITCH_SCALE_MAPS (0=MAJOR .. 4=CHROM)
    pitch_root: int = 0      # semitone offset for PITCH mode base note (-24 to +24)
    pitch_hold: bool = False  # True = loop in PITCH mode, False = one-shot
    playing: bool = False
    playback_pos: float = 0.0


@dataclass
class GroupState:
    """State for a mixer group."""
    volume: float = 1.0
    send: float = 0.0
    mute: bool = False
    route: int = 0  # 0 = OFF, 1 = FX1, 2 = FX2, 3 = FX3, 4 = FX4
    fx_route: int = 0  # 0 = OFF, 1 = FX1, 2 = FX2, 3 = FX3, 4 = FX4


@dataclass
class DelayState:
    """State for the digital delay effect."""
    enabled: bool = True
    sync_time: int = 3       # beat division index
    ms_time: float = 0.0     # manual ms time
    time_mode: int = 0       # 0 = sync, 1 = ms
    fb_attack: float = 67.0
    fb_release: float = 67.0
    fb_range_low: float = 42.0
    fb_range_high: float = 90.0
    filter_lp: bool = True
    filter_hp: bool = True
    filter_freq_lp: float = 7165.0
    filter_freq_hp: float = 311.0
    gain: float = 0.7
    fx_route: int = 1        # 0=OFF, 1=FX1, 2=FX2


@dataclass
class FilterState:
    """State for filter A (SSL-style EQ with LP/HP filters, 4 EQ bands)."""
    enabled: bool = True
    lp_enabled: bool = False
    hp_enabled: bool = False
    lp_freq: float = 8000.0    # Hz
    hp_freq: float = 200.0     # Hz
    lp_q: float = 0.5          # Q factor 0-1 for lowpass
    hp_q: float = 0.5          # Q factor 0-1 for highpass
    # LF band
    lf_enabled: bool = False
    lo_mid_gain: float = 0.5   # 0-1 mapped to -12..+12 dB
    lo_mid_freq: float = 400.0
    lo_mid_q: float = 0.5
    # LMF band
    mid_gain: float = 0.5
    mid_freq: float = 1000.0
    mid_q: float = 0.5
    # HMF band
    hi_mid_gain: float = 0.5
    hi_mid_freq: float = 3500.0
    hi_mid_q: float = 0.5
    # HF band
    hf_enabled: bool = False
    # Drive + output
    drive: float = 0.0         # 0-1
    output_gain: float = 0.5   # 0-1 mapped to -12..+12 dB
    fx_route: int = 1          # 0=OFF, 1=FX1, 2=FX2


@dataclass
class FilterBState:
    """State for filter B (Moog-style ladder sweep filter)."""
    enabled: bool = True
    cutoff: float = 8000.0     # Hz
    resonance: float = 0.3     # 0-1 (Moog ladder resonance)
    drive: float = 0.0         # 0-1 (input saturation)
    fx_route: int = 2          # 0=OFF, 1=FX1, 2=FX2


@dataclass
class TapeDelayState:
    """State for the Reel Space tape echo (Space Echo style)."""
    enabled: bool = True
    sync_time: int = 5         # beat division index
    ms_time: float = 375.0     # manual ms time
    time_mode: int = 0         # 0 = sync, 1 = ms
    feedback: float = 0.45     # 0-1 (intensity / repeat)
    wow_flutter: float = 0.3   # 0-1 (tape modulation depth)
    saturation: float = 0.4    # 0-1 (tape saturation amount)
    tone: float = 0.6          # 0-1 (0=dark, 1=bright)
    gain: float = 0.6          # wet gain (echo volume)
    fx_route: int = 2          # 0=OFF, 1=FX1, 2=FX2
    # Reel Space multi-head mode (0-6 = head combos, like RE-201 modes)
    head_mode: int = 0         # 0=H1, 1=H2, 2=H3, 3=H1+H2, 4=H1+H3, 5=H2+H3, 6=H1+2+3
    spring_mix: float = 0.0    # 0-1: built-in spring reverb amount


@dataclass
class ModState:
    """State for modulation effects."""
    # Phaser
    phaser_enabled: bool = False
    phaser_rate: float = 0.5    # Hz
    phaser_depth: float = 0.5
    phaser_feedback: float = 0.3
    # Chorus
    chorus_enabled: bool = False
    chorus_rate: float = 1.0    # Hz
    chorus_depth: float = 0.5
    chorus_mix: float = 0.5
    # Flanger
    flanger_enabled: bool = False
    flanger_rate: float = 0.3   # Hz
    flanger_depth: float = 0.5
    flanger_feedback: float = 0.3
    flanger_mix: float = 0.5


@dataclass
class BitCrusherState:
    """State for the bit crusher effect."""
    enabled: bool = True
    bit_depth: int = 12         # 1-32
    sample_rate_reduction: float = 0.5  # 0-1 (1 = max reduction)
    mix: float = 0.5
    fx_route: int = 1


@dataclass
class TubeOverdriveState:
    """State for the tube/overdrive effect."""
    enabled: bool = True
    drive: float = 0.3          # 0-1
    tone: float = 0.5           # 0-1
    mix: float = 0.5
    tube_bias: float = 0.5      # 0-1
    fx_route: int = 1


@dataclass
class ReelToReelState:
    """State for the studio reel-to-reel tape effect."""
    enabled: bool = True
    tape_speed: int = 2         # 0=3¾ips, 1=7½ips, 2=15ips, 3=30ips
    saturation: float = 0.35    # 0-1
    flutter: float = 0.2        # 0-1
    bias: float = 0.5           # 0-1
    tone: float = 0.6           # 0-1
    mix: float = 0.5
    fx_route: int = 1


@dataclass
class CassetteTapeState:
    """State for the home cassette tape effect."""
    enabled: bool = True
    saturation: float = 0.5     # 0-1
    hiss: float = 0.3           # 0-1
    punch: float = 0.5          # 0-1
    warp: float = 0.2           # 0-1 (tape speed wobble)
    tone: float = 0.55          # 0-1
    mix: float = 0.5
    fx_route: int = 1


@dataclass
class VHSState:
    """State for the VHS tape degradation effect."""
    enabled: bool = True
    wobble: float = 0.4         # 0-1 (tracking wobble)
    dropout: float = 0.2        # 0-1 (signal dropout)
    worn: float = 0.3           # 0-1 (worn tape character)
    noise: float = 0.25         # 0-1 (static/noise)
    mix: float = 0.5
    fx_route: int = 1


@dataclass
class VinylState:
    """State for the vinyl record character effect."""
    enabled: bool = True
    crackle: float = 0.3        # 0-1
    pops: float = 0.2           # 0-1
    warmth: float = 0.5         # 0-1
    wear: float = 0.3           # 0-1 (groove wear)
    mix: float = 0.5
    fx_route: int = 1


@dataclass
class PlateReverbState:
    """State for plate/spring reverb."""
    enabled: bool = True
    decay: float = 0.5          # 0-1
    damping: float = 0.5        # 0-1
    pre_delay: float = 20.0     # ms
    mix: float = 0.3
    fx_route: int = 2


@dataclass
class HallReverbState:
    """State for hall/room/chamber reverb."""
    enabled: bool = True
    room_size: float = 0.5      # 0-1
    decay: float = 0.6          # 0-1
    diffusion: float = 0.7      # 0-1
    mix: float = 0.3
    room_type: int = 0          # 0=Hall, 1=Room, 2=Chamber, 3=Studio
    fx_route: int = 2


@dataclass
class CompressorState:
    """State for sidechain-capable compressor (API 2500-style with THRUST circuit)."""
    enabled: bool = True
    threshold: float = -20.0    # dB
    ratio: float = 4.0          # :1
    attack: float = 10.0        # ms
    release: float = 100.0      # ms
    makeup_gain: float = 0.0    # dB
    sidechain_source: int = 0   # 0=Self, 1-4=Group 1-4
    sc_hp_freq: float = 150.0   # Hz — THRUST detect HP frequency
    thrust_enabled: bool = True # THRUST sidechain HP shelf on/off
    fx_route: int = 1


@dataclass
class GlueCompressorState:
    """State for SSL G-Bus style glue compressor."""
    enabled: bool = True
    threshold: float = 0.0      # dB, range -30 to +15
    ratio_idx: int = 1          # 0=2:1, 1=4:1, 2=10:1
    attack_idx: int = 2         # 0=0.1ms 1=0.3ms 2=1ms 3=3ms 4=10ms 5=30ms
    release_idx: int = 1        # 0=0.1s 1=0.3s 2=0.6s 3=Auto
    makeup_gain: float = 0.0    # dB, 0-15
    hpf_idx: int = 0            # 0=OFF, 1=30Hz, 2=60Hz, 3=105Hz, 4=125Hz, 5=185Hz
    fx_route: int = 1


@dataclass
class EtherDriftState:
    """State for the Ether Drift bipolar infinite phaser effect."""
    enabled: bool = True
    rate: float = 0.2           # 0-1 knob (maps to 0.05–3 Hz)
    depth: float = 0.65         # 0-1 sweep range fraction
    feedback: float = 0.3       # 0-1 resonance
    spread: float = 0.5         # 0-1 stereo spread (RISE↑→L, FALL↓→R)
    route: int = 0              # 0=BOTH, 1=UP only, 2=DOWN only
    sync: bool = True           # lock RISE and FALL to same rate
    mix: float = 0.55           # 0-1 dry/wet
    fx_route: int = 1


@dataclass
class NoiseGateState:
    """State for the Noise Gate (Drawmer DS201 model)."""
    enabled: bool = True
    threshold: float = -40.0    # dB, -80 to 0
    attack: float = 1.0         # ms, 0.02–20
    hold: float = 50.0          # ms, 2–4000
    release: float = 200.0      # ms, 5–4000
    range_db: float = -80.0     # dB, 0 to -80 (how far gate closes)
    key_filter: int = 0         # 0=Off 1=LP 2=HP
    key_freq: float = 1000.0    # Hz, 25–22000
    fx_route: int = 0


@dataclass
class TremoloState:
    """State for the Tremolo (Demeter TRM-1 model)."""
    enabled: bool = True
    rate: float = 4.0           # Hz, 0.5–15
    depth: float = 0.6          # 0–1
    shape: int = 0              # 0=Sine 1=Triangle 2=Square
    stereo_mode: int = 0        # 0=Mono 1=Stereo(90°) 2=PingPong
    mix: float = 1.0
    fx_route: int = 0


@dataclass
class AutoWahState:
    """State for the Auto-Wah / Envelope Filter (Mutron III model)."""
    enabled: bool = True
    sensitivity: float = 0.5    # 0–1, envelope follower gain
    peak: float = 0.6           # resonance Q 0–1
    mode: int = 0               # 0=Up 1=Down
    filter_type: int = 0        # 0=LP 1=BP 2=HP
    mix: float = 1.0
    fx_route: int = 0


@dataclass
class FETLimiterState:
    """State for the FET Limiter (Urei 1176LN model)."""
    enabled: bool = True
    input_gain: float = 0.5     # 0–1, maps to 0–40dBu drive
    output_gain: float = 0.5    # 0–1, maps to -20 to +20dB
    attack_idx: int = 4         # 0–6 (0=fastest ~20µs, 6=slowest ~800µs)
    release_idx: int = 2        # 0–4 (0=50ms, 4=1.1s)
    ratio_idx: int = 0          # 0=4:1, 1=8:1, 2=12:1, 3=20:1, 4=ABI
    fx_route: int = 0


@dataclass
class RotaryState:
    """State for the Rotary Speaker (Neo Ventilator II model)."""
    enabled: bool = True
    speed: int = 0              # 0=Stop 1=Slow 2=Fast
    acceleration: float = 0.5   # ramp time 0–1 (maps 0.2–3s)
    horn_level: float = 0.8     # 0–1
    rotor_level: float = 0.8    # 0–1
    mix: float = 1.0
    fx_route: int = 0


@dataclass
class ExciterState:
    """State for the Aural Exciter (Aphex Type C model)."""
    enabled: bool = True
    tune: float = 5000.0        # Hz, HPF for harmonics path, 1000–12000
    mix: float = 0.3            # harmonics blend 0–1
    lo_tune: float = 200.0      # Hz, low end crossover 50–1000
    lo_mix: float = 0.2         # low-end phase enhancement 0–1
    output: float = 0.0         # dB makeup, -6 to +6
    fx_route: int = 0


@dataclass
class TransientShaperState:
    """State for the SPL Transient Designer 4."""
    enabled: bool = True
    attack_shape: float = 0.0   # -1 to +1 (neg reduces, pos boosts attack)
    sustain_shape: float = 0.0  # -1 to +1 (neg gates tail, pos extends)
    output: float = 0.0         # dB makeup, -6 to +6
    stereo_link: bool = True
    fx_route: int = 0


@dataclass
class StereoWidenerState:
    """State for the Aphex 124A M-S Stereo Widener."""
    enabled: bool = True
    width: float = 1.0            # 0–2 (0=mono, 1=unity, 2=max)
    center: float = 1.0           # 0–2 mid level
    low_mono_freq: float = 120.0  # Hz below which side is filtered to mono, 20–500
    fx_route: int = 0


@dataclass
class FreqShifterState:
    """State for the Eventide H949 / Bode 1630 Frequency Shifter."""
    enabled: bool = True
    shift_hz: float = 0.0        # -500 to +500 Hz
    feedback: float = 0.0        # 0–0.9
    mix: float = 0.5
    fx_route: int = 0


@dataclass
class RingModState:
    """State for the Ring Leader ring modulator."""
    enabled: bool = True
    freq_hz: float = 440.0       # carrier freq 0.1–5000 Hz
    shape: int = 0               # 0=sine, 1=triangle, 2=square
    depth: float = 1.0           # 0–1 modulation depth
    mix: float = 0.5
    fx_route: int = 0


@dataclass
class HarmonizerState:
    """State for the Eventide H3000 Dual Harmonizer."""
    enabled: bool = True
    voice_a_semitones: int = -7     # -24 to +24
    voice_a_cents: int = 0          # -50 to +50
    voice_a_delay_ms: float = 0.0   # 0–250
    voice_b_semitones: int = 5      # -24 to +24
    voice_b_cents: int = 0          # -50 to +50
    voice_b_delay_ms: float = 0.0   # 0–250
    feedback: float = 0.0           # 0–0.9
    mix: float = 0.5
    algorithm: int = 0              # 0=Dual 1=Up 2=Down 3=PtchDly
    fx_route: int = 0


@dataclass
class SimpleEQState:
    """State for the Golden Ears 3-band EQ (Pultec-inspired)."""
    enabled: bool = True
    low: float = 0.0            # -12 to +12 dB
    mid: float = 0.0            # -12 to +12 dB
    high: float = 0.0           # -12 to +12 dB
    low_freq: float = 200.0     # shelf freq 60–500 Hz
    high_freq: float = 4000.0   # shelf freq 2000–12000 Hz
    mix: float = 1.0
    fx_route: int = 0


@dataclass
class PingPongDelayState:
    """State for the Table Tennis ping-pong delay."""
    enabled: bool = True
    time_ms: float = 375.0      # 10–2000 ms master time
    ping_ms: float = 375.0      # 10–2000 ms left (ping) tap offset
    pong_ms: float = 375.0      # 10–2000 ms right (pong) tap offset
    ping_pong_link: bool = True  # True = both follow time_ms
    feedback: float = 0.45      # 0–0.95
    spread: float = 1.0         # 0=mono, 1=full stereo ping-pong
    damping: float = 0.3        # 0–1 LP on feedback (1=dark)
    mix: float = 0.35
    fx_route: int = 0


@dataclass
class ShimmerReverbState:
    """State for the Stained Glass shimmer reverb."""
    enabled: bool = True
    decay: float = 0.8          # 0–1 (maps to RT60 1–20s)
    shimmer: float = 0.5        # 0–1 pitch-shifted feedback amount
    pitch: int = 12             # semitones: 5, 7, 12, 24
    damping: float = 0.4        # 0–1 high-freq damp on verb
    pre_delay_ms: float = 30.0  # 0–100 ms
    mix: float = 0.3
    fx_route: int = 0


@dataclass
class LoFiRadioState:
    """State for the Tin Can lo-fi radio/telephone filter."""
    enabled: bool = True
    band: int = 0               # 0=AM Radio, 1=Telephone, 2=Transistor, 3=Custom
    lo_cut: float = 300.0       # HP freq 20–2000 Hz
    hi_cut: float = 3400.0      # LP freq 500–16000 Hz
    noise: float = 0.15         # static/hiss 0–1
    distortion: float = 0.2     # speaker breakup 0–1
    mix: float = 1.0
    fx_route: int = 0


@dataclass
class AutoPanState:
    """State for the Pendulum auto-pan (stereo panner)."""
    enabled: bool = True
    rate: float = 2.0           # Hz, 0.1–20
    depth: float = 0.8          # 0–1
    shape: int = 0              # 0=Sine 1=Triangle 2=Square
    phase_offset: float = 0.0   # 0–1 (offset for starting position)
    mix: float = 1.0
    fx_route: int = 0


@dataclass
class FuzzState:
    """State for the Wool Over fuzz/distortion (Big Muff-inspired)."""
    enabled: bool = True
    gain: float = 0.7           # 0–1 (maps to 0–60 dB drive)
    tone: float = 0.5           # 0=dark, 1=bright (tone stack)
    sustain: float = 0.6        # 0–1 compression amount
    gate: float = 0.02          # noise gate threshold 0–0.1
    mix: float = 0.5
    fx_route: int = 0


@dataclass
class GranularFreezeState:
    """State for the Snow Globe granular freeze processor."""
    enabled: bool = True
    grain_size: float = 0.5     # 0–1 → 5–500 ms grain length
    scatter: float = 0.3        # 0–1 grain position scatter
    spread: float = 0.2         # 0–1 pitch randomisation per grain
    density: float = 0.6        # 0–1 grain overlap density
    freeze: bool = False        # True = lock buffer, stop writing
    mix: float = 0.4
    fx_route: int = 0


@dataclass
class GatedReverbState:
    """State for the Gate Crasher gated/reverse reverb."""
    enabled: bool = True
    room: float = 0.6           # 0–1 reverb room size
    gate_time: float = 0.3      # 0–1 → 50 ms–3 s gate duration
    slope: float = 0.3          # 0–1 gate closure shape (hard→soft)
    pre_delay: float = 0.15     # 0–1 → 0–100 ms pre-delay
    mode: int = 0               # 0=Gate, 1=Reverse
    mix: float = 0.35
    fx_route: int = 0


@dataclass
class StutterState:
    """State for the Broken Record stutter/glitch buffer looper."""
    enabled: bool = True
    size: float = 0.3           # 0–1 → 10–2000 ms capture size
    rate: float = 0.3           # 0–1 → 10–2000 ms repeat rate
    chaos: float = 0.0          # 0–1 random jump probability
    smear: float = 0.3          # 0–1 crossfade at loop boundary
    trigger: bool = False       # True = stutter active, False = pass-through
    mix: float = 1.0
    fx_route: int = 0


@dataclass
class SlicerState:
    """State for the slicer tool (cue preview, chop workflow, export)."""
    cue_volume: float = 1.0              # 0.0–2.0 preview gain (linear)
    cue_device: int = -1                 # sounddevice output device index; -1 = system default
    cue_channel_offset: int = 0          # first output channel on cue device (0-based pair)
    cue_mode: int = 0                    # 0 = CUE ONLY, 1 = CUE + MAIN, 2 = OFF
    preview_loop: bool = True            # default loop state when previewing
    auto_chop_mode: int = 0              # 0 = manual, 1 = transient, 2 = equal-division
    naming_template: str = "{source}_slice{n:02d}"  # export filename template


@dataclass
class RecorderState:
    """State for the live recorder."""
    buffer_idx: int = 0
    thru: bool = False
    input_device: int = 0
    input_channel: int = 0
    length_bars: int = 16
    length_beats: int = 1
    loop: bool = False
    recording: bool = False


@dataclass
class PatternRecorderState:
    """State for a pattern recorder."""
    length: int = 4  # in beats
    recording: bool = False
    playing: bool = False
    events: list = field(default_factory=list)


@dataclass
class AppConfig:
    """Full application configuration."""
    # Audio
    sample_rate: int = SAMPLE_RATE
    buffer_size: int = BUFFER_SIZE
    output_device: int = -1  # -1 = default
    input_device: int = -1
    host_api: str = ""  # empty = show all drivers

    # Grid
    row_count: int = 8
    grid_columns: int = 8

    # Tempo
    tempo: float = 120.0
    quantize: QuantizeMode = QuantizeMode.SIXTEENTH

    # Channels
    channels: list = field(default_factory=lambda: [ChannelState() for _ in range(MAX_ROWS)])

    # Groups
    groups: list = field(default_factory=lambda: [GroupState() for _ in range(NUM_GROUPS)])

    # Aux / Effects
    aux_volume: float = 1.0
    aux_send_level: float = 1.0
    aux_mute: bool = True
    aux_fx_route: int = 0  # 0=OFF, 1=FX1, 2=FX2
    aux_input_channel: int = 0  # audio input channel index
    delay: DelayState = field(default_factory=DelayState)
    delay_route: int = 1   # 0=Master/aux-send, 1=FX1, 2=FX2
    tape_delay: TapeDelayState = field(default_factory=TapeDelayState)
    tape_delay_route: int = 2  # 0=Master/aux-send, 1=FX1, 2=FX2

    # Filters (A = FX1, B = FX2 by defaults)
    filter_a: FilterState = field(default_factory=FilterState)
    filter_b: FilterBState = field(default_factory=FilterBState)

    # Modulation effects
    mod: ModState = field(default_factory=ModState)
    mod_route: int = 1     # 0=off, 1=FX1, 2=FX2

    # Lo-fi media effects
    reel_to_reel: ReelToReelState = field(default_factory=ReelToReelState)
    cassette_tape: CassetteTapeState = field(default_factory=CassetteTapeState)
    vhs: VHSState = field(default_factory=VHSState)
    vinyl: VinylState = field(default_factory=VinylState)

    # Compressor
    compressor: CompressorState = field(default_factory=CompressorState)

    # Reverb
    hall_reverb: HallReverbState = field(default_factory=HallReverbState)
    plate_reverb: PlateReverbState = field(default_factory=PlateReverbState)

    # Byte Me / Hot Snake (disabled by default — enabled when added to rack)
    bit_crusher: BitCrusherState = field(default_factory=BitCrusherState)
    tube_overdrive: TubeOverdriveState = field(default_factory=TubeOverdriveState)

    # Per-bus B state (FX Group B / FX2 — independent from bus A)
    delay_b: DelayState = field(default_factory=DelayState)
    tape_delay_b: TapeDelayState = field(default_factory=TapeDelayState)
    filter_a_b: FilterState = field(default_factory=FilterState)
    filter_b_b: FilterBState = field(default_factory=FilterBState)
    mod_b: ModState = field(default_factory=ModState)
    reel_to_reel_b: ReelToReelState = field(default_factory=ReelToReelState)
    cassette_tape_b: CassetteTapeState = field(default_factory=CassetteTapeState)
    vhs_b: VHSState = field(default_factory=VHSState)
    vinyl_b: VinylState = field(default_factory=VinylState)
    compressor_b: CompressorState = field(default_factory=CompressorState)
    hall_reverb_b: HallReverbState = field(default_factory=HallReverbState)
    plate_reverb_b: PlateReverbState = field(default_factory=PlateReverbState)
    bit_crusher_b: BitCrusherState = field(default_factory=BitCrusherState)
    tube_overdrive_b: TubeOverdriveState = field(default_factory=TubeOverdriveState)

    # Master bus (FX Group M) — processed after mixer output
    compressor_m: CompressorState = field(default_factory=CompressorState)
    filter_a_m: FilterState = field(default_factory=FilterState)
    filter_b_m: FilterBState = field(default_factory=FilterBState)
    hall_reverb_m: HallReverbState = field(default_factory=HallReverbState)
    plate_reverb_m: PlateReverbState = field(default_factory=PlateReverbState)
    tube_overdrive_m: TubeOverdriveState = field(default_factory=TubeOverdriveState)
    bit_crusher_m: BitCrusherState = field(default_factory=BitCrusherState)
    reel_to_reel_m: ReelToReelState = field(default_factory=ReelToReelState)
    cassette_tape_m: CassetteTapeState = field(default_factory=CassetteTapeState)
    vhs_m: VHSState = field(default_factory=VHSState)
    vinyl_m: VinylState = field(default_factory=VinylState)
    delay_m: DelayState = field(default_factory=DelayState)
    tape_delay_m: TapeDelayState = field(default_factory=TapeDelayState)
    mod_m: ModState = field(default_factory=ModState)
    glue_compressor: GlueCompressorState = field(default_factory=GlueCompressorState)
    glue_compressor_b: GlueCompressorState = field(default_factory=GlueCompressorState)
    glue_compressor_m: GlueCompressorState = field(default_factory=GlueCompressorState)

    # Ether Drift — per-bus (A / B / Master)
    ether_drift: EtherDriftState = field(default_factory=EtherDriftState)
    ether_drift_b: EtherDriftState = field(default_factory=EtherDriftState)
    ether_drift_m: EtherDriftState = field(default_factory=EtherDriftState)

    # Noise Gate — per-bus (A / B / Master)
    noise_gate: NoiseGateState = field(default_factory=NoiseGateState)
    noise_gate_b: NoiseGateState = field(default_factory=NoiseGateState)
    noise_gate_m: NoiseGateState = field(default_factory=NoiseGateState)

    # Per-bus C state (FX Group C / FX3 — independent send bus)
    delay_c: DelayState = field(default_factory=DelayState)
    tape_delay_c: TapeDelayState = field(default_factory=TapeDelayState)
    filter_a_c: FilterState = field(default_factory=FilterState)
    filter_b_c: FilterBState = field(default_factory=FilterBState)
    mod_c: ModState = field(default_factory=ModState)
    reel_to_reel_c: ReelToReelState = field(default_factory=ReelToReelState)
    cassette_tape_c: CassetteTapeState = field(default_factory=CassetteTapeState)
    vhs_c: VHSState = field(default_factory=VHSState)
    vinyl_c: VinylState = field(default_factory=VinylState)
    compressor_c: CompressorState = field(default_factory=CompressorState)
    glue_compressor_c: GlueCompressorState = field(default_factory=GlueCompressorState)
    hall_reverb_c: HallReverbState = field(default_factory=HallReverbState)
    plate_reverb_c: PlateReverbState = field(default_factory=PlateReverbState)
    bit_crusher_c: BitCrusherState = field(default_factory=BitCrusherState)
    tube_overdrive_c: TubeOverdriveState = field(default_factory=TubeOverdriveState)
    ether_drift_c: EtherDriftState = field(default_factory=EtherDriftState)
    noise_gate_c: NoiseGateState = field(default_factory=NoiseGateState)

    # Per-bus D state (FX Group D / FX4 — independent send bus)
    delay_d: DelayState = field(default_factory=DelayState)
    tape_delay_d: TapeDelayState = field(default_factory=TapeDelayState)
    filter_a_d: FilterState = field(default_factory=FilterState)
    filter_b_d: FilterBState = field(default_factory=FilterBState)
    mod_d: ModState = field(default_factory=ModState)
    reel_to_reel_d: ReelToReelState = field(default_factory=ReelToReelState)
    cassette_tape_d: CassetteTapeState = field(default_factory=CassetteTapeState)
    vhs_d: VHSState = field(default_factory=VHSState)
    vinyl_d: VinylState = field(default_factory=VinylState)
    compressor_d: CompressorState = field(default_factory=CompressorState)
    glue_compressor_d: GlueCompressorState = field(default_factory=GlueCompressorState)
    hall_reverb_d: HallReverbState = field(default_factory=HallReverbState)
    plate_reverb_d: PlateReverbState = field(default_factory=PlateReverbState)
    bit_crusher_d: BitCrusherState = field(default_factory=BitCrusherState)
    tube_overdrive_d: TubeOverdriveState = field(default_factory=TubeOverdriveState)
    ether_drift_d: EtherDriftState = field(default_factory=EtherDriftState)
    noise_gate_d: NoiseGateState = field(default_factory=NoiseGateState)
    tremolo: TremoloState = field(default_factory=TremoloState)
    tremolo_b: TremoloState = field(default_factory=TremoloState)
    tremolo_m: TremoloState = field(default_factory=TremoloState)
    tremolo_c: TremoloState = field(default_factory=TremoloState)
    tremolo_d: TremoloState = field(default_factory=TremoloState)
    auto_wah: AutoWahState = field(default_factory=AutoWahState)
    auto_wah_b: AutoWahState = field(default_factory=AutoWahState)
    auto_wah_m: AutoWahState = field(default_factory=AutoWahState)
    auto_wah_c: AutoWahState = field(default_factory=AutoWahState)
    auto_wah_d: AutoWahState = field(default_factory=AutoWahState)
    fet_limiter: FETLimiterState = field(default_factory=FETLimiterState)
    fet_limiter_b: FETLimiterState = field(default_factory=FETLimiterState)
    fet_limiter_m: FETLimiterState = field(default_factory=FETLimiterState)
    fet_limiter_c: FETLimiterState = field(default_factory=FETLimiterState)
    fet_limiter_d: FETLimiterState = field(default_factory=FETLimiterState)
    rotary: RotaryState = field(default_factory=RotaryState)
    rotary_b: RotaryState = field(default_factory=RotaryState)
    rotary_m: RotaryState = field(default_factory=RotaryState)
    rotary_c: RotaryState = field(default_factory=RotaryState)
    rotary_d: RotaryState = field(default_factory=RotaryState)
    exciter: ExciterState = field(default_factory=ExciterState)
    exciter_b: ExciterState = field(default_factory=ExciterState)
    exciter_m: ExciterState = field(default_factory=ExciterState)
    exciter_c: ExciterState = field(default_factory=ExciterState)
    exciter_d: ExciterState = field(default_factory=ExciterState)
    transient_shaper: TransientShaperState = field(default_factory=TransientShaperState)
    transient_shaper_b: TransientShaperState = field(default_factory=TransientShaperState)
    transient_shaper_m: TransientShaperState = field(default_factory=TransientShaperState)
    transient_shaper_c: TransientShaperState = field(default_factory=TransientShaperState)
    transient_shaper_d: TransientShaperState = field(default_factory=TransientShaperState)

    # Harmonizer
    harmonizer: HarmonizerState = field(default_factory=HarmonizerState)
    harmonizer_b: HarmonizerState = field(default_factory=HarmonizerState)
    harmonizer_m: HarmonizerState = field(default_factory=HarmonizerState)
    harmonizer_c: HarmonizerState = field(default_factory=HarmonizerState)
    harmonizer_d: HarmonizerState = field(default_factory=HarmonizerState)

    # Stereo Widener — per-bus (A / B / Master / C / D)
    stereo_widener: StereoWidenerState = field(default_factory=StereoWidenerState)
    stereo_widener_b: StereoWidenerState = field(default_factory=StereoWidenerState)
    stereo_widener_m: StereoWidenerState = field(default_factory=StereoWidenerState)
    stereo_widener_c: StereoWidenerState = field(default_factory=StereoWidenerState)
    stereo_widener_d: StereoWidenerState = field(default_factory=StereoWidenerState)

    # Freq Shifter — per-bus (A / B / Master / C / D)
    freq_shifter: FreqShifterState = field(default_factory=FreqShifterState)
    freq_shifter_b: FreqShifterState = field(default_factory=FreqShifterState)
    freq_shifter_m: FreqShifterState = field(default_factory=FreqShifterState)
    freq_shifter_c: FreqShifterState = field(default_factory=FreqShifterState)
    freq_shifter_d: FreqShifterState = field(default_factory=FreqShifterState)

    # Ring Modulator — per-bus (A / B / Master / C / D)
    ring_mod: RingModState = field(default_factory=RingModState)
    ring_mod_b: RingModState = field(default_factory=RingModState)
    ring_mod_m: RingModState = field(default_factory=RingModState)
    ring_mod_c: RingModState = field(default_factory=RingModState)
    ring_mod_d: RingModState = field(default_factory=RingModState)

    # Simple EQ — per-bus (A / B / Master / C / D)
    simple_eq: SimpleEQState = field(default_factory=SimpleEQState)
    simple_eq_b: SimpleEQState = field(default_factory=SimpleEQState)
    simple_eq_m: SimpleEQState = field(default_factory=SimpleEQState)
    simple_eq_c: SimpleEQState = field(default_factory=SimpleEQState)
    simple_eq_d: SimpleEQState = field(default_factory=SimpleEQState)

    # Ping-Pong Delay — per-bus (A / B / Master / C / D)
    ping_pong_delay: PingPongDelayState = field(default_factory=PingPongDelayState)
    ping_pong_delay_b: PingPongDelayState = field(default_factory=PingPongDelayState)
    ping_pong_delay_m: PingPongDelayState = field(default_factory=PingPongDelayState)
    ping_pong_delay_c: PingPongDelayState = field(default_factory=PingPongDelayState)
    ping_pong_delay_d: PingPongDelayState = field(default_factory=PingPongDelayState)

    # Shimmer Reverb — per-bus (A / B / Master / C / D)
    shimmer_reverb: ShimmerReverbState = field(default_factory=ShimmerReverbState)
    shimmer_reverb_b: ShimmerReverbState = field(default_factory=ShimmerReverbState)
    shimmer_reverb_m: ShimmerReverbState = field(default_factory=ShimmerReverbState)
    shimmer_reverb_c: ShimmerReverbState = field(default_factory=ShimmerReverbState)
    shimmer_reverb_d: ShimmerReverbState = field(default_factory=ShimmerReverbState)

    # Lo-Fi Radio — per-bus (A / B / Master / C / D)
    lofi_radio: LoFiRadioState = field(default_factory=LoFiRadioState)
    lofi_radio_b: LoFiRadioState = field(default_factory=LoFiRadioState)
    lofi_radio_m: LoFiRadioState = field(default_factory=LoFiRadioState)
    lofi_radio_c: LoFiRadioState = field(default_factory=LoFiRadioState)
    lofi_radio_d: LoFiRadioState = field(default_factory=LoFiRadioState)

    # Auto-Pan — per-bus (A / B / Master / C / D)
    auto_pan: AutoPanState = field(default_factory=AutoPanState)
    auto_pan_b: AutoPanState = field(default_factory=AutoPanState)
    auto_pan_m: AutoPanState = field(default_factory=AutoPanState)
    auto_pan_c: AutoPanState = field(default_factory=AutoPanState)
    auto_pan_d: AutoPanState = field(default_factory=AutoPanState)

    # Fuzz — per-bus (A / B / Master / C / D)
    fuzz: FuzzState = field(default_factory=FuzzState)
    fuzz_b: FuzzState = field(default_factory=FuzzState)
    fuzz_m: FuzzState = field(default_factory=FuzzState)
    fuzz_c: FuzzState = field(default_factory=FuzzState)
    fuzz_d: FuzzState = field(default_factory=FuzzState)

    # Granular Freeze — per-bus (A / B / Master / C / D)
    granular_freeze: GranularFreezeState = field(default_factory=GranularFreezeState)
    granular_freeze_b: GranularFreezeState = field(default_factory=GranularFreezeState)
    granular_freeze_m: GranularFreezeState = field(default_factory=GranularFreezeState)
    granular_freeze_c: GranularFreezeState = field(default_factory=GranularFreezeState)
    granular_freeze_d: GranularFreezeState = field(default_factory=GranularFreezeState)

    # Gated Reverb — per-bus (A / B / Master / C / D)
    gate_reverb: GatedReverbState = field(default_factory=GatedReverbState)
    gate_reverb_b: GatedReverbState = field(default_factory=GatedReverbState)
    gate_reverb_m: GatedReverbState = field(default_factory=GatedReverbState)
    gate_reverb_c: GatedReverbState = field(default_factory=GatedReverbState)
    gate_reverb_d: GatedReverbState = field(default_factory=GatedReverbState)

    # Stutter — per-bus (A / B / Master / C / D)
    stutter: StutterState = field(default_factory=StutterState)
    stutter_b: StutterState = field(default_factory=StutterState)
    stutter_m: StutterState = field(default_factory=StutterState)
    stutter_c: StutterState = field(default_factory=StutterState)
    stutter_d: StutterState = field(default_factory=StutterState)

    # Slicer tool
    slicer: SlicerState = field(default_factory=SlicerState)

    # Recorder
    recorder: RecorderState = field(default_factory=RecorderState)

    # Pattern Recorders
    patterns: list = field(default_factory=lambda: [
        PatternRecorderState(length=4),
        PatternRecorderState(length=8),
        PatternRecorderState(length=16),
        PatternRecorderState(length=32),
    ])

    # Master
    master_volume: float = 1.0
    fadetime: float = 1.2

    # OSC
    osc_enabled: bool = False
    osc_host: str = "127.0.0.1"
    osc_send_port: int = 8000
    osc_recv_port: int = 8001

    # MIDI
    midi_in_device: int = -1
    midi_out_device: int = -1

    # Playback
    snap_speed_to_rate: bool = False  # auto-snap speed to nearest clean 0.5x/2x step

    # UI
    group_colors: list = field(default_factory=lambda: list(DEFAULT_GROUP_COLORS))


def get_project_root() -> Path:
    """Get the mlrVp project root directory."""
    return Path(__file__).resolve().parent.parent


def get_config_dir() -> Path:
    """Get the data directory within the project folder."""
    config_dir = get_project_root() / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_presets_dir() -> Path:
    d = get_config_dir() / "presets"
    d.mkdir(exist_ok=True)
    return d


def get_slices_dir() -> Path:
    d = get_config_dir() / "slices"
    d.mkdir(exist_ok=True)
    return d


def get_setlists_dir() -> Path:
    d = get_config_dir() / "setlists"
    d.mkdir(exist_ok=True)
    return d


def get_defaults_dir() -> Path:
    d = get_config_dir() / "defaults"
    d.mkdir(exist_ok=True)
    return d


def get_ui_scale() -> float:
    """Read stored UI scale factor (default 1.0)."""
    p = get_config_dir() / "ui_scale.txt"
    if p.exists():
        try:
            return float(p.read_text().strip())
        except (ValueError, OSError):
            pass
    return 1.0


def set_ui_scale(factor: float):
    """Persist UI scale factor."""
    p = get_config_dir() / "ui_scale.txt"
    p.write_text(str(factor))
