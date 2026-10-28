# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — chạy: py -3.12 -m PyInstaller ThucXuatTool.spec"""
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = []
binaries = []
hiddenimports = [
  "tkinter",
  "tkinter.ttk",
  "tkinter.filedialog",
  "tkinter.messagebox",
  "openpyxl",
  "PIL",
  "PIL.Image",
  "PIL.ImageOps",
  "requests",
  "cv2",
  "numpy",
]

for pkg in ("certifi", "cv2", "numpy"):
  tmp = collect_all(pkg)
  datas += tmp[0]
  binaries += tmp[1]
  hiddenimports += tmp[2]

a = Analysis(
  ["app.py"],
  pathex=[],
  binaries=binaries,
  datas=datas,
  hiddenimports=hiddenimports,
  hookspath=[],
  hooksconfig={},
  runtime_hooks=[],
  excludes=["matplotlib", "pandas", "pytest"],
  noarchive=False,
  optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
  pyz,
  a.scripts,
  [],
  exclude_binaries=True,
  name="ThucXuatTool",
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
  icon=None,
)
coll = COLLECT(
  exe,
  a.binaries,
  a.datas,
  strip=False,
  upx=True,
  upx_exclude=[],
  name="ThucXuatTool",
)
