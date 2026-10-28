# -*- coding: utf-8 -*-
import unittest

from thuc_xuat.processor import Product, build_output, pack_factor, parse_qty


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

  def test_unit_factor_from_detail_file(self):
    # ĐVT có số quy đổi -> nhân khi phiếu ghi đơn vị ngoài
    self.assertEqual(parse_qty("2T", 7, "thùng 40 gói"), 80)
    self.assertEqual(parse_qty("2T", 7, "thùng (20 khay)"), 40)
    self.assertEqual(parse_qty("2T", 7, "thùng (40)"), 80)
    self.assertEqual(parse_qty("3T", 5, "thùng 10kg/11 con"), 33)
    # Đơn vị lẻ ghi rõ trên phiếu -> giữ nguyên, không nhân
    self.assertEqual(parse_qty("80G", 7, "thùng 40 gói"), 80)
    self.assertEqual(parse_qty("24 gói", 7, "thùng 40 gói"), 24)
    # Trọng lượng trong ĐVT không phải hệ số
    self.assertEqual(parse_qty("3T", 5, "túi 2kg"), 3)
    self.assertEqual(parse_qty("3T", 5, "thùng 10kg"), 3)
    self.assertEqual(parse_qty("3T", 5, "cái (600-700g)"), 3)
    self.assertEqual(parse_qty("3T", 5, "kg"), 3)
    # Có dấu nhân thì dấu nhân thắng ĐVT
    self.assertEqual(parse_qty("2T*3", 7, "thùng 40 gói"), 6)

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
    self.assertEqual(parse_qty("Y", 15), 15)
    self.assertEqual(parse_qty("y", 15), 15)
    self.assertEqual(parse_qty("✓", 5.5), 5.5)


class BuildOutputTests(unittest.TestCase):
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
