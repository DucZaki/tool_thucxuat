# -*- coding: utf-8 -*-
"""Cài Ollama trên Windows: tải bộ cài chính thức, chạy silent, chờ có ollama.exe.

Bộ cài của Ollama là Inno Setup nên dùng được /VERYSILENT (không cần bấm Next).
Cài theo từng người dùng (LOCALAPPDATA), thường không cần quyền admin.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from thuc_xuat.cancel import CancelledByUser
from thuc_xuat.ollama_client import find_ollama_exe

OLLAMA_SETUP_URL = "https://ollama.com/download/OllamaSetup.exe"
CHUNK_SIZE = 512 * 1024


def download_setup(
    dest: str | Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_event: Any = None,
    timeout: float = 60.0,
) -> Path:
    """Tải OllamaSetup.exe; on_progress(done, total) tính bằng byte."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        OLLAMA_SETUP_URL,
        stream=True,
        timeout=timeout,
        headers={"User-Agent": "ThucXuatTool-setup"},
    ) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(CHUNK_SIZE):
                if cancel_event is not None and cancel_event.is_set():
                    raise CancelledByUser("Đã hủy tải bộ cài Ollama.")
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    return dest


def install(setup_path: str | Path, *, timeout: float = 1800.0) -> str:
    """Chạy bộ cài silent, trả về đường dẫn ollama.exe."""
    setup_path = Path(setup_path)
    if not setup_path.exists():
        raise FileNotFoundError(f"Không thấy bộ cài Ollama: {setup_path}")
    subprocess.run(
        [str(setup_path), "/VERYSILENT", "/NORESTART", "/SP-"],
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        exe = find_ollama_exe()
        if exe:
            return exe
        time.sleep(2)
    raise RuntimeError(
        "Đã chạy bộ cài Ollama nhưng chưa thấy ollama.exe.\n"
        "Hãy cài tay từ https://ollama.com/download rồi chạy lại bộ cài này."
    )


def ensure_ollama(
    *,
    on_status: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_event: Any = None,
) -> tuple[str, bool]:
    """Bảo đảm máy có ollama.exe. Trả về (đường dẫn, vừa cài mới?)."""
    exe = find_ollama_exe()
    if exe:
        return exe, False

    def status(msg: str) -> None:
        if on_status is not None:
            on_status(msg)

    status("Đang tải bộ cài Ollama (khoảng 1,7 GB)...")
    dest = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
    download_setup(dest, on_progress=on_progress, cancel_event=cancel_event)
    status("Đang cài Ollama (1–3 phút, không cần bấm gì)...")
    exe = install(dest)
    try:
        dest.unlink()
    except OSError:
        pass
    return exe, True
