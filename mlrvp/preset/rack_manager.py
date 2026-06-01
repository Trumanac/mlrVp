"""Rack profile (RACKS) save/load — persists FX rack layout + parameters.

A rack profile captures one rack tab's worth of state:
  - Ordered list of effect modules (by ``effect_type`` string).
  - Each module's parameter dict (from the AppConfig dataclass).

Profiles live in ``data/racks/<name>.json``.  They are independent of
presets — loading a rack profile only modifies one bus, not the whole
config.
"""

from __future__ import annotations

import json
from pathlib import Path

from mlrvp.config import get_racks_dir
from mlrvp.preset.manager import _atomic_write_json, _sanitize_name, _SafeFloatEncoder


CURRENT_RACK_VERSION = "1.0.0"


def list_racks() -> list[str]:
    """Return sorted list of available rack profile names (file stems)."""
    d = get_racks_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json") if p.is_file())


def save_rack(name: str, modules: list[dict]) -> Path:
    """Save a rack profile.

    ``modules`` is a list of ``{"effect_type": str, "state": dict}`` entries
    in rack order.  Returns the written file path.
    """
    safe = _sanitize_name(name)
    fp = get_racks_dir() / f"{safe}.json"
    payload = {
        "version": CURRENT_RACK_VERSION,
        "name": name,
        "modules": modules,
    }
    _atomic_write_json(fp, payload, cls=_SafeFloatEncoder)
    return fp


def load_rack(name: str) -> list[dict]:
    """Load a rack profile by name.  Returns the ``modules`` list, or [] if missing/invalid."""
    safe = _sanitize_name(name)
    fp = get_racks_dir() / f"{safe}.json"
    if not fp.exists():
        return []
    try:
        with open(fp, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    mods = data.get("modules", [])
    if not isinstance(mods, list):
        return []
    # Sanitise entries
    out: list[dict] = []
    for entry in mods:
        if not isinstance(entry, dict):
            continue
        et = entry.get("effect_type")
        if not isinstance(et, str) or not et:
            continue
        state = entry.get("state", {})
        if not isinstance(state, dict):
            state = {}
        out.append({"effect_type": et, "state": state})
    return out


def delete_rack(name: str) -> bool:
    """Delete a rack profile.  Returns True if removed."""
    safe = _sanitize_name(name)
    fp = get_racks_dir() / f"{safe}.json"
    try:
        fp.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def rename_rack(old_name: str, new_name: str) -> bool:
    """Rename a rack profile.  Returns True on success.

    Updates the on-disk file name AND the ``"name"`` field inside the
    JSON payload so the displayed name matches.  Refuses to overwrite
    an existing profile.
    """
    old_safe = _sanitize_name(old_name)
    new_safe = _sanitize_name(new_name)
    if not new_safe:
        return False
    if old_safe == new_safe:
        return False
    d = get_racks_dir()
    src = d / f"{old_safe}.json"
    dst = d / f"{new_safe}.json"
    if not src.exists() or dst.exists():
        return False
    try:
        with open(src, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    data["name"] = new_name
    try:
        _atomic_write_json(dst, data, cls=_SafeFloatEncoder)
    except Exception:
        return False
    try:
        src.unlink()
    except OSError:
        # New file is written; orphan the old one rather than failing.
        pass
    return True
