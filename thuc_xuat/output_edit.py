# -*- coding: utf-8 -*-
"""Đọc và sửa lại file Excel đã xuất (Mã sản phẩm | [Tên sản phẩm] | Thực xuất).

Dùng cho tính năng «chọn 1 file đã xuất → sửa trực tiếp → lưu/xuất lại»:
file kết quả chỉ có 2 hoặc 3 cột nên đọc lại rất nhẹ, không cần ảnh hay Excel gốc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import Workbook, load_workbook

CODE_HEADER = "Mã sản phẩm"
NAME_HEADER = "Tên sản phẩm"
QTY_HEADER = "Thực xuất"


class OutputFormatError(ValueError):
  """File không phải file kết quả do app tạo (thiếu cột chuẩn)."""


@dataclass
class EditRow:
  code: str
  name: str = ""
  qty: int | float | None = None


@dataclass
class OutputSheet:
  path: Path
  rows: list[EditRow] = field(default_factory=list)
  has_name: bool = False
  dirty: bool = False

  @property
  def headers(self) -> list[str]:
    if self.has_name:
      return [CODE_HEADER, NAME_HEADER, QTY_HEADER]
    return [CODE_HEADER, QTY_HEADER]

  @property
  def total(self) -> int:
    return len(self.rows)


def _text(value: object) -> str:
  return str(value if value is not None else "").strip()


def _qty(value: object) -> int | float | None:
  """Ô Thực xuất → số (giữ số lẻ), rỗng/chữ → None."""
  if value is None or isinstance(value, bool):
    return None
  if isinstance(value, (int, float)):
    number = float(value)
    return int(number) if number.is_integer() else number
  text = str(value).strip().replace(" ", "").replace(",", ".")
  if not text:
    return None
  try:
    number = float(text)
  except ValueError:
    return None
  return int(number) if number.is_integer() else number


def _header_index(header: list[str], name: str) -> int | None:
  for i, cell in enumerate(header):
    if cell == name:
      return i
  return None


def list_outputs(folder: str | Path) -> list[Path]:
  """Các file .xlsx trong thư mục xuất, mới nhất trước (bỏ file Excel đang mở)."""
  path = Path(folder)
  if not path.is_dir():
    return []
  files = [
    p
    for p in path.glob("*.xlsx")
    if p.is_file() and not p.name.startswith("~$")
  ]
  files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
  return files


def load_sheet(path: str | Path) -> OutputSheet:
  """Đọc file kết quả. Raise OutputFormatError nếu không đúng cấu trúc."""
  file_path = Path(path)
  try:
    wb = load_workbook(str(file_path), data_only=True, read_only=True)
  except Exception as exc:  # noqa: BLE001 — file khóa/hỏng đều báo chung
    raise OutputFormatError(
      f"Không đọc được «{file_path.name}».\n"
      "Nếu file đang mở trong Excel, hãy đóng lại rồi thử lại.\n\n"
      f"Chi tiết: {exc}"
    ) from exc
  try:
    ws = wb.active
    header = [_text(c.value) for c in next(ws.iter_rows(min_row=1, max_row=1))]
    code_i = _header_index(header, CODE_HEADER)
    qty_i = _header_index(header, QTY_HEADER)
    name_i = _header_index(header, NAME_HEADER)
    if code_i is None or qty_i is None:
      raise OutputFormatError(
        f"«{file_path.name}» không phải file kết quả của app.\n"
        f"Cần có cột «{CODE_HEADER}» và «{QTY_HEADER}».\n\n"
        f"Cột đang có: {', '.join(h for h in header if h) or '(trống)'}"
      )
    rows: list[EditRow] = []
    for values in ws.iter_rows(min_row=2, values_only=True):
      if not values:
        continue
      code = _text(values[code_i] if code_i < len(values) else "")
      if not code:
        continue
      name = ""
      if name_i is not None and name_i < len(values):
        name = _text(values[name_i])
      qty = _qty(values[qty_i] if qty_i < len(values) else None)
      rows.append(EditRow(code=code, name=name, qty=qty))
  finally:
    wb.close()
  return OutputSheet(path=file_path, rows=rows, has_name=name_i is not None)


def save_sheet(sheet: OutputSheet, path: str | Path | None = None) -> Path:
  """Ghi lại file (mặc định ghi đè đúng file đang sửa)."""
  target = Path(path) if path is not None else sheet.path
  wb = Workbook()
  ws = wb.active
  ws.title = "ThucXuat"
  ws.append(sheet.headers)
  for row in sheet.rows:
    values: list[object] = [row.code]
    if sheet.has_name:
      values.append(row.name)
    values.append(row.qty if row.qty is not None else "")
    ws.append(values)
  wb.save(str(target))
  wb.close()
  sheet.path = target
  sheet.dirty = False
  return target
