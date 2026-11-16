# -*- coding: utf-8 -*-
"""Cấu hình app và các tùy chọn xử lý ảnh."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from thuc_xuat.app_paths import app_dir

CONFIG_DIR = app_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
  "auto_open_output": False,
  # Thư mục chứa file kết quả. Tên tương đối = nằm cạnh app; hoặc đường dẫn tuyệt đối.
  "output_dir": "KetQua",
  # True = file kết quả có thêm cột «Tên sản phẩm» để đối chiếu.
  "export_include_name": False,
  "nim_model": "nim-auto",
  # 0 = tự chia theo chiều cao ảnh; 1..4 = số dải cố định
  "image_bands": 0,
}

OUTPUT_DIR_NAME = "KetQua"


def load_config() -> dict[str, Any]:
  """Đọc config.json; file hỏng/thiếu khoá thì dùng giá trị mặc định."""
  if not CONFIG_FILE.exists():
    return dict(DEFAULT_CONFIG)
  data: Any = None
  for encoding in ("utf-8-sig", "utf-8"):
    try:
      data = json.loads(CONFIG_FILE.read_text(encoding=encoding))
      break
    except (OSError, UnicodeDecodeError, ValueError):
      continue
  if not isinstance(data, dict):
    return dict(DEFAULT_CONFIG)
  cfg = dict(DEFAULT_CONFIG)
  cfg.update({key: data[key] for key in DEFAULT_CONFIG if key in data})
  if "image_bands" not in data and "ollama_bands" in data:
    cfg["image_bands"] = data["ollama_bands"]
  return cfg


def ensure_config_file() -> None:
  """Tạo config.json mặc định bên cạnh .exe nếu chưa có."""
  if CONFIG_FILE.exists():
    return
  try:
    CONFIG_FILE.write_text(
      json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
      encoding="utf-8",
    )
  except OSError:
    # App nằm trong thư mục chỉ đọc (Program Files) — vẫn chạy với mặc định.
    pass


def image_band_count() -> int:
  """0 để tự chia dải ảnh, 1–4 để chọn số dải cố định."""
  try:
    return max(0, min(4, int(load_config()["image_bands"])))
  except (TypeError, ValueError, OverflowError):
    return 0


def auto_open_output_folder() -> bool:
  cfg = load_config()
  return cfg["auto_open_output"]


def _ensure_dir(path: Path) -> Path:
  """Tạo thư mục; nếu không ghi được (app nằm trong thư mục chỉ đọc) thì dùng Documents."""
  try:
    path.mkdir(parents=True, exist_ok=True)
    return path
  except OSError:
    pass
  for fallback in (Path.home() / "Documents" / "ThucXuat", Path(tempfile.gettempdir()) / "ThucXuat"):
    try:
      fallback.mkdir(parents=True, exist_ok=True)
      return fallback
    except OSError:
      continue
  return path


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

