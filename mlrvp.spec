# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for mlrVp.

Build with:
    pyinstaller mlrvp.spec

Produces a self-contained executable for Windows, macOS, or Linux.
"""

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['mlrvp/main.py'],
    pathex=[],
    binaries=[],
    datas=[('mlrvp/resources', 'mlrvp/resources')],
    hiddenimports=[
        'sounddevice',
        'soundfile',
        'numpy',
        'resampy',
        'librosa',
        'librosa.effects',
        'scipy',
        'scipy.signal',
        'lazy_loader',
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib',
        'torch', 'torchaudio', 'torchvision',
        'sympy', 'IPython', 'notebook', 'pytest',
        'sklearn', 'pandas', 'numba',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='mlrVp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='mlrvp/resources/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='mlrVp',
)

# macOS app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='mlrVp.app',
        icon='mlrvp/resources/icon.icns',
        bundle_identifier='org.monome-community.mlrvp',
        info_plist={
            'CFBundleName': 'mlrVp',
            'CFBundleDisplayName': 'mlrVp',
            'CFBundleVersion': '0.6.9',
            'CFBundleShortVersionString': '0.6.9',
            'NSHighResolutionCapable': True,
            'NSMicrophoneUsageDescription':
                'mlrVp needs microphone access for audio recording.',
        },
    )
