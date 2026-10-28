# -*- coding: utf-8 -*-
"""Lấy mã PDN từ tên file."""
import unittest
from pathlib import Path

from thuc_xuat.image_match import extract_pdn, pdn_from_excel


class PdnTests(unittest.TestCase):
  def test_extract_from_filename(self):
    self.assertEqual(
      extract_pdn("DanhSachChiTietDatHangNhap_PDN016535_KV30092026-095607-185.xlsx"),
      "PDN016535",
    )
    self.assertEqual(extract_pdn("pdn014331_anh.jpg"), "PDN014331")
    self.assertIsNone(extract_pdn("canvas-image.jpg"))

  def test_pdn_from_excel(self):
    self.assertEqual(pdn_from_excel(Path("PDN016505_x.xlsx")), "PDN016505")
    self.assertIsNone(pdn_from_excel(Path("khong-ma.xlsx")))


if __name__ == "__main__":
  unittest.main()
