# -*- coding: utf-8 -*-
"""Tự cập nhật: so phiên bản, đọc GitHub Release, tải và giải nén bản mới."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from thuc_xuat import updater
from thuc_xuat.updater import (
  ASSET_NAME,
  UpdateInfo,
  check_for_update,
  is_newer,
  parse_version,
  stage_zip,
  verify_digest,
)


class FakeResponse:
  def __init__(self, payload=None, chunks=None, headers=None, status=200):
    self._payload = payload
    self._chunks = chunks or []
    self.headers = headers or {}
    self.status_code = status

  def raise_for_status(self):
    if self.status_code >= 400:
      raise RuntimeError(f"HTTP {self.status_code}")

  def json(self):
    return self._payload

  def iter_content(self, size):
    yield from self._chunks

  def __enter__(self):
    return self

  def __exit__(self, *exc):
    return False


class VersionTests(unittest.TestCase):
  def test_parse_version(self):
    self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))
    self.assertEqual(parse_version("1.2"), (1, 2))
    self.assertEqual(parse_version(""), (0,))

  def test_newer_compares_numerically(self):
    self.assertTrue(is_newer("1.1.10", "1.1.9"))
    self.assertTrue(is_newer("1.2", "1.1.9"))
    self.assertFalse(is_newer("1.1.0", "1.1"))
    self.assertFalse(is_newer("1.0.9", "1.1.0"))

  def test_same_version_is_not_newer(self):
    self.assertFalse(is_newer(updater.__version__, updater.__version__))


class CheckUpdateTests(unittest.TestCase):
  def _release(self, tag, name=ASSET_NAME, digest="sha256:abc", body="- sua loi"):
    return {
      "tag_name": tag,
      "body": body,
      "assets": [
        {
          "name": name,
          "browser_download_url": "https://example.com/tool.zip",
          "size": 1234,
          "digest": digest,
        }
      ],
    }

  def test_returns_none_when_same_version(self):
    with patch.object(
      updater.requests, "get", return_value=FakeResponse(payload=self._release(f"v{updater.__version__}"))
    ):
      self.assertIsNone(check_for_update())

  def test_returns_none_when_no_release_yet(self):
    with patch.object(updater.requests, "get", return_value=FakeResponse(status=404)):
      self.assertIsNone(check_for_update())

  def test_returns_update_info(self):
    data = self._release("v9.9.9")
    with patch.object(updater.requests, "get", return_value=FakeResponse(payload=data)):
      info = check_for_update()
    self.assertIsNotNone(info)
    assert info is not None
    self.assertEqual(info.version, "9.9.9")
    self.assertEqual(info.download_url, "https://example.com/tool.zip")
    self.assertEqual(info.size, 1234)
    self.assertEqual(info.digest, "sha256:abc")
    self.assertEqual(info.notes, "- sua loi")

  def test_ignores_release_without_zip_asset(self):
    data = self._release("v9.9.9", name="other.zip")
    with patch.object(updater.requests, "get", return_value=FakeResponse(payload=data)):
      self.assertIsNone(check_for_update())


class DownloadTests(unittest.TestCase):
  def test_download_verifies_digest_and_reports_progress(self):
    payload = b"hello world"
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    info = UpdateInfo("9.9.9", "", "https://example.com/x.zip", len(payload), digest)
    resp = FakeResponse(
      chunks=[payload[:5], payload[5:]],
      headers={"Content-Length": str(len(payload))},
    )
    seen: list[tuple[int, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
      dest = Path(tmp) / "update.zip"
      with patch.object(updater.requests, "get", return_value=resp):
        out = updater.download_update(
          info, dest, on_progress=lambda d, t: seen.append((d, t))
        )
      self.assertEqual(out.read_bytes(), payload)
    self.assertEqual(seen[-1], (len(payload), len(payload)))

  def test_download_rejects_wrong_digest(self):
    info = UpdateInfo("9.9.9", "", "https://example.com/x.zip", 3, "sha256:deadbeef")
    resp = FakeResponse(chunks=[b"abc"], headers={"Content-Length": "3"})
    with tempfile.TemporaryDirectory() as tmp:
      with patch.object(updater.requests, "get", return_value=resp):
        with self.assertRaises(ValueError):
          updater.download_update(info, Path(tmp) / "x.zip")

  def test_verify_digest_skips_when_missing(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "f.bin"
      path.write_bytes(b"data")
      verify_digest(path, "")  # không lỗi


class StageTests(unittest.TestCase):
  def test_stage_zip_finds_exe_inside_folder(self):
    with tempfile.TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      zip_path = tmp_path / "tool.zip"
      with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ThucXuatTool/ThucXuatTool.exe", b"exe")
        zf.writestr("ThucXuatTool/_internal/lib.dll", b"dll")
      with patch.object(updater, "work_dir", return_value=tmp_path / "work"):
        staged = stage_zip(zip_path, "9.9.9")
      self.assertEqual(staged.name, "ThucXuatTool")
      self.assertTrue((staged / "ThucXuatTool.exe").exists())
      self.assertTrue((staged / "_internal" / "lib.dll").exists())


if __name__ == "__main__":
  unittest.main()
