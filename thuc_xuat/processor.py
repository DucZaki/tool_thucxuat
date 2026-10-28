# -*- coding: utf-8 -*-
"""Xử lý phiếu xuất: map STT -> Mã SP + Thực xuất."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook


@dataclass
class Product:
    stt: int
    code: str
    name: str
    ordered_qty: float | int | None
    unit: str = ""


@dataclass
class OutputRow:
    stt: int
    code: str
    name: str
    raw: str | None
    qty: int | float
    unit: str = ""


TICK_MARKS = frozenset({"✓", "✔", "v", "V", "Y", "y", ">", "/", "√"})

# Đơn vị lẻ: nếu phiếu ghi kèm đơn vị này thì số đã là số lẻ, không quy đổi.
_INNER_UNITS = (
  "gói", "goi", "hộp", "hop", "khay", "con", "chai", "lốc", "loc", "lon",
  "cái", "cai", "quả", "qua", "túi", "tui", "bịch", "bich", "hũ", "hu",
  "kg", "gam", "gram", "g", "lít", "lit", "l", "ml",
)

_COUNT_UNITS = (
  "gói", "goi", "hộp", "hop", "khay", "con", "chai", "lốc", "loc", "lon",
  "cái", "cai", "quả", "qua", "túi", "tui", "bịch", "bich", "hũ", "hu",
  "cuộn", "cuon", "viên", "vien", "miếng", "mieng",
)

# "2kg", "600-700g", "10 lít" — số này là trọng lượng, không phải số lượng quy đổi.
_WEIGHT_RE = re.compile(
  r"\d+(?:[.,]\d+)?\s*(?:kg|gam|gram|ml|lít|lit)(?![a-zà-ỹ])"
  r"|\b\d+(?:[.,]\d+)?\s*g(?![a-zà-ỹ])"
)


def _to_number(text: str) -> int | float | None:
  """'5' -> 5, '5.5' / '5,5' -> 5.5 (giữ số lẻ cho hàng kg)."""
  try:
    value = float(text)
  except ValueError:
    return None
  return int(value) if value.is_integer() else value


def pack_factor(unit: Any) -> int | float | None:
  """
  Hệ số quy đổi nằm trong ĐVT của file chi tiết.

  'thùng 40 gói' -> 40, 'thùng (20 khay)' -> 20, 'thùng (40)' -> 40.
  'túi 2kg', 'thùng 10kg', 'cái (600-700g)', 'gói' -> None (không phải số lượng).
  """
  if not unit:
    return None
  s = _WEIGHT_RE.sub(" ", str(unit).lower())
  factor: int | float | None = None
  for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*([a-zà-ỹ]+)", s):
    if m.group(2).startswith(_COUNT_UNITS):
      factor = _to_number(m.group(1).replace(",", "."))
  if factor is None:
    # 'thùng (40)' — số trần trong ngoặc
    m = re.search(r"\(\s*(\d+(?:[.,]\d+)?)\s*\)", s)
    if m:
      factor = _to_number(m.group(1).replace(",", "."))
  return factor


def _written_unit(text: str) -> str:
  """Đơn vị viết tay ngay sau con số: '3T x 6' -> 't', '10 gói' -> 'gói'."""
  m = re.match(r"^\s*\d+(?:[.,]\d+)?\s*([A-Za-zÀ-ỹĐđ]+)", text)
  return m.group(1).lower() if m else ""


def _explicit_factors(text: str) -> list[float]:
  """Các thừa số sau dấu nhân: '5T x 24' -> [24], '3T*3' -> [3].

  '5b x 30kg' -> [] vì 30kg là trọng lượng, không phải số lượng gấp.
  """
  factors: list[float] = []
  for m in re.finditer(r"[*x×]\s*(\d+(?:[.,]\d+)?)(.*)", text, re.IGNORECASE):
    tail = m.group(2).lower()
    if re.match(r"\s*(?:kg|gam|gram|ml|lít|lit)(?![a-zà-ỹ])", tail):
      continue
    if re.match(r"\s*g(?![a-zà-ỹ])", tail):
      continue
    if re.match(r"\s*l(?![a-zà-ỹ])", tail):
      continue
    factors.append(float(m.group(1).replace(",", ".")))
  return factors


def parse_qty(
  raw: Any,
  ordered_qty: float | int | None = None,
  unit: Any = None,
) -> int | float | None:
  """
  Chuyển ghi chú thực xuất thành số.

  - X / trống -> None (bỏ dòng)
  - Chỉ tick (✓ v V Y > / √) -> lấy số lượng đặt
  - Có dấu nhân -> nhân: '3T*3' -> 9, '5T x 24' -> 120
  - Không có dấu nhân, phiếu ghi đơn vị lẻ ('10 gói', '30kg') -> giữ nguyên
  - Không có dấu nhân, phiếu ghi đơn vị ngoài ('2T') -> nhân hệ số ĐVT:
    'thùng 40 gói' + '2T' -> 80; 'túi 2kg' + '3T' -> 3 (2kg là trọng lượng)
  - Số lẻ giữ nguyên: '5,5' -> 5.5
  """
  if raw is None:
    return None
  s = str(raw).strip()
  if not s or s.upper() == "X":
    return None
  if s in TICK_MARKS:
    if ordered_qty is not None:
      return ordered_qty
    return None

  base: int | float | None = None
  factors: list[float] = []

  # "37X6" / "3TX6" = "3T x 6" (model hay đọc chữ T thành 7).
  compact = re.sub(r"\s+", "", s.upper())
  ocr_t = re.match(r"^(\d+)7[X×*](\d+(?:[.,]\d+)?)$", compact)
  if ocr_t:
    base = _to_number(ocr_t.group(1))
    factors = [float(ocr_t.group(2).replace(",", "."))]
  else:
    m = re.match(r"^(\d+(?:[.,]\d+)?)", s.replace(",", "."))
    if not m:
      return None
    base = _to_number(m.group(1))
    factors = _explicit_factors(s)

  if base is None:
    return None

  value = float(base)
  if factors:
    for f in factors:
      value *= f
    return _to_number(str(value))

  written = _written_unit(s)
  if written.startswith(_INNER_UNITS):
    return base

  factor = pack_factor(unit)
  if factor:
    return _to_number(str(value * float(factor)))
  return base



def load_products(source_path: str | Path) -> list[Product]:
  wb = load_workbook(str(source_path), data_only=True)
  ws = wb.active
  products: list[Product] = []
  for r in range(2, (ws.max_row or 1) + 1):
    code = ws.cell(r, 1).value
    if code is None or str(code).strip() == "":
      continue
    name = ws.cell(r, 2).value
    unit = ws.cell(r, 3).value
    ordered = ws.cell(r, 4).value
    products.append(
      Product(
        stt=len(products) + 1,
        code=str(code).strip(),
        name=str(name or "").strip(),
        ordered_qty=ordered,
        unit=str(unit or "").strip(),
      )
    )
  wb.close()
  return products


def build_output(
  products: list[Product],
  thuc_xuat_map: dict[int, str],
) -> tuple[list[OutputRow], list[OutputRow]]:
  """
  Trả về (dòng xuất file, dòng bị bỏ).
  """
  included: list[OutputRow] = []
  skipped: list[OutputRow] = []

  for p in products:
    raw = thuc_xuat_map.get(p.stt)
    qty = parse_qty(raw, p.ordered_qty, p.unit)
    row = OutputRow(
      stt=p.stt, code=p.code, name=p.name, raw=raw, qty=qty or 0, unit=p.unit
    )
    if qty is not None:
      included.append(row)
    else:
      skipped.append(row)

  return included, skipped


def save_output(
  rows: list[OutputRow],
  output_path: str | Path,
  include_name: bool = False,
) -> None:
  wb = Workbook()
  ws = wb.active
  ws.title = "ThucXuat"
  if include_name:
    ws.append(["Mã sản phẩm", "Tên sản phẩm", "Thực xuất"])
    for row in rows:
      ws.append([row.code, row.name, row.qty])
  else:
    ws.append(["Mã sản phẩm", "Thực xuất"])
    for row in rows:
      ws.append([row.code, row.qty])
  wb.save(str(output_path))


def output_file_name(source_path: str | Path, include_name: bool = False) -> str:
  """PDNxxxxx_MaSanPham_ThucXuat[_KemTen].xlsx"""
  src = Path(source_path)
  base = ""
  name = src.name
  if "PDN" in name.upper():
    for part in name.replace(".xlsx", "").split("_"):
      if part.upper().startswith("PDN"):
        base = part
        break
  if not base:
    base = src.stem
  suffix = "_KemTen" if include_name else ""
  return f"{base}_MaSanPham_ThucXuat{suffix}.xlsx"


def default_output_path(
  source_path: str | Path,
  out_dir: str | Path | None = None,
  include_name: bool = False,
) -> Path:
  """Đường dẫn file kết quả: mặc định cùng thư mục Excel gốc."""
  folder = Path(out_dir) if out_dir is not None else Path(source_path).parent
  return folder / output_file_name(source_path, include_name)

