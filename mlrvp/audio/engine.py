"""Main audio engine for mlrVp.

Manages the sounddevice stream, channel playback, mixing, and effects processing.

Architecture
------------
A dedicated **DSP worker thread** runs the full processing pipeline (channels,
mixer, effects, metering) and writes completed audio blocks into a lock-free
ring buffer.  The **sounddevice callback** (running on the PortAudio C thread)
only copies from the ring buffer into the output — a single memcpy that never
needs to wait for the Python GIL.

This decoupling means GUI paint events, meter updates, or any other Python
thread activity cannot stall the audio output.  The ring buffer provides
several blocks of lookahead so even if the worker is briefly delayed by
a GIL-holding GUI repaint, pre-computed audio keeps flowing.
"""

import numpy as np
import sounddevice as sd
import threading
import gc
import logging
import os
import time
from collections import deque
from typing import Optional, Callable

log = logging.getLogger("mlrvp")

from mlrvp.config import (
    AppConfig, BUFFER_SIZE, MAX_ROWS, NUM_GROUPS,
    PlaybackMode
)
from mlrvp.audio.channel import Channel
from mlrvp.audio.mixer import Mixer
from mlrvp.audio.effects import (
    StereoDelay, TapeDelay, ChannelFilter, FXBus,
    BiquadFilter, GlobalFilter, MoogLadderFilter,
    ReelToReelProcessor, CassetteTapeProcessor, VHSProcessor, VinylProcessor,
    CompressorProcessor, GlueCompressorProcessor,
    HallReverbProcessor, PlateReverbProcessor,
    BitCrusherProcessor, TubeOverdriveProcessor,
    EtherDriftProcessor,
    NoiseGateProcessor,
    TremoloProcessor,
    AutoWahProcessor,
    FETLimiterProcessor,
    RotaryProcessor,
    ExciterProcessor,
    TransientShaperProcessor,
    HarmonizerProcessor,
    StereoWidenerProcessor,
    FreqShifterProcessor,
    RingModProcessor,
    SimpleEQProcessor,
    PingPongDelayProcessor,
    ShimmerReverbProcessor,
    LoFiRadioProcessor,
    AutoPanProcessor,
    FuzzProcessor,
    GranularFreezeProcessor,
    GatedReverbProcessor,
    StutterProcessor,
    StowawayProcessor,
)
from mlrvp.audio.recorder import Recorder, PreRollBuffer
from mlrvp.audio.sample import SampleBuffer
from mlrvp.audio.live_loop import LiveLoopRecorder
from mlrvp.audio.samp_recorder import SampRecorder
from mlrvp.audio.song_recorder import SongRecorder

# Check Numba status from effects module
try:
    from mlrvp.audio.effects import _HAS_NUMBA
except ImportError:
    _HAS_NUMBA = False


def _collect_young() -> None:
    gc.collect(0)


class AudioEngine:
    """Core real-time audio engine.

    Manages channels, mixer, effects, and the audio device stream.
    Thread-safe trigger/control methods for use from GUI and grid threads.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.sample_rate = config.sample_rate
        self.buffer_size = config.buffer_size

        # Channels
        self.channels = [Channel(i, self.sample_rate) for i in range(MAX_ROWS)]
        self.channel_filters = [ChannelFilter(self.sample_rate) for _ in range(MAX_ROWS)]

        # Mixer
        self.mixer = Mixer()

        # Effects — per-bus DSP instances so each rack processes independently.
        # Bus A (FX1) instances:
        self.delay = StereoDelay(self.sample_rate)
        self.tape_delay = TapeDelay(self.sample_rate)
        self.filter_a = GlobalFilter(self.sample_rate)
        self.filter_b = MoogLadderFilter(self.sample_rate)
        # Bus B (FX2) instances:
        self.delay_b = StereoDelay(self.sample_rate)
        self.tape_delay_b = TapeDelay(self.sample_rate)
        self.filter_a_b = GlobalFilter(self.sample_rate)
        self.filter_b_b = MoogLadderFilter(self.sample_rate)
        # Mod effects already have per-bus instances inside FXBus:
        self.fx_bus_1 = FXBus(self.sample_rate)
        self.fx_bus_2 = FXBus(self.sample_rate)
        # Lo-fi media effects — per-bus instances:
        self.reel_to_reel = ReelToReelProcessor(self.sample_rate, self.buffer_size)
        self.reel_to_reel_b = ReelToReelProcessor(self.sample_rate, self.buffer_size)
        self.cassette_tape = CassetteTapeProcessor(self.sample_rate, self.buffer_size)
        self.cassette_tape_b = CassetteTapeProcessor(self.sample_rate, self.buffer_size)
        self.vhs = VHSProcessor(self.sample_rate, self.buffer_size)
        self.vhs_b = VHSProcessor(self.sample_rate, self.buffer_size)
        self.vinyl = VinylProcessor(self.sample_rate, self.buffer_size)
        self.vinyl_b = VinylProcessor(self.sample_rate, self.buffer_size)

        # Compressor — per-bus instances
        self.compressor = CompressorProcessor(self.sample_rate)
        self.compressor_b = CompressorProcessor(self.sample_rate)

        # Reverb — per-bus instances
        self.hall_reverb = HallReverbProcessor(self.sample_rate)
        self.hall_reverb_b = HallReverbProcessor(self.sample_rate)
        self.plate_reverb = PlateReverbProcessor(self.sample_rate)
        self.plate_reverb_b = PlateReverbProcessor(self.sample_rate)

        # Bit crusher — per-bus instances
        self.bit_crusher = BitCrusherProcessor(self.sample_rate)
        self.bit_crusher_b = BitCrusherProcessor(self.sample_rate)

        # Tube overdrive — per-bus instances
        self.tube_overdrive = TubeOverdriveProcessor(self.sample_rate)
        self.tube_overdrive_b = TubeOverdriveProcessor(self.sample_rate)

        # Master bus (FX Group M) — processed post-master-HP/LP trim
        self.filter_a_m = GlobalFilter(self.sample_rate)
        self.filter_b_m = MoogLadderFilter(self.sample_rate)
        self.fx_bus_m = FXBus(self.sample_rate)
        self.delay_m = StereoDelay(self.sample_rate)
        self.tape_delay_m = TapeDelay(self.sample_rate)
        self.reel_to_reel_m = ReelToReelProcessor(self.sample_rate, self.buffer_size)
        self.cassette_tape_m = CassetteTapeProcessor(self.sample_rate, self.buffer_size)
        self.vhs_m = VHSProcessor(self.sample_rate, self.buffer_size)
        self.vinyl_m = VinylProcessor(self.sample_rate, self.buffer_size)
        self.compressor_m = CompressorProcessor(self.sample_rate)
        self.hall_reverb_m = HallReverbProcessor(self.sample_rate)
        self.plate_reverb_m = PlateReverbProcessor(self.sample_rate)
        self.bit_crusher_m = BitCrusherProcessor(self.sample_rate)
        self.tube_overdrive_m = TubeOverdriveProcessor(self.sample_rate)
        # Glue compressor — all three buses
        self.glue_compressor = GlueCompressorProcessor(self.sample_rate)
        self.glue_compressor_b = GlueCompressorProcessor(self.sample_rate)
        self.glue_compressor_m = GlueCompressorProcessor(self.sample_rate)

        # Ether Drift — per-bus instances
        self.ether_drift = EtherDriftProcessor(self.sample_rate, self.buffer_size)
        self.ether_drift_b = EtherDriftProcessor(self.sample_rate, self.buffer_size)
        self.ether_drift_m = EtherDriftProcessor(self.sample_rate, self.buffer_size)

        # Noise Gate — per-bus instances
        self.noise_gate = NoiseGateProcessor(self.sample_rate, self.buffer_size)
        self.noise_gate_b = NoiseGateProcessor(self.sample_rate, self.buffer_size)
        self.noise_gate_m = NoiseGateProcessor(self.sample_rate, self.buffer_size)

        # Tremolo — per-bus instances
        self.tremolo = TremoloProcessor(self.sample_rate, self.buffer_size)
        self.tremolo_b = TremoloProcessor(self.sample_rate, self.buffer_size)
        self.tremolo_m = TremoloProcessor(self.sample_rate, self.buffer_size)

        # Auto-Wah — per-bus instances
        self.auto_wah = AutoWahProcessor(self.sample_rate)
        self.auto_wah_b = AutoWahProcessor(self.sample_rate)
        self.auto_wah_m = AutoWahProcessor(self.sample_rate)

        # FET Limiter — per-bus instances
        self.fet_limiter = FETLimiterProcessor(self.sample_rate)
        self.fet_limiter_b = FETLimiterProcessor(self.sample_rate)
        self.fet_limiter_m = FETLimiterProcessor(self.sample_rate)

        # Rotary Speaker — per-bus instances
        self.rotary = RotaryProcessor(self.sample_rate)
        self.rotary_b = RotaryProcessor(self.sample_rate)
        self.rotary_m = RotaryProcessor(self.sample_rate)

        # Aural Exciter — per-bus instances
        self.exciter = ExciterProcessor(self.sample_rate)
        self.exciter_b = ExciterProcessor(self.sample_rate)
        self.exciter_m = ExciterProcessor(self.sample_rate)

        # Transient Shaper — per-bus instances
        self.transient_shaper = TransientShaperProcessor(self.sample_rate, self.buffer_size)
        self.transient_shaper_b = TransientShaperProcessor(self.sample_rate, self.buffer_size)
        self.transient_shaper_m = TransientShaperProcessor(self.sample_rate, self.buffer_size)

        # Harmonizer — per-bus instances
        self.harmonizer = HarmonizerProcessor(self.sample_rate)
        self.harmonizer_b = HarmonizerProcessor(self.sample_rate)
        self.harmonizer_m = HarmonizerProcessor(self.sample_rate)

        # Stereo Widener — per-bus instances
        self.stereo_widener = StereoWidenerProcessor(self.sample_rate)
        self.stereo_widener_b = StereoWidenerProcessor(self.sample_rate)
        self.stereo_widener_m = StereoWidenerProcessor(self.sample_rate)

        # Frequency Shifter — per-bus instances
        self.freq_shifter = FreqShifterProcessor(self.sample_rate)
        self.freq_shifter_b = FreqShifterProcessor(self.sample_rate)
        self.freq_shifter_m = FreqShifterProcessor(self.sample_rate)

        # Ring Modulator — per-bus instances
        self.ring_mod = RingModProcessor(self.sample_rate)
        self.ring_mod_b = RingModProcessor(self.sample_rate)
        self.ring_mod_m = RingModProcessor(self.sample_rate)

        # Bus C (FX3) instances:
        self.delay_c = StereoDelay(self.sample_rate)
        self.tape_delay_c = TapeDelay(self.sample_rate)
        self.filter_a_c = GlobalFilter(self.sample_rate)
        self.filter_b_c = MoogLadderFilter(self.sample_rate)
        self.fx_bus_c = FXBus(self.sample_rate)
        self.reel_to_reel_c = ReelToReelProcessor(self.sample_rate, self.buffer_size)
        self.cassette_tape_c = CassetteTapeProcessor(self.sample_rate, self.buffer_size)
        self.vhs_c = VHSProcessor(self.sample_rate, self.buffer_size)
        self.vinyl_c = VinylProcessor(self.sample_rate, self.buffer_size)
        self.compressor_c = CompressorProcessor(self.sample_rate)
        self.glue_compressor_c = GlueCompressorProcessor(self.sample_rate)
        self.hall_reverb_c = HallReverbProcessor(self.sample_rate)
        self.plate_reverb_c = PlateReverbProcessor(self.sample_rate)
        self.bit_crusher_c = BitCrusherProcessor(self.sample_rate)
        self.tube_overdrive_c = TubeOverdriveProcessor(self.sample_rate)
        self.ether_drift_c = EtherDriftProcessor(self.sample_rate, self.buffer_size)
        self.noise_gate_c = NoiseGateProcessor(self.sample_rate, self.buffer_size)
        self.tremolo_c = TremoloProcessor(self.sample_rate, self.buffer_size)
        self.auto_wah_c = AutoWahProcessor(self.sample_rate)
        self.fet_limiter_c = FETLimiterProcessor(self.sample_rate)
        self.rotary_c = RotaryProcessor(self.sample_rate)
        self.exciter_c = ExciterProcessor(self.sample_rate)
        self.transient_shaper_c = TransientShaperProcessor(self.sample_rate, self.buffer_size)
        self.harmonizer_c = HarmonizerProcessor(self.sample_rate)
        self.stereo_widener_c = StereoWidenerProcessor(self.sample_rate)
        self.freq_shifter_c = FreqShifterProcessor(self.sample_rate)
        self.ring_mod_c = RingModProcessor(self.sample_rate)
        # Bus D (FX4) instances:
        self.delay_d = StereoDelay(self.sample_rate)
        self.tape_delay_d = TapeDelay(self.sample_rate)
        self.filter_a_d = GlobalFilter(self.sample_rate)
        self.filter_b_d = MoogLadderFilter(self.sample_rate)
        self.fx_bus_d = FXBus(self.sample_rate)
        self.reel_to_reel_d = ReelToReelProcessor(self.sample_rate, self.buffer_size)
        self.cassette_tape_d = CassetteTapeProcessor(self.sample_rate, self.buffer_size)
        self.vhs_d = VHSProcessor(self.sample_rate, self.buffer_size)
        self.vinyl_d = VinylProcessor(self.sample_rate, self.buffer_size)
        self.compressor_d = CompressorProcessor(self.sample_rate)
        self.glue_compressor_d = GlueCompressorProcessor(self.sample_rate)
        self.hall_reverb_d = HallReverbProcessor(self.sample_rate)
        self.plate_reverb_d = PlateReverbProcessor(self.sample_rate)
        self.bit_crusher_d = BitCrusherProcessor(self.sample_rate)
        self.tube_overdrive_d = TubeOverdriveProcessor(self.sample_rate)
        self.ether_drift_d = EtherDriftProcessor(self.sample_rate, self.buffer_size)
        self.noise_gate_d = NoiseGateProcessor(self.sample_rate, self.buffer_size)
        self.tremolo_d = TremoloProcessor(self.sample_rate, self.buffer_size)
        self.auto_wah_d = AutoWahProcessor(self.sample_rate)
        self.fet_limiter_d = FETLimiterProcessor(self.sample_rate)
        self.rotary_d = RotaryProcessor(self.sample_rate)
        self.exciter_d = ExciterProcessor(self.sample_rate)
        self.transient_shaper_d = TransientShaperProcessor(self.sample_rate, self.buffer_size)
        self.harmonizer_d = HarmonizerProcessor(self.sample_rate)
        self.stereo_widener_d = StereoWidenerProcessor(self.sample_rate)
        self.freq_shifter_d = FreqShifterProcessor(self.sample_rate)
        self.ring_mod_d = RingModProcessor(self.sample_rate)

        # Simple EQ — per-bus instances
        self.simple_eq = SimpleEQProcessor(self.sample_rate)
        self.simple_eq_b = SimpleEQProcessor(self.sample_rate)
        self.simple_eq_m = SimpleEQProcessor(self.sample_rate)
        self.simple_eq_c = SimpleEQProcessor(self.sample_rate)
        self.simple_eq_d = SimpleEQProcessor(self.sample_rate)
        # Ping-Pong Delay — per-bus instances
        self.ping_pong_delay = PingPongDelayProcessor(self.sample_rate)
        self.ping_pong_delay_b = PingPongDelayProcessor(self.sample_rate)
        self.ping_pong_delay_m = PingPongDelayProcessor(self.sample_rate)
        self.ping_pong_delay_c = PingPongDelayProcessor(self.sample_rate)
        self.ping_pong_delay_d = PingPongDelayProcessor(self.sample_rate)
        # Shimmer Reverb — per-bus instances
        self.shimmer_reverb = ShimmerReverbProcessor(self.sample_rate)
        self.shimmer_reverb_b = ShimmerReverbProcessor(self.sample_rate)
        self.shimmer_reverb_m = ShimmerReverbProcessor(self.sample_rate)
        self.shimmer_reverb_c = ShimmerReverbProcessor(self.sample_rate)
        self.shimmer_reverb_d = ShimmerReverbProcessor(self.sample_rate)
        # Lo-Fi Radio — per-bus instances
        self.lofi_radio = LoFiRadioProcessor(self.sample_rate)
        self.lofi_radio_b = LoFiRadioProcessor(self.sample_rate)
        self.lofi_radio_m = LoFiRadioProcessor(self.sample_rate)
        self.lofi_radio_c = LoFiRadioProcessor(self.sample_rate)
        self.lofi_radio_d = LoFiRadioProcessor(self.sample_rate)
        # Auto-Pan — per-bus instances
        self.auto_pan = AutoPanProcessor(self.sample_rate)
        self.auto_pan_b = AutoPanProcessor(self.sample_rate)
        self.auto_pan_m = AutoPanProcessor(self.sample_rate)
        self.auto_pan_c = AutoPanProcessor(self.sample_rate)
        self.auto_pan_d = AutoPanProcessor(self.sample_rate)
        # Fuzz — per-bus instances
        self.fuzz = FuzzProcessor(self.sample_rate)
        self.fuzz_b = FuzzProcessor(self.sample_rate)
        self.fuzz_m = FuzzProcessor(self.sample_rate)
        self.fuzz_c = FuzzProcessor(self.sample_rate)
        self.fuzz_d = FuzzProcessor(self.sample_rate)
        # Granular Freeze — per-bus instances
        self.granular_freeze = GranularFreezeProcessor(self.sample_rate)
        self.granular_freeze_b = GranularFreezeProcessor(self.sample_rate)
        self.granular_freeze_m = GranularFreezeProcessor(self.sample_rate)
        self.granular_freeze_c = GranularFreezeProcessor(self.sample_rate)
        self.granular_freeze_d = GranularFreezeProcessor(self.sample_rate)
        # Gated Reverb — per-bus instances
        self.gate_reverb = GatedReverbProcessor(self.sample_rate)
        self.gate_reverb_b = GatedReverbProcessor(self.sample_rate)
        self.gate_reverb_m = GatedReverbProcessor(self.sample_rate)
        self.gate_reverb_c = GatedReverbProcessor(self.sample_rate)
        self.gate_reverb_d = GatedReverbProcessor(self.sample_rate)
        # Stutter — per-bus instances
        self.stutter = StutterProcessor(self.sample_rate)
        self.stutter_b = StutterProcessor(self.sample_rate)
        self.stutter_m = StutterProcessor(self.sample_rate)
        self.stutter_c = StutterProcessor(self.sample_rate)
        self.stutter_d = StutterProcessor(self.sample_rate)

        # Recorder
        self.recorder = Recorder(self.sample_rate)
        # Song Recorder — always loopback, standalone from the Tools recorder
        self.song_recorder = SongRecorder(self.sample_rate)
        # Pre-roll ring buffer (always-on, holds last N seconds of input)
        self.pre_roll = PreRollBuffer(self.sample_rate, max_seconds=30.0, num_channels=2)

        # LIVE-mode looper — single recorder instance shared across rows
        # (only one row can record at a time). Set by start_live_recording(),
        # cleared after finalize. _live_recorder_row tracks which row owns it.
        self._live_recorder: Optional[LiveLoopRecorder] = None
        self._live_recorder_row: int = -1
        self._live_recorder_trim: float = 1.0
        # Callback fired (from audio thread) when a live recording finishes.
        # Signature: callback(row: int, buffer: SampleBuffer) -> None
        # The callback MUST be thread-safe (e.g. enqueue a Qt signal via
        # QMetaObject.invokeMethod). Set by app.py after construction.
        self._live_recorded_callback: Optional[Callable[[int, SampleBuffer], None]] = None

        # SAMP / resample recorder — bus-capture sibling of LiveLoopRecorder.
        # Active when a LIVE-mode channel's source_bus != INPUT.  Single row at
        # a time (same constraint as input recording).  source_bus indices follow
        # config.LIVE_SOURCE_NAMES: 1=MAIN, 2-5=FX-A..D, 6-9=GRP-0..3.
        self._samp_recorder: Optional[SampRecorder] = None
        self._samp_recorder_row: int = -1
        self._samp_source_bus: int = 0
        self._samp_exclude_self: bool = True
        self._samp_recorded_callback: Optional[Callable[[int, "SampleBuffer"], None]] = None

        # Audio-threshold arm watches — list of (row, source_bus, threshold_linear).
        # Populated by arm_audio_trigger(); checked each block in _process_block.
        # Each entry is single-fire: removed as soon as threshold is exceeded.
        self._audio_arm_watches: list = []    # [(row, src, thresh), ...]
        self._audio_arm_callback: Optional[Callable[[int], None]] = None

        # Per-bus in-rack flags — True when the module is present in that rack.
        # Rack A → FX1 (Clean Studio): gate → compressor → multiband EQ → delay → stereo widener
        # Rack B → FX2 (Breakbeat): glue compressor → transient shaper → cassette tape
        # Rack C → FX3 (Space & Pitch): harmonizer → hall reverb → plate reverb → tremolo
        # Rack D → FX4 (Lo-Fi / Destroy): bit crusher → tube overdrive → ring mod → vinyl → cassette
        self.delay_in_rack_a = True
        self.delay_in_rack_b = False
        self.tape_delay_in_rack_a = False
        self.tape_delay_in_rack_b = False
        self.filter_a_in_rack_a = True
        self.filter_a_in_rack_b = False
        self.filter_b_in_rack_a = False
        self.filter_b_in_rack_b = False
        self.mod_in_rack_a = False
        self.mod_in_rack_b = False
        self.reel_to_reel_in_rack_a = False
        self.reel_to_reel_in_rack_b = False
        self.cassette_tape_in_rack_a = False
        self.cassette_tape_in_rack_b = True
        self.bit_crusher_in_rack_a = False
        self.bit_crusher_in_rack_b = False
        self.tube_overdrive_in_rack_a = False
        self.tube_overdrive_in_rack_b = False
        self.vhs_in_rack_a = False
        self.vhs_in_rack_b = False
        self.vinyl_in_rack_a = False
        self.vinyl_in_rack_b = False
        self.compressor_in_rack_a = True
        self.compressor_in_rack_b = False
        self.hall_reverb_in_rack_a = False
        self.hall_reverb_in_rack_b = False
        self.plate_reverb_in_rack_a = False
        self.plate_reverb_in_rack_b = False
        self.glue_compressor_in_rack_a = False
        self.glue_compressor_in_rack_b = True
        self.ether_drift_in_rack_a = False
        self.ether_drift_in_rack_b = False
        self.noise_gate_in_rack_a = True
        self.noise_gate_in_rack_b = False
        self.tremolo_in_rack_a = False
        self.tremolo_in_rack_b = False
        self.auto_wah_in_rack_a = False
        self.auto_wah_in_rack_b = False
        self.fet_limiter_in_rack_a = False
        self.fet_limiter_in_rack_b = False
        self.rotary_in_rack_a = False
        self.rotary_in_rack_b = False
        self.exciter_in_rack_a = False
        self.exciter_in_rack_b = False
        self.transient_shaper_in_rack_a = False
        self.transient_shaper_in_rack_b = True
        self.harmonizer_in_rack_a = False
        self.harmonizer_in_rack_b = False
        self.stereo_widener_in_rack_a = True
        self.stereo_widener_in_rack_b = False
        self.freq_shifter_in_rack_a = False
        self.freq_shifter_in_rack_b = False
        self.ring_mod_in_rack_a = False
        self.ring_mod_in_rack_b = False

        # Rack C in-rack flags (FX3 — Space & Pitch)
        self.delay_in_rack_c = False
        self.tape_delay_in_rack_c = False
        self.filter_a_in_rack_c = False
        self.filter_b_in_rack_c = False
        self.mod_in_rack_c = False
        self.reel_to_reel_in_rack_c = False
        self.cassette_tape_in_rack_c = False
        self.vhs_in_rack_c = False
        self.vinyl_in_rack_c = False
        self.compressor_in_rack_c = False
        self.glue_compressor_in_rack_c = False
        self.hall_reverb_in_rack_c = True
        self.plate_reverb_in_rack_c = True
        self.bit_crusher_in_rack_c = False
        self.tube_overdrive_in_rack_c = False
        self.ether_drift_in_rack_c = False
        self.noise_gate_in_rack_c = False
        self.tremolo_in_rack_c = True
        self.auto_wah_in_rack_c = False
        self.fet_limiter_in_rack_c = False
        self.rotary_in_rack_c = False
        self.exciter_in_rack_c = False
        self.transient_shaper_in_rack_c = False
        self.harmonizer_in_rack_c = True
        self.stereo_widener_in_rack_c = False
        self.freq_shifter_in_rack_c = False
        self.ring_mod_in_rack_c = False
        # Rack D in-rack flags (FX4 — Lo-Fi / Destroy)
        self.delay_in_rack_d = False
        self.tape_delay_in_rack_d = False
        self.filter_a_in_rack_d = False
        self.filter_b_in_rack_d = False
        self.mod_in_rack_d = False
        self.reel_to_reel_in_rack_d = False
        self.cassette_tape_in_rack_d = True
        self.vhs_in_rack_d = False
        self.vinyl_in_rack_d = True
        self.compressor_in_rack_d = False
        self.glue_compressor_in_rack_d = False
        self.hall_reverb_in_rack_d = False
        self.plate_reverb_in_rack_d = False
        self.bit_crusher_in_rack_d = True
        self.tube_overdrive_in_rack_d = True
        self.ether_drift_in_rack_d = False
        self.noise_gate_in_rack_d = False
        self.tremolo_in_rack_d = False
        self.auto_wah_in_rack_d = False
        self.fet_limiter_in_rack_d = False
        self.rotary_in_rack_d = False
        self.exciter_in_rack_d = False
        self.transient_shaper_in_rack_d = False
        self.harmonizer_in_rack_d = False
        self.stereo_widener_in_rack_d = False
        self.freq_shifter_in_rack_d = False
        self.ring_mod_in_rack_d = True
        # Master rack in-rack flags (Mastering): glue comp → multiband EQ → FET limiter → exciter
        self.filter_a_in_rack_m = True
        self.filter_b_in_rack_m = False
        self.mod_in_rack_m = False
        self.delay_in_rack_m = False
        self.tape_delay_in_rack_m = False
        self.reel_to_reel_in_rack_m = False
        self.cassette_tape_in_rack_m = False
        self.vhs_in_rack_m = False
        self.vinyl_in_rack_m = False
        self.compressor_in_rack_m = False
        self.hall_reverb_in_rack_m = False
        self.plate_reverb_in_rack_m = False
        self.bit_crusher_in_rack_m = False
        self.tube_overdrive_in_rack_m = False
        self.glue_compressor_in_rack_m = True
        self.ether_drift_in_rack_m = False
        self.noise_gate_in_rack_m = False
        self.tremolo_in_rack_m = False
        self.auto_wah_in_rack_m = False
        self.fet_limiter_in_rack_m = True
        self.rotary_in_rack_m = False
        self.exciter_in_rack_m = True
        self.transient_shaper_in_rack_m = False
        self.harmonizer_in_rack_m = False
        self.stereo_widener_in_rack_m = False
        self.freq_shifter_in_rack_m = False
        self.ring_mod_in_rack_m = False

        # Simple EQ in-rack flags (all racks)
        self.simple_eq_in_rack_a = False
        self.simple_eq_in_rack_b = False
        self.simple_eq_in_rack_c = False
        self.simple_eq_in_rack_d = False
        self.simple_eq_in_rack_m = False
        # Ping-Pong Delay in-rack flags
        self.ping_pong_delay_in_rack_a = False
        self.ping_pong_delay_in_rack_b = False
        self.ping_pong_delay_in_rack_c = False
        self.ping_pong_delay_in_rack_d = False
        self.ping_pong_delay_in_rack_m = False
        # Shimmer Reverb in-rack flags
        self.shimmer_reverb_in_rack_a = False
        self.shimmer_reverb_in_rack_b = False
        self.shimmer_reverb_in_rack_c = False
        self.shimmer_reverb_in_rack_d = False
        self.shimmer_reverb_in_rack_m = False
        # Lo-Fi Radio in-rack flags
        self.lofi_radio_in_rack_a = False
        self.lofi_radio_in_rack_b = False
        self.lofi_radio_in_rack_c = False
        self.lofi_radio_in_rack_d = False
        self.lofi_radio_in_rack_m = False
        # Auto-Pan in-rack flags
        self.auto_pan_in_rack_a = False
        self.auto_pan_in_rack_b = False
        self.auto_pan_in_rack_c = False
        self.auto_pan_in_rack_d = False
        self.auto_pan_in_rack_m = False
        # Fuzz in-rack flags
        self.fuzz_in_rack_a = False
        self.fuzz_in_rack_b = False
        self.fuzz_in_rack_c = False
        self.fuzz_in_rack_d = False
        self.fuzz_in_rack_m = False
        # Granular Freeze in-rack flags
        self.granular_freeze_in_rack_a = False
        self.granular_freeze_in_rack_b = False
        self.granular_freeze_in_rack_c = False
        self.granular_freeze_in_rack_d = False
        self.granular_freeze_in_rack_m = False
        # Gated Reverb in-rack flags
        self.gate_reverb_in_rack_a = False
        self.gate_reverb_in_rack_b = False
        self.gate_reverb_in_rack_c = False
        self.gate_reverb_in_rack_d = False
        self.gate_reverb_in_rack_m = False
        # Stutter in-rack flags
        self.stutter_in_rack_a = False
        self.stutter_in_rack_b = False
        self.stutter_in_rack_c = False
        self.stutter_in_rack_d = False
        self.stutter_in_rack_m = False

        # --- Dynamic FX chain ordering ---
        # Ordered lists of effect_type strings per bus.
        # The DSP processes effects in this order (set from the GUI rack).
        # Default order matches the original hardcoded chain.
        _default_order: list[str] = []  # empty = nothing in rack initially
        self._fx_order_a: list[str] = list(_default_order)
        self._fx_order_b: list[str] = list(_default_order)
        self._fx_order_c: list[str] = list(_default_order)
        self._fx_order_d: list[str] = list(_default_order)
        self._fx_order_m: list[str] = list(_default_order)

        # Per-bus processor dispatch tables — maps effect_type → processor instance.
        # Built once at init, keyed by bus suffix letter.
        self._fx_processors: dict[str, dict[str, object]] = {
            'a': {
                'noise_gate': self.noise_gate, 'tremolo': self.tremolo,
                'auto_wah': self.auto_wah, 'fet_limiter': self.fet_limiter,
                'filter_a': self.filter_a, 'filter_b': self.filter_b,
                'phaser': self.fx_bus_1, 'chorus': self.fx_bus_1, 'flanger': self.fx_bus_1,
                'delay': self.delay, 'tape_delay': self.tape_delay,
                'reel_to_reel': self.reel_to_reel, 'cassette_tape': self.cassette_tape,
                'vhs': self.vhs, 'vinyl': self.vinyl,
                'hall_reverb': self.hall_reverb, 'plate_reverb': self.plate_reverb,
                'compressor': self.compressor, 'glue_compressor': self.glue_compressor,
                'bit_crusher': self.bit_crusher, 'tube_overdrive': self.tube_overdrive,
                'exciter': self.exciter, 'transient_shaper': self.transient_shaper,
                'ether_drift': self.ether_drift, 'harmonizer': self.harmonizer,
                'rotary': self.rotary, 'stereo_widener': self.stereo_widener,
                'freq_shifter': self.freq_shifter, 'ring_mod': self.ring_mod,
                'simple_eq': self.simple_eq, 'ping_pong_delay': self.ping_pong_delay,
                'shimmer_reverb': self.shimmer_reverb, 'lofi_radio': self.lofi_radio,
                'auto_pan': self.auto_pan, 'fuzz': self.fuzz,
                'granular_freeze': self.granular_freeze, 'gate_reverb': self.gate_reverb,
                'stutter': self.stutter,
            },
            'b': {
                'noise_gate': self.noise_gate_b, 'tremolo': self.tremolo_b,
                'auto_wah': self.auto_wah_b, 'fet_limiter': self.fet_limiter_b,
                'filter_a': self.filter_a_b, 'filter_b': self.filter_b_b,
                'phaser': self.fx_bus_2, 'chorus': self.fx_bus_2, 'flanger': self.fx_bus_2,
                'delay': self.delay_b, 'tape_delay': self.tape_delay_b,
                'reel_to_reel': self.reel_to_reel_b, 'cassette_tape': self.cassette_tape_b,
                'vhs': self.vhs_b, 'vinyl': self.vinyl_b,
                'hall_reverb': self.hall_reverb_b, 'plate_reverb': self.plate_reverb_b,
                'compressor': self.compressor_b, 'glue_compressor': self.glue_compressor_b,
                'bit_crusher': self.bit_crusher_b, 'tube_overdrive': self.tube_overdrive_b,
                'exciter': self.exciter_b, 'transient_shaper': self.transient_shaper_b,
                'ether_drift': self.ether_drift_b, 'harmonizer': self.harmonizer_b,
                'rotary': self.rotary_b, 'stereo_widener': self.stereo_widener_b,
                'freq_shifter': self.freq_shifter_b, 'ring_mod': self.ring_mod_b,
                'simple_eq': self.simple_eq_b, 'ping_pong_delay': self.ping_pong_delay_b,
                'shimmer_reverb': self.shimmer_reverb_b, 'lofi_radio': self.lofi_radio_b,
                'auto_pan': self.auto_pan_b, 'fuzz': self.fuzz_b,
                'granular_freeze': self.granular_freeze_b, 'gate_reverb': self.gate_reverb_b,
                'stutter': self.stutter_b,
            },
            'c': {
                'noise_gate': self.noise_gate_c, 'tremolo': self.tremolo_c,
                'auto_wah': self.auto_wah_c, 'fet_limiter': self.fet_limiter_c,
                'filter_a': self.filter_a_c, 'filter_b': self.filter_b_c,
                'phaser': self.fx_bus_c, 'chorus': self.fx_bus_c, 'flanger': self.fx_bus_c,
                'delay': self.delay_c, 'tape_delay': self.tape_delay_c,
                'reel_to_reel': self.reel_to_reel_c, 'cassette_tape': self.cassette_tape_c,
                'vhs': self.vhs_c, 'vinyl': self.vinyl_c,
                'hall_reverb': self.hall_reverb_c, 'plate_reverb': self.plate_reverb_c,
                'compressor': self.compressor_c, 'glue_compressor': self.glue_compressor_c,
                'bit_crusher': self.bit_crusher_c, 'tube_overdrive': self.tube_overdrive_c,
                'exciter': self.exciter_c, 'transient_shaper': self.transient_shaper_c,
                'ether_drift': self.ether_drift_c, 'harmonizer': self.harmonizer_c,
                'rotary': self.rotary_c, 'stereo_widener': self.stereo_widener_c,
                'freq_shifter': self.freq_shifter_c, 'ring_mod': self.ring_mod_c,
                'simple_eq': self.simple_eq_c, 'ping_pong_delay': self.ping_pong_delay_c,
                'shimmer_reverb': self.shimmer_reverb_c, 'lofi_radio': self.lofi_radio_c,
                'auto_pan': self.auto_pan_c, 'fuzz': self.fuzz_c,
                'granular_freeze': self.granular_freeze_c, 'gate_reverb': self.gate_reverb_c,
                'stutter': self.stutter_c,
            },
            'd': {
                'noise_gate': self.noise_gate_d, 'tremolo': self.tremolo_d,
                'auto_wah': self.auto_wah_d, 'fet_limiter': self.fet_limiter_d,
                'filter_a': self.filter_a_d, 'filter_b': self.filter_b_d,
                'phaser': self.fx_bus_d, 'chorus': self.fx_bus_d, 'flanger': self.fx_bus_d,
                'delay': self.delay_d, 'tape_delay': self.tape_delay_d,
                'reel_to_reel': self.reel_to_reel_d, 'cassette_tape': self.cassette_tape_d,
                'vhs': self.vhs_d, 'vinyl': self.vinyl_d,
                'hall_reverb': self.hall_reverb_d, 'plate_reverb': self.plate_reverb_d,
                'compressor': self.compressor_d, 'glue_compressor': self.glue_compressor_d,
                'bit_crusher': self.bit_crusher_d, 'tube_overdrive': self.tube_overdrive_d,
                'exciter': self.exciter_d, 'transient_shaper': self.transient_shaper_d,
                'ether_drift': self.ether_drift_d, 'harmonizer': self.harmonizer_d,
                'rotary': self.rotary_d, 'stereo_widener': self.stereo_widener_d,
                'freq_shifter': self.freq_shifter_d, 'ring_mod': self.ring_mod_d,
                'simple_eq': self.simple_eq_d, 'ping_pong_delay': self.ping_pong_delay_d,
                'shimmer_reverb': self.shimmer_reverb_d, 'lofi_radio': self.lofi_radio_d,
                'auto_pan': self.auto_pan_d, 'fuzz': self.fuzz_d,
                'granular_freeze': self.granular_freeze_d, 'gate_reverb': self.gate_reverb_d,
                'stutter': self.stutter_d,
            },
            'm': {
                'noise_gate': self.noise_gate_m, 'tremolo': self.tremolo_m,
                'auto_wah': self.auto_wah_m, 'fet_limiter': self.fet_limiter_m,
                'filter_a': self.filter_a_m, 'filter_b': self.filter_b_m,
                'phaser': self.fx_bus_m, 'chorus': self.fx_bus_m, 'flanger': self.fx_bus_m,
                'delay': self.delay_m, 'tape_delay': self.tape_delay_m,
                'reel_to_reel': self.reel_to_reel_m, 'cassette_tape': self.cassette_tape_m,
                'vhs': self.vhs_m, 'vinyl': self.vinyl_m,
                'hall_reverb': self.hall_reverb_m, 'plate_reverb': self.plate_reverb_m,
                'compressor': self.compressor_m, 'glue_compressor': self.glue_compressor_m,
                'bit_crusher': self.bit_crusher_m, 'tube_overdrive': self.tube_overdrive_m,
                'exciter': self.exciter_m, 'transient_shaper': self.transient_shaper_m,
                'ether_drift': self.ether_drift_m, 'harmonizer': self.harmonizer_m,
                'rotary': self.rotary_m, 'stereo_widener': self.stereo_widener_m,
                'freq_shifter': self.freq_shifter_m, 'ring_mod': self.ring_mod_m,
                'simple_eq': self.simple_eq_m, 'ping_pong_delay': self.ping_pong_delay_m,
                'shimmer_reverb': self.shimmer_reverb_m, 'lofi_radio': self.lofi_radio_m,
                'auto_pan': self.auto_pan_m, 'fuzz': self.fuzz_m,
                'granular_freeze': self.granular_freeze_m, 'gate_reverb': self.gate_reverb_m,
                'stutter': self.stutter_m,
            },
        }
        # Set of effect_types that require .state.enabled check before processing
        self._FX_NEEDS_ENABLED_CHECK = frozenset({
            'filter_a', 'filter_b', 'delay', 'tape_delay',
        })
        # Set of mod sub-effects handled via FXBus.process_mod_only
        self._FX_MOD_TYPES = frozenset({'phaser', 'chorus', 'flanger'})
        # Map bus suffix letter → the fx_order list reference
        self._fx_order_map: dict[str, list[str]] = {
            'a': self._fx_order_a, 'b': self._fx_order_b,
            'c': self._fx_order_c, 'd': self._fx_order_d,
            'm': self._fx_order_m,
        }

        # --- External plugin host pool (Foreign Exchange) ---
        # 4 StowawayProcessor slots per bus, registered into _fx_processors
        # so _process_fx_chain handles them at whatever rack position they appear.
        self._stowaway_pool: dict[str, list[StowawayProcessor]] = {
            k: [StowawayProcessor(self.sample_rate, BUFFER_SIZE) for _ in range(4)]
            for k in ('a', 'b', 'c', 'd', 'm')
        }
        for _bus_key, _pool in self._stowaway_pool.items():
            for _i, _proc in enumerate(_pool):
                self._fx_processors[_bus_key][f'stowaway_{_i}'] = _proc

        # Master filter (gentle HP/LP trim)
        self._master_hp = BiquadFilter(self.sample_rate)
        self._master_lp = BiquadFilter(self.sample_rate)
        self._master_hp_freq: float = 20.0   # default: no cut
        self._master_lp_freq: float = 20000.0  # default: no cut
        self._master_hp.set_highpass(20.0, 0.707)
        self._master_lp.set_lowpass(20000.0, 0.707)
        # Pending filter params — set from GUI thread, applied on DSP thread
        self._pending_master_hp: Optional[float] = None
        self._pending_master_lp: Optional[float] = None

        # Stream
        self._stream: Optional[sd.OutputStream] = None
        self._input_stream: Optional[sd.InputStream] = None
        self._running = False

        # Thread-safe event queue — all channel trigger/stop mutations are
        # deferred to the audio thread to avoid races with process().
        # deque.append / popleft are atomic in CPython.
        # maxlen prevents unbounded growth under trigger spam (e.g. runaway MIDI).
        self._event_queue: deque = deque(maxlen=256)

        # Callback for UI updates (called from audio thread, should be fast)
        self._meter_callback: Optional[Callable] = None
        # Thread-safe meter queue — DSP worker pushes, GUI thread polls
        self._meter_queue: deque = deque(maxlen=2)

        # DSP error tracking — count consecutive exceptions so the GUI
        # can surface a warning instead of silently dropping audio.
        self._dsp_error_count: int = 0
        self._dsp_last_error: Optional[str] = None
        self._dsp_error_logged_at: float = -1e9
        # Per-effect failures are rate-limited in the log (a broken effect
        # raises on every block) and surfaced once to the GUI.
        self._fx_error_logged_at: dict[tuple[str, str], float] = {}
        self._fx_error_suppressed: dict[tuple[str, str], int] = {}
        self._fx_error_notice: Optional[str] = None

        # Double-buffered input for recording — input callback writes to one
        # buffer while the DSP worker reads from the other, avoiding torn reads.
        self._input_bufs = [
            np.zeros((self.buffer_size, 2), dtype=np.float32),
            np.zeros((self.buffer_size, 2), dtype=np.float32),
        ]
        self._input_write_idx = 0  # callback writes here
        self._input_read_idx = -1  # -1 = nothing ready
        self._input_copy = np.zeros((self.buffer_size, 2), dtype=np.float32)

        # Pre-allocated AUX scaling buffer (avoids per-block allocation)
        self._aux_buf = np.zeros((self.buffer_size, 2), dtype=np.float32)

        # Peak meters
        self.output_peak = np.zeros(2, dtype=np.float32)
        self._fx1_peak = np.zeros(2, dtype=np.float32)
        self._fx2_peak = np.zeros(2, dtype=np.float32)
        self._fx3_peak = np.zeros(2, dtype=np.float32)
        self._fx4_peak = np.zeros(2, dtype=np.float32)

        # Pre-allocated temp buffer for peak metering (avoids allocation in callback)
        self._peak_tmp = np.zeros(self.buffer_size, dtype=np.float32)
        self._peak_tmp2 = np.zeros((self.buffer_size, 2), dtype=np.float32)

        # Meter callback decimation — send every Nth callback (~30 fps)
        self._meter_skip = max(1, int(self.sample_rate / self.buffer_size / 30))
        self._meter_counter = 0

        # Pre-allocated waveform buffers for meter callback (avoids .tolist() allocation)
        self._wf_len = max(1, self.buffer_size // max(1, self.buffer_size // 64))
        self._wf_master_l = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_master_r = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_fx1_l = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_fx1_r = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_fx2_l = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_fx2_r = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_fx3_l = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_fx3_r = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_fx4_l = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_fx4_r = np.zeros(self._wf_len, dtype=np.float32)

        # Pre-allocated waveform snapshot buffers (avoid .copy() on DSP thread)
        self._wf_snap_master_l = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_snap_master_r = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_snap_fx1_l = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_snap_fx1_r = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_snap_fx2_l = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_snap_fx2_r = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_snap_fx3_l = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_snap_fx3_r = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_snap_fx4_l = np.zeros(self._wf_len, dtype=np.float32)
        self._wf_snap_fx4_r = np.zeros(self._wf_len, dtype=np.float32)

        # Pre-allocated channel output list to avoid per-callback allocation
        self._channel_outputs: list[tuple[int, np.ndarray]] = []
        self._co_count = 0

        # ── Ring buffer for decoupled audio output ──
        # The DSP worker fills this ahead of time; the sounddevice callback
        # just copies the next block out.  8 blocks ≈ 93 ms of lookahead at
        # 512 frames / 44.1 kHz.  Doubled from 4 → 8 to absorb the GIL
        # contention spike caused by Qt setStyleSheet + paintEvent when the
        # user switches grid modes (mix ↔ grid) on the main thread.
        # Trigger latency is unaffected (still single-block).
        self._ring_blocks = 8
        # _out_channels is set to 2 now and recomputed in start() when
        # multi-channel output is enabled.
        self._out_channels: int = 2
        self._ring_buf = np.zeros(
            (self._ring_blocks, self.buffer_size, 2), dtype=np.float32
        )
        # Atomic indices — only the worker writes _ring_write, only the
        # callback writes _ring_read.  Both are plain ints (GIL-atomic).
        self._ring_write = 0   # next slot the worker will fill
        self._ring_read = 0    # next slot the callback will consume
        self._ring_buf_full = False  # True when all ring slots are filled
        # Event to wake the worker when the callback consumes a block
        self._ring_event = threading.Event()
        # Worker thread handle
        self._dsp_thread: Optional[threading.Thread] = None

        # ── Real-time profiling (enabled via MLRVP_DEBUG=1 env var) ──
        self._prof_enabled = os.environ.get('MLRVP_DEBUG', '').strip() == '1'
        self._prof_count = 0
        self._prof_interval = int(self.sample_rate / self.buffer_size * 5)  # ~5s
        self._prof_total = 0.0
        self._prof_channels = 0.0
        self._prof_mixer = 0.0
        self._prof_fx = 0.0
        self._prof_master = 0.0
        self._prof_meter = 0.0
        self._prof_max = 0.0
        self._prof_overruns = 0  # blocks that exceeded budget
        self._prof_budget = self.buffer_size / self.sample_rate
        self._prof_ring_min = self._ring_blocks  # track min ring buffer occupancy
        self._prof_ring_sum = 0

        # Pre-allocated meter dict — reused every meter callback to avoid GC
        self._meter_dict = {
            "master": [0.0, 0.0],
            "groups": [0.0] * NUM_GROUPS,
            "fx1": [0.0, 0.0],
            "fx2": [0.0, 0.0],
            "fx3": [0.0, 0.0],
            "fx4": [0.0, 0.0],
            "waveform_l": [],
            "waveform_r": [],
            "waveform_fx1_l": [],
            "waveform_fx1_r": [],
            "waveform_fx2_l": [],
            "waveform_fx2_r": [],
            "waveform_fx3_l": [],
            "waveform_fx3_r": [],
            "waveform_fx4_l": [],
            "waveform_fx4_r": [],
            "compressor_gr_a": 0.0,
            "compressor_gr_b": 0.0,
            "compressor_gr_c": 0.0,
            "compressor_gr_d": 0.0,
            "compressor_gr_m": 0.0,
            "glue_compressor_gr_a": 0.0,
            "glue_compressor_gr_b": 0.0,
            "glue_compressor_gr_c": 0.0,
            "glue_compressor_gr_d": 0.0,
            "glue_compressor_gr_m": 0.0,
            "fet_limiter_gr_a": 0.0,
            "fet_limiter_gr_b": 0.0,
            "fet_limiter_gr_c": 0.0,
            "fet_limiter_gr_d": 0.0,
            "fet_limiter_gr_m": 0.0,
        }
        # Double-buffered meter snapshots — avoids dict()/list() copies
        # on the DSP thread.  DSP writes into _meter_snap[_meter_snap_idx],
        # then enqueues a reference and flips the index.
        def _make_snap():
            return {
                "master": [0.0, 0.0],
                "groups": [0.0] * NUM_GROUPS,
                "fx1": [0.0, 0.0],
                "fx2": [0.0, 0.0],
                "fx3": [0.0, 0.0],
                "fx4": [0.0, 0.0],
                "waveform_l": [],
                "waveform_r": [],
                "waveform_fx1_l": [],
                "waveform_fx1_r": [],
                "waveform_fx2_l": [],
                "waveform_fx2_r": [],
                "waveform_fx3_l": [],
                "waveform_fx3_r": [],
                "waveform_fx4_l": [],
                "waveform_fx4_r": [],
                "compressor_gr_a": 0.0,
                "compressor_gr_b": 0.0,
                "compressor_gr_c": 0.0,
                "compressor_gr_d": 0.0,
                "compressor_gr_m": 0.0,
                "glue_compressor_gr_a": 0.0,
                "glue_compressor_gr_b": 0.0,
                "glue_compressor_gr_c": 0.0,
                "glue_compressor_gr_d": 0.0,
                "glue_compressor_gr_m": 0.0,
                "fet_limiter_gr_a": 0.0,
                "fet_limiter_gr_b": 0.0,
                "fet_limiter_gr_c": 0.0,
                "fet_limiter_gr_d": 0.0,
                "fet_limiter_gr_m": 0.0,
            }
        self._meter_snap = [_make_snap(), _make_snap()]
        self._meter_snap_idx = 0

    def set_meter_callback(self, callback: Callable):
        self._meter_callback = callback

    def poll_meters(self) -> Optional[dict]:
        """Return the latest meter snapshot, or None if nothing new.

        Call this from the GUI refresh timer (main thread) instead of
        having the DSP worker invoke a Python callback — avoids GIL
        contention that could cause audio dropouts.
        """
        md = None
        try:
            while self._meter_queue:
                md = self._meter_queue.popleft()
        except IndexError:
            pass
        return md

    def poll_dsp_errors(self) -> Optional[tuple]:
        """Return (count, message) if DSP errors have accumulated, else None.

        Call from GUI refresh timer.  Resets the counter once read.
        """
        count = self._dsp_error_count
        if count == 0:
            return None
        msg = self._dsp_last_error
        self._dsp_error_count = 0
        self._dsp_last_error = None
        return (count, msg)

    def poll_fx_error_notice(self) -> Optional[str]:
        """Return a user-facing message if an effect started failing, else None.

        Call from the GUI refresh timer.  Cleared once read.
        """
        msg = self._fx_error_notice
        if msg is not None:
            self._fx_error_notice = None
        return msg

    _ERROR_LOG_INTERVAL = 10.0  # seconds between repeated tracebacks
    _BUS_DISPLAY = {'a': 'FX1', 'b': 'FX2', 'c': 'FX3', 'd': 'FX4', 'm': 'Master'}

    def _report_fx_error(self, fx_type: str, bus_key: str) -> None:
        """Log an effect exception (call from its except block), rate-limited."""
        key = (bus_key, fx_type)
        now = time.monotonic()
        if now - self._fx_error_logged_at.get(key, -1e9) < self._ERROR_LOG_INTERVAL:
            self._fx_error_suppressed[key] = self._fx_error_suppressed.get(key, 0) + 1
            return
        repeats = self._fx_error_suppressed.pop(key, 0)
        first = key not in self._fx_error_logged_at
        self._fx_error_logged_at[key] = now
        log.warning(
            "FX '%s' on bus '%s' raised an exception — bypassed%s",
            fx_type, bus_key,
            f" ({repeats} repeats in the last {self._ERROR_LOG_INTERVAL:.0f}s)" if repeats else "",
            exc_info=True,
        )
        if first:
            rack = self._BUS_DISPLAY.get(bus_key, bus_key)
            self._fx_error_notice = (
                f"⚠ Effect '{fx_type}' on {rack} is failing and was bypassed — see log"
            )

    def set_profiling(self, enabled: bool):
        """Toggle DSP profiling at runtime."""
        self._prof_enabled = enabled
        if enabled:
            self._prof_count = 0
            self._prof_total = 0.0
            self._prof_channels = 0.0
            self._prof_mixer = 0.0
            self._prof_fx = 0.0
            self._prof_master = 0.0
            self._prof_meter = 0.0
            self._prof_max = 0.0
            self._prof_overruns = 0
            self._prof_ring_min = self._ring_blocks
            self._prof_ring_sum = 0
            log.info("[AudioEngine] Profiling ENABLED")
        else:
            log.info("[AudioEngine] Profiling DISABLED")

    def start(self):
        """Start the audio engine."""
        if self._running:
            return

        log.info(
            "[AudioEngine] Starting — Numba JIT: %s, SR=%s, buffer=%s, "
            "budget=%.2fms, ring=%s blocks, profiling=%s",
            _HAS_NUMBA, self.sample_rate, self.buffer_size,
            self.buffer_size / self.sample_rate * 1000,
            self._ring_blocks,
            'ON' if self._prof_enabled else 'OFF (set MLRVP_DEBUG=1 to enable)',
        )

        # Disable Python's cyclic GC to prevent unpredictable collection
        # pauses on the audio thread.  A dedicated background thread runs
        # gen-0 collections once per second.
        gc.disable()
        self._gc_running = True
        self._gc_thread = threading.Thread(
            target=self._gc_loop, name="gc-worker", daemon=True
        )
        self._gc_thread.start()

        try:
            # Compute output channel count for this session.
            # Multi-channel mode: Master=ch1-2, Group1=ch3-4, ..., GroupN=ch(2N+1)-(2N+2)
            # Clamp to the device's actual max so we degrade gracefully instead
            # of hard-failing on stereo-only or limited-channel devices.
            if getattr(self.config, 'multichannel_output', False):
                num_grp = min(
                    getattr(self.config, 'active_groups', NUM_GROUPS), NUM_GROUPS
                )
                desired = 2 + 2 * num_grp
                output_device_idx = (
                    self.config.output_device if self.config.output_device >= 0 else None
                )
                try:
                    dev_info = (
                        sd.query_devices(output_device_idx, 'output')
                        if output_device_idx is not None
                        else sd.query_devices(kind='output')
                    )
                    dev_max = int(dev_info.get('max_output_channels', desired))
                except Exception:
                    dev_max = desired  # can't query — try the full count
                # Clamp to even number (complete stereo pairs only), minimum 2
                clamped = max(2, min(desired, dev_max))
                if clamped % 2:
                    clamped -= 1
                self._out_channels = clamped
            else:
                self._out_channels = 2

            # Re-allocate the ring buffer to match the new channel count
            self._ring_buf = np.zeros(
                (self._ring_blocks, self.buffer_size, self._out_channels),
                dtype=np.float32,
            )

            output_device = self.config.output_device if self.config.output_device >= 0 else None
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                blocksize=self.buffer_size,
                channels=self._out_channels,
                dtype='float32',
                callback=self._audio_callback,
                device=output_device,
                latency='low',
            )
            # Pre-fill the ring buffer BEFORE starting the stream so the
            # callback has audio from the very first invocation.
            self._ring_read = 0
            self._ring_write = 0
            self._ring_buf_full = False
            try:
                for i in range(self._ring_blocks - 1):
                    slot = self._ring_buf[i]
                    slot[:] = 0
                    self._process_block(self.buffer_size, slot)
                    self._ring_write = (self._ring_write + 1) % self._ring_blocks
            except Exception:
                self._stream.close()
                self._stream = None
                gc.enable()
                raise
            self._ring_event.clear()
            try:
                self._stream.start()
            except Exception:
                self._stream.close()
                self._stream = None
                raise
            self._running = True
        except Exception as e:
            # Stop the GC worker thread we started before the try block
            self._gc_running = False
            if self._gc_thread is not None:
                self._gc_thread.join(timeout=0.5)
                self._gc_thread = None
            gc.enable()
            log.error("Audio engine start error: %s", e)
            raise

        # Launch the DSP worker thread
        self._dsp_thread = threading.Thread(
            target=self._dsp_worker, name="dsp-worker", daemon=True
        )
        self._dsp_thread.start()

        # Start input stream for recording if input device available
        try:
            input_device = self.config.input_device if self.config.input_device >= 0 else None
            self._input_stream = sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=self.buffer_size,
                channels=2,
                dtype='float32',
                callback=self._input_callback,
                device=input_device,
                latency='low',
            )
            try:
                self._input_stream.start()
            except Exception:
                self._input_stream.close()
                self._input_stream = None
                raise
        except Exception as e:
            # Input not critical - recording just won't work
            log.warning("[AudioEngine] Input stream unavailable (recording disabled): %s", e)
            self._input_stream = None

    def stop(self):
        """Stop the audio engine."""
        self._running = False
        # Wake the worker so it sees _running=False and exits
        self._ring_event.set()
        if self._dsp_thread is not None:
            self._dsp_thread.join(timeout=2.0)
            self._dsp_thread = None
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._input_stream is not None:
            self._input_stream.stop()
            self._input_stream.close()
            self._input_stream = None
        # Re-enable automatic GC and stop the GC worker thread
        self._gc_running = False
        if hasattr(self, '_gc_thread') and self._gc_thread is not None:
            self._gc_thread.join(timeout=2.0)
            self._gc_thread = None
        gc.enable()
        gc.collect()

    # Runs the periodic collection somewhere other than the gc-worker thread
    # (the app passes run_on_main_thread).  Collecting on a worker thread can
    # destroy unreachable Qt objects there, which Qt does not allow.
    gc_scheduler: Optional[Callable[[Callable[[], None]], None]] = None

    def _gc_loop(self):
        """Background thread that runs gen-0 GC collection every 2 seconds.

        Uses a simple loop instead of threading.Timer to avoid creating
        a new Thread object every second — those objects can't be collected
        while the cyclic GC is disabled, causing a slow memory leak.

        Gen-1/gen-2 thresholds are raised to prevent gen-0 from escalating
        into a full collection that can stall the DSP thread for 50-200ms.
        """
        # Prevent gen-0 from cascading into expensive gen-1/gen-2 sweeps.
        # Default thresholds are (700, 10, 10) — after 10 gen-0 runs,
        # Python auto-promotes to gen-1, which can take 10-100ms.
        # Set gen-1/gen-2 very high so only cheap gen-0 runs automatically.
        gc.set_threshold(700, 50, 50)
        while self._gc_running:
            time.sleep(2.0)
            if self._gc_running:
                scheduler = self.gc_scheduler
                if scheduler is not None:
                    scheduler(_collect_young)
                else:
                    _collect_young()

    def _input_callback(self, indata, frames, time_info, status):
        """Capture input audio for recording (double-buffered)."""
        wi = self._input_write_idx
        self._input_bufs[wi][:frames] = indata
        # Publish the filled buffer and swap to the other one
        self._input_read_idx = wi
        self._input_write_idx = 1 - wi

    def _audio_callback(self, outdata, frames, time_info, status):
        """Sounddevice callback — copies pre-computed audio from ring buffer.

        Runs on the PortAudio C thread.  Does NO DSP — just a memcpy from
        the ring buffer slot that the worker thread has already filled.
        If the worker hasn't filled the next slot yet (underrun), output
        silence and carry on.
        """
        r = self._ring_read
        w = self._ring_write
        # How many blocks are available?  (w - r) mod ring_blocks
        available = (w - r) % self._ring_blocks if w != r else (
            self._ring_blocks if self._ring_buf_full else 0
        )
        if self._prof_enabled:
            if available < self._prof_ring_min:
                self._prof_ring_min = available
            self._prof_ring_sum += available
        if available > 0:
            outdata[:] = self._ring_buf[r % self._ring_blocks][:frames]
            self._ring_read = (r + 1) % self._ring_blocks
            self._ring_buf_full = False
            # Wake the worker so it can fill the now-free slot
            self._ring_event.set()
        else:
            # Underrun — output silence
            outdata[:] = 0

    def _process_block(self, frames: int, outbuf: np.ndarray):
        """Run the full DSP pipeline for one block, writing into *outbuf*.

        Called by the DSP worker thread — never from the audio callback.
        Exceptions propagate to _dsp_worker() for error counting/surfacing.
        """
        _prof = self._prof_enabled
        if _prof:
            _t0 = time.perf_counter()

        # ── Process pending trigger/stop events (thread-safe) ──
        # Cap to 16 events per block to prevent trigger-spam storms from
        # consuming the entire block budget.  Remaining events spill to the
        # next block (~5.8 ms later — inaudible delay).
        eq = self._event_queue
        _ev_budget = 16
        while eq and _ev_budget > 0:
            _ev_budget -= 1
            event = eq.popleft()
            cmd = event[0]
            if cmd == 'trigger':
                self.channels[event[1]].trigger(event[2], event[3])
            elif cmd == 'stop':
                self.channels[event[1]].stop()
            elif cmd == 'force_stop':
                self.channels[event[1]].force_stop()
            elif cmd == 'set_pos':
                self.channels[event[1]]._position = event[2]
            elif cmd == 'load_sample':
                self.channels[event[1]].load_sample(event[2])
            elif cmd == 'reset_fx':
                self._do_reset_all_fx()
            elif cmd == 'arm_watch':
                # Remove any existing watch for this row, then add the new one
                self._audio_arm_watches = [
                    w for w in self._audio_arm_watches if w[0] != event[1]
                ]
                self._audio_arm_watches.append((event[1], event[2], event[3]))
            elif cmd == 'disarm_watch':
                self._audio_arm_watches = [
                    w for w in self._audio_arm_watches if w[0] != event[1]
                ]

        # Capture input audio whenever a device frame is available — needed for
        # pre-roll ring buffer, input level metering, and any active recorder/AUX.
        _live_active = self._live_recorder is not None and self._live_recorder.is_recording
        ri = self._input_read_idx
        if ri >= 0:
            input_audio = self._input_copy[:frames]
            input_audio[:] = self._input_bufs[ri][:frames]
            if self._input_read_idx == ri:
                self._input_read_idx = -1
        else:
            input_audio = None

        # Always feed the pre-roll ring buffer when input is available
        if input_audio is not None:
            self.pre_roll.process(input_audio)

        # Process recording input — skip here if loopback mode (handled post-master)
        rec_trim = float(self.config.recorder.input_trim)
        if (input_audio is not None and self.recorder.recording
                and not self.recorder.use_loopback):
            thru = self.recorder.process(input_audio, rec_trim)
        elif input_audio is not None and not self.recorder.recording:
            # Run process() even when not recording so input_peak/rms update
            # and thru-monitor (if enabled) still mixes through.
            thru = self.recorder.process(input_audio, rec_trim)
        else:
            thru = None

        # Process LIVE-mode looper input (separate from main recorder).
        # If thru monitoring is on, mix into main bus alongside recorder thru.
        if input_audio is not None and _live_active:
            live_thru = self._live_recorder.process(input_audio, self._live_recorder_trim)
            if live_thru is not None:
                if thru is None:
                    thru = live_thru
                else:
                    thru = thru + live_thru
            # Hand off completed recorder to GUI thread WITHOUT calling
            # finalize() here — finalize allocates a new MB-sized buffer
            # and runs a crossfade loop, which would risk an audio underrun.
            # The callback receives the recorder; GUI thread finalizes.
            if self._live_recorder.is_done:
                row = self._live_recorder_row
                done_rec = self._live_recorder
                cb = self._live_recorded_callback
                self._live_recorder = None
                self._live_recorder_row = -1
                self._live_recorder_trim = 1.0
                if cb is not None:
                    try:
                        cb(row, done_rec)
                    except Exception:
                        pass

        # Process all active channels — reuse pre-allocated output slot list
        # to avoid creating (group, audio) tuples every block.
        channel_outputs = self._channel_outputs
        co_count = 0
        active_count = min(self.config.row_count, MAX_ROWS)

        # Snapshot for SAMP exclude-self: the row's post-pan/filter audio so we
        # can subtract it from the captured bus before recording (anti-feedback).
        samp_row_audio = None
        samp_row = self._samp_recorder_row if (self._samp_recorder is not None
                                                and self._samp_recorder.is_recording
                                                and self._samp_exclude_self) else -1

        for i in range(active_count):
            ch = self.channels[i]
            if ch._playing or ch.state.playing:
                audio = ch.process(frames)
                # Apply per-channel filter (independent LP/HP knobs)
                cf = self.channel_filters[i]
                cf.set_lp(ch.state.ch_lp)
                cf.set_hp(ch.state.ch_hp)
                if ch.state.ch_lp > 0.01 or ch.state.ch_hp > 0.01:
                    audio = cf.process(audio)
                # Apply per-channel pan as a stereo balance: centre is unity
                # on both sides and panning only attenuates the opposite side
                # (continuous at centre, never boosts — samples are stereo).
                _pan = ch.state.pan
                if _pan != 0.5:
                    audio = audio * np.array(
                        [min(1.0, 2.0 * (1.0 - _pan)), min(1.0, 2.0 * _pan)],
                        dtype=np.float32,
                    )
                if i == samp_row:
                    samp_row_audio = audio
                if co_count < len(channel_outputs):
                    channel_outputs[co_count][0] = ch.state.group
                    channel_outputs[co_count][1] = audio
                else:
                    channel_outputs.append([ch.state.group, audio])
                co_count += 1
        self._co_count = co_count

        if _prof:
            _t1 = time.perf_counter()

        # Mix through groups (now returns FX bus outputs too)
        _r = self.mixer.process_groups(channel_outputs[:co_count], frames)
        main_bus = _r[0]; aux_send = _r[1]
        fx1_bus = _r[2]; fx2_bus = _r[3]; fx3_bus = _r[4]; fx4_bus = _r[5]

        # Add recorder thru to main bus if active
        if thru is not None:
            main_bus += thru

        # --- AUX input channel (external audio source) ---
        if not self.mixer.aux_mute and input_audio is not None:
            self._aux_buf[:frames] = input_audio[:frames]
            self._aux_buf[:frames] *= self.mixer.aux_volume
            aux_scaled = self._aux_buf[:frames]
            # Route AUX to FX bus or main
            aux_route = self.mixer.aux_fx_route
            if aux_route == 1:
                fx1_bus += aux_scaled
            elif aux_route == 2:
                fx2_bus += aux_scaled
            elif aux_route == 3:
                fx3_bus += aux_scaled
            elif aux_route == 4:
                fx4_bus += aux_scaled
            else:
                main_bus += aux_scaled

        if _prof:
            _t2 = time.perf_counter()

        # --- FX Processing Chain (per-bus: rack A → FX1, rack B → FX2) ---
        # Dynamic order: processes effects in the order set by the rack UI.
        fx1_bus = self._process_fx_chain(fx1_bus, 'a', frames)
        fx2_bus = self._process_fx_chain(fx2_bus, 'b', frames)

        # --- FX Processing Chain C (rack C → FX3) ---
        fx3_bus = self._process_fx_chain(fx3_bus, 'c', frames)

        # --- FX Processing Chain D (rack D → FX4) ---
        fx4_bus = self._process_fx_chain(fx4_bus, 'd', frames)

        # Track FX bus peaks before summing (for per-rack metering)
        # Use pre-allocated stereo abs buffer to avoid per-column view allocs.
        # NaN guard: a divergent FX effect can produce NaN which, without the
        # guard, permanently corrupts the peak meter (max(NaN, x) == NaN).
        # Mirrors the np.isfinite() guard in mixer.py group_peaks.
        if frames > 0:
            _pt = self._peak_tmp2[:frames]
            np.abs(fx1_bus[:frames], out=_pt)
            _p0 = float(_pt[:, 0].max()); _p1 = float(_pt[:, 1].max())
            if not np.isfinite(_p0): _p0 = 0.0
            if not np.isfinite(_p1): _p1 = 0.0
            self._fx1_peak[0] = max(self._fx1_peak[0] * 0.95, _p0)
            self._fx1_peak[1] = max(self._fx1_peak[1] * 0.95, _p1)
            np.abs(fx2_bus[:frames], out=_pt)
            _p0 = float(_pt[:, 0].max()); _p1 = float(_pt[:, 1].max())
            if not np.isfinite(_p0): _p0 = 0.0
            if not np.isfinite(_p1): _p1 = 0.0
            self._fx2_peak[0] = max(self._fx2_peak[0] * 0.95, _p0)
            self._fx2_peak[1] = max(self._fx2_peak[1] * 0.95, _p1)
            np.abs(fx3_bus[:frames], out=_pt)
            _p0 = float(_pt[:, 0].max()); _p1 = float(_pt[:, 1].max())
            if not np.isfinite(_p0): _p0 = 0.0
            if not np.isfinite(_p1): _p1 = 0.0
            self._fx3_peak[0] = max(self._fx3_peak[0] * 0.95, _p0)
            self._fx3_peak[1] = max(self._fx3_peak[1] * 0.95, _p1)
            np.abs(fx4_bus[:frames], out=_pt)
            _p0 = float(_pt[:, 0].max()); _p1 = float(_pt[:, 1].max())
            if not np.isfinite(_p0): _p0 = 0.0
            if not np.isfinite(_p1): _p1 = 0.0
            self._fx4_peak[0] = max(self._fx4_peak[0] * 0.95, _p0)
            self._fx4_peak[1] = max(self._fx4_peak[1] * 0.95, _p1)

        # Capture per-bus waveform for oscilloscopes (before summing)
        # Only compute waveform data when we'll actually send it
        _send_meters = False
        if self._meter_callback is not None:
            self._meter_counter += 1
            if self._meter_counter >= self._meter_skip:
                self._meter_counter = 0
                _send_meters = True
                _bus_step = max(1, frames // 64)
                _wn = len(fx1_bus[::_bus_step, 0])
                self._wf_fx1_l[:_wn] = fx1_bus[::_bus_step, 0]
                self._wf_fx1_r[:_wn] = fx1_bus[::_bus_step, 1]
                self._wf_fx2_l[:_wn] = fx2_bus[::_bus_step, 0]
                self._wf_fx2_r[:_wn] = fx2_bus[::_bus_step, 1]
                self._wf_fx3_l[:_wn] = fx3_bus[::_bus_step, 0]
                self._wf_fx3_r[:_wn] = fx3_bus[::_bus_step, 1]
                self._wf_fx4_l[:_wn] = fx4_bus[::_bus_step, 0]
                self._wf_fx4_r[:_wn] = fx4_bus[::_bus_step, 1]

        main_bus += fx1_bus
        main_bus += fx2_bus
        main_bus += fx3_bus
        main_bus += fx4_bus

        # Early NaN flush — prevent corrupted per-bus FX output from poisoning
        # the master chain's internal state (delay lines, filter histories).
        np.nan_to_num(main_bus, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

        if _prof:
            _t3 = time.perf_counter()

        # Master output
        dsp_output = self.mixer.apply_master(main_bus, aux_send)

        # Master filter trim (HP then LP)
        # Apply any pending coefficient changes from the GUI thread
        hp_pending = self._pending_master_hp
        if hp_pending is not None:
            self._pending_master_hp = None
            self._master_hp.set_highpass(hp_pending, 0.707)
            self._master_hp_freq = hp_pending
        lp_pending = self._pending_master_lp
        if lp_pending is not None:
            self._pending_master_lp = None
            self._master_lp.set_lowpass(lp_pending, 0.707)
            self._master_lp_freq = lp_pending
        if self._master_hp_freq > 21.0:
            dsp_output = self._master_hp.process(dsp_output)
        if self._master_lp_freq < 19900.0:
            dsp_output = self._master_lp.process(dsp_output)

        # --- FX Group M (Master rack) — post-master-trim chain ---
        dsp_output = self._process_fx_chain(dsp_output, 'm', frames)

        if _prof:
            _t4 = time.perf_counter()

        # Flush any NaN/Inf that crept through the FX chain to silence.
        # Cheaper than checking every effect — one pass at the end.
        np.nan_to_num(dsp_output, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

        # Loopback: capture the final, NaN-cleaned master output into the
        # recorder (allows resampling the live mix without external routing).
        # Must run AFTER nan_to_num so a transient FX NaN doesn't poison the
        # recording buffer permanently.
        if self.recorder.recording and self.recorder.use_loopback:
            self.recorder.process(dsp_output, rec_trim)

        # SAMP / resample bus capture — runs after master FX so the captured
        # bus reflects exactly what is heard.  Routes the right tap based on
        # source_bus and applies exclude-self subtraction for anti-feedback.
        if self._samp_recorder is not None and self._samp_recorder.is_recording:
            src = self._samp_source_bus
            tap = None
            if src == 1:                        # MAIN
                tap = dsp_output
            elif 2 <= src <= 5:                 # FX-A..D (post-FX, pre-master-sum)
                _fx_buses = (fx1_bus, fx2_bus, fx3_bus, fx4_bus)
                tap = _fx_buses[src - 2]
            elif 6 <= src < 6 + NUM_GROUPS:    # GRP-1..N (post group-vol, pre-route)
                g = src - 6
                if 0 <= g < len(self.mixer._group_audio):
                    tap = self.mixer._group_audio[g][:frames]
            if tap is not None:
                # Exclude-self: subtract this row's pre-group audio from the
                # tap to suppress feedback.  This is an approximation (does not
                # account for group volume scaling or FX wet/dry routing) but
                # cancels the dominant feedback signal for the common case.
                if self._samp_exclude_self and samp_row_audio is not None:
                    n = min(len(tap), len(samp_row_audio))
                    if n > 0:
                        tap = tap.copy()
                        tap[:n] -= samp_row_audio[:n]
                self._samp_recorder.process(tap)
            # Hand off completed recorder to GUI thread (mirrors live recorder)
            if self._samp_recorder.is_done:
                row = self._samp_recorder_row
                done_rec = self._samp_recorder
                cb = self._samp_recorded_callback
                self._samp_recorder = None
                self._samp_recorder_row = -1
                self._samp_source_bus = 0
                if cb is not None:
                    try:
                        cb(row, done_rec)
                    except Exception:
                        pass

        # Audio-threshold arm watches — single-fire per entry.  Checked every
        # block so latency is at most one buffer (~12 ms at 44100/512).
        if self._audio_arm_watches and self._audio_arm_callback:
            _fired_rows: list = []
            for (w_row, w_src, w_thresh) in self._audio_arm_watches:
                tap = None
                if w_src == 0:                          # INPUT — hardware input
                    tap = input_audio
                elif w_src == 1:                        # MAIN
                    tap = dsp_output
                elif 2 <= w_src <= 5:                   # FX-A..D
                    _fw = (fx1_bus, fx2_bus, fx3_bus, fx4_bus)
                    tap = _fw[w_src - 2]
                elif 6 <= w_src < 6 + NUM_GROUPS:      # GRP-1..N
                    _g = w_src - 6
                    if 0 <= _g < len(self.mixer._group_audio):
                        tap = self.mixer._group_audio[_g][:frames]
                if tap is not None and len(tap) > 0:
                    peak = float(np.max(np.abs(tap)))
                    if peak >= w_thresh:
                        _fired_rows.append(w_row)
            if _fired_rows:
                # Remove fired watches before callback to prevent re-entry
                _fired_set = set(_fired_rows)
                self._audio_arm_watches = [
                    w for w in self._audio_arm_watches if w[0] not in _fired_set
                ]
                cb = self._audio_arm_callback
                if cb is not None:
                    for _fr in _fired_rows:
                        try:
                            cb(_fr)
                        except Exception:
                            pass

        # Song Recorder — always loopback; armed_or_recording drives VU metering too
        if self.song_recorder.armed_or_recording:
            self.song_recorder.process(dsp_output)

        # Peak metering — use stereo abs buffer to avoid column view allocs
        if len(dsp_output) > 0:
            _pt = self._peak_tmp2[:len(dsp_output)]
            np.abs(dsp_output, out=_pt)
            self.output_peak[0] = max(self.output_peak[0] * 0.95, float(_pt[:, 0].max()))
            self.output_peak[1] = max(self.output_peak[1] * 0.95, float(_pt[:, 1].max()))

        out_frames = len(dsp_output)
        if self._out_channels > 2:
            # Multi-channel output: master on ch 1-2, each group on its own pair.
            # mixer._group_audio[g] holds post-fader, post-mute, pre-FX audio —
            # the cleanest "stem" for DAW recording.
            outbuf[:out_frames, :2] = dsp_output
            grp_count = (self._out_channels - 2) // 2
            for _g in range(grp_count):
                _ga = self.mixer._group_audio[_g]
                outbuf[:out_frames, 2 + _g * 2 : 4 + _g * 2] = _ga[:out_frames]
        else:
            outbuf[:] = dsp_output

        # Meter callback for UI — decimated to ~30 fps to reduce GC pressure
        if _send_meters:
            step = max(1, len(dsp_output) // 64)
            _wn = len(dsp_output[::step, 0])
            self._wf_master_l[:_wn] = dsp_output[::step, 0]
            self._wf_master_r[:_wn] = dsp_output[::step, 1]
            # Write into pre-allocated working dict (no allocations)
            md = self._meter_dict
            md["master"][0] = float(self.output_peak[0])
            md["master"][1] = float(self.output_peak[1])
            gp = self.mixer.group_peaks
            md_groups = md["groups"]
            for gi in range(NUM_GROUPS):
                md_groups[gi] = gp[gi]
            md["fx1"][0] = float(self._fx1_peak[0])
            md["fx1"][1] = float(self._fx1_peak[1])
            md["fx2"][0] = float(self._fx2_peak[0])
            md["fx2"][1] = float(self._fx2_peak[1])
            md["fx3"][0] = float(self._fx3_peak[0])
            md["fx3"][1] = float(self._fx3_peak[1])
            md["fx4"][0] = float(self._fx4_peak[0])
            md["fx4"][1] = float(self._fx4_peak[1])
            self._wf_snap_master_l[:_wn] = self._wf_master_l[:_wn]
            self._wf_snap_master_r[:_wn] = self._wf_master_r[:_wn]
            self._wf_snap_fx1_l[:_wn] = self._wf_fx1_l[:_wn]
            self._wf_snap_fx1_r[:_wn] = self._wf_fx1_r[:_wn]
            self._wf_snap_fx2_l[:_wn] = self._wf_fx2_l[:_wn]
            self._wf_snap_fx2_r[:_wn] = self._wf_fx2_r[:_wn]
            self._wf_snap_fx3_l[:_wn] = self._wf_fx3_l[:_wn]
            self._wf_snap_fx3_r[:_wn] = self._wf_fx3_r[:_wn]
            self._wf_snap_fx4_l[:_wn] = self._wf_fx4_l[:_wn]
            self._wf_snap_fx4_r[:_wn] = self._wf_fx4_r[:_wn]
            md["compressor_gr_a"] = self.compressor.gr_db
            md["compressor_gr_b"] = self.compressor_b.gr_db
            md["compressor_gr_c"] = self.compressor_c.gr_db
            md["compressor_gr_d"] = self.compressor_d.gr_db
            md["compressor_gr_m"] = self.compressor_m.gr_db
            md["glue_compressor_gr_a"] = self.glue_compressor.gr_db
            md["glue_compressor_gr_b"] = self.glue_compressor_b.gr_db
            md["glue_compressor_gr_c"] = self.glue_compressor_c.gr_db
            md["glue_compressor_gr_d"] = self.glue_compressor_d.gr_db
            md["glue_compressor_gr_m"] = self.glue_compressor_m.gr_db
            md["fet_limiter_gr_a"] = self.fet_limiter.gr_db
            md["fet_limiter_gr_b"] = self.fet_limiter_b.gr_db
            md["fet_limiter_gr_c"] = self.fet_limiter_c.gr_db
            md["fet_limiter_gr_d"] = self.fet_limiter_d.gr_db
            md["fet_limiter_gr_m"] = self.fet_limiter_m.gr_db
            # Copy scalar/float values into the double-buffered snapshot dict
            # instead of dict(md) + list() copies — zero heap allocations.
            snap_idx = self._meter_snap_idx
            snap = self._meter_snap[snap_idx]
            snap["master"][0] = md["master"][0]
            snap["master"][1] = md["master"][1]
            s_groups = snap["groups"]
            for gi in range(NUM_GROUPS):
                s_groups[gi] = md_groups[gi]
            snap["fx1"][0] = md["fx1"][0]; snap["fx1"][1] = md["fx1"][1]
            snap["fx2"][0] = md["fx2"][0]; snap["fx2"][1] = md["fx2"][1]
            snap["fx3"][0] = md["fx3"][0]; snap["fx3"][1] = md["fx3"][1]
            snap["fx4"][0] = md["fx4"][0]; snap["fx4"][1] = md["fx4"][1]
            snap["waveform_l"] = self._wf_snap_master_l[:_wn]
            snap["waveform_r"] = self._wf_snap_master_r[:_wn]
            snap["waveform_fx1_l"] = self._wf_snap_fx1_l[:_wn]
            snap["waveform_fx1_r"] = self._wf_snap_fx1_r[:_wn]
            snap["waveform_fx2_l"] = self._wf_snap_fx2_l[:_wn]
            snap["waveform_fx2_r"] = self._wf_snap_fx2_r[:_wn]
            snap["waveform_fx3_l"] = self._wf_snap_fx3_l[:_wn]
            snap["waveform_fx3_r"] = self._wf_snap_fx3_r[:_wn]
            snap["waveform_fx4_l"] = self._wf_snap_fx4_l[:_wn]
            snap["waveform_fx4_r"] = self._wf_snap_fx4_r[:_wn]
            snap["compressor_gr_a"] = md["compressor_gr_a"]
            snap["compressor_gr_b"] = md["compressor_gr_b"]
            snap["compressor_gr_c"] = md["compressor_gr_c"]
            snap["compressor_gr_d"] = md["compressor_gr_d"]
            snap["compressor_gr_m"] = md["compressor_gr_m"]
            snap["glue_compressor_gr_a"] = md["glue_compressor_gr_a"]
            snap["glue_compressor_gr_b"] = md["glue_compressor_gr_b"]
            snap["glue_compressor_gr_c"] = md["glue_compressor_gr_c"]
            snap["glue_compressor_gr_d"] = md["glue_compressor_gr_d"]
            snap["glue_compressor_gr_m"] = md["glue_compressor_gr_m"]
            snap["fet_limiter_gr_a"] = md["fet_limiter_gr_a"]
            snap["fet_limiter_gr_b"] = md["fet_limiter_gr_b"]
            snap["fet_limiter_gr_c"] = md["fet_limiter_gr_c"]
            snap["fet_limiter_gr_d"] = md["fet_limiter_gr_d"]
            snap["fet_limiter_gr_m"] = md["fet_limiter_gr_m"]
            self._meter_queue.append(snap)
            self._meter_snap_idx = 1 - snap_idx

        # ── Profiling accumulation ──
        if _prof:
            _t5 = time.perf_counter()
            total = _t5 - _t0
            self._prof_total += total
            self._prof_channels += (_t1 - _t0)
            self._prof_mixer += (_t2 - _t1)
            self._prof_fx += (_t3 - _t2)
            self._prof_master += (_t4 - _t3)
            self._prof_meter += (_t5 - _t4)
            if total > self._prof_max:
                self._prof_max = total
            if total > self._prof_budget:
                self._prof_overruns += 1
            self._prof_count += 1
            if self._prof_count >= self._prof_interval:
                n = self._prof_count
                budget_ms = self._prof_budget * 1000
                pt = self._prof_total if self._prof_total > 0 else 1e-9
                active = len(channel_outputs)
                ring_avg = self._prof_ring_sum / max(n, 1)
                log.debug(
                    "[DSP PROFILE] %d blocks budget=%.2fms | "
                    "avg=%.2fms max=%.2fms overruns=%d (%.1f%%) | "
                    "ch=%.2fms mix=%.2fms fx=%.2fms mast=%.2fms met=%.2fms | "
                    "active_ch=%d numba=%s ring: min=%d avg=%.1f",
                    n, budget_ms,
                    pt/n*1000, self._prof_max*1000,
                    self._prof_overruns, self._prof_overruns/n*100,
                    self._prof_channels/n*1000, self._prof_mixer/n*1000,
                    self._prof_fx/n*1000, self._prof_master/n*1000,
                    self._prof_meter/n*1000,
                    active, _HAS_NUMBA, self._prof_ring_min, ring_avg,
                )
                # Reset
                self._prof_count = 0
                self._prof_total = 0.0
                self._prof_channels = 0.0
                self._prof_mixer = 0.0
                self._prof_fx = 0.0
                self._prof_master = 0.0
                self._prof_meter = 0.0
                self._prof_max = 0.0
                self._prof_overruns = 0
                self._prof_ring_min = self._ring_blocks
                self._prof_ring_sum = 0

    def _dsp_worker(self):
        """Background thread that fills the ring buffer with processed audio.

        Runs continuously while self._running is True.  Fills one block at a
        time into the ring buffer, then waits if the buffer is full.  The
        sounddevice callback wakes us via _ring_event when it consumes a slot.
        """
        # Raise thread priority so OS prefers us over GUI repaints
        try:
            import ctypes
            handle = ctypes.windll.kernel32.GetCurrentThread()
            # THREAD_PRIORITY_TIME_CRITICAL = 15 — matches the priority level
            # that ASIO drivers and PortAudio use for their own threads.
            # THREAD_PRIORITY_HIGHEST = 2 is not enough to stay ahead of
            # background OS threads in a frozen exe.
            ctypes.windll.kernel32.SetThreadPriority(handle, 15)
        except Exception:
            pass  # Non-Windows or permission denied — no harm

        ring = self._ring_buf
        n = self._ring_blocks
        frames = self.buffer_size

        while self._running:
            w = self._ring_write
            r = self._ring_read

            # How many slots are filled?
            if w == r:
                used = n if self._ring_buf_full else 0
            else:
                used = (w - r) % n

            if used >= n - 1:
                # Ring buffer is full — wait for callback to consume a block
                self._ring_event.wait(timeout=0.05)
                self._ring_event.clear()
                continue

            # Fill the next slot
            slot = ring[w % n]
            slot[:] = 0
            try:
                self._process_block(frames, slot)
                self._dsp_error_count = 0  # reset on success
            except Exception as exc:
                slot[:] = 0
                self._dsp_error_count += 1
                self._dsp_last_error = str(exc)
                now = time.monotonic()
                if now - self._dsp_error_logged_at >= self._ERROR_LOG_INTERVAL:
                    self._dsp_error_logged_at = now
                    log.error("DSP block failed (output silenced): %s", exc, exc_info=True)

            # Advance write pointer
            new_w = (w + 1) % n
            if new_w == self._ring_read:
                self._ring_buf_full = True
            self._ring_write = new_w

    def _get_compressor_sidechain(self, comp: CompressorProcessor,
                                   bus_audio: np.ndarray, frames: int):
        """Return the appropriate sidechain signal for a compressor instance."""
        src = comp.state.sidechain_source
        if src == 0:
            return None  # self-keyed
        # Group 1-4 → mixer._group_audio[0-3]
        grp_idx = src - 1
        if 0 <= grp_idx < len(self.mixer._group_audio):
            return self.mixer._group_audio[grp_idx][:frames]
        return None

    def _process_fx_chain(self, bus_audio: np.ndarray, bus_key: str,
                          frames: int) -> np.ndarray:
        """Process an FX bus through its ordered effect chain.

        Args:
            bus_audio: stereo audio buffer for this bus
            bus_key: bus suffix letter ('a', 'b', 'c', 'd', 'm')
            frames: number of audio frames

        Returns:
            Processed stereo audio buffer
        """
        order = self._fx_order_map[bus_key]
        if not order:
            return bus_audio
        # Snapshot the order list so GUI-thread mutations during iteration
        # cannot cause skipped/duplicated effects in this audio block.
        order = tuple(order)
        procs = self._fx_processors[bus_key]
        needs_enabled = self._FX_NEEDS_ENABLED_CHECK
        mod_types = self._FX_MOD_TYPES
        # Track which mod sub-effects we've already processed via a single
        # FXBus.process_mod_only call, to avoid running it multiple times.
        mod_processed = False
        for fx_type in order:
            proc = procs.get(fx_type)
            if proc is None:
                continue
            try:
                # Mod effects (phaser/chorus/flanger) share a single FXBus instance.
                # Process all active mod effects once at the position of the first
                # mod effect encountered in the chain.
                if fx_type in mod_types:
                    if not mod_processed:
                        mod_processed = True
                        bus_audio = proc.process_mod_only(bus_audio)
                    continue
                # Effects that gate on .state.enabled
                if fx_type in needs_enabled:
                    if not proc.state.enabled:
                        continue
                # Compressor needs sidechain routing
                if fx_type == 'compressor':
                    sc = self._get_compressor_sidechain(proc, bus_audio, frames)
                    bus_audio = proc.process(bus_audio, sc)
                    continue
                bus_audio = proc.process(bus_audio)
            except Exception:
                # A single bad effect must not kill the entire bus chain.
                # Skip this effect and continue with the current audio.
                self._report_fx_error(fx_type, bus_key)
        return bus_audio

    # --- Thread-safe control methods ---

    def set_effect_in_rack(self, effect_type: str, bus: int, in_rack: bool):
        """Update whether an effect module is present in a specific rack.

        Args:
            effect_type: e.g. "delay", "tape_delay", "filter_a", etc.
            bus: 1=Rack A/FX1, 2=Rack B/FX2, 3=Master, 4=Rack C/FX3, 5=Rack D/FX4.
            in_rack: True if the module is present.
        """
        # Map bus → suffix for simple flag-based effects
        _SUFFIX = {1: '_a', 2: '_b', 3: '_m', 4: '_c', 5: '_d'}
        suffix = _SUFFIX.get(bus)
        if suffix is None:
            return

        # --- Mod effects (phaser/chorus/flanger) need special FXBus handling ---
        if effect_type in ("phaser", "chorus", "flanger"):
            _BUS_MAP = {
                1: (self.fx_bus_1, 'mod_in_rack_a'),
                2: (self.fx_bus_2, 'mod_in_rack_b'),
                3: (self.fx_bus_m, 'mod_in_rack_m'),
                4: (self.fx_bus_c, 'mod_in_rack_c'),
                5: (self.fx_bus_d, 'mod_in_rack_d'),
            }
            fx_bus, mod_flag = _BUS_MAP[bus]
            setattr(fx_bus, f"{effect_type}_in_rack", in_rack)
            setattr(self, mod_flag,
                    fx_bus.phaser_in_rack or fx_bus.chorus_in_rack or fx_bus.flanger_in_rack)
            return

        # --- All other effect types — direct flag set ---
        flag_name = f"{effect_type}_in_rack{suffix}"
        if hasattr(self, flag_name):
            setattr(self, flag_name, in_rack)

    def set_rack_order(self, bus: int, order: list[str]):
        """Set the processing order for an FX bus.

        Args:
            bus: 1=Rack A/FX1, 2=Rack B/FX2, 3=Master, 4=Rack C/FX3, 5=Rack D/FX4.
            order: Ordered list of effect_type strings (e.g. ["compressor", "delay"]).
        """
        _KEY = {1: 'a', 2: 'b', 3: 'm', 4: 'c', 5: 'd'}
        key = _KEY.get(bus)
        if key is None:
            return
        # Atomic reference swap — the DSP thread reads via tuple() which
        # iterates whatever list the reference currently points to.
        # Swapping the dict value is a single STORE_SUBSCR bytecode, which
        # is atomic under CPython's GIL.
        self._fx_order_map[key] = list(order)

    # --- External plugin slot management (Foreign Exchange) ---

    _BUS_KEY_NORM = {
        'fx_bus_1': 'a', 'fx_bus_2': 'b', 'fx_bus_m': 'm',
        'fx_bus_c': 'c', 'fx_bus_d': 'd',
        'a': 'a', 'b': 'b', 'm': 'm', 'c': 'c', 'd': 'd',
    }

    def _get_stowaway(self, bus_key: str, slot_idx: int) -> Optional[StowawayProcessor]:
        key = self._BUS_KEY_NORM.get(bus_key, bus_key)
        pool = self._stowaway_pool.get(key)
        if pool is None or not (0 <= slot_idx < len(pool)):
            return None
        return pool[slot_idx]

    def load_plugin_slot(self, bus_key: str, slot_idx: int,
                         path: str, fmt: str = "vst3") -> bool:
        """Load a plugin into a stowaway slot.  Call from background thread."""
        proc = self._get_stowaway(bus_key, slot_idx)
        if proc is None:
            return False
        return proc.load(path, fmt)

    def unload_plugin_slot(self, bus_key: str, slot_idx: int) -> None:
        """Unload a plugin from a stowaway slot."""
        proc = self._get_stowaway(bus_key, slot_idx)
        if proc is not None:
            proc.unload()

    def set_plugin_slot_param(self, bus_key: str, slot_idx: int,
                              name: str, value: float) -> None:
        proc = self._get_stowaway(bus_key, slot_idx)
        if proc is not None:
            proc.set_param(name, value)

    def set_plugin_slot_wet_dry(self, bus_key: str, slot_idx: int,
                                value: float) -> None:
        proc = self._get_stowaway(bus_key, slot_idx)
        if proc is not None:
            proc._wet_dry = max(0.0, min(1.0, value))

    def set_plugin_slot_enabled(self, bus_key: str, slot_idx: int,
                                enabled: bool) -> None:
        proc = self._get_stowaway(bus_key, slot_idx)
        if proc is not None:
            proc._enabled = enabled

    def get_plugin_slot_params(self, bus_key: str, slot_idx: int) -> dict:
        """Read all params from a plugin slot.  Main thread only."""
        proc = self._get_stowaway(bus_key, slot_idx)
        if proc is None:
            return {}
        return proc.get_params()

    def get_plugin_slot_param_names(self, bus_key: str, slot_idx: int) -> list:
        proc = self._get_stowaway(bus_key, slot_idx)
        if proc is None:
            return []
        return proc.get_param_names()

    def show_plugin_slot_editor(self, bus_key: str, slot_idx: int,
                               close_event=None) -> bool:
        """Open the native editor for a plugin slot.  Main thread only."""
        proc = self._get_stowaway(bus_key, slot_idx)
        if proc is None:
            return False
        return proc.show_editor(close_event)

    def get_plugin_slot_state_b64(self, bus_key: str, slot_idx: int) -> str:
        proc = self._get_stowaway(bus_key, slot_idx)
        if proc is None:
            return ""
        return proc.get_state_b64()

    def set_plugin_slot_state_b64(self, bus_key: str, slot_idx: int,
                                  state_b64: str) -> bool:
        proc = self._get_stowaway(bus_key, slot_idx)
        if proc is None:
            return False
        return proc.set_state_b64(state_b64)

    def get_stowaway_states(self) -> dict[str, list[dict]]:
        """Return serialisable state for all stowaway slots across all buses."""
        result: dict[str, list[dict]] = {}
        for bus_key, pool in self._stowaway_pool.items():
            bus_states = []
            for i, proc in enumerate(pool):
                bus_states.append({
                    "slot_index": i,
                    "loaded": proc._loaded,
                    "enabled": proc._enabled,
                    "wet_dry": proc._wet_dry,
                    "state_b64": proc.get_state_b64(),
                })
            result[bus_key] = bus_states
        return result

    def set_master_hp(self, freq: float):
        """Set master highpass trim frequency (20-500 Hz).

        Stores the new frequency for the DSP thread to apply, avoiding
        a torn coefficient update across threads.
        """
        self._pending_master_hp = freq

    def set_master_lp(self, freq: float):
        """Set master lowpass trim frequency (200-20000 Hz).

        Stores the new frequency for the DSP thread to apply, avoiding
        a torn coefficient update across threads.
        """
        self._pending_master_lp = freq

    def trigger_channel(self, row: int, column: int, columns: int = 16):
        """Queue a channel trigger — processed on the audio thread."""
        if 0 <= row < MAX_ROWS:
            self._event_queue.append(('trigger', row, column, columns))

    def stop_channel(self, row: int):
        """Queue a channel stop (with release) — processed on the audio thread."""
        if 0 <= row < MAX_ROWS:
            self._event_queue.append(('stop', row))

    def force_stop_channel(self, row: int):
        """Queue an immediate channel stop — processed on the audio thread."""
        if 0 <= row < MAX_ROWS:
            self._event_queue.append(('force_stop', row))

    def stop_group(self, group: int):
        """Queue stops for all channels in a group."""
        for i, ch in enumerate(self.channels):
            if ch.state.group == group:
                self._event_queue.append(('stop', i))

    def set_channel_position(self, row: int, position: float):
        """Queue a playback position change — processed on the audio thread."""
        if 0 <= row < MAX_ROWS:
            self._event_queue.append(('set_pos', row, position))

    def load_sample(self, row: int, sample: SampleBuffer):
        """Queue a sample load — processed on the audio thread."""
        if 0 <= row < MAX_ROWS:
            self._event_queue.append(('load_sample', row, sample))

    # ── LIVE looper API ──────────────────────────────────────────────────

    def set_live_recorded_callback(self, callback: Callable[[int, SampleBuffer], None]):
        """Register a callback fired (from audio thread) when a live recording finishes.

        The callback receives (row, buffer). It MUST be thread-safe.
        Typically the app uses QMetaObject.invokeMethod to marshal back to GUI thread.
        """
        self._live_recorded_callback = callback

    def start_live_recording(self, row: int, bars: int, tempo: float,
                             input_trim: float = 1.0,
                             thru: bool = False,
                             overdub_buffer: Optional[np.ndarray] = None) -> bool:
        """Arm and start a LIVE-mode recording for a row.

        Returns True if recording started; False if another row is already
        recording (busy). Caller should check return value and update UI accordingly.

        Recording starts immediately on the next audio block. For quantized
        starts, the caller (app) is responsible for delaying the call until
        the desired beat/bar boundary.
        """
        if not (0 <= row < MAX_ROWS):
            return False
        # Single-row constraint
        if self._live_recorder is not None and self._live_recorder.is_recording:
            return False
        rec = LiveLoopRecorder(row=row, sample_rate=self.sample_rate)
        rec.arm(bars=bars, tempo=tempo, overdub_buffer=overdub_buffer, thru=thru)
        rec.start()
        self._live_recorder = rec
        self._live_recorder_row = row
        self._live_recorder_trim = float(input_trim)
        return True

    def cancel_live_recording(self):
        """Abort any in-progress live recording (no callback fired)."""
        if self._live_recorder is not None:
            self._live_recorder.cancel()
        self._live_recorder = None
        self._live_recorder_row = -1
        self._live_recorder_trim = 1.0

    def set_live_input_trim(self, trim: float):
        """Update input trim for the active live recorder (if any)."""
        self._live_recorder_trim = float(trim)

    @property
    def live_recording_row(self) -> int:
        """Row currently recording, or -1 if none."""
        rec = self._live_recorder
        if rec is None or not rec.is_recording:
            return -1
        return self._live_recorder_row

    @property
    def live_recording_progress(self) -> float:
        """0.0-1.0 progress of active live recording (0.0 if none)."""
        rec = self._live_recorder
        if rec is None:
            return 0.0
        return rec.progress

    # ── SAMP / resample API ──────────────────────────────────────────────

    def set_samp_recorded_callback(self, callback: Callable[[int, SampleBuffer], None]):
        """Register a callback fired (from audio thread) when a samp recording finishes.
        Same threading rules as set_live_recorded_callback.
        """
        self._samp_recorded_callback = callback

    def start_samp_recording(self, row: int, bars: int, tempo: float,
                             source_bus: int,
                             exclude_self: bool = True,
                             fade_ms: float = 10.0,
                             normalize: bool = True) -> bool:
        """Arm and start a bus-capture recording for a row.

        source_bus follows config.LIVE_SOURCE_NAMES index:
          1       = MAIN (master out)
          2..5    = FX-A..FX-D buses
          6..5+NG = GRP-1..GRP-N  (NG = NUM_GROUPS, currently 8 → indices 6..13)

        Returns True if started; False if another samp/live recording is in progress
        or if source_bus is out of range.
        """
        # Valid source buses: 1 (MAIN) + 4 FX + NUM_GROUPS group buses
        _max_source = 5 + NUM_GROUPS  # e.g. 13 for 8 groups
        if not (0 <= row < MAX_ROWS):
            return False
        if source_bus < 1 or source_bus > _max_source:
            return False
        if self._samp_recorder is not None and self._samp_recorder.is_recording:
            return False
        # Reuse single-row constraint with live recorder too — only one capture
        # can be in flight per row at a time.  Different rows can NOT overlap.
        if self._live_recorder is not None and self._live_recorder.is_recording:
            return False
        rec = SampRecorder(row=row, sample_rate=self.sample_rate)
        rec.arm(bars=bars, tempo=tempo, fade_ms=fade_ms, normalize=normalize)
        rec.start()
        self._samp_recorder = rec
        self._samp_recorder_row = row
        self._samp_source_bus = int(source_bus)
        self._samp_exclude_self = bool(exclude_self)
        return True

    def cancel_samp_recording(self):
        """Abort any in-progress samp recording (no callback fired)."""
        if self._samp_recorder is not None:
            self._samp_recorder.cancel()
        self._samp_recorder = None
        self._samp_recorder_row = -1
        self._samp_source_bus = 0

    # ── Audio-threshold arm trigger ──────────────────────────────────

    def set_audio_arm_callback(self, fn) -> None:
        """Set the callback fired when an audio-threshold arm watch triggers.

        Signature: fn(row: int) -> None.  Called from the audio thread — must
        be thread-safe (use a Qt QueuedConnection marshaller in app.py).
        """
        self._audio_arm_callback = fn

    def arm_audio_trigger(self, row: int, source_bus: int,
                          threshold_linear: float) -> None:
        """Add an audio-threshold watch for *row*.

        When the peak level on *source_bus* exceeds *threshold_linear* during
        any future _process_block call, the audio_arm_callback is fired with
        *row* and the watch is removed (single-fire).

        The mutation is deferred to the DSP thread via the event queue so
        _audio_arm_watches is only ever written on the DSP thread, preventing
        a race between arm_audio_trigger() and _process_block().
        """
        self._event_queue.append(('arm_watch', row, int(source_bus),
                                  float(threshold_linear)))

    def disarm_audio_trigger(self, row: int) -> None:
        """Remove any pending audio-threshold watch for *row*."""
        self._event_queue.append(('disarm_watch', row))

    @property
    def samp_recording_row(self) -> int:
        rec = self._samp_recorder
        if rec is None or not rec.is_recording:
            return -1
        return self._samp_recorder_row

    @property
    def samp_recording_progress(self) -> float:
        rec = self._samp_recorder
        if rec is None:
            return 0.0
        return rec.progress

    def reset_all_fx(self):
        """Queue a full FX buffer reset — processed on the audio thread."""
        self._event_queue.append(('reset_fx',))

    # ------------------------------------------------------------------ Song Recorder

    def arm_song_recorder(self) -> None:
        """Arm the song recorder; recording begins on the next start_song_recorder() call."""
        self.song_recorder.arm()

    def start_song_recorder(self) -> None:
        """Begin capturing the master output into the song recorder."""
        self.song_recorder.start()

    def start_song_recording_now(self) -> None:
        """Immediate record — clears the buffer and begins capturing without arming."""
        self.song_recorder.start_recording_now()

    def clear_song_recording(self) -> None:
        """Discard recording + result; stop playback. Safe to call any time."""
        self.song_recorder.clear()

    def stop_song_recorder(self):
        """Stop the song recorder and return the finalised SampleBuffer."""
        return self.song_recorder.stop()

    def cancel_song_recorder(self) -> None:
        """Abort without finalising."""
        self.song_recorder.cancel()



    def flush_ring_buffer(self):
        """Zero all ring buffer slots and reset pointers.

        Must be called from the main thread AFTER queuing force_stop /
        reset_fx events.  The DSP worker will refill with silence (or
        whatever the next _process_block produces) before the callback
        plays them out.
        """
        self._ring_buf[:] = 0
        self._ring_write = 0
        self._ring_read = 0
        self._ring_buf_full = False
        self._ring_event.set()  # wake worker to start refilling

    def _do_reset_all_fx(self):
        """Clear all internal FX buffers (delay lines, reverb tails, filter
        histories, compressor envelopes, etc.).

        Called on the audio thread via the event queue so no buffers are
        accessed mid-process.
        """
        _suffixes = ('', '_b', '_c', '_d', '_m')
        _fx_names = (
            'delay', 'tape_delay', 'filter_a', 'filter_b',
            'reel_to_reel', 'cassette_tape', 'vhs', 'vinyl',
            'compressor', 'glue_compressor', 'hall_reverb', 'plate_reverb',
            'bit_crusher', 'tube_overdrive', 'ether_drift', 'noise_gate',
            'tremolo', 'auto_wah', 'fet_limiter', 'rotary', 'exciter',
            'transient_shaper', 'harmonizer', 'stereo_widener',
            'freq_shifter', 'ring_mod', 'simple_eq', 'ping_pong_delay',
            'shimmer_reverb', 'lofi_radio', 'auto_pan', 'fuzz',
            'granular_freeze', 'gate_reverb', 'stutter',
        )
        for sfx in _suffixes:
            for name in _fx_names:
                fx = getattr(self, name + sfx, None)
                if fx is not None and hasattr(fx, 'reset'):
                    fx.reset()
        # FXBus instances have irregular names
        for bus in (self.fx_bus_1, self.fx_bus_2, self.fx_bus_c,
                    self.fx_bus_d, self.fx_bus_m):
            bus.reset()
        # Master HP/LP biquads
        self._master_hp.reset()
        self._master_lp.reset()
        # Per-channel filters
        for cf in self.channel_filters:
            cf.reset()
        # Snap mixer volume smoothing so the new preset's volumes take
        # effect immediately rather than gliding from the old values.
        self.mixer.reset_smooth_volumes()

    def set_channel_speed(self, row: int, tempo: float, sample=None):
        """Set channel playback speed based on tempo and sample length.

        If *sample* is provided, use it for the duration calculation instead
        of reading from the channel (which may not be loaded yet when the
        load is queued via the event queue).
        """
        if 0 <= row < MAX_ROWS and tempo > 0:
            ch = self.channels[row]
            s = sample if sample is not None else ch.sample
            if s is not None and s.duration_sec > 0:
                # Calculate speed to fit sample into the step grid
                beats = ch.state.steps
                bar_duration = beats * (60.0 / tempo)
                sample_duration = s.duration_sec * (ch.state.sel_end - ch.state.sel_start)
                if sample_duration > 0 and bar_duration > 0:
                    ch.state.speed = sample_duration / bar_duration

    def set_tempo(self, tempo: float):
        """Update tempo for all tempo-synced components."""
        old_tempo = self.config.tempo
        self.config.tempo = tempo
        self.delay.set_tempo(tempo)
        self.delay_b.set_tempo(tempo)
        self.delay_c.set_tempo(tempo)
        self.delay_d.set_tempo(tempo)
        self.delay_m.set_tempo(tempo)
        self.tape_delay.set_tempo(tempo)
        self.tape_delay_b.set_tempo(tempo)
        self.tape_delay_c.set_tempo(tempo)
        self.tape_delay_d.set_tempo(tempo)
        self.tape_delay_m.set_tempo(tempo)
        self.stutter.set_tempo(tempo)
        self.stutter_b.set_tempo(tempo)
        self.stutter_c.set_tempo(tempo)
        self.stutter_d.set_tempo(tempo)
        self.stutter_m.set_tempo(tempo)
        # Scale existing channel speeds proportionally instead of
        # recalculating from scratch, so manual speed overrides are preserved.
        # SEQ channels use a user-defined speed (default 1.0) that is not
        # BPM-relative, so skip them here.
        if old_tempo > 0:
            ratio = tempo / old_tempo
            for i in range(self.config.row_count):
                ch = self.channels[i]
                if ch.sample is not None:
                    if ch.state.mode != PlaybackMode.SEQ:
                        ch.state.speed *= ratio

    def set_group_volume(self, group: int, volume: float):
        if 0 <= group < NUM_GROUPS:
            self.mixer.groups[group].volume = volume

    def set_group_send(self, group: int, send: float):
        if 0 <= group < NUM_GROUPS:
            self.mixer.groups[group].send = send

    def set_group_mute(self, group: int, mute: bool):
        if 0 <= group < NUM_GROUPS:
            self.mixer.groups[group].mute = mute

    def set_master_volume(self, volume: float):
        self.mixer.master_volume = volume

    def set_aux_volume(self, volume: float):
        self.mixer.aux_volume = volume

    @staticmethod
    def get_host_apis() -> list[dict]:
        """Return available host APIs (driver types).

        Each entry: {"index": int, "name": str, "default_output": int, "default_input": int}
        """
        apis = sd.query_hostapis()
        return [
            {
                "index": i,
                "name": a["name"],
                "default_output": a.get("default_output_device", -1),
                "default_input": a.get("default_input_device", -1),
            }
            for i, a in enumerate(apis)
        ]

    @staticmethod
    def get_devices() -> dict:
        """Get available audio devices."""
        devices = sd.query_devices()
        result = {"input": [], "output": []}
        apis = sd.query_hostapis()
        for i, d in enumerate(devices):
            api_name = apis[d["hostapi"]]["name"] if d["hostapi"] < len(apis) else ""
            info = {"index": i, "name": d["name"],
                    "channels": d["max_output_channels"] or d["max_input_channels"],
                    "sample_rate": d["default_samplerate"],
                    "host_api": api_name, "host_api_index": d["hostapi"]}
            if d["max_output_channels"] > 0:
                result["output"].append(info)
            if d["max_input_channels"] > 0:
                result["input"].append(info)
        return result
