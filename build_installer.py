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
PAYLOAD_ZIP = BUILD / "payload.zip"
DOCS = (
    "HUONG-DAN-NGUOI-DUNG.txt",
    "Chay-ThucXuatTool.vbs",
    "Chay-ThucXuatTool.bat",
    "config.json.example",
)
SKIP = {"config.json", "KetQua", ".write-test"}


def run(cmd: list[str]) -> None:
  print(">>", " ".join(cmd))
  subprocess.run(cmd, cwd=ROOT, check=True)


def stage_payload() -> None:
  """Nén trực tiếp app đã build, bỏ cấu hình và dữ liệu của máy dev."""
  if not (APP / "ThucXuatTool.exe").is_file():
    raise SystemExit(f"Thiếu {APP / 'ThucXuatTool.exe'} — chạy lại không có --no-app.")
  for name in DOCS:
    source = ROOT / name
    if source.exists():
      shutil.copy2(source, APP / name)
  BUILD.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(PAYLOAD_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for path in sorted(APP.rglob("*")):
      rel = path.relative_to(APP)
      if path.is_file() and not any(part in SKIP or part.endswith(".log") for part in rel.parts):
        zf.write(path, rel.as_posix())
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
