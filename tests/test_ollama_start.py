# -*- coding: utf-8 -*-
"""Test tự bật Ollama + kiểm tra model trước khi đọc ảnh."""
import unittest
from unittest.mock import patch

from thuc_xuat import ollama_client as ollama


class HasModelTests(unittest.TestCase):
  def test_exact_tag(self):
    with patch.object(ollama, "list_models", return_value=["gemma3:4b", "gemma3:12b"]):
      self.assertTrue(ollama.has_model("gemma3:4b"))
      self.assertTrue(ollama.has_model("gemma3:12b"))
      self.assertFalse(ollama.has_model("gemma3:27b"))
      self.assertFalse(ollama.has_model(""))

  def test_untagged_means_latest(self):
    with patch.object(ollama, "list_models", return_value=["llava:latest"]):
      self.assertTrue(ollama.has_model("llava"))
      self.assertFalse(ollama.has_model("llava:13b"))


class EnsureServerTests(unittest.TestCase):
  def test_already_running_does_not_spawn(self):
    with patch.object(ollama, "list_models", return_value=["gemma3:4b"]), patch.object(
      ollama, "_spawn_server"
    ) as spawn:
      self.assertEqual(ollama.ensure_server(), "running")
    spawn.assert_not_called()

  def test_starts_server_and_waits(self):
    calls = {"n": 0}

    def fake_list(*_a, **_k):
      calls["n"] += 1
      if calls["n"] == 1:
        raise ollama.OllamaError("offline")
      return ["gemma3:4b"]

    with patch.object(ollama, "list_models", side_effect=fake_list), patch.object(
      ollama, "find_ollama_exe", return_value=r"C:\Ollama\ollama.exe"
    ), patch.object(ollama, "_spawn_server") as spawn, patch.object(ollama.time, "sleep"):
      self.assertEqual(ollama.ensure_server(), "started")
    spawn.assert_called_once_with(r"C:\Ollama\ollama.exe")

  def test_missing_exe_gives_install_hint(self):
    with patch.object(ollama, "list_models", side_effect=ollama.OllamaError("offline")), patch.object(
      ollama, "find_ollama_exe", return_value=None
    ):
      with self.assertRaises(ollama.OllamaError) as ctx:
        ollama.ensure_server()
    self.assertIn("ollama.com/download", str(ctx.exception))

  def test_timeout_message(self):
    with patch.object(ollama, "list_models", side_effect=ollama.OllamaError("offline")), patch.object(
      ollama, "find_ollama_exe", return_value="ollama.exe"
    ), patch.object(ollama, "_spawn_server"), patch.object(ollama.time, "sleep"):
      with self.assertRaises(ollama.OllamaError) as ctx:
        ollama.ensure_server(wait_seconds=0)
    self.assertIn("không kết nối được", str(ctx.exception))

  def test_reports_status(self):
    seen: list[str] = []
    calls = {"n": 0}

    def fake_list(*_a, **_k):
      calls["n"] += 1
      if calls["n"] == 1:
        raise ollama.OllamaError("offline")
      return ["gemma3:4b"]

    with patch.object(ollama, "list_models", side_effect=fake_list), patch.object(
      ollama, "find_ollama_exe", return_value="ollama.exe"
    ), patch.object(ollama, "_spawn_server"), patch.object(ollama.time, "sleep"):
      ollama.ensure_server(on_status=seen.append)
    self.assertIn("Đang khởi động Ollama...", seen)


class EnsureReadyTests(unittest.TestCase):
  def setUp(self):
    import tempfile
    from pathlib import Path

    from thuc_xuat import config

    self._tmp = tempfile.mkdtemp()
    self._patch_cfg = patch.object(config, "CONFIG_FILE", Path(self._tmp) / "config.json")
    self._patch_cfg.start()
    self.addCleanup(self._patch_cfg.stop)

  def test_ready_when_model_present(self):
    with patch.object(ollama, "ensure_server", return_value="running"), patch.object(
      ollama, "list_models", return_value=["gemma3:4b"]
    ):
      info = ollama.ensure_ready()
    self.assertEqual(info["server"], "running")
    self.assertFalse(info["pulled"])

  def test_missing_model_raises_unless_pull(self):
    with patch.object(ollama, "ensure_server", return_value="running"), patch.object(
      ollama, "list_models", return_value=["llava:latest"]
    ):
      with self.assertRaises(ollama.ModelMissing) as ctx:
        ollama.ensure_ready()
    self.assertIn("ollama pull gemma3:4b", str(ctx.exception))

  def test_pull_when_allowed(self):
    with patch.object(ollama, "ensure_server", return_value="started"), patch.object(
      ollama, "list_models", return_value=[]
    ), patch.object(ollama, "pull_model") as pull:
      info = ollama.ensure_ready(pull=True)
    pull.assert_called_once()
    self.assertTrue(info["pulled"])
    self.assertEqual(info["model"], "gemma3:4b")


if __name__ == "__main__":
  unittest.main()
