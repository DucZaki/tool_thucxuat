# -*- coding: utf-8 -*-
"""Sắp ảnh theo tên (natural sort)."""
import unittest
from pathlib import Path

from thuc_xuat.vision_extract import _natural_key, _sort_images


class ImageSortTests(unittest.TestCase):
  def test_natural_sort_pages(self):
    paths = [Path("page10.jpg"), Path("page2.jpg"), Path("page1.jpg")]
    self.assertEqual(
      [p.name for p in _sort_images(paths)],
      ["page1.jpg", "page2.jpg", "page10.jpg"],
    )

  def test_natural_key_numbers(self):
    self.assertLess(_natural_key("img2.jpg"), _natural_key("img10.jpg"))


if __name__ == "__main__":
  unittest.main()
