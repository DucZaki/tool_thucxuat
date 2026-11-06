# -*- coding: utf-8 -*-
"""
Thực Xuất Tool — map phiếu xuất KiotViet sang Excel nhập kho.

  py app.py          # giao diện
  py cli.py build -s file.xlsx -m map.json -o out.xlsx
"""
from __future__ import annotations

import sys

from thuc_xuat.win_utf8 import fix_stdio_utf8

# Phải chạy trước mọi import ghi log / in tiếng Việt
fix_stdio_utf8()


def main() -> int:
  from thuc_xuat.config import ensure_config_file
  from thuc_xuat.proc import install_no_window_defaults

  fix_stdio_utf8()
  # App GUI: không để bất kỳ tiến trình con nào làm nhấp nháy cửa sổ terminal.
  install_no_window_defaults()
  ensure_config_file()

  if len(sys.argv) > 1 and sys.argv[1] == "cli":
    from cli import main as cli_main

    return cli_main(sys.argv[2:])

  from gui import run_gui

  run_gui()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
