"""Background save failures must reach the user, not just the log."""
import threading
from types import SimpleNamespace

import pytest

from mlrvp.config import AppConfig


def test_preset_manager_reports_failed_writes(preset_manager, monkeypatch):
    import mlrvp.preset.manager as manager_mod

    def _boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(manager_mod, "_atomic_write_json", _boom)

    reported = []
    done = threading.Event()

    def _on_error(what, exc):
        reported.append((what, str(exc)))
        if len(reported) == 2:
            done.set()
    preset_manager.on_write_error = _on_error

    preset_manager.save_preset("Gig", AppConfig())
    preset_manager.save_setlist()
    assert done.wait(3.0)
    whats = sorted(w for w, _ in reported)
    assert whats[0] == "preset 'Gig'" and whats[1].startswith("setlist")
    assert all(e == "disk full" for _, e in reported)


@pytest.mark.gui
def test_failure_is_shown_without_blocking(qtbot):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from mlrvp.app import AppController
    from mlrvp.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    ctrl = SimpleNamespace(window=window)
    AppController._show_save_failure(ctrl, "rack 'Drums'", OSError("Access denied"))

    boxes = [w for w in QApplication.topLevelWidgets()
             if isinstance(w, QMessageBox) and w.isVisible()]
    assert len(boxes) == 1 and not boxes[0].isModal()
    assert "rack 'Drums'" in boxes[0].text() and "Access denied" in boxes[0].text()
    assert "Could not save rack 'Drums'" in window.statusBar().currentMessage()
    boxes[0].close()
