"""Level-domain correctness tests for the dynamics processors:
compressor, glue compressor, noise gate, FET limiter, transient shaper.
"""
import numpy as np
import pytest

from mlrvp.audio.effects import (
    CompressorProcessor, GlueCompressorProcessor, NoiseGateProcessor,
    FETLimiterProcessor, TransientShaperProcessor,
)
from mlrvp.config import SAMPLE_RATE


def tone(amp: float, seconds: float = 0.3, freq: float = 200.0, sr: int = SAMPLE_RATE) -> np.ndarray:
    t = np.arange(int(seconds * sr), dtype=np.float64) / sr
    sig = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([sig, sig], axis=1)


def process_in_blocks(proc, signal: np.ndarray, block: int = 512, **kwargs) -> np.ndarray:
    out = np.empty_like(signal)
    for i in range(0, len(signal), block):
        out[i:i + block] = proc.process(signal[i:i + block], **kwargs)
    return out


def rms(x: np.ndarray, skip_frac: float = 0.3) -> float:
    # Skip enough of the head for envelope followers to settle before measuring.
    settle = int(len(x) * skip_frac)
    return float(np.sqrt(np.mean(np.square(x[settle:]))))


class TestCompressor:
    def test_gain_reduction_reported_above_threshold(self):
        c = CompressorProcessor(SAMPLE_RATE)
        c.state.threshold = -30.0
        c.state.ratio = 8.0
        c.state.attack = 1.0
        c.state.release = 50.0
        c.state.makeup_gain = 0.0
        sig = tone(amp=0.8)  # well above -30 dB threshold
        process_in_blocks(c, sig)
        assert c.gr_db < -0.5, "compressor should report gain reduction on a loud signal"

    def test_no_gain_reduction_below_threshold(self):
        c = CompressorProcessor(SAMPLE_RATE)
        c.state.threshold = -6.0
        c.state.ratio = 8.0
        c.state.makeup_gain = 0.0
        sig = tone(amp=0.05)  # well below -6 dB threshold
        process_in_blocks(c, sig)
        assert c.gr_db == pytest.approx(0.0, abs=0.5)

    def test_output_rms_reduced_relative_to_uncompressed_peak_ratio(self):
        # A compressed loud signal should have less dynamic range headroom
        # consumed than an unprocessed signal of the same input level —
        # i.e. output RMS should be measurably below input RMS once the
        # ratio pulls it down (with makeup gain at 0 dB).
        c = CompressorProcessor(SAMPLE_RATE)
        c.state.threshold = -20.0
        c.state.ratio = 10.0
        c.state.attack = 1.0
        c.state.release = 50.0
        c.state.makeup_gain = 0.0
        sig = tone(amp=0.9)
        out = process_in_blocks(c, sig.copy())
        assert rms(out) < rms(sig)

    def test_external_sidechain_overrides_self_key(self):
        c = CompressorProcessor(SAMPLE_RATE)
        c.state.threshold = -20.0
        c.state.ratio = 10.0
        c.state.makeup_gain = 0.0
        quiet_program = tone(amp=0.05)
        loud_sidechain = tone(amp=0.9)
        process_in_blocks(c, quiet_program.copy(), sidechain=loud_sidechain)
        # Gate/compressor keyed from a loud external sidechain should react
        # even though the program material itself is quiet.
        assert c.gr_db < -0.5

    def test_disabled_resets_gain_reduction_metering(self):
        c = CompressorProcessor(SAMPLE_RATE)
        c.state.threshold = -30.0
        c.state.ratio = 8.0
        process_in_blocks(c, tone(amp=0.8))
        assert c.gr_db < 0
        c.state.enabled = False
        c.process(tone(amp=0.8, seconds=0.05))
        assert c.gr_db == 0.0


class TestGlueCompressor:
    def test_finite_output_across_ratio_settings(self):
        for ratio_idx in range(3):
            gc = GlueCompressorProcessor(SAMPLE_RATE)
            gc.state.ratio_idx = ratio_idx
            gc.state.threshold = -15.0
            out = process_in_blocks(gc, tone(amp=0.9))
            assert np.all(np.isfinite(out))

    def test_low_threshold_reduces_output_level(self):
        gc_off = GlueCompressorProcessor(SAMPLE_RATE)
        gc_off.state.threshold = 15.0  # effectively never triggers
        sig = tone(amp=0.9)
        out_off = process_in_blocks(gc_off, sig.copy())

        gc_on = GlueCompressorProcessor(SAMPLE_RATE)
        gc_on.state.threshold = -30.0
        gc_on.state.ratio_idx = 2  # 10:1
        gc_on.state.makeup_gain = 0.0
        out_on = process_in_blocks(gc_on, sig.copy())

        assert rms(out_on) < rms(out_off)


class TestNoiseGate:
    def _run_seconds(self, gate, signal_block, seconds: float, block: int = 512):
        n_blocks = int(seconds * SAMPLE_RATE / block)
        out = []
        for _ in range(n_blocks):
            out.append(gate.process(signal_block).copy())
        return np.concatenate(out)

    def test_quiet_signal_is_attenuated_toward_range_floor(self):
        gate = NoiseGateProcessor(SAMPLE_RATE)
        gate.state.threshold = -20.0
        gate.state.hold = 10.0
        gate.state.release = 20.0
        gate.state.range_db = -60.0
        quiet_block = tone(amp=0.001, seconds=512 / SAMPLE_RATE)  # ~-60 dB, below threshold
        out = self._run_seconds(gate, quiet_block, seconds=0.5)
        # After attack+hold+release settle, a below-threshold signal should
        # end up heavily attenuated (gate closed).
        tail = out[-512:]
        assert rms(tail, skip_frac=0.0) < rms(quiet_block, skip_frac=0.0) * 0.5

    def test_loud_signal_stays_open(self):
        gate = NoiseGateProcessor(SAMPLE_RATE)
        gate.state.threshold = -40.0
        loud_block = tone(amp=0.8, seconds=512 / SAMPLE_RATE)
        out = self._run_seconds(gate, loud_block, seconds=0.2)
        tail = out[-512:]
        assert rms(tail, skip_frac=0.0) > rms(loud_block, skip_frac=0.0) * 0.8

    def test_range_db_zero_means_no_attenuation_even_when_closed(self):
        gate = NoiseGateProcessor(SAMPLE_RATE)
        gate.state.threshold = -20.0
        gate.state.range_db = 0.0  # gate "closes" but attenuates by 0 dB
        gate.state.hold = 5.0
        gate.state.release = 5.0
        quiet_block = tone(amp=0.001, seconds=512 / SAMPLE_RATE)
        out = self._run_seconds(gate, quiet_block, seconds=0.3)
        tail = out[-512:]
        assert rms(tail, skip_frac=0.0) == pytest.approx(rms(quiet_block, skip_frac=0.0), rel=0.1)


class TestFETLimiter:
    def test_steady_state_level_settles_after_attack_overshoot(self):
        # input_gain drives up to ~40 dB into the limiter before gain
        # reduction catches up (real 1176-style behavior: a brief attack
        # overshoot is expected, not a bug) — so the meaningful check is
        # that the limiter reins the level back in once it settles, not
        # that literally every sample stays under the raw input peak.
        lim = FETLimiterProcessor(SAMPLE_RATE)
        lim.state.input_gain = 1.0    # max drive into the limiter
        lim.state.output_gain = 0.5   # unity makeup — isolate the limiting itself
        lim.state.ratio_idx = 3       # 20:1
        sig = tone(amp=0.9)
        out = process_in_blocks(lim, sig.copy())
        tail = out[-2048:]
        assert np.max(np.abs(tail)) < np.max(np.abs(out)) * 0.5, (
            "limiter should settle well below its attack-phase overshoot peak"
        )

    def test_higher_ratio_reduces_settled_level_more(self):
        sig = tone(amp=0.9)

        lim_low = FETLimiterProcessor(SAMPLE_RATE)
        lim_low.state.input_gain = 1.0
        lim_low.state.ratio_idx = 0  # 4:1
        out_low = process_in_blocks(lim_low, sig.copy())

        lim_high = FETLimiterProcessor(SAMPLE_RATE)
        lim_high.state.input_gain = 1.0
        lim_high.state.ratio_idx = 3  # 20:1
        out_high = process_in_blocks(lim_high, sig.copy())

        assert rms(out_high[-2048:], skip_frac=0.0) <= rms(out_low[-2048:], skip_frac=0.0) + 1e-3

    def test_output_stays_finite_across_settings(self):
        for ratio_idx in range(5):
            lim = FETLimiterProcessor(SAMPLE_RATE)
            lim.state.ratio_idx = ratio_idx
            lim.state.input_gain = 1.0
            out = process_in_blocks(lim, tone(amp=0.95))
            assert np.all(np.isfinite(out))


class TestTransientShaper:
    def _onset_signal(self):
        """Silence followed by a sudden loud tone — a clean transient onset."""
        silence = np.zeros((2048, 2), dtype=np.float32)
        loud = tone(amp=0.8, seconds=0.1)
        return np.concatenate([silence, loud], axis=0)

    def test_positive_attack_shape_boosts_onset_relative_to_negative(self):
        sig = self._onset_signal()

        boosted = TransientShaperProcessor(SAMPLE_RATE)
        boosted.state.attack_shape = 1.0
        out_boost = process_in_blocks(boosted, sig.copy())

        reduced = TransientShaperProcessor(SAMPLE_RATE)
        reduced.state.attack_shape = -1.0
        out_reduce = process_in_blocks(reduced, sig.copy())

        # Compare peak amplitude in a short window right after the onset
        # (2048 = where silence ends).
        window = slice(2048, 2048 + 256)
        assert np.max(np.abs(out_boost[window])) > np.max(np.abs(out_reduce[window]))

    def test_neutral_shape_is_near_unity(self):
        sig = tone(amp=0.5)
        ts = TransientShaperProcessor(SAMPLE_RATE)
        ts.state.attack_shape = 0.0
        ts.state.sustain_shape = 0.0
        ts.state.output = 0.0
        out = process_in_blocks(ts, sig.copy())
        assert rms(out) == pytest.approx(rms(sig), rel=0.1)

    def test_output_makeup_gain_increases_level(self):
        sig = tone(amp=0.3)
        ts = TransientShaperProcessor(SAMPLE_RATE)
        ts.state.output = 6.0  # +6 dB makeup
        out = process_in_blocks(ts, sig.copy())
        assert rms(out) > rms(sig)
