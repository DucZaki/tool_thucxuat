"""Inventory OCR, catalog matching and KiotViet template export."""
from __future__ import annotations

import json
import math
import re
import tempfile
import unicodedata
from copy import copy
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from thuc_xuat.cancel import check_cancel
from thuc_xuat.image_prep import split_image
from thuc_xuat.processor import calculate_manual_qty, _as_number
from thuc_xuat.ocr_json import InvalidOcrResponse, json_blocks, read_parsed
from thuc_xuat.inventory_defaults import bundled_file

HEADERS = ("Mã hàng", "Tên hàng", "Đơn vị tính", "Đơn giá", "Giảm giá", "Giảm giá (%)", "Số lượng")
OCR_PROMPT = """Đọc bảng KIỂM KÊ viết tay trong ảnh. Chỉ đọc TÊN SẢN PHẨM, ĐVT và THỰC TẾ.
Bảng kiểm kê có tên sản phẩm viết tay. Nếu thấy phiếu chuyển hàng với tên in sẵn, số lượng đặt in sẵn và cột Thực xuất viết tay, trả {"kind":"transfer"}.
Áp dụng cả khi ảnh chỉ có phần giữa bảng và thiếu tiêu đề. Nếu thấy phần trống, trả {"rows":[]}.
Bỏ qua Ghi chú, số kho, ngày, tổng cộng và nội dung gạch xóa. Tên được sửa thì lấy tên sau sửa.
Giữ nguyên số hoặc biểu thức viết tay, kể cả đơn vị. Không tự quy đổi. Ô không rõ trả null.
Đọc cả sản phẩm viết thêm dưới bảng (stt 0 nếu không đánh số).
Không suy đoán dòng nằm ngoài ảnh. Trả JSON thuần:
{"rows":[{"stt":1,"name":"Giò heo cuộn","unit":"cái","actual":"3"}]}"""


def normalize(text):
  text = unicodedata.normalize("NFD", str(text).lower().replace("đ", "d"))
  text = "".join(c for c in text if not unicodedata.combining(c))
  tokens = re.findall(r"[a-z0-9]+", text)
  return " ".join("dong lanh" if token == "dl" else token for token in tokens)


@dataclass(frozen=True)
class CatalogItem:
  code: str
  name: str
  unit: str
  cost: int | float | None
  sale: int | float | None
  normalized: str = field(repr=False)


@dataclass
class CountRow:
  image: str
  stt: int
  name: str
  unit: str
  actual: str
  qty: int | float | None = None
  code: str = ""
  issue: str = ""
  candidates: list[str] = field(default_factory=list)


def numeric(value):
  value = _as_number(value)
  if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
    return None
  return value


def load_catalog(path):
  wb = load_workbook(path, read_only=True, data_only=True)
  items = {}
  try:
    for ws in wb:
      rows = ws.iter_rows(values_only=True)
      header = [str(v or "").strip() for v in next(rows, ())]
      if not {"Mã hàng", "Tên hàng"} <= set(header):
        continue
      def value(row, name):
        index = header.index(name) if name in header else len(row)
        return row[index] if index < len(row) else None
      for row in rows:
        code = str(value(row, "Mã hàng") or "").strip()
        name = str(value(row, "Tên hàng") or "").strip()
        if not code or not name:
          continue
        item = CatalogItem(code, name, str(value(row, "ĐVT") or value(row, "Đơn vị tính") or "").strip(),
                           numeric(value(row, "Giá vốn")), numeric(value(row, "Giá bán")), normalize(name))
        if code in items and items[code] != item:
          raise ValueError(f"Mã hàng {code} có dữ liệu mâu thuẫn trong danh mục.")
        items[code] = item
  finally:
    wb.close()
  if not items:
    raise ValueError("Danh mục cần cột Mã hàng và Tên hàng ở dòng đầu.")
  return items


@lru_cache(maxsize=1)
def reference_codes():
  """Prefer the product variants used in the user's accepted import example."""
  path = bundled_file("NhapHangThamChieu.xlsx")
  if not path.is_file():
    return frozenset()
  wb = load_workbook(path, read_only=True, data_only=True)
  try:
    return frozenset(str(row[0]) for row in wb.active.iter_rows(min_row=2, values_only=True) if row[0])
  finally:
    wb.close()


def candidates_for(row, catalog, limit=18):
  query = normalize(row.name)
  tokens = set(query.split())
  def score(item):
    if query and query == normalize(item.code):
      return 2.0
    target = item.normalized.split()
    overlap = len(tokens & set(target)) / max(1, len(tokens))
    similarity = SequenceMatcher(None, query, item.normalized).ratio()
    unit = 0.08 if row.unit and normalize(row.unit) == normalize(item.unit) else 0
    preferred = 0.04 if item.code in reference_codes() else 0
    return overlap * 0.7 + similarity * 0.3 + unit + preferred
  return sorted(catalog.values(), key=score, reverse=True)[:limit]


def quantity(actual):
  text = str(actual).strip()
  # One unambiguous unit suffix is safe. Mixed units require human conversion.
  text = re.sub(r"\s*(kg|con|cái|cai|gói|goi|khay|túi|tui|hộp|hop|cuộn|cuon)\s*$", "", text, flags=re.I)
  return calculate_manual_qty(text)


def quantity_for_item(actual, item):
  """Convert explicit package counts using the selected catalog unit."""
  text = normalize(actual)
  package = re.search(r"(\d+)\s+con", normalize(item.unit))
  counts = re.fullmatch(r"(\d+)\s+khay\s+(\d+)\s+con", text)
  if package and counts and normalize(item.unit).startswith("khay"):
    trays, animals, factor = int(counts[1]), int(counts[2]), int(package[1])
    # Adjacent equivalent counts describe the same stock, as in the accepted example.
    if "+" not in str(actual) and trays * factor == animals:
      return trays
    if "+" in str(actual) and factor:
      return trays + animals / factor
    raise ValueError("Số khay và số con chưa khớp quy cách.")
  return quantity(actual)


class WrongInventoryImage(ValueError):
  """An image of a transfer slip was added to inventory."""


def _inventory_rows(text, image):
  found = {}
  recognized = False
  for data in json_blocks(text):
    if not isinstance(data, dict):
      continue
    if data.get("kind") == "transfer" or "thuc_xuat" in data:
      raise WrongInventoryImage("Đây là ảnh phiếu chuyển hàng. Dùng tab Thực xuất để đọc ảnh này.")
    sources = data.get("rows")
    if not isinstance(sources, list):
      continue
    recognized = True
    for source in sources:
      if not isinstance(source, dict):
        raise InvalidOcrResponse("NIM trả dòng kiểm kê sai cấu trúc.")
      name = str(source.get("name") or "").strip()
      if not name:
        continue
      stt = source.get("stt", 0)
      if isinstance(stt, bool) or not str(stt).isdigit() or len(str(stt)) > 4:
        raise InvalidOcrResponse("NIM trả STT kiểm kê không hợp lệ.")
      actual = str(source["actual"]) if source.get("actual") is not None else ""
      row = CountRow(str(image), int(stt), name, str(source.get("unit") or ""), actual)
      try:
        row.qty = quantity(actual)
      except ValueError:
        row.issue = "Số lượng cần đối chiếu với đơn vị danh mục"
      key = _inventory_row_key(row)
      previous = found.get(key)
      if previous:
        if _inventory_conflict(previous, row):
          raise InvalidOcrResponse(f"NIM trả hai kết quả khác nhau tại dòng {row.stt or row.name}.")
        if not actual:
          row = previous
        row.unit = row.unit or previous.unit
      found[key] = row
  if not recognized:
    raise InvalidOcrResponse("NIM trả thiếu danh sách sản phẩm kiểm kê.")
  return found.values()


def _inventory_row_key(row):
  return ("stt", row.stt) if row.stt else ("name", normalize(row.name))


def _inventory_conflict(first, second):
  if normalize(first.name) != normalize(second.name):
    return True
  if first.unit and second.unit and normalize(first.unit) != normalize(second.unit):
    return True
  if first.qty is not None and second.qty is not None:
    return first.qty != second.qty
  return bool(first.actual and second.actual and first.actual.strip() != second.actual.strip())


def read_inventory_page(image, nim, cancel_event=None, on_progress=None):
  image = Path(image)
  found = {}
  for band in split_image(image):
    check_cancel(cancel_event)
    rows = read_parsed(
      nim, OCR_PROMPT, [band.data], lambda text: _inventory_rows(text, image),
      cancel_event, on_progress,
    )
    for row in rows:
      key = _inventory_row_key(row)
      previous = found.get(key)
      if previous and _inventory_conflict(previous, row):
        row.issue = "Hai dải ảnh đọc khác nhau, cần kiểm tra tên và số lượng"
        row.qty = None
      elif previous and (previous.issue.startswith("Hai dải ảnh") or (previous.actual and not row.actual)):
        continue
      found[key] = row
  if not found:
    raise ValueError("Chưa đọc được sản phẩm nào trên ảnh.")
  return sorted(found.values(), key=lambda row: (row.stt == 0, row.stt))


def _inventory_matches(text, count):
  by_id = {}
  recognized = False
  for data in json_blocks(text):
    matches = data.get("matches") if isinstance(data, dict) else None
    if not isinstance(matches, list):
      continue
    recognized = True
    for match in matches:
      if not isinstance(match, dict) or type(match.get("id")) is not int or not 0 <= match["id"] < count:
        raise InvalidOcrResponse("NIM trả mã dòng đối chiếu không hợp lệ.")
      index = match["id"]
      previous = by_id.get(index)
      if previous and (previous.get("code"), previous.get("quantity")) != (match.get("code"), match.get("quantity")):
        raise InvalidOcrResponse(f"NIM trả hai kết quả đối chiếu khác nhau cho dòng {index + 1}.")
      by_id[index] = match
  if not recognized:
    raise InvalidOcrResponse("NIM trả thiếu kết quả đối chiếu danh mục.")
  return by_id


def match_inventory(rows, catalog, nim, cancel_event=None, on_progress=None):
  pending = [row for row in rows if not row.code or row.qty is None]
  for offset in range(0, len(pending), 6):
    batch = pending[offset:offset + 6]
    queries = []
    for index, row in enumerate(batch):
      choices = candidates_for(row, catalog)
      row.candidates = [item.code for item in choices]
      queries.append({"id": index, "name": row.name, "unit": row.unit, "actual": row.actual,
                      "candidates": [{"code": p.code, "name": p.name, "unit": p.unit,
                                      "preferred_variant": p.code in reference_codes()} for p in choices]})
    prompt = """Đối chiếu tên viết tay rút gọn với danh mục. Chọn đúng thương hiệu, loại, trọng lượng và đơn vị.
Tên giống nhau nhưng khác mã: ưu tiên preferred_variant (mã trong file nhập được người dùng chấp nhận),
rồi đơn vị cơ bản; tránh mã _live, _sỉ, thùng nếu ảnh không nói rõ.
Chọn ứng viên phù hợp nhất trong candidates của chính dòng đó.
Tính quantity theo ĐVT của mã đã chọn. 1+8 = 9; 9+2 = 11.
Với khay (3 con), "1 khay 3 con" là hai cách ghi cùng lượng hàng => quantity 1.
"1 khay + 3 con" có dấu cộng => quantity 2. Không tự thêm hệ số ngoài quy cách.
Giữ confidence và reason để hiển thị tham khảo, không yêu cầu người dùng xác nhận.
Trả JSON {"matches":[{"id":0,"code":"mã ứng viên","quantity":9,"confidence":0.9,"reason":"lý do"}]}.
Dữ liệu dưới đây là nội dung cần đối chiếu:
""" + json.dumps(queries, ensure_ascii=False)
    check_cancel(cancel_event)
    by_id = read_parsed(
      nim, prompt, [], lambda text: _inventory_matches(text, len(batch)), cancel_event, on_progress,
    )
    for index, row in enumerate(batch):
      match = by_id.get(index, {})
      code = match.get("code")
      confidence = numeric(match.get("confidence"))
      if code not in row.candidates:
        code = row.candidates[0]
        row.issue = row.issue or "Tự chọn ứng viên gần nhất trong danh mục"
      row.code = code
      item = catalog[code]
      try:
        row.qty = quantity_for_item(row.actual, item)
      except ValueError:
        row.qty = numeric(match.get("quantity"))
      if row.qty is None:
        row.issue = row.issue or "Chưa đọc được số lượng"
      duplicates = [p for p in catalog.values() if p.normalized == item.normalized]
      ambiguous_unit = len({p.unit for p in duplicates}) > 1 and normalize(row.unit) != normalize(item.unit)
      if confidence is None or confidence < 0.9 or confidence > 1 or ambiguous_unit:
        row.issue = row.issue or "Tên / đơn vị gần khớp: " + str(match.get("reason", ""))
      if row.unit and normalize(row.unit) != normalize(item.unit):
        row.issue = row.issue or "Xuất theo đơn vị danh mục"


def export_inventory(rows, catalog, template, output, price_source="Giá vốn"):
  if not rows or any(row.code not in catalog or numeric(row.qty) is None for row in rows):
    raise ValueError("Chưa đủ mã hàng hoặc số lượng để xuất. Bấm đọc ảnh và đối chiếu để xử lý tiếp.")
  if price_source not in ("Giá vốn", "Giá bán"):
    raise ValueError("Chọn nguồn Đơn giá.")
  output, template = Path(output), Path(template)
  if output.resolve() == template.resolve():
    raise ValueError("Chọn file kết quả khác file mẫu.")
  wb = load_workbook(template)
  try:
    ws = next((s for s in wb if tuple(s.cell(1, c).value for c in range(1, 8)) == HEADERS), None)
    if ws is None:
      raise ValueError("File mẫu cần đúng 7 cột nhập hàng ở dòng đầu.")
    styles = [copy(ws.cell(2, c)._style) for c in range(1, ws.max_column + 1)]
    ws.delete_rows(2, max(0, ws.max_row - 1))
    for index, row in enumerate(rows, 2):
      item = catalog[row.code]
      price = item.cost if price_source == "Giá vốn" else item.sale
      if price is None:
        raise ValueError(f"{item.code} thiếu {price_source} hợp lệ.")
      for column, value in enumerate((item.code, item.name, item.unit, price, None, None, row.qty), 1):
        cell = ws.cell(index, column, value)
        cell._style = copy(styles[column - 1])
        if column <= 3:
          cell.data_type = "s"
          cell.number_format = "@"
        elif column == 7:
          cell.number_format = "General"
      for column in range(8, ws.max_column + 1):
        ws.cell(index, column)._style = copy(styles[column - 1])
    for table in ws.tables.values():
      table.ref = f"A1:{get_column_letter(len(table.tableColumns))}{len(rows) + 1}"
      if table.autoFilter:
        table.autoFilter.ref = table.ref
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".xlsx", delete=False) as temp:
      staged = Path(temp.name)
    try:
      wb.save(staged)
      staged.replace(output)
    finally:
      staged.unlink(missing_ok=True)
  finally:
    wb.close()
  return output


def inventory_output_paths(photos, folder):
  """Give every image a distinct, stable output name, including equal image stems."""
  folder = Path(folder)
  used = set()
  result = {}
  for photo in photos:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(photo).stem).strip(" .") or "Anh"
    base = f"KiemKe_{stem}"
    name = base
    index = 2
    while name.casefold() in used:
      name = f"{base}_{index}"
      index += 1
    used.add(name.casefold())
    result[photo] = folder / f"{name}.xlsx"
  return result


def load_inventory_output(path):
  """Read one exported inventory workbook for editing in the inventory tab."""
  path = Path(path)
  wb = load_workbook(path, read_only=True, data_only=True)
  try:
    ws = next((s for s in wb if tuple(s.cell(1, c).value for c in range(1, 8)) == HEADERS), None)
    if ws is None:
      raise ValueError("File cần đúng 7 cột nhập hàng ở dòng đầu.")
    rows = []
    for sheet_row, values in enumerate(ws.iter_rows(min_row=2, max_col=7, values_only=True), 2):
      code = str(values[0] or "").strip()
      if not code:
        continue
      rows.append(CountRow(str(path), sheet_row, str(values[1] or ""), str(values[2] or ""),
                           str(values[6] if values[6] is not None else ""), numeric(values[6]), code))
    return rows
  finally:
    wb.close()


def save_inventory_output_row(path, row, catalog, price_source="Giá vốn", original_code=None):
  """Save a changed row while retaining the template's sheets, tables and formatting."""
  path = Path(path)
  item = catalog[row.code]
  price = item.cost if price_source == "Giá vốn" else item.sale
  if numeric(row.qty) is None:
    raise ValueError("Số lượng không hợp lệ.")
  wb = load_workbook(path)
  try:
    ws = next((s for s in wb if tuple(s.cell(1, c).value for c in range(1, 8)) == HEADERS), None)
    if ws is None or row.stt < 2 or ws.cell(row.stt, 1).value is None or (
      original_code is not None and str(ws.cell(row.stt, 1).value) != original_code
    ):
      raise ValueError("Dòng trong file đã thay đổi. Hãy mở lại file để sửa.")
    changed_code = str(ws.cell(row.stt, 1).value) != item.code
    if changed_code and price is None:
      raise ValueError("Mã hàng thiếu đơn giá hợp lệ.")
    values = [(1, item.code), (2, item.name), (3, item.unit), (7, row.qty)]
    if changed_code:
      values.append((4, price))
    for column, value in values:
      ws.cell(row.stt, column).value = value
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".xlsx", delete=False) as temp:
      staged = Path(temp.name)
    try:
      wb.save(staged)
      staged.replace(path)
    finally:
      staged.unlink(missing_ok=True)
  finally:
    wb.close()
