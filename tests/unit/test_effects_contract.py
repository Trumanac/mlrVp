"""Universal contract tests across every FX processor in mlrvp.audio.effects.

Every processor listed here follows the same convention (confirmed by
reading effects.py): ``self.state`` holds a dataclass with (at minimum)
an ``enabled`` flag; ``process(data)`` returns ``data`` unchanged when
``enabled`` is False, and — for processors with a wet/dry ``mix`` field —
``mix == 0.0`` should be (near) fully dry.

This doesn't replace processor-specific DSP-correctness tests (see the
other test_effects_*.py files for those), but it cheaply guards the one
behavioral contract every single rack module actually depends on: engine.py
gates most FX purely via ``state.enabled`` (see ``_FX_NEEDS_ENABLED_CHECK``
in engine.py for the handful of exceptions that DON'T self-gate and are
covered separately in test_effects_filters.py / test_effects_delay_mod.py),
and the rack UI's per-module mix knob is expected to crossfade to a true
bypass at 0.
"""
import numpy as np
import pytest

from mlrvp.audio.effects import (
    BitCrusherProcessor, TubeOverdriveProcessor, ReelToReelProcessor,
    CassetteTapeProcessor, EtherDriftProcessor, VHSProcessor, VinylProcessor,
    CompressorProcessor, GlueCompressorProcessor, HallReverbProcessor,
    PlateReverbProcessor, NoiseGateProcessor, TremoloProcessor,
    AutoWahProcessor, FETLimiterProcessor, RotaryProcessor, ExciterProcessor,
    TransientShaperProcessor, HarmonizerProcessor, StereoWidenerProcessor,
    FreqShifterProcessor, RingModProcessor, SimpleEQProcessor,
    PingPongDelayProcessor, ShimmerReverbProcessor, LoFiRadioProcessor,
    AutoPanProcessor, FuzzProcessor, GranularFreezeProcessor,
    GatedReverbProcessor, StutterProcessor,
)
from mlrvp.config import SAMPLE_RATE

# (label, class) — every processor that self-gates on state.enabled.
# StereoDelay/TapeDelay/GlobalFilter/MoogLadderFilter are deliberately
# excluded: engine.py has to gate THEM externally (_FX_NEEDS_ENABLED_CHECK)
# precisely because they do NOT bypass internally — see test_effects_filters.py
# and test_effects_delay_mod.py.
SELF_GATING_PROCESSORS = [
    ("bit_crusher", BitCrusherProcessor),
    ("tube_overdrive", TubeOverdriveProcessor),
    ("reel_to_reel", ReelToReelProcessor),
    ("cassette_tape", CassetteTapeProcessor),
    ("ether_drift", EtherDriftProcessor),
    ("vhs", VHSProcessor),
    ("vinyl", VinylProcessor),
    ("compressor", CompressorProcessor),
    ("glue_compressor", GlueCompressorProcessor),
    ("hall_reverb", HallReverbProcessor),
    ("plate_reverb", PlateReverbProcessor),
    ("noise_gate", NoiseGateProcessor),
    ("tremolo", TremoloProcessor),
    ("auto_wah", AutoWahProcessor),
    ("fet_limiter", FETLimiterProcessor),
    ("rotary", RotaryProcessor),
    ("exciter", ExciterProcessor),
    ("transient_shaper", TransientShaperProcessor),
    ("harmonizer", HarmonizerProcessor),
    ("stereo_widener", StereoWidenerProcessor),
    ("freq_shifter", FreqShifterProcessor),
    ("ring_mod", RingModProcessor),
    ("simple_eq", SimpleEQProcessor),
    ("ping_pong_delay", PingPongDelayProcessor),
    ("shimmer_reverb", ShimmerReverbProcessor),
    ("lofi_radio", LoFiRadioProcessor),
    ("auto_pan", AutoPanProcessor),
    ("fuzz", FuzzProcessor),
    ("granular_freeze", GranularFreezeProcessor),
    ("gate_reverb", GatedReverbProcessor),
    ("stutter", StutterProcessor),
]

IDS = [label for label, _ in SELF_GATING_PROCESSORS]


def make_test_signal(num_frames: int = 512) -> np.ndarray:
    """A stereo test tone — not silence (some processors treat all-zero
    input as a degenerate/no-op case), not full-scale (headroom for
    processors that add gain)."""
    t = np.arange(num_frames, dtype=np.float64) / SAMPLE_RATE
    tone = (0.4 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    return np.stack([tone, tone * 0.9], axis=1)


@pytest.fixture(params=SELF_GATING_PROCESSORS, ids=IDS)
def processor(request):
    label, cls = request.param
    return label, cls(SAMPLE_RATE)


class TestEnabledBypass:
    def test_disabled_returns_input_unchanged(self, processor):
        label, proc = processor
        proc.state.enabled = False
        signal = make_test_signal()
        out = proc.process(signal.copy())
        np.testing.assert_array_equal(out, signal, err_msg=f"{label} mutated audio while disabled")

    def test_enabled_produces_finite_output_of_same_shape(self, processor):
        label, proc = processor
        proc.state.enabled = True
        signal = make_test_signal()
        out = proc.process(signal.copy())
        assert out.shape == signal.shape, f"{label} changed buffer shape"
        assert np.all(np.isfinite(out)), f"{label} produced NaN/Inf"

    def test_enabled_survives_silence(self, processor):
        label, proc = processor
        proc.state.enabled = True
        silence = np.zeros((512, 2), dtype=np.float32)
        out = proc.process(silence)
        assert np.all(np.isfinite(out)), f"{label} produced NaN/Inf on silent input"

    def test_enabled_survives_full_scale_input(self, processor):
        label, proc = processor
        proc.state.enabled = True
        loud = np.random.RandomState(0).uniform(-1.0, 1.0, (512, 2)).astype(np.float32)
        out = proc.process(loud)
        assert np.all(np.isfinite(out)), f"{label} produced NaN/Inf on full-scale noise"

    def test_multiple_blocks_stay_finite(self, processor):
        """Catches state (filter/envelope/delay-line) that blows up over time."""
        label, proc = processor
        proc.state.enabled = True
        signal = make_test_signal()
        for _ in range(20):
            signal = proc.process(signal)
            assert np.all(np.isfinite(signal)), f"{label} diverged after repeated processing"


class TestMixKnob:
    # exciter has TWO independent blend controls (state.mix for the harmonics
    # path, state.lo_mix for the low-end phase-enhancement path — see
    # ExciterProcessor.process()) — mix=0.0 alone isn't full bypass by design.
    _MULTI_STAGE_MIX = {"exciter"}

    def test_zero_mix_is_fully_dry(self, processor):
        label, proc = processor
        if not hasattr(proc.state, "mix"):
            pytest.skip(f"{label} has no wet/dry mix control")
        if label in self._MULTI_STAGE_MIX:
            pytest.skip(f"{label} has additional independent blend stages beyond state.mix")
        proc.state.enabled = True
        proc.state.mix = 0.0
        signal = make_test_signal()
        out = proc.process(signal.copy())
        np.testing.assert_allclose(
            out, signal, atol=1e-4,
            err_msg=f"{label} mix=0.0 is not fully dry",
        )
