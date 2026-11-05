"""First-run setup of mlrVp's data folders.

- Copies data from the pre-1.0.0 location (a data/ folder next to the
  package — inside the app bundle for frozen builds) into the per-user
  folders, once, never overwriting anything already there.
- Seeds the factory racks shipped in mlrvp/resources/factory_racks, once.
"""

import logging
import re
import shutil
from pathlib import Path
from typing import Optional

from mlrvp import config

log = logging.getLogger("mlrvp")

_MIGRATION_MARKER = ".migrated_from_legacy"
_FACTORY_RACKS_MARKER = ".factory_racks_v1"

# Legacy data/ entries that hold settings (copied into the data folder)
_LEGACY_SETTINGS_DIRS = ("presets", "setlists", "racks", "defaults")
_LEGACY_SETTINGS_FILES = ("ui_scale.txt",)


def factory_racks_dir() -> Path:
    return Path(__file__).resolve().parent / "resources" / "factory_racks"


def _copy_missing(src: Path, dest: Path) -> int:
    """Copy files under *src* into *dest*, skipping ones that already exist."""
    copied = 0
    for path in src.rglob("*"):
        if not path.is_file() or path.suffix == ".tmp" or path.name == ".gitkeep":
            continue
        target = dest / path.relative_to(src)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied


def _copy_legacy(legacy: Path) -> int:
    """Copy a legacy data/ folder's contents into the current folders.

    Never overwrites; raises OSError if copying stops part-way.
    """
    data_dir = config.get_config_dir()
    copied = 0
    for name in _LEGACY_SETTINGS_DIRS:
        if (legacy / name).is_dir():
            copied += _copy_missing(legacy / name, data_dir / name)
    for name in _LEGACY_SETTINGS_FILES:
        src, dest = legacy / name, data_dir / name
        if src.is_file() and not dest.exists():
            shutil.copy2(src, dest)
            copied += 1
    if (legacy / "slices").is_dir():
        copied += _copy_missing(legacy / "slices", config.get_slices_dir())
    if (legacy / "songs").is_dir():
        copied += _copy_missing(legacy / "songs", config.get_songs_dir())
    return copied


def migrate_legacy_data() -> int:
    """Copy legacy data/ contents into the per-user folders (first run only).

    Returns the number of files copied.
    """
    if config.uses_local_data_dir():
        return 0
    legacy = config.legacy_data_dir()
    data_dir = config.get_config_dir()
    marker = data_dir / _MIGRATION_MARKER
    if marker.exists() or not legacy.is_dir():
        return 0
    if legacy.resolve() == data_dir.resolve():
        return 0
    try:
        copied = _copy_legacy(legacy)
    except OSError as e:
        # Leave no marker so the next launch retries the remaining files.
        log.error("Data migration from %s stopped early: %s", legacy, e)
        return 0
    marker.write_text(str(legacy), encoding="utf-8")
    if copied:
        log.info("Migrated %d file(s) from %s to %s", copied, legacy, data_dir)
    return copied


def relocate_sample_path(path: str) -> Optional[str]:
    """New location of a sample that lived in an older install's data folder.

    Presets store absolute paths.  Files under a legacy ``data/slices`` or
    ``data/songs`` folder were copied to the current Slices / Songs folders,
    so look for the same relative path there.  Returns None if *path* isn't
    in such a folder or the file isn't at the new location either.
    """
    parts = re.split(r"[\\/]+", path)
    for i in range(len(parts) - 2, 0, -1):
        if parts[i - 1].lower() != "data":
            continue
        kind = parts[i].lower()
        if kind == "slices":
            base = config.get_slices_dir()
        elif kind == "songs":
            base = config.get_songs_dir()
        else:
            continue
        candidate = base.joinpath(*parts[i + 1:])
        if candidate.is_file():
            return str(candidate)
        return None
    return None


def find_legacy_data_dir(folder: Path) -> Optional[Path]:
    """Locate an older install's data/ folder from a folder the user picked.

    Accepts the data folder itself, a source checkout, an extracted
    Windows/Linux release folder (``_internal/data``), or a folder holding
    a macOS ``mlrVp.app`` bundle.
    """
    folder = Path(folder)
    app = "mlrVp.app"
    candidates = [
        folder,
        folder / "data",
        folder / "_internal" / "data",
        folder / "Contents" / "Frameworks" / "data",
        folder / "Contents" / "Resources" / "data",
        folder / app / "Contents" / "Frameworks" / "data",
        folder / app / "Contents" / "Resources" / "data",
    ]
    for c in candidates:
        if c.is_dir() and any((c / n).is_dir() for n in ("presets", "setlists", "racks")):
            return c
    return None


def import_data_from(folder: Path) -> tuple[Optional[Path], int]:
    """Import presets, setlists, racks, settings and audio from an older install.

    Returns (data folder found or None, files copied).  Raises OSError if
    copying fails part-way.
    """
    legacy = find_legacy_data_dir(folder)
    if legacy is None:
        return None, 0
    if legacy.resolve() == config.get_config_dir().resolve():
        return legacy, 0
    copied = _copy_legacy(legacy)
    log.info("Imported %d file(s) from %s", copied, legacy)
    return legacy, copied


def seed_factory_racks() -> int:
    """Copy the shipped factory racks into the racks folder (once)."""
    marker = config.get_defaults_dir() / _FACTORY_RACKS_MARKER
    if marker.exists():
        return 0
    racks_dir = config.get_racks_dir()
    copied = 0
    for src in sorted(factory_racks_dir().glob("*.json")):
        dest = racks_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
            copied += 1
    marker.write_text("", encoding="utf-8")
    return copied


def init_data_dirs() -> None:
    """Run first-run migration and seeding; never raises."""
    try:
        migrate_legacy_data()
    except Exception:
        log.exception("Legacy data migration failed")
    try:
        seed_factory_racks()
    except Exception:
        log.exception("Seeding factory racks failed")
    log.info("Data folder: %s", config.get_config_dir())
