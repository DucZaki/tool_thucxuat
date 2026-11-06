# -*- coding: utf-8 -*-
"""Không để tiến trình con làm nhấp nháy cửa sổ terminal (Windows)."""
import os
import subprocess
import unittest
from unittest.mock import patch

from thuc_xuat import proc


class HiddenWindowTests(unittest.TestCase):
  def test_hidden_kwargs(self):
    kwargs = proc.hidden_kwargs()
    if os.name != "nt":
      self.assertEqual(kwargs, {})
      return
    self.assertTrue(int(kwargs["creationflags"]) & proc.CREATE_NO_WINDOW)
    self.assertIsNotNone(kwargs["startupinfo"])

  def test_detached_kwargs(self):
    kwargs = proc.hidden_kwargs(detached=True)
    if os.name != "nt":
      self.assertEqual(kwargs, {})
      return
    self.assertEqual(int(kwargs["creationflags"]) & proc.DETACHED_PROCESS,
                     proc.DETACHED_PROCESS)
    # DETACHED_PROCESS: Windows không cấp console nên không có cửa sổ.
    self.assertNotIn("startupinfo", kwargs)

  def test_popen_helper_passes_flags(self):
    with patch.object(proc.subprocess, "Popen") as popen_cls:
      proc.popen(["tool.exe", "arg"])
    _, kwargs = popen_cls.call_args
    if os.name == "nt":
      self.assertTrue(int(kwargs["creationflags"]) & proc.CREATE_NO_WINDOW)

  def test_run_helper_passes_flags(self):
    with patch.object(proc.subprocess, "run") as run_func:
      proc.run(["tasklist"])
    _, kwargs = run_func.call_args
    self.assertTrue(kwargs["capture_output"])
    if os.name == "nt":
      self.assertTrue(int(kwargs["creationflags"]) & proc.CREATE_NO_WINDOW)

  @unittest.skipUnless(os.name == "nt", "chỉ Windows mới có console window")
  def test_global_patch_hides_every_popen(self):
    original = subprocess.Popen
    was_installed = proc._installed
    real_init = original.__init__
    seen: dict = {}

    def fake_init(self, *args, **kwargs):  # noqa: ANN001
      seen.update(kwargs)
      raise RuntimeError("stop")  # không thực sự chạy tiến trình

    try:
      original.__init__ = fake_init
      proc._installed = False
      proc.install_no_window_defaults()
      with self.assertRaises(RuntimeError):
        subprocess.Popen(["tool.exe"])
    finally:
      original.__init__ = real_init
      subprocess.Popen = original
      proc._installed = was_installed

    self.assertTrue(int(seen["creationflags"]) & proc.CREATE_NO_WINDOW)
    self.assertEqual(seen["stdout"], subprocess.DEVNULL)


if __name__ == "__main__":
  unittest.main()
