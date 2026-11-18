"""Inventory regressions against the supplied catalog and accepted output."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openpyxl import load_workbook

from thuc_xuat.inventory import (
  CountRow, candidates_for, export_inventory, load_catalog, match_inventory,
)
from thuc_xuat.inventory_defaults import bundled_file, inventory_inputs


class BundledInventoryTests(unittest.TestCase):
  def test_inputs_resolve_after_install_and_missing_override(self):
    with patch("thuc_xuat.inventory_defaults.load_config", return_value={"inventory_catalog": "missing.xlsx"}):
      catalog, template = inventory_inputs()
    self.assertTrue(catalog.is_file())
    self.assertTrue(template.is_file())
    with tempfile.TemporaryDirectory() as tmp:
      with patch("thuc_xuat.inventory_defaults.sys._MEIPASS", tmp, create=True):
        self.assertEqual(bundled_file("MauFileNhapHang.xlsx"), Path(tmp) / "data/MauFileNhapHang.xlsx")

  def test_accepted_page_exports_31_rows_without_confirmation(self):
    catalog = load_catalog(bundled_file("DanhMucSanPham.xlsx"))
    wb = load_workbook(bundled_file("NhapHangThamChieu.xlsx"), read_only=True, data_only=True)
    try:
      expected = list(wb.active.iter_rows(min_row=2, values_only=True))
    finally:
      wb.close()
    names = "Giò heo cuộn|Chả Chiên|Giò bò Deli|Xá xíu|Tai Deli|bì Deli|Chả cá Quy Nhơn 500g|Chả quế Deli|Chả lụa Deli|Giò bì TDM|Giò tai TDM|Giò bò TDM|Vịt không đầu|Vịt cắt sẵn|Chim cút|Bạch tuộc mini|Mực trứng|Cánh gà|Trứng tràng|Cá chỉ vàng|Chân gà rút xương|Chân gà dài|Chả quế TDM|Giò lụa lá chuối|Gà ủ muối|Vịt quay|Vịt giả cầy|Chân giò giả cầy|Ếch ĐL|Đùi gà ủ muối|Heo quay".split("|")
    raw = "3|9|8|1|7|8|1+8|9+2|7|7|6|5|13 con|8|1 khay 3 con|6|3+1|4|5|2|8+1|8|2|1|4|11|3|8|13|12|2".split("|")
    rows = [CountRow("photo.jpg", i+1, name, "", actual) for i, (name, actual) in enumerate(zip(names, raw))]
    for row, accepted in zip(rows, expected):
      self.assertIn(accepted[0], [p.code for p in candidates_for(row, catalog)], row.name)
    by_name = {row.name: accepted for row, accepted in zip(rows, expected)}

    def match_response(prompt, images, cancel, progress):
      queries = json.loads(prompt.split("Dữ liệu dưới đây là nội dung cần đối chiếu:\n")[1])
      return json.dumps({"matches": [{"id": query["id"], "code": by_name[query["name"]][0],
                                      "confidence": 0.8} for query in queries]})
    nim = MagicMock()
    nim.read.side_effect = match_response
    match_inventory(rows, catalog, nim)
    self.assertEqual([r.qty for r in rows], [r[6] for r in expected])
    # AI uncertainty is advisory and never blocks the requested export.
    self.assertTrue(any(row.issue for row in rows))
    with tempfile.TemporaryDirectory() as tmp:
      target = Path(tmp) / "output.xlsx"
      export_inventory(rows, catalog, bundled_file("MauFileNhapHang.xlsx"), target)
      output = load_workbook(target)
      try:
        actual = list(output.active.values)[1:]
        for line, accepted in zip(actual, expected):
          self.assertEqual(line[:3], accepted[:3])
          self.assertEqual(line[3], catalog[line[0]].cost)
          self.assertEqual(line[4:], accepted[4:])
        self.assertEqual(len(actual), 31)
        self.assertEqual(output.active.tables["PurchaseOrderTemplate"].ref, "A1:M32")
        self.assertEqual(output.active["D5"].value, 885.43)
      finally:
        output.close()


if __name__ == "__main__":
  unittest.main()
