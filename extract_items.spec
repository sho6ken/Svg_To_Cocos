# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

# PyInstaller 執行 spec 時會注入 SPEC（本 spec 檔的絕對路徑）
SPEC_ROOT = Path(SPEC).parent.resolve()

a = Analysis(
    [str(SPEC_ROOT / "extract_items.py")],
    pathex=[str(SPEC_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=["lxml", "lxml.etree", "PIL", "PIL.Image"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="extract_items",
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
