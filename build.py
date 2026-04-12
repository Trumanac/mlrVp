#!/usr/bin/env python3
"""Build script for mlrVp — creates standalone executables and installers.

Usage:
    python build.py              Build standalone executable (folder)
    python build.py --onefile    Build single-file executable
    python build.py --installer  Build executable + Windows installer (requires Inno Setup)
"""

import os
import subprocess
import sys
import shutil
import platform
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
SPEC = ROOT / "mlrvp.spec"
ICON_ICO = ROOT / "mlrvp" / "resources" / "icon.ico"


def ensure_pyinstaller():
    """Make sure PyInstaller is installed."""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("Installing PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller>=6.0"])


def build_folder():
    """Build using the spec file (folder mode)."""
    ensure_pyinstaller()
    cmd = [sys.executable, "-m", "PyInstaller", "--clean", str(SPEC)]
    print(f"Building mlrVp for {platform.system()}...")
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    out = DIST / "mlrVp"
    print(f"\nBuild complete! Output in {out}")
    return out


def build_onefile():
    """Build as a single executable file."""
    ensure_pyinstaller()
    args = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "mlrVp",
        "--add-data", f"mlrvp/resources{os.pathsep}mlrvp/resources",
        "--hidden-import", "sounddevice",
        "--hidden-import", "soundfile",
        "--hidden-import", "numpy",
        "--hidden-import", "numba",
        "--hidden-import", "llvmlite",
        "--hidden-import", "llvmlite.binding",
        "--hidden-import", "resampy",
        "--hidden-import", "librosa",
        "--hidden-import", "librosa.effects",
        "--hidden-import", "scipy",
        "--hidden-import", "scipy.signal",
        "--hidden-import", "lazy_loader",
        "--hidden-import", "PySide6",
        "--hidden-import", "PySide6.QtCore",
        "--hidden-import", "PySide6.QtGui",
        "--hidden-import", "PySide6.QtWidgets",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "torch",
        "--exclude-module", "torchaudio",
        "--exclude-module", "torchvision",
        "--exclude-module", "sympy",
        "--exclude-module", "IPython",
        "--exclude-module", "notebook",
        "--exclude-module", "pytest",
        "--exclude-module", "sklearn",
        "--exclude-module", "pandas",
        "--exclude-module", "numba.np.ufunc.tbbpool",
    ]
    if ICON_ICO.exists():
        args.extend(["--icon", str(ICON_ICO)])
    args.append("mlrvp/main.py")
    print(f"Building mlrVp (onefile) for {platform.system()}...")
    subprocess.run(args, check=True, cwd=str(ROOT))
    if platform.system() == "Windows":
        print(f"\nBuild complete! Output: {DIST / 'mlrVp.exe'}")
    else:
        print(f"\nBuild complete! Output: {DIST / 'mlrVp'}")


def build_installer():
    """Build + create Inno Setup Windows installer."""
    build_folder()

    iss_path = ROOT / "installer.iss"
    if not iss_path.exists():
        print("Generating Inno Setup script...")
        generate_inno_script(iss_path)

    iscc = shutil.which("ISCC")
    if not iscc:
        candidates = [
            Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
            Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        ]
        for c in candidates:
            if c.exists():
                iscc = str(c)
                break

    if iscc:
        print("Creating Windows installer with Inno Setup...")
        subprocess.run([iscc, str(iss_path)], check=True, cwd=str(ROOT))
        print(f"\nInstaller created in {ROOT / 'Output'}")
    else:
        print("\nInno Setup not found. Install from https://jrsoftware.org/isinfo.php")
        print(f"Then run: ISCC {iss_path}")


def generate_inno_script(path: Path):
    """Generate an Inno Setup script for Windows installer."""
    icon_line = ""
    if ICON_ICO.exists():
        icon_line = f"SetupIconFile={ICON_ICO}"

    content = f"""; mlrVp Inno Setup Script
[Setup]
AppName=mlrVp
AppVersion=0.6.9
AppPublisher=monome community
AppPublisherURL=https://github.com/trentgill/mlrv2
DefaultDirName={{autopf}}\\mlrVp
DefaultGroupName=mlrVp
OutputBaseFilename=mlrVp-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
{icon_line}
UninstallDisplayIcon={{app}}\\mlrVp.exe

[Files]
Source: "dist\\mlrVp\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{{group}}\\mlrVp"; Filename: "{{app}}\\mlrVp.exe"
Name: "{{autodesktop}}\\mlrVp"; Filename: "{{app}}\\mlrVp.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{{app}}\\mlrVp.exe"; Description: "Launch mlrVp"; Flags: nowait postinstall skipifsilent
"""
    path.write_text(content, encoding="utf-8")
    print(f"Generated {path}")


if __name__ == "__main__":
    if "--onefile" in sys.argv:
        build_onefile()
    elif "--installer" in sys.argv:
        build_installer()
    else:
        build_folder()
