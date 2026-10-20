"""Plugin scanner — discovers VST3 and AU plugins on the system."""

import contextlib
import json
import logging
import platform
import tempfile
import os
import sys
import threading
import re as _re
from dataclasses import dataclass, asdict, fields as _dc_fields
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Windows native error dialog suppression
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def suppress_plugin_dialogs():
    """Suppress native Win32 error dialogs that VST3 plugins may spawn.

    On Windows this sets ``SetErrorMode`` flags that prevent the OS
    from showing crash / missing-DLL / critical-error message boxes
    when a plugin DLL is loaded.  The previous error mode is restored
    on exit.  On non-Windows platforms this is a no-op.
    """
    if sys.platform != "win32":
        yield
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32
    # SEM_FAILCRITICALERRORS  = 0x0001  — no critical-error dialog
    # SEM_NOGPFAULTERRORBOX   = 0x0002  — no GP-fault dialog
    # SEM_NOOPENFILEERRORBOX  = 0x8000  — no file-not-found dialog
    new_mode = 0x0001 | 0x0002 | 0x8000
    old_mode = kernel32.SetErrorMode(new_mode)
    # SetErrorMode returns previous mode; call again to OR in (not replace)
    kernel32.SetErrorMode(old_mode | new_mode)
    try:
        yield
    finally:
        kernel32.SetErrorMode(old_mode)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PluginInfo:
    """Metadata for a discovered audio plugin."""
    name: str
    vendor: str
    path: str
    fmt: str               # "vst3" | "au"
    is_instrument: bool = False
    category: str = ""     # VST3 subcategory string, e.g. "Fx|Delay"
    qt_conflict: bool = False  # True if plugin bundles Qt (crashes in Qt hosts)

    def __hash__(self):
        return hash((self.path, self.fmt))

    def __eq__(self, other):
        if not isinstance(other, PluginInfo):
            return NotImplemented
        return self.path == other.path and self.fmt == other.fmt


# ---------------------------------------------------------------------------
# Platform-specific default search paths
# ---------------------------------------------------------------------------

def get_default_scan_paths(fmt: str) -> list[Path]:
    """Return platform-specific default directories for a plugin format."""
    system = platform.system()
    home = Path.home()
    paths: list[Path] = []

    if fmt == "vst3":
        if system == "Windows":
            pf = os.environ.get("ProgramFiles", r"C:\Program Files")
            pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
            paths = [
                Path(pf) / "Common Files" / "VST3",
                Path(pf86) / "Common Files" / "VST3",
            ]
        elif system == "Darwin":
            paths = [
                Path("/Library/Audio/Plug-Ins/VST3"),
                home / "Library" / "Audio" / "Plug-Ins" / "VST3",
            ]
        else:  # Linux
            paths = [
                Path("/usr/lib/vst3"),
                Path("/usr/local/lib/vst3"),
                home / ".vst3",
            ]
    elif fmt == "au":
        if system == "Darwin":
            paths = [
                Path("/Library/Audio/Plug-Ins/Components"),
                home / "Library" / "Audio" / "Plug-Ins" / "Components",
            ]
        # AU is macOS only — return empty on other platforms

    return [p for p in paths if p.exists()]


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

_EXT_MAP = {"vst3": ".vst3", "au": ".component"}


def resolve_vst3_binary(path: str) -> str:
    """Resolve a VST3 path to the actual loadable binary.

    VST3 plugins come in two forms:
    - **Single file**: ``Plugin.vst3`` — a flat DLL/dylib; return as-is.
    - **Bundle directory**: ``Plugin.vst3/Contents/<arch>/Plugin.vst3``
      where ``<arch>`` is ``x86_64-win``, ``x86_64-linux``, etc.
      pedalboard cannot load the top-level directory; it needs the
      inner binary.

    Returns the resolved path (always a file, never a directory).
    """
    p = Path(path)
    if not p.is_dir():
        return path  # already a file

    # Look for the inner binary inside Contents/<arch>/
    contents = p / "Contents"
    if contents.is_dir():
        for arch_dir in contents.iterdir():
            if not arch_dir.is_dir():
                continue
            for candidate in arch_dir.glob("*.vst3"):
                if candidate.is_file():
                    return str(candidate)
    return path  # fallback — return original


def scan_plugins(
    formats: Optional[list[str]] = None,
    extra_paths: Optional[list[str]] = None,
    progress_callback=None,
) -> list[PluginInfo]:
    """Walk default + extra directories and probe each plugin file.

    Args:
        formats: List of format strings, e.g. ["vst3", "au"].
                 Defaults to platform-appropriate formats.
        extra_paths: Additional directories to search.
        progress_callback: Optional ``fn(found_so_far: int)`` called after each find.

    Returns:
        Deduplicated list of PluginInfo.
    """
    if formats is None:
        formats = ["vst3"]
        if platform.system() == "Darwin":
            formats.append("au")

    seen: set[str] = set()
    results: list[PluginInfo] = []

    for fmt in formats:
        ext = _EXT_MAP.get(fmt)
        if ext is None:
            continue
        dirs = get_default_scan_paths(fmt)
        if extra_paths:
            dirs += [Path(p) for p in extra_paths if Path(p).exists()]

        for search_dir in dirs:
            try:
                entries = list(search_dir.rglob(f"*{ext}"))
            except PermissionError:
                continue
            for entry in entries:
                # Skip files nested inside a .vst3 bundle directory
                try:
                    rel = entry.relative_to(search_dir)
                except ValueError:
                    continue
                if any(p.endswith(ext) for p in rel.parts[:-1]):
                    continue
                path_str = str(entry)
                if path_str in seen:
                    continue
                seen.add(path_str)
                info = _probe_plugin(path_str, fmt)
                if info is not None:
                    results.append(info)
                    if progress_callback:
                        progress_callback(len(results))

    return results


def _read_vst3_metadata(path: str) -> dict:
    """Extract metadata from a VST3 bundle's *moduleinfo.json*.

    Returns a dict with optional keys ``name``, ``vendor``,
    ``is_instrument``, ``category``.  Returns an empty dict when the
    file is missing or unreadable.
    """
    p = Path(path)
    if not p.is_dir():
        return {}
    for candidate in (
        p / "Contents" / "Resources" / "moduleinfo.json",
        p / "Contents" / "moduleinfo.json",
    ):
        if not candidate.exists():
            continue
        try:
            raw = candidate.read_text(encoding="utf-8")
            # moduleinfo.json often contains trailing commas (invalid JSON)
            raw = _re.sub(r",(\s*[}\]])", r"\1", raw)
            data = json.loads(raw)
        except Exception:
            continue
        result: dict = {}
        factory = data.get("Factory Info", {})
        if factory.get("Vendor"):
            result["vendor"] = factory["Vendor"]
        for cls in data.get("Classes", []):
            if cls.get("Category") == "Audio Module Class":
                if cls.get("Vendor") and "vendor" not in result:
                    result["vendor"] = cls["Vendor"]
                if cls.get("Name"):
                    result["name"] = cls["Name"]
                subcats = cls.get("Sub Categories", "")
                if isinstance(subcats, list):
                    subcats = "|".join(subcats)
                result["category"] = subcats
                result["is_instrument"] = "instrument" in subcats.lower()
                break
        return result
    return {}


# ---------------------------------------------------------------------------
# Qt-conflict detection
# ---------------------------------------------------------------------------

# File-name patterns that indicate a plugin ships its own Qt runtime.
_QT_DLL_PATTERNS = _re.compile(
    r"(qt[56]?(core|gui|qml|quick|widgets|network)"
    r"|libqt[56]?"
    r"|qt[56]?\.(dll|dylib|so))",
    _re.IGNORECASE,
)

# Vendors known to embed Qt/QML in their plugin UIs.
_QT_VENDORS = {
    "native instruments",
}


def _check_qt_conflict(path: str, vendor: str = "") -> bool:
    """Return *True* if a VST3 bundle likely ships its own Qt runtime.

    Three heuristics:
    1. Check the vendor name against a known list of Qt-embedding vendors.
    2. For flat .vst3 files, check if the parent directory matches a known vendor.
    3. Walk the bundle directory looking for Qt shared-library filenames.
    """
    if vendor and vendor.strip().lower() in _QT_VENDORS:
        return True

    p = Path(path)

    # For flat .vst3 files, the parent directory is often the vendor name
    # e.g. "Native Instruments/Guitar Rig 7.vst3"
    if not p.is_dir():
        parent_name = p.parent.name.strip().lower()
        if parent_name in _QT_VENDORS:
            return True
        return False

    try:
        for entry in p.rglob("*"):
            if entry.is_file() and _QT_DLL_PATTERNS.search(entry.name):
                return True
    except (PermissionError, OSError):
        pass
    return False


def _has_pedalboard() -> bool:
    """Return True if the pedalboard package is importable."""
    try:
        __import__('pedalboard')
        return True
    except ImportError:
        return False


_PROBE_TIMEOUT = 10  # seconds per plugin


def _probe_plugin(path: str, fmt: str, use_pedalboard: bool = False) -> Optional[PluginInfo]:
    """Try to extract name/vendor from a plugin.

    Reads VST3 bundle *moduleinfo.json* for vendor, category and
    instrument/effect classification.  Falls back to filename-based
    metadata when the file is absent.

    When *use_pedalboard* is True the plugin is also loaded for
    validation (with a timeout).  This is OFF by default.
    """
    if use_pedalboard and _has_pedalboard():
        try:
            info = _probe_with_pedalboard(path, fmt)
            if info is not None:
                return info
        except Exception as exc:
            log.debug("Pedalboard probe failed for %s: %s", path, exc)

    p = Path(path)
    name = p.stem
    vendor = "Unknown"
    is_instrument = False
    category = ""

    if fmt == "vst3":
        meta = _read_vst3_metadata(path)
        if meta:
            name = meta.get("name", name)
            vendor = meta.get("vendor", vendor)
            is_instrument = meta.get("is_instrument", False)
            category = meta.get("category", "")
        # For flat .vst3 files without moduleinfo, infer vendor from
        # the parent directory name (e.g. "Native Instruments", "Arturia")
        if vendor == "Unknown" and not p.is_dir():
            parent_name = p.parent.name
            # Only use parent if it's not a generic scan root like "VST3"
            if parent_name.lower() not in ("vst3", "common files", ""):
                vendor = parent_name

    qt_conflict = _check_qt_conflict(path, vendor) if fmt == "vst3" else False

    return PluginInfo(
        name=name, vendor=vendor, path=path, fmt=fmt,
        is_instrument=is_instrument, category=category,
        qt_conflict=qt_conflict,
    )


def _probe_with_pedalboard(path: str, fmt: str) -> Optional[PluginInfo]:
    """Load a plugin via pedalboard in a thread with a timeout.

    We only use pedalboard to *validate* the file is a real loadable plugin.
    Plugin name comes from the filename since pedalboard's __getattr__
    intercepts attribute access and triggers slow parameter enumeration.
    """
    outcome: dict = {}

    def _load():
        try:
            from pedalboard import load_plugin
            with suppress_plugin_dialogs():
                plugin = load_plugin(path)
            # Don't access plugin.name or .manufacturer — __getattr__ intercepts
            # those and triggers full parameter enumeration which can hang.
            del plugin
            outcome["ok"] = True
        except Exception as exc:
            outcome["error"] = exc

    # A plain daemon thread, not a ThreadPoolExecutor: leaving an executor's
    # `with` block waits for the worker, so a hung plugin defeated the
    # timeout, and its non-daemon worker would also block app exit.
    t = threading.Thread(target=_load, daemon=True, name="plugin-probe")
    t.start()
    t.join(_PROBE_TIMEOUT)
    if t.is_alive():
        log.warning("Plugin probe timed out for %s (>%ss) — skipped", path, _PROBE_TIMEOUT)
        return None
    if "error" in outcome:
        log.debug("Pedalboard probe error for %s: %s", path, outcome["error"])
        return None
    # Plugin loaded successfully — use filename for display
    return PluginInfo(
        name=Path(path).stem,
        vendor="(verified)",
        path=path,
        fmt=fmt,
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path() -> Path:
    from mlrvp.config import get_config_dir
    return get_config_dir() / "defaults" / "plugin_cache.json"


def load_cache() -> list[PluginInfo]:
    """Load cached plugin list from disk."""
    fp = _cache_path()
    if not fp.exists():
        return []
    try:
        with open(fp) as f:
            data = json.load(f)
        valid = {f.name for f in _dc_fields(PluginInfo)}
        return [PluginInfo(**{k: v for k, v in d.items() if k in valid}) for d in data]
    except Exception as exc:
        log.warning("Plugin cache load failed: %s", exc)
        return []


def save_cache(plugins: list[PluginInfo]) -> None:
    """Atomically write plugin cache to disk."""
    fp = _cache_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(fp.parent), suffix=".tmp"
        )
        with os.fdopen(tmp_fd, "w") as f:
            json.dump([asdict(p) for p in plugins], f, indent=2)
        os.replace(tmp_path, str(fp))
    except Exception as exc:
        log.error("Plugin cache save failed: %s", exc)
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Custom scan paths (user-configurable)
# ---------------------------------------------------------------------------

def _custom_paths_file() -> Path:
    from mlrvp.config import get_config_dir
    return get_config_dir() / "defaults" / "plugin_scan_paths.json"


def load_custom_scan_paths() -> list[str]:
    """Load user-configured extra scan directories."""
    fp = _custom_paths_file()
    if not fp.exists():
        return []
    try:
        with open(fp) as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(p) for p in data]
    except Exception as exc:
        log.warning("Custom scan paths load failed: %s", exc)
    return []


def save_custom_scan_paths(paths: list[str]) -> None:
    """Persist user-configured extra scan directories."""
    fp = _custom_paths_file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(fp, "w") as f:
            json.dump(paths, f, indent=2)
    except Exception as exc:
        log.error("Custom scan paths save failed: %s", exc)
