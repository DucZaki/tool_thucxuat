# -*- coding: utf-8 -*-
"""Bộ cài: giải nén payload, giữ dữ liệu người dùng, tải Ollama."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import installer
from thuc_xuat import ollama_setup
from thuc_xuat.cancel import CancelledByUser


class FakeStream:
  def __init__(self, chunks, headers=None, status=200):
    self._chunks = chunks
    self.headers = headers or {"Content-Length": str(sum(len(c) for c in chunks))}
    self.status_code = status

  def raise_for_status(self):
    if self.status_code >= 400:
      raise RuntimeError(f"HTTP {self.status_code}")

  def iter_content(self, size):
    yield from self._chunks

  def __enter__(self):
    return self

  def __exit__(self, *exc):
    return False


class ExtractPayloadTests(unittest.TestCase):
  def _zip(self, tmp: Path) -> Path:
    path = tmp / "payload.zip"
    with zipfile.ZipFile(path, "w") as zf:
      zf.writestr("ThucXuatTool.exe", b"exe")
      zf.writestr("_internal/lib.dll", b"dll")
    return path

  def test_extracts_and_keeps_user_data(self):
    with tempfile.TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      target = tmp_path / "app"
      target.mkdir()
      (target / "config.json").write_text('{"x": 1}', encoding="utf-8")
      (target / "KetQua").mkdir()
      (target / "KetQua" / "old.xlsx").write_text("data", encoding="utf-8")

      count = installer.extract_payload(self._zip(tmp_path), target)

      self.assertEqual(count, 2)
      self.assertTrue((target / "ThucXuatTool.exe").exists())
      self.assertTrue((target / "_internal" / "lib.dll").exists())
      self.assertEqual((target / "config.json").read_text(encoding="utf-8"), '{"x": 1}')
      self.assertTrue((target / "KetQua" / "old.xlsx").exists())

  def test_skips_unsafe_names(self):
    with tempfile.TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      zip_path = tmp_path / "payload.zip"
      with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../evil.txt", b"bad")
        zf.writestr("ok.txt", b"good")
      target = tmp_path / "app"
      installer.extract_payload(zip_path, target)
      self.assertFalse((tmp_path / "evil.txt").exists())
      self.assertTrue((target / "ok.txt").exists())

  def test_cancel_stops_extraction(self):
    with tempfile.TemporaryDirectory() as tmp:
      tmp_path = Path(tmp)
      event = threading.Event()
      event.set()
      with self.assertRaises(CancelledByUser):
        installer.extract_payload(self._zip(tmp_path), tmp_path / "app", cancel_event=event)


class PathTests(unittest.TestCase):
  def test_install_dir_env_override(self):
    with patch.dict(os.environ, {"THUCXUAT_INSTALL_DIR": r"C:\Temp\app"}, clear=False):
      self.assertEqual(installer.install_dir(), Path(r"C:\Temp\app"))

  def test_install_dir_default(self):
    env = {k: v for k, v in os.environ.items() if k != "THUCXUAT_INSTALL_DIR"}
    env["LOCALAPPDATA"] = r"C:\Users\demo\AppData\Local"
    with patch.dict(os.environ, env, clear=True):
      self.assertEqual(
        installer.install_dir(),
        Path(r"C:\Users\demo\AppData\Local") / "Programs" / "ThucXuatTool",
      )


class OllamaSetupTests(unittest.TestCase):
  def test_download_reports_progress(self):
    seen: list[tuple[int, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
      dest = Path(tmp) / "OllamaSetup.exe"
      chunks = [b"a" * 10, b"b" * 10]
      with patch.object(
        ollama_setup.requests, "get", return_value=FakeStream(chunks)
      ):
        ollama_setup.download_setup(
          dest, on_progress=lambda done, total: seen.append((done, total))
        )
      self.assertEqual(dest.read_bytes(), b"a" * 10 + b"b" * 10)
    self.assertEqual(seen[-1], (20, 20))

  def test_download_honours_cancel(self):
    event = threading.Event()
    event.set()
    with tempfile.TemporaryDirectory() as tmp:
      with patch.object(
        ollama_setup.requests, "get", return_value=FakeStream([b"x"])
      ):
        with self.assertRaises(CancelledByUser):
          ollama_setup.download_setup(Path(tmp) / "x.exe", cancel_event=event)

  def test_install_returns_exe_found(self):
    with tempfile.TemporaryDirectory() as tmp:
      setup = Path(tmp) / "OllamaSetup.exe"
      setup.write_bytes(b"stub")
      with patch.object(ollama_setup.subprocess, "run") as run, patch.object(
        ollama_setup, "find_ollama_exe", return_value=r"C:\Ollama\ollama.exe"
      ):
        exe = ollama_setup.install(setup)
      self.assertEqual(exe, r"C:\Ollama\ollama.exe")
      run.assert_called_once()
      args = run.call_args[0][0]
      self.assertIn("/VERYSILENT", args)


if __name__ == "__main__":
  unittest.main()
