# -*- coding: utf-8 -*-
"""Mở thư mục chứa file trên Windows/macOS/Linux."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def reveal_in_folder(path: str | Path) -> None:
  """Mở Explorer/Finder tới file (Windows: chọn sẵn file)."""
  target = Path(path).resolve()
  if not target.exists():
    target = target.parent
  if not target.parent.exists() and not target.is_dir():
    return

  if os.name == "nt":
    if target.is_file():
      subprocess.Popen(["explorer", "/select,", str(target)])
    else:
      subprocess.Popen(["explorer", str(target)])
    return

  if sys.platform == "darwin":
    subprocess.Popen(["open", "-R", str(target)])
    return

  folder = target if target.is_dir() else target.parent
  subprocess.Popen(["xdg-open", str(folder)])


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
