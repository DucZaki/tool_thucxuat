# -*- coding: utf-8 -*-
"""Test provider Ollama: payload, stream, cancel, lỗi, parse, cắt ảnh."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from PIL import Image

from thuc_xuat import config
from thuc_xuat.cancel import CancelledByUser
from thuc_xuat.image_prep import plan_band_count, split_image
from thuc_xuat.ollama_client import OllamaError, chat_vision
from thuc_xuat.vision_extract import (
  _normalize_ollama_value,
  _parse_ollama_order_response,
  _parse_ollama_response,
)


class FakeResponse:
  def __init__(self, lines, status_code=200, text=""):
    self.lines = lines
    self.status_code = status_code
    self.text = text
    self.closed = False

  def __enter__(self):
    return self

  def __exit__(self, *exc):
    self.closed = True
    return False

  def iter_lines(self, decode_unicode=False):
    for line in self.lines:
      yield line


def stream_lines(*pieces: str) -> list[str]:
  lines = [
    json.dumps({"message": {"role": "assistant", "content": p}, "done": False})
    for p in pieces
  ]
  lines.append(json.dumps({"done": True}))
  return lines


class ChatVisionPayloadTests(unittest.TestCase):
  def test_payload_and_stream_join(self):
    captured = {}

    def fake_post(self, url, **kwargs):
      captured["url"] = url
      captured.update(kwargs)
      return FakeResponse(stream_lines('{"thuc', '_xuat": {}}'))

    with patch.object(requests.Session, "post", fake_post):
      text = chat_vision(
        "prompt",
        [b"fake-image"],
        model="gemma3:4b",
        base_url="http://127.0.0.1:11434/",
        timeout=42,
        num_ctx=4096,
      )

    self.assertEqual(text, '{"thuc_xuat": {}}')
    self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
    payload = captured["json"]
    self.assertEqual(payload["model"], "gemma3:4b")
    self.assertTrue(payload["stream"])
    self.assertEqual(payload["format"], "json")
    self.assertEqual(payload["options"]["temperature"], 0)
    self.assertEqual(payload["options"]["num_ctx"], 4096)
    self.assertEqual(len(payload["messages"][0]["images"]), 1)
    self.assertTrue(payload["messages"][0]["images"][0].startswith("ZmFrZS1pbWFnZQ"))
    self.assertEqual(captured["timeout"], (5.0, 42.0))

  def test_cancel_mid_stream(self):
    event = threading.Event()

    def gen():
      yield json.dumps({"message": {"content": "a"}, "done": False})
      event.set()
      yield json.dumps({"message": {"content": "b"}, "done": False})

    def fake_post(self, url, **kwargs):
      return FakeResponse(gen())

    with patch.object(requests.Session, "post", fake_post):
      with self.assertRaises(CancelledByUser):
        chat_vision("p", [b"x"], cancel_event=event)

  def test_model_missing_message(self):
    def fake_post(self, url, **kwargs):
      return FakeResponse([], status_code=404, text='{"error":"model not found"}')

    with patch.object(requests.Session, "post", fake_post):
      with self.assertRaises(OllamaError) as ctx:
        chat_vision("p", [b"x"], model="gemma3:4b")
    self.assertIn("ollama pull gemma3:4b", str(ctx.exception))

  def test_connection_refused_message(self):
    def fake_post(self, url, **kwargs):
      raise requests.exceptions.ConnectionError("refused")

    with patch.object(requests.Session, "post", fake_post):
      with self.assertRaises(OllamaError) as ctx:
        chat_vision("p", [b"x"])
    self.assertIn("ollama serve", str(ctx.exception))


class ParseOllamaTests(unittest.TestCase):
  def test_normalize_x_with_printed_qty(self):
    self.assertEqual(_normalize_ollama_value("X"), "X")
    self.assertEqual(_normalize_ollama_value("5 X"), "X")
    self.assertEqual(_normalize_ollama_value("10x"), "X")
    self.assertEqual(_normalize_ollama_value("10H"), "10H")
    self.assertEqual(_normalize_ollama_value("14 con"), "14 con")
    self.assertEqual(_normalize_ollama_value("1T"), "1T")

  def test_parse_filters_stt_out_of_range_and_bad_keys(self):
    text = (
      '{"thuc_xuat": {"17": "10", "18": "5 X", "39": "9", '
      '"abc": "3", "20": "", "21": "9", "10H": "3"}}'
    )
    out = _parse_ollama_response(text, max_stt=38)
    self.assertEqual(out, {17: "10", 18: "X", 21: "9"})

  def test_parse_order_response_with_pdn(self):
    text = '{"pdn": "pdn014331", "thuc_xuat": {"1": "10", "2": "X", "999": "5"}}'
    pdn, values = _parse_ollama_order_response(text, {"PDN014331": 20})
    self.assertEqual(pdn, "PDN014331")
    self.assertEqual(values, {1: "10", 2: "X"})

  def test_parse_order_response_unknown_pdn(self):
    text = '{"pdn": "PDN999999", "thuc_xuat": {"1": "10"}}'
    pdn, _values = _parse_ollama_order_response(text, {"PDN014331": 20})
    self.assertEqual(pdn, "PDN999999")


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