# -*- coding: utf-8 -*-
"""Hủy tác vụ đang chạy (nút Dừng)."""
from __future__ import annotations

import threading


class CancelledByUser(Exception):
  """Người dùng bấm Dừng."""


def check_cancel(cancel_event: threading.Event | None) -> None:
  if cancel_event is not None and cancel_event.is_set():
    raise CancelledByUser("Đã dừng theo yêu cầu.")
