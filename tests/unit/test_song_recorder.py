"""SongRecorder: export must match the tape as heard; no takes may be lost."""
import numpy as np
import pytest
import soundfile as sf

import mlrvp.audio.song_recorder as song_mod
from mlrvp.audio.song_recorder import SongRecorder

SR = 44100
BLOCK = 512


def _record(rec, seconds, value):
    for _ in range(int(seconds * SR / BLOCK)):
        rec.process(np.full((BLOCK, 2), value, np.float32))


@pytest.fixture
def rec():
    r = SongRecorder(SR)
    r.auto_level = False
    return r


def _exported(rec, tmp_path, **kw):
    fp = tmp_path / "out.wav"
    assert rec.export(str(fp), **kw)
    data, sr = sf.read(str(fp), dtype="float32", always_2d=True)
    assert sr == SR
    return data


def test_export_after_punch_in_keeps_pre_punch_audio(rec, tmp_path):
    rec.start_recording_now()
    _record(rec, 2.0, 0.1)
    rec.stop()

    rec._pb_fpos = SR * 1.0          # punch in at 1 s
    rec.start_recording_now()
    _record(rec, 1.0, 0.5)
    tape = rec.stop()

    data = _exported(rec, tmp_path, tape_speed=1.0)
    assert len(data) == tape.num_frames
    assert data[0, 0] == pytest.approx(0.1, abs=1e-3)    # pre-punch audio kept
    assert data[-1, 0] == pytest.approx(0.5, abs=1e-3)   # new take after it


def test_export_at_record_speed_restores_original_duration(rec, tmp_path):
    rec.tape_speed = 0.5
    rec.start_recording_now()
    _record(rec, 2.0, 0.1)
    tape = rec.stop()
    assert tape.num_frames == pytest.approx(1.0 * SR, rel=0.01)  # stored at half speed

    # Playing back at the record speed (as the panel does) sounds 2 s long.
    data = _exported(rec, tmp_path, tape_speed=0.5)
    assert len(data) == pytest.approx(2.0 * SR, rel=0.01)


def test_export_during_recording_includes_take_in_progress(rec, tmp_path):
    rec.start_recording_now()
    _record(rec, 1.0, 0.3)
    data = _exported(rec, tmp_path, tape_speed=1.0)
    assert len(data) == pytest.approx(1.0 * SR, rel=0.02)


def test_stop_releases_chunk_memory(rec):
    rec.start_recording_now()
    _record(rec, 0.5, 0.2)
    rec.stop()
    assert rec._chunks == []


def test_duration_cap_take_is_finalised_by_stop(rec, monkeypatch):
    monkeypatch.setattr(song_mod, "MAX_FREE_SECONDS", 0.5)
    rec.start_recording_now()
    _record(rec, 1.0, 0.2)           # runs past the cap
    assert not rec.recording
    assert rec.capped

    tape = rec.stop()
    assert tape is not None
    assert tape.num_frames >= int(0.5 * SR)
    assert not rec.capped


def test_export_with_nothing_recorded_fails(rec, tmp_path):
    assert rec.export(str(tmp_path / "x.wav")) is False


# ── Waveform peaks (polled ~20×/s by the GUI) ────────────────────────────

def _brute_peaks(data, num_bins):
    """Reference: mono |peak| per _ENV_HOP frames, then max-downsampled."""
    mono = data.mean(axis=1)
    hop = song_mod._ENV_HOP
    env = np.array([np.abs(mono[i:i + hop]).max() for i in range(0, len(mono), hop)])
    b = max(1, len(env) // num_bins)
    return env[: (len(env) // b) * b].reshape(-1, b).max(axis=1)


def _noise_take(rec, seconds, seed):
    rng = np.random.default_rng(seed)
    for _ in range(int(seconds * SR / BLOCK)):
        rec.process((rng.standard_normal((BLOCK, 2)) * 0.2).astype(np.float32))


def test_peaks_match_reference_for_finished_tape(rec):
    rec.start_recording_now()
    _noise_take(rec, 1.0, seed=1)
    tape = rec.stop()
    np.testing.assert_allclose(rec.get_peaks(200), _brute_peaks(tape.data, 200), atol=1e-6)


def test_peaks_track_punch_in_take_and_survive_stop(rec):
    rec.start_recording_now()
    _noise_take(rec, 2.0, seed=2)
    rec.stop()
    rec._pb_fpos = song_mod._ENV_HOP * 80   # hop-aligned punch-in point
    rec.start_recording_now()
    _noise_take(rec, 0.5, seed=3)
    live = rec.get_peaks(150)        # mid-take, incremental path
    _noise_take(rec, 0.5, seed=4)
    live2 = rec.get_peaks(150)
    tape_now = rec._tape_data()
    np.testing.assert_allclose(live2, _brute_peaks(tape_now, 150), atol=1e-6)
    assert live is not None

    tape = rec.stop()                # cache seeded from the live envelope
    np.testing.assert_allclose(rec.get_peaks(150), _brute_peaks(tape.data, 150), atol=1e-6)


def test_peaks_are_cheap_on_long_tape(rec):
    import time
    from mlrvp.audio.sample import SampleBuffer
    n = SR * 60 * 10                 # 10-minute tape
    data = np.random.default_rng(5).random((n, 2), dtype=np.float32)
    rec.result = SampleBuffer(data=data, sample_rate=SR, num_frames=n,
                              num_channels=2, duration_sec=n / SR)
    rec.get_peaks(800)               # first call builds the cache
    t = time.perf_counter()
    for _ in range(20):
        rec.get_peaks(800)
    assert (time.perf_counter() - t) / 20 < 0.005   # was ~260 ms per call
