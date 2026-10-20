"""Frequency-domain correctness tests for the filter-family processors.

BiquadFilter / ChannelFilter / GlobalFilter / MoogLadderFilter don't
self-gate on state.enabled (engine.py has to gate them externally via
_FX_NEEDS_ENABLED_CHECK) — see test_effects_contract.py's docstring for
why they're excluded from the universal contract suite. They get their
own correctness tests here instead, checked the way a filter actually
should be verified: does it attenuate the frequencies it claims to.
"""
import numpy as np
import pytest

from mlrvp.audio.effects import (
    BiquadFilter, ChannelFilter, GlobalFilter, MoogLadderFilter,
    SimpleEQProcessor,
)
from mlrvp.config import SAMPLE_RATE


def sine(freq: float, seconds: float = 0.2, sr: int = SAMPLE_RATE, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * sr), dtype=np.float64) / sr
    tone = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([tone, tone], axis=1)


def process_in_blocks(proc, signal: np.ndarray, block: int = 512) -> np.ndarray:
    """Feed a long signal through *proc* in engine-sized blocks — filters
    carry state across calls, so processing the whole signal at once
    would hide any per-block bookkeeping bugs."""
    out = np.empty_like(signal)
    for i in range(0, len(signal), block):
        out[i:i + block] = proc.process(signal[i:i + block])
    return out


def rms(x: np.ndarray) -> float:
    # Skip the first 10% to let filter transients settle before measuring.
    settle = len(x) // 10
    return float(np.sqrt(np.mean(np.square(x[settle:]))))


class TestBiquadFilter:
    def test_lowpass_passes_low_frequency(self):
        f = BiquadFilter(SAMPLE_RATE)
        f.set_lowpass(1000.0)
        sig = sine(100.0)
        out = process_in_blocks(f, sig)
        assert rms(out) > 0.8 * rms(sig)

    def test_lowpass_attenuates_high_frequency(self):
        f = BiquadFilter(SAMPLE_RATE)
        f.set_lowpass(500.0)
        sig = sine(8000.0)
        out = process_in_blocks(f, sig)
        assert rms(out) < 0.3 * rms(sig)

    def test_highpass_passes_high_frequency(self):
        f = BiquadFilter(SAMPLE_RATE)
        f.set_highpass(500.0)
        sig = sine(8000.0)
        out = process_in_blocks(f, sig)
        assert rms(out) > 0.8 * rms(sig)

    def test_highpass_attenuates_low_frequency(self):
        f = BiquadFilter(SAMPLE_RATE)
        f.set_highpass(2000.0)
        sig = sine(80.0)
        out = process_in_blocks(f, sig)
        assert rms(out) < 0.3 * rms(sig)

    def test_reset_clears_filter_state(self):
        f = BiquadFilter(SAMPLE_RATE)
        f.set_lowpass(500.0)
        process_in_blocks(f, sine(8000.0))
        f.reset()
        assert np.all(f._zi == 0.0)


class TestChannelFilter:
    def test_lp_bypass_at_zero(self):
        f = ChannelFilter(SAMPLE_RATE)
        f.set_lp(0.0)
        sig = sine(8000.0)
        out = process_in_blocks(f, sig)
        np.testing.assert_allclose(out, sig)

    def test_lp_fully_closed_attenuates_highs(self):
        f = ChannelFilter(SAMPLE_RATE)
        f.set_lp(1.0)  # fully closed -> 200 Hz cutoff
        sig = sine(8000.0)
        out = process_in_blocks(f, sig)
        assert rms(out) < 0.2 * rms(sig)

    def test_hp_bypass_at_zero(self):
        f = ChannelFilter(SAMPLE_RATE)
        f.set_hp(0.0)
        sig = sine(80.0)
        out = process_in_blocks(f, sig)
        np.testing.assert_allclose(out, sig)

    def test_hp_fully_closed_attenuates_lows(self):
        f = ChannelFilter(SAMPLE_RATE)
        f.set_hp(1.0)  # fully closed -> 8 kHz cutoff
        sig = sine(80.0)
        out = process_in_blocks(f, sig)
        assert rms(out) < 0.2 * rms(sig)

    def test_lp_hp_knob_path_takes_priority_over_legacy_filter_val(self):
        # process() branches on "is either new knob active (>0.01)?" — when
        # yes, the legacy filter_val sweep is ignored entirely even if set.
        f = ChannelFilter(SAMPLE_RATE)
        f.set_filter(1.0)  # legacy full-HP sweep — would attenuate an 80 Hz tone
        f.set_lp(1.0)       # new knob active -> LP path wins, no HP applied
        sig = sine(80.0)
        out = process_in_blocks(f, sig)
        # LP at 200 Hz cutoff barely touches an 80 Hz tone; if the legacy HP
        # sweep were still active instead, this would be heavily attenuated.
        assert rms(out) > 0.6 * rms(sig)

    def test_lp_at_zero_falls_back_to_legacy_filter_val(self):
        # 0.0 means "the new knob hasn't been touched", not "force bypass" —
        # process() falls back to whatever the legacy sweep was set to.
        f = ChannelFilter(SAMPLE_RATE)
        f.set_filter(-1.0)  # legacy full-LP sweep -> 200 Hz cutoff
        f.set_lp(0.0)
        f.set_hp(0.0)
        sig = sine(8000.0)
        out = process_in_blocks(f, sig)
        assert rms(out) < 0.3 * rms(sig)


class TestGlobalFilter:
    def test_disabled_bands_pass_through_unchanged(self):
        f = GlobalFilter(SAMPLE_RATE)
        # All *_enabled flags default False, gain knobs default to unity (0.5)
        sig = sine(1000.0)
        out = process_in_blocks(f, sig)
        np.testing.assert_allclose(out, sig, atol=1e-5)

    def test_lp_enabled_attenuates_highs(self):
        f = GlobalFilter(SAMPLE_RATE)
        f.state.lp_enabled = True
        f.state.lp_freq = 500.0
        f._update()
        sig = sine(8000.0)
        out = process_in_blocks(f, sig)
        assert rms(out) < 0.3 * rms(sig)

    def test_hp_enabled_attenuates_lows(self):
        f = GlobalFilter(SAMPLE_RATE)
        f.state.hp_enabled = True
        f.state.hp_freq = 2000.0
        f._update()
        sig = sine(80.0)
        out = process_in_blocks(f, sig)
        assert rms(out) < 0.3 * rms(sig)

    def test_drive_keeps_output_bounded(self):
        f = GlobalFilter(SAMPLE_RATE)
        f.state.drive = 1.0
        sig = sine(200.0, amp=0.9)
        out = process_in_blocks(f, sig)
        # tanh soft-saturation should never exceed the drive-normalised
        # unity ceiling by more than a small margin.
        assert np.max(np.abs(out)) < 1.5

    def test_output_gain_boosts_amplitude(self):
        f_unity = GlobalFilter(SAMPLE_RATE)
        sig = sine(1000.0)
        out_unity = process_in_blocks(f_unity, sig.copy())

        f_boost = GlobalFilter(SAMPLE_RATE)
        f_boost.state.output_gain = 1.0  # +12 dB
        out_boost = process_in_blocks(f_boost, sig.copy())

        assert rms(out_boost) > rms(out_unity) * 2.0


class TestMoogLadderFilter:
    def test_low_cutoff_attenuates_high_frequency(self):
        f = MoogLadderFilter(SAMPLE_RATE)
        f.state.cutoff = 300.0
        f.state.resonance = 0.0
        f.state.drive = 0.0
        sig = sine(6000.0)
        out = process_in_blocks(f, sig)
        assert rms(out) < 0.3 * rms(sig)

    def test_high_cutoff_passes_low_frequency(self):
        f = MoogLadderFilter(SAMPLE_RATE)
        f.state.cutoff = 15000.0
        f.state.resonance = 0.0
        f.state.drive = 0.0
        sig = sine(200.0)
        out = process_in_blocks(f, sig)
        assert rms(out) > 0.6 * rms(sig)

    def test_high_resonance_does_not_blow_up(self):
        f = MoogLadderFilter(SAMPLE_RATE)
        f.state.cutoff = 1000.0
        f.state.resonance = 1.0  # scaled internally to near self-oscillation
        f.state.drive = 0.5
        sig = sine(500.0)
        out = process_in_blocks(f, sig)
        assert np.all(np.isfinite(out))
        assert np.max(np.abs(out)) < 10.0  # generous — just guards against divergence

    def test_reset_clears_stage_state(self):
        f = MoogLadderFilter(SAMPLE_RATE)
        f.state.cutoff = 500.0
        f.state.resonance = 0.8
        process_in_blocks(f, sine(1000.0, amp=0.9))
        f.reset()
        assert np.all(f._y == 0.0)
        assert np.all(f._fb == 0.0)


class TestSimpleEQProcessor:
    def test_flat_settings_pass_through_near_unity(self):
        eq = SimpleEQProcessor(SAMPLE_RATE)
        sig = sine(1000.0)
        out = process_in_blocks(eq, sig)
        assert rms(out) == pytest.approx(rms(sig), rel=0.05)

    def test_low_band_cut_reduces_low_frequency_energy(self):
        eq = SimpleEQProcessor(SAMPLE_RATE)
        eq.state.low = -12.0
        eq.state.mix = 1.0
        sig = sine(100.0)  # within the low-shelf's default corner range
        out = process_in_blocks(eq, sig)
        assert rms(out) < rms(sig)

    def test_high_band_boost_increases_high_frequency_energy(self):
        eq = SimpleEQProcessor(SAMPLE_RATE)
        eq.state.high = 12.0
        eq.state.mix = 1.0
        sig = sine(9000.0)
        out = process_in_blocks(eq, sig)
        assert rms(out) > rms(sig)
