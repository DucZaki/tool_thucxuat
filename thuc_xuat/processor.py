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


TICK_MARKS = frozenset({"✓", "✔", "v", "V", ">", "/", "√"})

# Phiếu không có chữ Y. Nét gạch chéo (X) hay bị đọc thành Y → vẫn là "bỏ dòng".
_X_OR_Y_RE = re.compile(r"^(?:\d+(?:[.,]\d+)?)?[XY]$")

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


# Ký tự vô hình hay lọt vào ô mã hàng (copy từ web/chat) → coi như rỗng.
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")


def _clean_text(value: Any) -> str:
  return _ZERO_WIDTH_RE.sub("", str(value or "")).strip()


def _to_number(text: str) -> int | float | None:
  """'5' -> 5, '5.5' / '5,5' -> 5.5 (giữ số lẻ cho hàng kg)."""
  try:
    value = float(text)
  except ValueError:
    return None
  return int(value) if value.is_integer() else value


_THOUSANDS_RE = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")


def _as_number(value: Any) -> Any:
  """Ép số lượng về int/float.

  Ô «Số lượng» trong file KiotViet đôi khi ở định dạng text ("12", "12 ",
  "1.000") — nếu để nguyên, dòng chỉ tick sẽ ghi ra Excel dạng chữ.
  """
  if value is None or isinstance(value, bool):
    return value
  if isinstance(value, (int, float)):
    return value
  text = str(value).strip()
  if not text:
    return None
  if _THOUSANDS_RE.fullmatch(text.replace(" ", "")):
    # "1.000" / "1,000" = một nghìn (dấu phân cách hàng nghìn, không phải số lẻ).
    return int(re.sub(r"[.,]", "", text.replace(" ", "")))
  text = text.replace(" ", "").replace(",", ".")
  try:
    number = float(text)
  except ValueError:
    return value
  return int(number) if number.is_integer() else number


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


def _looks_like_t_read_as_7(compact: str, ordered_qty: Any) -> bool:
  """'47' khi SL đặt là 4 → gần như chắc chắn là '4T' (model đọc T thành 7)."""
  if not re.fullmatch(r"\d+7", compact) or len(compact) < 2:
    return False
  try:
    ordered = float(ordered_qty)  # type: ignore[arg-type]
  except (TypeError, ValueError):
    return False
  if ordered <= 0:
    return False
  lead = int(compact[:-1])
  whole = int(compact)
  if lead <= 0:
    return False
  return abs(lead - ordered) < abs(whole - ordered)


def _prefer_ordered(whole: int, lead: int, ordered_qty: Any, prefer_lead: bool) -> int:
  """Chọn cách đọc khớp SL đặt: '21x24' → 21×24 hay 2T×24."""
  try:
    ordered = float(ordered_qty)  # type: ignore[arg-type]
  except (TypeError, ValueError):
    ordered = 0.0
  if ordered > 0 and lead > 0:
    lead_diff = abs(lead - ordered)
    whole_diff = abs(whole - ordered)
    if lead_diff < whole_diff:
      return lead
    if lead_diff == whole_diff and prefer_lead:
      return lead
    return whole
  return lead if prefer_lead and lead > 0 else whole


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
  - Y (nét gạch chéo bị đọc nhầm) -> None (bỏ dòng)
  - Chỉ tick (✓ v V > / √) -> lấy số lượng đặt
  - Có dấu nhân -> nhân: '3T*3' -> 9, '5T x 24' -> 120
  - Không có dấu nhân -> giữ nguyên số, KHÔNG quy đổi theo ĐVT:
    '10 gói' -> 10, '30kg' -> 30, '30cm' -> 30, '1T' -> 1, '12' -> 12
  - Số lẻ giữ nguyên: '5,5' -> 5.5
  """
  if raw is None:
    return None
  s = str(raw).strip()
  if not s:
    return None
  # "X", "5X", "Y", "5 Y" — cùng nghĩa: bỏ dòng. Trên phiếu không có chữ Y,
  # model hay đọc nét gạch chéo X thành Y (trước đây Y bị coi là dấu tick nên
  # dòng đáng bỏ lại bị xuất với số lượng đặt).
  compact_marks = re.sub(r"\s+", "", s.upper())
  if _X_OR_Y_RE.fullmatch(compact_marks):
    return None
  if s in TICK_MARKS:
    value = _as_number(ordered_qty)
    return value if isinstance(value, (int, float)) else None

  base: int | float | None = None
  factors: list[float] = []

  # "37X6" / "21x24" = "3T x 6" / "2T x 24" (model hay đọc chữ T thành 7 hoặc 1).
  compact = re.sub(r"\s+", "", s.upper())
  ocr_t = re.match(r"^(\d+)([17])[X×*](\d+(?:[.,]\d+)?)$", compact)
  if ocr_t:
    whole = int(ocr_t.group(1) + ocr_t.group(2))
    lead = int(ocr_t.group(1))
    base = _prefer_ordered(whole, lead, ordered_qty, prefer_lead=ocr_t.group(2) == "7")
    factors = [float(ocr_t.group(3).replace(",", "."))]
  else:
    m = re.match(r"^(\d+(?:[.,]\d+)?)", s.replace(",", "."))
    if not m:
      return None
    base = _to_number(m.group(1))
    factors = _explicit_factors(s)
    # "47" mà SL đặt là 4 → sửa thành "4T" (AI đọc chữ T viết tay thành số 7).
    if not factors and _looks_like_t_read_as_7(compact, ordered_qty):
      fixed = _to_number(compact[:-1])
      if fixed is not None:
        base = fixed

  if base is None:
    return None

  if factors:
    value = float(base)
    for f in factors:
      value *= f
    return _to_number(str(value))
  # Không có dấu nhân: chỉ lấy đúng con số trên phiếu.
  return base


def explicit_multiplier(raw: Any) -> float:
  """Tích các thừa số sau dấu nhân: '5T x 24' -> 24, '12' -> 1."""
  if raw is None:
    return 1.0
  product = 1.0
  for factor in _explicit_factors(str(raw)):
    product *= factor
  return product


def expected_qty(ordered_qty: Any, raw: Any = None) -> float | None:
  """SL đặt × thừa số ghi trên phiếu — mốc để phát hiện dòng bị đọc sai số."""
  try:
    ordered = float(ordered_qty)
  except (TypeError, ValueError):
    return None
  if ordered <= 0:
    return None
  return ordered * explicit_multiplier(raw)


def load_products(source_path: str | Path) -> list[Product]:
  wb = load_workbook(str(source_path), data_only=True)
  ws = wb.active
  products: list[Product] = []
  for r in range(2, (ws.max_row or 1) + 1):
    code = _clean_text(ws.cell(r, 1).value)
    if not code:
      continue
    name = _clean_text(ws.cell(r, 2).value)
    unit = _clean_text(ws.cell(r, 3).value)
    ordered = _as_number(ws.cell(r, 4).value)
    products.append(
      Product(
        # STT trên phiếu = số thứ tự dòng của bảng Excel (dòng 2 → STT 1).
        # Dùng số dòng thật để nếu file có dòng trống xen giữa thì các STT
        # phía sau vẫn khớp với phiếu giấy.
        stt=r - 1,
        code=code,
        name=name,
        ordered_qty=ordered,
        unit=unit,
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

