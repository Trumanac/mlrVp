"""PITCH mode's per-semitone cache is skipped for long samples (RAM/CPU)."""
import threading
from types import SimpleNamespace

import numpy as np

import mlrvp.app as app_mod
from mlrvp.app import AppController
from mlrvp.config import AppConfig


def _ctrl(seconds, monkeypatch):
    sr = 1000                       # tiny rate keeps the arrays small
    data = np.zeros((int(seconds * sr), 2), dtype=np.float32)
    ech = SimpleNamespace(
        sample=SimpleNamespace(data=data), sample_rate=sr, _pitch_cache=None,
        _pitch_scale_cache={}, _pitch_scale_cache_gen=0,
        state=SimpleNamespace(pitch_scale=0, pitch_root=0),
    )
    statuses = []
    ctrl = SimpleNamespace(
        engine=SimpleNamespace(channels=[ech]), config=AppConfig(),
        window=SimpleNamespace(set_status=statuses.append),
        _PITCH_SCALE_MAX_SECONDS=AppController._PITCH_SCALE_MAX_SECONDS,
    )
    monkeypatch.setattr(app_mod, "run_on_main_thread", lambda fn: fn())
    started = []
    monkeypatch.setattr(threading, "Thread",
                        lambda *a, **kw: SimpleNamespace(start=lambda: started.append(1)))
    return ctrl, statuses, started


def test_long_sample_skips_the_cache(monkeypatch):
    ctrl, statuses, started = _ctrl(AppController._PITCH_SCALE_MAX_SECONDS + 1, monkeypatch)
    AppController._build_pitch_scale_cache_async(ctrl, 0)
    assert started == []
    assert "speed-based pitch" in statuses[-1]


def test_short_sample_builds_the_cache(monkeypatch):
    ctrl, statuses, started = _ctrl(2, monkeypatch)
    AppController._build_pitch_scale_cache_async(ctrl, 0)
    assert started == [1] and statuses == []
