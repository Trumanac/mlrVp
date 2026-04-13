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
VERSION = "0.6.9.13"
RELEASE_DIR = ROOT / "release"

# Files/dirs to include in the source package
SOURCE_INCLUDE = [
    "mlrvp",
    "data",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    "run.bat",
    "run.sh",
    "run.command",
    "generate_icon.py",
    "build.py",
    "mlrvp.spec",
]

# Patterns to exclude from source package
SOURCE_EXCLUDE = {
    "__pycache__",
    ".pyc",
    ".pyo",
    ".egg-info",
}


def make_source_zip():
    """Create a source distribution zip for macOS/Linux testers."""
    RELEASE_DIR.mkdir(exist_ok=True)
    zip_name = f"mlrVp-{VERSION}-source.zip"
    zip_path = RELEASE_DIR / zip_name

    print(f"Creating {zip_name}...")
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in SOURCE_INCLUDE:
            item_path = ROOT / item
            if item_path.is_file():
                zf.write(item_path, f"mlrVp-{VERSION}/{item}")
            elif item_path.is_dir():
                for root, dirs, files in os.walk(item_path):
                    # Skip excluded directories
                    dirs[:] = [d for d in dirs if d not in SOURCE_EXCLUDE]
                    for f in files:
                        if any(f.endswith(ext) for ext in SOURCE_EXCLUDE):
                            continue
                        full = Path(root) / f
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
