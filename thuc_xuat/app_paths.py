# -*- coding: utf-8 -*-
"""Đường dẫn thư mục app — dev vs bản .exe đóng gói."""
from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
  """Thư mục chứa app.py / ThucXuatTool.exe (nơi ghi config.json)."""
  if getattr(sys, "frozen", False):
    return Path(sys.executable).resolve().parent
  return Path(__file__).resolve().parent.parent
