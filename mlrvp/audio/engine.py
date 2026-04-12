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
import os
import time
from collections import deque
from typing import Optional, Callable

from mlrvp.config import (
    AppConfig, SAMPLE_RATE, BUFFER_SIZE, MAX_ROWS, NUM_GROUPS,
    PlaybackMode, ChannelState
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
)
from mlrvp.audio.recorder import Recorder
from mlrvp.audio.sample import SampleBuffer

# Check Numba status from effects module
try:
    from mlrvp.audio.effects import _HAS_NUMBA
except ImportError:
    _HAS_NUMBA = False


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
        self.transient_shaper = TransientShaperProcessor(self.sample_rate)
        self.transient_shaper_b = TransientShaperProcessor(self.sample_rate)
        self.transient_shaper_m = TransientShaperProcessor(self.sample_rate)

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
        self.transient_shaper_c = TransientShaperProcessor(self.sample_rate)
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
        self.transient_shaper_d = TransientShaperProcessor(self.sample_rate)
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

        # Master filter (gentle HP/LP trim)
        self._master_hp = BiquadFilter(self.sample_rate)
        self._master_lp = BiquadFilter(self.sample_rate)
        self._master_hp_freq: float = 20.0   # default: no cut
        self._master_lp_freq: float = 20000.0  # default: no cut
        self._master_hp.set_highpass(20.0, 0.707)
        self._master_lp.set_lowpass(20000.0, 0.707)

        # Stream
        self._stream: Optional[sd.OutputStream] = None
        self._input_stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()
        self._running = False

        # Thread-safe event queue — all channel trigger/stop mutations are
        # deferred to the audio thread to avoid races with process().
        # deque.append / popleft are atomic in CPython.
        self._event_queue: deque = deque()

        # Callback for UI updates (called from audio thread, should be fast)
        self._meter_callback: Optional[Callable] = None

        # Pre-allocated meter data to avoid allocations in audio callback
        self._meter_data = {
            "master": [0.0, 0.0],
            "groups": [0.0] * NUM_GROUPS,
            "fx1": [0.0, 0.0],
            "fx2": [0.0, 0.0],
            "waveform_l": [],
            "waveform_r": [],
        }

        # Input buffer for recording
        self._input_buffer = np.zeros((self.buffer_size, 2), dtype=np.float32)
        self._input_copy = np.empty((self.buffer_size, 2), dtype=np.float32)
        self._input_ready = False

        # Pre-allocated zero buffer for delay routing bypass
        self._zero_buf = np.zeros((self.buffer_size, 2), dtype=np.float32)

        # Peak meters
        self.output_peak = np.zeros(2, dtype=np.float32)
        self._fx1_peak = np.zeros(2, dtype=np.float32)
        self._fx2_peak = np.zeros(2, dtype=np.float32)
        self._fx3_peak = np.zeros(2, dtype=np.float32)
        self._fx4_peak = np.zeros(2, dtype=np.float32)

        # Pre-allocated temp buffer for peak metering (avoids allocation in callback)
        self._peak_tmp = np.zeros(self.buffer_size, dtype=np.float32)

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

        # Pre-allocated channel output list to avoid per-callback allocation
        self._channel_outputs: list[tuple[int, np.ndarray]] = []

        # ── Ring buffer for decoupled audio output ──
        # The DSP worker fills this ahead of time; the sounddevice callback
        # just copies the next block out.  8 blocks ≈ 93 ms of lookahead.
        self._ring_blocks = 8
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

    def set_meter_callback(self, callback: Callable):
        self._meter_callback = callback

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
            print("[AudioEngine] Profiling ENABLED")
        else:
            print("[AudioEngine] Profiling DISABLED")

    def start(self):
        """Start the audio engine."""
        if self._running:
            return

        print(f"\n[AudioEngine] Starting — Numba JIT: {_HAS_NUMBA}, "
              f"SR={self.sample_rate}, buffer={self.buffer_size}, "
              f"budget={self.buffer_size/self.sample_rate*1000:.2f}ms, "
              f"ring={self._ring_blocks} blocks, "
              f"profiling={'ON' if self._prof_enabled else 'OFF (set MLRVP_DEBUG=1 to enable)'}")

        # Disable Python's cyclic GC to prevent unpredictable collection
        # pauses on the audio thread.  A periodic timer runs gen-0
        # collections from the main thread between callbacks.
        gc.disable()
        self._gc_timer = threading.Timer(1.0, self._periodic_gc)
        self._gc_timer.daemon = True
        self._gc_timer.start()

        try:
            output_device = self.config.output_device if self.config.output_device >= 0 else None
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                blocksize=self.buffer_size,
                channels=2,
                dtype='float32',
                callback=self._audio_callback,
                device=output_device,
                latency='low',
            )
            try:
                self._stream.start()
            except Exception:
                self._stream.close()
                self._stream = None
                raise
            self._running = True
        except Exception as e:
            gc.enable()
            print(f"Audio engine start error: {e}")
            raise

        # Pre-fill the ring buffer so the callback has audio from the start
        self._ring_read = 0
        self._ring_write = 0
        self._ring_buf_full = False
        for i in range(self._ring_blocks - 1):
            slot = self._ring_buf[i]
            slot[:] = 0
            self._process_block(self.buffer_size, slot)
            self._ring_write = (self._ring_write + 1) % self._ring_blocks
        self._ring_event.clear()

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
        except Exception:
            # Input not critical - recording just won't work
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
        # Re-enable automatic GC and cancel the periodic timer
        if hasattr(self, '_gc_timer') and self._gc_timer is not None:
            self._gc_timer.cancel()
            self._gc_timer = None
        gc.enable()
        gc.collect()

    def _periodic_gc(self):
        """Run a gen-0 GC collection from a background timer thread.

        This keeps memory tidy while the cyclic GC is disabled during
        audio processing.  Only gen-0 is collected to keep pauses short.
        """
        if self._running:
            gc.collect(0)
            self._gc_timer = threading.Timer(1.0, self._periodic_gc)
            self._gc_timer.daemon = True
            self._gc_timer.start()

    def _input_callback(self, indata, frames, time_info, status):
        """Capture input audio for recording."""
        self._input_buffer[:frames] = indata
        self._input_ready = True

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
        """
        try:
            _prof = self._prof_enabled
            if _prof:
                _t0 = time.perf_counter()

            # ── Process pending trigger/stop events (thread-safe) ──
            eq = self._event_queue
            while eq:
                try:
                    event = eq.popleft()
                except IndexError:
                    break
                cmd = event[0]
                if cmd == 'trigger':
                    self.channels[event[1]].trigger(event[2], event[3])
                elif cmd == 'stop':
                    self.channels[event[1]].stop()
                elif cmd == 'force_stop':
                    self.channels[event[1]].force_stop()
                elif cmd == 'set_pos':
                    self.channels[event[1]]._position = event[2]

            # Capture input audio only when needed (recording or AUX monitoring)
            _need_input = self.recorder.recording or not self.mixer.aux_mute
            if _need_input and self._input_ready:
                input_audio = self._input_copy[:frames]
                input_audio[:] = self._input_buffer[:frames]
                self._input_ready = False
            else:
                if self._input_ready:
                    self._input_ready = False   # discard unused frame
                input_audio = None

            # Process recording input
            if input_audio is not None and self.recorder.recording:
                thru = self.recorder.process(input_audio)
            else:
                thru = None

            # Process all active channels
            channel_outputs = self._channel_outputs
            channel_outputs.clear()
            active_count = min(self.config.row_count, MAX_ROWS)

            for i in range(active_count):
                ch = self.channels[i]
                if ch.playing or ch.state.playing:
                    audio = ch.process(frames)
                    # Apply per-channel filter
                    if abs(ch.state.filter_val) > 0.01:
                        self.channel_filters[i].set_filter(ch.state.filter_val)
                        audio = self.channel_filters[i].process(audio)
                    channel_outputs.append((ch.state.group, audio))

            if _prof:
                _t1 = time.perf_counter()

            # Mix through groups (now returns FX bus outputs too)
            main_bus, aux_send, fx1_bus, fx2_bus, fx3_bus, fx4_bus = self.mixer.process_groups(channel_outputs, frames)

            # Add recorder thru to main bus if active
            if thru is not None:
                main_bus += thru

            # --- AUX input channel (external audio source) ---
            if not self.mixer.aux_mute and input_audio is not None:
                aux_scaled = input_audio * self.mixer.aux_volume
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
            # Noise Gate (first in chain — gates signal before any processing)
            if self.noise_gate_in_rack_a:
                fx1_bus = self.noise_gate.process(fx1_bus)
            if self.noise_gate_in_rack_b:
                fx2_bus = self.noise_gate_b.process(fx2_bus)

            # Tremolo
            if self.tremolo_in_rack_a:
                fx1_bus = self.tremolo.process(fx1_bus)
            if self.tremolo_in_rack_b:
                fx2_bus = self.tremolo_b.process(fx2_bus)

            # Auto-Wah
            if self.auto_wah_in_rack_a:
                fx1_bus = self.auto_wah.process(fx1_bus)
            if self.auto_wah_in_rack_b:
                fx2_bus = self.auto_wah_b.process(fx2_bus)

            # FET Limiter (1176)
            if self.fet_limiter_in_rack_a:
                fx1_bus = self.fet_limiter.process(fx1_bus)
            if self.fet_limiter_in_rack_b:
                fx2_bus = self.fet_limiter_b.process(fx2_bus)

            # Filter A (shapeable LP/HP)
            if self.filter_a_in_rack_a and self.filter_a.state.enabled:
                fx1_bus = self.filter_a.process(fx1_bus)
            if self.filter_a_in_rack_b and self.filter_a_b.state.enabled:
                fx2_bus = self.filter_a_b.process(fx2_bus)

            # Filter B (Moog ladder)
            if self.filter_b_in_rack_a and self.filter_b.state.enabled:
                fx1_bus = self.filter_b.process(fx1_bus)
            if self.filter_b_in_rack_b and self.filter_b_b.state.enabled:
                fx2_bus = self.filter_b_b.process(fx2_bus)

            # Mod effects (phaser, chorus, flanger) — per-bus FXBus handles own enabled
            if self.mod_in_rack_a:
                fx1_bus = self.fx_bus_1.process_mod_only(fx1_bus)
            if self.mod_in_rack_b:
                fx2_bus = self.fx_bus_2.process_mod_only(fx2_bus)

            # Digital delay
            if self.delay_in_rack_a and self.delay.state.enabled:
                fx1_bus = self.delay.process(fx1_bus)
            if self.delay_in_rack_b and self.delay_b.state.enabled:
                fx2_bus = self.delay_b.process(fx2_bus)

            # Tape delay
            if self.tape_delay_in_rack_a and self.tape_delay.state.enabled:
                fx1_bus = self.tape_delay.process(fx1_bus)
            if self.tape_delay_in_rack_b and self.tape_delay_b.state.enabled:
                fx2_bus = self.tape_delay_b.process(fx2_bus)

            # Lo-fi media effects (each checks own state.enabled in process())
            if self.reel_to_reel_in_rack_a:
                fx1_bus = self.reel_to_reel.process(fx1_bus)
            if self.reel_to_reel_in_rack_b:
                fx2_bus = self.reel_to_reel_b.process(fx2_bus)

            if self.cassette_tape_in_rack_a:
                fx1_bus = self.cassette_tape.process(fx1_bus)
            if self.cassette_tape_in_rack_b:
                fx2_bus = self.cassette_tape_b.process(fx2_bus)

            if self.vhs_in_rack_a:
                fx1_bus = self.vhs.process(fx1_bus)
            if self.vhs_in_rack_b:
                fx2_bus = self.vhs_b.process(fx2_bus)

            if self.vinyl_in_rack_a:
                fx1_bus = self.vinyl.process(fx1_bus)
            if self.vinyl_in_rack_b:
                fx2_bus = self.vinyl_b.process(fx2_bus)

            # Hall reverb
            if self.hall_reverb_in_rack_a:
                fx1_bus = self.hall_reverb.process(fx1_bus)
            if self.hall_reverb_in_rack_b:
                fx2_bus = self.hall_reverb_b.process(fx2_bus)

            # Plate reverb
            if self.plate_reverb_in_rack_a:
                fx1_bus = self.plate_reverb.process(fx1_bus)
            if self.plate_reverb_in_rack_b:
                fx2_bus = self.plate_reverb_b.process(fx2_bus)

            # Compressor (with sidechain routing, checks own state.enabled)
            if self.compressor_in_rack_a:
                sc_a = self._get_compressor_sidechain(self.compressor, fx1_bus, frames)
                fx1_bus = self.compressor.process(fx1_bus, sc_a)
            if self.compressor_in_rack_b:
                sc_b = self._get_compressor_sidechain(self.compressor_b, fx2_bus, frames)
                fx2_bus = self.compressor_b.process(fx2_bus, sc_b)

            # Glue compressor (no sidechain routing; self-keyed bus glue)
            if self.glue_compressor_in_rack_a:
                fx1_bus = self.glue_compressor.process(fx1_bus)
            if self.glue_compressor_in_rack_b:
                fx2_bus = self.glue_compressor_b.process(fx2_bus)

            # Bit crusher (checks own state.enabled via process())
            if self.bit_crusher_in_rack_a:
                fx1_bus = self.bit_crusher.process(fx1_bus)
            if self.bit_crusher_in_rack_b:
                fx2_bus = self.bit_crusher_b.process(fx2_bus)

            # Tube overdrive (checks own state.enabled via process())
            if self.tube_overdrive_in_rack_a:
                fx1_bus = self.tube_overdrive.process(fx1_bus)
            if self.tube_overdrive_in_rack_b:
                fx2_bus = self.tube_overdrive_b.process(fx2_bus)

            # Aural Exciter
            if self.exciter_in_rack_a:
                fx1_bus = self.exciter.process(fx1_bus)
            if self.exciter_in_rack_b:
                fx2_bus = self.exciter_b.process(fx2_bus)

            # Transient Shaper
            if self.transient_shaper_in_rack_a:
                fx1_bus = self.transient_shaper.process(fx1_bus)
            if self.transient_shaper_in_rack_b:
                fx2_bus = self.transient_shaper_b.process(fx2_bus)

            # Ether Drift (checks own state.enabled via process())
            if self.ether_drift_in_rack_a:
                fx1_bus = self.ether_drift.process(fx1_bus)
            if self.ether_drift_in_rack_b:
                fx2_bus = self.ether_drift_b.process(fx2_bus)

            # Harmonizer
            if self.harmonizer_in_rack_a:
                fx1_bus = self.harmonizer.process(fx1_bus)
            if self.harmonizer_in_rack_b:
                fx2_bus = self.harmonizer_b.process(fx2_bus)

            # Rotary Speaker
            if self.rotary_in_rack_a:
                fx1_bus = self.rotary.process(fx1_bus)
            if self.rotary_in_rack_b:
                fx2_bus = self.rotary_b.process(fx2_bus)

            # Stereo Widener
            if self.stereo_widener_in_rack_a:
                fx1_bus = self.stereo_widener.process(fx1_bus)
            if self.stereo_widener_in_rack_b:
                fx2_bus = self.stereo_widener_b.process(fx2_bus)

            # Frequency Shifter
            if self.freq_shifter_in_rack_a:
                fx1_bus = self.freq_shifter.process(fx1_bus)
            if self.freq_shifter_in_rack_b:
                fx2_bus = self.freq_shifter_b.process(fx2_bus)

            # Ring Modulator
            if self.ring_mod_in_rack_a:
                fx1_bus = self.ring_mod.process(fx1_bus)
            if self.ring_mod_in_rack_b:
                fx2_bus = self.ring_mod_b.process(fx2_bus)

            # Simple EQ
            if self.simple_eq_in_rack_a:
                fx1_bus = self.simple_eq.process(fx1_bus)
            if self.simple_eq_in_rack_b:
                fx2_bus = self.simple_eq_b.process(fx2_bus)

            # Ping-Pong Delay
            if self.ping_pong_delay_in_rack_a:
                fx1_bus = self.ping_pong_delay.process(fx1_bus)
            if self.ping_pong_delay_in_rack_b:
                fx2_bus = self.ping_pong_delay_b.process(fx2_bus)

            # Shimmer Reverb
            if self.shimmer_reverb_in_rack_a:
                fx1_bus = self.shimmer_reverb.process(fx1_bus)
            if self.shimmer_reverb_in_rack_b:
                fx2_bus = self.shimmer_reverb_b.process(fx2_bus)

            # Lo-Fi Radio
            if self.lofi_radio_in_rack_a:
                fx1_bus = self.lofi_radio.process(fx1_bus)
            if self.lofi_radio_in_rack_b:
                fx2_bus = self.lofi_radio_b.process(fx2_bus)

            # Auto-Pan
            if self.auto_pan_in_rack_a:
                fx1_bus = self.auto_pan.process(fx1_bus)
            if self.auto_pan_in_rack_b:
                fx2_bus = self.auto_pan_b.process(fx2_bus)

            # Fuzz
            if self.fuzz_in_rack_a:
                fx1_bus = self.fuzz.process(fx1_bus)
            if self.fuzz_in_rack_b:
                fx2_bus = self.fuzz_b.process(fx2_bus)
            # Granular Freeze
            if self.granular_freeze_in_rack_a:
                fx1_bus = self.granular_freeze.process(fx1_bus)
            if self.granular_freeze_in_rack_b:
                fx2_bus = self.granular_freeze_b.process(fx2_bus)
            # Gated Reverb
            if self.gate_reverb_in_rack_a:
                fx1_bus = self.gate_reverb.process(fx1_bus)
            if self.gate_reverb_in_rack_b:
                fx2_bus = self.gate_reverb_b.process(fx2_bus)
            # Stutter
            if self.stutter_in_rack_a:
                fx1_bus = self.stutter.process(fx1_bus)
            if self.stutter_in_rack_b:
                fx2_bus = self.stutter_b.process(fx2_bus)

            # --- FX Processing Chain C (rack C → FX3) ---
            if self.noise_gate_in_rack_c:
                fx3_bus = self.noise_gate_c.process(fx3_bus)
            if self.tremolo_in_rack_c:
                fx3_bus = self.tremolo_c.process(fx3_bus)
            if self.auto_wah_in_rack_c:
                fx3_bus = self.auto_wah_c.process(fx3_bus)
            if self.fet_limiter_in_rack_c:
                fx3_bus = self.fet_limiter_c.process(fx3_bus)
            if self.filter_a_in_rack_c and self.filter_a_c.state.enabled:
                fx3_bus = self.filter_a_c.process(fx3_bus)
            if self.filter_b_in_rack_c and self.filter_b_c.state.enabled:
                fx3_bus = self.filter_b_c.process(fx3_bus)
            if self.mod_in_rack_c:
                fx3_bus = self.fx_bus_c.process_mod_only(fx3_bus)
            if self.delay_in_rack_c and self.delay_c.state.enabled:
                fx3_bus = self.delay_c.process(fx3_bus)
            if self.tape_delay_in_rack_c and self.tape_delay_c.state.enabled:
                fx3_bus = self.tape_delay_c.process(fx3_bus)
            if self.reel_to_reel_in_rack_c:
                fx3_bus = self.reel_to_reel_c.process(fx3_bus)
            if self.cassette_tape_in_rack_c:
                fx3_bus = self.cassette_tape_c.process(fx3_bus)
            if self.vhs_in_rack_c:
                fx3_bus = self.vhs_c.process(fx3_bus)
            if self.vinyl_in_rack_c:
                fx3_bus = self.vinyl_c.process(fx3_bus)
            if self.hall_reverb_in_rack_c:
                fx3_bus = self.hall_reverb_c.process(fx3_bus)
            if self.plate_reverb_in_rack_c:
                fx3_bus = self.plate_reverb_c.process(fx3_bus)
            if self.compressor_in_rack_c:
                sc_c = self._get_compressor_sidechain(self.compressor_c, fx3_bus, frames)
                fx3_bus = self.compressor_c.process(fx3_bus, sc_c)
            if self.glue_compressor_in_rack_c:
                fx3_bus = self.glue_compressor_c.process(fx3_bus)
            if self.bit_crusher_in_rack_c:
                fx3_bus = self.bit_crusher_c.process(fx3_bus)
            if self.tube_overdrive_in_rack_c:
                fx3_bus = self.tube_overdrive_c.process(fx3_bus)
            if self.exciter_in_rack_c:
                fx3_bus = self.exciter_c.process(fx3_bus)
            if self.transient_shaper_in_rack_c:
                fx3_bus = self.transient_shaper_c.process(fx3_bus)
            if self.ether_drift_in_rack_c:
                fx3_bus = self.ether_drift_c.process(fx3_bus)
            if self.harmonizer_in_rack_c:
                fx3_bus = self.harmonizer_c.process(fx3_bus)
            if self.rotary_in_rack_c:
                fx3_bus = self.rotary_c.process(fx3_bus)
            if self.stereo_widener_in_rack_c:
                fx3_bus = self.stereo_widener_c.process(fx3_bus)
            if self.freq_shifter_in_rack_c:
                fx3_bus = self.freq_shifter_c.process(fx3_bus)
            if self.ring_mod_in_rack_c:
                fx3_bus = self.ring_mod_c.process(fx3_bus)
            if self.simple_eq_in_rack_c:
                fx3_bus = self.simple_eq_c.process(fx3_bus)
            if self.ping_pong_delay_in_rack_c:
                fx3_bus = self.ping_pong_delay_c.process(fx3_bus)
            if self.shimmer_reverb_in_rack_c:
                fx3_bus = self.shimmer_reverb_c.process(fx3_bus)
            if self.lofi_radio_in_rack_c:
                fx3_bus = self.lofi_radio_c.process(fx3_bus)
            if self.auto_pan_in_rack_c:
                fx3_bus = self.auto_pan_c.process(fx3_bus)
            if self.fuzz_in_rack_c:
                fx3_bus = self.fuzz_c.process(fx3_bus)
            if self.granular_freeze_in_rack_c:
                fx3_bus = self.granular_freeze_c.process(fx3_bus)
            if self.gate_reverb_in_rack_c:
                fx3_bus = self.gate_reverb_c.process(fx3_bus)
            if self.stutter_in_rack_c:
                fx3_bus = self.stutter_c.process(fx3_bus)

            # --- FX Processing Chain D (rack D → FX4) ---
            if self.noise_gate_in_rack_d:
                fx4_bus = self.noise_gate_d.process(fx4_bus)
            if self.tremolo_in_rack_d:
                fx4_bus = self.tremolo_d.process(fx4_bus)
            if self.auto_wah_in_rack_d:
                fx4_bus = self.auto_wah_d.process(fx4_bus)
            if self.fet_limiter_in_rack_d:
                fx4_bus = self.fet_limiter_d.process(fx4_bus)
            if self.filter_a_in_rack_d and self.filter_a_d.state.enabled:
                fx4_bus = self.filter_a_d.process(fx4_bus)
            if self.filter_b_in_rack_d and self.filter_b_d.state.enabled:
                fx4_bus = self.filter_b_d.process(fx4_bus)
            if self.mod_in_rack_d:
                fx4_bus = self.fx_bus_d.process_mod_only(fx4_bus)
            if self.delay_in_rack_d and self.delay_d.state.enabled:
                fx4_bus = self.delay_d.process(fx4_bus)
            if self.tape_delay_in_rack_d and self.tape_delay_d.state.enabled:
                fx4_bus = self.tape_delay_d.process(fx4_bus)
            if self.reel_to_reel_in_rack_d:
                fx4_bus = self.reel_to_reel_d.process(fx4_bus)
            if self.cassette_tape_in_rack_d:
                fx4_bus = self.cassette_tape_d.process(fx4_bus)
            if self.vhs_in_rack_d:
                fx4_bus = self.vhs_d.process(fx4_bus)
            if self.vinyl_in_rack_d:
                fx4_bus = self.vinyl_d.process(fx4_bus)
            if self.hall_reverb_in_rack_d:
                fx4_bus = self.hall_reverb_d.process(fx4_bus)
            if self.plate_reverb_in_rack_d:
                fx4_bus = self.plate_reverb_d.process(fx4_bus)
            if self.compressor_in_rack_d:
                sc_d = self._get_compressor_sidechain(self.compressor_d, fx4_bus, frames)
                fx4_bus = self.compressor_d.process(fx4_bus, sc_d)
            if self.glue_compressor_in_rack_d:
                fx4_bus = self.glue_compressor_d.process(fx4_bus)
            if self.bit_crusher_in_rack_d:
                fx4_bus = self.bit_crusher_d.process(fx4_bus)
            if self.tube_overdrive_in_rack_d:
                fx4_bus = self.tube_overdrive_d.process(fx4_bus)
            if self.exciter_in_rack_d:
                fx4_bus = self.exciter_d.process(fx4_bus)
            if self.transient_shaper_in_rack_d:
                fx4_bus = self.transient_shaper_d.process(fx4_bus)
            if self.ether_drift_in_rack_d:
                fx4_bus = self.ether_drift_d.process(fx4_bus)
            if self.harmonizer_in_rack_d:
                fx4_bus = self.harmonizer_d.process(fx4_bus)
            if self.rotary_in_rack_d:
                fx4_bus = self.rotary_d.process(fx4_bus)
            if self.stereo_widener_in_rack_d:
                fx4_bus = self.stereo_widener_d.process(fx4_bus)
            if self.freq_shifter_in_rack_d:
                fx4_bus = self.freq_shifter_d.process(fx4_bus)
            if self.ring_mod_in_rack_d:
                fx4_bus = self.ring_mod_d.process(fx4_bus)
            if self.simple_eq_in_rack_d:
                fx4_bus = self.simple_eq_d.process(fx4_bus)
            if self.ping_pong_delay_in_rack_d:
                fx4_bus = self.ping_pong_delay_d.process(fx4_bus)
            if self.shimmer_reverb_in_rack_d:
                fx4_bus = self.shimmer_reverb_d.process(fx4_bus)
            if self.lofi_radio_in_rack_d:
                fx4_bus = self.lofi_radio_d.process(fx4_bus)
            if self.auto_pan_in_rack_d:
                fx4_bus = self.auto_pan_d.process(fx4_bus)
            if self.fuzz_in_rack_d:
                fx4_bus = self.fuzz_d.process(fx4_bus)
            if self.granular_freeze_in_rack_d:
                fx4_bus = self.granular_freeze_d.process(fx4_bus)
            if self.gate_reverb_in_rack_d:
                fx4_bus = self.gate_reverb_d.process(fx4_bus)
            if self.stutter_in_rack_d:
                fx4_bus = self.stutter_d.process(fx4_bus)

            # Track FX bus peaks before summing (for per-rack metering)
            tmp = self._peak_tmp[:frames]
            np.abs(fx1_bus[:, 0], out=tmp)
            self._fx1_peak[0] = max(self._fx1_peak[0] * 0.95, float(tmp.max()))
            np.abs(fx1_bus[:, 1], out=tmp)
            self._fx1_peak[1] = max(self._fx1_peak[1] * 0.95, float(tmp.max()))
            np.abs(fx2_bus[:, 0], out=tmp)
            self._fx2_peak[0] = max(self._fx2_peak[0] * 0.95, float(tmp.max()))
            np.abs(fx2_bus[:, 1], out=tmp)
            self._fx2_peak[1] = max(self._fx2_peak[1] * 0.95, float(tmp.max()))
            np.abs(fx3_bus[:, 0], out=tmp)
            self._fx3_peak[0] = max(self._fx3_peak[0] * 0.95, float(tmp.max()))
            np.abs(fx3_bus[:, 1], out=tmp)
            self._fx3_peak[1] = max(self._fx3_peak[1] * 0.95, float(tmp.max()))
            np.abs(fx4_bus[:, 0], out=tmp)
            self._fx4_peak[0] = max(self._fx4_peak[0] * 0.95, float(tmp.max()))
            np.abs(fx4_bus[:, 1], out=tmp)
            self._fx4_peak[1] = max(self._fx4_peak[1] * 0.95, float(tmp.max()))

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

            if _prof:
                _t3 = time.perf_counter()

            # Master output
            aux_return = self._zero_buf[:frames]
            dsp_output = self.mixer.apply_master(main_bus, aux_return)

            # Master filter trim (HP then LP)
            if self._master_hp_freq > 21.0:
                dsp_output = self._master_hp.process(dsp_output)
            if self._master_lp_freq < 19900.0:
                dsp_output = self._master_lp.process(dsp_output)

            # --- FX Group M (Master rack) — post-master-trim chain ---
            if self.noise_gate_in_rack_m:
                dsp_output = self.noise_gate_m.process(dsp_output)
            if self.tremolo_in_rack_m:
                dsp_output = self.tremolo_m.process(dsp_output)
            if self.auto_wah_in_rack_m:
                dsp_output = self.auto_wah_m.process(dsp_output)
            if self.fet_limiter_in_rack_m:
                dsp_output = self.fet_limiter_m.process(dsp_output)
            if self.filter_a_in_rack_m and self.filter_a_m.state.enabled:
                dsp_output = self.filter_a_m.process(dsp_output)
            if self.filter_b_in_rack_m and self.filter_b_m.state.enabled:
                dsp_output = self.filter_b_m.process(dsp_output)
            if self.mod_in_rack_m:
                dsp_output = self.fx_bus_m.process_mod_only(dsp_output)
            if self.delay_in_rack_m and self.delay_m.state.enabled:
                dsp_output = self.delay_m.process(dsp_output)
            if self.tape_delay_in_rack_m and self.tape_delay_m.state.enabled:
                dsp_output = self.tape_delay_m.process(dsp_output)
            if self.reel_to_reel_in_rack_m:
                dsp_output = self.reel_to_reel_m.process(dsp_output)
            if self.cassette_tape_in_rack_m:
                dsp_output = self.cassette_tape_m.process(dsp_output)
            if self.vhs_in_rack_m:
                dsp_output = self.vhs_m.process(dsp_output)
            if self.vinyl_in_rack_m:
                dsp_output = self.vinyl_m.process(dsp_output)
            if self.hall_reverb_in_rack_m:
                dsp_output = self.hall_reverb_m.process(dsp_output)
            if self.plate_reverb_in_rack_m:
                dsp_output = self.plate_reverb_m.process(dsp_output)
            if self.compressor_in_rack_m:
                dsp_output = self.compressor_m.process(dsp_output)
            if self.glue_compressor_in_rack_m:
                dsp_output = self.glue_compressor_m.process(dsp_output)
            if self.bit_crusher_in_rack_m:
                dsp_output = self.bit_crusher_m.process(dsp_output)
            if self.tube_overdrive_in_rack_m:
                dsp_output = self.tube_overdrive_m.process(dsp_output)
            if self.exciter_in_rack_m:
                dsp_output = self.exciter_m.process(dsp_output)
            if self.transient_shaper_in_rack_m:
                dsp_output = self.transient_shaper_m.process(dsp_output)
            if self.ether_drift_in_rack_m:
                dsp_output = self.ether_drift_m.process(dsp_output)
            if self.harmonizer_in_rack_m:
                dsp_output = self.harmonizer_m.process(dsp_output)
            if self.rotary_in_rack_m:
                dsp_output = self.rotary_m.process(dsp_output)
            if self.stereo_widener_in_rack_m:
                dsp_output = self.stereo_widener_m.process(dsp_output)
            if self.freq_shifter_in_rack_m:
                dsp_output = self.freq_shifter_m.process(dsp_output)
            if self.ring_mod_in_rack_m:
                dsp_output = self.ring_mod_m.process(dsp_output)
            if self.simple_eq_in_rack_m:
                dsp_output = self.simple_eq_m.process(dsp_output)
            if self.ping_pong_delay_in_rack_m:
                dsp_output = self.ping_pong_delay_m.process(dsp_output)
            if self.shimmer_reverb_in_rack_m:
                dsp_output = self.shimmer_reverb_m.process(dsp_output)
            if self.lofi_radio_in_rack_m:
                dsp_output = self.lofi_radio_m.process(dsp_output)
            if self.auto_pan_in_rack_m:
                dsp_output = self.auto_pan_m.process(dsp_output)
            if self.fuzz_in_rack_m:
                dsp_output = self.fuzz_m.process(dsp_output)
            if self.granular_freeze_in_rack_m:
                dsp_output = self.granular_freeze_m.process(dsp_output)
            if self.gate_reverb_in_rack_m:
                dsp_output = self.gate_reverb_m.process(dsp_output)
            if self.stutter_in_rack_m:
                dsp_output = self.stutter_m.process(dsp_output)

            if _prof:
                _t4 = time.perf_counter()

            # Peak metering
            tmp = self._peak_tmp[:len(dsp_output)]
            np.abs(dsp_output[:, 0], out=tmp)
            self.output_peak[0] = max(self.output_peak[0] * 0.95, float(tmp.max()))
            np.abs(dsp_output[:, 1], out=tmp)
            self.output_peak[1] = max(self.output_peak[1] * 0.95, float(tmp.max()))

            outbuf[:] = dsp_output

            # Meter callback for UI — decimated to ~30 fps to reduce GC pressure
            if _send_meters:
                step = max(1, len(dsp_output) // 64)
                _wn = len(dsp_output[::step, 0])
                self._wf_master_l[:_wn] = dsp_output[::step, 0]
                self._wf_master_r[:_wn] = dsp_output[::step, 1]
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
                md["waveform_l"] = self._wf_master_l[:_wn]
                md["waveform_r"] = self._wf_master_r[:_wn]
                md["waveform_fx1_l"] = self._wf_fx1_l[:_wn]
                md["waveform_fx1_r"] = self._wf_fx1_r[:_wn]
                md["waveform_fx2_l"] = self._wf_fx2_l[:_wn]
                md["waveform_fx2_r"] = self._wf_fx2_r[:_wn]
                md["waveform_fx3_l"] = self._wf_fx3_l[:_wn]
                md["waveform_fx3_r"] = self._wf_fx3_r[:_wn]
                md["waveform_fx4_l"] = self._wf_fx4_l[:_wn]
                md["waveform_fx4_r"] = self._wf_fx4_r[:_wn]
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
                self._meter_callback(md)

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
                    print(f"\n[DSP PROFILE] {n} blocks, budget={budget_ms:.2f}ms")
                    print(f"  avg total:   {self._prof_total/n*1000:.2f}ms  "
                          f"max: {self._prof_max*1000:.2f}ms  "
                          f"overruns: {self._prof_overruns} ({self._prof_overruns/n*100:.1f}%)")
                    print(f"  channels:    {self._prof_channels/n*1000:.2f}ms  "
                          f"({self._prof_channels/self._prof_total*100:.0f}%)")
                    print(f"  mixer:       {self._prof_mixer/n*1000:.2f}ms  "
                          f"({self._prof_mixer/self._prof_total*100:.0f}%)")
                    print(f"  fx_buses:    {self._prof_fx/n*1000:.2f}ms  "
                          f"({self._prof_fx/self._prof_total*100:.0f}%)")
                    print(f"  master:      {self._prof_master/n*1000:.2f}ms  "
                          f"({self._prof_master/self._prof_total*100:.0f}%)")
                    print(f"  metering:    {self._prof_meter/n*1000:.2f}ms  "
                          f"({self._prof_meter/self._prof_total*100:.0f}%)")
                    active = len(channel_outputs)
                    ring_avg = self._prof_ring_sum / max(n, 1)
                    print(f"  active_ch:   {active}  numba: {_HAS_NUMBA}  "
                          f"ring: min={self._prof_ring_min} avg={ring_avg:.1f}")
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

        except Exception as exc:
            import traceback
            traceback.print_exc()
            outbuf[:] = 0

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
            # THREAD_PRIORITY_HIGHEST = 2 (above normal, below time-critical)
            ctypes.windll.kernel32.SetThreadPriority(handle, 2)
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
            except Exception:
                import traceback
                traceback.print_exc()
                slot[:] = 0

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

    def set_master_hp(self, freq: float):
        """Set master highpass trim frequency (20-500 Hz)."""
        self._master_hp_freq = freq
        self._master_hp.set_highpass(freq, 0.707)

    def set_master_lp(self, freq: float):
        """Set master lowpass trim frequency (5000-20000 Hz)."""
        self._master_lp_freq = freq
        self._master_lp.set_lowpass(freq, 0.707)

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
        """Load a sample into a channel."""
        if 0 <= row < MAX_ROWS:
            self.channels[row].load_sample(sample)

    def set_channel_speed(self, row: int, tempo: float):
        """Set channel playback speed based on tempo and sample length."""
        if 0 <= row < MAX_ROWS:
            ch = self.channels[row]
            if ch.sample is not None and ch.sample.duration_sec > 0:
                # Calculate speed to fit sample into the step grid
                beats = ch.state.steps
                bar_duration = beats * (60.0 / tempo)
                sample_duration = ch.sample.duration_sec * (ch.state.sel_end - ch.state.sel_start)
                if sample_duration > 0:
                    ch.state.speed = sample_duration / bar_duration

    def set_tempo(self, tempo: float):
        """Update tempo for all tempo-synced components."""
        old_tempo = self.config.tempo
        self.config.tempo = tempo
        self.delay.set_tempo(tempo)
        self.delay_b.set_tempo(tempo)
        self.delay_m.set_tempo(tempo)
        self.tape_delay.set_tempo(tempo)
        self.tape_delay_b.set_tempo(tempo)
        self.tape_delay_m.set_tempo(tempo)
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
