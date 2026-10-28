# -*- coding: utf-8 -*-
"""CLI: đọc ảnh phiếu bằng Ollama → xuất Excel nhập kho."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from thuc_xuat.config import output_dir
from thuc_xuat.io_json import load_mapping, save_mapping
from thuc_xuat.pipeline import process_batch_like_chat
from thuc_xuat.processor import build_output, default_output_path, load_products, save_output
from thuc_xuat.updater import check_for_update
from thuc_xuat.version import __version__


def cmd_process(args: argparse.Namespace) -> int:
  batch = process_batch_like_chat(
    args.source,
    args.images,
    output_path=args.output,
    on_progress=lambda msg: print(msg, flush=True),
    output_dir=Path(args.out_dir) if args.out_dir else output_dir(),
    include_name=args.with_name,
  )
  for r in batch.results:
    print(f"OK: {r.output_path}")
    print(f"  SP goc: {r.product_count}")
    print(f"  STT doc tu anh: {r.extracted_stt}")
    print(f"  Dong xuat: {len(r.included)}")
    print(f"  Bo: {len(r.skipped)}")
    save_mapping(
      r.output_path.with_suffix(".json"),
      r.thuc_xuat_map,
      meta={"source": str(r.source_path)},
    )
  for p, msg in batch.errors:
    print(f"LOI {p.name}: {msg}", file=sys.stderr)
  return 0 if batch.ok_count and not batch.fail_count else 1


def cmd_build(args: argparse.Namespace) -> int:
  products = load_products(args.source)
  mapping = load_mapping(args.map)
  included, skipped = build_output(products, mapping)
  if args.output:
    out = Path(args.output)
  else:
    out = default_output_path(
      args.source,
      out_dir=Path(args.out_dir) if args.out_dir else output_dir(),
      include_name=args.with_name,
    )
  out.parent.mkdir(parents=True, exist_ok=True)
  save_output(included, out, include_name=args.with_name)
  print(f"OK: {len(included)} dong -> {out}")
  return 0


def cmd_check_update(args: argparse.Namespace) -> int:
  info = check_for_update()
  if info is None:
    print(f"Ban moi nhat: v{__version__}")
    return 0
  print(f"Co ban moi: v{info.version} (dang dung v{__version__})")
  if info.notes:
    print(info.notes[:800])
  print(info.download_url)
  return 0


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="Thuc xuat: anh + Excel -> Excel ket qua")
  sub = parser.add_subparsers(dest="command", required=True)

  p_proc = sub.add_parser("process", help="Doc anh bang Ollama va xuat Excel")
  p_proc.add_argument("-s", "--source", nargs="+", required=True, help="Excel goc (1 hoac nhieu)")
  p_proc.add_argument("-i", "--images", nargs="+", required=True, help="Anh phieu")
  p_proc.add_argument("-o", "--output", help="Chi dung khi co 1 Excel")
  p_proc.add_argument("--out-dir", help="Thu muc xuat (mac dinh: KetQua canh app)")
  p_proc.add_argument("--with-name", action="store_true", help="Xuat kem cot Ten san pham")
  p_proc.set_defaults(func=cmd_process)

  p_build = sub.add_parser("build", help="Xuat Excel tu file JSON map (khong can anh)")
  p_build.add_argument("-s", "--source", required=True)
  p_build.add_argument("-m", "--map", required=True)
  p_build.add_argument("-o", "--output")
  p_build.add_argument("--out-dir", help="Thu muc xuat (mac dinh: KetQua canh app)")
  p_build.add_argument("--with-name", action="store_true", help="Xuat kem cot Ten san pham")
  p_build.set_defaults(func=cmd_build)

  p_check = sub.add_parser("check-update", help="Kiem tra ban moi tren GitHub Releases")
  p_check.set_defaults(func=cmd_check_update)

  args = parser.parse_args(argv)
  try:
    return args.func(args)
  except Exception as exc:
    print(f"LOI: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
