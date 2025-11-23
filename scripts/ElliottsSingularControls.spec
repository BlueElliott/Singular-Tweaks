# -*- mode: python ; coding: utf-8 -*-
import os
import sys

try:
    from PyInstaller.building.build_main import Analysis
    from PyInstaller.building.api import EXE, PYZ
except ImportError:
    from PyInstaller.building.api import Analysis, EXE, PYZ

# Get the repo root (parent of scripts/ folder where this spec file lives)
# SPECPATH is the full path to this .spec file
REPO_ROOT = os.path.dirname(os.path.dirname(SPECPATH))

# Get version from package
sys.path.insert(0, REPO_ROOT)
try:
    from elliotts_singular_controls import __version__
    VERSION = __version__
except:
    VERSION = "1.0.15"

# Build data files list
datas = []
static_path = os.path.join(REPO_ROOT, 'static')
readme_path = os.path.join(REPO_ROOT, 'README.md')
if os.path.exists(static_path):
    datas.append((static_path, 'static'))
if os.path.exists(readme_path):
    datas.append((readme_path, '.'))

a = Analysis(
    [os.path.join(REPO_ROOT, 'elliotts_singular_controls/__main__.py')],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'tkinter',
        'tkinter.scrolledtext',
        'pystray',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'psutil',
    ],
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
    name=f'ElliottsSingularControls-{VERSION}',
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
    icon=os.path.join(REPO_ROOT, 'static/esc_icon.ico'),
)