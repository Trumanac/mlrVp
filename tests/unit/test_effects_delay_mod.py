"""Tests for delay and modulation processors: StereoDelay, TapeDelay,
PingPongDelayProcessor, TremoloProcessor, AutoPanProcessor, RingModProcessor,
FreqShifterProcessor, StereoWidenerProcessor, and FXBus's phaser/chorus/flanger.

StereoDelay/TapeDelay don't self-gate on state.enabled (same story as the
plain filters — see test_effects_filters.py's module docstring), so they
get their own tests here rather than living in the contract suite.
"""
import numpy as np
import pytest

from mlrvp.audio.effects import (
    StereoDelay, TapeDelay, PingPongDelayProcessor, TremoloProcessor,
    AutoPanProcessor, RingModProcessor, FreqShifterProcessor,
    StereoWidenerProcessor, FXBus,
)
from mlrvp.config import SAMPLE_RATE, ModState


def tone(amp: float = 0.5, seconds: float = 0.3, freq: float = 220.0, sr: int = SAMPLE_RATE) -> np.ndarray:
    t = np.arange(int(seconds * sr), dtype=np.float64) / sr
    sig = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([sig, sig], axis=1)


def impulse(seconds: float = 1.0, sr: int = SAMPLE_RATE) -> np.ndarray:
    n = int(seconds * sr)
    sig = np.zeros((n, 2), dtype=np.float32)
    sig[0] = 1.0
    return sig


def process_in_blocks(proc, signal: np.ndarray, block: int = 512) -> np.ndarray:
    out = np.empty_like(signal)
    for i in range(0, len(signal), block):
        out[i:i + block] = proc.process(signal[i:i + block])
    return out


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


class TestStereoDelay:
    def test_produces_echo_at_expected_delay_time(self):
        d = StereoDelay(SAMPLE_RATE)
        d.state.time_mode = 1  # ms mode
        d.state.ms_time = 100.0
        d.state.gain = 1.0
        d.state.fb_range_low = 0.0
        d.state.fb_range_high = 0.0  # isolate the single tap, no feedback tail
        sig = impulse(seconds=0.3)
        out = process_in_blocks(d, sig)
        expected_delay_samples = int(0.1 * SAMPLE_RATE)
        # Energy should show up in a window around the expected delay tap.
        window = out[expected_delay_samples - 50:expected_delay_samples + 50]
        assert np.max(np.abs(window)) > 0.5

    def test_zero_gain_produces_no_audible_echo(self):
        d = StereoDelay(SAMPLE_RATE)
        d.state.time_mode = 1
        d.state.ms_time = 100.0
        d.state.gain = 0.0
        d.state.fb_range_low = 0.0
        d.state.fb_range_high = 0.0
        sig = impulse(seconds=0.3)
        out = process_in_blocks(d, sig)
        expected_delay_samples = int(0.1 * SAMPLE_RATE)
        window = out[expected_delay_samples - 50:expected_delay_samples + 50]
        assert np.max(np.abs(window)) < 0.01

    def test_feedback_extends_the_tail(self):
        # Compare against a zero-feedback baseline rather than an absolute
        # threshold — the feedback envelope smooths in over fb_attack ms
        # (default 67 ms), so the exact settled level at a fixed time
        # depends on that ramp, not just fb_range_high.
        def build(fb_pct: float) -> np.ndarray:
            d = StereoDelay(SAMPLE_RATE)
            d.state.time_mode = 1
            d.state.ms_time = 50.0
            d.state.gain = 0.8
            d.state.fb_range_low = fb_pct
            d.state.fb_range_high = fb_pct
            return process_in_blocks(d, impulse(seconds=1.0))

        out_fb = build(60.0)
        out_no_fb = build(0.0)
        window = slice(int(0.4 * SAMPLE_RATE), int(0.4 * SAMPLE_RATE) + 512)
        assert rms(out_fb[window]) > rms(out_no_fb[window])

    def test_clear_empties_the_buffer(self):
        d = StereoDelay(SAMPLE_RATE)
        d.state.gain = 1.0
        process_in_blocks(d, tone(amp=0.8, seconds=0.1))
        d.clear()
        assert np.all(d.buffer == 0.0)
        assert d.write_pos == 0


class TestTapeDelay:
    def test_produces_finite_echo_output(self):
        td = TapeDelay(SAMPLE_RATE)
        td.state.time_mode = 1
        td.state.ms_time = 200.0
        td.state.feedback = 0.5
        td.state.gain = 0.8
        out = process_in_blocks(td, impulse(seconds=1.0))
        assert np.all(np.isfinite(out))
        assert np.max(np.abs(out)) > 0.01  # echo actually produced something

    def test_zero_feedback_still_produces_direct_taps(self):
        td = TapeDelay(SAMPLE_RATE)
        td.state.time_mode = 1
        td.state.ms_time = 150.0
        td.state.feedback = 0.0
        td.state.gain = 1.0
        out = process_in_blocks(td, impulse(seconds=0.5))
        assert np.max(np.abs(out)) > 0.01


class TestPingPongDelay:
    def test_produces_finite_echo_tail(self):
        pp = PingPongDelayProcessor(SAMPLE_RATE)
        pp.state.time_ms = 200.0
        pp.state.feedback = 0.5
        pp.state.mix = 1.0
        out = process_in_blocks(pp, impulse(seconds=1.0))
        assert np.all(np.isfinite(out))
        # Ping-pong taps are sparse (near-impulse repeats every time_ms),
        # not a continuous tail — check a wide range for ANY echo energy
        # rather than a narrow fixed window that could fall between taps.
        late_range = out[int(0.25 * SAMPLE_RATE):int(0.9 * SAMPLE_RATE)]
        assert np.max(np.abs(late_range)) > 1e-3


class TestTremolo:
    def test_depth_zero_keeps_amplitude_steady(self):
        tr = TremoloProcessor(SAMPLE_RATE)
        tr.state.depth = 0.0
        tr.state.mix = 1.0
        sig = tone(amp=0.5, seconds=0.5)
        out = process_in_blocks(tr, sig)
        # Envelope of a steady tone through a non-modulating tremolo should
        # itself be steady — check RMS is consistent block-to-block.
        block = SAMPLE_RATE // 20
        levels = [rms(out[i:i + block]) for i in range(0, len(out) - block, block)]
        assert np.std(levels) < 0.05 * np.mean(levels)

    def test_depth_one_produces_amplitude_modulation(self):
        tr = TremoloProcessor(SAMPLE_RATE)
        tr.state.rate = 5.0
        tr.state.depth = 1.0
        tr.state.mix = 1.0
        sig = tone(amp=0.5, seconds=0.5)
        out = process_in_blocks(tr, sig)
        block = SAMPLE_RATE // 20
        levels = [rms(out[i:i + block]) for i in range(0, len(out) - block, block)]
        # Deep modulation at 5 Hz should show clear level variation across
        # 20 measurement windows spanning half a second (~2.5 cycles).
        assert np.std(levels) > 0.1 * np.mean(levels)


class TestAutoPan:
    def test_depth_zero_keeps_channels_balanced(self):
        ap = AutoPanProcessor(SAMPLE_RATE)
        ap.state.depth = 0.0
        ap.state.mix = 1.0
        sig = tone(amp=0.5, seconds=0.3)
        out = process_in_blocks(ap, sig)
        assert rms(out[:, 0]) == pytest.approx(rms(out[:, 1]), rel=0.05)

    def test_depth_one_creates_channel_imbalance_over_time(self):
        ap = AutoPanProcessor(SAMPLE_RATE)
        ap.state.rate = 4.0
        ap.state.depth = 1.0
        ap.state.mix = 1.0
        sig = tone(amp=0.5, seconds=0.5)
        out = process_in_blocks(ap, sig)
        block = SAMPLE_RATE // 20
        diffs = [
            rms(out[i:i + block, 0]) - rms(out[i:i + block, 1])
            for i in range(0, len(out) - block, block)
        ]
        # Some windows should be left-heavy, others right-heavy.
        assert max(diffs) > 0.05
        assert min(diffs) < -0.05


class TestRingMod:
    def test_depth_zero_is_near_passthrough(self):
        rm = RingModProcessor(SAMPLE_RATE)
        rm.state.depth = 0.0
        rm.state.mix = 1.0
        sig = tone(amp=0.5, seconds=0.1)
        out = process_in_blocks(rm, sig.copy())
        np.testing.assert_allclose(out, sig, atol=0.05)

    def test_modulation_alters_the_waveform(self):
        rm = RingModProcessor(SAMPLE_RATE)
        rm.state.freq_hz = 150.0
        rm.state.depth = 1.0
        rm.state.mix = 1.0
        sig = tone(amp=0.5, seconds=0.1)
        out = process_in_blocks(rm, sig.copy())
        assert not np.allclose(out, sig, atol=0.05)


class TestFreqShifter:
    def test_zero_shift_preserves_dominant_frequency(self):
        fs = FreqShifterProcessor(SAMPLE_RATE)
        fs.state.shift_hz = 0.0
        fs.state.mix = 1.0
        sig = tone(amp=0.5, seconds=0.5, freq=440.0)
        out = process_in_blocks(fs, sig.copy())
        spec = np.abs(np.fft.rfft(out[:, 0]))
        freqs = np.fft.rfftfreq(len(out), 1 / SAMPLE_RATE)
        peak_freq = freqs[np.argmax(spec)]
        assert peak_freq == pytest.approx(440.0, abs=10.0)

    def test_nonzero_shift_moves_spectral_peak(self):
        fs = FreqShifterProcessor(SAMPLE_RATE)
        fs.state.shift_hz = 200.0
        fs.state.mix = 1.0
        fs.state.feedback = 0.0
        sig = tone(amp=0.5, seconds=0.5, freq=440.0)
        out = process_in_blocks(fs, sig.copy())
        spec = np.abs(np.fft.rfft(out[:, 0]))
        freqs = np.fft.rfftfreq(len(out), 1 / SAMPLE_RATE)
        peak_freq = freqs[np.argmax(spec)]
        # Single-sideband shifter: peak should move away from 440 Hz,
        # roughly toward 440+200=640 Hz (allow generous tolerance for
        # sideband rejection imperfections).
        assert peak_freq == pytest.approx(640.0, abs=40.0)


class TestStereoWidener:
    def test_zero_width_collapses_toward_mono(self):
        sw = StereoWidenerProcessor(SAMPLE_RATE)
        sw.state.width = 0.0
        # Decorrelated stereo input (different freqs per channel) so there's
        # something to collapse.
        left = tone(amp=0.5, freq=220.0)[:, 0]
        right = tone(amp=0.5, freq=330.0)[:, 0]
        sig = np.stack([left, right], axis=1)
        out = process_in_blocks(sw, sig)
        side = out[:, 0] - out[:, 1]
        assert rms(side) < 0.1 * rms(sig[:, 0] - sig[:, 1])

    def test_wide_setting_increases_channel_difference(self):
        sw_unity = StereoWidenerProcessor(SAMPLE_RATE)
        sw_unity.state.width = 1.0
        sw_wide = StereoWidenerProcessor(SAMPLE_RATE)
        sw_wide.state.width = 2.0

        left = tone(amp=0.5, freq=220.0)[:, 0]
        right = tone(amp=0.5, freq=330.0)[:, 0]
        sig = np.stack([left, right], axis=1)

        out_unity = process_in_blocks(sw_unity, sig.copy())
        out_wide = process_in_blocks(sw_wide, sig.copy())

        side_unity = rms(out_unity[:, 0] - out_unity[:, 1])
        side_wide = rms(out_wide[:, 0] - out_wide[:, 1])
        assert side_wide > side_unity


class TestFXBusModulation:
    def test_process_mod_only_skips_disabled_effects(self):
        bus = FXBus(SAMPLE_RATE)
        state = ModState()  # all mod effects disabled by default
        bus.update_mod(state)
        sig = tone(amp=0.5, seconds=0.1)
        out = process_in_blocks(bus, sig.copy())
        np.testing.assert_allclose(out, sig, atol=1e-5)

    def test_process_mod_only_applies_enabled_chorus(self):
        bus = FXBus(SAMPLE_RATE)
        state = ModState()
        state.chorus_enabled = True
        state.chorus_depth = 1.0
        state.chorus_rate = 2.0
        state.chorus_mix = 1.0
        bus.update_mod(state)
        sig = tone(amp=0.5, seconds=0.2)
        out = bus.process_mod_only(sig.copy()[:512])
        assert not np.allclose(out, sig[:512], atol=1e-4)

    def test_in_rack_flag_suppresses_effect_even_when_enabled(self):
        bus = FXBus(SAMPLE_RATE)
        state = ModState()
        state.phaser_enabled = True
        bus.update_mod(state)
        bus.phaser_in_rack = False  # removed from the rack UI
        sig = tone(amp=0.5, seconds=0.1)
        out = bus.process_mod_only(sig.copy())
        np.testing.assert_allclose(out, sig, atol=1e-5)
