# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['mlrvp\\main.py'],
    pathex=[],
    binaries=[],
    datas=[('mlrvp/resources', 'mlrvp/resources')],
    hiddenimports=['sounddevice', 'soundfile', 'numpy', 'numba', 'llvmlite', 'llvmlite.binding', 'resampy', 'librosa', 'librosa.effects', 'scipy', 'scipy.signal', 'lazy_loader', 'PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'torch', 'torchaudio', 'torchvision', 'sympy', 'IPython', 'notebook', 'pytest', 'sklearn', 'pandas', 'numba.np.ufunc.tbbpool'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='mlrVp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['c:\\PythonProjects\\MLRVP\\mlrvp\\resources\\icon.ico'],
)
