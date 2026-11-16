# -*- coding: utf-8 -*-
"""Thực Xuất — xử lý ảnh phiếu kho.

Các công cụ xử lý ảnh và Excel được import khi cần, để bộ cài khởi động nhẹ.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
  "BatchResult": ("thuc_xuat.pipeline", "BatchResult"),
  "ProcessResult": ("thuc_xuat.pipeline", "ProcessResult"),
  "process_batch_like_chat": ("thuc_xuat.pipeline", "process_batch_like_chat"),
  "Product": ("thuc_xuat.processor", "Product"),
  "build_output": ("thuc_xuat.processor", "build_output"),
  "load_products": ("thuc_xuat.processor", "load_products"),
  "parse_qty": ("thuc_xuat.processor", "parse_qty"),
  "save_output": ("thuc_xuat.processor", "save_output"),
}

_SUBMODULES = {
  "app_paths",
  "cancel",
  "config",
  "image_match",
  "image_prep",
  "io_json",
  "nim_client",
  "pipeline",
  "processor",
  "shell_open",
  "updater",
  "version",
  "vision_extract",
  "win_utf8",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
  if name in _EXPORTS:
    module_name, attr = _EXPORTS[name]
    value = getattr(import_module(module_name), attr)
    globals()[name] = value
    return value
  if name in _SUBMODULES:
    return import_module(f"thuc_xuat.{name}")
  raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
  return sorted(__all__)
