# -*- coding: utf-8 -*-
"""Sửa file kết quả đã xuất: đọc, sửa, ghi lại."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from thuc_xuat.output_edit import (
  EditRow,
  OutputFormatError,
  list_outputs,
  load_sheet,
  save_sheet,
)
from thuc_xuat.processor import OutputRow, save_output


def _make_output(folder: Path, name: str, include_name: bool) -> Path:
  rows = [
    OutputRow(stt=1, code="SP001", name="Nước mắm", raw="10", qty=10),
    OutputRow(stt=2, code="SP002", name="Bánh gạo", raw="5,5", qty=5.5),
  ]
  out = folder / name
  save_output(rows, out, include_name=include_name)
  return out


class LoadSheetTests(unittest.TestCase):
  def test_reads_two_columns(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = _make_output(Path(tmp), "a.xlsx", include_name=False)
      sheet = load_sheet(path)
      self.assertFalse(sheet.has_name)
      self.assertEqual([r.code for r in sheet.rows], ["SP001", "SP002"])
      self.assertEqual([r.qty for r in sheet.rows], [10, 5.5])

  def test_reads_three_columns(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = _make_output(Path(tmp), "b.xlsx", include_name=True)
      sheet = load_sheet(path)
      self.assertTrue(sheet.has_name)
      self.assertEqual(sheet.rows[0].name, "Nước mắm")

  def test_rejects_other_excel(self):
    from openpyxl import Workbook

    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "khac.xlsx"
      wb = Workbook()
      wb.active.append(["Số phiếu", "Ngày"])
      wb.save(str(path))
      with self.assertRaises(OutputFormatError):
        load_sheet(path)

  def test_missing_file_message(self):
    with tempfile.TemporaryDirectory() as tmp:
      with self.assertRaises(OutputFormatError):
        load_sheet(Path(tmp) / "khong-co.xlsx")


class SaveSheetTests(unittest.TestCase):
  def test_edit_and_save_in_place(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = _make_output(Path(tmp), "c.xlsx", include_name=False)
      sheet = load_sheet(path)
      sheet.rows[0].qty = 12
      sheet.rows.append(EditRow(code="SP003", qty=7))
      save_sheet(sheet)

      again = load_sheet(path)
      self.assertEqual([r.code for r in again.rows], ["SP001", "SP002", "SP003"])
      self.assertEqual([r.qty for r in again.rows], [12, 5.5, 7])

  def test_delete_row(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = _make_output(Path(tmp), "d.xlsx", include_name=True)
      sheet = load_sheet(path)
      del sheet.rows[0]
      save_sheet(sheet)
      again = load_sheet(path)
      self.assertEqual([r.code for r in again.rows], ["SP002"])
      self.assertEqual(again.rows[0].name, "Bánh gạo")

  def test_save_as_new_file_keeps_original(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = _make_output(Path(tmp), "e.xlsx", include_name=False)
      sheet = load_sheet(path)
      sheet.rows[0].qty = 99
      copy = Path(tmp) / "e_sua.xlsx"
      save_sheet(sheet, copy)
      self.assertEqual(load_sheet(path).rows[0].qty, 10)
      self.assertEqual(load_sheet(copy).rows[0].qty, 99)


class ListOutputsTests(unittest.TestCase):
  def test_newest_first_and_skips_lock_files(self):
    with tempfile.TemporaryDirectory() as tmp:
      folder = Path(tmp)
      old = _make_output(folder, "old.xlsx", include_name=False)
      new = _make_output(folder, "new.xlsx", include_name=False)
      (folder / "~$new.xlsx").write_text("lock", encoding="utf-8")
      (folder / "ghi-chu.txt").write_text("x", encoding="utf-8")
      now = time.time()
      old.touch()
      import os

      os.utime(old, (now - 100, now - 100))

      names = [p.name for p in list_outputs(folder)]
      self.assertEqual(names, ["new.xlsx", "old.xlsx"])

  def test_missing_folder_is_empty(self):
    self.assertEqual(list_outputs("Z:/khong/ton/tai"), [])


if __name__ == "__main__":
  unittest.main()
