# -*- coding: utf-8 -*-
"""Đóng gói ThucXuatTool-Setup.exe (bộ cài một chạm).

Cách dùng:
    py -3.12 build_installer.py            # build app rồi build bộ cài
    py -3.12 build_installer.py --no-app   # dùng lại dist\\ThucXuatTool đã có

Kết quả: dist\\ThucXuatTool-Setup.exe
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
APP = DIST / "ThucXuatTool"
BUILD = ROOT / "build"
PAYLOAD_STAGE = BUILD / "payload_app"
PAYLOAD_ZIP = BUILD / "payload.zip"
DOCS = ("HUONG-DAN-NGUOI-DUNG.txt", "Chay-ThucXuatTool.bat", "config.json.example")
SKIP = {"config.json", "KetQua", ".write-test"}


def run(cmd: list[str]) -> None:
  print(">>", " ".join(cmd))
  subprocess.run(cmd, cwd=ROOT, check=True)


def stage_payload() -> None:
  """Copy app sạch (bỏ config.json/KetQua của máy dev) rồi nén thành payload.zip."""
  for name in DOCS:
    source = ROOT / name
    if source.exists():
      shutil.copy2(source, APP / name)
  if not (APP / "ThucXuatTool.exe").exists():
    raise SystemExit(f"Thiếu {APP / 'ThucXuatTool.exe'} — chạy lại không có --no-app.")

  if PAYLOAD_STAGE.exists():
    shutil.rmtree(PAYLOAD_STAGE)
  shutil.copytree(
    APP,
    PAYLOAD_STAGE,
    ignore=shutil.ignore_patterns(*SKIP, "*.log"),
  )

  if PAYLOAD_ZIP.exists():
    PAYLOAD_ZIP.unlink()
  with zipfile.ZipFile(PAYLOAD_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for path in sorted(PAYLOAD_STAGE.rglob("*")):
      if path.is_file():
        zf.write(path, path.relative_to(PAYLOAD_STAGE).as_posix())
  print(f"payload.zip: {PAYLOAD_ZIP.stat().st_size / 1024 / 1024:.1f} MB")


def main(argv: list[str]) -> int:
  if "--no-app" not in argv:
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "ThucXuatTool.spec"])
  stage_payload()
  run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "installer.spec"])

  out = DIST / "ThucXuatTool-Setup.exe"
  if not out.exists():
    raise SystemExit("Không thấy dist\\ThucXuatTool-Setup.exe")
  print(f"OK: {out}  ({out.stat().st_size / 1024 / 1024:.1f} MB)")
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
