"""Bundled inventory inputs, available in source and PyInstaller builds."""
import sys
from pathlib import Path
from thuc_xuat.config import load_config

def bundled_file(name):
  root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
  return root / "data" / name

def inventory_inputs():
  cfg = load_config()
  paths = []
  for key, name in (("inventory_catalog", "DanhMucSanPham.xlsx"), ("inventory_template", "MauFileNhapHang.xlsx")):
    chosen = Path(cfg[key]) if cfg.get(key) else None
    paths.append(chosen if chosen and chosen.is_file() else bundled_file(name))
  return tuple(paths)
