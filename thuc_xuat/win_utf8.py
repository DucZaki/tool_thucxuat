# -*- coding: utf-8 -*-
"""Ép UTF-8 trên Windows — tránh lỗi charmap với tiếng Việt."""
from __future__ import annotations

import os
import sys


def fix_stdio_utf8() -> None:
  """sys.stdout/stderr trên Windows hay là cp1252 → crash khi in Unicode."""
  os.environ.setdefault("PYTHONIOENCODING", "utf-8")
  os.environ.setdefault("PYTHONUTF8", "1")

  for name in ("stdout", "stderr"):
    stream = getattr(sys, name, None)
    if stream is None:
      continue
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
      try:
        reconfigure(encoding="utf-8", errors="replace")
      except Exception:
        pass


def safe_text(exc: BaseException | str) -> str:
  """Chuỗi hiển thị an toàn cho messagebox."""
  if isinstance(exc, BaseException):
    msg = str(exc)
  else:
    msg = str(exc)
  try:
    msg.encode("utf-8")
  except Exception:
    msg = repr(msg)
  if "charmap" in msg.lower() or "codec can't encode" in msg.lower():
    return (
      "Lỗi encoding trên Windows (charmap/cp1252).\n\n"
      "Cập nhật bản ThucXuatTool mới rồi chạy lại.\n\n"
      f"Chi tiết: {msg}"
    )
  return msg
