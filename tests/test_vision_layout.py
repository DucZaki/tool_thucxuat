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
  _normalize_ocr_value,
  _parse_ocr_response,
)


class NormalizeValueTests(unittest.TestCase):
  def test_y_read_for_x_becomes_x(self):
    # Phiếu không có chữ Y: nét gạch chéo X bị đọc thành Y → phải là "X".
    for value in ("Y", "y", "5 Y", "10y", "X", "x", "5 X"):
      self.assertEqual(_normalize_ocr_value(value), "X", value)

  def test_numbers_untouched(self):
    for value in ("10", "1T", "5T x 24", "10H"):
      self.assertEqual(_normalize_ocr_value(value), value)

  def test_tick_like_marks_from_ocr_become_x(self):
    for value in ("✓", "✔.", "v", "V", ">", "/", "√", "10 ✓", "5✔️"):
      self.assertEqual(_normalize_ocr_value(value), "X")


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
    self.assertEqual(_parse_ocr_response(text, 186), {2: "10"})


class ParseOcrRangeTests(unittest.TestCase):
  def test_clamps_extrapolated_rows(self):
    text = (
      '{"stt_dau": 17, "stt_cuoi": 38, '
      '"thuc_xuat": {"17": "10", "38": "10H", "39": "X", "186": "X"}}'
    )
    self.assertEqual(sorted(_parse_ocr_response(text, 186)), [17, 38])

  def test_no_range_keeps_all(self):
    text = '{"thuc_xuat": {"1": "5", "2": "X"}}'
    self.assertEqual(sorted(_parse_ocr_response(text, 186)), [1, 2])

  def test_bad_range_ignored_when_keys_outside(self):
    text = (
      '{"stt_dau": 1, "stt_cuoi": 1, '
      '"thuc_xuat": {"17": "10", "18": "X", "19": "5"}}'
    )
    self.assertEqual(sorted(_parse_ocr_response(text, 186)), [17, 18, 19])


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


class IgnoreNotesColumnTests(unittest.TestCase):
  def test_masks_notes_and_ordered_quantity_without_touching_shipped(self):
    from thuc_xuat.image_prep import _mask_ocr_columns, warp_available

    if not warp_available():
      self.skipTest("chưa cài opencv/numpy")
    import cv2
    import numpy as np

    image = np.full((400, 1000, 3), 255, dtype=np.uint8)
    for x in (25, 85, 560, 670, 820, 980):
      cv2.line(image, (x, 0), (x, 399), (0, 0, 0), 2)
    image[170:189, 600:630] = 0
    image[170:189, 700:740] = 0
    image[170:189, 865:940] = 0
    masked = _mask_ocr_columns(image)
    self.assertTrue((masked[170:189, 600:630] == 255).all())
    self.assertTrue((masked[170:189, 865:940] == 255).all())
    self.assertTrue((masked[170:189, 700:740] == 0).all())
    self.assertTrue((masked[:, 820] == 0).all())


class PageIdentificationTests(unittest.TestCase):
  def test_ambiguous_or_truncated_pdn_does_not_block_matching_names(self):
    from thuc_xuat.vision_extract import _match_order_from_page

    orders = {"PDN016505": ["sua milo lua mach", "chao ngon viet nam"], "PDN016535": ["gio bo", "kho ca"]}
    rows = [{"stt": 1, "ten": "Sữa MILO lúa mạch"}, {"stt": 2, "ten": "Chao ngon Việt Nam"}]
    for pdn in ("PDN01650", "PDN016535"):
      self.assertEqual(_match_order_from_page({"pdn": pdn, "dong": rows}, orders), "PDN016505")
    self.assertEqual(_match_order_from_page({"pdn": "PDN 016505"}, orders), "PDN016505")

  def test_single_footer_row_matches_the_only_order_with_that_name_at_stt(self):
    from thuc_xuat.vision_extract import _match_order_from_page

    orders = {"PDN016505": [""] * 185 + ["banh phong tom 200g"], "PDN016535": [""] * 72}
    row = {"stt": 186, "ten": "Bánh phồng tôm 200g"}
    self.assertEqual(_match_order_from_page({"pdn": None, "dong": [row]}, orders), "PDN016505")
    self.assertIsNone(_match_order_from_page({"pdn": None, "dong": [{"stt": 186, "ten": "Tổng cộng"}]}, orders))

  def test_short_table_with_one_row_removes_large_blank_region(self):
    from thuc_xuat.image_prep import _short_table, warp_available

    if not warp_available():
      self.skipTest("chưa cài opencv/numpy")
    import cv2
    import numpy as np

    photo = np.full((1300, 1100, 3), 245, dtype=np.uint8)
    cv2.rectangle(photo, (60, 170), (1020, 236), (55, 55, 55), 2)
    for x in (115, 737, 828, 930):
      cv2.line(photo, (x, 170), (x, 236), (55, 55, 55), 2)
    cv2.putText(photo, "186", (65, 214), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (20, 20, 20), 2)
    band = _short_table(photo)
    self.assertIsNotNone(band)
    self.assertEqual(band.mode, "short_table")
    self.assertLess(band.height, 1300 / 8)


if __name__ == "__main__":
  unittest.main()
