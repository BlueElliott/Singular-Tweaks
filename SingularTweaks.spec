# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import get_module_collection_mode
from PyInstaller.building.api import EXE, PYZ, Analysis

a = Analysis(
    ['singular_tweaks/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
    ('static', 'static'),  # ✅ static/ is in root, not in singular_tweaks/
    ('README.md', '.'),
],
    hiddenimports=['uvicorn.logging', 'fastapi'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SingularTweaks',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)