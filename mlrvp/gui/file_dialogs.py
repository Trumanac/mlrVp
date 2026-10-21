"""File dialogs that start in a useful folder and remember where you were.

Qt's static dialogs start in the process's working directory when given
"", which for an installed app is the install folder.  These wrappers
start in the last folder used for that kind of file (persisted in
startup_settings.json), falling back to a sensible default.
"""

import logging
import os
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtWidgets import QFileDialog, QWidget

from mlrvp import startup_settings

log = logging.getLogger("mlrvp")

# Audio formats mlrVp can load (libsndfile: WAV, AIFF, FLAC, OGG, MP3)
AUDIO_EXTENSIONS = frozenset({".wav", ".aiff", ".aif", ".flac", ".ogg", ".mp3"})
AUDIO_FILTER = ("Audio Files (" + " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS))
                + ");;All Files (*)")
PRESET_FILTER = "mlrVp Presets (*.json);;All Files (*)"

_KEY = "last_dirs"


def _music_dir() -> Path:
    from mlrvp.config import _standard_location
    return _standard_location("MusicLocation", Path.home() / "Music")


def _presets_dir() -> Path:
    from mlrvp.config import get_presets_dir
    return get_presets_dir()


# kind -> default folder when nothing has been remembered yet
_DEFAULTS: dict[str, Callable[[], Path]] = {
    "samples": _music_dir,
    "library": _music_dir,
    "presets": _presets_dir,
}


def last_dir(kind: str) -> str:
    """Folder to start a *kind* dialog in."""
    remembered = startup_settings.load().get(_KEY, {})
    path = remembered.get(kind) if isinstance(remembered, dict) else None
    if isinstance(path, str) and os.path.isdir(path):
        return path
    try:
        return str(_DEFAULTS.get(kind, Path.home)())
    except Exception:
        return str(Path.home())


def remember_dir(kind: str, path: str) -> None:
    """Remember the folder of *path* (a file or folder) for *kind* dialogs."""
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if not folder:
        return
    remembered = startup_settings.load().get(_KEY, {})
    if not isinstance(remembered, dict):
        remembered = {}
    remembered[kind] = folder
    try:
        startup_settings.update({_KEY: remembered})
    except OSError as e:
        log.warning("Could not remember last folder: %s", e)


def open_file(parent: Optional[QWidget], title: str, kind: str, file_filter: str) -> str:
    path, _ = QFileDialog.getOpenFileName(parent, title, last_dir(kind), file_filter)
    if path:
        remember_dir(kind, path)
    return path


def save_file(parent: Optional[QWidget], title: str, kind: str, file_filter: str) -> str:
    path, _ = QFileDialog.getSaveFileName(parent, title, last_dir(kind), file_filter)
    if path:
        remember_dir(kind, path)
    return path


def existing_dir(parent: Optional[QWidget], title: str, kind: str) -> str:
    folder = QFileDialog.getExistingDirectory(parent, title, last_dir(kind))
    if folder:
        remember_dir(kind, folder)
    return folder
