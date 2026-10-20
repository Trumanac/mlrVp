"""Shared pytest fixtures for the mlrVp test suite.

Two test families live under tests/:

- tests/unit/  — pure-logic tests (DSP, config, preset I/O). No Qt, no
  audio hardware, no real filesystem writes outside a tmp_path.
- tests/gui/   — Qt widget tests driven via pytest-qt's ``qtbot``, using
  the Page-Object wrappers in tests/gui/pages/.

QT_QPA_PLATFORM is forced to "offscreen" before PySide6 is imported
anywhere, so the suite never needs a visible desktop / X server and
never pops up real windows — safe for CI and for local runs alike.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time

import numpy as np
import pytest

from mlrvp.audio.sample import SampleBuffer
from mlrvp.config import SAMPLE_RATE


def _join_new_threads(before: set, timeout: float = 2.0):
    """Wait for any thread started during a test (not present in *before*)
    to finish, then return.

    PresetManager/rack_manager write files on non-daemon background
    threads (by design — a save should complete even if the GUI is
    closing). Nothing ever joined them in the app either, so under a
    fast test suite dozens of these can pile up across the whole
    session, all doing blocking retry/sleep loops against tmp
    directories — a plausible source of rare cross-test timing
    flakiness. Joining at the end of each test that touches preset/rack
    I/O keeps the suite deterministic and mirrors what a clean shutdown
    should do anyway.
    """
    deadline = time.time() + timeout
    for t in threading.enumerate():
        if t not in before and t is not threading.current_thread():
            remaining = max(0.0, deadline - time.time())
            t.join(timeout=remaining)


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect mlrvp.config.get_config_dir() at a scratch directory.

    Every get_*_dir() helper in mlrvp.config (presets, setlists, racks,
    slices, ...) derives from get_config_dir(), so patching this one
    function is enough to keep preset/rack/setlist tests from touching
    the real project data/ folder.
    """
    import mlrvp.config as config_mod

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config_mod, "get_config_dir", lambda: data_dir)
    threads_before = set(threading.enumerate())
    yield data_dir
    # Let any in-flight background save finish (and thus re-read
    # get_config_dir() through the still-active monkeypatch) before the
    # patch is reverted and the tmp directory is torn down.
    _join_new_threads(threads_before)


@pytest.fixture
def app_config():
    """A fresh AppConfig with default values."""
    from mlrvp.config import AppConfig
    return AppConfig()


@pytest.fixture
def preset_manager(tmp_config_dir):
    """A PresetManager backed by a scratch data directory."""
    from mlrvp.preset.manager import PresetManager
    return PresetManager()


def make_sine_sample(freq: float = 440.0, duration_sec: float = 0.5,
                      sample_rate: int = SAMPLE_RATE, num_channels: int = 2,
                      amplitude: float = 0.5) -> SampleBuffer:
    """Build a synthetic sine-wave SampleBuffer for DSP tests.

    Using a known signal (rather than loading a real audio file) keeps
    unit tests fast, deterministic, and independent of test fixtures
    on disk.
    """
    num_frames = int(duration_sec * sample_rate)
    t = np.arange(num_frames, dtype=np.float64) / sample_rate
    tone = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    if num_channels == 1:
        data = tone.reshape(-1, 1)
    else:
        data = np.stack([tone] * num_channels, axis=1)
    return SampleBuffer(
        data=data,
        sample_rate=sample_rate,
        num_frames=num_frames,
        num_channels=num_channels,
        filename="sine_test.wav",
        filepath="",
        duration_sec=duration_sec,
    )


@pytest.fixture
def sine_sample():
    """A 0.5s, 440 Hz, stereo sine-wave SampleBuffer."""
    return make_sine_sample()


@pytest.fixture
def ramp_sample():
    """A short sample whose left-channel samples equal their frame index.

    Makes position/interpolation math trivial to assert on exactly
    (sample N should read back as very close to N), which a sine wave
    can't give you without reimplementing the DSP math in the test.
    """
    num_frames = 1000
    data = np.zeros((num_frames, 2), dtype=np.float32)
    data[:, 0] = np.arange(num_frames, dtype=np.float32)
    data[:, 1] = -np.arange(num_frames, dtype=np.float32)
    return SampleBuffer(
        data=data,
        sample_rate=SAMPLE_RATE,
        num_frames=num_frames,
        num_channels=2,
        filename="ramp_test.wav",
        filepath="",
        duration_sec=num_frames / SAMPLE_RATE,
    )
