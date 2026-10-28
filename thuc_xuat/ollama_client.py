# -*- coding: utf-8 -*-
"""Client Ollama local — đọc ảnh bằng model vision (mặc định gemma3:4b).

Không cần Internet, không cần API key. Chỉ cần Ollama đang chạy
(https://ollama.com) và model đã tải: `ollama pull gemma3:4b`.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from thuc_xuat.cancel import CancelledByUser, check_cancel
from thuc_xuat.config import get_ollama_settings

CONNECT_TIMEOUT = 5.0
SERVER_WAIT_SECONDS = 40.0


class OllamaError(RuntimeError):
  """Lỗi khi gọi Ollama, message đã viết sẵn cho người dùng."""


def _session() -> requests.Session:
  # trust_env=False: bỏ qua proxy hệ thống — Ollama chạy ở localhost.
  session = requests.Session()
  session.trust_env = False
  return session


def list_models(
  base_url: str | None = None,
  timeout: float = 3.0,
) -> list[str]:
  """Trả về tên các model đã tải. Raise OllamaError nếu không kết nối được."""
  base = (base_url or get_ollama_settings()["base_url"]).rstrip("/")
  try:
    with _session() as session:
      resp = session.get(f"{base}/api/tags", timeout=timeout)
  except requests.exceptions.RequestException as exc:
    raise OllamaError(_offline_message(base)) from exc
  if resp.status_code != 200:
    raise OllamaError(
      f"Ollama trả lỗi HTTP {resp.status_code} khi lấy danh sách model.\n{resp.text[:300]}"
    )
  try:
    data = resp.json()
  except ValueError as exc:
    raise OllamaError("Ollama trả dữ liệu không phải JSON.") from exc
  return [str(m.get("name") or m.get("model") or "") for m in data.get("models", [])]


def find_ollama_exe() -> str | None:
  """Đường dẫn tới ollama.exe (PATH hoặc thư mục cài mặc định trên Windows)."""
  found = shutil.which("ollama")
  if found:
    return found
  candidates: list[Path] = []
  local = os.environ.get("LOCALAPPDATA")
  if local:
    candidates.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
  for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
    base = os.environ.get(env)
    if base:
      candidates.append(Path(base) / "Ollama" / "ollama.exe")
  for path in candidates:
    if path.is_file():
      return str(path)
  return None


def _spawn_server(exe: str) -> None:
  """Chạy `ollama serve` ẩn, tách khỏi app (app tắt vẫn còn chạy cho lần sau)."""
  kwargs: dict[str, Any] = {
    "stdin": subprocess.DEVNULL,
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
  }
  if sys.platform == "win32":
    kwargs["creationflags"] = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
  subprocess.Popen([exe, "serve"], **kwargs)


def ensure_server(
  base_url: str | None = None,
  *,
  autostart: bool = True,
  wait_seconds: float = SERVER_WAIT_SECONDS,
  on_status: Callable[[str], None] | None = None,
) -> str:
  """Bảo đảm Ollama đang chạy; tự bật `ollama serve` nếu chưa.

  Trả về "running" (đã chạy sẵn) hoặc "started" (app vừa bật).
  Raise OllamaError với hướng dẫn cụ thể nếu không bật được.
  """
  def status(msg: str) -> None:
    if on_status is not None:
      on_status(msg)

  try:
    list_models(base_url, timeout=2.0)
    return "running"
  except OllamaError:
    pass

  base = (base_url or get_ollama_settings()["base_url"]).rstrip("/")
  if not autostart:
    raise OllamaError(_offline_message(base))

  exe = find_ollama_exe()
  if exe is None:
    raise OllamaError(
      f"Không kết nối được Ollama tại {base} và không tìm thấy ollama.exe.\n\n"
      "Cách xử lý:\n"
      "1. Cài Ollama: https://ollama.com/download\n"
      "2. Mở PowerShell: ollama pull gemma3:4b\n"
      "3. Mở lại app (app sẽ tự bật Ollama)."
    )

  status("Đang khởi động Ollama...")
  try:
    _spawn_server(exe)
  except OSError as exc:
    raise OllamaError(f"Không chạy được «{exe} serve».\n{exc}") from exc

  deadline = time.monotonic() + wait_seconds
  while time.monotonic() < deadline:
    time.sleep(0.7)
    try:
      list_models(base_url, timeout=2.0)
      return "started"
    except OllamaError:
      continue
  raise OllamaError(
    f"Đã chạy «{exe} serve» nhưng sau {int(wait_seconds)}s vẫn không kết nối được {base}.\n\n"
    "Thử mở app Ollama bằng tay rồi bấm lại."
  )


def has_model(model: str, base_url: str | None = None) -> bool:
  """Model đã tải chưa. «gemma3» (không tag) tính là «gemma3:latest»."""
  target = (model or "").strip()
  if not target:
    return False
  name, _, tag = target.partition(":")
  want_tag = tag or "latest"
  for item in list_models(base_url, timeout=3.0):
    item_name, _, item_tag = item.partition(":")
    if item_name == name and (item_tag or "latest") == want_tag:
      return True
  return False


def pull_model(
  model: str,
  base_url: str | None = None,
  *,
  on_status: Callable[[str], None] | None = None,
  timeout: float = 3600.0,
) -> None:
  """Tải model (như `ollama pull`), báo tiến độ qua on_status."""
  base = (base_url or get_ollama_settings()["base_url"]).rstrip("/")
  last = -1
  try:
    with _session() as session:
      with session.post(
        f"{base}/api/pull",
        json={"model": model, "stream": True},
        stream=True,
        timeout=(CONNECT_TIMEOUT, timeout),
      ) as resp:
        if resp.status_code != 200:
          raise OllamaError(f"Ollama không tải được model «{model}» (HTTP {resp.status_code}).\n{resp.text[:300]}")
        for raw in resp.iter_lines(decode_unicode=True):
          if not raw:
            continue
          try:
            obj = json.loads(raw)
          except ValueError:
            continue
          if obj.get("error"):
            raise OllamaError(f"Tải model lỗi: {obj['error']}")
          total = int(obj.get("total") or 0)
          done = int(obj.get("completed") or 0)
          if total and on_status is not None:
            pct = int(done * 100 / total)
            if pct >= last + 5:
              last = pct
              on_status(f"Đang tải model {model}: {pct}%")
          if obj.get("status") == "success":
            break
  except requests.exceptions.RequestException as exc:
    raise OllamaError(f"Tải model «{model}» thất bại: {exc}") from exc
  if on_status is not None:
    on_status(f"Đã tải xong model {model}")


class ModelMissing(OllamaError):
  """Ollama chạy nhưng chưa tải model cần dùng."""


def ensure_ready(
  model: str | None = None,
  base_url: str | None = None,
  *,
  on_status: Callable[[str], None] | None = None,
  pull: bool = False,
) -> dict[str, Any]:
  """Bật Ollama nếu cần + kiểm tra model; pull=True thì tải luôn model còn thiếu."""
  cfg = get_ollama_settings()
  mdl = (model or cfg["model"]).strip()
  server = ensure_server(base_url, on_status=on_status)
  if has_model(mdl, base_url):
    return {"server": server, "model": mdl, "pulled": False}
  if not pull:
    raise ModelMissing(
      f"Ollama đã chạy nhưng chưa có model «{mdl}».\n\n"
      f"Tải ngay (khoảng 3 GB):\n    ollama pull {mdl}"
    )
  pull_model(mdl, base_url, on_status=on_status)
  return {"server": server, "model": mdl, "pulled": True}


def _offline_message(base: str) -> str:
  host = urlparse(base).hostname or base
  return (
    f"Không kết nối được Ollama tại {base}.\n\n"
    "Cách xử lý:\n"
    "1. Mở app Ollama (hoặc chạy: ollama serve)\n"
    f"2. Kiểm tra {host} đúng cổng 11434\n"
    "3. Tải model nếu chưa có: ollama pull gemma3:4b"
  )


def _http_error_message(resp: requests.Response, model: str) -> str:
  body = (resp.text or "").strip()
  low = body.lower()
  if resp.status_code == 404 or "not found" in low or "pull model" in low:
    return (
      f"Chưa có model «{model}» trong Ollama.\n\n"
      f"Chạy lệnh sau rồi thử lại:\n    ollama pull {model}"
    )
  if resp.status_code >= 500:
    return f"Ollama lỗi máy chủ (HTTP {resp.status_code}).\n{body[:500]}"
  return f"Ollama trả lỗi HTTP {resp.status_code}.\n{body[:500]}"


def chat_vision(
  prompt: str,
  image_bytes: list[bytes],
  *,
  model: str | None = None,
  base_url: str | None = None,
  timeout: float | None = None,
  num_ctx: int | None = None,
  cancel_event: threading.Event | None = None,
) -> str:
  """Gửi prompt + 1 (hoặc vài) ảnh, trả về text model sinh ra.

  Stream NDJSON để bấm «Dừng» có tác dụng ngay: đóng kết nối là Ollama
  dừng sinh token.
  """
  cfg = get_ollama_settings()
  base = (base_url or cfg["base_url"]).rstrip("/")
  mdl = model or cfg["model"]
  read_timeout = float(timeout or cfg["timeout"])
  ctx = int(num_ctx or cfg["num_ctx"])

  check_cancel(cancel_event)
  payload: dict[str, Any] = {
    "model": mdl,
    "messages": [
      {
        "role": "user",
        "content": prompt,
        "images": [base64.standard_b64encode(b).decode("ascii") for b in image_bytes],
      }
    ],
    "stream": True,
    "format": "json",
    "options": {"temperature": 0, "num_ctx": ctx},
  }

  parts: list[str] = []
  try:
    with _session() as session:
      with session.post(
        f"{base}/api/chat",
        json=payload,
        stream=True,
        timeout=(CONNECT_TIMEOUT, read_timeout),
      ) as resp:
        if resp.status_code != 200:
          raise OllamaError(_http_error_message(resp, mdl))
        try:
          for raw in resp.iter_lines(decode_unicode=True):
            check_cancel(cancel_event)
            if not raw:
              continue
            try:
              obj = json.loads(raw)
            except ValueError:
              continue
            if isinstance(obj, dict) and obj.get("error"):
              raise OllamaError(f"Ollama báo lỗi: {obj['error']}")
            message = obj.get("message") or {}
            piece = message.get("content") or ""
            if piece:
              parts.append(piece)
            if obj.get("done"):
              break
        except CancelledByUser:
          # Thoát khỏi `with` để đóng socket → Ollama dừng sinh.
          raise
  except OllamaError:
    raise
  except CancelledByUser:
    raise
  except requests.exceptions.Timeout as exc:
    raise OllamaError(
      f"Ollama không phản hồi trong {int(read_timeout)}s.\n"
      "Ảnh quá lớn hoặc máy đang tải nặng — thử giảm số dải ảnh trong Cài đặt."
    ) from exc
  except requests.exceptions.RequestException as exc:
    raise OllamaError(_offline_message(base)) from exc

  text = "".join(parts).strip()
  if not text:
    raise OllamaError(
      "Ollama trả về rỗng.\n"
      f"Kiểm tra model «{mdl}» có hỗ trợ đọc ảnh (vision) và thử lại."
    )
  return text
