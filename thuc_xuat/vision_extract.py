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
from thuc_xuat.image_prep import prepare_page, split_image
from thuc_xuat.ollama_client import OllamaError, chat_vision

# Retry khi 1 ảnh trả về 0 STT (AI bỏ qua trang)
EMPTY_CHUNK_RETRIES = 5

# ---------------------------------------------------------------------------
# Ollama (gemma3:4b local) — prompt viết ngắn, nhấn mạnh cột viết tay.
# 4b hay lẫn cột "Đặt" in sẵn với cột "Thực xuất" viết tay.
# ---------------------------------------------------------------------------
OLLAMA_SINGLE_PROMPT = """Bạn là OCR phiếu xuất kho. Ảnh là một trang bảng in (đã cắt cột «Số lượng đặt» in sẵn).

Bảng còn các cột, từ trái sang phải:
- STT: số in nhỏ ở mép trái.
- Tên hàng: chữ IN dài (tiếng Việt và/hoặc tiếng Nhật). TUYỆT ĐỐI KHÔNG chép cột này.
- THỰC XUẤT: cột VIẾT TAY, mỗi ô rất ngắn (1–6 ký tự): số như 10, 5, 110, 5,5; hoặc 1T, 10H; hoặc dấu ✓; hoặc X.
- Ghi chú: cột dấu ✓ ở mép phải — BỎ QUA.

Quy tắc:
- Chỉ đọc cột THỰC XUẤT viết tay. KHÔNG đọc tên hàng, KHÔNG đọc cột ghi chú.
- BƯỚC 1: xác định STT dòng ĐẦU và dòng CUỐI nhìn thấy. BƯỚC 2: chỉ đọc các dòng trong khoảng đó.
- CHỈ ghi các STT CÓ TRONG ẢNH, không ghi tiếp tới {max_stt}.
- Ô chỉ có dấu tick → "✓". Ô chỉ có X → "X". Ô trống thực sự → bỏ STT đó.
- Giữ đơn vị viết tay: "14 con", "5,5", "1T", "10H", "5T x 24".
- Chữ T viết tay giống số 7 → ghi T: "1T", "4T", "10T", không ghi 17/47/107.
- Không tự nhân/quy đổi.
- STT nằm trong khoảng 1–{max_stt}.{stt_hint}

Trả JSON thuần (không markdown). Ví dụ ĐỊNH DẠNG (số 999 là giả, KHÔNG chép):
{{"stt_dau": 999, "stt_cuoi": 999, "thuc_xuat": {{"999": "ví dụ"}}}}"""

STT_VISIBLE_PROMPT = """Ảnh là cột STT (số thứ tự in sẵn) ở mép trái bảng phiếu xuất kho.
Chỉ đọc các số IN RÕ trên giấy. BỎ QUA chữ/số mờ ở mặt sau tờ giấy (nhìn như
bóng mờ, có thể bị ngược) và watermark.
Liệt kê các số nhìn thấy, từ trên xuống. Nếu không có số nào, trả mảng rỗng.

Trả JSON thuần (không markdown): {"stt": [84]}"""

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
      # Chấp nhận "10", "10.", "10)", "10:", "10 -" (model hay thêm dấu).
      m = re.match(r"^(\d{1,4})\s*[.)\]:\-]?$", key_text)
      if not m:
        continue
      stt = int(m.group(1))
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
  # Dấu câu thừa cuối giá trị ("10.", "X,", "✓.") — bỏ để khớp quy tắc.
  s = s.strip(" .,;:")
  if not s:
    return ""
  if _X_ONLY_RE.fullmatch(s):
    return "X"
  return s


# Từ chỉ đơn vị hay gặp trong ô «Thực xuất» (đã bỏ dấu). Từ khác dài hơn 3 chữ
# cái mà không nằm trong danh sách này = model đọc nhầm chữ trong trang
# (vd "Vận chuyển", "IWATSUKA") chứ không phải số lượng.
_QTY_UNIT_WORDS = {
  "con", "goi", "qua", "hop", "chai", "cai", "bich", "tui", "bao", "thung",
  "loc", "cuon", "chiec", "doi", "bo", "hu", "can", "khay", "vien", "hop",
  "thung", "kg", "g", "ml", "l", "m", "cm", "mm", "lit", "tan", "ta", "yen",
}
_LETTER_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
# Chữ Nhật/Trung/Hàn không bao giờ có trong ô số lượng viết tay.
_CJK_RE = re.compile(r"[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef\uac00-\ud7af]")


def _looks_like_qty(value: str) -> bool:
  """Giá trị có dạng số lượng viết tay không (số, X, ✓, kèm đơn vị ngắn).

  Trang trống/nhiều chữ khiến model trả cả câu hay cả tên hàng vào ô «Thực
  xuất»; lọc để không ghi chữ vào file kết quả và để ảnh đó được đọc lại.
  """
  import unicodedata

  v = (value or "").strip()
  if not v:
    return False
  if len(v) > 16:
    return False
  if _CJK_RE.search(v):
    return False
  plain = unicodedata.normalize("NFD", v.lower().replace("×", "x"))
  plain = "".join(c for c in plain if unicodedata.category(c)[0] != "M")
  for word in _LETTER_WORD_RE.findall(plain):
    if len(word) <= 3:
      continue
    if word in _QTY_UNIT_WORDS:
      continue
    return False
  return True


def _is_product_name(value: str, product_name: str) -> bool:
  """Giá trị model trả có phải tên hàng (đọc nhầm cột) chứ không phải số lượng."""
  from difflib import SequenceMatcher

  v = _norm_name(value)
  n = _norm_name(product_name)
  if not v or not n:
    return False
  if v == n or (len(v) >= 4 and v in n):
    return True
  return SequenceMatcher(None, v, n).ratio() >= 0.72


def _stt_int(value: object) -> int | None:
  """Đọc số STT model trả (có thể kèm dấu như "17.", "38)")."""
  m = re.match(r"^\s*(\d{1,4})\s*[.)\]:\-]?\s*$", str(value or ""))
  return int(m.group(1)) if m else None


def _visible_stt_range(data: dict) -> tuple[int, int] | None:
  """Khoảng STT model khai là nhìn thấy trong ảnh (stt_dau..stt_cuoi)."""
  if not isinstance(data, dict):
    return None
  lo = _stt_int(data.get("stt_dau"))
  hi = _stt_int(data.get("stt_cuoi"))
  if lo is None or hi is None or hi < lo:
    return None
  return lo, hi


def _parse_ollama_data(
  data: object, max_stt: int
) -> tuple[dict[int, str], tuple[int, int] | None]:
  """Đọc JSON model đã parse → (map STT, khoảng STT nhìn thấy)."""
  raw = data.get("thuc_xuat", data) if isinstance(data, dict) else {}
  if not isinstance(raw, dict):
    raise ValueError("JSON thiếu object thuc_xuat")
  parsed = _parse_thuc_xuat_dict(raw)
  out: dict[int, str] = {}
  for stt, value in parsed.items():
    if not (1 <= stt <= max_stt):
      continue
    norm = _normalize_ollama_value(value)
    if norm and _looks_like_qty(norm):
      out[stt] = norm
  rng = _visible_stt_range(data) if isinstance(data, dict) else None
  # Model hay "đọc tiếp" các STT không có trong ảnh (bịa tới max_stt). Cắt về
  # đúng khoảng stt_dau..stt_cuoi mà nó khai, nhưng chỉ khi phần lớn kết quả
  # nằm trong khoảng đó (tránh cắt nhầm khi model khai khoảng sai).
  if rng and out:
    lo, hi = rng
    inside = {stt: val for stt, val in out.items() if lo <= stt <= hi}
    if inside and len(inside) >= max(1, len(out) * 0.5):
      out = inside
  return out, rng


def _parse_ollama_response(text: str, max_stt: int) -> dict[int, str]:
  return _parse_ollama_data(json.loads(_json_slice(text)), max_stt)[0]


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
    if norm and _looks_like_qty(norm):
      out[stt] = norm
  return pdn, out


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


def _has_far_gap(mp: dict[int, str], max_gap: int = 8) -> bool:
  """Map có hai khối STT cách xa nhau (thường do đọc lẫn mặt sau tờ giấy)."""
  keys = sorted(mp)
  return any(b - a > max_gap for a, b in zip(keys, keys[1:]))



def _image_scores(mp: dict[int, str]) -> dict[int, int]:
  """Điểm tin cậy theo vị trí trong ảnh: STT càng xa mép trên/dưới càng cao.

  Dòng sát mép ảnh hay bị cắt → đọc sai. Dùng điểm này để chỉ cho phép ảnh
  sau sửa lại đúng những dòng mà ảnh trước đọc ở sát mép.
  """
  if not mp:
    return {}
  lo, hi = min(mp), max(mp)
  return {stt: min(stt - lo, hi - stt) for stt in mp}


def _commit_image_map(
  merged: dict[int, str],
  scores: dict[int, int],
  img_map: dict[int, str],
) -> tuple[int, int]:
  """Chốt kết quả 1 ảnh vào kết quả tổng; trả về (số STT mới, số dòng sửa mép).

  Ảnh đầu thắng: STT đã có thì giữ nguyên, ảnh sau chỉ bổ sung STT mới. Chỉ
  cho ảnh sau sửa lại khi dòng cũ được đọc ở sát mép ảnh trước (điểm <= 1) mà
  ảnh này thấy nó ở giữa ảnh (điểm >= 3) — đó là dòng bị cắt ở mép, không
  phải ảnh sau đọc đè số đúng.
  """
  img_scores = _image_scores(img_map)
  added = fixed = 0
  for stt, val in img_map.items():
    if stt not in merged:
      merged[stt] = val
      scores[stt] = img_scores[stt]
      added += 1
      continue
    if val == merged[stt]:
      continue
    old = merged[stt]
    # "X" là giá trị model rất hay bịa khi không thấy ô. Một ảnh khác đọc
    # được chữ số/tick thật thì đáng tin hơn → cho ghi đè.
    if old.upper() == "X" and val.upper() != "X" and (
      img_scores[stt] >= 1 or len(img_map) <= 3
    ):
      merged[stt] = val
      scores[stt] = img_scores[stt]
      fixed += 1
      continue
    if old.upper() != "X" and val.upper() == "X":
      continue  # không để X đè lên số đã đọc được
    if scores.get(stt, 0) <= 1 and img_scores[stt] >= 3:
      merged[stt] = val
      scores[stt] = img_scores[stt]
      fixed += 1
  return added, fixed


def _accept_image_map(new: dict[int, str], base: dict[int, str]) -> bool:
  """Ảnh này có đáng gộp vào kết quả tổng không.

  - Ảnh đầu: cần dãy STT liền mạch (tránh ảnh chụp phần cuối trang đọc loạn).
  - Ảnh sau: nhận nếu có ít nhất 1 STT trùng với kết quả đã có (chồng lấn),
    hoặc dãy STT liền mạch. Ảnh rời rạc và không trùng → bỏ.
  """
  if not new:
    return False
  if not base:
    return _maps_contiguous([new])
  if set(new) & set(base):
    return True
  return _maps_contiguous([new])


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
) -> tuple[dict[int, str], tuple[int, int] | None]:
  """Đọc 1 dải ảnh → (map STT, khoảng STT model khai). Retry khi JSON hỏng
  hoặc đọc được quá ít dòng."""
  import time


  last_exc: Exception | None = None
  parsed_ok = False
  best: dict[int, str] = {}
  best_range: tuple[int, int] | None = None
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
        keep_alive=cfg.get("keep_alive"),
      )
      part, rng = _parse_ollama_data(json.loads(_json_slice(text)), max_stt)
      parsed_ok = True
      if len(part) > len(best):
        best, best_range = part, rng
      if len(part) >= min_keys:
        return part, rng
    except CancelledByUser:
      raise
    except OllamaError as exc:
      last_exc = exc
      if attempt < EMPTY_CHUNK_RETRIES:
        time.sleep(2.0 * attempt)
        continue
      raise
    except Exception as exc:  # JSON hỏng, model trả rỗng...
      last_exc = exc
    if attempt < EMPTY_CHUNK_RETRIES:
      time.sleep(1.0 * attempt)
  if not parsed_ok and last_exc is not None:
    raise ValueError(f"AI trả JSON không đọc được cho {label}:\n{last_exc}")
  return best, best_range


def _stt_strip(data: bytes) -> bytes | None:
  """Cắt dải STT mép trái của ảnh và tăng tương phản để xóa chữ mờ mặt sau."""
  import cv2
  import numpy as np

  arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
  if arr is None:
    return None
  h, w = arr.shape[:2]
  strip = arr[:, : max(1, int(w * 0.13))]
  gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
  enh = np.clip((gray.astype(np.float32) - 90) * (255 / 110), 0, 255).astype(
    np.uint8
  )
  ok, buf = cv2.imencode(".jpg", enh, [cv2.IMWRITE_JPEG_QUALITY, 92])
  return buf.tobytes() if ok else None


def _read_visible_stt(
  band,
  max_stt: int,
  cfg: dict,
  on_progress,
  label: str,
  cancel_event: threading.Event | None,
) -> set[int]:
  """Đọc các STT in rõ ở mép trái ảnh (để loại STT đọc lẫn từ mặt sau)."""
  strip = _stt_strip(band.data)
  if not strip:
    return set()
  if on_progress:
    on_progress(f"Đang xác minh STT {label}")
  out: set[int] = set()
  for attempt in range(1, 3):
    try:
      text = chat_vision(
        STT_VISIBLE_PROMPT,
        [strip],
        model=cfg["model"],
        base_url=cfg["base_url"],
        timeout=cfg["timeout"],
        num_ctx=cfg["num_ctx"],
        cancel_event=cancel_event,
        keep_alive=cfg.get("keep_alive"),
      )
      data = json.loads(_json_slice(text))
    except Exception:
      data = None
    nums = data.get("stt") if isinstance(data, dict) else data
    out = set()
    for item in nums or []:
      stt = _stt_int(item)
      if stt is not None and 1 <= stt <= max_stt:
        out.add(stt)
    if out:
      break
  return out



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
    products = load_products(excel)
    # Danh sách tên xếp theo STT (dòng trống giữa bảng để chỗ trống).
    max_stt = max((p.stt for p in products), default=0)
    names = [""] * max_stt
    for p in products:
      if 1 <= p.stt <= max_stt:
        names[p.stt - 1] = _norm_name(p.name)
    orders.append((pdn, names))

  assigned: dict[str, list[Path]] = {}
  for i, img in enumerate(images, 1):
    check_cancel(cancel_event)
    prog(f"Đang nhận diện ảnh {i}/{len(images)}: {img.name}...")
    try:
      band = prepare_page(img, cfg["bands"])[0]
    except Exception as exc:  # ảnh hỏng → bỏ qua, các ảnh khác vẫn chạy
      prog(f"Cảnh báo: bỏ ảnh {img.name} — không đọc được file ảnh ({exc})")
      continue
    try:
      text = chat_vision(
        OLLAMA_ASSIGN_PROMPT,
        [band.data],
        model=cfg["model"],
        base_url=cfg["base_url"],
        timeout=cfg["timeout"],
        num_ctx=cfg["num_ctx"],
        cancel_event=cancel_event,
        keep_alive=cfg.get("keep_alive"),
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
  _products = load_products(excel)
  max_stt = max((p.stt for p in _products), default=0)
  merged: dict[int, str] = {}
  merged_scores: dict[int, int] = {}
  empty_images = 0
  skipped_images = 0
  for i, img in enumerate(images, 1):
    check_cancel(cancel_event)
    try:
      bands = prepare_page(img, cfg["bands"])
    except Exception as exc:  # ảnh hỏng/định dạng lạ → bỏ ảnh, không bỏ cả phiếu
      empty_images += 1
      prog(f"Cảnh báo: bỏ ảnh {img.name} — không đọc được file ảnh ({exc})")
      continue
    img_maps: list[dict[int, str]] = []
    for band in bands:
      prompt = OLLAMA_SINGLE_PROMPT.format(
        pdn=pdn, max_stt=max_stt, stt_hint=""
      )
      label = f"{pdn} — ảnh {i}/{len(images)}: {img.name} | {band.label}"
      part: dict[int, str] = {}
      part_range: tuple[int, int] | None = None
      try:
        part, part_range = _ollama_band_map(
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
        # Trang chỉ có đúng 1 dòng (stt_dau == stt_cuoi) thì thôi: đọc lại nửa
        # trang dễ "nhìn" sang mặt sau tờ giấy và bịa thêm dòng.
        single_row = bool(part_range and part_range[0] == part_range[1])
        if band.mode in ("page", "table") and len(part) < 5 and not single_row:
          sub_maps: list[dict[int, str]] = []
          for j, data in enumerate(_split_band(band), 1):
            sub, _ = _ollama_band_map(
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
          # Chỉ nhận kết quả nửa trang khi nó đọc cùng vùng với cả trang. Rời
          # rạc hoàn toàn nghĩa là model đọc sang vùng khác/mặt sau → bỏ.
          if len(alt) > len(part) and (not part or set(alt) & set(part)):
            prog(
              f"Đọc lại {band.label} theo nửa trang: {len(alt)} STT "
              f"(cả trang chỉ {len(part)})"
            )
            part = alt
        if part:
          img_maps.append(part)
      except Exception as exc:  # ảnh bị lỗi model (vd lặp token) → thử đọc theo nửa trang
        prog(f"Cảnh báo: dải {band.label} lỗi — {exc}")
      # Fallback: nếu cả trang lỗi/trống, thử đọc theo nửa trang.
      if (not part) and band.mode in ("page", "table"):
        sub_maps_fb: list[dict[int, str]] = []
        for j, data in enumerate(_split_band(band), 1):
          try:
            sub, _ = _ollama_band_map(
              prompt,
              SimpleNamespace(data=data, mode="band"),
              max_stt,
              cfg,
              on_progress,
              f"{label} — nửa {j}",
              cancel_event,
              min_keys=3,
            )
          except Exception as exc:
            prog(f"Cảnh báo: nửa {j} lỗi — {exc}")
            sub = {}
          if sub:
            sub_maps_fb.append(sub)
        alt = _merge_thuc_maps_weighted(sub_maps_fb)
        if alt:
          prog(f"Đọc lại {band.label} theo nửa trang (fallback): {len(alt)} STT")
          part = alt
          img_maps.append(part)
    if not img_maps:
      # Ảnh khó (trang gần trống, chỉ 1 dòng, lệch góc): thử đọc lại ảnh gốc
      # chưa làm phẳng một lần. Chỉ chạy khi các dải đều hỏng nên hiếm khi tốn.
      try:
        raw_band = split_image(img, 1)[0]
        part, _ = _ollama_band_map(
          prompt,
          raw_band,
          max_stt,
          cfg,
          on_progress,
          f"{pdn} — ảnh {i}/{len(images)}: {img.name} | ảnh gốc",
          cancel_event,
          min_keys=1,
        )
        if part:
          prog(f"Đọc lại {img.name} bằng ảnh gốc: {len(part)} STT")
          img_maps.append(part)
      except Exception as exc:
        prog(f"Cảnh báo: đọc lại ảnh gốc {img.name} lỗi — {exc}")
    if not img_maps:
      empty_images += 1
      prog(f"Cảnh báo: ảnh {img.name} không đọc được STT nào")
      continue
    # Chốt riêng từng ảnh: gộp các dải trong ảnh, rồi chỉ BỔ SUNG STT mới
    # vào kết quả tổng. Ảnh đầu thắng — ảnh sau không ghi đè số đã đọc đúng.
    img_map = _merge_thuc_maps_weighted(img_maps)
    # Model đôi khi đọc nhầm sang cột tên hàng. Tên hàng của đúng STT trong
    # Excel là căn cứ chắc chắn để loại các giá trị đó.
    names_by_stt = {p.stt: p.name for p in _products}
    before = len(img_map)
    img_map = {
      stt: val
      for stt, val in img_map.items()
      if not _is_product_name(val, names_by_stt.get(stt, ""))
    }
    if len(img_map) != before:
      prog(
        f"Bỏ {before - len(img_map)} dòng đọc nhầm cột tên hàng ({img.name})"
      )
    # Một ảnh chụp không thể chứa quá ~35 dòng. Nhiều hơn = model hallucinate STT
    # (điền tiếp tới max_stt) → bỏ ảnh, tránh đè số đúng của trang khác.
    if img_map and (
      len(img_map) > 35 or (max(img_map) - min(img_map)) > 35
    ):
      prog(
        f"Cảnh báo: bỏ ảnh {img.name} — đọc được {len(img_map)} STT "
        "(vượt quá số dòng một trang, nghi model điền thêm)"
      )
      continue
    # Ảnh bị cắt dải (mode "band") dễ đọc lẫn chữ mờ mặt sau; ảnh chỉ có ít dòng
    # mà model trả nhiều STT cũng vậy. Xác minh bằng cột STT in rõ ở mép trái.
    band_split = any(b.mode == "band" for b in bands)
    if _has_far_gap(img_map) or (band_split and len(img_map) <= 15):
      visible: set[int] = set()
      for band in bands:
        visible |= _read_visible_stt(
          band,
          max_stt,
          cfg,
          on_progress,
          f"{img.name} | {band.label}",
          cancel_event,
        )
      kept = {k: v for k, v in img_map.items() if k in visible} if visible else img_map
      if kept != img_map:
        prog(
          f"Xác minh STT {img.name}: bỏ {len(img_map) - len(kept)} dòng "
          "không thấy ở cột STT (chữ mờ mặt sau)"
        )
        img_map = kept
    if not _accept_image_map(img_map, merged):
      skipped_images += 1
      keys = sorted(img_map)
      prog(
        f"Cảnh báo: bỏ kết quả ảnh {img.name} — STT rời rạc "
        f"({', '.join(str(k) for k in keys[:8])}...)"
      )
      continue
    added, fixed = _commit_image_map(merged, merged_scores, img_map)
    prog(
      f"Ảnh {i}/{len(images)} xong: {img.name} — {len(img_map)} STT "
      f"(+{added} mới"
      + (f", sửa {fixed} dòng sát mép" if fixed else "")
      + f", tổng {len(merged)})"
    )

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
    f"(làm phẳng trang + cắt cột số lượng in)..."
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
