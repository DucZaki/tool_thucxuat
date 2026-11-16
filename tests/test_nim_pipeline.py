"""Hồi quy NVIDIA NIM: đọc ảnh, dự phòng và xuất Excel."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from openpyxl import Workbook, load_workbook

from thuc_xuat.image_prep import ImageBand
from thuc_xuat.nim_client import CHAT_URL, NANO_OMNI, SUPER_VL, NimError, NimVision
from thuc_xuat.pipeline import process_batch_like_chat
from thuc_xuat.vision_extract import VisionResume


class NimPipelineTests(unittest.TestCase):
  def setUp(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    self.folder = Path(temp.name)
    self.excel = self.folder / "PDN123.xlsx"
    self.photo = self.folder / "page.jpg"
    self.output = self.folder / "out.xlsx"
    self.photo.touch()
    wb = Workbook()
    wb.active.append(["Mã hàng", "Tên hàng", "ĐVT", "Số lượng"])
    wb.active.append(["SKU01", "Tên một", "thùng", 5])
    wb.active.append(["SKU02", "Tên hai", "gói", 12])
    wb.active.append(["SKU03", "Tên ba", "gói", 7])
    wb.save(self.excel)
    wb.close()
    self.band = ImageBand(b"\xff\xd8", 1, 1, 1400, 1000, "table")

  def run_batch(self, response):
    model = SUPER_VL
    with (
      patch("thuc_xuat.pipeline.ensure_ready") as local,
      patch("thuc_xuat.vision_extract.prepare_page", return_value=[self.band]),
      patch("thuc_xuat.nim_client.requests.post") as post,
    ):
      post.return_value.__enter__.return_value = response
      try:
        return process_batch_like_chat(
          [self.excel], [self.photo], self.output,
          nim=NimVision(model, "nvapi-test"),
        )
      finally:
        local.assert_not_called()
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], CHAT_URL)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], model)
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        image = payload["messages"][0]["content"][1]
        self.assertTrue(image["image_url"]["url"].startswith("data:image/jpeg;base64,"))

  def test_short_page_one_request_and_exact_output(self):
    content = json.dumps({
      "stt_dau": 1, "stt_cuoi": 3,
      "thuc_xuat": {"1": "5T x 24", "2": "✓", "3": "X"},
    })
    packet = {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]}
    response = MagicMock(status_code=200)
    response.iter_lines.return_value = iter([
      b": keep-alive", b"data: " + json.dumps(packet).encode(), b"data: [DONE]",
    ])
    batch = self.run_batch(response)
    self.assertEqual(batch.ok_count, 1)
    self.assertEqual(batch.results[0].thuc_xuat_map[2], "X")
    wb = load_workbook(self.output, read_only=True)
    try:
      self.assertEqual(list(wb.active.values), [
        ("Mã sản phẩm", "Thực xuất"), ("SKU01", 120),
      ])
    finally:
      wb.close()

  def test_quota_error_aborts_without_export_or_ocr_retry(self):
    response = MagicMock(status_code=429, headers={"Retry-After": "30"})
    with self.assertRaisesRegex(NimError, "429.*30"):
      self.run_batch(response)
    self.assertFalse(self.output.exists())

  def test_auto_switches_provider_and_reuses_backup(self):
    unavailable = MagicMock(status_code=503, headers={})
    ok = MagicMock(status_code=200, headers={"X-RateLimit-Remaining-Requests": "17"})
    ok.iter_lines.return_value = [
      b'data: {"choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]}',
      b"data: [DONE]",
    ]
    usage = []
    client = NimVision(api_key="nvapi-test", on_usage=usage.append)
    with patch("thuc_xuat.nim_client.requests.post") as post:
      post.return_value.__enter__.side_effect = [unavailable, ok, ok]
      self.assertEqual(client.read("ocr", [self.band.data]), "{}")
      self.assertEqual(client.read("ocr", [self.band.data]), "{}")
      self.assertEqual(
        [call.kwargs["json"]["model"] for call in post.call_args_list],
        [SUPER_VL, NANO_OMNI, NANO_OMNI],
      )
    self.assertEqual(
      [(event.request_count, event.http_status, event.remaining) for event in usage],
      [(1, None, None), (1, 503, None), (2, None, None), (2, 200, 17), (3, None, None), (3, 200, 17)],
    )

  def test_auto_does_not_switch_on_account_limit(self):
    limited = MagicMock(status_code=429, headers={})
    client = NimVision(api_key="nvapi-test")
    with patch("thuc_xuat.nim_client.requests.post") as post:
      post.return_value.__enter__.return_value = limited
      with self.assertRaisesRegex(NimError, "429"):
        client.read("ocr", [self.band.data])
      post.assert_called_once()

  def test_two_explicit_image_groups_export_separately_without_assignment_calls(self):
    second = self.folder / "PDN124.xlsx"
    second_photo = self.folder / "another.jpg"
    second_photo.touch()
    wb = Workbook()
    wb.active.append(["Mã hàng", "Tên hàng", "ĐVT", "Số lượng"])
    wb.active.append(["OTHER01", "Hàng khác", "cái", 9])
    wb.save(second)
    wb.close()

    def response(value):
      content = json.dumps({
        "stt_dau": 1, "stt_cuoi": 1, "thuc_xuat": {"1": value},
      })
      packet = {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]}
      result = MagicMock(status_code=200)
      result.iter_lines.return_value = [b"data: " + json.dumps(packet).encode()]
      return result

    client = NimVision(SUPER_VL, "nvapi-test")
    with (
      patch("thuc_xuat.vision_extract._assign_pages_to_orders") as guess,
      patch("thuc_xuat.vision_extract.prepare_page", return_value=[self.band]) as prepare,
      patch("thuc_xuat.nim_client.requests.post") as post,
    ):
      post.return_value.__enter__.side_effect = [response("5T x 24"), response("9")]
      batch = process_batch_like_chat(
        [self.excel, second], [second_photo, self.photo],
        output_dir=self.folder, nim=client,
        images_by_excel={self.excel: [self.photo], second: [second_photo]},
      )
      guess.assert_not_called()
      self.assertEqual([call.args[0] for call in prepare.call_args_list], [self.photo, second_photo])
    self.assertEqual(batch.fail_count, 0)
    self.assertEqual(batch.ok_count, 2)
    self.assertEqual(client.request_count, 2)
    for result, expected in zip(batch.results, [("SKU01", 120), ("OTHER01", 9)]):
      wb = load_workbook(result.output_path, read_only=True)
      try:
        self.assertEqual(list(wb.active.values), [("Mã sản phẩm", "Thực xuất"), expected])
      finally:
        wb.close()

  def test_duplicate_image_assignment_is_rejected_before_requests(self):
    second = self.folder / "PDN124.xlsx"
    second_photo = self.folder / "another.jpg"
    second_photo.touch()
    with patch("thuc_xuat.nim_client.requests.post") as post:
      with self.assertRaisesRegex(ValueError, "đúng một lần"):
        process_batch_like_chat(
          [self.excel, second], [self.photo, second_photo],
          nim=NimVision(SUPER_VL, "nvapi-test"),
          images_by_excel={self.excel: [self.photo], second: [self.photo]},
        )
      post.assert_not_called()

  def test_auto_groups_two_photos_with_identical_first_names_and_interleaved_inputs(self):
    second = self.folder / "PDN124.xlsx"
    second_photo = self.folder / "another.jpg"
    second_photo.touch()
    wb = Workbook()
    wb.active.append(["Mã hàng", "Tên hàng", "ĐVT", "Số lượng"])
    wb.active.append(["OTHER01", "Tên một", "cái", 9])
    wb.active.append(["OTHER02", "Bánh gạo", "gói", 11])
    wb.save(second)
    wb.close()
    client = NimVision(SUPER_VL, "nvapi-test")

    def reply(data):
      response = MagicMock(status_code=200)
      packet = {"choices": [{"delta": {"content": json.dumps(data)}, "finish_reason": "stop"}]}
      response.iter_lines.return_value = [b"data: " + json.dumps(packet).encode()]
      return response

    ambiguous = reply({"dong": [{"stt": 1, "ten": "Tên một"}]})
    for_second = reply({"dong": [{"stt": 1, "ten": "Tên một"}, {"stt": 2, "ten": "Bánh gạo"}]})
    for_first = reply({"dong": [{"stt": 1, "ten": "Tên một"}, {"stt": 2, "ten": "Tên hai"}]})
    with (
      patch("thuc_xuat.vision_extract.split_image", return_value=[self.band]),
      patch("thuc_xuat.vision_extract.prepare_assignment_focus", return_value=self.band),
      patch("thuc_xuat.vision_extract.prepare_page", return_value=[self.band]),
      patch("thuc_xuat.nim_client.requests.post") as post,
    ):
      post.return_value.__enter__.side_effect = [
        ambiguous, for_second, for_first,
        reply({"stt_dau": 1, "stt_cuoi": 1, "thuc_xuat": {"1": "5T x 24"}, "ghi_chu": {"2": "999"}}),
        reply({"stt_dau": 1, "stt_cuoi": 1, "thuc_xuat": {"1": "9"}}),
      ]
      batch = process_batch_like_chat(
        [self.excel, second], [self.photo, second_photo], output_dir=self.folder, nim=client,
      )
    self.assertEqual(batch.ok_count, 2)
    self.assertEqual(client.request_count, 5)
    first_result, second_result = batch.results
    self.assertEqual([(row.code, row.qty) for row in first_result.included], [("SKU01", 120)])
    self.assertEqual([(row.code, row.qty) for row in second_result.included], [("OTHER01", 9)])

  def test_retry_only_failed_photo_and_preserve_manual_edits(self):
    failed = self.folder / "z-page2.jpg"
    failed.touch()
    resume = VisionResume()
    client = NimVision(SUPER_VL, "nvapi-test")

    def response(stt, value):
      content = json.dumps({"stt_dau": stt, "stt_cuoi": stt, "thuc_xuat": {str(stt): value}})
      packet = {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]}
      reply = MagicMock(status_code=200)
      reply.iter_lines.return_value = [b"data: " + json.dumps(packet).encode()]
      return reply

    with (
      patch("thuc_xuat.vision_extract.prepare_page") as prepare,
      patch("thuc_xuat.nim_client.requests.post") as post,
    ):
      prepare.side_effect = [[self.band], OSError("ảnh tạm thời lỗi"), [self.band]]
      post.return_value.__enter__.side_effect = [response(1, "7"), response(2, "8")]
      batch = process_batch_like_chat(
        [self.excel], [self.photo, failed], self.output, nim=client, resume=resume,
      )
      self.assertEqual(batch.pending_images, [failed])
      self.assertEqual(batch.ok_count, 1)
      batch = process_batch_like_chat(
        [self.excel], [self.photo, failed], self.output, nim=client, resume=resume,
        manual_edits={self.excel: {1: "=3*8"}},
      )
      self.assertEqual([call.args[0] for call in prepare.call_args_list], [self.photo, failed, failed])
    self.assertEqual(client.request_count, 2)
    self.assertEqual(batch.pending_images, [])
    self.assertEqual(batch.results[0].thuc_xuat_map, {1: "=3*8", 2: "8"})
    wb = load_workbook(self.output, read_only=True)
    try:
      self.assertEqual(list(wb.active.values), [("Mã sản phẩm", "Thực xuất"), ("SKU01", 24), ("SKU02", 8)])
    finally:
      wb.close()
