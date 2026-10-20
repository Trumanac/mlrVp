"""A saved audio device that can't be opened must not stop mlrVp starting."""
from types import SimpleNamespace

import pytest

from mlrvp.app import AppController
from mlrvp.config import AppConfig


class _FakeEngine:
    def __init__(self, fail_on_device):
        self.fail_on_device = fail_on_device   # callable(output_device) -> bool
        self.config = None
        self._running = False

    def start(self):
        if self.fail_on_device(self.config.output_device):
            raise RuntimeError("Invalid sample rate")
        self._running = True


def _ctrl(output_device, fail_on_device, window=None):
    config = AppConfig()
    config.output_device = output_device
    engine = _FakeEngine(fail_on_device)
    engine.config = config
    return SimpleNamespace(
        config=config, engine=engine, window=window,
        _audio_device_names={"output": "Scarlett 2i2", "input": ""},
    )


def test_working_device_is_kept():
    ctrl = _ctrl(3, lambda dev: False)
    AppController._start_audio_at_launch(ctrl)
    assert ctrl.engine._running and ctrl.config.output_device == 3


def test_falls_back_to_default_output():
    ctrl = _ctrl(3, lambda dev: dev == 3)
    AppController._start_audio_at_launch(ctrl)
    assert ctrl.engine._running
    assert ctrl.config.output_device == -1


def test_unopenable_audio_starts_with_audio_off():
    ctrl = _ctrl(3, lambda dev: True)
    AppController._start_audio_at_launch(ctrl)   # must not raise
    assert not ctrl.engine._running


@pytest.mark.gui
def test_audio_off_is_explained_without_blocking(qtbot):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from mlrvp.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    ctrl = _ctrl(-1, lambda dev: True, window=window)
    AppController._start_audio_at_launch(ctrl)

    boxes = [w for w in QApplication.topLevelWidgets()
             if isinstance(w, QMessageBox) and w.isVisible()]
    assert len(boxes) == 1 and not boxes[0].isModal()
    assert "Invalid sample rate" in boxes[0].text()
    assert "Audio is off" in window.statusBar().currentMessage()
    boxes[0].close()
    window._allow_close = True
    window.close()
