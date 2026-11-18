"""Regressions for inventory matching and template exports."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.table import Table

from thuc_xuat.inventory import (
  CatalogItem, CountRow, HEADERS, candidates_for, export_inventory,
  load_catalog, match_inventory, normalize, quantity, read_inventory_page,
  quantity_for_item,
  WrongInventoryImage,
)
from thuc_xuat.output_edit import load_sheet, save_sheet
from thuc_xuat.processor import OutputRow, save_output


class InventoryTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.folder = Path(self.temp.name)
    self.item = CatalogItem("00123", "Chả cá Quy Nhơn 500g", "gói", 120, 180, normalize("Chả cá Quy Nhơn 500g"))
    self.catalog = {self.item.code: self.item}

  def template(self):
    path = self.folder / "template.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append([*HEADERS, "Lô 1", "Hạn sử dụng 1", "Số lượng 1", "Lô 2", "Hạn sử dụng 2", "Số lượng 2"])
    ws.append(["00483", None, None, 100000, 10000, None, 3, "EXAMPLE"])
    ws.append(["00560", None, None, 200000, 10000, None, 10])
    ws["D2"].number_format = "#,##0.0####"
    ws["G2"].number_format = "#,0.##0"
    ws.add_table(Table(displayName="PurchaseOrderTemplate", ref="A1:M3"))
    wb.save(path)
    wb.close()
    return path

  def test_export_keeps_template_types_and_removes_examples(self):
    template = self.template()
    row = CountRow("page", 1, "chả cá", "", "1+8", 9, "00123")
    decimal_row = CountRow("page", 2, "chả cá", "", "5.5", 5.5, "00123")
    target = self.folder / "result.xlsx"
    export_inventory([row, decimal_row], self.catalog, template, target)
    wb = load_workbook(target)
    try:
      ws = wb.active
      self.assertEqual(list(ws.values)[1], ("00123", self.item.name, "gói", 120, None, None, 9, None, None, None, None, None, None))
      self.assertEqual(ws["A2"].data_type, "s")
      self.assertEqual(ws["G2"].data_type, "n")
      self.assertEqual(ws["G2"].number_format, "General")
      self.assertEqual((ws["G3"].value, ws["G3"].data_type, ws["G3"].number_format), (5.5, "n", "General"))
      self.assertEqual(ws["D2"].number_format, "#,##0.0####")
      self.assertEqual(ws.tables["PurchaseOrderTemplate"].ref, "A1:M3")
      self.assertEqual(ws.max_row, 3)
    finally:
      wb.close()
    source = load_workbook(template)
    self.assertEqual(source.active["A2"].value, "00483")
    source.close()

  def test_unresolved_or_mixed_units_cannot_export(self):
    template = self.template()
    with self.assertRaises(ValueError):
      quantity("1 khay 3 con")
    for row in (CountRow("", 1, "?", "", "3", 3), CountRow("", 1, "?", "", "", None, "00123")):
      with self.assertRaises(ValueError):
        export_inventory([row], self.catalog, template, self.folder / "out.xlsx")
    self.assertFalse((self.folder / "out.xlsx").exists())

  def test_quantities_arithmetic_zero_and_units(self):
    for text, expected in (("1 + 8", 9), ("9+2", 11), ("3*8", 24), ("13 con", 13), ("0", 0), ("2,5", 2.5)):
      self.assertEqual(quantity(text), expected)
    for text in ("", "-1", "1/0", "x", "__import__('os')", "1 khay 3 con"):
      with self.assertRaises(ValueError):
        quantity(text)

  def test_model_cannot_invent_catalog_code(self):
    row = CountRow("", 1, "chả cá", "", "9", 9)
    nim = MagicMock()
    nim.read.return_value = json.dumps({"matches": [{"id": 0, "code": "made-up", "confidence": 1}]})
    match_inventory([row], self.catalog, nim)
    self.assertEqual(row.code, "00123")
    self.assertTrue(row.issue)

  def test_confidence_note_does_not_require_confirmation_for_export(self):
    row = CountRow("", 1, "chả cá", "", "9", 9, "00123", "Tên gần khớp")
    target = self.folder / "no-confirmation.xlsx"
    export_inventory([row], self.catalog, self.template(), target)
    self.assertTrue(target.is_file())

  def test_equivalent_package_counts_follow_accepted_example(self):
    bird = CatalogItem("BIRD", "Chim cút", "khay (3 con)", 810, 1000, "chim cut")
    self.assertEqual(quantity_for_item("1 khay 3 con", bird), 1)
    self.assertEqual(quantity_for_item("1 khay + 3 con", bird), 2)
    with self.assertRaises(ValueError):
      quantity_for_item("1 khay 4 con", bird)

  def test_review_can_search_by_exact_code(self):
    row = CountRow("", 0, "00123", "", "")
    self.assertEqual(candidates_for(row, self.catalog)[0].code, "00123")

  def test_same_name_different_units_requires_review(self):
    variant = CatalogItem("002", self.item.name, "thùng", 1000, 2000, self.item.normalized)
    self.catalog[variant.code] = variant
    row = CountRow("", 1, "chả cá Quy Nhơn", "", "9", 9)
    nim = MagicMock()
    nim.read.return_value = json.dumps({"matches": [{"id": 0, "code": "00123", "confidence": 0.99}]})
    match_inventory([row], self.catalog, nim)
    self.assertEqual(row.code, "00123")
    self.assertTrue(row.issue)

  def test_read_overlap_conflict_requires_review_and_keeps_unnumbered(self):
    nim = MagicMock()
    responses = [
      {"rows": [{"stt": 1, "name": "Chả cá", "actual": "1+8"}]},
      {"rows": [{"stt": 1, "name": "Chả cá", "actual": "8"}, {"stt": 0, "name": "Heo quay", "actual": "2"}]},
    ]
    nim.read.side_effect = [json.dumps(data) for data in responses]
    with patch("thuc_xuat.inventory.split_image", return_value=[MagicMock(data=b"image"), MagicMock(data=b"image")]):
      rows = read_inventory_page("photo.jpg", nim)
    self.assertEqual(len(rows), 2)
    self.assertTrue(rows[0].issue)
    self.assertIsNone(rows[0].qty)
    self.assertEqual(rows[1].qty, 2)

  def test_inventory_retries_truncated_reply_then_merges_complete_blocks(self):
    nim = MagicMock()
    first = {"rows": [{"stt": 1, "name": "Chả cá", "actual": "1+8"}]}
    second = {"rows": [{"stt": 1, "name": "Chả cá", "actual": "9"},
                       {"stt": 0, "name": "Heo quay", "actual": "2"}]}
    nim.read.side_effect = ['{"rows":[', "### Kết quả\n```JSON\n" + json.dumps(first) + "\n```\n" + json.dumps(second)]
    with patch("thuc_xuat.inventory.split_image", return_value=[MagicMock(data=b"image")]):
      rows = read_inventory_page("photo.jpg", nim)
    self.assertEqual([(row.name, row.qty) for row in rows], [("Chả cá", 9), ("Heo quay", 2)])
    self.assertEqual(nim.read.call_count, 2)

  def test_transfer_photo_gets_direct_instruction_in_inventory(self):
    nim = MagicMock()
    nim.read.return_value = '{"kind":"transfer"}'
    with patch("thuc_xuat.inventory.split_image", return_value=[MagicMock(data=b"image")]):
      with self.assertRaisesRegex(WrongInventoryImage, "tab Thực xuất"):
        read_inventory_page("photo.jpg", nim)
    nim.read.assert_called_once()

  def test_inventory_merges_multiple_match_blocks(self):
    nim = MagicMock()
    nim.read.return_value = ('{"matches":[{"id":0,"code":"00123"}]}\n'
                             '{"matches":[{"id":1,"code":"00123"}]}')
    rows = [CountRow("", i, "Chả cá", "", str(i)) for i in (1, 2)]
    match_inventory(rows, self.catalog, nim)
    self.assertEqual([(row.code, row.qty) for row in rows], [("00123", 1), ("00123", 2)])
    nim.read.assert_called_once()

  def test_catalog_reads_every_sheet_and_numeric_cost(self):
    path = self.folder / "catalog.xlsx"
    wb = Workbook()
    for index in range(2):
      ws = wb.active if index == 0 else wb.create_sheet()
      ws.append(["Mã hàng", "Tên hàng", "ĐVT", "Giá vốn", "Giá bán"])
      ws.append([f"00{index}", f"Hàng {index}", "gói", "120", 180])
    wb.save(path)
    wb.close()
    result = load_catalog(path)
    self.assertEqual(set(result), {"000", "001"})
    self.assertEqual(result["000"].cost, 120)

  def test_new_transfer_headers_native_table_and_old_file_editing(self):
    path = self.folder / "transfer.xlsx"
    save_output([OutputRow(1, "00123", "hàng", "3", 3)], path)
    wb = load_workbook(path)
    self.assertEqual(tuple(wb.active.values)[0], ("Mã hàng", "Số lượng chuyển"))
    self.assertEqual(wb.active.tables["ThucXuatTable"].ref, "A1:B2")
    wb.close()
    old = self.folder / "old.xlsx"
    wb = Workbook()
    wb.active.append(["Mã sản phẩm", "Thực xuất"])
    wb.active.append(["00123", 3])
    wb.save(old)
    wb.close()
    sheet = load_sheet(old)
    self.assertEqual(sheet.rows[0].code, "00123")
    save_sheet(sheet)
    wb = load_workbook(old)
    self.assertEqual(tuple(wb.active.values)[0], ("Mã hàng", "Số lượng chuyển"))
    self.assertIn("ThucXuatTable", wb.active.tables)
    wb.close()


if __name__ == "__main__":
  unittest.main()
