# -*- coding: utf-8 -*-
"""Chuẩn bị ảnh cho model NVIDIA NIM.

Làm phẳng bảng hoặc cả tờ giấy để model dễ đọc chữ viết tay.
Che cột Số lượng và Ghi chú khi nhận ra bố cục, rồi cắt dải khi cần.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

# Mỗi dải cao tối đa ~900px gốc: đủ để chữ số còn rõ sau khi model resize.
BAND_HEIGHT = 900
MAX_BANDS = 4
MIN_WIDTH = 1400
MAX_WIDTH = 2000
JPEG_QUALITY = 92
OVERLAP_RATIO = 0.10  # chồng lấn mỗi bên, tính theo chiều cao 1 dải

# Làm phẳng cả tờ giấy: A4 dọc.
PAGE_WIDTH = 1240
PAGE_HEIGHT = 1754
# Làm phẳng riêng khung bảng.
TABLE_WIDTH = 1400


@dataclass
class ImageBand:
  data: bytes
  index: int
  total: int
  width: int
  height: int
  mode: str = "band"  # "band" | "page" | "table" | "short_table"

  @property
  def label(self) -> str:
    if self.mode == "short_table":
      return "bảng ngắn"
    if self.mode == "table":
      return "cả bảng"
    if self.mode == "page":
      return "cả trang"
    return f"dải {self.index}/{self.total}"


def plan_band_count(size: tuple[int, int], bands: int = 0) -> int:
  """bands=0 → tự chia theo chiều cao; 1..4 → cố định."""
  if bands and bands > 0:
    return max(1, min(MAX_BANDS, int(bands)))
  width, height = size
  if height <= BAND_HEIGHT:
    return 1
  # Ảnh càng cao càng nhiều dải, nhưng không quá MAX_BANDS.
  return max(1, min(MAX_BANDS, math.ceil(height / BAND_HEIGHT)))


def _load(path: str | Path) -> Image.Image:
  with Image.open(path) as raw:
    img = ImageOps.exif_transpose(raw).convert("RGB")
  return img


def _resize_for_model(img: Image.Image) -> Image.Image:
  width, height = img.size
  if width < MIN_WIDTH:
    scale = MIN_WIDTH / width
    return img.resize((MIN_WIDTH, max(1, round(height * scale))), Image.LANCZOS)
  if width > MAX_WIDTH:
    scale = MAX_WIDTH / width
    return img.resize((MAX_WIDTH, max(1, round(height * scale))), Image.LANCZOS)
  return img


def _encode(img: Image.Image) -> bytes:
  buf = io.BytesIO()
  img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
  return buf.getvalue()


def split_image(path: str | Path, bands: int = 0) -> list[ImageBand]:
  """Cắt ảnh thành các dải ngang (có overlap) và nén JPEG."""
  img = _load(path)
  width, height = img.size
  count = plan_band_count(img.size, bands)
  if count == 1:
    out = _resize_for_model(img)
    return [
      ImageBand(
        data=_encode(out), index=1, total=1, width=out.width, height=out.height
      )
    ]

  core = height / count
  pad = core * OVERLAP_RATIO
  result: list[ImageBand] = []
  for i in range(count):
    top = max(0, int(i * core - pad))
    bottom = min(height, int((i + 1) * core + pad))
    crop = img.crop((0, top, width, bottom))
    crop = _resize_for_model(crop)
    result.append(
      ImageBand(
        data=_encode(crop),
        index=i + 1,
        total=count,
        width=crop.width,
        height=crop.height,
      )
    )
  return result


def _order_points(pts):
  """4 điểm → thứ tự trên-trái, trên-phải, dưới-phải, dưới-trái."""
  import numpy as np

  pts = np.array(pts, dtype=np.float32).reshape(4, 2)
  s = pts.sum(axis=1)
  d = np.diff(pts, axis=1).ravel()
  return np.array(
    [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
    dtype=np.float32,
  )


def _find_quad(img, min_ratio: float, max_ratio: float, min_aspect=0.5, max_aspect=6.0):
  """Tìm contour 4 cạnh có diện tích trong khoảng [min_ratio, max_ratio]."""
  import cv2
  import numpy as np

  h, w = img.shape[:2]
  gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
  blur = cv2.GaussianBlur(gray, (5, 5), 0)
  edges = cv2.Canny(blur, 30, 90)
  edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
  contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

  best = None
  best_area = 0.0
  for c in contours:
    area = cv2.contourArea(c)
    ratio = area / float(h * w)
    if ratio < min_ratio or ratio > max_ratio:
      continue
    peri = cv2.arcLength(c, True)
    approx = cv2.approxPolyDP(c, 0.02 * peri, True)
    if len(approx) != 4:
      continue
    _, _, bw, bh = cv2.boundingRect(approx)
    if not bw or not bh:
      continue
    aspect = bw / float(bh)
    if aspect < min_aspect or aspect > max_aspect:
      continue
    if area > best_area:
      best, best_area = approx, area
  return best


def _warp(img, quad, width: int, height: int):
  import cv2
  import numpy as np

  pts = _order_points(quad.reshape(4, 2))
  dst = np.array(
    [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
    dtype=np.float32,
  )
  matrix = cv2.getPerspectiveTransform(pts, dst)
  return cv2.warpPerspective(img, matrix, (width, height), flags=cv2.INTER_CUBIC)


def _column_lines(img) -> list[int]:
  """Vị trí các đường kẻ dọc của bảng (ảnh đã làm phẳng, đường gần thẳng đứng)."""
  import cv2

  gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
  bw = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY_INV)[1]
  colsum = (bw > 0).sum(axis=0)
  h = gray.shape[0]
  xs: list[int] = []
  for x, value in enumerate(colsum):
    if value > h * 0.55:
      if not xs or x - xs[-1] > 20:
        xs.append(x)
      elif value > colsum[xs[-1]]:
        xs[-1] = x
  return xs


def _mask_ocr_columns(img):
  """Che Số lượng đặt và Ghi chú nếu nhận ra đủ đường kẻ của mẫu phiếu."""
  width = img.shape[1]
  lines = _column_lines(img)
  if len(lines) < 6:
    return img
  ordered_left, shipped_left, note_left, table_right = lines[-4:]
  # Phải thấy cả ô STT hẹp và vùng tên hàng ở trái; viền giấy/đường chữ
  # ở nửa phải riêng lẻ không đủ để suy ra vị trí Ghi chú.
  has_stt = any(
    left <= 0.18 * width and 0.02 * width <= right - left <= 0.13 * width
    for left, right in zip(lines[:-4], lines[1:-4])
  )
  if not (
    has_stt
    and ordered_left >= 0.48 * width
    and table_right >= 0.88 * width
    and 0.025 * width <= shipped_left - ordered_left <= 0.14 * width
    and 0.035 * width <= note_left - shipped_left <= 0.24 * width
    and 0.025 * width <= table_right - note_left <= 0.20 * width
  ):
    # Giữ toàn bộ ảnh khi bố cục không rõ để tránh che nhầm Thực xuất.
    return img
  out = img.copy()
  out[:, ordered_left + 3:shipped_left - 2] = 255
  out[:, note_left + 3:table_right - 2] = 255
  return out


def _band_from_bgr(img, mode: str) -> ImageBand:
  import cv2

  ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
  if not ok:
    raise ValueError("Không nén được ảnh sau khi làm phẳng")
  height, width = img.shape[:2]
  return ImageBand(data=buf.tobytes(), index=1, total=1, width=width, height=height, mode=mode)


def warp_available() -> bool:
  """OpenCV + numpy có sẵn? (bản .exe tự build cần cv2 để làm phẳng/che cột)"""
  try:
    import cv2  # noqa: F401
    import numpy  # noqa: F401
  except ImportError:
    return False
  return True


def _short_table(image) -> ImageBand | None:
  """Nhận bảng ngắn gần đầu ảnh nếu nhìn thấy đúng các vạch chia năm cột."""
  import cv2
  import numpy as np

  region = image[:int(image.shape[0] * 0.57)]
  if region.shape[0] < 40 or region.shape[1] < 200:
    return None
  quad = _find_quad(region, 0.007, 0.12, 6, 26)
  if quad is None:
    return None
  table = _warp(region, quad, TABLE_WIDTH, 110)
  gray = cv2.cvtColor(table, cv2.COLOR_BGR2GRAY)
  dark = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 3)
  rules = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((50, 1), np.uint8))
  xs = np.flatnonzero(rules.max(axis=0))
  clusters = np.split(xs, np.where(np.diff(xs) > 7)[0] + 1)
  inner = [int(np.median(c)) for c in clusters if c.size and 12 < np.median(c) < TABLE_WIDTH - 12]
  if len(inner) != 4:
    return None
  stt_right, ordered_left, shipped_left, note_left = inner
  if not (
    0.025 * TABLE_WIDTH < stt_right < 0.14 * TABLE_WIDTH
    and 0.49 * TABLE_WIDTH < ordered_left < 0.78 * TABLE_WIDTH
    and 0.035 * TABLE_WIDTH < shipped_left - ordered_left < 0.15 * TABLE_WIDTH
    and 0.045 * TABLE_WIDTH < note_left - shipped_left < 0.22 * TABLE_WIDTH
    and 0.025 * TABLE_WIDTH < TABLE_WIDTH - note_left < 0.18 * TABLE_WIDTH
  ):
    return None
  table[:, ordered_left + 3:shipped_left - 2] = 255
  table[:, note_left + 3:TABLE_WIDTH - 3] = 255
  return _band_from_bgr(table, "short_table")


def prepare_assignment_focus(path: str | Path) -> ImageBand:
  """Đọc cận phần có mã phiếu/dòng đầu; ưu tiên crop bảng ngắn đã xác minh."""
  image = _load(path)
  if warp_available():
    import numpy as np

    short = _short_table(np.array(image)[:, :, ::-1].copy())
    if short is not None:
      return short
  width, height = image.size
  top = _resize_for_model(image.crop((0, 0, width, max(1, round(height * 0.54)))))
  return ImageBand(data=_encode(top), index=1, total=1, width=top.width, height=top.height)


def prepare_page(path: str | Path, bands: int = 0, allow_table: bool = True) -> list[ImageBand]:
  """Ảnh 1 trang → ảnh đã làm phẳng, che cột đặt/ghi chú khi dò rõ bố cục.

  Thứ tự thử: khung bảng (chính xác nhất) → cả tờ giấy →
  cắt dải như cũ (khi không thấy cả bảng lẫn tờ giấy).

  allow_table=False: không cắt riêng khung bảng (giữ header/PDN cho ảnh nhiều phiếu).
  """
  if not warp_available():
    return split_image(path, bands)
  import cv2
  import numpy as np

  img = _load(path)
  bgr = np.array(img)[:, :, ::-1].copy()
  h, w = bgr.shape[:2]

  # 1) Khung bảng: diện tích 5–45% ảnh, dạng chữ nhật rộng.
  if allow_table:
    quad = _find_quad(bgr, 0.05, 0.45, 0.5, 6.0)
    if quad is not None:
      _, _, bw, bh = cv2.boundingRect(quad)
      table = _warp(bgr, quad, TABLE_WIDTH, max(200, int(TABLE_WIDTH * bh / bw)))
      return [_band_from_bgr(_mask_ocr_columns(table), "table")]

    short = _short_table(bgr)
    if short is not None:
      return [short]

  # 2) Cả tờ giấy: chỉ che nếu dò được đủ đường kẻ của mẫu.
  quad = _find_quad(bgr, 0.18, 0.98, 0.3, 3.5)
  if quad is not None:
    page = _warp(bgr, quad, PAGE_WIDTH, PAGE_HEIGHT)
    return [_band_from_bgr(_mask_ocr_columns(page), "page")]

  # 3) Không thấy giấy/bảng → cắt dải như cũ.
  return split_image(path, bands)

