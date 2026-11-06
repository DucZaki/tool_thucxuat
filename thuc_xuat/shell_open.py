# -*- coding: utf-8 -*-
"""Mở thư mục chứa file trên Windows/macOS/Linux."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from thuc_xuat.proc import popen


def reveal_in_folder(path: str | Path) -> None:
  """Mở Explorer/Finder tới file (Windows: chọn sẵn file)."""
  target = Path(path).resolve()
  if not target.exists():
    target = target.parent
  if not target.parent.exists() and not target.is_dir():
    return

  try:
    if os.name == "nt":
      if target.is_file():
        popen(["explorer", "/select,", str(target)])
      else:
        popen(["explorer", str(target)])
      return

    if sys.platform == "darwin":
      popen(["open", "-R", str(target)])
      return

    folder = target if target.is_dir() else target.parent
    popen(["xdg-open", str(folder)])
  except OSError:
    # Không mở được Explorer không phải lỗi chặn công việc.
    pass


def reveal_outputs(paths: list[str | Path]) -> None:
  """Mở vị trí file kết quả (nhiều file cùng thư mục → mở 1 lần)."""
  resolved = [Path(p).resolve() for p in paths if Path(p).exists()]
  if not resolved:
    return
  parents = {p.parent for p in resolved}
  if len(resolved) == 1 or len(parents) == 1:
    reveal_in_folder(resolved[0])
    return
  for p in resolved:
    reveal_in_folder(p)
