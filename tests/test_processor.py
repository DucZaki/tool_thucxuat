# -*- coding: utf-8 -*-
import unittest

from thuc_xuat.processor import (
  Product,
  build_output,
  carton_review_message,
  expected_qty,
  load_products,
  pack_factor,
  parse_qty,
)


class ParseQtyTests(unittest.TestCase):
  def test_skip_x_and_empty(self):
    self.assertIsNone(parse_qty("X"))
    self.assertIsNone(parse_qty("x"))
    self.assertIsNone(parse_qty(""))
    self.assertIsNone(parse_qty(None))

  def test_multiply_when_slip_has_x(self):
    cases = {
      "5T x 24": 120,
      "3Tx20": 60,
      "2T x 18": 36,
      "3T*3": 9,
      "1Tx14": 14,
      "5TX24": 120,
      "37X6": 18,  # model đọc chữ T thành 7
      "3TX6": 18,
      "21x24": 504,
    }
    for raw, expected in cases.items():
      with self.subTest(raw=raw):
        self.assertEqual(parse_qty(raw), expected)

  def test_single_numbers_and_inner_units(self):
    cases = {
      "10 gói": 10,
      "30kg": 30,
      "20 vỉ": 20,
      "18 quả": 18,
      "5b x 30kg": 5,  # 30kg là trọng lượng, không nhân
      "210.19": 210.19,
      "1T": 1,
      "16": 16,
      "48 kg": 48,
    }
    for raw, expected in cases.items():
      with self.subTest(raw=raw):
        self.assertEqual(parse_qty(raw), expected)

  def test_keep_quantities_already_in_excel_unit(self):
    self.assertEqual(parse_qty("2T", 7, "thùng 40 gói"), 2)
    self.assertEqual(parse_qty("1T", 1, "thùng 6 hộp"), 1)
    self.assertEqual(parse_qty("12", 12, "thùng 20 hộp"), 12)
    self.assertEqual(parse_qty("4T", 4, "thùng 11 hộp"), 4)
    self.assertEqual(parse_qty("10T", 10, "thùng 20 hộp"), 10)
    self.assertEqual(parse_qty("30cm", 3, "thùng 10 con"), 30)
    self.assertEqual(parse_qty("80G", 7, "thùng 40 gói"), 80)
    self.assertEqual(parse_qty("2T*3", 7, "thùng 40 gói"), 6)

  def test_convert_only_matching_explicit_package_units(self):
    cases = [
      ("120 gói", "thùng(T)/60 gói", 2),
      ("120GOI", "Thùng (60 gói)", 2),
      ("90 gói", "thùng 60 gói", 1.5),
      ("24 gói", "thùng 40 gói", 0.6),
      ("120 gói", "thùng 60 hộp", None),
      ("120 gói", "thùng (40–60 gói)", None),
      ("120 gói", "thùng 10 lốc x 6 gói", None),
      ("120 gói", "thùng (60)", None),
      ("120 gói", "thùng 0 gói", None),
      ("12 kg", "thùng 6 kg", 12),
    ]
    for raw, unit, expected in cases:
      with self.subTest(raw=raw, unit=unit):
        self.assertEqual(parse_qty(raw, 7, unit), expected)

  def test_plus_after_thung_is_ocr_multiplication(self):
    self.assertEqual(parse_qty("5T+8"), 40)
    self.assertEqual(parse_qty("5 thùng + 8"), 40)
    self.assertEqual(parse_qty("1,5T + 8"), 12)
    self.assertEqual(parse_qty("=5+8"), 13)
    self.assertEqual(expected_qty(5, "5T+8"), 40)

  def test_carton_size_from_product_name_only_when_target_is_carton(self):
    cases = [
      ("120 cái", "thùng", "Hàng ( thùng/60 cái)", 2),
      ("90 gói", "Thùng (T)", "Hàng 80g (thùng 60 gói)", 1.5),
      ("120 gói", "thùng", "Hàng (thùng(T)/60goi)", 2),
      ("120 cái", "cái", "Hàng (thùng/60 cái)", 120),
      ("120 cái", "thùng 40 cái", "Hàng (thùng/60 cái)", None),
      ("120 cái", "thùng 40 gói", "Hàng (thùng/60 cái)", None),
      ("120 cái", "thùng", "Hàng (thùng 40–60 cái)", None),
      ("120 cái", "thùng", "Hàng (thùng 60 cái) hoặc (thùng 40 cái)", None),
      ("120 gói", "thùng", "Hàng (thùng 60 cái)", None),
      ("120", "thùng", "Hàng (thùng 60 gói)", 120),
      ("620", "thùng 60 cái", "", 620),
      ("620 chiếc", "thùng", "Bánh 100g (60 cái/thùng)", 620 / 60),
      ("620 cái", "60 chiếc/thùng", "Bánh (thùng 60 cái)", 620 / 60),
      ("620 gói", "T", "Bánh (60 gói / thùng)", 620 / 60),
      ("620 gói", "thùng", "Bánh (30 gói/hộp)", None),
      ("620 gói", "thùng (40)", "Bánh (60 gói/thùng)", None),
      ("620 gói", "thùng", "Bánh (thùng 10 hộp x 6 gói)", None),
      ("620 gói", "gói", "Bánh (thùng 60 gói)", 620),
    ]
    for raw, unit, name, expected in cases:
      with self.subTest(raw=raw, unit=unit, name=name):
        self.assertEqual(parse_qty(raw, unit=unit, product_name=name), expected)

  def test_pack_factor_values(self):
    self.assertEqual(pack_factor("thùng 40 gói"), 40)
    self.assertEqual(pack_factor("thùng 24lon"), 24)
    self.assertEqual(pack_factor("thùng 12 lốc"), 12)
    self.assertEqual(pack_factor("thùng 10kg/11 con"), 11)
    self.assertIsNone(pack_factor("túi 2kg"))
    self.assertIsNone(pack_factor("gói"))
    self.assertIsNone(pack_factor("NỬA con (cửa hàng)"))
    self.assertIsNone(pack_factor(None))

  def test_decimal_kept(self):
    self.assertEqual(parse_qty("5,5"), 5.5)
    self.assertEqual(parse_qty("5.5"), 5.5)
    self.assertEqual(parse_qty("12,25 kg"), 12.25)
    self.assertEqual(parse_qty("10.0"), 10)

  def test_tick_uses_ordered(self):
    self.assertEqual(parse_qty("✓", 5), 5)
    self.assertEqual(parse_qty("✓", 5.5), 5.5)
    self.assertEqual(parse_qty("v", 5), 5)

  def test_y_is_x_not_tick(self):
    # Phiếu không có chữ Y: nét gạch chéo X bị đọc thành Y → vẫn là bỏ dòng.
    self.assertIsNone(parse_qty("Y", 15))
    self.assertIsNone(parse_qty("y", 15))
    self.assertIsNone(parse_qty("5 Y", 15))
    self.assertIsNone(parse_qty("10y", 15))
    self.assertIsNone(parse_qty("5X", 15))

  def test_bare_numbers_preserved_and_multiplier_ocr_repaired(self):
    self.assertEqual(parse_qty("47", 4, "thùng 50 gói"), 47)
    self.assertEqual(parse_qty("57", 5, "thùng"), 57)
    self.assertEqual(parse_qty("17", 1, "thùng 12 lốc"), 17)
    # "21x24" của phiếu đặt 2 thùng = 2T x 24
    self.assertEqual(parse_qty("21x24", 2, "thùng (24 hộp)"), 48)
    self.assertEqual(parse_qty("17x24", 1, "thùng (24 hộp)"), 24)
    self.assertEqual(parse_qty("37X6", 20, "thùng 6 hộp"), 18)
    # Số đọc đúng thì không sửa: đặt 50 thùng, phiếu ghi 57
    self.assertEqual(parse_qty("57", 50, "thùng"), 57)
    # Không có SL đặt thì giữ nguyên
    self.assertEqual(parse_qty("47", None, "thùng 50 gói"), 47)

  def test_expected_qty(self):
    self.assertEqual(expected_qty(12, "12"), 12)
    self.assertEqual(expected_qty(5, "5T x 24"), 120)
    self.assertEqual(expected_qty(2, "21x24"), 48)
    self.assertIsNone(expected_qty(None))
    self.assertIsNone(expected_qty(0))


class LoadProductsTests(unittest.TestCase):
  def test_skip_blank_and_zero_width_code(self):
    import tempfile
    from pathlib import Path

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Mã hàng", "Tên hàng", "ĐVT", "Số lượng"])
    ws.append(["   ", "dòng rác", "gói", 1])
    ws.append(["\u200b", "mã rỗng", "gói", 2])
    ws.append(["SP1", "hàng thật", "gói", 3])
    with tempfile.TemporaryDirectory() as d:
      p = Path(d) / "t.xlsx"
      wb.save(p)
      products = load_products(p)
    self.assertEqual([x.code for x in products], ["SP1"])
    # STT = số dòng trên phiếu (dòng Excel 4 → STT 3), không dồn lại.
    self.assertEqual(products[0].stt, 3)

  def test_text_quantity_is_coerced(self):
    import tempfile
    from pathlib import Path

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Mã hàng", "Tên hàng", "ĐVT", "Số lượng"])
    ws.append(["SP1", "hàng 1", "thùng", "12"])       # định dạng text
    ws.append(["SP2", "hàng 2", "thùng", "1.000"])    # phân cách hàng nghìn
    ws.append(["SP3", "hàng 3", "kg", "5,5"])
    with tempfile.TemporaryDirectory() as d:
      p = Path(d) / "t.xlsx"
      wb.save(p)
      products = load_products(p)
    self.assertEqual(products[0].ordered_qty, 12)
    self.assertEqual(products[1].ordered_qty, 1000)
    self.assertEqual(products[2].ordered_qty, 5.5)
    # Dòng chỉ tick phải ra số (ghi Excel là số, không phải chữ)
    self.assertEqual(parse_qty("✓", products[0].ordered_qty), 12)


class BuildOutputTests(unittest.TestCase):
  def test_unresolved_carton_not_exported_and_identified_by_stt(self):
    products = [Product(stt, str(stt), "Bánh", 10, "thùng 60 gói") for stt in (1, 2, 3, 4)]
    included, skipped = build_output(products, {1: "620 cái", 2: "X", 3: "620 gói", 4: "620"})
    self.assertEqual([row.qty for row in included], [620 / 60, 620])
    self.assertEqual([row.raw for row in skipped], ["620 cái", "X"])
    self.assertIn("STT 1.", carton_review_message(skipped))

  def test_export_uses_the_carton_size_in_the_product_name(self):
    products = [
      Product(104, "001", "Sản phẩm A (thùng/60 cái)", 2, "thùng"),
      Product(105, "002", "Lẩu Thái Aji Quick (thùng 60 gói)", 2, "thùng"),
    ]
    included, skipped = build_output(products, {104: "120 cái", 105: "90 gói"})
    self.assertEqual([row.qty for row in included], [2, 1.5])
    self.assertFalse(skipped)

  def test_mixed_rows(self):
    products = [
      Product(1, "A", "sp1", 10),
      Product(2, "B", "sp2", 5),
      Product(3, "C", "sp3", 1),
    ]
    included, skipped = build_output(products, {1: "10", 2: "X", 3: "3T"})
    self.assertEqual(len(included), 2)
    self.assertEqual(included[0].qty, 10)
    self.assertEqual(included[1].qty, 3)
    self.assertEqual([r.stt for r in skipped], [2])


if __name__ == "__main__":
  unittest.main()
