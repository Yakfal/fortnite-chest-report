# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for FortniteGrindSuite.exe.

Build with:
    pyinstaller --noconfirm --clean FortniteGrindSuite.spec

or, which also stages the release folder:
    python build.py --portable

What is inside the exe:
  * every Python module (launcher, overlay, onboarding, the six analysis modules, fc_core)
  * Tkinter, collected explicitly because the modules import it lazily
  * the 70 MB replay reader, so a single file is genuinely enough for a fresh machine

Set FC_BUNDLE_READER=0 to leave the reader outside the exe (13 MB exe, reader folder beside it).
"""

import os
from pathlib import Path

BASE = Path(SPECPATH) if "SPECPATH" in globals() else Path(os.getcwd())
READER = BASE / "reader" / "ChestReplayReader.exe"
KNOWN = BASE / "reader" / "cofres_conocidos.txt"
BUNDLE_READER = os.environ.get("FC_BUNDLE_READER", "1") != "0"

binaries = []
if BUNDLE_READER and READER.exists():
    binaries.append((str(READER), "reader"))

datas = []
if KNOWN.exists():
    datas.append((str(KNOWN), "reader"))

hidden = [
    "tkinter", "tkinter.ttk", "tkinter.messagebox", "tkinter.filedialog",
    "onboarding", "overlay", "launcher",
    "efficiency", "forecast", "loadout", "spatial", "mode", "quit", "archive", "fc_core",
    # imported lazily inside functions, so PyInstaller cannot see them by itself
    "csv", "zipfile", "subprocess", "ctypes", "json", "statistics", "threading",
]

a = Analysis(
    ["launcher.py"],
    pathex=[str(BASE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "pandas", "matplotlib", "PIL", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FortniteGrindSuite",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no command prompt window, ever
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
