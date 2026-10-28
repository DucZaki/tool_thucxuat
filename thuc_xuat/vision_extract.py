# -*- coding: utf-8 -*-
"""Đọc ảnh phiếu xuất bằng Ollama local (gemma3) → map STT -> thực xuất."""
from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from pathlib import Path

from thuc_xuat.cancel import CancelledByUser, check_cancel
from thuc_xuat.config import get_ollama_settings
from thuc_xuat.image_prep import prepare_page
from thuc_xuat.ollama_client import OllamaError, chat_vision

# Retry khi 1 ảnh trả về 0 STT (AI bỏ qua trang)
EMPTY_CHUNK_RETRIES = 3

# ---------------------------------------------------------------------------
# Ollama (gemma3:4b local) — prompt viết ngắn, nhấn mạnh cột viết tay.
# 4b hay lẫn cột "Đặt" in sẵn với cột "Thực xuất" viết tay.
# ---------------------------------------------------------------------------
OLLAMA_SINGLE_PROMPT = """Bạn là OCR phiếu xuất kho Việt Nam. Ảnh là một phần của trang bảng in phiếu xuất kho.

Bảng có các cột: STT | Tên hàng | Số lượng (IN SẴN) | THỰC XUẤT (VIẾT TAY) | Ghi chú.
Cột THỰC XUẤT là cột VIẾT TAY nằm NGAY BÊN PHẢI cột Số lượng in.
Cột Số lượng in đã bị CHE TRẮNG trên ảnh — chỉ đọc cột viết tay.

Đọc MỌI dòng nhìn thấy. Với mỗi STT, chép NGUYÊN VĂN ô THỰC XUẤT viết tay.
- KHÔNG lấy số in ở cột Số lượng.
- Ô viết tay chỉ có dấu tick → "✓". Ô chỉ có X → "X".
- Giữ số lẻ và đơn vị viết tay: "5,5", "14 con", "1T", "10H", "5T x 24".
- Ô trống hoặc không đọc được → bỏ STT đó, KHÔNG bịa.
- Chỉ dùng STT từ 1 đến {max_stt}.{stt_hint}

Trả JSON thuần (không markdown):
{{"thuc_xuat": {{"17": "10", "18": "X"}}}}"""

OLLAMA_BATCH_PROMPT = """Bạn là OCR phiếu xuất kho Việt Nam. Ảnh là MỘT PHẦN của một trang phiếu.

Các phiếu cần đọc:
{orders_context}

Bảng có các cột: STT | Tên hàng | Số lượng (in) | THỰC XUẤT (viết tay) | Ghi chú.
Cột Số lượng in đã bị CHE TRẮNG — chỉ đọc cột viết tay.
1. Đọc mã PDN trên header ảnh (nếu có).
2. Đọc STT và chép NGUYÊN VĂN ô THỰC XUẤT viết tay — không lấy cột Số lượng.
3. Ô chỉ có dấu tick → "✓"; chỉ có X → "X"; giữ số lẻ và đơn vị ("5,5", "14 con", "1T"), giữ cả phần nhân ("3T*3", "5T x 24").
4. Không thấy PDN → chọn phiếu có tên hàng / STT khớp nhất với ảnh.
5. Ô trống → bỏ STT đó, không bịa.

Trả JSON thuần:
{{"pdn": "PDN014331", "thuc_xuat": {{"1": "10", "2": "X"}}}}"""

OLLAMA_ASSIGN_PROMPT = """Ảnh là một trang bảng phiếu xuất kho Việt Nam.

1. Đọc STT của dòng đầu tiên (số ở cột ngoài cùng bên trái).
2. Đọc 3 tên hàng của 3 dòng đầu tiên ở cột "Tên hàng" (bỏ số lượng, bỏ ĐVT).

Trả JSON thuần (không markdown):
{"stt_dau": 19, "ten": ["tên dòng 1", "tên dòng 2", "tên dòng 3"]}"""

# "5 X" / "10x" — model đọc nhầm số in ở cột Đặt rồi ghép với chữ X viết tay.
_X_ONLY_RE = re.compile(r"^(?:\d+(?:[.,]\d+)?\s*)?[xX]$")


def _parse_thuc_xuat_dict(raw: dict) -> dict[int, str]:
  out: dict[int, str] = {}
  for k, v in raw.items():
    if v is None:
      continue
    s = str(v).strip()
    if not s:
      continue
    try:
      key_text = str(k).strip()
      if not re.fullmatch(r"\d{1,4}[.)]?", key_text):
        continue
      stt = int(re.sub(r"\D", "", key_text) or 0)
    except (TypeError, ValueError):
      continue
    if stt > 0:
      out[stt] = s
  return out


def _json_slice(text: str) -> str:
  text = text.strip()
  fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
  if fence:
    text = fence.group(1).strip()
  start = text.find("{")
  end = text.rfind("}")
  if start < 0 or end < 0:
    raise ValueError(f"AI không trả JSON hợp lệ:\n{text[:500]}")
  return text[start : end + 1]


def _normalize_ollama_value(value: str) -> str:
  """Chuẩn hóa giá trị gemma trả về.

  gemma3:4b hay gộp số in ở cột «Đặt» với chữ X viết tay ("5 X", "10x")
  → quy về "X" để đúng quy tắc: có X là bỏ dòng.
  """
  s = str(value or "").strip().strip('"').strip("'").strip()
  if not s:
    return ""
  if _X_ONLY_RE.fullmatch(s):
    return "X"
  return s


def _parse_ollama_response(text: str, max_stt: int) -> dict[int, str]:
  data = json.loads(_json_slice(text))
  raw = data.get("thuc_xuat", data) if isinstance(data, dict) else {}
  if not isinstance(raw, dict):
    raise ValueError("JSON thiếu object thuc_xuat")
  parsed = _parse_thuc_xuat_dict(raw)
  out: dict[int, str] = {}
  for stt, value in parsed.items():
    if not (1 <= stt <= max_stt):
      continue
    norm = _normalize_ollama_value(value)
    if norm:
      out[stt] = norm
  return out


def _parse_ollama_order_response(
  text: str,
  max_stt_by_pdn: dict[str, int],
) -> tuple[str | None, dict[int, str]]:
  """Trả (PDN, map STT). PDN có thể None nếu model không đọc được."""
  from thuc_xuat.image_match import extract_pdn

  data = json.loads(_json_slice(text))
  if not isinstance(data, dict):
    raise ValueError("JSON thiếu object")

  payload = data.get("thuc_xuat")
  if payload is None and isinstance(data.get("orders"), dict) and data["orders"]:
    # Model tự dùng format batch: lấy phiếu đầu tiên.
    first_key, first_val = next(iter(data["orders"].items()))
    if isinstance(first_val, dict):
      payload = first_val.get("thuc_xuat", first_val)
      data.setdefault("pdn", first_key)
  if not isinstance(payload, dict):
    raise ValueError("JSON thiếu object thuc_xuat")

  pdn = extract_pdn(str(data.get("pdn") or "")) or extract_pdn(text)
  max_stt = max_stt_by_pdn.get(pdn or "", 0)
  parsed = _parse_thuc_xuat_dict(payload)
  out: dict[int, str] = {}
  for stt, value in parsed.items():
    if max_stt and not (1 <= stt <= max_stt):
      continue
    norm = _normalize_ollama_value(value)
    if norm:
      out[stt] = norm
  return pdn, out


def build_orders_context(excel_paths: list[Path]) -> str:
  from thuc_xuat.image_match import pdn_from_excel
  from thuc_xuat.processor import load_products

  lines: list[str] = []
  for path in excel_paths:
    pdn = pdn_from_excel(path) or path.stem
    products = load_products(path)
    n = len(products)
    lines.append(f"### {pdn} — {n} SP (STT 1..{n})")
    if products:
      for stt in (1, 2, n):
        if 1 <= stt <= n:
          p = products[stt - 1]
          lines.append(f"  STT {stt}: {p.code} | {p.name[:45]}")
  return "\n".join(lines)


def _sample_lines(products, max_lines: int = 3) -> str:
  from thuc_xuat.processor import Product

  if not products:
    return ""
  lines: list[str] = []
  for i in [0, 1, len(products) - 1]:
    if 0 <= i < len(products) and len(lines) < max_lines:
      p: Product = products[i]
      lines.append(f"  STT {p.stt}: {p.code} | {p.name[:45]}")
  return "\n".join(lines)


def _natural_key(name: str) -> list:
  """Sắp page2 trước page10 (không dùng sort alphabetical thuần)."""
  parts = re.split(r"(\d+)", name.lower())
  key: list = []
  for p in parts:
    if p.isdigit():
      key.append(int(p))
    elif p:
      key.append(p)
  return key


def _sort_images(paths: list[Path]) -> list[Path]:
  """Sắp theo tên file (natural sort) để trang liên tiếp đúng thứ tự."""
  return sorted(paths, key=lambda p: _natural_key(p.name))



def _maps_contiguous(maps: list[dict[int, str]]) -> bool:
  """Kết quả đọc của một ảnh có phải một dãy STT liền mạch không.

  Ảnh chụp phần cuối trang (chỉ 1 dòng + phần tổng kết/chữ ký) hay bị model
  đọc thành vài STT rời rạc (1, 2, 17, 18...). Dãy rời rạc như vậy mà đem gộp
  sẽ đè số đúng của trang khác.
  """
  keys = sorted({stt for mp in maps for stt in mp})
  if len(keys) < 3:
    return True
  best = run = 1
  for prev, cur in zip(keys, keys[1:]):
    run = run + 1 if cur - prev <= 2 else 1
    best = max(best, run)
  return best >= max(3, int(len(keys) * 0.5))


def _merge_thuc_maps_weighted(maps: list[dict[int, str]]) -> dict[int, str]:
  """Gộp nhiều ảnh, ưu tiên ảnh mà STT nằm giữa trang.

  Ảnh chụp thường chồng lấn 1–2 dòng ở mép (dòng bị cắt → đọc sai). Dòng
  càng xa mép trên/dưới của ảnh càng đáng tin.
  """
  best: dict[int, tuple[int, str]] = {}
  for mp in maps:
    if not mp:
      continue
    lo, hi = min(mp), max(mp)
    for stt, val in mp.items():
      s = (val or "").strip()
      if not s:
        continue
      edge = min(stt - lo, hi - stt)
      cur = best.get(stt)
      if cur is None or edge > cur[0]:
        best[stt] = (edge, s)
      elif edge == cur[0] and cur[1].upper() == "X" and s.upper() != "X":
        best[stt] = (edge, s)
  return {stt: val for stt, (_, val) in best.items()}


def _ollama_band_map(
  prompt: str,
  band,
  max_stt: int,
  cfg: dict,
  on_progress,
  label: str,
  cancel_event: threading.Event | None,
  min_keys: int = 1,
) -> dict[int, str]:
  """Đọc 1 dải ảnh → map STT. Retry khi JSON hỏng hoặc đọc được quá ít dòng."""
  import time


  last_exc: Exception | None = None
  parsed_ok = False
  best: dict[int, str] = {}
  for attempt in range(1, EMPTY_CHUNK_RETRIES + 1):
    check_cancel(cancel_event)
    if on_progress:
      suffix = f" (thử lại {attempt})" if attempt > 1 else ""
      on_progress(f"Đang đọc {label}{suffix}")
    try:
      text = chat_vision(
        prompt,
        [band.data],
        model=cfg["model"],
        base_url=cfg["base_url"],
        timeout=cfg["timeout"],
        num_ctx=cfg["num_ctx"],
        cancel_event=cancel_event,
      )
      part = _parse_ollama_response(text, max_stt)
      parsed_ok = True
      if len(part) > len(best):
        best = part
      if len(part) >= min_keys:
        return part
    except CancelledByUser:
      raise
    except OllamaError:
      raise
    except Exception as exc:  # JSON hỏng, model trả rỗng...
      last_exc = exc
    if attempt < EMPTY_CHUNK_RETRIES:
      time.sleep(1.0 * attempt)
  if not parsed_ok and last_exc is not None:
    raise ValueError(f"AI trả JSON không đọc được cho {label}:\n{last_exc}")
  return best


def _norm_name(text: str) -> str:
  """Bỏ dấu, viết thường, bỏ ký tự lạ — để so tên hàng model đọc với Excel."""
  import unicodedata

  s = unicodedata.normalize("NFD", str(text or "").lower())
  s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
  s = re.sub(r"[^a-z0-9 ]+", " ", s)
  return re.sub(r"\s+", " ", s).strip()


def _best_names_match(names: list[str], product_names: list[str]) -> tuple[float, int]:
  """Điểm khớp + vị trí (STT 1-based) tốt nhất của các tên đọc được trong Excel."""
  from difflib import SequenceMatcher

  norm = [_norm_name(n) for n in names if _norm_name(n)]
  if not norm or len(product_names) < len(norm):
    return 0.0, 0
  best_score = 0.0
  best_pos = 0
  for i in range(len(product_names) - len(norm) + 1):
    total = sum(
      SequenceMatcher(None, name, product_names[i + j]).ratio()
      for j, name in enumerate(norm)
    )
    score = total / len(norm)
    if score > best_score:
      best_score = score
      best_pos = i + 1
  return best_score, best_pos


def _names_score(names: list[str], product_names: list[str]) -> float:
  return _best_names_match(names, product_names)[0]


def _assign_pages_to_orders(
  images: list[Path],
  excel_paths: list[Path],
  cfg: dict,
  prog: Callable[[str], None],
  cancel_event: threading.Event | None,
) -> dict[str, list[Path]]:
  """Chia ảnh về từng phiếu bằng 3 tên hàng đầu trang (so với danh sách SP).

  Ảnh chụp từng trang thường không có header PDN; tên hàng ở đầu trang là
  dấu hiệu ổn định nhất để biết trang thuộc phiếu nào.
  """
  from thuc_xuat.image_match import pdn_from_excel
  from thuc_xuat.processor import load_products

  orders: list[tuple[str, list[str]]] = []
  for excel in excel_paths:
    pdn = (pdn_from_excel(excel) or excel.stem).upper()
    names = [_norm_name(p.name) for p in load_products(excel)]
    orders.append((pdn, names))

  assigned: dict[str, list[Path]] = {}
  for i, img in enumerate(images, 1):
    check_cancel(cancel_event)
    band = prepare_page(img, cfg["bands"])[0]
    prog(f"Đang nhận diện ảnh {i}/{len(images)}: {img.name}...")
    try:
      text = chat_vision(
        OLLAMA_ASSIGN_PROMPT,
        [band.data],
        model=cfg["model"],
        base_url=cfg["base_url"],
        timeout=cfg["timeout"],
        num_ctx=cfg["num_ctx"],
        cancel_event=cancel_event,
      )
      data = json.loads(_json_slice(text))
      names = data.get("ten") or []
      if isinstance(names, str):
        names = [names]
      names = [str(n) for n in names if str(n).strip()]
      stt_dau = data.get("stt_dau")
      if isinstance(stt_dau, str) and stt_dau.strip().isdigit():
        stt_dau = int(stt_dau.strip())
      if not isinstance(stt_dau, int):
        stt_dau = 0
    except CancelledByUser:
      raise
    except OllamaError:
      raise
    except Exception:
      names = []
      stt_dau = 0

    # STT đầu trang vượt quá số dòng của một phiếu → chỉ phiếu còn lại là khớp.
    eligible = [
      (pdn, names_list) for pdn, names_list in orders if len(names_list) >= stt_dau > 0
    ]
    matches = sorted(
      ((_best_names_match(names, names_list), pdn) for pdn, names_list in orders),
      key=lambda item: item[0][0],
      reverse=True,
    )
    (best_score, best_pos), best_pdn = matches[0] if matches else ((0.0, 0), "")
    second = matches[1][0][0] if len(matches) > 1 else 0.0
    if stt_dau and len(eligible) == 1 and (not names or best_score >= 0.30):
      pdn = eligible[0][0]
      assigned.setdefault(pdn, []).append(img)
      prog(f"Ảnh {i} → {pdn} (STT đầu {stt_dau})")
      continue
    if names and best_score >= 0.40 and best_score - second >= 0.02:
      assigned.setdefault(best_pdn, []).append(img)
      prog(f"Ảnh {i} → {best_pdn} (khớp tên {best_score:.2f}, STT đầu {best_pos})")
    else:
      prog(
        f"Cảnh báo: ảnh {i} ({img.name}) không rõ phiếu — "
        f"khớp {best_pdn or '?'} {best_score:.2f} / nhì {second:.2f}"
      )
  return assigned


def _split_band(band, parts: int = 2, overlap: int = 80) -> list[bytes]:
  """Cắt 1 ảnh trang thành vài dải ngang chồng lấn (đọc lại khi model bỏ dòng)."""
  import cv2
  import numpy as np

  arr = cv2.imdecode(np.frombuffer(band.data, np.uint8), cv2.IMREAD_COLOR)
  if arr is None:
    return []
  h = arr.shape[0]
  step = max(1, h // parts)
  out: list[bytes] = []
  for i in range(parts):
    y0 = max(0, i * step - (overlap if i else 0))
    y1 = min(h, (i + 1) * step + (overlap if i < parts - 1 else 0))
    ok, buf = cv2.imencode(".jpg", arr[y0:y1], [cv2.IMWRITE_JPEG_QUALITY, 92])
    if ok:
      out.append(buf.tobytes())
  return out


def _extract_one_ollama(
  excel: Path,
  images: list[Path],
  cfg: dict,
  prog: Callable[[str], None],
  on_progress: Callable[[str], None] | None,
  cancel_event: threading.Event | None,
) -> dict[int, str]:
  """Đọc 1 phiếu từ các ảnh đã biết thuộc phiếu đó."""
  from types import SimpleNamespace

  from thuc_xuat.image_match import pdn_from_excel
  from thuc_xuat.processor import load_products

  pdn = (pdn_from_excel(excel) or excel.stem).upper()
  max_stt = len(load_products(excel))
  collected: list[dict[int, str]] = []
  empty_images = 0
  skipped_images = 0
  for i, img in enumerate(images, 1):
    check_cancel(cancel_event)
    img_maps: list[dict[int, str]] = []
    for band in prepare_page(img, cfg["bands"]):
      prompt = OLLAMA_SINGLE_PROMPT.format(
        pdn=pdn, max_stt=max_stt, stt_hint=""
      )
      label = f"{pdn} — ảnh {i}/{len(images)}: {img.name} | {band.label}"
      part = _ollama_band_map(
        prompt,
        band,
        max_stt,
        cfg,
        on_progress,
        label,
        cancel_event,
        min_keys=5 if band.mode in ("page", "table") else 1,
      )
      # Cả trang mà vẫn đọc được < 5 dòng → model bị "ngợp" ảnh dài.
      # Đọc lại bằng 2 nửa trang chồng lấn rồi gộp.
      if band.mode in ("page", "table") and len(part) < 5:
        sub_maps: list[dict[int, str]] = []
        for j, data in enumerate(_split_band(band), 1):
          sub = _ollama_band_map(
            prompt,
            SimpleNamespace(data=data, mode="band"),
            max_stt,
            cfg,
            on_progress,
            f"{label} — nửa {j}",
            cancel_event,
            min_keys=3,
          )
          if sub:
            sub_maps.append(sub)
        alt = _merge_thuc_maps_weighted(sub_maps)
        if len(alt) > len(part):
          prog(
            f"Đọc lại {band.label} theo nửa trang: {len(alt)} STT "
            f"(cả trang chỉ {len(part)})"
          )
          part = alt
      if part:
        img_maps.append(part)
      seen = len(
        {stt for mp in collected for stt in mp}
        | {stt for mp in img_maps for stt in mp}
      )
      prog(f"Đã đọc {label} | đã có {seen} STT")
    if not img_maps:
      empty_images += 1
      prog(f"Cảnh báo: ảnh {img.name} không đọc được STT nào")
      continue
    if _maps_contiguous(img_maps):
      collected.extend(img_maps)
    else:
      skipped_images += 1
      keys = sorted({stt for mp in img_maps for stt in mp})
      prog(
        f"Cảnh báo: bỏ kết quả ảnh {img.name} — STT rời rạc "
        f"({', '.join(str(k) for k in keys[:8])}...)"
      )

  merged = _merge_thuc_maps_weighted(collected)
  prog(
    f"Xong {pdn}: {len(merged)} STT từ {len(images)} ảnh"
    + (f" ({empty_images} ảnh trống)" if empty_images else "")
    + (f" ({skipped_images} ảnh bị bỏ)" if skipped_images else "")
  )
  return merged


def _extract_orders_ollama(
  excel_paths: list[Path],
  image_paths: list[Path],
  on_progress: Callable[[str], None] | None = None,
  cancel_event: threading.Event | None = None,
) -> dict[str, dict[int, str]]:
  from thuc_xuat.image_match import pdn_from_excel

  def prog(msg: str) -> None:
    if on_progress:
      on_progress(msg)

  cfg = get_ollama_settings()
  ordered = _sort_images(image_paths)
  prog(
    f"Ollama {cfg['model']}: {len(ordered)} ảnh, {len(excel_paths)} phiếu "
    f"(làm phẳng trang + che cột số lượng in)..."
  )

  if len(excel_paths) == 1:
    pdn = (pdn_from_excel(excel_paths[0]) or excel_paths[0].stem).upper()
    merged = _extract_one_ollama(
      excel_paths[0], ordered, cfg, prog, on_progress, cancel_event
    )
    return {pdn: merged}

  # Nhiều phiếu: chia ảnh theo tên hàng đầu trang, rồi đọc từng phiếu riêng.
  assignments = _assign_pages_to_orders(
    ordered, excel_paths, cfg, prog, cancel_event
  )
  if not assignments:
    raise ValueError(
      "gemma3:4b không nhận diện được ảnh nào thuộc phiếu nào.\n\n"
      "Kiểm tra ảnh có chụp rõ 3 dòng đầu bảng (tên hàng) và đúng phiếu không."
    )

  result: dict[str, dict[int, str]] = {}
  for excel in excel_paths:
    pdn = (pdn_from_excel(excel) or excel.stem).upper()
    images = assignments.get(pdn) or []
    if not images:
      prog(f"Cảnh báo: không có ảnh nào khớp {pdn}")
      continue
    result[pdn] = _extract_one_ollama(
      excel, images, cfg, prog, on_progress, cancel_event
    )

  missing = [pdn for pdn in assignments if pdn not in result]
  if missing:
    prog("Chưa đọc được phiếu: " + ", ".join(missing))
  unassigned = len(ordered) - sum(len(v) for v in assignments.values())
  prog(
    f"Xong đọc ảnh: {sum(len(v) for v in result.values())} STT / "
    f"{len(result)} phiếu từ {len(ordered)} ảnh"
    + (f" ({unassigned} ảnh chưa xác định phiếu)" if unassigned else "")
  )
  return result


def extract_orders_from_images(
  excel_paths: list[str | Path],
  image_paths: list[str | Path],
  on_progress: Callable[[str], None] | None = None,
  cancel_event: threading.Event | None = None,
) -> dict[str, dict[int, str]]:
  imgs = [Path(p) for p in image_paths]
  excels = [Path(p) for p in excel_paths]
  for p in imgs:
    if not p.exists():
      raise FileNotFoundError(f"Không tìm thấy ảnh: {p}")
  return _extract_orders_ollama(excels, imgs, on_progress, cancel_event)
