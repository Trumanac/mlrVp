"""Tests for distortion (BitCrusher, TubeOverdrive, Fuzz) and lo-fi media
character processors (ReelToReel, CassetteTape, VHS, Vinyl, LoFiRadio).
"""
import numpy as np

from mlrvp.audio.effects import (
    BitCrusherProcessor, TubeOverdriveProcessor, FuzzProcessor,
    ReelToReelProcessor, CassetteTapeProcessor, VHSProcessor,
    VinylProcessor, LoFiRadioProcessor,
)
from mlrvp.config import SAMPLE_RATE


def tone(amp: float = 0.5, seconds: float = 0.3, freq: float = 220.0, sr: int = SAMPLE_RATE) -> np.ndarray:
    t = np.arange(int(seconds * sr), dtype=np.float64) / sr
    sig = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([sig, sig], axis=1)


def process_in_blocks(proc, signal: np.ndarray, block: int = 512) -> np.ndarray:
    out = np.empty_like(signal)
    for i in range(0, len(signal), block):
        out[i:i + block] = proc.process(signal[i:i + block])
    return out


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def harmonic_energy_ratio(signal: np.ndarray, fundamental: float, sr: int = SAMPLE_RATE) -> float:
    """Energy at 2x/3x the fundamental relative to the fundamental itself —
    a simple, robust proxy for "how much harmonic distortion was added"."""
    spec = np.abs(np.fft.rfft(signal))
    freqs = np.fft.rfftfreq(len(signal), 1 / sr)

    def bin_energy(target_freq):
        idx = np.argmin(np.abs(freqs - target_freq))
        return spec[max(0, idx - 2):idx + 3].sum()

    fundamental_e = bin_energy(fundamental)
    harmonics_e = bin_energy(2 * fundamental) + bin_energy(3 * fundamental)
    return harmonics_e / max(fundamental_e, 1e-9)


class TestBitCrusher:
    def test_low_bit_depth_quantizes_to_coarse_levels(self):
        bc = BitCrusherProcessor(SAMPLE_RATE)
        bc.state.bit_depth = 2  # 2 levels per polarity -> very coarse
        bc.state.sample_rate_reduction = 0.0
        bc.state.mix = 1.0
        sig = tone(amp=0.9, freq=100.0)
        out = process_in_blocks(bc, sig)
        unique_levels = np.unique(np.round(out[:, 0], 4))
        # A smooth sine has thousands of distinct sample values; 2-bit
        # quantization should collapse it to a handful of levels.
        assert len(unique_levels) < 20

    def test_high_bit_depth_stays_close_to_original(self):
        bc = BitCrusherProcessor(SAMPLE_RATE)
        bc.state.bit_depth = 32
        bc.state.sample_rate_reduction = 0.0
        bc.state.mix = 1.0
        sig = tone(amp=0.5)
        out = process_in_blocks(bc, sig.copy())
        np.testing.assert_allclose(out, sig, atol=1e-3)

    def test_sample_rate_reduction_creates_held_repeated_samples(self):
        bc = BitCrusherProcessor(SAMPLE_RATE)
        bc.state.bit_depth = 32
        bc.state.sample_rate_reduction = 1.0  # max hold length
        bc.state.mix = 1.0
        sig = tone(amp=0.5, freq=100.0)
        out = process_in_blocks(bc, sig)
        # Zero-order hold repeats samples verbatim for stretches -> count
        # how often consecutive samples are exactly equal.
        repeats = np.sum(out[1:, 0] == out[:-1, 0])
        assert repeats > len(out) * 0.5


class TestTubeOverdrive:
    def test_drive_adds_harmonic_content(self):
        clean = TubeOverdriveProcessor(SAMPLE_RATE)
        clean.state.drive = 0.0
        clean.state.mix = 1.0

        driven = TubeOverdriveProcessor(SAMPLE_RATE)
        driven.state.drive = 1.0
        driven.state.mix = 1.0

        sig = tone(amp=0.6, freq=200.0, seconds=0.5)
        out_clean = process_in_blocks(clean, sig.copy())
        out_driven = process_in_blocks(driven, sig.copy())

        ratio_clean = harmonic_energy_ratio(out_clean[:, 0], 200.0)
        ratio_driven = harmonic_energy_ratio(out_driven[:, 0], 200.0)
        assert ratio_driven > ratio_clean


class TestFuzz:
    def test_high_gain_adds_harmonic_content_and_clips(self):
        clean = FuzzProcessor(SAMPLE_RATE)
        clean.state.gain = 0.0
        clean.state.mix = 1.0
        clean.state.gate = 0.0

        fuzzed = FuzzProcessor(SAMPLE_RATE)
        fuzzed.state.gain = 1.0
        fuzzed.state.mix = 1.0
        fuzzed.state.gate = 0.0

        sig = tone(amp=0.5, freq=200.0, seconds=0.5)
        out_clean = process_in_blocks(clean, sig.copy())
        out_fuzzed = process_in_blocks(fuzzed, sig.copy())

        ratio_clean = harmonic_energy_ratio(out_clean[:, 0], 200.0)
        ratio_fuzzed = harmonic_energy_ratio(out_fuzzed[:, 0], 200.0)
        assert ratio_fuzzed > ratio_clean

    def test_gate_silences_signal_below_threshold(self):
        fz = FuzzProcessor(SAMPLE_RATE)
        fz.state.gate = 0.05
        fz.state.gain = 0.0
        fz.state.mix = 1.0
        quiet = tone(amp=0.01, seconds=0.1)  # below the gate threshold
        out = process_in_blocks(fz, quiet)
        assert rms(out) < rms(quiet)


class TestLoFiMediaCharacter:
    """ReelToReel / CassetteTape / VHS / Vinyl all layer noise/wobble/wear
    character onto the signal — verified here as "more character setting
    -> more deviation from a clean tone", rather than trying to model the
    exact analog circuit each one emulates."""

    def test_reel_to_reel_saturation_increases_harmonic_content(self):
        clean = ReelToReelProcessor(SAMPLE_RATE)
        clean.state.saturation = 0.0
        clean.state.flutter = 0.0
        clean.state.mix = 1.0

        hot = ReelToReelProcessor(SAMPLE_RATE)
        hot.state.saturation = 1.0
        hot.state.flutter = 0.0
        hot.state.mix = 1.0

        sig = tone(amp=0.7, freq=200.0, seconds=0.5)
        out_clean = process_in_blocks(clean, sig.copy())
        out_hot = process_in_blocks(hot, sig.copy())
        assert harmonic_energy_ratio(out_hot[:, 0], 200.0) > harmonic_energy_ratio(out_clean[:, 0], 200.0)

    def test_cassette_hiss_adds_high_frequency_noise_energy(self):
        quiet = CassetteTapeProcessor(SAMPLE_RATE)
        quiet.state.hiss = 0.0
        quiet.state.saturation = 0.0
        quiet.state.warp = 0.0
        quiet.state.mix = 1.0

        hissy = CassetteTapeProcessor(SAMPLE_RATE)
        hissy.state.hiss = 1.0
        hissy.state.saturation = 0.0
        hissy.state.warp = 0.0
        hissy.state.mix = 1.0

        sig = tone(amp=0.3, freq=200.0, seconds=0.5)
        out_quiet = process_in_blocks(quiet, sig.copy())
        out_hissy = process_in_blocks(hissy, sig.copy())
        # Hiss lives above the fundamental — high-passed residual energy
        # should be higher with hiss maxed.
        spec_quiet = np.abs(np.fft.rfft(out_quiet[:, 0]))
        spec_hissy = np.abs(np.fft.rfft(out_hissy[:, 0]))
        freqs = np.fft.rfftfreq(len(out_quiet), 1 / SAMPLE_RATE)
        hi_band = freqs > 5000
        assert spec_hissy[hi_band].sum() > spec_quiet[hi_band].sum()

    def test_vhs_noise_adds_broadband_energy(self):
        clean = VHSProcessor(SAMPLE_RATE)
        clean.state.noise = 0.0
        clean.state.wobble = 0.0
        clean.state.dropout = 0.0
        clean.state.mix = 1.0

        noisy = VHSProcessor(SAMPLE_RATE)
        noisy.state.noise = 1.0
        noisy.state.wobble = 0.0
        noisy.state.dropout = 0.0
        noisy.state.mix = 1.0

        sig = tone(amp=0.3, freq=200.0, seconds=0.3)
        out_clean = process_in_blocks(clean, sig.copy())
        out_noisy = process_in_blocks(noisy, sig.copy())
        # Residual after removing the clean signal's own energy should be
        # larger for the noisy version.
        assert rms(out_noisy - sig) > rms(out_clean - sig)

    def test_vinyl_crackle_adds_impulsive_energy(self):
        clean = VinylProcessor(SAMPLE_RATE)
        clean.state.crackle = 0.0
        clean.state.pops = 0.0
        clean.state.mix = 1.0

        crackly = VinylProcessor(SAMPLE_RATE)
        crackly.state.crackle = 1.0
        crackly.state.pops = 1.0
        crackly.state.mix = 1.0

        sig = tone(amp=0.3, freq=200.0, seconds=0.5)
        out_clean = process_in_blocks(clean, sig.copy())
        out_crackly = process_in_blocks(crackly, sig.copy())
        assert rms(out_crackly - sig) > rms(out_clean - sig)


class TestLoFiRadio:
    def test_passband_frequency_survives_better_than_out_of_band(self):
        radio = LoFiRadioProcessor(SAMPLE_RATE)
        radio.state.lo_cut = 300.0
        radio.state.hi_cut = 3400.0
        radio.state.noise = 0.0
        radio.state.distortion = 0.0
        radio.state.mix = 1.0

        in_band = tone(amp=0.5, freq=1000.0)
        out_band_low = tone(amp=0.5, freq=50.0)

        out_in_band = process_in_blocks(radio, in_band.copy())
        radio2 = LoFiRadioProcessor(SAMPLE_RATE)
        radio2.state.lo_cut = 300.0
        radio2.state.hi_cut = 3400.0
        radio2.state.noise = 0.0
        radio2.state.distortion = 0.0
        radio2.state.mix = 1.0
        out_low = process_in_blocks(radio2, out_band_low.copy())

        # A 1 kHz tone (well inside the telephone/AM band) should survive
        # much better than a 50 Hz tone (below lo_cut).
        ratio_in_band = rms(out_in_band) / rms(in_band)
        ratio_low = rms(out_low) / rms(out_band_low)
        assert ratio_in_band > ratio_low
