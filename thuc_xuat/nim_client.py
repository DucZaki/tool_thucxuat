"""Đọc ảnh qua endpoint NVIDIA NIM được lưu trữ trên NVIDIA Build."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import requests

from thuc_xuat.cancel import check_cancel

CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
AUTO_MODEL = "nim-auto"
SUPER_VL = "nvidia/nemotron-3.5-super-vl-120b-a12b"
NANO_OMNI = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
MODELS = {
  "Tự động · Super VL → Nano Omni": AUTO_MODEL,
  "Nemotron 3.5 Super VL": SUPER_VL,
  "Nemotron 3 Nano Omni": NANO_OMNI,
}
DEFAULT_MODEL = AUTO_MODEL


class NimError(RuntimeError):
  """Lỗi NIM cần dừng lô để tránh ghi kết quả thiếu."""


class NimUnavailable(NimError):
  """Model tạm thời không phản hồi."""


@dataclass(frozen=True)
class NimUsage:
  request_count: int
  http_status: int | None = None
  remaining: int | None = None


@dataclass
class NimVision:
  model: str = DEFAULT_MODEL
  api_key: str = field(default="", repr=False)
  request_count: int = field(default=0, init=False)
  on_usage: Callable[[NimUsage], None] | None = field(default=None, kw_only=True, repr=False)
  _use_backup: bool = field(default=False, init=False, repr=False)

  def __post_init__(self) -> None:
    self.api_key = (self.api_key or os.getenv("NVIDIA_API_KEY", "")).strip()
    if not self.api_key.startswith("nvapi-") or any(c.isspace() for c in self.api_key):
      raise NimError("Nhập API key NVIDIA bắt đầu bằng nvapi-, hoặc đặt NVIDIA_API_KEY.")
    if self.model not in MODELS.values():
      raise NimError("Chọn một model NVIDIA NIM trong danh sách.")

  def read(
    self, prompt: str, images: list[bytes],
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[str], None] | None = None,
  ) -> str:
    if self.model != AUTO_MODEL:
      return self._read_model(self.model, prompt, images, cancel_event, on_progress)
    if not self._use_backup:
      try:
        return self._read_model(SUPER_VL, prompt, images, cancel_event, on_progress)
      except NimUnavailable as exc:
        check_cancel(cancel_event)
        self._use_backup = True
        if on_progress:
          on_progress(f"{exc} Đang chuyển sang Nano Omni.")
    return self._read_model(NANO_OMNI, prompt, images, cancel_event, on_progress)

  def _read_model(self, model, prompt, images, cancel_event, on_progress) -> str:
    check_cancel(cancel_event)
    self.request_count += 1
    if on_progress:
      short_name = "Super VL" if model == SUPER_VL else "Nano Omni"
      on_progress(f"NVIDIA NIM · {short_name} · yêu cầu {self.request_count}")
    content = [{"type": "text", "text": prompt}]
    for image in images:
      encoded = base64.b64encode(image).decode("ascii")
      content.append({
        "type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
      })
    payload = {
      "model": model,
      "messages": [{"role": "user", "content": content}],
      "temperature": 0.1,
      "max_tokens": 2048,
      "chat_template_kwargs": {"enable_thinking": False},
      "stream": True,
    }
    parts: list[str] = []
    try:
      if self.on_usage:
        self.on_usage(NimUsage(self.request_count))
      with requests.post(
        CHAT_URL,
        headers={"Authorization": f"Bearer {self.api_key}", "Accept": "text/event-stream"},
        json=payload, timeout=(10, 45), stream=True, allow_redirects=False,
      ) as response:
        if self.on_usage:
          self.on_usage(NimUsage(
            self.request_count, response.status_code, _remaining_requests(response.headers),
          ))
        check_cancel(cancel_event)
        if response.status_code != 200:
          raise _http_error(response.status_code, response.headers.get("Retry-After"))
        deadline = time.monotonic() + 100
        for line in response.iter_lines():
          check_cancel(cancel_event)
          if time.monotonic() > deadline:
            raise NimUnavailable("NVIDIA NIM phản hồi quá chậm.")
          if not line.startswith(b"data:"):
            continue
          event = line[5:].strip()
          if not event or event == b"[DONE]":
            break
          packet = json.loads(event)
          if packet.get("error"):
            error = packet["error"]
            code = error.get("code") if isinstance(error, dict) else None
            raise _http_error(int(code) if str(code).isdigit() else 502)
          for choice in packet.get("choices", []):
            parts.append((choice.get("delta") or {}).get("content") or "")
            reason = choice.get("finish_reason")
            if reason == "stop":
              return "".join(parts).strip()
            if reason == "length":
              return ""  # Luồng OCR sẽ thử với vùng ảnh nhỏ hơn.
            if reason == "error":
              raise NimUnavailable("Model NVIDIA NIM gặp lỗi trong lúc đọc ảnh.")
            if reason:
              raise NimError(f"NVIDIA NIM không hoàn tất đọc ảnh ({reason}).")
    except (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError):
      check_cancel(cancel_event)
      raise NimUnavailable("Kết nối NVIDIA NIM bị gián đoạn hoặc quá thời gian chờ.") from None
    except (requests.RequestException, ValueError, TypeError, AttributeError):
      check_cancel(cancel_event)
      raise NimError("NVIDIA NIM trả phản hồi không hợp lệ.") from None
    raise NimUnavailable("Kết nối NVIDIA NIM kết thúc trước khi đọc xong ảnh.")


def _remaining_requests(headers) -> int | None:
  for name in ("X-RateLimit-Remaining-Requests", "RateLimit-Remaining", "X-RateLimit-Remaining"):
    value = headers.get(name)
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 12:
      return int(value)
  return None


def _http_error(status: int, retry_after: str | None = None) -> NimError:
  if status in (404, 408, 500, 502, 503, 504):
    return NimUnavailable(f"Endpoint NVIDIA NIM tạm thời không phục vụ được (HTTP {status}).")
  if status == 429:
    wait = f" Thử lại sau {retry_after} giây." if retry_after and retry_after.isdecimal() else " Chờ rồi thử lại."
    return NimError(f"NVIDIA NIM đang giới hạn số lần gọi (HTTP 429).{wait}")
  if status in (401, 403):
    return NimError(f"NVIDIA từ chối API key hoặc quyền truy cập (HTTP {status}).")
  return NimError(f"NVIDIA NIM không xử lý được yêu cầu (HTTP {status}).")
