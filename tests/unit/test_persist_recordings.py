"""Recordings that exist only in memory are written to disk when a preset is saved."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from mlrvp.app import AppController
from mlrvp.audio.sample import SampleBuffer
from mlrvp.config import AppConfig


def _buf(filename=""):
    data = np.full((100, 2), 0.25, dtype=np.float32)
    return SampleBuffer(data=data, sample_rate=44100, num_channels=2, num_frames=100,
                        duration_sec=100 / 44100, filename=filename)


def test_in_memory_rows_are_saved_and_repointed(tmp_config_dir, tmp_path):
    loaded = tmp_path / "kick.wav"
    sf.write(str(loaded), np.zeros((10, 2), dtype=np.float32), 44100)
    old = tmp_path / "old.wav"
    sf.write(str(old), np.zeros((10, 2), dtype=np.float32), 44100)

    cfg = AppConfig()
    cfg.row_count = 4
    cfg.channels[0].filename = str(loaded)         # plays its file
    cfg.channels[1].filename = str(old)            # SAMP replaced the audio
    cfg.channels[2].filename = "<recorded 0.00s>"  # studio-recorder take
    samples = [_buf("kick.wav"), _buf(), _buf("recording"), None]
    ctrl = SimpleNamespace(
        config=cfg,
        engine=SimpleNamespace(channels=[SimpleNamespace(sample=s) for s in samples]),
        _report_save_failure=lambda *a: None,
    )

    assert AppController._persist_recorded_samples(ctrl, "Live/Set 1") == 2
    assert cfg.channels[0].filename == str(loaded)
    assert cfg.channels[3].filename == ""
    for row in (1, 2):
        out = Path(cfg.channels[row].filename)
        assert out.parent.name == "Recordings" and out.name.startswith("Live_Set 1_row0")
        data, sr = sf.read(str(out), dtype="float32")
        assert sr == 44100 and np.allclose(data, 0.25)
    # Saving again doesn't write the same takes twice
    assert AppController._persist_recorded_samples(ctrl, "Live/Set 1") == 0
