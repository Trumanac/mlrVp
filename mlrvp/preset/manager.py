"""Preset save/load manager for mlrVp.

Handles full state serialization including channel settings,
mixer state, effect parameters, MIDI mappings, and setlist.
"""

import json
import logging
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional


class _SafeFloatEncoder(json.JSONEncoder):
    """JSON encoder that replaces NaN / Infinity with 0.0.

    Prevents ``json.dump`` from crashing or producing non-standard output
    if any config value somehow becomes NaN (e.g. filter feedback blowup).
    """

    def default(self, o):
        return super().default(o)

    def encode(self, o):
        return super().encode(self._sanitize(o))

    def _sanitize(self, obj):
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return 0.0
            return obj
        if isinstance(obj, dict):
            return {k: self._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._sanitize(v) for v in obj]
        return obj

from mlrvp.config import (
    AppConfig,
    PlaybackMode, QuantizeMode,
    MONOME_BACKENDS, MONOME_BACKEND_AUTO,
    get_presets_dir, get_setlists_dir, NUM_SETLIST_SLOTS, NUM_GROUPS, TEMPO_MIN, TEMPO_MAX,
)

log = logging.getLogger("mlrvp")


def _safe_setattr(obj, key, value):
    """setattr wrapped in try/except so a bad preset value won't crash."""
    try:
        setattr(obj, key, value)
    except (ValueError, TypeError, AttributeError) as exc:
        log.warning("Preset load: skipping bad value %s=%r (%s)", key, value, exc)


def _sanitize_name(name: str) -> str:
    """Convert a display name to a safe directory/file name."""
    s = re.sub(r'[<>:"/\\|?*]', '_', name.strip())
    return s or "untitled"


def _atomic_write_json(fp: Path, data: dict, cls=None):
    """Write JSON atomically via temp-file-then-rename.

    Writes to a temporary file in the same directory, then uses
    ``os.replace`` (atomic on the same filesystem) so a crash
    mid-write can never leave a truncated target file.

    On Windows, os.replace can fail with WinError 5 (Access Denied)
    when antivirus or another process briefly holds the target file
    open.  We retry a few times with a short sleep before giving up.
    """
    import time
    dir_ = fp.parent
    fd, tmp = tempfile.mkstemp(dir=str(dir_), suffix='.tmp')
    fd_open = False
    try:
        with os.fdopen(fd, 'w') as f:
            fd_open = True
            json.dump(data, f, indent=2, cls=cls)
        last_err = None
        for attempt in range(5):
            try:
                os.replace(tmp, fp)
                return
            except PermissionError as e:
                last_err = e
                time.sleep(0.05 * (attempt + 1))
        raise last_err
    except BaseException:
        if not fd_open:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------- #
#  Preset schema version + migrations                                    #
# ---------------------------------------------------------------------- #
# Bump CURRENT_PRESET_VERSION whenever the on-disk preset format changes
# in a way that requires a transformation (renamed key, removed key,
# split/merged value, etc.).  Just adding a new field with a safe default
# does NOT need a bump — apply_preset already falls back to current config
# values for missing keys.
#
# Migration functions take and return a preset dict ({"version": ...,
# "config": {...}, ...}) and may mutate in place.  Register them in
# _MIGRATIONS keyed by the *source* version they upgrade FROM.
CURRENT_PRESET_VERSION = "4.0.0"


def _parse_version(v) -> tuple:
    """Parse 'X.Y.Z' into (X, Y, Z) tuple. Unknown/garbage -> (0, 0, 0)."""
    if not isinstance(v, str):
        return (0, 0, 0)
    parts = v.split(".")
    out = []
    for p in parts[:3]:
        try:
            out.append(int(p))
        except (ValueError, TypeError, OverflowError):
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


# Map of source_version -> migration callable.
# Add entries here when bumping CURRENT_PRESET_VERSION.
_MIGRATIONS: dict = {}


def migrate_preset(data: dict) -> dict:
    """Upgrade preset data from any older schema to CURRENT_PRESET_VERSION.

    - Missing version is treated as legacy (0.0.0) and run through every
      migration in order.
    - Newer-than-current is logged and returned as-is (best-effort load).
    - Mutates and returns the same dict.
    """
    src = data.get("version", "0.0.0")
    src_tup = _parse_version(src)
    cur_tup = _parse_version(CURRENT_PRESET_VERSION)

    if src_tup > cur_tup:
        log.warning(
            "Preset schema %s is newer than supported %s — loading best-effort",
            src, CURRENT_PRESET_VERSION,
        )
        return data

    if src_tup == cur_tup:
        return data

    applied = []
    for from_ver in sorted(_MIGRATIONS, key=_parse_version):
        if _parse_version(from_ver) >= src_tup:
            try:
                data = _MIGRATIONS[from_ver](data) or data
                applied.append(from_ver)
            except Exception as exc:
                log.warning("Preset migration from %s failed: %s", from_ver, exc)

    data["version"] = CURRENT_PRESET_VERSION
    if applied:
        log.info("Migrated preset %s -> %s (steps: %s)",
                 src, CURRENT_PRESET_VERSION, ", ".join(applied))
    return data


class PresetManager:
    """Manages a global preset library and ordered setlists.

    Presets are standalone JSON files in data/presets/ (global pool).
    Setlists are ordered lists of preset names in data/setlists/.
    """

    def __init__(self):
        self.setlist_name: str = "Default"
        self.setlist_presets: list[str] = []   # ordered preset names in current setlist
        self.active_index: int = -1            # selected preset in setlist (-1 = none)

        # Cached list of setlist names — avoids re-scanning disk on every
        # UI refresh, which can stall the main thread on slow storage (HDD).
        self._setlist_names_cache: Optional[list[str]] = None

        # Serialize file writes so background saves don't race.
        self._io_lock = threading.Lock()

        # Called as on_write_error(what, exc) when a background write fails —
        # from the writer thread, so the callee must marshal to the GUI.
        self.on_write_error: Optional[Callable[[str, Exception], None]] = None

        self._ensure_dirs()
        self.load_setlist("Default")

    def _report_write_error(self, what: str, error: Exception) -> None:
        cb = self.on_write_error
        if cb is not None:
            try:
                cb(what, error)
            except Exception:
                log.exception("on_write_error callback failed")

    def _ensure_dirs(self):
        """Create preset library and setlist directories if missing."""
        get_presets_dir().mkdir(parents=True, exist_ok=True)
        get_setlists_dir().mkdir(parents=True, exist_ok=True)
        fp = get_setlists_dir() / "Default.json"
        if not fp.exists():
            with open(fp, 'w', encoding="utf-8") as f:
                json.dump({"name": "Default", "presets": []}, f, indent=2)

    # ------------------------------------------------------------------ #
    #  Preset library (global pool)                                       #
    # ------------------------------------------------------------------ #

    def list_presets(self) -> list[str]:
        """Return names of all presets in the global library."""
        result = []
        for fp in sorted(get_presets_dir().glob("*.json")):
            try:
                with open(fp, encoding="utf-8") as f:
                    data = json.load(f)
                result.append(data.get("name", fp.stem))
            except Exception:
                result.append(fp.stem)
        return result

    def save_preset(self, name: str, config: AppConfig,
                    midi_mappings: Optional[list] = None,
                    tilt_mappings: Optional[list] = None) -> None:
        """Save current state to the preset library under the given name.

        The actual file write happens on a background thread so slow
        storage doesn't block the main thread during playback.
        Errors are logged but not propagated.
        """
        fp = get_presets_dir() / f"{_sanitize_name(name)}.json"
        data = {
            "version": CURRENT_PRESET_VERSION,
            "name": name,
            "config": self._serialize_config(config),
            "midi_mappings": midi_mappings or [],
            "tilt_mappings": tilt_mappings or [],
        }

        def _write():
            try:
                with self._io_lock:
                    _atomic_write_json(fp, data, cls=_SafeFloatEncoder)
            except Exception as e:
                log.error("Preset save error: %s", e)
                self._report_write_error(f"preset '{name}'", e)

        threading.Thread(target=_write, daemon=False).start()

    def load_preset(self, name: str) -> Optional[dict]:
        """Load a preset from the library by name.

        Old-schema files are migrated in-memory before being returned.
        """
        fp = get_presets_dir() / f"{_sanitize_name(name)}.json"
        if not fp.exists():
            return None
        try:
            with self._io_lock:
                with open(fp, encoding="utf-8") as f:
                    data = json.load(f)
            return migrate_preset(data)
        except Exception as e:
            log.error("Preset load error: %s", e)
            return None

    def delete_preset(self, name: str):
        """Remove a preset from the library."""
        fp = get_presets_dir() / f"{_sanitize_name(name)}.json"
        with self._io_lock:
            if fp.exists():
                fp.unlink()

    def rename_preset(self, old_name: str, new_name: str) -> bool:
        """Rename a preset in the library.

        Updates the preset's internal name and its references in *every*
        setlist, not just the current one.
        """
        old_fp = get_presets_dir() / f"{_sanitize_name(old_name)}.json"
        new_fp = get_presets_dir() / f"{_sanitize_name(new_name)}.json"
        if not old_fp.exists():
            return False
        # A case-only rename ("live set" -> "Live Set") resolves to the same
        # file on case-insensitive filesystems (Windows, macOS default).
        same_file = new_fp.exists() and new_fp.samefile(old_fp)
        if new_fp.exists() and not same_file:
            return False
        try:
            with self._io_lock:
                with open(old_fp, encoding="utf-8") as f:
                    data = json.load(f)
                data["name"] = new_name
                if same_file:
                    _atomic_write_json(old_fp, data, cls=_SafeFloatEncoder)
                    os.replace(old_fp, new_fp)  # applies the new filename case
                else:
                    _atomic_write_json(new_fp, data, cls=_SafeFloatEncoder)
                    old_fp.unlink()
                self._rename_in_other_setlists(old_name, new_name)
                for i, p in enumerate(self.setlist_presets):
                    if p == old_name:
                        self.setlist_presets[i] = new_name
            self.save_setlist()
            return True
        except Exception as e:
            log.error("Rename preset %r -> %r failed: %s", old_name, new_name, e)
            return False

    def _rename_in_other_setlists(self, old_name: str, new_name: str) -> None:
        """Rewrite preset references in setlist files other than the current one.

        Caller holds _io_lock.  The current setlist is saved separately from
        its in-memory list.
        """
        current_fp = get_setlists_dir() / f"{_sanitize_name(self.setlist_name)}.json"
        for fp in get_setlists_dir().glob("*.json"):
            if fp == current_fp:
                continue
            try:
                with open(fp, encoding="utf-8") as f:
                    sl = json.load(f)
                presets = sl.get("presets", [])
                if old_name not in presets:
                    continue
                sl["presets"] = [new_name if p == old_name else p for p in presets]
                _atomic_write_json(fp, sl)
            except Exception as e:
                log.warning("Could not update preset name in setlist %s: %s", fp.name, e)

    def preset_exists(self, name: str) -> bool:
        """Check if a preset exists in the library."""
        return (get_presets_dir() / f"{_sanitize_name(name)}.json").exists()

    # ------------------------------------------------------------------ #
    #  Setlist operations                                                 #
    # ------------------------------------------------------------------ #

    def list_setlists(self) -> list[str]:
        """Return names of all setlists on disk (cached)."""
        if self._setlist_names_cache is not None:
            return list(self._setlist_names_cache)
        result = []
        for fp in sorted(get_setlists_dir().glob("*.json")):
            try:
                with open(fp, encoding="utf-8") as f:
                    data = json.load(f)
                result.append(data.get("name", fp.stem))
            except Exception:
                result.append(fp.stem)
        result = result or ["Default"]
        # Hide the system Default setlist from user-facing lists
        result = [n for n in result if n != "Default"]
        self._setlist_names_cache = result
        return list(result)

    def _invalidate_setlist_cache(self):
        """Clear the cached setlist names so the next list_setlists() re-reads."""
        self._setlist_names_cache = None

    def load_setlist(self, name: str) -> bool:
        """Load a setlist by name."""
        fp = get_setlists_dir() / f"{_sanitize_name(name)}.json"
        if not fp.exists():
            return False
        try:
            with self._io_lock:
                with open(fp, encoding="utf-8") as f:
                    data = json.load(f)
                self.setlist_name = data.get("name", name)
                self.setlist_presets = data.get("presets", [])
                self.active_index = -1
            return True
        except Exception as e:
            log.error("Setlist load error: %s", e)
            return False

    def save_setlist(self):
        """Persist the current setlist on a background thread."""
        self._invalidate_setlist_cache()
        # Snapshot values on the calling thread to avoid reading live state
        # from the background thread (race with concurrent modifications).
        _name = self.setlist_name
        _presets = list(self.setlist_presets)

        def _write():
            try:
                with self._io_lock:
                    fp = get_setlists_dir() / f"{_sanitize_name(_name)}.json"
                    data = {"name": _name, "presets": _presets}
                    _atomic_write_json(fp, data)
            except Exception as e:
                log.error("Setlist save error: %s", e)
                self._report_write_error(f"setlist '{_name}'", e)

        threading.Thread(target=_write, daemon=False).start()

    def create_setlist(self, name: str):
        """Create a new empty setlist (synchronous so it's safe to load immediately)."""
        fp = get_setlists_dir() / f"{_sanitize_name(name)}.json"
        data = {"name": name, "presets": []}
        self._invalidate_setlist_cache()
        try:
            with self._io_lock:
                _atomic_write_json(fp, data)
        except Exception as e:
            log.error("Create setlist error: %s", e)
            self._report_write_error(f"setlist '{name}'", e)

    def rename_setlist(self, old_name: str, new_name: str):
        """Rename a setlist (background I/O)."""
        old_fp = get_setlists_dir() / f"{_sanitize_name(old_name)}.json"
        new_fp = get_setlists_dir() / f"{_sanitize_name(new_name)}.json"
        if old_fp.exists() and not new_fp.exists():
            self._invalidate_setlist_cache()
            # Update in-memory name on the main thread before bg I/O
            if self.setlist_name == old_name:
                self.setlist_name = new_name

            def _io():
                try:
                    with self._io_lock:
                        with open(old_fp, encoding="utf-8") as f:
                            data = json.load(f)
                        data["name"] = new_name
                        _atomic_write_json(new_fp, data)
                        old_fp.unlink()
                except Exception as e:
                    log.error("Rename setlist I/O failed: %s", e)
                    self._report_write_error(f"setlist '{new_name}'", e)
                    # Revert optimistic in-memory update since disk write failed
                    if self.setlist_name == new_name:
                        self.setlist_name = old_name

            threading.Thread(target=_io, daemon=False).start()

    def delete_setlist(self, name: str):
        """Delete a setlist (does not delete the presets it references)."""
        fp = get_setlists_dir() / f"{_sanitize_name(name)}.json"
        self._invalidate_setlist_cache()
        with self._io_lock:
            if fp.exists():
                fp.unlink()
        if self.setlist_name == name:
            self._ensure_dirs()
            self.load_setlist("Default")

    # ------------------------------------------------------------------ #
    #  Setlist preset management                                          #
    # ------------------------------------------------------------------ #

    def add_to_setlist(self, preset_name: str, index: int = -1) -> int:
        """Add a preset reference to the current setlist. Returns index.

        Returns -1 if the list is full or the preset is already present.
        """
        if len(self.setlist_presets) >= NUM_SETLIST_SLOTS:
            return -1
        if preset_name in self.setlist_presets:
            return self.setlist_presets.index(preset_name)
        if index < 0 or index > len(self.setlist_presets):
            self.setlist_presets.append(preset_name)
            idx = len(self.setlist_presets) - 1
        else:
            self.setlist_presets.insert(index, preset_name)
            idx = index
        self.save_setlist()
        return idx

    def remove_from_setlist(self, index: int, auto_save: bool = True):
        """Remove a preset reference from the setlist (doesn't delete preset).

        Set *auto_save* to False when doing bulk removals and call
        save_setlist() once after all mutations are done.
        """
        if 0 <= index < len(self.setlist_presets):
            self.setlist_presets.pop(index)
            # Maintain selection across the removed slot:
            #   - removing BEFORE active: shift active down by 1 to keep
            #     pointing at the same preset (was: silently shifted to a
            #     different preset).
            #   - removing AT active: clamp to last (or -1 if empty).
            #   - removing AFTER active: no change.
            if index < self.active_index:
                self.active_index -= 1
            elif self.active_index >= len(self.setlist_presets):
                self.active_index = max(-1, len(self.setlist_presets) - 1)
            if auto_save:
                self.save_setlist()

    def reorder_setlist(self, new_order: list[int]):
        """Reorder setlist by a list of old indices."""
        with self._io_lock:
            old = list(self.setlist_presets)
            # Validate that new_order is a permutation of 0..len-1 so no
            # entries are silently dropped.  Raise early rather than lose data.
            if sorted(new_order) != list(range(len(old))):
                raise ValueError(
                    f"reorder_setlist: new_order {new_order} is not a valid "
                    f"permutation of 0..{len(old) - 1}"
                )
            self.setlist_presets = [old[i] for i in new_order]
            if 0 <= self.active_index < len(new_order):
                try:
                    self.active_index = new_order.index(self.active_index)
                except ValueError:
                    self.active_index = -1
        self.save_setlist()

    def load_setlist_preset(self, index: int) -> Optional[dict]:
        """Load preset data from setlist by index. Sets active_index."""
        if index < 0 or index >= len(self.setlist_presets):
            return None
        name = self.setlist_presets[index]
        data = self.load_preset(name)
        if data:
            self.active_index = index
        return data

    def apply_preset(self, preset_data: dict, config: AppConfig) -> AppConfig:
        """Apply loaded preset data to the config."""
        cfg_data = preset_data.get("config", {})

        # Tempo
        try:
            config.tempo = max(TEMPO_MIN, min(TEMPO_MAX, float(cfg_data.get("tempo", config.tempo))))
        except (ValueError, TypeError, OverflowError):
            pass
        try:
            config.quantize = QuantizeMode(cfg_data.get("quantize", config.quantize))
        except (ValueError, KeyError, TypeError):
            pass
        try:
            config.row_count = max(1, min(16, int(cfg_data.get("row_count", config.row_count))))
        except (ValueError, TypeError, OverflowError):
            pass
        try:
            config.master_volume = max(0.0, min(2.0, float(cfg_data.get("master_volume", config.master_volume))))
        except (ValueError, TypeError, OverflowError):
            pass
        try:
            config.master_hp = max(20.0, min(500.0, float(cfg_data.get("master_hp", config.master_hp))))
        except (ValueError, TypeError, OverflowError):
            pass
        try:
            config.master_lp = max(200.0, min(20000.0, float(cfg_data.get("master_lp", config.master_lp))))
        except (ValueError, TypeError, OverflowError):
            pass
        try:
            config.fadetime = max(0.0, min(5000.0, float(cfg_data.get("fadetime", config.fadetime))))
        except (ValueError, TypeError, OverflowError):
            pass

        # Channels
        channels_data = cfg_data.get("channels", [])
        for i, ch_data in enumerate(channels_data):
            if i < len(config.channels):
                ch = config.channels[i]
                try:
                    ch.speed = max(0.125, min(8.0, float(ch_data.get("speed", ch.speed))))
                    ch.normal_speed = max(0.125, min(8.0, float(ch_data.get("normal_speed", ch_data.get("speed", ch.speed)))))
                except (ValueError, TypeError, OverflowError):
                    pass
                try:
                    ch.mode = PlaybackMode(ch_data.get("mode", ch.mode))
                except (ValueError, KeyError, TypeError):
                    pass
                ch.slave = ch_data.get("slave", ch.slave)
                ch.filename = ch_data.get("filename", ch.filename)
                try:
                    ch.gain = max(0.0, min(2.0, float(ch_data.get("gain", ch.gain))))
                    ch.group = max(0, min(NUM_GROUPS - 1, int(ch_data.get("group", ch.group))))
                except (ValueError, TypeError, OverflowError):
                    pass
                ch.reverse = ch_data.get("reverse", ch.reverse)
                ch.shot_hold = ch_data.get("shot_hold", ch.shot_hold)
                try:
                    ch.steps = max(1, min(64, int(ch_data.get("steps", ch.steps))))
                    ch.transpose = max(-24, min(24, int(ch_data.get("transpose", ch.transpose))))
                    ch.octave = max(-4, min(4, int(ch_data.get("octave", ch.octave))))
                    ch.sel_start = max(0.0, min(1.0, float(ch_data.get("sel_start", ch.sel_start))))
                    ch.sel_end = max(0.0, min(1.0, float(ch_data.get("sel_end", ch.sel_end))))
                    if ch.sel_start > ch.sel_end:
                        ch.sel_start, ch.sel_end = ch.sel_end, ch.sel_start
                except (ValueError, TypeError, OverflowError):
                    pass
                ch.mute = ch_data.get("mute", ch.mute)
                try:
                    ch.attack = max(0.0, min(5000.0, float(ch_data.get("attack", ch.attack))))
                    ch.release = max(0.0, min(5000.0, float(ch_data.get("release", ch.release))))
                    ch.pan = max(0.0, min(1.0, float(ch_data.get("pan", ch.pan))))
                    ch.pitch = max(-24.0, min(24.0, float(ch_data.get("pitch", ch.pitch))))
                    ch.velocity = max(0.0, min(1.0, float(ch_data.get("velocity", ch.velocity))))
                    ch.filter_val = max(-1.0, min(1.0, float(ch_data.get("filter_val", ch.filter_val))))
                    ch.ch_lp = max(0.0, min(1.0, float(ch_data.get("ch_lp", ch.ch_lp))))
                    ch.ch_hp = max(0.0, min(1.0, float(ch_data.get("ch_hp", ch.ch_hp))))
                    ch.pitch_scale = max(0, min(4, int(ch_data.get("pitch_scale", ch.pitch_scale))))
                    ch.pitch_root = max(-24, min(24, int(ch_data.get("pitch_root", ch.pitch_root))))
                except (ValueError, TypeError, OverflowError):
                    pass
                ch.pitch_loop = bool(ch_data.get("pitch_loop", ch_data.get("pitch_hold", ch.pitch_loop)))
                try:
                    ch.seq_step_div = max(0, min(6, int(ch_data.get("seq_step_div", ch.seq_step_div))))
                except (ValueError, TypeError, OverflowError):
                    pass
                try:
                    ch.seq_swing = max(50, min(75, int(ch_data.get("seq_swing", ch.seq_swing))))
                except (ValueError, TypeError, OverflowError):
                    pass

                # LIVE looper state
                live_data = ch_data.get("live")
                if isinstance(live_data, dict):
                    try:
                        ch.live.bars = max(1, min(32, int(live_data.get("bars", ch.live.bars))))
                        ch.live.quantize = int(live_data.get("quantize", ch.live.quantize))
                        ch.live.thru = bool(live_data.get("thru", ch.live.thru))
                        ch.live.count_in = max(0, min(8, int(live_data.get("count_in", ch.live.count_in))))
                        ch.live.input_trim = max(0.0, min(2.0, float(live_data.get("input_trim", ch.live.input_trim))))
                        ch.live.loop_state = str(live_data.get("loop_state", ch.live.loop_state))
                        ch.live.exported_path = str(live_data.get("exported_path", ch.live.exported_path))
                    except (ValueError, TypeError, OverflowError):
                        pass

        # Groups
        groups_data = cfg_data.get("groups", [])
        for i, g_data in enumerate(groups_data):
            if i < len(config.groups):
                g = config.groups[i]
                try:
                    g.volume = max(0.0, min(2.0, float(g_data.get("volume", g.volume))))
                    g.send = max(0.0, min(1.0, float(g_data.get("send", g.send))))
                    g.mute = g_data.get("mute", g.mute)
                    g.route = g_data.get("route", g.route)
                    g.fx_route = max(0, min(4, int(g_data.get("fx_route", g.fx_route))))
                except (ValueError, TypeError, OverflowError):
                    pass

        # Delay
        delay_data = cfg_data.get("delay", {})
        if delay_data:
            d = config.delay
            for key in ["sync_time", "ms_time", "time_mode", "fb_attack",
                        "fb_release", "fb_range_low", "fb_range_high",
                        "filter_lp", "filter_hp", "filter_freq_lp",
                        "filter_freq_hp", "gain", "enabled", "fx_route"]:
                if key in delay_data:
                    _safe_setattr(d, key, delay_data[key])

        # Tape Delay
        tape_delay_data = cfg_data.get("tape_delay", {})
        if tape_delay_data:
            td = config.tape_delay
            for key in ["sync_time", "ms_time", "time_mode", "feedback",
                        "wow_flutter", "saturation", "tone", "gain",
                        "enabled", "fx_route", "head_mode", "spring_mix"]:
                if key in tape_delay_data:
                    _safe_setattr(td, key, tape_delay_data[key])

        # Filter A
        filter_a_data = cfg_data.get("filter_a", cfg_data.get("filter", {}))
        if filter_a_data:
            fa = config.filter_a
            for key in ["lp_enabled", "hp_enabled", "lp_freq", "hp_freq",
                        "lp_q", "hp_q", "lf_enabled",
                        "lo_mid_gain", "lo_mid_freq", "lo_mid_q",
                        "mid_gain", "mid_freq", "mid_q",
                        "hi_mid_gain", "hi_mid_freq", "hi_mid_q",
                        "hf_enabled", "drive", "output_gain",
                        "enabled", "fx_route"]:
                if key in filter_a_data:
                    _safe_setattr(fa, key, filter_a_data[key])
            # Backward compat: old presets used single "resonance" for both Q
            if "resonance" in filter_a_data and "lp_q" not in filter_a_data:
                fa.lp_q = filter_a_data["resonance"]
                fa.hp_q = filter_a_data["resonance"]

        # Filter B (Moog ladder)
        filter_b_data = cfg_data.get("filter_b", {})
        if filter_b_data:
            fb = config.filter_b
            for key in ["cutoff", "resonance", "drive", "enabled", "fx_route"]:
                if key in filter_b_data:
                    _safe_setattr(fb, key, filter_b_data[key])
            # Backward compat: old presets had LP/HP fields for filter_b
            if "lp_freq" in filter_b_data and "cutoff" not in filter_b_data:
                fb.cutoff = filter_b_data["lp_freq"]

        # Modulation
        mod_data = cfg_data.get("mod", {})
        if mod_data:
            m = config.mod
            for key in ["phaser_enabled", "phaser_rate", "phaser_depth", "phaser_feedback",
                        "chorus_enabled", "chorus_rate", "chorus_depth", "chorus_mix",
                        "flanger_enabled", "flanger_rate", "flanger_depth",
                        "flanger_feedback", "flanger_mix"]:
                if key in mod_data:
                    _safe_setattr(m, key, mod_data[key])

        # Lo-fi media effects + compressor (bus A and bus B)
        _FX_KEYS = (
            ("reel_to_reel", ["enabled", "tape_speed", "saturation", "flutter",
                              "bias", "tone", "mix", "fx_route"]),
            ("cassette_tape", ["enabled", "saturation", "hiss", "punch",
                               "warp", "tone", "mix", "fx_route"]),
            ("vhs", ["enabled", "wobble", "dropout", "worn",
                     "noise", "mix", "fx_route"]),
            ("vinyl", ["enabled", "crackle", "pops", "warmth",
                       "wear", "mix", "fx_route"]),
            ("compressor", ["enabled", "threshold", "ratio", "attack",
                            "release", "makeup_gain", "sidechain_source",
                            "sc_hp_freq", "thrust_enabled", "fx_route"]),
            ("hall_reverb", ["enabled", "room_size", "decay", "diffusion",
                             "mix", "room_type", "fx_route"]),
            ("plate_reverb", ["enabled", "decay", "damping", "pre_delay",
                              "mix", "fx_route"]),
            ("bit_crusher", ["enabled", "bit_depth", "sample_rate_reduction",
                             "mix", "fx_route"]),
            ("tube_overdrive", ["enabled", "drive", "tone", "tube_bias",
                                "mix", "fx_route"]),
            ("glue_compressor", ["enabled", "threshold", "ratio_idx",
                                  "attack_idx", "release_idx", "makeup_gain",
                                  "hpf_idx", "fx_route"]),
            ("ether_drift", ["enabled", "rate", "depth", "feedback",
                             "spread", "route", "sync", "mix", "fx_route"]),
            ("noise_gate", ["enabled", "threshold", "attack", "hold",
                            "release", "range_db", "key_filter", "key_freq",
                            "fx_route"]),
            ("tremolo", ["enabled", "rate", "depth", "shape",
                         "stereo_mode", "mix", "fx_route"]),
            ("auto_wah", ["enabled", "sensitivity", "peak", "mode",
                          "filter_type", "mix", "fx_route"]),
            ("fet_limiter", ["enabled", "input_gain", "output_gain",
                             "attack_idx", "release_idx", "ratio_idx",
                             "fx_route"]),
            ("rotary", ["enabled", "speed", "acceleration",
                        "horn_level", "rotor_level", "mix", "fx_route"]),
            ("exciter", ["enabled", "tune", "mix", "lo_tune",
                         "lo_mix", "output", "fx_route"]),
            ("transient_shaper", ["enabled", "attack_shape", "sustain_shape",
                                  "output", "stereo_link", "fx_route"]),
            ("harmonizer", ["enabled", "voice_a_semitones", "voice_a_cents",
                            "voice_a_delay_ms", "voice_b_semitones",
                            "voice_b_cents", "voice_b_delay_ms",
                            "feedback", "mix", "algorithm", "fx_route"]),
            ("stereo_widener", ["enabled", "width", "center",
                                "low_mono_freq", "fx_route"]),
            ("freq_shifter", ["enabled", "shift_hz", "feedback",
                              "mix", "fx_route"]),
            ("ring_mod", ["enabled", "freq_hz", "shape", "depth",
                          "mix", "fx_route"]),
            ("simple_eq", ["enabled", "low", "mid", "high",
                           "low_freq", "high_freq", "mix", "fx_route"]),
            ("ping_pong_delay", ["enabled", "time_ms", "ping_ms", "pong_ms",
                                 "ping_pong_link", "feedback",
                                 "spread", "damping", "mix", "fx_route"]),
            ("shimmer_reverb", ["enabled", "decay", "shimmer", "pitch",
                                "damping", "pre_delay_ms", "mix", "fx_route"]),
            ("lofi_radio", ["enabled", "band", "lo_cut", "hi_cut",
                            "noise", "distortion", "mix", "fx_route"]),
            ("auto_pan", ["enabled", "rate", "depth", "shape",
                          "phase_offset", "mix", "fx_route"]),
            ("fuzz", ["enabled", "gain", "tone", "sustain",
                      "gate", "mix", "fx_route"]),
            ("stutter", ["enabled", "size", "rate", "chaos",
                         "smear", "trigger", "mix", "fx_route"]),
            ("granular_freeze", ["enabled", "grain_size", "scatter",
                                 "spread", "density", "freeze",
                                 "mix", "fx_route"]),
            ("gate_reverb", ["enabled", "room", "gate_time", "slope",
                             "pre_delay", "mode", "mix", "fx_route"]),
        )
        for fx_name, fx_keys in _FX_KEYS:
            for suffix in ("", "_b", "_m", "_c", "_d"):
                cfg_key = f"{fx_name}{suffix}" if suffix else fx_name
                fx_data = cfg_data.get(cfg_key, {})
                if fx_data and hasattr(config, cfg_key):
                    state = getattr(config, cfg_key)
                    for key in fx_keys:
                        if key in fx_data:
                            _safe_setattr(state, key, fx_data[key])

        # Per-bus state for delay / tape_delay / filters / mod
        _EXTRA_KEYS = (
            ("delay", ["sync_time", "ms_time", "time_mode", "fb_attack",
                       "fb_release", "fb_range_low", "fb_range_high",
                       "filter_lp", "filter_hp", "filter_freq_lp",
                       "filter_freq_hp", "gain", "enabled", "fx_route"]),
            ("tape_delay", ["sync_time", "ms_time", "time_mode", "feedback",
                            "wow_flutter", "saturation", "tone", "gain",
                            "head_mode", "spring_mix",
                            "enabled", "fx_route"]),
            ("filter_a", ["lp_enabled", "hp_enabled", "lp_freq", "hp_freq",
                          "lp_q", "hp_q", "lf_enabled",
                          "lo_mid_gain", "lo_mid_freq", "lo_mid_q",
                          "mid_gain", "mid_freq", "mid_q",
                          "hi_mid_gain", "hi_mid_freq", "hi_mid_q",
                          "hf_enabled", "drive", "output_gain",
                          "enabled", "fx_route"]),
            ("filter_b", ["cutoff", "resonance", "drive", "enabled", "fx_route"]),
            ("mod", ["phaser_enabled", "phaser_rate", "phaser_depth", "phaser_feedback",
                     "chorus_enabled", "chorus_rate", "chorus_depth", "chorus_mix",
                     "flanger_enabled", "flanger_rate", "flanger_depth",
                     "flanger_feedback", "flanger_mix"]),
        )
        for base_name, base_keys in _EXTRA_KEYS:
            for suffix in ("_b", "_m", "_c", "_d"):
                cfg_name = f"{base_name}{suffix}"
                entry = cfg_data.get(cfg_name, {})
                if entry and hasattr(config, cfg_name):
                    st = getattr(config, cfg_name)
                    for key in base_keys:
                        if key in entry:
                            _safe_setattr(st, key, entry[key])

        # Patterns
        patterns_data = cfg_data.get("patterns", [])
        for i, p_data in enumerate(patterns_data):
            if i < len(config.patterns):
                config.patterns[i].length = p_data.get("length", config.patterns[i].length)

        # Group colors
        if "group_colors" in cfg_data:
            config.group_colors = cfg_data["group_colors"]

        # OSC
        config.osc_host = cfg_data.get("osc_host", config.osc_host)
        config.osc_send_port = cfg_data.get("osc_send_port", config.osc_send_port)
        config.osc_recv_port = cfg_data.get("osc_recv_port", config.osc_recv_port)

        # Aux bus
        try:
            config.aux_volume = max(0.0, min(2.0, float(cfg_data.get("aux_volume", config.aux_volume))))
        except (ValueError, TypeError, OverflowError):
            pass
        config.aux_mute = bool(cfg_data.get("aux_mute", config.aux_mute))
        try:
            config.aux_fx_route = max(0, min(4, int(cfg_data.get("aux_fx_route", config.aux_fx_route))))
        except (ValueError, TypeError, OverflowError):
            pass
        # Support both new float key and legacy bool key from older presets
        try:
            if "aux_send_level" in cfg_data:
                config.aux_send_level = max(0.0, min(1.0, float(cfg_data["aux_send_level"])))
            elif "aux_send_toggle" in cfg_data:
                config.aux_send_level = 1.0 if cfg_data["aux_send_toggle"] else 0.0
        except (ValueError, TypeError, OverflowError):
            pass

        # Grid
        try:
            config.grid_columns = max(1, min(16, int(cfg_data.get("grid_columns", config.grid_columns))))
        except (ValueError, TypeError, OverflowError):
            pass
        backend = cfg_data.get("monome_backend", config.monome_backend)
        if backend not in MONOME_BACKENDS:
            backend = MONOME_BACKEND_AUTO
        config.monome_backend = backend

        # Per-effect routing
        # Backward compat: old presets stored delay_route at top level
        if "delay_route" in cfg_data:
            config.delay.fx_route = max(0, min(2, int(cfg_data["delay_route"])))
        config.mod_route = cfg_data.get("mod_route", config.mod_route)

        # Slicer tool state (backward-compatible: missing key → defaults)
        slicer_data = cfg_data.get("slicer", {})
        if slicer_data:
            for key in ["cue_volume", "cue_device", "cue_channel_offset",
                        "cue_mode", "preview_loop", "auto_chop_mode",
                        "naming_template"]:
                if key in slicer_data:
                    _safe_setattr(config.slicer, key, slicer_data[key])

        # Foreign Exchange (stowaway) plugin slots
        stowaway_data = cfg_data.get("stowaway_slots", [])
        if stowaway_data and hasattr(config, "stowaway_slots"):
            from mlrvp.config import StowawayState
            loaded = [StowawayState.from_dict(d) for d in stowaway_data]
            # Pad to expected 20 slots (5 buses × 4 slots) if preset has fewer
            _EXPECTED = 20
            while len(loaded) < _EXPECTED:
                loaded.append(StowawayState(slot_index=len(loaded)))
            config.stowaway_slots = loaded

        # Control row
        cr_data = cfg_data.get("control_row", {})
        if cr_data and isinstance(cr_data, dict):
            try:
                config.control_row.enabled = bool(cr_data.get("enabled", config.control_row.enabled))
                config.control_row.monome_row = int(cr_data.get("monome_row", config.control_row.monome_row))
                from mlrvp.config import CR_SLOT_GROUP_STOP, CR_SLOT_PATTERN, CR_SLOT_EMPTY
                saved_assignments = cr_data.get("assignments")
                if saved_assignments and isinstance(saved_assignments, list):
                    empty = {"type": CR_SLOT_EMPTY, "value": 0}
                    # Malformed slots (hand-edited / damaged files) become empty
                    valid = [
                        a if (isinstance(a, dict) and isinstance(a.get("type"), str)
                              and isinstance(a.get("value", 0), int)) else dict(empty)
                        for a in saved_assignments
                    ]
                    config.control_row.assignments = (valid + [empty] * 8)[:8]
                elif "num_group_stops" in cr_data or "num_patterns" in cr_data:
                    n_grp = max(1, min(7, int(cr_data.get("num_group_stops", 4))))
                    n_pat = max(1, min(7, int(cr_data.get("num_patterns", 4))))
                    if n_grp + n_pat > 8:
                        n_pat = max(1, 8 - n_grp)
                    config.control_row.assignments = (
                        [{"type": CR_SLOT_GROUP_STOP, "value": i} for i in range(n_grp)] +
                        [{"type": CR_SLOT_PATTERN,    "value": i} for i in range(n_pat)] +
                        [{"type": CR_SLOT_EMPTY,      "value": 0}] * max(0, 8 - n_grp - n_pat)
                    )
            except (ValueError, TypeError, OverflowError) as exc:
                log.warning("Preset load: skipping bad control_row data (%s)", exc)

        # Grid MIDI CC output map
        gm_data = cfg_data.get("grid_midi_cc_map")
        if isinstance(gm_data, dict):
            config.grid_midi_cc_map = {
                k: v for k, v in gm_data.items()
                if isinstance(k, str) and isinstance(v, dict)
            }

        return config

    def _serialize_config(self, config: AppConfig) -> dict:
        """Serialize config to a JSON-compatible dict."""
        result = {
            "tempo": config.tempo,
            "quantize": int(config.quantize),
            "row_count": config.row_count,
            "master_volume": config.master_volume,
            "master_hp": config.master_hp,
            "master_lp": config.master_lp,
            "fadetime": config.fadetime,
            "channels": [
                {
                    "speed": ch.speed,
                    "normal_speed": ch.normal_speed,
                    "mode": int(ch.mode),
                    "slave": ch.slave,
                    "filename": ch.filename,
                    "gain": ch.gain,
                    "group": ch.group,
                    "reverse": ch.reverse,
                    "shot_hold": ch.shot_hold,
                    "steps": ch.steps,
                    "transpose": ch.transpose,
                    "octave": ch.octave,
                    "sel_start": ch.sel_start,
                    "sel_end": ch.sel_end,
                    "mute": ch.mute,
                    "pan": ch.pan,
                    "attack": ch.attack,
                    "release": ch.release,
                    "pitch": ch.pitch,
                    "velocity": ch.velocity,
                    "filter_val": ch.filter_val,
                    "ch_lp": ch.ch_lp,
                    "ch_hp": ch.ch_hp,
                    "pitch_scale": ch.pitch_scale,
                    "pitch_root": ch.pitch_root,
                    "pitch_loop": ch.pitch_loop,
                    "seq_step_div": ch.seq_step_div,
                    "seq_swing": ch.seq_swing,
                    "live": {
                        "bars": ch.live.bars,
                        "quantize": ch.live.quantize,
                        "thru": ch.live.thru,
                        "count_in": ch.live.count_in,
                        "input_trim": ch.live.input_trim,
                        "loop_state": ch.live.loop_state,
                        "exported_path": ch.live.exported_path,
                    },
                }
                for ch in config.channels
            ],
            "groups": [
                {"volume": g.volume, "send": g.send, "mute": g.mute,
                 "route": g.route, "fx_route": g.fx_route}
                for g in config.groups
            ],
            "delay": {
                "sync_time": config.delay.sync_time,
                "ms_time": config.delay.ms_time,
                "time_mode": config.delay.time_mode,
                "fb_attack": config.delay.fb_attack,
                "fb_release": config.delay.fb_release,
                "fb_range_low": config.delay.fb_range_low,
                "fb_range_high": config.delay.fb_range_high,
                "filter_lp": config.delay.filter_lp,
                "filter_hp": config.delay.filter_hp,
                "filter_freq_lp": config.delay.filter_freq_lp,
                "filter_freq_hp": config.delay.filter_freq_hp,
                "gain": config.delay.gain,
                "enabled": config.delay.enabled,
                "fx_route": config.delay.fx_route,
            },
            "tape_delay": {
                "sync_time": config.tape_delay.sync_time,
                "ms_time": config.tape_delay.ms_time,
                "time_mode": config.tape_delay.time_mode,
                "feedback": config.tape_delay.feedback,
                "wow_flutter": config.tape_delay.wow_flutter,
                "saturation": config.tape_delay.saturation,
                "tone": config.tape_delay.tone,
                "gain": config.tape_delay.gain,
                "head_mode": config.tape_delay.head_mode,
                "spring_mix": config.tape_delay.spring_mix,
                "enabled": config.tape_delay.enabled,
                "fx_route": config.tape_delay.fx_route,
            },
            "filter_a": {k: v for k, v in vars(config.filter_a).items()
                         if not k.startswith('_')},
            "filter_b": {
                "cutoff": config.filter_b.cutoff,
                "resonance": config.filter_b.resonance,
                "drive": config.filter_b.drive,
                "enabled": config.filter_b.enabled,
                "fx_route": config.filter_b.fx_route,
            },
            "mod": {
                "phaser_enabled": config.mod.phaser_enabled,
                "phaser_rate": config.mod.phaser_rate,
                "phaser_depth": config.mod.phaser_depth,
                "phaser_feedback": config.mod.phaser_feedback,
                "chorus_enabled": config.mod.chorus_enabled,
                "chorus_rate": config.mod.chorus_rate,
                "chorus_depth": config.mod.chorus_depth,
                "chorus_mix": config.mod.chorus_mix,
                "flanger_enabled": config.mod.flanger_enabled,
                "flanger_rate": config.mod.flanger_rate,
                "flanger_depth": config.mod.flanger_depth,
                "flanger_feedback": config.mod.flanger_feedback,
                "flanger_mix": config.mod.flanger_mix,
            },
            "patterns": [{"length": p.length} for p in config.patterns],
            "group_colors": config.group_colors,
            "aux_volume": config.aux_volume,
            "aux_mute": config.aux_mute,
            "aux_fx_route": config.aux_fx_route,
            "aux_send_level": config.aux_send_level,
            "grid_columns": config.grid_columns,
            "monome_backend": config.monome_backend,
            "osc_host": config.osc_host,
            "osc_send_port": config.osc_send_port,
            "osc_recv_port": config.osc_recv_port,
            "mod_route": config.mod_route,
            "reel_to_reel": {
                "enabled": config.reel_to_reel.enabled,
                "tape_speed": config.reel_to_reel.tape_speed,
                "saturation": config.reel_to_reel.saturation,
                "flutter": config.reel_to_reel.flutter,
                "bias": config.reel_to_reel.bias,
                "tone": config.reel_to_reel.tone,
                "mix": config.reel_to_reel.mix,
                "fx_route": config.reel_to_reel.fx_route,
            },
            "cassette_tape": {
                "enabled": config.cassette_tape.enabled,
                "saturation": config.cassette_tape.saturation,
                "hiss": config.cassette_tape.hiss,
                "punch": config.cassette_tape.punch,
                "warp": config.cassette_tape.warp,
                "tone": config.cassette_tape.tone,
                "mix": config.cassette_tape.mix,
                "fx_route": config.cassette_tape.fx_route,
            },
            "vhs": {
                "enabled": config.vhs.enabled,
                "wobble": config.vhs.wobble,
                "dropout": config.vhs.dropout,
                "worn": config.vhs.worn,
                "noise": config.vhs.noise,
                "mix": config.vhs.mix,
                "fx_route": config.vhs.fx_route,
            },
            "vinyl": {
                "enabled": config.vinyl.enabled,
                "crackle": config.vinyl.crackle,
                "pops": config.vinyl.pops,
                "warmth": config.vinyl.warmth,
                "wear": config.vinyl.wear,
                "mix": config.vinyl.mix,
                "fx_route": config.vinyl.fx_route,
            },
            "compressor": {
                "enabled": config.compressor.enabled,
                "threshold": config.compressor.threshold,
                "ratio": config.compressor.ratio,
                "attack": config.compressor.attack,
                "release": config.compressor.release,
                "makeup_gain": config.compressor.makeup_gain,
                "sidechain_source": config.compressor.sidechain_source,
                "sc_hp_freq": config.compressor.sc_hp_freq,
                "thrust_enabled": config.compressor.thrust_enabled,
                "fx_route": config.compressor.fx_route,
            },
            "hall_reverb": {
                "enabled": config.hall_reverb.enabled,
                "room_size": config.hall_reverb.room_size,
                "decay": config.hall_reverb.decay,
                "diffusion": config.hall_reverb.diffusion,
                "mix": config.hall_reverb.mix,
                "room_type": config.hall_reverb.room_type,
                "fx_route": config.hall_reverb.fx_route,
            },
            "plate_reverb": {
                "enabled": config.plate_reverb.enabled,
                "decay": config.plate_reverb.decay,
                "damping": config.plate_reverb.damping,
                "pre_delay": config.plate_reverb.pre_delay,
                "mix": config.plate_reverb.mix,
                "fx_route": config.plate_reverb.fx_route,
            },
            "bit_crusher": {
                "enabled": config.bit_crusher.enabled,
                "bit_depth": config.bit_crusher.bit_depth,
                "sample_rate_reduction": config.bit_crusher.sample_rate_reduction,
                "mix": config.bit_crusher.mix,
                "fx_route": config.bit_crusher.fx_route,
            },
            "tube_overdrive": {
                "enabled": config.tube_overdrive.enabled,
                "drive": config.tube_overdrive.drive,
                "tone": config.tube_overdrive.tone,
                "tube_bias": config.tube_overdrive.tube_bias,
                "mix": config.tube_overdrive.mix,
                "fx_route": config.tube_overdrive.fx_route,
            },
            "bit_crusher_b": {
                "enabled": config.bit_crusher_b.enabled,
                "bit_depth": config.bit_crusher_b.bit_depth,
                "sample_rate_reduction": config.bit_crusher_b.sample_rate_reduction,
                "mix": config.bit_crusher_b.mix,
                "fx_route": config.bit_crusher_b.fx_route,
            },
            "tube_overdrive_b": {
                "enabled": config.tube_overdrive_b.enabled,
                "drive": config.tube_overdrive_b.drive,
                "tone": config.tube_overdrive_b.tone,
                "tube_bias": config.tube_overdrive_b.tube_bias,
                "mix": config.tube_overdrive_b.mix,
                "fx_route": config.tube_overdrive_b.fx_route,
            },
            # Per-bus B state
            "delay_b": {
                "sync_time": config.delay_b.sync_time,
                "ms_time": config.delay_b.ms_time,
                "time_mode": config.delay_b.time_mode,
                "fb_attack": config.delay_b.fb_attack,
                "fb_release": config.delay_b.fb_release,
                "fb_range_low": config.delay_b.fb_range_low,
                "fb_range_high": config.delay_b.fb_range_high,
                "filter_lp": config.delay_b.filter_lp,
                "filter_hp": config.delay_b.filter_hp,
                "filter_freq_lp": config.delay_b.filter_freq_lp,
                "filter_freq_hp": config.delay_b.filter_freq_hp,
                "gain": config.delay_b.gain,
                "enabled": config.delay_b.enabled,
                "fx_route": config.delay_b.fx_route,
            },
            "tape_delay_b": {
                "sync_time": config.tape_delay_b.sync_time,
                "ms_time": config.tape_delay_b.ms_time,
                "time_mode": config.tape_delay_b.time_mode,
                "feedback": config.tape_delay_b.feedback,
                "wow_flutter": config.tape_delay_b.wow_flutter,
                "saturation": config.tape_delay_b.saturation,
                "tone": config.tape_delay_b.tone,
                "gain": config.tape_delay_b.gain,
                "head_mode": config.tape_delay_b.head_mode,
                "spring_mix": config.tape_delay_b.spring_mix,
                "enabled": config.tape_delay_b.enabled,
                "fx_route": config.tape_delay_b.fx_route,
            },
            "filter_a_b": {k: v for k, v in vars(config.filter_a_b).items()
                           if not k.startswith('_')},
            "filter_b_b": {
                "cutoff": config.filter_b_b.cutoff,
                "resonance": config.filter_b_b.resonance,
                "drive": config.filter_b_b.drive,
                "enabled": config.filter_b_b.enabled,
                "fx_route": config.filter_b_b.fx_route,
            },
            "mod_b": {
                "phaser_enabled": config.mod_b.phaser_enabled,
                "phaser_rate": config.mod_b.phaser_rate,
                "phaser_depth": config.mod_b.phaser_depth,
                "phaser_feedback": config.mod_b.phaser_feedback,
                "chorus_enabled": config.mod_b.chorus_enabled,
                "chorus_rate": config.mod_b.chorus_rate,
                "chorus_depth": config.mod_b.chorus_depth,
                "chorus_mix": config.mod_b.chorus_mix,
                "flanger_enabled": config.mod_b.flanger_enabled,
                "flanger_rate": config.mod_b.flanger_rate,
                "flanger_depth": config.mod_b.flanger_depth,
                "flanger_feedback": config.mod_b.flanger_feedback,
                "flanger_mix": config.mod_b.flanger_mix,
            },
            "reel_to_reel_b": {
                "enabled": config.reel_to_reel_b.enabled,
                "tape_speed": config.reel_to_reel_b.tape_speed,
                "saturation": config.reel_to_reel_b.saturation,
                "flutter": config.reel_to_reel_b.flutter,
                "bias": config.reel_to_reel_b.bias,
                "tone": config.reel_to_reel_b.tone,
                "mix": config.reel_to_reel_b.mix,
                "fx_route": config.reel_to_reel_b.fx_route,
            },
            "cassette_tape_b": {
                "enabled": config.cassette_tape_b.enabled,
                "saturation": config.cassette_tape_b.saturation,
                "hiss": config.cassette_tape_b.hiss,
                "punch": config.cassette_tape_b.punch,
                "warp": config.cassette_tape_b.warp,
                "tone": config.cassette_tape_b.tone,
                "mix": config.cassette_tape_b.mix,
                "fx_route": config.cassette_tape_b.fx_route,
            },
            "vhs_b": {
                "enabled": config.vhs_b.enabled,
                "wobble": config.vhs_b.wobble,
                "dropout": config.vhs_b.dropout,
                "worn": config.vhs_b.worn,
                "noise": config.vhs_b.noise,
                "mix": config.vhs_b.mix,
                "fx_route": config.vhs_b.fx_route,
            },
            "vinyl_b": {
                "enabled": config.vinyl_b.enabled,
                "crackle": config.vinyl_b.crackle,
                "pops": config.vinyl_b.pops,
                "warmth": config.vinyl_b.warmth,
                "wear": config.vinyl_b.wear,
                "mix": config.vinyl_b.mix,
                "fx_route": config.vinyl_b.fx_route,
            },
            "compressor_b": {
                "enabled": config.compressor_b.enabled,
                "threshold": config.compressor_b.threshold,
                "ratio": config.compressor_b.ratio,
                "attack": config.compressor_b.attack,
                "release": config.compressor_b.release,
                "makeup_gain": config.compressor_b.makeup_gain,
                "sidechain_source": config.compressor_b.sidechain_source,
                "sc_hp_freq": config.compressor_b.sc_hp_freq,
                "thrust_enabled": config.compressor_b.thrust_enabled,
                "fx_route": config.compressor_b.fx_route,
            },
            "hall_reverb_b": {
                "enabled": config.hall_reverb_b.enabled,
                "room_size": config.hall_reverb_b.room_size,
                "decay": config.hall_reverb_b.decay,
                "diffusion": config.hall_reverb_b.diffusion,
                "mix": config.hall_reverb_b.mix,
                "room_type": config.hall_reverb_b.room_type,
                "fx_route": config.hall_reverb_b.fx_route,
            },
            "plate_reverb_b": {
                "enabled": config.plate_reverb_b.enabled,
                "decay": config.plate_reverb_b.decay,
                "damping": config.plate_reverb_b.damping,
                "pre_delay": config.plate_reverb_b.pre_delay,
                "mix": config.plate_reverb_b.mix,
                "fx_route": config.plate_reverb_b.fx_route,
            },
            # Per-bus M state
            "delay_m": {
                "sync_time": config.delay_m.sync_time,
                "ms_time": config.delay_m.ms_time,
                "time_mode": config.delay_m.time_mode,
                "fb_attack": config.delay_m.fb_attack,
                "fb_release": config.delay_m.fb_release,
                "fb_range_low": config.delay_m.fb_range_low,
                "fb_range_high": config.delay_m.fb_range_high,
                "filter_lp": config.delay_m.filter_lp,
                "filter_hp": config.delay_m.filter_hp,
                "filter_freq_lp": config.delay_m.filter_freq_lp,
                "filter_freq_hp": config.delay_m.filter_freq_hp,
                "gain": config.delay_m.gain,
                "enabled": config.delay_m.enabled,
                "fx_route": config.delay_m.fx_route,
            },
            "tape_delay_m": {
                "sync_time": config.tape_delay_m.sync_time,
                "ms_time": config.tape_delay_m.ms_time,
                "time_mode": config.tape_delay_m.time_mode,
                "feedback": config.tape_delay_m.feedback,
                "wow_flutter": config.tape_delay_m.wow_flutter,
                "saturation": config.tape_delay_m.saturation,
                "tone": config.tape_delay_m.tone,
                "gain": config.tape_delay_m.gain,
                "head_mode": config.tape_delay_m.head_mode,
                "spring_mix": config.tape_delay_m.spring_mix,
                "enabled": config.tape_delay_m.enabled,
                "fx_route": config.tape_delay_m.fx_route,
            },
            "filter_a_m": {k: v for k, v in vars(config.filter_a_m).items()
                           if not k.startswith('_')},
            "filter_b_m": {
                "cutoff": config.filter_b_m.cutoff,
                "resonance": config.filter_b_m.resonance,
                "drive": config.filter_b_m.drive,
                "enabled": config.filter_b_m.enabled,
                "fx_route": config.filter_b_m.fx_route,
            },
            "mod_m": {
                "phaser_enabled": config.mod_m.phaser_enabled,
                "phaser_rate": config.mod_m.phaser_rate,
                "phaser_depth": config.mod_m.phaser_depth,
                "phaser_feedback": config.mod_m.phaser_feedback,
                "chorus_enabled": config.mod_m.chorus_enabled,
                "chorus_rate": config.mod_m.chorus_rate,
                "chorus_depth": config.mod_m.chorus_depth,
                "chorus_mix": config.mod_m.chorus_mix,
                "flanger_enabled": config.mod_m.flanger_enabled,
                "flanger_rate": config.mod_m.flanger_rate,
                "flanger_depth": config.mod_m.flanger_depth,
                "flanger_feedback": config.mod_m.flanger_feedback,
                "flanger_mix": config.mod_m.flanger_mix,
            },
            "reel_to_reel_m": {
                "enabled": config.reel_to_reel_m.enabled,
                "tape_speed": config.reel_to_reel_m.tape_speed,
                "saturation": config.reel_to_reel_m.saturation,
                "flutter": config.reel_to_reel_m.flutter,
                "bias": config.reel_to_reel_m.bias,
                "tone": config.reel_to_reel_m.tone,
                "mix": config.reel_to_reel_m.mix,
                "fx_route": config.reel_to_reel_m.fx_route,
            },
            "cassette_tape_m": {
                "enabled": config.cassette_tape_m.enabled,
                "saturation": config.cassette_tape_m.saturation,
                "hiss": config.cassette_tape_m.hiss,
                "punch": config.cassette_tape_m.punch,
                "warp": config.cassette_tape_m.warp,
                "tone": config.cassette_tape_m.tone,
                "mix": config.cassette_tape_m.mix,
                "fx_route": config.cassette_tape_m.fx_route,
            },
            "vhs_m": {
                "enabled": config.vhs_m.enabled,
                "wobble": config.vhs_m.wobble,
                "dropout": config.vhs_m.dropout,
                "worn": config.vhs_m.worn,
                "noise": config.vhs_m.noise,
                "mix": config.vhs_m.mix,
                "fx_route": config.vhs_m.fx_route,
            },
            "vinyl_m": {
                "enabled": config.vinyl_m.enabled,
                "crackle": config.vinyl_m.crackle,
                "pops": config.vinyl_m.pops,
                "warmth": config.vinyl_m.warmth,
                "wear": config.vinyl_m.wear,
                "mix": config.vinyl_m.mix,
                "fx_route": config.vinyl_m.fx_route,
            },
            "compressor_m": {
                "enabled": config.compressor_m.enabled,
                "threshold": config.compressor_m.threshold,
                "ratio": config.compressor_m.ratio,
                "attack": config.compressor_m.attack,
                "release": config.compressor_m.release,
                "makeup_gain": config.compressor_m.makeup_gain,
                "sidechain_source": config.compressor_m.sidechain_source,
                "sc_hp_freq": config.compressor_m.sc_hp_freq,
                "thrust_enabled": config.compressor_m.thrust_enabled,
                "fx_route": config.compressor_m.fx_route,
            },
            "glue_compressor": {
                "enabled": config.glue_compressor.enabled,
                "threshold": config.glue_compressor.threshold,
                "ratio_idx": config.glue_compressor.ratio_idx,
                "attack_idx": config.glue_compressor.attack_idx,
                "release_idx": config.glue_compressor.release_idx,
                "makeup_gain": config.glue_compressor.makeup_gain,
                "hpf_idx": config.glue_compressor.hpf_idx,
                "fx_route": config.glue_compressor.fx_route,
            },
            "glue_compressor_b": {
                "enabled": config.glue_compressor_b.enabled,
                "threshold": config.glue_compressor_b.threshold,
                "ratio_idx": config.glue_compressor_b.ratio_idx,
                "attack_idx": config.glue_compressor_b.attack_idx,
                "release_idx": config.glue_compressor_b.release_idx,
                "makeup_gain": config.glue_compressor_b.makeup_gain,
                "hpf_idx": config.glue_compressor_b.hpf_idx,
                "fx_route": config.glue_compressor_b.fx_route,
            },
            "glue_compressor_m": {
                "enabled": config.glue_compressor_m.enabled,
                "threshold": config.glue_compressor_m.threshold,
                "ratio_idx": config.glue_compressor_m.ratio_idx,
                "attack_idx": config.glue_compressor_m.attack_idx,
                "release_idx": config.glue_compressor_m.release_idx,
                "makeup_gain": config.glue_compressor_m.makeup_gain,
                "hpf_idx": config.glue_compressor_m.hpf_idx,
                "fx_route": config.glue_compressor_m.fx_route,
            },
            "hall_reverb_m": {
                "enabled": config.hall_reverb_m.enabled,
                "room_size": config.hall_reverb_m.room_size,
                "decay": config.hall_reverb_m.decay,
                "diffusion": config.hall_reverb_m.diffusion,
                "mix": config.hall_reverb_m.mix,
                "room_type": config.hall_reverb_m.room_type,
                "fx_route": config.hall_reverb_m.fx_route,
            },
            "plate_reverb_m": {
                "enabled": config.plate_reverb_m.enabled,
                "decay": config.plate_reverb_m.decay,
                "damping": config.plate_reverb_m.damping,
                "pre_delay": config.plate_reverb_m.pre_delay,
                "mix": config.plate_reverb_m.mix,
                "fx_route": config.plate_reverb_m.fx_route,
            },
            "bit_crusher_m": {
                "enabled": config.bit_crusher_m.enabled,
                "bit_depth": config.bit_crusher_m.bit_depth,
                "sample_rate_reduction": config.bit_crusher_m.sample_rate_reduction,
                "mix": config.bit_crusher_m.mix,
                "fx_route": config.bit_crusher_m.fx_route,
            },
            "tube_overdrive_m": {
                "enabled": config.tube_overdrive_m.enabled,
                "drive": config.tube_overdrive_m.drive,
                "tone": config.tube_overdrive_m.tone,
                "tube_bias": config.tube_overdrive_m.tube_bias,
                "mix": config.tube_overdrive_m.mix,
                "fx_route": config.tube_overdrive_m.fx_route,
            },
            "ether_drift": {
                "enabled": config.ether_drift.enabled,
                "rate": config.ether_drift.rate,
                "depth": config.ether_drift.depth,
                "feedback": config.ether_drift.feedback,
                "spread": config.ether_drift.spread,
                "route": config.ether_drift.route,
                "sync": config.ether_drift.sync,
                "mix": config.ether_drift.mix,
                "fx_route": config.ether_drift.fx_route,
            },
            "ether_drift_b": {
                "enabled": config.ether_drift_b.enabled,
                "rate": config.ether_drift_b.rate,
                "depth": config.ether_drift_b.depth,
                "feedback": config.ether_drift_b.feedback,
                "spread": config.ether_drift_b.spread,
                "route": config.ether_drift_b.route,
                "sync": config.ether_drift_b.sync,
                "mix": config.ether_drift_b.mix,
                "fx_route": config.ether_drift_b.fx_route,
            },
            "ether_drift_m": {
                "enabled": config.ether_drift_m.enabled,
                "rate": config.ether_drift_m.rate,
                "depth": config.ether_drift_m.depth,
                "feedback": config.ether_drift_m.feedback,
                "spread": config.ether_drift_m.spread,
                "route": config.ether_drift_m.route,
                "sync": config.ether_drift_m.sync,
                "mix": config.ether_drift_m.mix,
                "fx_route": config.ether_drift_m.fx_route,
            },
            "noise_gate": {
                "enabled": config.noise_gate.enabled,
                "threshold": config.noise_gate.threshold,
                "attack": config.noise_gate.attack,
                "hold": config.noise_gate.hold,
                "release": config.noise_gate.release,
                "range_db": config.noise_gate.range_db,
                "key_filter": config.noise_gate.key_filter,
                "key_freq": config.noise_gate.key_freq,
                "fx_route": config.noise_gate.fx_route,
            },
            "noise_gate_b": {
                "enabled": config.noise_gate_b.enabled,
                "threshold": config.noise_gate_b.threshold,
                "attack": config.noise_gate_b.attack,
                "hold": config.noise_gate_b.hold,
                "release": config.noise_gate_b.release,
                "range_db": config.noise_gate_b.range_db,
                "key_filter": config.noise_gate_b.key_filter,
                "key_freq": config.noise_gate_b.key_freq,
                "fx_route": config.noise_gate_b.fx_route,
            },
            "noise_gate_m": {
                "enabled": config.noise_gate_m.enabled,
                "threshold": config.noise_gate_m.threshold,
                "attack": config.noise_gate_m.attack,
                "hold": config.noise_gate_m.hold,
                "release": config.noise_gate_m.release,
                "range_db": config.noise_gate_m.range_db,
                "key_filter": config.noise_gate_m.key_filter,
                "key_freq": config.noise_gate_m.key_freq,
                "fx_route": config.noise_gate_m.fx_route,
            },
            "tremolo": {
                "enabled": config.tremolo.enabled,
                "rate": config.tremolo.rate,
                "depth": config.tremolo.depth,
                "shape": config.tremolo.shape,
                "stereo_mode": config.tremolo.stereo_mode,
                "mix": config.tremolo.mix,
                "fx_route": config.tremolo.fx_route,
            },
            "tremolo_b": {
                "enabled": config.tremolo_b.enabled,
                "rate": config.tremolo_b.rate,
                "depth": config.tremolo_b.depth,
                "shape": config.tremolo_b.shape,
                "stereo_mode": config.tremolo_b.stereo_mode,
                "mix": config.tremolo_b.mix,
                "fx_route": config.tremolo_b.fx_route,
            },
            "tremolo_m": {
                "enabled": config.tremolo_m.enabled,
                "rate": config.tremolo_m.rate,
                "depth": config.tremolo_m.depth,
                "shape": config.tremolo_m.shape,
                "stereo_mode": config.tremolo_m.stereo_mode,
                "mix": config.tremolo_m.mix,
                "fx_route": config.tremolo_m.fx_route,
            },
            "auto_wah": {
                "enabled": config.auto_wah.enabled,
                "sensitivity": config.auto_wah.sensitivity,
                "peak": config.auto_wah.peak,
                "mode": config.auto_wah.mode,
                "filter_type": config.auto_wah.filter_type,
                "mix": config.auto_wah.mix,
                "fx_route": config.auto_wah.fx_route,
            },
            "auto_wah_b": {
                "enabled": config.auto_wah_b.enabled,
                "sensitivity": config.auto_wah_b.sensitivity,
                "peak": config.auto_wah_b.peak,
                "mode": config.auto_wah_b.mode,
                "filter_type": config.auto_wah_b.filter_type,
                "mix": config.auto_wah_b.mix,
                "fx_route": config.auto_wah_b.fx_route,
            },
            "auto_wah_m": {
                "enabled": config.auto_wah_m.enabled,
                "sensitivity": config.auto_wah_m.sensitivity,
                "peak": config.auto_wah_m.peak,
                "mode": config.auto_wah_m.mode,
                "filter_type": config.auto_wah_m.filter_type,
                "mix": config.auto_wah_m.mix,
                "fx_route": config.auto_wah_m.fx_route,
            },
            "fet_limiter": {
                "enabled": config.fet_limiter.enabled,
                "input_gain": config.fet_limiter.input_gain,
                "output_gain": config.fet_limiter.output_gain,
                "attack_idx": config.fet_limiter.attack_idx,
                "release_idx": config.fet_limiter.release_idx,
                "ratio_idx": config.fet_limiter.ratio_idx,
                "fx_route": config.fet_limiter.fx_route,
            },
            "fet_limiter_b": {
                "enabled": config.fet_limiter_b.enabled,
                "input_gain": config.fet_limiter_b.input_gain,
                "output_gain": config.fet_limiter_b.output_gain,
                "attack_idx": config.fet_limiter_b.attack_idx,
                "release_idx": config.fet_limiter_b.release_idx,
                "ratio_idx": config.fet_limiter_b.ratio_idx,
                "fx_route": config.fet_limiter_b.fx_route,
            },
            "fet_limiter_m": {
                "enabled": config.fet_limiter_m.enabled,
                "input_gain": config.fet_limiter_m.input_gain,
                "output_gain": config.fet_limiter_m.output_gain,
                "attack_idx": config.fet_limiter_m.attack_idx,
                "release_idx": config.fet_limiter_m.release_idx,
                "ratio_idx": config.fet_limiter_m.ratio_idx,
                "fx_route": config.fet_limiter_m.fx_route,
            },
            "rotary": {
                "enabled": config.rotary.enabled,
                "speed": config.rotary.speed,
                "acceleration": config.rotary.acceleration,
                "horn_level": config.rotary.horn_level,
                "rotor_level": config.rotary.rotor_level,
                "mix": config.rotary.mix,
                "fx_route": config.rotary.fx_route,
            },
            "rotary_b": {
                "enabled": config.rotary_b.enabled,
                "speed": config.rotary_b.speed,
                "acceleration": config.rotary_b.acceleration,
                "horn_level": config.rotary_b.horn_level,
                "rotor_level": config.rotary_b.rotor_level,
                "mix": config.rotary_b.mix,
                "fx_route": config.rotary_b.fx_route,
            },
            "rotary_m": {
                "enabled": config.rotary_m.enabled,
                "speed": config.rotary_m.speed,
                "acceleration": config.rotary_m.acceleration,
                "horn_level": config.rotary_m.horn_level,
                "rotor_level": config.rotary_m.rotor_level,
                "mix": config.rotary_m.mix,
                "fx_route": config.rotary_m.fx_route,
            },
            "exciter": {
                "enabled": config.exciter.enabled,
                "tune": config.exciter.tune,
                "mix": config.exciter.mix,
                "lo_tune": config.exciter.lo_tune,
                "lo_mix": config.exciter.lo_mix,
                "output": config.exciter.output,
                "fx_route": config.exciter.fx_route,
            },
            "exciter_b": {
                "enabled": config.exciter_b.enabled,
                "tune": config.exciter_b.tune,
                "mix": config.exciter_b.mix,
                "lo_tune": config.exciter_b.lo_tune,
                "lo_mix": config.exciter_b.lo_mix,
                "output": config.exciter_b.output,
                "fx_route": config.exciter_b.fx_route,
            },
            "exciter_m": {
                "enabled": config.exciter_m.enabled,
                "tune": config.exciter_m.tune,
                "mix": config.exciter_m.mix,
                "lo_tune": config.exciter_m.lo_tune,
                "lo_mix": config.exciter_m.lo_mix,
                "output": config.exciter_m.output,
                "fx_route": config.exciter_m.fx_route,
            },
            "transient_shaper": {
                "enabled": config.transient_shaper.enabled,
                "attack_shape": config.transient_shaper.attack_shape,
                "sustain_shape": config.transient_shaper.sustain_shape,
                "output": config.transient_shaper.output,
                "stereo_link": config.transient_shaper.stereo_link,
                "fx_route": config.transient_shaper.fx_route,
            },
            "transient_shaper_b": {
                "enabled": config.transient_shaper_b.enabled,
                "attack_shape": config.transient_shaper_b.attack_shape,
                "sustain_shape": config.transient_shaper_b.sustain_shape,
                "output": config.transient_shaper_b.output,
                "stereo_link": config.transient_shaper_b.stereo_link,
                "fx_route": config.transient_shaper_b.fx_route,
            },
            "transient_shaper_m": {
                "enabled": config.transient_shaper_m.enabled,
                "attack_shape": config.transient_shaper_m.attack_shape,
                "sustain_shape": config.transient_shaper_m.sustain_shape,
                "output": config.transient_shaper_m.output,
                "stereo_link": config.transient_shaper_m.stereo_link,
                "fx_route": config.transient_shaper_m.fx_route,
            },
            "harmonizer": {
                "enabled": config.harmonizer.enabled,
                "voice_a_semitones": config.harmonizer.voice_a_semitones,
                "voice_a_cents": config.harmonizer.voice_a_cents,
                "voice_a_delay_ms": config.harmonizer.voice_a_delay_ms,
                "voice_b_semitones": config.harmonizer.voice_b_semitones,
                "voice_b_cents": config.harmonizer.voice_b_cents,
                "voice_b_delay_ms": config.harmonizer.voice_b_delay_ms,
                "feedback": config.harmonizer.feedback,
                "mix": config.harmonizer.mix,
                "algorithm": config.harmonizer.algorithm,
                "fx_route": config.harmonizer.fx_route,
            },
            "harmonizer_b": {
                "enabled": config.harmonizer_b.enabled,
                "voice_a_semitones": config.harmonizer_b.voice_a_semitones,
                "voice_a_cents": config.harmonizer_b.voice_a_cents,
                "voice_a_delay_ms": config.harmonizer_b.voice_a_delay_ms,
                "voice_b_semitones": config.harmonizer_b.voice_b_semitones,
                "voice_b_cents": config.harmonizer_b.voice_b_cents,
                "voice_b_delay_ms": config.harmonizer_b.voice_b_delay_ms,
                "feedback": config.harmonizer_b.feedback,
                "mix": config.harmonizer_b.mix,
                "algorithm": config.harmonizer_b.algorithm,
                "fx_route": config.harmonizer_b.fx_route,
            },
            "harmonizer_m": {
                "enabled": config.harmonizer_m.enabled,
                "voice_a_semitones": config.harmonizer_m.voice_a_semitones,
                "voice_a_cents": config.harmonizer_m.voice_a_cents,
                "voice_a_delay_ms": config.harmonizer_m.voice_a_delay_ms,
                "voice_b_semitones": config.harmonizer_m.voice_b_semitones,
                "voice_b_cents": config.harmonizer_m.voice_b_cents,
                "voice_b_delay_ms": config.harmonizer_m.voice_b_delay_ms,
                "feedback": config.harmonizer_m.feedback,
                "mix": config.harmonizer_m.mix,
                "algorithm": config.harmonizer_m.algorithm,
                "fx_route": config.harmonizer_m.fx_route,
            },
            "stereo_widener": {
                "enabled": config.stereo_widener.enabled,
                "width": config.stereo_widener.width,
                "center": config.stereo_widener.center,
                "low_mono_freq": config.stereo_widener.low_mono_freq,
                "fx_route": config.stereo_widener.fx_route,
            },
            "stereo_widener_b": {
                "enabled": config.stereo_widener_b.enabled,
                "width": config.stereo_widener_b.width,
                "center": config.stereo_widener_b.center,
                "low_mono_freq": config.stereo_widener_b.low_mono_freq,
                "fx_route": config.stereo_widener_b.fx_route,
            },
            "stereo_widener_m": {
                "enabled": config.stereo_widener_m.enabled,
                "width": config.stereo_widener_m.width,
                "center": config.stereo_widener_m.center,
                "low_mono_freq": config.stereo_widener_m.low_mono_freq,
                "fx_route": config.stereo_widener_m.fx_route,
            },
            "freq_shifter": {
                "enabled": config.freq_shifter.enabled,
                "shift_hz": config.freq_shifter.shift_hz,
                "feedback": config.freq_shifter.feedback,
                "mix": config.freq_shifter.mix,
                "fx_route": config.freq_shifter.fx_route,
            },
            "freq_shifter_b": {
                "enabled": config.freq_shifter_b.enabled,
                "shift_hz": config.freq_shifter_b.shift_hz,
                "feedback": config.freq_shifter_b.feedback,
                "mix": config.freq_shifter_b.mix,
                "fx_route": config.freq_shifter_b.fx_route,
            },
            "freq_shifter_m": {
                "enabled": config.freq_shifter_m.enabled,
                "shift_hz": config.freq_shifter_m.shift_hz,
                "feedback": config.freq_shifter_m.feedback,
                "mix": config.freq_shifter_m.mix,
                "fx_route": config.freq_shifter_m.fx_route,
            },
            "ring_mod": {
                "enabled": config.ring_mod.enabled,
                "freq_hz": config.ring_mod.freq_hz,
                "shape": config.ring_mod.shape,
                "depth": config.ring_mod.depth,
                "mix": config.ring_mod.mix,
                "fx_route": config.ring_mod.fx_route,
            },
            "ring_mod_b": {
                "enabled": config.ring_mod_b.enabled,
                "freq_hz": config.ring_mod_b.freq_hz,
                "shape": config.ring_mod_b.shape,
                "depth": config.ring_mod_b.depth,
                "mix": config.ring_mod_b.mix,
                "fx_route": config.ring_mod_b.fx_route,
            },
            "ring_mod_m": {
                "enabled": config.ring_mod_m.enabled,
                "freq_hz": config.ring_mod_m.freq_hz,
                "shape": config.ring_mod_m.shape,
                "depth": config.ring_mod_m.depth,
                "mix": config.ring_mod_m.mix,
                "fx_route": config.ring_mod_m.fx_route,
            },
        }

        # Auto-serialize new effects (A / B / M) via dataclass vars
        for fx_name in ("simple_eq", "ping_pong_delay", "shimmer_reverb",
                        "lofi_radio", "auto_pan", "fuzz", "stutter",
                        "granular_freeze", "gate_reverb"):
            for sfx in ("", "_b", "_m"):
                key = f"{fx_name}{sfx}" if sfx else fx_name
                if not hasattr(config, key):
                    continue
                state = getattr(config, key)
                result[key] = {
                    k: v for k, v in vars(state).items() if not k.startswith("_")
                }

        # Per-bus C and D state — auto-serialize all dataclass fields
        _ALL_FX = (
            "delay", "tape_delay", "filter_a", "filter_b", "mod",
            "reel_to_reel", "cassette_tape", "vhs", "vinyl",
            "compressor", "hall_reverb", "plate_reverb",
            "glue_compressor", "bit_crusher", "tube_overdrive",
            "ether_drift", "noise_gate", "tremolo", "auto_wah",
            "fet_limiter", "rotary", "exciter", "transient_shaper",
            "harmonizer",
            "stereo_widener",
            "freq_shifter",
            "ring_mod",
            "simple_eq",
            "ping_pong_delay",
            "shimmer_reverb",
            "lofi_radio",
            "auto_pan",
            "fuzz",
            "stutter",
            "granular_freeze",
            "gate_reverb",
        )
        for suffix in ("_c", "_d"):
            for fx_base in _ALL_FX:
                cfg_key = f"{fx_base}{suffix}"
                if not hasattr(config, cfg_key):
                    continue
                state = getattr(config, cfg_key)
                result[cfg_key] = {
                    k: v for k, v in vars(state).items()
                    if not k.startswith('_')
                }

        # Slicer tool state
        result["slicer"] = {
            "cue_volume": config.slicer.cue_volume,
            "cue_device": config.slicer.cue_device,
            "cue_channel_offset": config.slicer.cue_channel_offset,
            "cue_mode": config.slicer.cue_mode,
            "preview_loop": config.slicer.preview_loop,
            "auto_chop_mode": config.slicer.auto_chop_mode,
            "naming_template": config.slicer.naming_template,
        }

        # Foreign Exchange (stowaway) plugin slots
        if hasattr(config, "stowaway_slots"):
            result["stowaway_slots"] = [
                s.to_dict() for s in config.stowaway_slots
            ]

        result["control_row"] = {
            "enabled": config.control_row.enabled,
            "monome_row": config.control_row.monome_row,
            "assignments": config.control_row.assignments,
        }

        result["grid_midi_cc_map"] = dict(config.grid_midi_cc_map)

        return result
