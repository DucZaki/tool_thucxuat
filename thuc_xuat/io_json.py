# -*- coding: utf-8 -*-
import json
from pathlib import Path
from typing import Any


def load_mapping(path: str | Path) -> dict[int, str]:
  data = json.loads(Path(path).read_text(encoding="utf-8"))
  if isinstance(data, dict) and "thuc_xuat" in data:
    data = data["thuc_xuat"]
  if not isinstance(data, dict):
    raise ValueError("File JSON phải là object {stt: ghi_chu}")
  return {int(k): str(v).strip() for k, v in data.items() if v is not None}


def save_mapping(
  path: str | Path,
  thuc_xuat: dict[int, str],
  meta: dict[str, Any] | None = None,
  names: dict[str, str] | None = None,
) -> None:
  """Ghi file JSON phụ cạnh file kết quả.

  names: {mã sản phẩm: tên hàng} — để cửa sổ «Sửa file đã xuất» vẫn hiện được
  tên hàng dù file Excel xuất ra chỉ có 2 cột.
  """
  payload: dict[str, Any] = {"thuc_xuat": {str(k): v for k, v in sorted(thuc_xuat.items())}}
  if meta:
    payload["meta"] = meta
  if names:
    payload["names"] = dict(sorted(names.items()))
  Path(path).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
  )


def _read_payload(path: str | Path) -> dict[str, Any]:
  try:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}
  return data if isinstance(data, dict) else {}


def load_sidecar(path: str | Path) -> tuple[dict[str, str], str | None]:
  """Đọc (tên hàng theo mã, đường dẫn Excel gốc) từ JSON cạnh file kết quả."""
  data = _read_payload(path)
  raw_names = data.get("names")
  names: dict[str, str] = {}
  if isinstance(raw_names, dict):
    names = {str(k): str(v) for k, v in raw_names.items() if str(v).strip()}
  meta = data.get("meta")
  source = None
  if isinstance(meta, dict):
    source = str(meta.get("source") or "").strip() or None
  return names, source


def update_sidecar_names(path: str | Path, names: dict[str, str]) -> None:
  """Cập nhật riêng phần «names» trong JSON phụ, giữ nguyên map/meta.

  Dùng sau khi sửa file kết quả để lần mở sau vẫn có tên hàng.
  Không ghi được (file khóa, JSON hỏng) thì bỏ qua — không ảnh hưởng file Excel.
  """
  if not names:
    return
  target = Path(path)
  data = _read_payload(target)
  data["names"] = dict(sorted(names.items()))
  try:
    target.write_text(
      json.dumps(data, ensure_ascii=False, indent=2),
      encoding="utf-8",
    )
  except OSError:
    pass
