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
    aux_mute: bool = False
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
