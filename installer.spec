# -*- mode: python ; coding: utf-8 -*-
"""Spec cho bộ cài một chạm ThucXuatTool-Setup.exe.

Chứa payload.zip (toàn bộ app đã build) và mã installer.py.
Build: python build_installer.py
"""
from pathlib import Path

root = Path(SPECPATH)
payload = root / "build" / "payload.zip"

if not payload.exists():
    raise SystemExit(f"Thiếu {payload} — chạy build_installer.py (không có --no-app).")

a = Analysis(
    ["installer.py"],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(payload), ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["numpy", "cv2", "PIL", "openpyxl", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ThucXuatTool-Setup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
