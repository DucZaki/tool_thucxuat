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
) -> None:
  payload: dict[str, Any] = {"thuc_xuat": {str(k): v for k, v in sorted(thuc_xuat.items())}}
  if meta:
    payload["meta"] = meta
  Path(path).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2),
    encoding="utf-8",
  )
