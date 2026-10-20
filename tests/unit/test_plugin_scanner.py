"""A hung plugin must not stall the plugin scan."""
import threading
import time

import pytest

import mlrvp.audio.plugin_scanner as scanner

pedalboard = pytest.importorskip("pedalboard")


def test_probe_timeout_is_enforced(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(pedalboard, "load_plugin", lambda path: release.wait(5))
    monkeypatch.setattr(scanner, "_PROBE_TIMEOUT", 0.2)
    t0 = time.perf_counter()
    assert scanner._probe_with_pedalboard("hung.vst3", "vst3") is None
    assert time.perf_counter() - t0 < 2.0     # used to wait for the hung load (5 s)
    release.set()


def test_probe_success_and_failure(monkeypatch):
    monkeypatch.setattr(pedalboard, "load_plugin", lambda path: object())
    info = scanner._probe_with_pedalboard("/plugins/Good.vst3", "vst3")
    assert info is not None and info.name == "Good"

    def boom(path):
        raise RuntimeError("not a plugin")
    monkeypatch.setattr(pedalboard, "load_plugin", boom)
    assert scanner._probe_with_pedalboard("/plugins/Bad.vst3", "vst3") is None
