# -*- coding: utf-8 -*-
"""Xử lý phiếu xuất: map STT -> Mã SP + Thực xuất."""
from __future__ import annotations

import ast
import math
import operator
import re
import unicodedata
from decimal import Decimal, DecimalException
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
_MANUAL_OPS = {
  ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
}


def calculate_manual_qty(text: str) -> int | float:
  """Tính biểu thức số do người dùng nhập, giới hạn ở bốn phép tính."""
  expression = text.strip().replace(",", ".").replace("×", "*").replace("÷", "/")
  expression = re.sub(r"[xX]", "*", expression)
  if len(expression) > 80 or not re.fullmatch(r"[0-9.+*/()\s-]+", expression):
    raise ValueError("Nhập số hoặc phép tính hợp lệ, ví dụ 3*8, 10+2 hoặc 12/3.")

  def evaluate(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
      return Decimal(str(node.value))
    if isinstance(node, ast.BinOp) and type(node.op) in _MANUAL_OPS:
      return _MANUAL_OPS[type(node.op)](evaluate(node.left), evaluate(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
      value = evaluate(node.operand)
      return -value if isinstance(node.op, ast.USub) else value
    raise ValueError("Chỉ dùng số, dấu ngoặc và các phép +, -, *, /.")

  try:
    value = evaluate(ast.parse(expression, mode="eval").body)
    number = float(value)
  except SyntaxError as exc:
    raise ValueError("Phép tính chưa hợp lệ.") from exc
  except (DecimalException, OverflowError) as exc:
    raise ValueError("Không tính được biểu thức. Kiểm tra phép chia cho 0.") from exc
  if not math.isfinite(number) or number < 0:
    raise ValueError("Thực xuất phải là số không âm và hữu hạn.")
  return int(number) if number.is_integer() else number

# Phiếu không có chữ Y. Nét gạch chéo (X) hay bị đọc thành Y → vẫn là "bỏ dòng".
_X_OR_Y_RE = re.compile(r"^(?:\d+(?:[.,]\d+)?)?[XY]$")

CARTON_ITEM_UNITS = frozenset({"goi", "cai", "chiec", "chai", "tui", "lon", "hop"})
_COUNT_UNITS = CARTON_ITEM_UNITS | frozenset({
  "khay", "con", "loc", "qua",
  "bich", "hu", "cuon", "vien", "mieng", "vi", "coc", "ly", "cay",
  "bo", "doi", "bao", "binh",
})
_PACKAGE_UNITS = frozenset({
  "thung", "t", "hop", "khay", "loc", "tui", "bich", "bao", "vi", "goi", "bo",
})
_COUNT_WITH_UNIT = re.compile(r"(\d+(?:[.,]\d+)?)\s*([a-z]+)")
_OCR_PACK_PLUS = re.compile(r"\d+(?:[.,]\d+)?\s*(?:t|thung)\s*\+\s*(\d+(?:[.,]\d+)?)")
_CARTON = r"(?:thung\b(?:\s*\(\s*t\s*\))?|t\b)"
_PACK_ITEM = r"\d+(?:[.,]\d+)?\s*(?:" + "|".join(sorted(_COUNT_UNITS)) + r")\b"
_PACK_SPEC = re.compile(
  rf"\b{_CARTON}[\s_(/:=–-]*(?P<after>{_PACK_ITEM})"
  rf"|(?P<before>{_PACK_ITEM})\s*/\s*{_CARTON}"
)
_INVERSE_CARTON = re.compile(rf"^{_PACK_ITEM}\s*/\s*{_CARTON}")
_COMPOUND_COUNT = re.compile(
  rf"\d\s*(?:[-–—~]|den)\s*{_PACK_ITEM}|{_PACK_ITEM}\s*(?:[x×*/+]|hoac)\s*{_PACK_ITEM}"
)


def _carton_handwriting(raw: Any, unit: Any) -> re.Match | None:
  written = _COUNT_WITH_UNIT.fullmatch(_plain_unit(raw))
  if not written or written[2] not in CARTON_ITEM_UNITS:
    return None
  target = _plain_unit(unit).replace("_", " ")
  return written if re.match(_CARTON, target) or _INVERSE_CARTON.match(target) else None


def needs_carton_review(raw: Any, unit: Any, qty: int | float | None) -> bool:
  return qty is None and _carton_handwriting(raw, unit) is not None


def carton_review_message(skipped: list[OutputRow]) -> str:
  rows = [str(r.stt) for r in skipped if needs_carton_review(r.raw, r.unit, None)]
  if not rows:
    return ""
  return "Chưa xuất do chưa xác định được quy cách thùng ở STT " + ", ".join(rows) + "."


def _carton_factor(unit: Any, product_name: str, written_unit: str) -> float | None:
  specs: set[tuple[float, str]] = set()
  for is_unit, field in ((True, unit), (False, product_name)):
    text = _plain_unit(field).replace("_", " ")
    if _COMPOUND_COUNT.search(text):
      return None
    if is_unit:
      items = _COUNT_WITH_UNIT.findall(text)
    else:
      items = []
      for spec in _PACK_SPEC.finditer(text):
        items.extend(_COUNT_WITH_UNIT.findall(spec["after"] or spec["before"]))
    for amount, label in items:
      if label not in _COUNT_UNITS:
        continue
      specs.add((float(amount.replace(",", ".")), "cai" if label == "chiec" else label))
  if len(specs) == 1:
    factor, label = specs.pop()
    bare_sizes = re.findall(r"\(\s*(\d+(?:[.,]\d+)?)\s*\)", _plain_unit(unit))
    if any(float(size.replace(",", ".")) != factor for size in bare_sizes):
      return None
    if factor > 0 and math.isfinite(factor) and label == ("cai" if written_unit == "chiec" else written_unit):
      return factor
  return None

def _plain_unit(text: Any) -> str:
  normalized = unicodedata.normalize("NFD", str(text or "").strip().lower())
  return "".join(char for char in normalized if not unicodedata.combining(char))


def _package_counts(unit: Any) -> set[tuple[float, str]]:
  counts = set()
  for amount, label in _COUNT_WITH_UNIT.findall(_plain_unit(unit)):
    if label in _COUNT_UNITS:
      factor = float(amount.replace(",", "."))
      if math.isfinite(factor) and factor > 0:
        counts.add((factor, label))
  return counts


def _quantity_in_excel_unit(raw: str, unit: Any) -> int | float | None:
  """Convert a handwritten count only when the Excel unit gives its exact pack size."""
  written = _COUNT_WITH_UNIT.fullmatch(_plain_unit(raw))
  excel_unit = _plain_unit(unit)
  target = re.match(r"^[a-z]+", excel_unit)
  if not written or not target or target[0] not in _PACKAGE_UNITS:
    return None
  if re.search(r"\d\s*[-–—~]\s*\d", excel_unit):
    return None  # A range is not a fixed packing quantity.
  counts = _package_counts(unit)
  if len(counts) != 1:
    return None
  factor, inner_unit = next(iter(counts))
  if written[2] != inner_unit or inner_unit == target[0]:
    return None
  amount = float(written[1].replace(",", "."))
  result = amount / factor
  return _to_number(str(result)) if math.isfinite(result) else None


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
  counts = _package_counts(unit)
  if len(counts) == 1:
    return next(iter(counts))[0]
  if not counts:
    # 'thùng (40)' — số trần trong ngoặc, không có tên đơn vị nhỏ.
    m = re.search(r"\(\s*(\d+(?:[.,]\d+)?)\s*\)", str(unit))
    if m:
      return _to_number(m.group(1).replace(",", "."))
  return None


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
  # A handwritten x often looks like + after the abbreviation for thùng.
  if match := _OCR_PACK_PLUS.fullmatch(_plain_unit(text)):
    return [float(match[1].replace(",", "."))]
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
  *,
  product_name: str = "",
) -> int | float | None:
  """
  Chuyển ghi chú thực xuất thành số.

  - X / trống -> None (bỏ dòng)
  - Y (nét gạch chéo bị đọc nhầm) -> None (bỏ dòng)
  - Chỉ tick (✓ v V > / √) -> lấy số lượng đặt
  - Có dấu nhân -> nhân: '3T*3' -> 9, '5T x 24' -> 120
  - '5T+8' đọc từ ảnh -> ưu tiên 5T x 8. Biểu thức nhập tay có tiền tố =.
  - '120 gói' với ĐVT 'thùng(T)/60 gói' -> 2 thùng. Chỉ chia khi khớp đơn vị nhỏ.
  - Nếu ĐVT chỉ ghi thùng, dùng quy cách thùng/gói hoặc thùng/cái trong tên hàng.
  - Số trần hoặc số đã ghi theo ĐVT đích giữ nguyên.
  - Số lẻ giữ nguyên: '5,5' -> 5.5
  """
  if raw is None:
    return None
  s = str(raw).strip()
  if not s:
    return None
  if s.startswith("="):
    try:
      return calculate_manual_qty(s[1:])
    except ValueError:
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
  if written := _carton_handwriting(s, unit):
    factor = _carton_factor(unit, product_name, written[2])
    if factor is None:
      return None
    result = float(written[1].replace(",", ".")) / factor
    return _to_number(str(result)) if math.isfinite(result) else None
  converted = _quantity_in_excel_unit(s, unit)
  if converted is not None:
    return converted

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
    qty = parse_qty(raw, p.ordered_qty, p.unit, product_name=p.name)
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
    ws.append(["Mã hàng", "Tên sản phẩm", "Số lượng chuyển"])
    for row in rows:
      ws.append([row.code, row.name, row.qty])
  else:
    ws.append(["Mã hàng", "Số lượng chuyển"])
    for row in rows:
      ws.append([row.code, row.qty])
  format_output_table(ws)
  wb.save(str(output_path))
  wb.close()


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



def format_output_table(ws) -> None:
  """Create the native Excel table used by transfer imports."""
  from openpyxl.worksheet.table import Table, TableStyleInfo
  from openpyxl.utils import get_column_letter

  last = get_column_letter(ws.max_column)
  table = Table(displayName="ThucXuatTable", ref=f"A1:{last}{ws.max_row}")
  table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
  ws.add_table(table)
  ws.freeze_panes = "A2"
  for row in ws.iter_rows(min_row=2, max_col=ws.max_column - 1):
    for cell in row:
      cell.number_format = "@"
      cell.data_type = "s"
  ws.column_dimensions["A"].width = 24
  ws.column_dimensions[last].width = 22
  if ws.max_column == 3:
    ws.column_dimensions["B"].width = 60
