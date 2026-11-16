"""OCR response parsing and image splitting."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from thuc_xuat.image_prep import plan_band_count, split_image
from thuc_xuat.vision_extract import _normalize_ocr_value, _parse_ocr_response


class ParseOcrTests(unittest.TestCase):
  def test_normalize_x_with_printed_qty(self):
    self.assertEqual(_normalize_ocr_value("X"), "X")
    self.assertEqual(_normalize_ocr_value("5 X"), "X")
    self.assertEqual(_normalize_ocr_value("10x"), "X")
    self.assertEqual(_normalize_ocr_value("10H"), "10H")
    self.assertEqual(_normalize_ocr_value("14 con"), "14 con")
    self.assertEqual(_normalize_ocr_value("1T"), "1T")

  def test_parse_filters_stt_out_of_range_and_bad_keys(self):
    text = (
      '{"thuc_xuat": {"17": "10", "18": "5 X", "39": "9", '
      '"abc": "3", "20": "", "21": "9", "10H": "3"}}'
    )
    out = _parse_ocr_response(text, max_stt=38)
    self.assertEqual(out, {17: "10", 18: "X", 21: "9"})

class ImagePrepTests(unittest.TestCase):
  def test_plan_band_count(self):
    self.assertEqual(plan_band_count((768, 800)), 1)
    self.assertEqual(plan_band_count((768, 1024)), 2)
    self.assertEqual(plan_band_count((768, 2000)), 3)
    self.assertEqual(plan_band_count((768, 5000)), 4)
    self.assertEqual(plan_band_count((768, 1024), bands=3), 3)
    self.assertEqual(plan_band_count((768, 1024), bands=99), 4)

  def test_split_image_makes_jpegs(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "page.png"
      Image.new("RGB", (800, 1600), "white").save(path)
      bands = split_image(path)
      self.assertEqual(len(bands), 2)
      for band in bands:
        self.assertTrue(band.data.startswith(b"\xff\xd8"))
        self.assertEqual(band.width, 1400)
        self.assertGreater(band.height, 500)
        self.assertIn("dải", band.label)


if __name__ == "__main__":
  unittest.main()