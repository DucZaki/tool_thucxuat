# -*- coding: utf-8 -*-
"""Regression coverage for the app configuration."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from thuc_xuat import config


class ConfigFileTests(unittest.TestCase):
  def setUp(self):
    folder = tempfile.TemporaryDirectory()
    self.addCleanup(folder.cleanup)
    self.path = Path(folder.name) / "config.json"
    override = patch.object(config, "CONFIG_FILE", self.path)
    override.start()
    self.addCleanup(override.stop)

  def write_config(self, values):
    self.path.write_text(json.dumps(values), encoding="utf-8")

  def test_defaults_when_no_file(self):
    self.assertEqual(config.load_config()["nim_model"], "nim-auto")
    self.assertEqual(config.image_band_count(), 0)
    self.assertFalse(config.auto_open_output_folder())

  def test_image_band_count_clamps_to_supported_range(self):
    for value, expected in [(99, 4), (-1, 0), ("2", 2), ("bad", 0)]:
      with self.subTest(value=value):
        self.write_config({"image_bands": value})
        self.assertEqual(config.image_band_count(), expected)

  def test_legacy_band_setting_survives_saving_model(self):
    self.write_config({"ollama_model": "unused", "ollama_bands": 3})
    self.assertEqual(config.image_band_count(), 3)
    config.set_config_value("nim_model", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
    stored = json.loads(self.path.read_text(encoding="utf-8"))
    self.assertEqual(stored["image_bands"], 3)
    self.assertNotIn("ollama_model", stored)
    self.assertNotIn("ollama_bands", stored)

  def test_explicit_band_setting_overrides_legacy_key(self):
    self.write_config({"image_bands": 0, "ollama_bands": 4})
    self.assertEqual(config.image_band_count(), 0)

  def test_ensure_config_file_creates_defaults_without_key(self):
    config.ensure_config_file()
    data = json.loads(self.path.read_text(encoding="utf-8"))
    self.assertEqual(data["nim_model"], "nim-auto")
    self.assertEqual(data["image_bands"], 0)
    self.assertNotIn("api_key", data)

  def test_broken_json_falls_back_to_defaults(self):
    self.path.write_text("{ hong json", encoding="utf-8")
    self.assertEqual(config.load_config(), config.DEFAULT_CONFIG)


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
    self.assertEqual(data["nim_model"], "nim-auto")


if __name__ == "__main__":
  unittest.main()
