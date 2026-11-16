# -*- coding: utf-8 -*-
"""Luồng chính: Excel + ảnh → file kết quả."""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from thuc_xuat.cancel import CancelledByUser, check_cancel
from thuc_xuat.image_match import pdn_from_excel
from thuc_xuat.nim_client import NimVision
from thuc_xuat.processor import (
  OutputRow,
  build_output,
  default_output_path,
  load_products,
  save_output,
)
from thuc_xuat.vision_extract import VisionResume, extract_orders_from_images


@dataclass
class ProcessResult:
  source_path: Path
  output_path: Path
  product_count: int
  extracted_stt: int
  included: list[OutputRow]
  skipped: list[OutputRow]
  thuc_xuat_map: dict[int, str] = field(default_factory=dict)
  include_name: bool = False


@dataclass
class BatchResult:
  results: list[ProcessResult] = field(default_factory=list)
  errors: list[tuple[Path, str]] = field(default_factory=list)
  warnings: list[str] = field(default_factory=list)
  pending_images: list[Path] = field(default_factory=list)

  @property
  def ok_count(self) -> int:
    return len(self.results)

  @property
  def fail_count(self) -> int:
    return len(self.errors)


def _lookup_thuc_map(
  order_maps: dict[str, dict[int, str]],
  pdn: str | None,
) -> dict[int, str]:
  if not pdn:
    return {}
  key = pdn.upper()
  if key in order_maps:
    return order_maps[key]
  for k, v in order_maps.items():
    if k.upper() == key:
      return v
  return {}


def process_batch_like_chat(
  source_excels: list[str | Path],
  image_paths: list[str | Path],
  output_path: str | Path | None = None,
  on_progress: Callable[[str], None] | None = None,
  cancel_event: threading.Event | None = None,
  output_dir: str | Path | None = None,
  include_name: bool = False,
  *,
  nim: NimVision | None = None,
  images_by_excel: dict[Path, list[Path]] | None = None,
  resume: VisionResume | None = None,
  manual_edits: dict[Path, dict[int, str | None]] | None = None,
) -> BatchResult:
  """
  User đưa tất cả Excel + ảnh → AI đọc → xuất file kết quả cho từng phiếu.

  output_path: chỉ dùng khi có đúng 1 Excel (ghi đè vị trí mặc định).
  output_dir: thư mục chứa file kết quả; None = cùng thư mục Excel gốc.
  include_name: True = file kết quả có thêm cột «Tên sản phẩm».
  images_by_excel: nhóm ảnh do người dùng xác nhận, bỏ qua bước đoán phiếu.
  """
  excels = [Path(p) for p in source_excels]
  if not excels:
    raise ValueError("Chưa có file Excel.")
  if not image_paths:
    raise ValueError("Chưa có ảnh phiếu.")
  keys = [(pdn_from_excel(src) or src.stem).upper() for src in excels]
  if len(keys) != len(set(keys)):
    raise ValueError("Có nhiều Excel trùng mã phiếu. Chỉ giữ một file Excel cho mỗi phiếu.")

  check_cancel(cancel_event)
  batch = BatchResult()
  order_maps = extract_orders_from_images(
    excels,
    image_paths,
    on_progress=on_progress,
    cancel_event=cancel_event,
    on_issue=batch.warnings.append,
    nim=nim,
    images_by_excel=images_by_excel,
    resume=resume,
  )
  if resume is not None:
    batch.pending_images = resume.pending([Path(path) for path in image_paths])

  for src in excels:
    try:
      check_cancel(cancel_event)
      pdn = pdn_from_excel(src) or src.stem
      if on_progress:
        on_progress(f"Đang tạo file {pdn}...")
      products = load_products(src)
      if not products:
        raise ValueError("File Excel không có sản phẩm.")
      thuc_xuat_map = _lookup_thuc_map(order_maps, pdn)
      if not thuc_xuat_map:
        if images_by_excel is not None:
          names = ", ".join(path.name for path in images_by_excel[src][:4])
          raise ValueError(
            f"Không đọc được STT trên {len(images_by_excel[src])} ảnh đã gán cho {pdn}.\n"
            f"Ảnh: {names}. Kiểm tra cột STT và cột Thực xuất trên các ảnh này."
          )
        if pdn.upper() not in order_maps and len(excels) > 1:
          raise ValueError(f"Không xác định được ảnh nào thuộc {pdn}. Kiểm tra ảnh chụp đầu trang và tên hàng.")
        raise ValueError(
          f"AI không đọc được STT nào cho {pdn}.\n"
          "Kiểm tra ảnh có đúng phiếu và mã PDN trên phiếu."
        )
      thuc_xuat_map = dict(thuc_xuat_map)
      for stt, raw in (manual_edits or {}).get(src, {}).items():
        if raw is None:
          thuc_xuat_map.pop(stt, None)
        else:
          thuc_xuat_map[stt] = raw
      included, skipped = build_output(products, thuc_xuat_map)
      if output_path and len(excels) == 1:
        out = Path(output_path)
      else:
        out = default_output_path(src, out_dir=output_dir, include_name=include_name)
      out.parent.mkdir(parents=True, exist_ok=True)
      save_output(included, out, include_name=include_name)
      batch.results.append(
        ProcessResult(
          source_path=src,
          output_path=out,
          product_count=len(products),
          extracted_stt=len(thuc_xuat_map),
          included=included,
          skipped=skipped,
          thuc_xuat_map=thuc_xuat_map,
          include_name=include_name,
        )
      )
    except CancelledByUser:
      raise
    except Exception as exc:
      batch.errors.append((src, str(exc)))
  return batch
