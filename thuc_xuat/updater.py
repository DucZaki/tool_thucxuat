# -*- coding: utf-8 -*-
"""Tự cập nhật: kiểm tra GitHub Releases, tải bản mới và thay thế bản đang chạy.

Luồng: check_for_update() → download_update() → stage_zip() → launch_updater().
Updater là một script PowerShell chờ app thoát rồi chép đè, sau đó mở lại app.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from thuc_xuat.app_paths import app_dir
from thuc_xuat.cancel import CancelledByUser
from thuc_xuat.version import __version__

GITHUB_REPO = "DucZaki/tool_thucxuat"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSET_NAME = "ThucXuatTool-win64.zip"
USER_AGENT = f"ThucXuatTool/{__version__}"


@dataclass
class UpdateInfo:
    version: str
    notes: str
    download_url: str
    size: int = 0
    digest: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3). Chấp nhận 1.2 và 1.2.3.4."""
    nums = re.findall(r"\d+", str(text))
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    size = max(len(a), len(b))
    a += (0,) * (size - len(a))
    b += (0,) * (size - len(b))
    return a > b


def check_for_update(timeout: float = 12) -> UpdateInfo | None:
    """Trả về thông tin bản mới, hoặc None nếu đang là bản mới nhất."""
    resp = requests.get(
        RELEASES_API,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    if getattr(resp, "status_code", 200) == 404:
        return None  # repo chưa có Release nào
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()

    version = str(data.get("tag_name") or "").lstrip("vV")
    if not version or not is_newer(version, __version__):
        return None

    asset = next(
        (a for a in data.get("assets") or [] if a.get("name") == ASSET_NAME),
        None,
    )
    if not asset:
        return None

    return UpdateInfo(
        version=version,
        notes=str(data.get("body") or "").strip(),
        download_url=str(asset.get("browser_download_url") or ""),
        size=int(asset.get("size") or 0),
        digest=str(asset.get("digest") or ""),
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_digest(path: Path, digest: str) -> None:
    """Kiểm tra SHA-256 nếu GitHub có gắn digest cho asset."""
    digest = (digest or "").strip().lower()
    if not digest.startswith("sha256:"):
        return
    if _sha256(path) != digest.split(":", 1)[1]:
        raise ValueError("File tải về bị hỏng (SHA-256 không khớp). Thử lại sau.")


def download_update(
    info: UpdateInfo,
    dest: str | Path,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_event: Any = None,
) -> Path:
    """Tải file zip bản mới; on_progress(done, total)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        info.download_url,
        stream=True,
        timeout=120,
        headers={"User-Agent": USER_AGENT},
    ) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or info.size or 0)
        done = 0
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(256 * 1024):
                if cancel_event is not None and cancel_event.is_set():
                    raise CancelledByUser("Đã hủy tải bản cập nhật.")
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    verify_digest(dest, info.digest)
    return dest


def work_dir() -> Path:
    """Thư mục tạm cho việc cập nhật."""
    path = Path(tempfile.gettempdir()) / "ThucXuatToolUpdate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stage_zip(zip_path: str | Path, version: str) -> Path:
    """Giải nén bản mới, trả về thư mục chứa ThucXuatTool.exe."""
    staged = work_dir() / f"staged-{version}"
    if staged.exists():
        shutil.rmtree(staged, ignore_errors=True)
    staged.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staged)
    inner = staged / "ThucXuatTool"
    return inner if (inner / "ThucXuatTool.exe").exists() else staged


def can_write_app_dir() -> bool:
    """Thư mục app có ghi được không (để biết cập nhật tại chỗ được)."""
    try:
        probe = app_dir() / ".write-test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _ps_quote(path: str | Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def launch_updater(staged_dir: str | Path) -> Path:
    """Chạy script chờ app thoát → chép đè bản mới → mở lại app."""
    staged = Path(staged_dir)
    app = app_dir()
    exe = app / "ThucXuatTool.exe"
    folder = work_dir()
    script = folder / "apply_update.ps1"
    log = folder / "update-error.log"

    script.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'SilentlyContinue'",
                f"$appPid = {os.getpid()}",
                "# Chờ app thoát (tối đa 2 phút)",
                "for ($i = 0; $i -lt 240; $i++) {",
                "  if (-not (Get-Process -Id $appPid -ErrorAction SilentlyContinue)) { break }",
                "  Start-Sleep -Milliseconds 500",
                "}",
                f"robocopy {_ps_quote(staged)} {_ps_quote(app)} /E /IS /IT /R:3 /W:1 /NFL /NDL /NJH /NJS | Out-Null",
                "$code = $LASTEXITCODE",
                f"if ($code -ge 8) {{ \"LOI cap nhat (robocopy $code)\" | Out-File -FilePath {_ps_quote(log)} -Encoding utf8 }}",
                f"Start-Process -FilePath {_ps_quote(exe)} -WorkingDirectory {_ps_quote(app)}",
                f"Remove-Item -LiteralPath {_ps_quote(staged)} -Recurse -Force",
                "",
            ]
        ),
        encoding="utf-8",
    )

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
        ],
        creationflags=flags,
    )
    return script
