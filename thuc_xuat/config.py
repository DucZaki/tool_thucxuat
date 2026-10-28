# -*- coding: utf-8 -*-
"""Cấu hình: chỉ Ollama local. Sửa config.json cạnh app nếu muốn đổi model."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from thuc_xuat.app_paths import app_dir

CONFIG_DIR = app_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
  "auto_open_output": True,
  # Thư mục chứa file kết quả. Tên tương đối = nằm cạnh app; hoặc đường dẫn tuyệt đối.
  "output_dir": "KetQua",
  # True = file kết quả có thêm cột «Tên sản phẩm» để đối chiếu.
  "export_include_name": False,
  "ollama_base_url": "http://127.0.0.1:11434",
  "ollama_model": "gemma3:4b",
  # 0 = tự chia theo chiều cao ảnh; 1..4 = số dải cố định
  "ollama_bands": 0,
  "ollama_num_ctx": 8192,
  "ollama_timeout": 180,
}

OLLAMA_DEFAULT_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_DEFAULT_MODEL = "gemma3:4b"
OUTPUT_DIR_NAME = "KetQua"


def load_config() -> dict[str, Any]:
  if CONFIG_FILE.exists():
    # utf-8-sig: chấp nhận file có BOM (Notepad/PowerShell hay thêm vào).
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
  return dict(DEFAULT_CONFIG)


def ensure_config_file() -> None:
  """Tạo config.json mặc định bên cạnh .exe nếu chưa có."""
  if not CONFIG_FILE.exists():
    CONFIG_FILE.write_text(
      json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
      encoding="utf-8",
    )


def get_ollama_settings() -> dict[str, Any]:
  """Cấu hình Ollama (local) đã chuẩn hóa."""
  cfg = load_config()

  def as_int(value: Any, default: int) -> int:
    try:
      return int(value)
    except (TypeError, ValueError):
      return default

  def as_float(value: Any, default: float) -> float:
    try:
      return float(value)
    except (TypeError, ValueError):
      return default

  base = str(cfg.get("ollama_base_url") or OLLAMA_DEFAULT_BASE_URL).strip()
  return {
    "base_url": base.rstrip("/") or OLLAMA_DEFAULT_BASE_URL,
    "model": str(cfg.get("ollama_model") or OLLAMA_DEFAULT_MODEL).strip()
    or OLLAMA_DEFAULT_MODEL,
    "bands": max(0, min(4, as_int(cfg.get("ollama_bands"), 0))),
    "num_ctx": max(2048, as_int(cfg.get("ollama_num_ctx"), 8192)),
    "timeout": max(30.0, as_float(cfg.get("ollama_timeout"), 180.0)),
  }


def auto_open_output_folder() -> bool:
  cfg = load_config()
  return cfg.get("auto_open_output", True)


def _ensure_dir(path: Path) -> Path:
  """Tạo thư mục; nếu không ghi được (app nằm trong thư mục chỉ đọc) thì dùng Documents."""
  try:
    path.mkdir(parents=True, exist_ok=True)
    return path
  except OSError:
    fallback = Path.home() / "Documents" / "ThucXuat"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def output_dir() -> Path:
  """Thư mục chứa file Excel kết quả — tự tạo nếu chưa có."""
  raw = str(load_config().get("output_dir") or OUTPUT_DIR_NAME).strip()
  path = Path(raw).expanduser()
  if not path.is_absolute():
    path = app_dir() / path
  return _ensure_dir(path)


def export_include_name() -> bool:
  """True = xuất kèm cột «Tên sản phẩm» (mặc định tắt)."""
  return bool(load_config().get("export_include_name", False))


def set_config_value(key: str, value: Any) -> None:
  """Ghi 1 khoá vào config.json, giữ nguyên các khoá khác."""
  cfg = load_config()
  cfg[key] = value
  try:
    CONFIG_FILE.write_text(
      json.dumps(cfg, ensure_ascii=False, indent=2),
      encoding="utf-8",
    )
  except OSError:
    pass  # không ghi được thì thôi, app vẫn chạy với giá trị đang chọn

