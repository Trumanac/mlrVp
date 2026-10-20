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
