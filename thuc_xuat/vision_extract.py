# -*- coding: utf-8 -*-
"""Đọc ảnh phiếu xuất → map STT -> thực xuất."""
from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from thuc_xuat.cancel import CancelledByUser, check_cancel
from thuc_xuat.config import image_band_count, load_config
from thuc_xuat.image_prep import prepare_assignment_focus, prepare_page, split_image
from thuc_xuat.nim_client import DEFAULT_MODEL, NimError, NimVision
from thuc_xuat.processor import TICK_MARKS


def _file_revision(path: Path) -> tuple[Path, int, int] | None:
  try:
    stat = path.stat()
  except OSError:
    return None
  return path.resolve(), stat.st_size, stat.st_mtime_ns


@dataclass
class VisionResume:
  """Giữ kết quả OCR theo ảnh trong phiên để có thể chạy tiếp lô lỗi."""

  _scope: tuple = field(default=(), init=False)
  _revisions: dict = field(default_factory=dict, init=False)
  assigned: dict[Path, str] = field(default_factory=dict, init=False)
  pages: dict[Path, tuple[str, dict[int, str]]] = field(default_factory=dict, init=False)

  def prepare(self, excels: list[Path], images: list[Path], model: str) -> None:
    scope = (tuple(_file_revision(excel) for excel in sorted(excels)), model)
    revisions = {image: _file_revision(image) for image in images}
    if scope != self._scope:
      self.assigned.clear()
      self.pages.clear()
    else:
      for image, revision in self._revisions.items():
        if revision is None or revisions.get(image) != revision:
          self.assigned.pop(image, None)
          self.pages.pop(image, None)
    self._scope, self._revisions = scope, revisions

  def pending(self, images: list[Path]) -> list[Path]:
    return [image for image in images if image not in self.pages]

# ---------------------------------------------------------------------------
# Nhấn mạnh cột viết tay để model không chép nhầm cột Đặt.
# ---------------------------------------------------------------------------
OCR_PROMPT = """Bạn là OCR phiếu xuất kho. Ảnh là bảng in; cột Số lượng đặt và Ghi chú có thể đã được che.

Bảng còn các cột, từ trái sang phải:
- STT: số in nhỏ ở mép trái.
- Tên hàng: chữ IN dài (tiếng Việt và/hoặc tiếng Nhật). TUYỆT ĐỐI KHÔNG chép cột này.
- Số lượng đặt: cột số IN trước Thực xuất, có thể vẫn nhìn thấy. Bỏ qua.
- THỰC XUẤT: cột VIẾT TAY, mỗi ô rất ngắn (1–6 ký tự): số như 10, 5, 110, 5,5; hoặc 1T, 10H; hoặc dấu ✓; hoặc X.
- Ghi chú: cột bên phải Thực xuất. Nội dung của cột này không liên quan.

Quy tắc:
- Chỉ đọc cột THỰC XUẤT viết tay. KHÔNG đọc tên hàng, KHÔNG đọc cột ghi chú.
- BỎ QUA hoàn toàn Ghi chú dù có số, chữ, phép tính, X hay dấu tick. Nếu ô Thực xuất trống, bỏ STT đó kể cả khi ô Ghi chú cùng dòng có nội dung.
- BƯỚC 1: xác định STT dòng ĐẦU và dòng CUỐI nhìn thấy. BƯỚC 2: chỉ đọc các dòng trong khoảng đó.
- CHỈ ghi các STT CÓ TRONG ẢNH, không ghi tiếp tới {max_stt}.
- Cột này không dùng dấu tick. Nét giống ✓, ✔, V, Y hay gạch chéo đều là dấu bỏ dòng: ghi "X".
  Ô trống thực sự → bỏ STT đó.
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

ASSIGN_PROMPT = """Ảnh là một trang bảng phiếu xuất kho Việt Nam.

1. Chép mã phiếu PDN nếu nhìn rõ trên đầu trang. Không thấy thì trả null.
2. Chép tối đa 5 cặp STT và Tên hàng in sẵn, lấy ở đầu, giữa và cuối phần bảng nhìn rõ.
3. Giữ tên đúng dòng có STT ở mép trái. Không chép cột Ghi chú, Thực xuất, ĐVT hay Số lượng. Không đoán nội dung bị khuất.
4. Trang cuối có thể chỉ có một dòng ngay trên phần tổng cộng/chữ ký. Nếu thấy, vẫn chép đúng cặp STT và tên của dòng đó; bỏ qua các tổng số và watermark.

Trả JSON thuần (không markdown):
{"pdn": null, "dong": [{"stt": 19, "ten": "tên hàng ở dòng 19"}, {"stt": 27, "ten": "tên hàng ở dòng 27"}]}"""

ASSIGN_FOCUS_PROMPT = ASSIGN_PROMPT + """
Ảnh này là phần cận cảnh. Ưu tiên dòng «Mã phiếu» in ngay dưới tiêu đề nếu có,
và đọc ít nhất một STT cùng tên hàng, kể cả khi chỉ có một hàng trong ảnh."""

# Model có thể ghép số in ở cột Đặt với ký hiệu bỏ dòng ở cột Thực xuất.
_OCR_SKIP_MARKS = "xXyY" + "".join(sorted(TICK_MARKS))
_X_ONLY_RE = re.compile(rf"^(?:\d+(?:[.,]\d+)?\s*)?[{re.escape(_OCR_SKIP_MARKS)}]$")


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


def _normalize_ocr_value(value: str) -> str:
  """Chuẩn hóa ký hiệu model đọc từ ảnh.

  Model có thể gộp số in ở cột «Đặt» với chữ X viết tay ("5 X", "10x")
  → quy về "X" để đúng quy tắc: có X là bỏ dòng. Nét gạch chéo bị đọc thành
  Y cũng quy về "X" (trên phiếu không có chữ Y).
  """
  s = str(value or "").replace("\ufe0e", "").replace("\ufe0f", "").strip().strip('"').strip("'").strip()
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


def _parse_ocr_data(
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
    norm = _normalize_ocr_value(value)
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


def _parse_ocr_response(text: str, max_stt: int) -> dict[int, str]:
  return _parse_ocr_data(json.loads(_json_slice(text)), max_stt)[0]


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


def _read_band(
  prompt: str,
  image: bytes,
  max_stt: int,
  nim: NimVision,
  on_progress,
  label: str,
  cancel_event: threading.Event | None,
) -> tuple[dict[int, str], tuple[int, int] | None]:
  """Đọc một dải; trang thiếu dữ liệu sẽ được xử lý ở bước chia ảnh."""
  check_cancel(cancel_event)
  if on_progress:
    on_progress(f"Đang đọc {label}")
  text = nim.read(prompt, [image], cancel_event, on_progress)
  try:
    part, visible_range = _parse_ocr_data(json.loads(_json_slice(text)), max_stt)
  except ValueError as exc:
    raise ValueError(f"AI trả JSON không đọc được cho {label}:\n{exc}") from exc
  return part, visible_range if part else None


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
  nim: NimVision,
  on_progress,
  label: str,
  cancel_event: threading.Event | None,
) -> set[int]:
  """Đọc STT in ở mép trái, lọc chữ mờ từ mặt sau."""
  strip = _stt_strip(band.data)
  if not strip:
    return set()
  if on_progress:
    on_progress(f"Đang xác minh STT {label}")
  text = nim.read(STT_VISIBLE_PROMPT, [strip], cancel_event, on_progress)
  try:
    data = json.loads(_json_slice(text))
  except ValueError:
    return set()
  nums = data.get("stt") if isinstance(data, dict) else data
  if not isinstance(nums, list):
    return set()
  return {stt for item in nums if (stt := _stt_int(item)) is not None and 1 <= stt <= max_stt}


def _norm_name(text: str) -> str:
  """Bỏ dấu, viết thường, bỏ ký tự lạ — để so tên hàng model đọc với Excel."""
  import unicodedata

  s = unicodedata.normalize("NFD", str(text or "").lower())
  s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
  s = "".join(ch if ch.isalnum() else " " for ch in s).replace("đ", "d")
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


def _match_order_from_page(data: dict, orders: dict[str, list[str]]) -> str | None:
  """Đối chiếu PDN hoặc tên tại STT OCR đọc được; tránh ép chọn khi hai phiếu giống nhau."""
  from difflib import SequenceMatcher
  from thuc_xuat.image_match import extract_pdn

  printed_pdn = extract_pdn(re.sub(r"(?<=PDN)[\s:-]+(?=\d)", "", str(data.get("pdn") or ""), flags=re.I))
  known_pdn = printed_pdn if printed_pdn in orders else None

  rows: dict[int, str] = {}
  raw_names = data.get("ten")
  if isinstance(raw_names, str):
    raw_names = [raw_names]
  names = [name for name in raw_names if isinstance(name, str)] if isinstance(raw_names, list) else []
  raw_rows = data.get("dong")
  if isinstance(raw_rows, list):
    for row in raw_rows[:5]:
      if not isinstance(row, dict):
        continue
      stt = _stt_int(row.get("stt"))
      name = row.get("ten")
      if stt and isinstance(name, str) and _norm_name(name):
        rows.setdefault(stt, name)
  elif names and (first := _stt_int(data.get("stt_dau"))):
    rows = {first + offset: name for offset, name in enumerate(names[:5]) if _norm_name(name)}

  def similarity(name: str, expected: str) -> float:
    full = _norm_name(name)
    score = SequenceMatcher(None, full, expected).ratio()
    # Excel có thể kèm tên Nhật sau dấu |, còn ảnh chỉ thấy phần tiếng Việt.
    if len(full) >= 7 and f" {full} " in f" {expected} ":
      return max(score, 0.90)
    return score

  if rows:
    anchored = []
    for pdn, products in orders.items():
      scores = [
        similarity(name, products[stt - 1]) if stt <= len(products) else 0.0
        for stt, name in rows.items()
      ]
      anchored.append((sum(scores) / len(scores), max(scores), pdn))
    anchored.sort(reverse=True)
    score, strong, pdn = anchored[0]
    runner_up = anchored[1][0] if len(anchored) > 1 else 0.0
    if score >= 0.62 and strong >= 0.82 and score - runner_up >= 0.12:
      if known_pdn and known_pdn != pdn and (len(rows) < 2 or score < 0.78):
        return known_pdn
      return pdn
    if known_pdn:
      return known_pdn
    # Đừng để dò theo tên ở STT khác lấn át bằng chứng rõ ràng nhưng không phân biệt được.
    if score >= 0.62:
      return None
    # Dạng mới lấy tên ở các dòng không liên tiếp, không dùng dò chuỗi tên.
    if isinstance(raw_rows, list):
      return None

  if known_pdn:
    return known_pdn
  if names:
    ranked = sorted(
      ((_best_names_match(names, products)[0], pdn) for pdn, products in orders.items()),
      reverse=True,
    )
    score, pdn = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if score >= (0.84 if len(names) == 1 else 0.64) and score - runner_up >= 0.13:
      return pdn
  return None


def _assign_pages_to_orders(
  images: list[Path],
  excel_paths: list[Path],
  nim: NimVision,
  prog: Callable[[str], None],
  cancel_event: threading.Event | None,
  on_issue: Callable[[str], None] | None = None,
  resume: VisionResume | None = None,
) -> dict[str, list[Path]]:
  from thuc_xuat.image_match import extract_pdn, pdn_from_excel
  from thuc_xuat.processor import load_products

  orders: dict[str, list[str]] = {}
  for excel in excel_paths:
    pdn = (pdn_from_excel(excel) or excel.stem).upper()
    products = load_products(excel)
    names = [""] * max((p.stt for p in products), default=0)
    for product in products:
      names[product.stt - 1] = _norm_name(product.name)
    orders[pdn] = names

  assigned: dict[str, list[Path]] = {}
  for index, image in enumerate(images, 1):
    check_cancel(cancel_event)
    cached = resume.assigned.get(image) if resume is not None else None
    if cached in orders:
      assigned.setdefault(cached, []).append(image)
      continue
    prog(f"Đang nhận diện ảnh {index}/{len(images)}: {image.name}…")
    owner = extract_pdn(image.name)
    if owner not in orders:
      owner = None
      # Lần hai phóng vùng đầu và bảng ngắn, không để phần giấy trống lấn át dòng cần đọc.
      for focused in (False, True):
        try:
          band = prepare_assignment_focus(image) if focused else split_image(image, 1)[0]
        except (OSError, ValueError, IndexError):
          continue
        prompt = ASSIGN_FOCUS_PROMPT if focused else ASSIGN_PROMPT
        text = nim.read(prompt, [band.data], cancel_event, prog)
        try:
          data = json.loads(_json_slice(text))
        except ValueError:
          continue
        if not isinstance(data, dict):
          continue
        owner = _match_order_from_page(data, orders)
        if owner:
          break
    if owner:
      assigned.setdefault(owner, []).append(image)
      if resume is not None:
        resume.assigned[image] = owner
      prog(f"Ảnh {index} → {owner}")
    else:
      issue = f"Chưa xác định được phiếu cho ảnh {image.name}."
      prog(issue)
      if on_issue:
        on_issue(issue)
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


def _extract_one_order(
  excel: Path,
  images: list[Path],
  nim: NimVision,
  band_count: int,
  prog: Callable[[str], None],
  on_progress: Callable[[str], None] | None,
  cancel_event: threading.Event | None,
  on_issue: Callable[[str], None] | None = None,
  resume: VisionResume | None = None,
) -> dict[int, str]:
  """Đọc 1 phiếu từ các ảnh đã biết thuộc phiếu đó."""

  from thuc_xuat.image_match import pdn_from_excel
  from thuc_xuat.processor import load_products

  pdn = (pdn_from_excel(excel) or excel.stem).upper()
  _products = load_products(excel)
  max_stt = max((p.stt for p in _products), default=0)
  merged: dict[int, str] = {}
  merged_scores: dict[int, int] = {}
  empty_images = 0
  skipped_images = 0
  prompt = OCR_PROMPT.format(pdn=pdn, max_stt=max_stt, stt_hint="")
  for i, img in enumerate(images, 1):
    check_cancel(cancel_event)
    cached = resume.pages.get(img) if resume is not None else None
    if cached is not None and cached[0] == pdn:
      _commit_image_map(merged, merged_scores, cached[1])
      prog(f"{pdn}: dùng lại kết quả ảnh {img.name}.")
      continue
    try:
      bands = prepare_page(img, band_count)
    except Exception as exc:  # ảnh hỏng/định dạng lạ → bỏ ảnh, không bỏ cả phiếu
      empty_images += 1
      issue = f"{pdn}: không mở được ảnh {img.name} ({exc})."
      prog(issue)
      if on_issue:
        on_issue(issue)
      continue
    img_maps: list[dict[int, str]] = []
    for band in bands:
      band_prompt = prompt
      if band.mode == "short_table":
        band_prompt += "\nẢnh đã cắt sát bảng ngắn. Có thể chỉ có 1 dòng; dùng STT in ở ô ngoài cùng bên trái."
      label = f"{pdn} — ảnh {i}/{len(images)}: {img.name} | {band.label}"
      part: dict[int, str] = {}
      part_range: tuple[int, int] | None = None
      try:
        part, part_range = _read_band(
          band_prompt,
          band.data,
          max_stt,
          nim,
          on_progress,
          label,
          cancel_event,
        )
        # Cả trang mà vẫn đọc được < 5 dòng → model bị "ngợp" ảnh dài.
        # Đọc lại bằng 2 nửa trang chồng lấn rồi gộp.
        # Trang chỉ có đúng 1 dòng (stt_dau == stt_cuoi) thì thôi: đọc lại nửa
        # trang dễ "nhìn" sang mặt sau tờ giấy và bịa thêm dòng.
        single_row = bool(part_range and part_range[0] == part_range[1])
        min_page_keys = 5
        if part_range:
          min_page_keys = min(min_page_keys, part_range[1] - part_range[0] + 1)
        if band.mode in ("page", "table") and 0 < len(part) < min_page_keys and not single_row:
          sub_maps: list[dict[int, str]] = []
          for j, data in enumerate(_split_band(band), 1):
            try:
              sub, _ = _read_band(
                prompt, data, max_stt, nim,
                on_progress, f"{label} — nửa {j}", cancel_event,
              )
            except (CancelledByUser, NimError):
              raise
            except ValueError:
              continue
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
      except (CancelledByUser, NimError):
        raise
      except Exception as exc:  # ảnh bị lỗi model (vd lặp token) → thử đọc theo nửa trang
        prog(f"Cảnh báo: dải {band.label} lỗi — {exc}")
      # Fallback: nếu cả trang lỗi/trống, thử đọc theo nửa trang.
      if (not part) and band.mode in ("page", "table"):
        sub_maps_fb: list[dict[int, str]] = []
        for j, data in enumerate(_split_band(band), 1):
          try:
            sub, _ = _read_band(
              prompt,
              data,
              max_stt,
              nim,
              on_progress,
              f"{label} — nửa {j}",
              cancel_event,
            )
          except (CancelledByUser, NimError):
            raise
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
        part, _ = _read_band(
          prompt,
          raw_band.data,
          max_stt,
          nim,
          on_progress,
          f"{pdn} — ảnh {i}/{len(images)}: {img.name} | ảnh gốc",
          cancel_event,
        )
        if part:
          prog(f"Đọc lại {img.name} bằng ảnh gốc: {len(part)} STT")
          img_maps.append(part)
      except (CancelledByUser, NimError):
        raise
      except Exception as exc:
        prog(f"Cảnh báo: đọc lại ảnh gốc {img.name} lỗi — {exc}")
    if not img_maps:
      empty_images += 1
      issue = f"{pdn}: không đọc được STT/Thực xuất trên ảnh {img.name}."
      prog(issue)
      if on_issue:
        on_issue(issue)
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
      if on_issue:
        on_issue(f"{pdn}: ảnh {img.name} có STT vượt phạm vi một trang.")
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
          nim,
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
      if on_issue:
        on_issue(f"{pdn}: ảnh {img.name} có kết quả STT thiếu nhất quán.")
      continue
    added, fixed = _commit_image_map(merged, merged_scores, img_map)
    if resume is not None and len(img_maps) >= len(bands):
      resume.pages[img] = (pdn, img_map)
    elif resume is not None and on_issue:
      on_issue(f"{pdn}: ảnh {img.name} còn dải chưa đọc được.")
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


def _extract_orders(
  excel_paths: list[Path],
  image_paths: list[Path],
  on_progress: Callable[[str], None] | None = None,
  cancel_event: threading.Event | None = None,
  nim: NimVision | None = None,
  images_by_excel: dict[Path, list[Path]] | None = None,
  on_issue: Callable[[str], None] | None = None,
  resume: VisionResume | None = None,
) -> dict[str, dict[int, str]]:
  from thuc_xuat.image_match import pdn_from_excel

  def prog(msg: str) -> None:
    if on_progress:
      on_progress(msg)

  nim = nim if nim is not None else NimVision(load_config().get("nim_model") or DEFAULT_MODEL)
  band_count = image_band_count()
  ordered = _sort_images(image_paths)
  if resume is not None:
    resume.prepare(excel_paths, ordered, nim.model)
    reused = len(ordered) - len(resume.pending(ordered))
    if reused:
      prog(f"Dùng lại {reused} ảnh đã đọc, xử lý {len(ordered) - reused} ảnh còn lại…")
  prog(
    f"NVIDIA NIM {nim.model}: {len(ordered)} ảnh, {len(excel_paths)} phiếu "
    "(làm phẳng trang, bỏ cột Ghi chú)…"
  )

  def order_id(excel: Path) -> str:
    return (pdn_from_excel(excel) or excel.stem).upper()

  if images_by_excel is not None:
    assignments = {order_id(excel): _sort_images(images_by_excel[excel]) for excel in excel_paths}
    prog("Đọc ảnh theo từng phiếu đã gán.")
  elif len(excel_paths) == 1:
    assignments = {order_id(excel_paths[0]): ordered}
  else:
    assignments = _assign_pages_to_orders(ordered, excel_paths, nim, prog, cancel_event, on_issue, resume)
  if not assignments:
    examples = ", ".join(photo.name for photo in ordered[:5])
    raise ValueError(
      f"Không xác định được phiếu cho ảnh: {examples}.\n\n"
      "Kiểm tra đầu trang hoặc các cột STT và Tên hàng có nhìn rõ không."
    )

  result: dict[str, dict[int, str]] = {}
  for excel in excel_paths:
    pdn = order_id(excel)
    images = assignments.get(pdn) or []
    if not images:
      prog(f"Cảnh báo: không có ảnh nào khớp {pdn}")
      continue
    result[pdn] = _extract_one_order(
      excel, images, nim, band_count, prog, on_progress, cancel_event, on_issue, resume
    )

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
  *,
  nim: NimVision | None = None,
  images_by_excel: dict[Path, list[Path]] | None = None,
  on_issue: Callable[[str], None] | None = None,
  resume: VisionResume | None = None,
) -> dict[str, dict[int, str]]:
  imgs = [Path(p) for p in image_paths]
  excels = [Path(p) for p in excel_paths]
  for p in imgs:
    if resume is None and not p.exists():
      raise FileNotFoundError(f"Không tìm thấy ảnh: {p}")
  if images_by_excel is not None:
    if set(images_by_excel) != set(excels) or any(not images_by_excel[p] for p in excels):
      raise ValueError("Mỗi Excel phải có ít nhất một ảnh đã gán.")
    grouped = [photo.resolve() for pages in images_by_excel.values() for photo in pages]
    if len(grouped) != len(set(grouped)) or set(grouped) != {photo.resolve() for photo in imgs}:
      raise ValueError("Mỗi ảnh cần được gán đúng một lần cho một phiếu trong lô.")
  return _extract_orders(excels, imgs, on_progress, cancel_event, nim, images_by_excel, on_issue, resume)
