# -*- coding: utf-8 -*-
"""Test tạo lối tắt Desktop / Start Menu (Windows)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from thuc_xuat import shortcut


class ShortcutDirTests(unittest.TestCase):
  def test_override_desktop_va_start_menu(self):
    with tempfile.TemporaryDirectory() as tmp:
      env = {
        "THUCXUAT_DESKTOP_DIR": str(Path(tmp) / "desk"),
        "THUCXUAT_STARTMENU_DIR": str(Path(tmp) / "menu"),
      }
      with mock.patch.dict(os.environ, env):
        self.assertEqual(shortcut.desktop_dir(), Path(tmp) / "desk")
        self.assertEqual(shortcut.start_menu_dir(), Path(tmp) / "menu")

  def test_nhan_dien_ban_cai(self):
    with tempfile.TemporaryDirectory() as tmp:
      exe = Path(tmp) / "Programs" / "ThucXuatTool" / "ThucXuatTool.exe"
      exe.parent.mkdir(parents=True)
      exe.write_bytes(b"MZ")
      other = Path(tmp) / "Khac" / "ThucXuatTool.exe"
      other.parent.mkdir()
      other.write_bytes(b"MZ")
      with mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp}):
        self.assertTrue(shortcut.is_installed_copy(exe))
        self.assertFalse(shortcut.is_installed_copy(other))


@unittest.skipUnless(os.name == "nt", "chỉ chạy trên Windows")
class CreateShortcutTests(unittest.TestCase):
  def test_tao_lnk_that(self):
    with tempfile.TemporaryDirectory() as tmp:
      target = Path(tmp) / "ThucXuatTool.exe"
      target.write_bytes(b"MZ")
      link = Path(tmp) / "Thực Xuất.lnk"
      result = shortcut.create_shortcut(link, target)
      self.assertEqual(result, link)
      self.assertTrue(link.is_file())
      with link.open("rb") as fh:
        self.assertEqual(fh.read(4), b"\x4c\x00\x00\x00")
      self.assertGreater(link.stat().st_size, 100)

  def test_ensure_tao_du_hai_noi(self):
    with tempfile.TemporaryDirectory() as tmp:
      target = Path(tmp) / "ThucXuatTool.exe"
      target.write_bytes(b"MZ")
      desk = Path(tmp) / "desk"
      menu = Path(tmp) / "menu"
      desk.mkdir()
      menu.mkdir()
      env = {
        "THUCXUAT_DESKTOP_DIR": str(desk),
        "THUCXUAT_STARTMENU_DIR": str(menu),
      }
      with mock.patch.dict(os.environ, env):
        made = shortcut.ensure_shortcuts(target, "Thực Xuất")
        self.assertEqual(
          sorted(p.name for p in made), ["Thực Xuất.lnk", "Thực Xuất.lnk"]
        )
        self.assertTrue((desk / "Thực Xuất.lnk").is_file())
        self.assertTrue((menu / "Thực Xuất.lnk").is_file())

  def test_thieu_exe_thi_bao_loi(self):
    with tempfile.TemporaryDirectory() as tmp:
      with self.assertRaises(FileNotFoundError):
        shortcut.create_shortcut(Path(tmp) / "x.lnk", Path(tmp) / "khong-co.exe")


if __name__ == "__main__":
  unittest.main()
