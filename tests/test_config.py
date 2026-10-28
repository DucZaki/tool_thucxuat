# -*- coding: utf-8 -*-
"""Cấu hình Ollama: mặc định, đọc/ghi config.json, chuẩn hóa giá trị."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from thuc_xuat import config


class ConfigFileTests(unittest.TestCase):
  def test_defaults_when_no_file(self):
    with patch.object(config, "CONFIG_FILE", Path(tempfile.mktemp())):
      settings = config.get_ollama_settings()
      self.assertTrue(config.auto_open_output_folder())
    self.assertEqual(settings["base_url"], "http://127.0.0.1:11434")
    self.assertEqual(settings["model"], "gemma3:4b")
    self.assertEqual(settings["bands"], 0)
    self.assertEqual(settings["num_ctx"], 8192)

  def test_custom_values_clamped(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "config.json"
      path.write_text(
        json.dumps(
          {
            "ollama_base_url": "http://localhost:1234/",
            "ollama_model": "gemma3:12b",
            "ollama_bands": 99,
            "ollama_timeout": 1,
          }
        ),
        encoding="utf-8",
      )
      with patch.object(config, "CONFIG_FILE", path):
        settings = config.get_ollama_settings()
    self.assertEqual(settings["base_url"], "http://localhost:1234")
    self.assertEqual(settings["model"], "gemma3:12b")
    self.assertEqual(settings["bands"], 4)
    self.assertEqual(settings["timeout"], 30.0)

  def test_ensure_config_file_creates_defaults(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "config.json"
      with patch.object(config, "CONFIG_FILE", path):
        config.ensure_config_file()
      data = json.loads(path.read_text(encoding="utf-8"))
    self.assertEqual(data["ollama_model"], "gemma3:4b")
    self.assertNotIn("provider", data)
    self.assertNotIn("api_key", data)


class OutputFolderTests(unittest.TestCase):
  def test_default_output_dir_is_ketqua_and_created(self):
    with tempfile.TemporaryDirectory() as tmp:
      with patch.object(config, "CONFIG_FILE", Path(tmp) / "config.json"), patch.object(
        config, "app_dir", lambda: Path(tmp)
      ):
        folder = config.output_dir()
      self.assertEqual(folder.name, "KetQua")
      self.assertTrue(folder.is_dir())
      self.assertEqual(folder.parent, Path(tmp))

  def test_absolute_output_dir_from_config(self):
    with tempfile.TemporaryDirectory() as tmp:
      target = Path(tmp) / "custom"
      path = Path(tmp) / "config.json"
      path.write_text(json.dumps({"output_dir": str(target)}), encoding="utf-8")
      with patch.object(config, "CONFIG_FILE", path):
        folder = config.output_dir()
      self.assertEqual(folder, target)
      self.assertTrue(target.is_dir())

  def test_export_include_name_default_and_persist(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "config.json"
      with patch.object(config, "CONFIG_FILE", path):
        self.assertFalse(config.export_include_name())
        config.set_config_value("export_include_name", True)
        self.assertTrue(config.export_include_name())
      data = json.loads(path.read_text(encoding="utf-8"))
    self.assertTrue(data["export_include_name"])
    self.assertEqual(data["ollama_model"], "gemma3:4b")


if __name__ == "__main__":
  unittest.main()
