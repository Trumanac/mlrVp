"""File > Import Data from Previous Version."""
import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

import mlrvp.config as config
from mlrvp.gui.main_window import MainWindow


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path / "userdata"))
    w = MainWindow()
    qtbot.addWidget(w)
    yield w
    w._allow_close = True   # closeEvent otherwise defers to the app controller
    w.close()


@pytest.fixture
def dialogs(monkeypatch):
    """Record message boxes instead of showing them; answer Yes to questions."""
    shown = []

    def _record(kind, answer=None):
        def _fn(parent, title, text, *a, **k):
            shown.append((kind, text))
            return answer
        return _fn

    monkeypatch.setattr(QMessageBox, "information", _record("info"))
    monkeypatch.setattr(QMessageBox, "warning", _record("warning"))
    monkeypatch.setattr(QMessageBox, "question",
                        _record("question", QMessageBox.StandardButton.Yes))
    return shown


def test_import_copies_and_offers_restart(window, dialogs, tmp_path, monkeypatch):
    old = tmp_path / "mlrVp-old" / "_internal" / "data" / "presets"
    old.mkdir(parents=True)
    (old / "Gig.json").write_text("{}")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: str(tmp_path / "mlrVp-old"))
    restarts = []
    window.restart_requested.connect(lambda: restarts.append(True))

    window._on_import_data()

    assert (config.get_presets_dir() / "Gig.json").exists()
    assert dialogs[0][0] == "question" and "Imported 1 file" in dialogs[0][1]
    assert restarts == [True]


def test_import_from_wrong_folder_explains(window, dialogs, tmp_path, monkeypatch):
    (tmp_path / "nothing").mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: str(tmp_path / "nothing"))
    window._on_import_data()
    assert dialogs[0][0] == "info" and "No mlrVp data" in dialogs[0][1]


def test_import_cancelled_does_nothing(window, dialogs, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    window._on_import_data()
    assert dialogs == []
