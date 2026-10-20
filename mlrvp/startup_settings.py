"""Small persisted settings file: <data folder>/defaults/startup_settings.json.

Several writers share it (the app's settings worker, the welcome dialog), so
every write is a locked read-modify-write that keeps keys it doesn't touch,
done atomically so a crash mid-write can't truncate the file.
"""

import json
import logging
import threading
from pathlib import Path

from mlrvp.config import get_defaults_dir

log = logging.getLogger("mlrvp")

FILENAME = "startup_settings.json"
_lock = threading.Lock()


def settings_path() -> Path:
    return get_defaults_dir() / FILENAME


def load() -> dict:
    """Return the stored settings ({} if missing or unreadable)."""
    path = settings_path()
    if not path.exists():
        return {}
    try:
        with _lock:
            data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        log.warning("Could not read %s: %s", path, e)
        return {}


def update(values: dict) -> None:
    """Merge *values* into the stored settings and write them atomically.

    Raises OSError if the file can't be written.
    """
    from mlrvp.preset.manager import _atomic_write_json
    path = settings_path()
    with _lock:
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except (OSError, ValueError):
                data = {}
        data.update(values)
        _atomic_write_json(path, data)
