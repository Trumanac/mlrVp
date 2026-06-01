#!/usr/bin/env python3
"""Package mlrVp for distribution to testers.

Creates:
  release/mlrVp-<ver>-windows.zip   — PyInstaller build (Windows only)
  release/mlrVp-<ver>-source.zip    — Source package (macOS / Linux)

Usage:
    python package.py              Package source only
    python package.py --windows    Build Windows exe + package it
    python package.py --all        Build exe + source package
"""

import os
import sys
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
RELEASE_DIR = ROOT / "release"

# Read the canonical version from the package itself (single source of truth)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("mlrvp", ROOT / "mlrvp" / "__init__.py")
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
VERSION = _mod.__version__

# Files/dirs to include in the source package
SOURCE_INCLUDE = [
    "mlrvp",
    "data",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "run.bat",
    "run.sh",
    "run.command",
    "generate_icon.py",
    "build.py",
    "mlrvp.spec",
]

# Patterns/names to exclude from source package (never bundle user-data dirs)
SOURCE_EXCLUDE_NAMES = {
    "__pycache__",
    ".egg-info",
}
SOURCE_EXCLUDE_EXTS = {".pyc", ".pyo"}
# data/ subdirs that contain user-specific files should be shipped empty
DATA_EXCLUDE_SUBDIRS = {"presets", "setlists", "slices", "songs"}


def make_source_zip():
    """Create a source distribution zip for macOS/Linux testers."""
    RELEASE_DIR.mkdir(exist_ok=True)
    zip_name = f"mlrVp-{VERSION}-source.zip"
    zip_path = RELEASE_DIR / zip_name

    print(f"Creating {zip_name}...")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in SOURCE_INCLUDE:
            item_path = ROOT / item
            if not item_path.exists():
                print(f"  Skipping (not found): {item}")
                continue
            if item_path.is_file():
                zf.write(item_path, f"mlrVp-{VERSION}/{item}")
            elif item_path.is_dir():
                for root, dirs, files in os.walk(item_path):
                    root_path = Path(root)
                    # Skip cache / build artifact dirs
                    dirs[:] = [
                        d for d in dirs
                        if not any(d == ex or d.endswith(ex) for ex in SOURCE_EXCLUDE_NAMES)
                    ]
                    # Skip user-data subdirs of data/ to avoid leaking personal paths
                    if item == "data":
                        rel_root = root_path.relative_to(ROOT / "data")
                        parts = rel_root.parts
                        if parts and parts[0] in DATA_EXCLUDE_SUBDIRS:
                            dirs.clear()
                            continue
                    for f in files:
                        if Path(f).suffix in SOURCE_EXCLUDE_EXTS:
                            continue
                        full = root_path / f
                        rel = full.relative_to(ROOT)
                        zf.write(full, f"mlrVp-{VERSION}/{rel}")

    print(f"Created {zip_path} ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return zip_path


def make_windows_zip():
    """Build exe with PyInstaller and zip the output."""
    import subprocess

    dist_dir = ROOT / "dist" / "mlrVp"

    # Build if not already built
    if not dist_dir.exists() or not (dist_dir / "mlrVp.exe").exists():
        print("Building Windows executable...")
        subprocess.run([sys.executable, "build.py"], check=True, cwd=str(ROOT))

    if not (dist_dir / "mlrVp.exe").exists():
        print("ERROR: Build failed — mlrVp.exe not found")
        sys.exit(1)

    RELEASE_DIR.mkdir(exist_ok=True)
    zip_name = f"mlrVp-{VERSION}-windows.zip"
    zip_path = RELEASE_DIR / zip_name

    print(f"Creating {zip_name}...")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(dist_dir):
            for f in files:
                full = Path(root) / f
                rel = full.relative_to(dist_dir)
                zf.write(full, f"mlrVp/{rel}")

    print(f"Created {zip_path} ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return zip_path


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--all" in args:
        make_windows_zip()
        make_source_zip()
    elif "--windows" in args:
        make_windows_zip()
    else:
        make_source_zip()

    print("\nDone! Check the release/ folder.")
