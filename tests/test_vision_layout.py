# -*- coding: utf-8 -*-
"""Test các bước bố cục trang: dò cột in, gán trang, khoảng STT."""
import unittest
from pathlib import Path

from thuc_xuat.vision_extract import (
  _best_names_match,
  _maps_contiguous,
)


class MapsContiguousTests(unittest.TestCase):
  def test_it_keys_always_kept(self):
    self.assertTrue(_maps_contiguous([{1: "10", 2: "X"}]))

  def test_contiguous_page_kept(self):
    page = {stt: "5" for stt in range(19, 43)}
    self.assertTrue(_maps_contiguous([page]))

  def test_gap_of_two_still_contiguous(self):
    page = {1: "5", 3: "X", 4: "5", 5: "X"}
    self.assertTrue(_maps_contiguous([page]))

  def test_scattered_footer_dropped(self):
    # Trang cuối chỉ có 1 dòng + phần tổng kết → model trả STT rời rạc.
    footer = [{1: "10", 2: "X"}, {23: "Komaki"}, {17: "10", 18: "X"}]
    self.assertFalse(_maps_contiguous(footer))

  def test_long_run_wins_over_noise(self):
    page = {stt: "5" for stt in range(100, 124)}
    page.update({1: "X", 50: "X"})
    self.assertTrue(_maps_contiguous([page]))


class BestNamesMatchTests(unittest.TestCase):
  PRODUCTS = ["a", "b", "c", "d", "e"]

  def test_position_of_block(self):
    score, pos = _best_names_match(["c", "d"], self.PRODUCTS)
    self.assertAlmostEqual(score, 1.0)
    self.assertEqual(pos, 3)

  def test_ignores_diacritics_and_case(self):
    score, pos = _best_names_match(
      ["Sữa MILO lúa mạch"], ["sua milo lua mach", "bánh"]
    )
    self.assertGreater(score, 0.9)
    self.assertEqual(pos, 1)

  def test_no_match(self):
    score, _ = _best_names_match(["zzz"], ["a", "b"])
    self.assertLess(score, 0.5)

  def test_too_few_products(self):
    self.assertEqual(_best_names_match(["a", "b"], ["a"]), (0.0, 0))


class SplitBandTests(unittest.TestCase):
  def setUp(self):
    try:
      import cv2  # noqa: F401
      import numpy  # noqa: F401
    except ImportError:
      self.skipTest("chưa cài opencv/numpy")

  def test_two_overlapping_bands(self):
    import types

    import cv2
    import numpy as np

    from thuc_xuat.vision_extract import _split_band

    arr = np.full((400, 200, 3), 200, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", arr)
    self.assertTrue(ok)
    parts = _split_band(types.SimpleNamespace(data=buf.tobytes()))
    self.assertEqual(len(parts), 2)
    for part in parts:
      img = cv2.imdecode(np.frombuffer(part, np.uint8), cv2.IMREAD_COLOR)
      self.assertIsNotNone(img)


class MaskBetweenLinesTests(unittest.TestCase):
  def setUp(self):
    try:
      import numpy  # noqa: F401
    except ImportError:
      self.skipTest("chưa cài numpy")
    try:
      import cv2  # noqa: F401
    except ImportError:
      self.skipTest("chưa cài opencv")

  def test_masks_printed_column_leftmost_narrow(self):
    import numpy as np

    from thuc_xuat.image_prep import _mask_between_lines

    img = np.full((100, 1000, 3), 128, dtype=np.uint8)
    lines = [50, 60, 300, 720, 800, 900, 990]
    out, ok = _mask_between_lines(img, lines)
    self.assertTrue(ok)
    self.assertEqual(tuple(out[50, 760]), (255, 255, 255))
    self.assertEqual(tuple(out[50, 300]), (128, 128, 128))
    self.assertEqual(tuple(out[50, 950]), (128, 128, 128))

  def test_skips_wide_columns(self):
    import numpy as np

    from thuc_xuat.image_prep import _mask_between_lines

    img = np.full((100, 1000, 3), 128, dtype=np.uint8)
    # Không có cột hẹp nào ở nửa phải → không che.
    out, ok = _mask_between_lines(img, [50, 300, 700, 990])
    self.assertFalse(ok)
    self.assertTrue((out == 128).all())


class PreparePageTests(unittest.TestCase):
  """Chạy prepare_page trên ảnh thật (bắt lỗi thiếu import cv2/numpy)."""

  def setUp(self):
    try:
      import numpy  # noqa: F401
    except ImportError:
      self.skipTest("chưa cài numpy")
    try:
      import cv2  # noqa: F401
    except ImportError:
      self.skipTest("chưa cài opencv")

  def test_returns_one_band_for_clean_page(self):
    import tempfile

    from PIL import Image, ImageDraw

    from thuc_xuat.image_prep import prepare_page

    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "page.png"
      img = Image.new("RGB", (1536, 2048), "white")
      draw = ImageDraw.Draw(img)
      draw.rectangle([70, 70, 1466, 1978], outline="black", width=6)
      for x in (250, 520, 1080, 1250):
        draw.line([(x, 200), (x, 1900)], fill="black", width=2)
      img.save(path)
      bands = prepare_page(path)

    self.assertEqual(len(bands), 1)
    self.assertTrue(bands[0].data.startswith(b"\xff\xd8"))
    self.assertIn(bands[0].mode, {"page", "table", "split"})

  def test_falls_back_to_split_when_no_page(self):
    import tempfile

    from PIL import Image

    from thuc_xuat.image_prep import prepare_page

    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "blank.png"
      Image.new("RGB", (800, 1600), "white").save(path)
      bands = prepare_page(path)

    self.assertGreaterEqual(len(bands), 1)
    self.assertTrue(all(b.data.startswith(b"\xff\xd8") for b in bands))


if __name__ == "__main__":
  unittest.main()
