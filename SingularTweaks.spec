# -*- mode: python ; coding: utf-8 -*-
import os

try:
    from PyInstaller.building.build_main import Analysis
    from PyInstaller.building.api import EXE, PYZ
except ImportError:
    from PyInstaller.building.api import Analysis, EXE, PYZ

# Build data files list
datas = []
if os.path.exists('static'):
    datas.append(('static', 'static'))
if os.path.exists('README.md'):
    datas.append(('README.md', '.'))

a = Analysis(
    ['singular_tweaks/__main__.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on'],
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