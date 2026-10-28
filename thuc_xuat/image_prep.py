# -*- coding: utf-8 -*-
"""Chuẩn bị ảnh cho model vision local.

gemma3:4b nhìn ảnh ở độ phân giải thấp (~896px), nên ảnh chụp cả trang A4
thì chữ viết tay rất nhỏ. Cách hiệu quả nhất (đã đo trên phiếu thật):
1. Tìm khung bảng trên ảnh, làm phẳng (perspective transform) về hình chữ nhật.
2. Che trắng cột "Số lượng" in sẵn — model 4b hay đọc nhầm cột này thay vì
   cột "Thực Xuất" viết tay.
3. Không tìm được bảng thì làm phẳng cả tờ giấy; vẫn không được thì cắt dải.
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
# Vị trí cột "Số lượng" in trên tờ giấy đã làm phẳng (đo trên mẫu phiếu thật).
PAGE_MASK_X = (0.655, 0.775)
PAGE_MASK_Y0 = 0.10

# Làm phẳng riêng khung bảng.
TABLE_WIDTH = 1400


@dataclass
class ImageBand:
  data: bytes
  index: int
  total: int
  width: int
  height: int
  mode: str = "band"  # "band" | "page" | "table"

  @property
  def label(self) -> str:
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


def _mask_printed_column(img):
  """Che cột "Số lượng" in sẵn trên ảnh bảng đã làm phẳng (đường kẻ thẳng đứng)."""
  return _mask_between_lines(img, _column_lines(img))


def _hough_column_lines(img) -> list[int]:
  """Đường kẻ dọc trên ảnh cả trang (hơi nghiêng) — dùng Hough thay vì chiếu cột."""
  import cv2
  import numpy as np

  h, _ = img.shape[:2]
  gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
  edges = cv2.Canny(gray, 50, 150)
  lines = cv2.HoughLinesP(
    edges,
    1,
    np.pi / 360,
    threshold=120,
    minLineLength=int(h * 0.18),
    maxLineGap=int(h * 0.02),
  )
  if lines is None:
    return []
  items: list[tuple[float, int, int]] = []
  for item in lines:
    seg = np.asarray(item).reshape(-1)
    x1, y1, x2, y2 = (int(v) for v in seg[:4])
    dy = y2 - y1
    dx = x2 - x1
    if abs(dy) < h * 0.15:
      continue
    if abs(dx) > abs(dy) * 0.18:
      continue
    items.append(((x1 + x2) / 2.0, min(y1, y2), max(y1, y2)))
  items.sort()
  clusters: list[tuple[float, int, int]] = []
  for xm, ylo, yhi in items:
    if clusters and abs(xm - clusters[-1][0]) <= 18:
      cx, lo, hi = clusters[-1]
      clusters[-1] = ((cx + xm) / 2.0, min(lo, ylo), max(hi, yhi))
    else:
      clusters.append((xm, ylo, yhi))
  # Nét chữ (dấu tick, nét bút) chỉ cao vài chục px; đường kẻ bảng chạy gần hết trang.
  clusters = [c for c in clusters if (c[2] - c[1]) >= h * 0.40]
  clusters.sort(key=lambda c: c[0])
  return [int(round(c[0])) for c in clusters]


def _mask_between_lines(img, lines: list[int]):
  """Che cột in ngay TRƯỚC cột viết tay.

  Cột "Số lượng" in là cột hẹp (2–10% bề rộng trang) nằm ở nửa phải, ngay
  trước cột "Thực xuất". Chọn khoảng hẹp nhất/trái nhất trong nửa phải để
  không phụ thuộc số cột của từng mẫu phiếu (có mẫu thêm cột ĐVT).
  """
  import cv2

  width = img.shape[1]
  pairs = [
    (left, right)
    for left, right in zip(lines, lines[1:])
    if left >= 0.55 * width and 0.02 * width <= right - left <= 0.10 * width
  ]
  if not pairs:
    return img, False
  left, right = pairs[0]
  out = img.copy()
  cv2.rectangle(out, (left, 0), (right, img.shape[0]), (255, 255, 255), -1)
  return out, True


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


def prepare_page(path: str | Path, bands: int = 0, allow_table: bool = True) -> list[ImageBand]:
  """Ảnh 1 trang → 1 ảnh đã làm phẳng + che cột số lượng in.

  Thứ tự thử: khung bảng (chính xác nhất) → cả tờ giấy (che theo tỉ lệ) →
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
      masked, did_mask = _mask_printed_column(table)
      if did_mask:
        return [_band_from_bgr(masked, "table")]

  # 2) Cả tờ giấy: che cột số lượng in (dò đường kẻ dọc, không dùng tỉ lệ cố định).
  quad = _find_quad(bgr, 0.18, 0.98, 0.3, 3.5)
  if quad is not None:
    page = _warp(bgr, quad, PAGE_WIDTH, PAGE_HEIGHT)
    masked, did_mask = _mask_between_lines(page, _hough_column_lines(page))
    if not did_mask:
      # Không dò được đường kẻ → che theo tỉ lệ đã đo trên mẫu phiếu.
      left = int(PAGE_WIDTH * PAGE_MASK_X[0])
      right = int(PAGE_WIDTH * PAGE_MASK_X[1])
      top = int(PAGE_HEIGHT * PAGE_MASK_Y0)
      masked = page.copy()
      cv2.rectangle(masked, (left, top), (right, PAGE_HEIGHT), (255, 255, 255), -1)
    return [_band_from_bgr(masked, "page")]

  # 3) Không thấy giấy/bảng → cắt dải như cũ.
  return split_image(path, bands)

