# -*- coding: utf-8 -*-
"""Test các bước bố cục trang: dò cột in, gán trang, khoảng STT."""
import unittest
from pathlib import Path

from thuc_xuat.vision_extract import (
  _accept_image_map,
  _best_names_match,
  _commit_image_map,
  _looks_like_qty,
  _maps_contiguous,
  _parse_ollama_response,
)


class LooksLikeQtyTests(unittest.TestCase):
  def test_quantities_accepted(self):
    for value in (
      "10", "110", "1T", "10H", "5T x 24", "3T×48", "14 con", "5,5",
      "24 gói", "30cm", "X", "✓", "1↓", "5 thùng", "2 kg",
    ):
      self.assertTrue(_looks_like_qty(value), value)

  def test_text_rejected(self):
    for value in ("Vận chuyển", "IWATSUKA(Nagoya)", "giao sau", "Số lượng", ""):
      self.assertFalse(_looks_like_qty(value), value)

  def test_parse_drops_text_values(self):
    text = (
      '{"stt_dau": 1, "stt_cuoi": 3, '
      '"thuc_xuat": {"1": "Vận chuyển", "2": "10", "3": "IWATSUKA(Nagoya)"}}'
    )
    self.assertEqual(_parse_ollama_response(text, 186), {2: "10"})


class ParseOllamaRangeTests(unittest.TestCase):
  def test_clamps_extrapolated_rows(self):
    text = (
      '{"stt_dau": 17, "stt_cuoi": 38, '
      '"thuc_xuat": {"17": "10", "38": "10H", "39": "X", "186": "X"}}'
    )
    self.assertEqual(sorted(_parse_ollama_response(text, 186)), [17, 38])

  def test_no_range_keeps_all(self):
    text = '{"thuc_xuat": {"1": "5", "2": "X"}}'
    self.assertEqual(sorted(_parse_ollama_response(text, 186)), [1, 2])

  def test_bad_range_ignored_when_keys_outside(self):
    text = (
      '{"stt_dau": 1, "stt_cuoi": 1, '
      '"thuc_xuat": {"17": "10", "18": "X", "19": "5"}}'
    )
    self.assertEqual(sorted(_parse_ollama_response(text, 186)), [17, 18, 19])


class AcceptImageMapTests(unittest.TestCase):
  def test_first_image_needs_contiguous_run(self):
    self.assertTrue(_accept_image_map({1: "5", 2: "X", 3: "5"}, {}))

  def test_first_scattered_footer_rejected(self):
    self.assertFalse(_accept_image_map({1: "10", 17: "10", 23: "X"}, {}))

  def test_later_image_overlapping_is_accepted(self):
    base = {stt: "5" for stt in range(1, 11)}
    self.assertTrue(_accept_image_map({10: "9", 11: "9"}, base))

  def test_later_scattered_and_disjoint_rejected(self):
    base = {1: "5", 2: "5"}
    self.assertFalse(_accept_image_map({17: "5", 40: "5", 55: "5"}, base))


class CommitImageMapTests(unittest.TestCase):
  def test_first_image_wins_on_conflict(self):
    merged: dict[int, str] = {}
    scores: dict[int, int] = {}
    _commit_image_map(merged, scores, {19: "10", 20: "4", 21: "X"})
    # Ảnh sau đọc lệch nhưng không phải dòng sát mép → giữ ảnh đầu.
    added, fixed = _commit_image_map(merged, scores, {19: "99", 20: "44", 21: "X"})
    self.assertEqual((added, fixed), (0, 0))
    self.assertEqual(merged[19], "10")
    self.assertEqual(merged[20], "4")

  def test_later_image_adds_new_rows_only(self):
    merged: dict[int, str] = {}
    scores: dict[int, int] = {}
    _commit_image_map(merged, scores, {1: "10", 2: "X", 3: "4"})
    added, fixed = _commit_image_map(
      merged, scores, {2: "X", 3: "4", 4: "8", 5: "2", 6: "9"}
    )
    self.assertEqual(fixed, 0)
    self.assertEqual(added, 3)
    self.assertEqual(merged[1], "10")
    self.assertEqual(merged[4], "8")

  def test_edge_row_can_be_fixed_by_centered_read(self):
    merged: dict[int, str] = {}
    scores: dict[int, int] = {}
    _commit_image_map(merged, scores, {10: "Sai", 11: "5", 12: "5"})
    self.assertEqual(scores[10], 0)
    # Ảnh sau thấy STT 10 ở giữa ảnh (điểm >= 3) → được sửa.
    added, fixed = _commit_image_map(
      merged, scores, {7: "1", 8: "1", 9: "1", 10: "10", 11: "1", 12: "1", 13: "1"}
    )
    self.assertEqual(fixed, 1)
    self.assertEqual(merged[10], "10")
    self.assertEqual(merged[11], "5")

  def test_number_repairs_bogus_x(self):
    merged: dict[int, str] = {}
    scores: dict[int, int] = {}
    _commit_image_map(merged, scores, {17: "X", 18: "X", 19: "X"})
    added, fixed = _commit_image_map(
      merged, scores, {10: "1", 11: "1", 12: "1", 17: "10", 18: "5", 19: "1"}
    )
    self.assertEqual(fixed, 2)
    self.assertEqual(merged[17], "10")
    self.assertEqual(merged[18], "5")
    self.assertEqual(merged[19], "X")

  def test_number_never_replaced_by_x(self):
    merged: dict[int, str] = {}
    scores: dict[int, int] = {}
    _commit_image_map(merged, scores, {17: "10", 18: "5", 19: "7"})
    _commit_image_map(merged, scores, {15: "1", 16: "1", 17: "X", 18: "X", 19: "1"})
    self.assertEqual(merged[17], "10")
    self.assertEqual(merged[18], "5")


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


class PrintedColumnBoundsTests(unittest.TestCase):
  def setUp(self):
    try:
      import numpy  # noqa: F401
    except ImportError:
      self.skipTest("chưa cài numpy")

  def _blank(self, width: int = 1000):
    import numpy as np

    return np.full((400, width, 3), 255, dtype=np.uint8)

  def test_finds_printed_column_of_digits(self):
    from thuc_xuat.image_prep import _printed_column_bounds

    img = self._blank()
    # cột in: 20 chữ số nhỏ, đều mọi dòng
    for i in range(20):
      y = 20 + i * 18
      img[y : y + 12, 700:715] = 0
    # cột viết tay: ít ô hơn, nét lớn hơn, nằm bên phải
    for i in range(10):
      y = 20 + i * 36
      img[y : y + 20, 850:890] = 0
    bounds = _printed_column_bounds(img)
    self.assertIsNotNone(bounds)
    left, right = bounds
    self.assertLessEqual(left, 700)
    self.assertGreaterEqual(right, 715)
    self.assertLess(right, 800)

  def test_blank_image_returns_none(self):
    from thuc_xuat.image_prep import _printed_column_bounds

    self.assertIsNone(_printed_column_bounds(self._blank()))


class FarGapTests(unittest.TestCase):
  def test_contiguous_with_small_gaps_ok(self):
    from thuc_xuat.vision_extract import _has_far_gap

    self.assertFalse(_has_far_gap({17: "10", 18: "X", 21: "5", 22: "5"}))

  def test_two_far_blocks_flagged(self):
    from thuc_xuat.vision_extract import _has_far_gap

    mp = {i: "1" for i in range(1, 17)}
    mp[84] = "10"
    self.assertTrue(_has_far_gap(mp))


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
