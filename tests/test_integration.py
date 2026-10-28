# -*- coding: utf-8 -*-
"""Test tích hợp — đối chiếu quy tắc nghiệp vụ user yêu cầu."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from thuc_xuat.pipeline import process_batch_like_chat
from thuc_xuat.processor import (
  OutputRow,
  Product,
  build_output,
  default_output_path,
  load_products,
  parse_qty,
  save_output,
)


def _write_source_excel(path: Path, rows: list[tuple[str, str, float | int]]) -> None:
  """rows: (code, name, ordered_qty)"""
  wb = Workbook()
  ws = wb.active
  ws.append(["Mã hàng", "Tên hàng", "ĐVT", "Số lượng"])
  for code, name, qty in rows:
    ws.append([code, name, "cái", qty])
  wb.save(str(path))


class ParseQtyRulesTests(unittest.TestCase):
  """Quy tắc: X bỏ, số đầu tiên, tick → số đặt."""

  def test_x_variants_skipped(self):
    for raw in ("X", "x", " X "):
      self.assertIsNone(parse_qty(raw))

  def test_empty_and_none_skipped(self):
    self.assertIsNone(parse_qty(None))
    self.assertIsNone(parse_qty(""))
    self.assertIsNone(parse_qty("   "))

  def test_multiply_and_units_from_real_slip_formats(self):
    cases = {
      "5T x 24": 120,
      "3Tx20": 60,
      "2T x 18": 36,
      "3T*3": 9,
      "10 gói": 10,
      "30kg": 30,
      "20 vỉ": 20,
      "18 quả": 18,
      "5b x 30kg": 5,
      "210.19": 210.19,
      "1Tx14": 14,
      "1T": 1,
      "16": 16,
      "48 kg": 48,
      "37X6": 18,
      "3TX6": 18,
      "5TX24": 120,
    }
    for raw, expected in cases.items():
      with self.subTest(raw=raw):
        self.assertEqual(parse_qty(raw), expected)

  def test_unit_factor_from_detail_file(self):
    self.assertEqual(parse_qty("2T", 7, "thùng 40 gói"), 80)
    self.assertEqual(parse_qty("3T", 5, "túi 2kg"), 3)
    self.assertEqual(parse_qty("24 gói", 7, "thùng 40 gói"), 24)

  def test_tick_marks_use_ordered_qty(self):
    for mark in ("✓", "v", "V", ">", "/", "√"):
      with self.subTest(mark=mark):
        self.assertEqual(parse_qty(mark, 12), 12)

  def test_tick_without_ordered_is_skipped(self):
    self.assertIsNone(parse_qty("✓", None))

  def test_no_number_no_tick_skipped(self):
    self.assertIsNone(parse_qty("Hộp"))


class SttMappingTests(unittest.TestCase):
  """STT trên phiếu = thứ tự dòng Excel (dòng 2 = STT 1)."""

  def test_load_products_stt_from_row_order(self):
    with tempfile.TemporaryDirectory() as tmp:
      src = Path(tmp) / "order.xlsx"
      _write_source_excel(
        src,
        [
          ("CODE1", "SP 1", 10),
          ("CODE2", "SP 2", 5),
          ("CODE3", "SP 3", 1),
        ],
      )
      products = load_products(src)
      self.assertEqual([(p.stt, p.code) for p in products], [(1, "CODE1"), (2, "CODE2"), (3, "CODE3")])

  def test_load_kiotviet_export_on_disk_if_present(self):
    path = Path(r"d:\DanhSachChiTietDatHangNhap_PDN014331_KV13072026-102238-580.xlsx")
    if not path.exists():
      self.skipTest("File mẫu không có trên ổ D")
    products = load_products(path)
    self.assertGreaterEqual(len(products), 80)
    self.assertEqual(products[0].code, "SP1290344564")


class BuildOutputRulesTests(unittest.TestCase):
  def test_stt_not_in_map_is_skipped(self):
    products = [Product(1, "A", "n1", 10), Product(2, "B", "n2", 5), Product(3, "C", "n3", 1)]
    included, skipped = build_output(products, {1: "10", 3: "3T"})
    self.assertEqual([r.stt for r in included], [1, 3])
    self.assertEqual([r.stt for r in skipped], [2])

  def test_x_and_empty_skipped(self):
    products = [Product(1, "A", "n1", 10), Product(2, "B", "n2", 5)]
    included, skipped = build_output(products, {1: "X", 2: ""})
    self.assertEqual(len(included), 0)
    self.assertEqual(len(skipped), 2)

  def test_tick_uses_ordered_in_build(self):
    products = [Product(1, "A", "n1", 7)]
    included, _ = build_output(products, {1: "✓"})
    self.assertEqual(len(included), 1)
    self.assertEqual(included[0].qty, 7)

  def test_pdn014044_scenario(self):
    """Tham chiếu chat: chỉ STT 13 có ảnh → 1 dòng, qty 210.19 (giữ số lẻ)."""
    products = [Product(i, f"C{i}", f"sp{i}", 3) for i in range(1, 14)]
    products[12] = Product(13, "2000082", "Đùi gà có xương", 3)
    included, skipped = build_output(products, {13: "210.19"})
    self.assertEqual(len(included), 1)
    self.assertEqual(included[0].code, "2000082")
    self.assertEqual(included[0].qty, 210.19)
    self.assertEqual(len(skipped), 12)


class OutputFileTests(unittest.TestCase):
  def test_output_has_two_columns_only(self):
    with tempfile.TemporaryDirectory() as tmp:
      out = Path(tmp) / "out.xlsx"
      save_output(
        [
          OutputRow(1, "SP1", "n", "10", 10),
          OutputRow(2, "SP2", "n", "5T", 5),
        ],
        out,
      )
      wb = load_workbook(out, read_only=True)
      ws = wb.active
      self.assertEqual([ws.cell(1, 1).value, ws.cell(1, 2).value], ["Mã sản phẩm", "Thực xuất"])
      self.assertEqual(ws.max_column, 2)
      self.assertEqual(ws.cell(2, 1).value, "SP1")
      self.assertEqual(ws.cell(2, 2).value, 10)
      self.assertEqual(ws.cell(3, 2).value, 5)
      wb.close()

  def test_default_output_path_pdn(self):
    src = Path(r"d:\DanhSachChiTietDatHangNhap_PDN014324_KV13072026.xlsx")
    out = default_output_path(src)
    self.assertEqual(out.name, "PDN014324_MaSanPham_ThucXuat.xlsx")

  def test_output_with_name_column(self):
    with tempfile.TemporaryDirectory() as tmp:
      out = Path(tmp) / "out.xlsx"
      save_output(
        [OutputRow(1, "SP1", "Bánh tráng", "10", 10)],
        out,
        include_name=True,
      )
      wb = load_workbook(out, read_only=True)
      ws = wb.active
      self.assertEqual(
        [ws.cell(1, i).value for i in (1, 2, 3)],
        ["Mã sản phẩm", "Tên sản phẩm", "Thực xuất"],
      )
      self.assertEqual(ws.max_column, 3)
      self.assertEqual(
        [ws.cell(2, i).value for i in (1, 2, 3)], ["SP1", "Bánh tráng", 10]
      )
      wb.close()

  def test_default_output_path_with_name_in_custom_dir(self):
    with tempfile.TemporaryDirectory() as tmp:
      src = Path(tmp) / "DanhSachChiTietDatHangNhap_PDN014324_KV13072026.xlsx"
      out = default_output_path(src, out_dir=Path(tmp) / "KetQua", include_name=True)
      self.assertEqual(out.name, "PDN014324_MaSanPham_ThucXuat_KemTen.xlsx")
      self.assertEqual(out.parent, Path(tmp) / "KetQua")


class PipelineTests(unittest.TestCase):
  def test_process_batch_end_to_end_with_mock_ai(self):
    with tempfile.TemporaryDirectory() as tmp:
      src = Path(tmp) / "DanhSachChiTietDatHangNhap_PDN099999_test.xlsx"
      _write_source_excel(
        src,
        [
          ("SP001", "Hang 1", 10),
          ("SP002", "Hang 2", 5),
          ("SP003", "Hang 3", 8),
        ],
      )
      fake = {"PDN099999": {1: "10", 2: "X", 3: "✓"}}
      with patch("thuc_xuat.pipeline.ensure_ready"), patch(
        "thuc_xuat.pipeline.extract_orders_from_images", return_value=fake
      ):
        batch = process_batch_like_chat([src], [Path(tmp) / "fake.jpg"])

      self.assertEqual(batch.ok_count, 1)
      result = batch.results[0]
      self.assertEqual(result.product_count, 3)
      self.assertEqual(result.extracted_stt, 3)
      self.assertEqual(len(result.included), 2)
      self.assertEqual(result.included[0].code, "SP001")
      self.assertEqual(result.included[0].qty, 10)
      self.assertEqual(result.included[1].code, "SP003")
      self.assertEqual(result.included[1].qty, 8)
      self.assertTrue(result.output_path.exists())

      wb = load_workbook(result.output_path, read_only=True)
      ws = wb.active
      self.assertEqual(ws.max_row, 3)  # header + 2 rows
      wb.close()

  def test_process_writes_to_output_dir_with_name_column(self):
    with tempfile.TemporaryDirectory() as tmp:
      src = Path(tmp) / "DanhSachChiTietDatHangNhap_PDN033333_test.xlsx"
      _write_source_excel(src, [("SP900", "Bánh", 4)])
      out_dir = Path(tmp) / "KetQua"
      fake = {"PDN033333": {1: "2"}}
      with patch("thuc_xuat.pipeline.ensure_ready"), patch(
        "thuc_xuat.pipeline.extract_orders_from_images", return_value=fake
      ):
        batch = process_batch_like_chat(
          [src], [Path(tmp) / "fake.jpg"], output_dir=out_dir, include_name=True
        )

      result = batch.results[0]
      self.assertEqual(result.output_path.parent, out_dir)
      self.assertEqual(
        result.output_path.name, "PDN033333_MaSanPham_ThucXuat_KemTen.xlsx"
      )
      self.assertTrue(result.include_name)
      wb = load_workbook(result.output_path, read_only=True)
      ws = wb.active
      self.assertEqual(ws.max_column, 3)
      self.assertEqual(ws.cell(2, 2).value, "Bánh")
      wb.close()

  def test_process_batch_like_chat_mock(self):
    with tempfile.TemporaryDirectory() as tmp:
      src1 = Path(tmp) / "DanhSachChiTietDatHangNhap_PDN111111_a.xlsx"
      src2 = Path(tmp) / "DanhSachChiTietDatHangNhap_PDN222222_b.xlsx"
      _write_source_excel(src1, [("A1", "n", 1)])
      _write_source_excel(src2, [("B1", "n", 2)])
      fake = {
        "PDN111111": {1: "1"},
        "PDN222222": {1: "2"},
      }
      with patch("thuc_xuat.pipeline.ensure_ready"), patch(
        "thuc_xuat.pipeline.extract_orders_from_images", return_value=fake
      ):
        batch = process_batch_like_chat([src1, src2], ["a.jpg"])
      self.assertEqual(batch.ok_count, 2)


class ReferenceOutputRegressionTests(unittest.TestCase):
  """Đối chiếu file kết quả đã làm thủ công trước đó (nếu còn trong project)."""

  REF_DIR = Path(__file__).resolve().parent.parent.parent

  def _read_output(self, name: str) -> list[tuple[str, int]]:
    path = self.REF_DIR / f"{name}_MaSanPham_ThucXuat.xlsx"
    if not path.exists():
      self.skipTest(f"Không có file tham chiếu {path.name}")
    wb = load_workbook(path, read_only=True)
    ws = wb.active
    rows = []
    for r in range(2, ws.max_row + 1):
      code = ws.cell(r, 1).value
      qty = ws.cell(r, 2).value
      if code is not None:
        rows.append((str(code), int(qty)))
    wb.close()
    return rows

  def test_reference_row_counts(self):
    expected = {
      "PDN014044": 1,
      "PDN014324": 123,
      "PDN014331": 71,
    }
    for pdn, count in expected.items():
      with self.subTest(pdn=pdn):
        rows = self._read_output(pdn)
        self.assertEqual(len(rows), count)

  def test_reference_qty_are_integers_not_strings(self):
    for pdn in ("PDN014324", "PDN014331"):
      with self.subTest(pdn=pdn):
        rows = self._read_output(pdn)
        if not rows:
          continue
        for code, qty in rows[:5]:
          self.assertIsInstance(qty, int, f"{pdn} {code} qty phải là số nguyên")


if __name__ == "__main__":
  unittest.main()
