#!/usr/bin/env python3
"""Build script for mlrVp — creates standalone executables and installers.

Usage:
    python build.py              Build standalone executable (folder)
    python build.py --onefile    Build single-file executable
    python build.py --installer  Build executable + Windows installer (requires Inno Setup)
"""

import os
import re
import subprocess
import sys
import shutil
import platform
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"
SPEC = ROOT / "mlrvp.spec"
ICON_ICO = ROOT / "mlrvp" / "resources" / "icon.ico"


def read_version() -> str:
    """Single source of truth for the version: mlrvp/__init__.py."""
    import re
    text = (ROOT / "mlrvp" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


LICENSES_DIR = ROOT / "licenses"
NOTICES = ROOT / "THIRD_PARTY_LICENSES.txt"   # generated, gitignored
_LICENSE_FILE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE)[^/]*$", re.I)
_QT_NOTICE = """\
Qt and Qt for Python (PySide6, shiboken6) are licensed under the GNU Lesser
General Public License v3 (LGPL-3.0-only; also available under GPL-2.0/3.0).
Full texts: licenses/LGPL-3.0.txt and licenses/GPL-3.0.txt.
The Qt libraries are shipped as separate shared libraries in this folder and
may be replaced with compatible versions you build yourself.
Source code: https://code.qt.io/cgit/pyside/pyside-setup.git/ and
https://download.qt.io/official_releases/qt/
"""


def _bundled_distributions() -> list[str]:
    """Names of the packages a release bundles: everything in the lock file."""
    names = []
    for line in (ROOT / "requirements-lock.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)==", line)
        if m:
            names.append(m.group(1))
    return names


def write_third_party_licenses(path: Path = NOTICES) -> Path:
    """Write the licenses of Python and every bundled package (that is
    installed in this environment) into one notices file."""
    from importlib import metadata

    rule = "=" * 78
    parts = [
        "mlrVp bundles the following third-party software. Each component is\n"
        "distributed under its own license, reproduced below.\n",
    ]
    py_license = Path(sys.base_prefix) / "LICENSE.txt"
    parts.append(f"{rule}\nPython {platform.python_version()} (PSF-2.0)\n{rule}\n")
    if py_license.is_file():
        parts.append(py_license.read_text(encoding="utf-8", errors="replace"))
    for name in sorted(set(_bundled_distributions()), key=str.lower):
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue    # e.g. a platform-specific package for another OS
        meta = dist.metadata
        lic = meta.get("License-Expression") or ", ".join(
            c.split(" :: ")[-1] for c in meta.get_all("Classifier") or []
            if c.startswith("License ::"))
        if not lic:
            first = (meta.get("License") or "").strip().splitlines()
            lic = first[0][:60] if first else ""
        url = meta.get("Home-page") or next(
            (u.split(",", 1)[-1].strip() for u in meta.get_all("Project-URL") or []), "")
        parts.append(f"\n{rule}\n{meta['Name']} {dist.version}  ({lic or 'see below'})\n"
                     f"{url}\n{rule}\n")
        if name.lower().startswith(("pyside6", "shiboken6")):
            parts.append(_QT_NOTICE)
            continue
        texts = []
        for f in dist.files or []:
            if _LICENSE_FILE.search(str(f)):
                try:
                    texts.append(f"\n--- {f.name} ---\n"
                                 + Path(dist.locate_file(f)).read_text(encoding="utf-8",
                                                                       errors="replace"))
                except OSError:
                    pass
        # No license file in the wheel: use the license text from its metadata
        parts.extend(texts or [meta.get("License") or ""])
    path.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {path}")
    return path


def copy_license_files(dest: Path) -> None:
    """Put mlrVp's license and the third-party notices next to the executable."""
    shutil.copy2(ROOT / "LICENSE", dest / "LICENSE.txt")
    shutil.copy2(NOTICES, dest / NOTICES.name)
    shutil.copytree(LICENSES_DIR, dest / "licenses", dirs_exist_ok=True)


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
    write_third_party_licenses()
    cmd = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(SPEC)]
    print(f"Building mlrVp for {platform.system()}...")
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    out = DIST / "mlrVp"
    if platform.system() != "Darwin":     # macOS: bundled in Contents/Resources
        copy_license_files(out)
    print(f"\nBuild complete! Output in {out}")
    return out


def build_onefile():
    """Build as a single executable file."""
    ensure_pyinstaller()
    write_third_party_licenses()
    args = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", "mlrVp",
        "--add-data", f"mlrvp/resources{os.pathsep}mlrvp/resources",
        "--add-data", f"LICENSE{os.pathsep}.",
        "--add-data", f"{NOTICES.name}{os.pathsep}.",
        "--add-data", f"licenses{os.pathsep}licenses",
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

    # Always regenerate: the script embeds the version (and is gitignored)
    iss_path = ROOT / "installer.iss"
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
AppVersion={read_version()}
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
