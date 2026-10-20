"""Release builds have no console: everything must land in logs/mlrvp.log."""
import faulthandler
import logging
import sys
import threading

import pytest

import mlrvp.app as app_mod
import mlrvp.config as config


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path / "data"))
    root = logging.getLogger()
    saved = (root.level, list(root.handlers), sys.excepthook, threading.excepthook)
    root.handlers = []
    yield tmp_path / "data" / "logs"
    for h in root.handlers:
        h.close()
    root.setLevel(saved[0])
    root.handlers = saved[1]
    sys.excepthook, threading.excepthook = saved[2], saved[3]
    faulthandler.disable()
    if app_mod._crash_log_file is not None:
        app_mod._crash_log_file.close()
        app_mod._crash_log_file = None


def _log_text(logs_dir):
    for h in logging.getLogger().handlers:
        h.flush()
    return (logs_dir / "mlrvp.log").read_text(encoding="utf-8")


def test_log_file_and_uncaught_exceptions(isolated_logging):
    app_mod._setup_logging()
    logs_dir = isolated_logging
    assert (logs_dir / "crash.log").exists()

    try:
        raise RuntimeError("boom in a Qt slot")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())

    def worker():
        raise ValueError("boom in a worker thread")
    t = threading.Thread(target=worker, name="test-worker")
    t.start()
    t.join()

    text = _log_text(logs_dir)
    assert "mlrVp" in text and "starting" in text
    assert "RuntimeError: boom in a Qt slot" in text
    assert "Uncaught exception in thread test-worker" in text
    assert "ValueError: boom in a worker thread" in text


def test_debug_env_enables_debug_level(isolated_logging, monkeypatch):
    monkeypatch.setenv("MLRVP_DEBUG", "1")
    app_mod._setup_logging()
    assert logging.getLogger().level == logging.DEBUG
