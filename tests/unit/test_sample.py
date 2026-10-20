"""Tests for mlrvp.audio.sample — SampleBuffer, file loading, waveform overview."""
import numpy as np
import soundfile as sf

from mlrvp.audio.sample import (
    SampleBuffer, load_sample, create_empty_buffer, get_waveform_overview,
)
from mlrvp.config import SAMPLE_RATE


class TestCreateEmptyBuffer:
    def test_shape_and_duration(self):
        buf = create_empty_buffer(duration_sec=2.0, num_channels=2, sample_rate=48000)
        assert buf.num_frames == 96000
        assert buf.num_channels == 2
        assert buf.data.shape == (96000, 2)
        assert buf.sample_rate == 48000

    def test_data_is_silence(self):
        buf = create_empty_buffer(duration_sec=0.1)
        assert np.all(buf.data == 0.0)

    def test_duration_samples_property(self):
        buf = create_empty_buffer(duration_sec=1.0, sample_rate=44100)
        assert buf.duration_samples == 44100


class TestLoadSample:
    def _write_wav(self, tmp_path, freq=440.0, duration=0.2, sr=44100, channels=2):
        path = tmp_path / "tone.wav"
        t = np.arange(int(duration * sr)) / sr
        tone = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        data = np.stack([tone] * channels, axis=1) if channels > 1 else tone.reshape(-1, 1)
        sf.write(str(path), data, sr)
        return path

    def test_loads_existing_wav(self, tmp_path):
        path = self._write_wav(tmp_path)
        buf = load_sample(str(path))
        assert buf is not None
        assert buf.num_channels == 2
        assert buf.sample_rate == SAMPLE_RATE
        assert buf.filename == "tone.wav"
        assert buf.num_frames > 0

    def test_missing_file_returns_none(self, tmp_path):
        assert load_sample(str(tmp_path / "nope.wav")) is None

    def test_resamples_to_target_rate(self, tmp_path):
        path = self._write_wav(tmp_path, sr=22050, duration=0.1)
        buf = load_sample(str(path), target_sr=44100)
        assert buf is not None
        assert buf.sample_rate == 44100
        # ~0.1s at 44100 should be close to 4410 frames (resampling isn't exact)
        assert abs(buf.num_frames - 4410) < 100

    def test_mono_file_reports_one_channel(self, tmp_path):
        path = self._write_wav(tmp_path, channels=1)
        buf = load_sample(str(path))
        assert buf.num_channels == 1

    def test_rejects_oversized_file(self, tmp_path, monkeypatch):
        path = self._write_wav(tmp_path, duration=0.05)
        # Simulate a file that's reported as absurdly large without
        # actually allocating 500MB+ on disk for the test.
        import mlrvp.audio.sample as sample_mod
        real_stat = sample_mod.Path.stat

        def fake_stat(self):
            result = real_stat(self)
            if self.name == "tone.wav":
                class _Big:
                    st_size = 999 * 1024 * 1024
                return _Big()
            return result

        monkeypatch.setattr(sample_mod.Path, "stat", fake_stat)
        assert load_sample(str(path)) is None

    def test_empty_file_returns_none(self, tmp_path):
        path = tmp_path / "empty.wav"
        sf.write(str(path), np.zeros((0, 2), dtype=np.float32), 44100)
        assert load_sample(str(path)) is None


class TestWaveformOverview:
    def test_output_shape(self):
        buf = create_empty_buffer(duration_sec=1.0)
        overview = get_waveform_overview(buf, num_points=256)
        assert overview.shape == (256, 2)

    def test_short_sample_is_padded_not_crashing(self):
        data = np.ones((10, 2), dtype=np.float32)
        buf = SampleBuffer(data=data, sample_rate=44100, num_frames=10,
                            num_channels=2, filename="short.wav")
        overview = get_waveform_overview(buf, num_points=512)
        assert overview.shape == (512, 2)

    def test_min_max_bracket_a_known_sine(self):
        sr = 44100
        t = np.arange(sr) / sr
        tone = np.sin(2 * np.pi * 100 * t).astype(np.float32)  # amplitude 1.0
        data = np.stack([tone, tone], axis=1)
        buf = SampleBuffer(data=data, sample_rate=sr, num_frames=sr, num_channels=2, filename="s")
        overview = get_waveform_overview(buf, num_points=64)
        # A full-amplitude sine over many periods should get close to +/-1
        # in at least some segments.
        assert overview[:, 0].min() < -0.9
        assert overview[:, 1].max() > 0.9
        # min <= max in every segment
        assert np.all(overview[:, 0] <= overview[:, 1])
