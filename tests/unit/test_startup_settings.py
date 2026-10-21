"""startup_settings.json: merged writes, audio settings round-trip, no lost saves."""
import queue
from types import SimpleNamespace

import pytest

import mlrvp.config as config
from mlrvp import startup_settings
from mlrvp.app import AppController
from mlrvp.config import AppConfig


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path / "data"))
    return tmp_path / "data"


_DEVICES = {
    "output": [
        {"index": 3, "name": "Speakers", "host_api": "MME"},
        {"index": 7, "name": "Focusrite USB ASIO", "host_api": "ASIO"},
    ],
    "input": [{"index": 8, "name": "Focusrite USB ASIO", "host_api": "ASIO"}],
}


def _controller(monkeypatch, devices=_DEVICES):
    monkeypatch.setattr("mlrvp.app.AudioEngine.get_devices", staticmethod(lambda: devices))
    ctrl = SimpleNamespace(
        config=AppConfig(),
        _audio_device_names={"output": "", "input": ""},
        _startup_notices=[],
        _find_audio_device=AppController._find_audio_device,
    )
    return ctrl


def test_update_merges_and_keeps_other_keys():
    startup_settings.update({"welcome_shown": True})
    startup_settings.update({"grid_mode": "mix"})
    assert startup_settings.load() == {"welcome_shown": True, "grid_mode": "mix"}


def test_load_tolerates_corrupt_file():
    startup_settings.settings_path().write_text("{not json", encoding="utf-8")
    assert startup_settings.load() == {}


def test_app_save_keeps_welcome_flag_and_stores_audio(monkeypatch):
    startup_settings.update({"welcome_shown": True})
    ctrl = _controller(monkeypatch)
    ctrl.config.sample_rate, ctrl.config.buffer_size = 48000, 256
    ctrl.config.host_api = "ASIO"
    ctrl.config.latency = "Low"
    ctrl._audio_device_names = {"output": "Focusrite USB ASIO", "input": ""}

    AppController._save_startup_settings(ctrl)

    data = startup_settings.load()
    assert data["welcome_shown"] is True          # was erased before the fix
    assert data["audio"] == {
        "host_api": "ASIO", "output_device": "Focusrite USB ASIO", "input_device": "",
        "sample_rate": 48000, "buffer_size": 256, "multichannel_output": False,
        "latency": "Low",
    }


def test_restore_resolves_devices_by_name(monkeypatch):
    ctrl = _controller(monkeypatch)
    AppController._restore_audio_settings(ctrl, {
        "host_api": "ASIO", "output_device": "Focusrite USB ASIO",
        "input_device": "Focusrite USB ASIO", "sample_rate": 96000, "buffer_size": 1024,
    })
    assert (ctrl.config.output_device, ctrl.config.input_device) == (7, 8)
    assert (ctrl.config.sample_rate, ctrl.config.buffer_size) == (96000, 1024)
    assert ctrl._startup_notices == []


def test_restore_missing_device_falls_back_and_keeps_name(monkeypatch):
    ctrl = _controller(monkeypatch)
    AppController._restore_audio_settings(ctrl, {"output_device": "Unplugged Interface"})
    assert ctrl.config.output_device == -1
    assert ctrl._audio_device_names["output"] == "Unplugged Interface"
    assert "Unplugged Interface" in ctrl._startup_notices[0]


def test_restore_ignores_unsupported_values(monkeypatch):
    ctrl = _controller(monkeypatch)
    AppController._restore_audio_settings(ctrl, {"sample_rate": 12345, "buffer_size": 333,
                                                 "latency": "Ludicrous"})
    assert ctrl.config.sample_rate == config.SAMPLE_RATE
    assert ctrl.config.buffer_size == config.BUFFER_SIZE
    assert ctrl.config.latency == config.DEFAULT_LATENCY


def test_restore_latency(monkeypatch):
    ctrl = _controller(monkeypatch)
    AppController._restore_audio_settings(ctrl, {"latency": "Low"})
    assert ctrl.config.latency == "Low"


def test_worker_saves_pending_request_before_stopping():
    saves = []
    q = queue.SimpleQueue()
    q.put(True)
    q.put(None)       # quit right after a settings change
    ctrl = SimpleNamespace(_settings_save_queue=q,
                           _save_startup_settings=lambda: saves.append(1))
    AppController._settings_save_worker(ctrl)
    assert saves == [1]
