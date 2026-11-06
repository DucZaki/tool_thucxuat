# -*- coding: utf-8 -*-
"""Chạy tiến trình con trên Windows mà KHÔNG hiện cửa sổ console.

App đóng gói ở chế độ GUI (console=False). Khi một app GUI gọi tiến trình
console con (powershell, tasklist, ollama, cmd...) mà không có cờ ẩn, Windows
cấp cho tiến trình đó một cửa sổ console mới → người dùng thấy "nhấp nháy"
một cửa sổ terminal. Mọi chỗ spawn tiến trình phải đi qua module này.

Dùng:
  from thuc_xuat.proc import popen, run
  popen(["explorer", path])                     # ẩn cửa sổ
  run(["tasklist", ...], capture_output=True)   # ẩn cửa sổ + lấy output

Và gọi install_no_window_defaults() một lần khi app khởi động để bọc luôn
các chỗ spawn của thư viện bên thứ ba.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010

_IS_WINDOWS = os.name == "nt"


def _startupinfo() -> Any:
  """STARTUPINFO yêu cầu cửa sổ ẩn (một số bản Windows vẫn cần cờ này)."""
  if not _IS_WINDOWS:
    return None
  info = subprocess.STARTUPINFO()
  info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
  info.wShowWindow = subprocess.SW_HIDE
  return info


def hidden_kwargs(detached: bool = False) -> dict[str, Any]:
  """kwargs cho Popen/run: không tạo cửa sổ console.

  detached=True: tách hẳn khỏi app (app tắt tiến trình vẫn chạy) — dùng cho
  `ollama serve`.
  """
  if not _IS_WINDOWS:
    return {}
  if detached:
    # DETACHED_PROCESS: không console, không cửa sổ, sống độc lập.
    return {"creationflags": DETACHED_PROCESS}
  return {
    "creationflags": CREATE_NO_WINDOW,
    "startupinfo": _startupinfo(),
  }


def popen(cmd: Any, *, detached: bool = False, **kwargs: Any) -> subprocess.Popen:
  """subprocess.Popen nhưng không hiện cửa sổ console."""
  params = hidden_kwargs(detached)
  params.update(kwargs)
  return subprocess.Popen(cmd, **params)


def run(
  cmd: Any,
  *,
  timeout: float | None = None,
  check: bool = False,
  capture_output: bool = True,
  **kwargs: Any,
) -> subprocess.CompletedProcess:
  """subprocess.run nhưng không hiện cửa sổ console."""
  params = hidden_kwargs()
  params.update(kwargs)
  return subprocess.run(
    cmd,
    timeout=timeout,
    check=check,
    capture_output=capture_output,
    **params,
  )


_installed = False


def install_no_window_defaults() -> None:
  """Bọc subprocess.Popen để MỌI tiến trình con đều ẩn cửa sổ.

  Gọi 1 lần lúc khởi động app. Nhờ vậy cả code của thư viện bên thứ ba
  (không đi qua proc.popen) cũng không làm nhấp nháy cửa sổ terminal.
  """
  global _installed
  if _installed or not _IS_WINDOWS:
    return
  _installed = True
  original = subprocess.Popen

  class _HiddenPopen(original):  # type: ignore[misc, valid-type]
    def __init__(self, *args: Any, **kwargs: Any) -> None:
      flags = int(kwargs.get("creationflags") or 0)
      # Tôn trọng lựa chọn của nơi gọi: đã tách/đã tạo console riêng thì thôi.
      if not flags & (DETACHED_PROCESS | CREATE_NEW_CONSOLE):
        kwargs["creationflags"] = flags | CREATE_NO_WINDOW
        kwargs.setdefault("startupinfo", _startupinfo())
      # App GUI không có std handles: tránh Windows tự cấp console cho con.
      for name in ("stdin", "stdout", "stderr"):
        if name not in kwargs:
          kwargs[name] = subprocess.DEVNULL
      super().__init__(*args, **kwargs)

  subprocess.Popen = _HiddenPopen  # type: ignore[assignment]
