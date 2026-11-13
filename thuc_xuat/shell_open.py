# -*- coding: utf-8 -*-
"""Mở thư mục chứa file trên Windows/macOS/Linux."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from thuc_xuat.proc import popen


def _target_dir(path: str | Path) -> tuple[Path | None, Path | None]:
  """Trả (thư mục cần mở, file cần chọn sẵn hoặc None)."""
  try:
    target = Path(path).expanduser().resolve()
  except OSError:
    return None, None
  if not target.exists():
    target = target.parent
  if not target.exists():
    return None, None
  if target.is_dir():
    return target, None
  if target.parent.is_dir():
    return target.parent, target
  return None, None


def _open_folder_windows(folder: Path) -> bool:
  """Mở thư mục bằng shell của Windows; thử lần lượt 2 cách."""
  # 1) ShellExecute — cách chuẩn của Windows, không phụ thuộc PATH/explorer.exe.
  try:
    os.startfile(str(folder))  # type: ignore[attr-defined]
    return True
  except OSError:
    pass
  # 2) Dự phòng: gọi thẳng explorer.exe (bản Windows tối giản thiếu shell).
  for exe in ("explorer", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "explorer.exe")):
    try:
      popen([exe, str(folder)])
      return True
    except OSError:
      continue
  return False


def _select_file_windows(target: Path) -> bool:
  """Mở thư mục cha và chọn sẵn file (explorer /select,...)."""
  for exe in ("explorer", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "explorer.exe")):
    try:
      popen([exe, "/select,", str(target)])
      return True
    except OSError:
      continue
  return False


def reveal_in_folder(path: str | Path) -> bool:
  """Mở Explorer/Finder tới file (Windows: chọn sẵn file). True = đã mở."""
  folder, selected = _target_dir(path)
  if folder is None:
    return False

  try:
    if os.name == "nt":
      if selected is not None and _select_file_windows(selected):
        return True
      return _open_folder_windows(folder)

    if sys.platform == "darwin":
      if selected is not None:
        popen(["open", "-R", str(selected)])
      else:
        popen(["open", str(folder)])
      return True

    popen(["xdg-open", str(folder)])
    return True
  except OSError:
    # Không mở được Explorer không phải lỗi chặn công việc.
    return False


def reveal_outputs(paths: list[str | Path]) -> bool:
  """Mở vị trí file kết quả (nhiều file cùng thư mục → mở 1 lần)."""
  resolved = [Path(p).resolve() for p in paths if Path(p).exists()]
  if not resolved:
    return False
  parents = {p.parent for p in resolved}
  if len(resolved) == 1 or len(parents) == 1:
    return reveal_in_folder(resolved[0])
  opened = False
  for p in resolved:
    opened = reveal_in_folder(p) or opened
  return opened
