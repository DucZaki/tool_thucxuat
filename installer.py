# -*- coding: utf-8 -*-
"""Bộ cài một chạm cho Thực Xuất (đóng gói thành ThucXuatTool-Setup.exe).

Bấm chạy là tự:
  1. Chép app vào %LOCALAPPDATA%\\Programs\\ThucXuatTool (giữ config.json + KetQua cũ)
  2. Tạo lối tắt Desktop + Start Menu
  3. Cài Ollama nếu máy chưa có (tải bộ cài chính thức, chạy silent)
  4. Tải model gemma3:4b (~3,4 GB) kèm thanh tiến độ
  5. Mở app

Chạy lại bộ cài = cập nhật đè bản mới, dữ liệu người dùng không mất.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import messagebox, ttk

from thuc_xuat import ollama_setup
from thuc_xuat.cancel import CancelledByUser
from thuc_xuat.config import OLLAMA_DEFAULT_MODEL
from thuc_xuat.ollama_client import ensure_server, find_ollama_exe, has_model, pull_model
from thuc_xuat.version import __version__

APP_NAME = "ThucXuatTool"
EXE_NAME = "ThucXuatTool.exe"
SHORTCUT_NAME = "Thực Xuất"
GB = 1024**3
MB = 1024**2


# ---------------------------------------------------------------- đường dẫn

def install_dir() -> Path:
  override = os.environ.get("THUCXUAT_INSTALL_DIR")
  if override:
    return Path(override)
  base = os.environ.get("LOCALAPPDATA") or str(Path.home())
  return Path(base) / "Programs" / APP_NAME


def _user_shell_folder(name: str) -> Path | None:
  """Đường dẫn thật của Desktop/Programs (có thể bị OneDrive chuyển hướng)."""
  try:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
      value, _ = winreg.QueryValueEx(key, name)
    path = Path(os.path.expandvars(str(value)))
    if path.is_dir():
      return path
  except (OSError, ValueError):
    pass
  return None


def desktop_dir() -> Path:
  override = os.environ.get("THUCXUAT_DESKTOP_DIR")
  if override:
    return Path(override)
  known = _user_shell_folder("Desktop")
  if known is not None:
    return known
  return Path(os.environ.get("USERPROFILE") or str(Path.home())) / "Desktop"


def start_menu_dir() -> Path:
  override = os.environ.get("THUCXUAT_STARTMENU_DIR")
  if override:
    return Path(override)
  known = _user_shell_folder("Programs")
  if known is not None:
    return known
  appdata = os.environ.get("APPDATA") or str(Path.home())
  return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def payload_zip() -> Path:
  """File zip chứa app, nhúng trong bộ cài (onefile → _MEIPASS)."""
  here = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
  for candidate in (here / "payload.zip", Path(__file__).resolve().parent / "build" / "payload.zip"):
    if candidate.exists():
      return candidate
  raise RuntimeError("Bộ cài thiếu payload.zip — hãy build lại bằng build_installer.py.")


# ---------------------------------------------------------------- cài đặt

def extract_payload(
  zip_path: Path,
  target: Path,
  on_progress=None,
  cancel_event=None,
) -> int:
  """Giải nén đè lên thư mục cài (không xoá file người dùng)."""
  target.mkdir(parents=True, exist_ok=True)
  count = 0
  with zipfile.ZipFile(zip_path) as zf:
    files = [i for i in zf.infolist() if not i.is_dir()]
    total = sum(i.file_size for i in files) or 1
    done = 0
    for info in files:
      if cancel_event is not None and cancel_event.is_set():
        raise CancelledByUser("Đã hủy cài đặt.")
      name = info.filename.replace("\\", "/")
      if name.startswith("/") or ".." in name.split("/"):
        continue
      zf.extract(info, target)
      done += info.file_size
      count += 1
      if on_progress is not None and count % 20 == 0:
        on_progress(done, total)
  return count


def create_shortcut(link: Path, target: Path) -> None:
  """Tạo .lnk bằng WScript.Shell (PowerShell)."""
  link.parent.mkdir(parents=True, exist_ok=True)

  def q(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"

  script = "\n".join(
    [
      "$ws = New-Object -ComObject WScript.Shell",
      f"$sc = $ws.CreateShortcut({q(link)})",
      f"$sc.TargetPath = {q(target)}",
      f"$sc.WorkingDirectory = {q(target.parent)}",
      f"$sc.IconLocation = {q(str(target) + ',0')}",
      "$sc.Save()",
    ]
  )
  subprocess.run(
    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
    check=True,
    capture_output=True,
    timeout=60,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
  )


def app_running() -> bool:
  try:
    out = subprocess.run(
      ["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/FO", "CSV", "/NH"],
      capture_output=True,
      text=True,
      timeout=30,
      errors="ignore",
      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
  except OSError:
    return False
  return EXE_NAME.lower() in (out.stdout or "").lower()


def close_running_app() -> None:
  subprocess.run(
    ["taskkill", "/IM", EXE_NAME, "/F"],
    capture_output=True,
    timeout=60,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
  )


# ---------------------------------------------------------------- giao diện

class SetupWindow(tk.Tk):
  def __init__(self) -> None:
    super().__init__()
    self.title(f"Cài đặt Thực Xuất v{__version__}")
    self.geometry("660x480")
    self.resizable(False, False)
    self.cancel_event = threading.Event()
    self._running = False
    self._build_ui()
    self.protocol("WM_DELETE_WINDOW", self.on_close)
    self.after(400, self.start)

  # ----- khung

  def _build_ui(self) -> None:
    pad = {"padx": 22}
    tk.Label(self, text="Cài đặt Thực Xuất", font=("Segoe UI", 16, "bold")).pack(
      anchor="w", pady=(20, 2), **pad
    )
    tk.Label(self, text=f"Phiên bản {__version__}", fg="#555").pack(anchor="w", **pad)
    tk.Label(
      self,
      text=f"Thư mục cài: {install_dir()}",
      fg="#555",
      wraplength=610,
      justify="left",
    ).pack(anchor="w", pady=(2, 12), **pad)

    self.status_var = tk.StringVar(value="Chuẩn bị cài đặt...")
    tk.Label(
      self,
      textvariable=self.status_var,
      font=("Segoe UI", 10),
      wraplength=610,
      justify="left",
      anchor="w",
      height=2,
    ).pack(anchor="w", **pad)

    self.progress = ttk.Progressbar(self, orient="horizontal", length=616, mode="indeterminate")
    self.progress.pack(pady=(6, 4), **pad)

    self.detail_var = tk.StringVar(value="")
    tk.Label(self, textvariable=self.detail_var, fg="#555", anchor="w").pack(
      anchor="w", **pad
    )

    self.log_box = tk.Text(
      self,
      height=7,
      width=74,
      state="disabled",
      wrap="word",
      relief="solid",
      borderwidth=1,
      font=("Segoe UI", 9),
      fg="#333333",
    )
    self.log_box.pack(pady=(10, 10), **pad)

    buttons = tk.Frame(self)
    buttons.pack(fill="x", pady=(0, 16), **pad)
    self.cancel_btn = ttk.Button(buttons, text="Hủy", command=self.on_cancel)
    self.cancel_btn.pack(side="left")
    self.primary_btn = ttk.Button(
      buttons, text="Đang cài...", command=self.on_primary, state="disabled"
    )
    self.primary_btn.pack(side="right")

    self.progress.start(12)

  # ----- tiện ích UI (chỉ gọi trên main thread)

  def post(self, fn, *args) -> None:
    try:
      self.after(0, lambda: fn(*args))
    except tk.TclError:
      pass

  def log(self, text: str) -> None:
    self.log_box.config(state="normal")
    self.log_box.insert("end", text + "\n")
    self.log_box.see("end")
    self.log_box.config(state="disabled")

  def set_status(self, text: str) -> None:
    self.status_var.set(text)

  def set_detail(self, text: str) -> None:
    self.detail_var.set(text)

  def bar_indeterminate(self) -> None:
    self.progress.config(mode="indeterminate")
    self.progress.start(12)

  def bar_determinate(self, value: float = 0) -> None:
    self.progress.stop()
    self.progress.config(mode="determinate", maximum=100, value=value)

  def check_cancel(self) -> None:
    if self.cancel_event.is_set():
      raise CancelledByUser("Đã hủy cài đặt.")

  # ----- luồng cài

  def start(self) -> None:
    if self._running:
      return
    if app_running():
      if not messagebox.askyesno(
        "App đang chạy", "Thực Xuất đang mở. Đóng app để cài đè lên bản cũ?"
      ):
        self.destroy()
        return
      close_running_app()
      self.after(800, self.start)
      return
    self._running = True
    threading.Thread(target=self.worker, daemon=True).start()

  def worker(self) -> None:
    model = OLLAMA_DEFAULT_MODEL
    target = install_dir()
    try:
      self.post(self.set_status, "Đang chép ứng dụng...")
      count = extract_payload(
        payload_zip(), target, self.on_extract_progress, self.cancel_event
      )
      self.post(self.set_detail, "")
      self.post(self.log, f"Đã cài {count} tệp vào {target}")

      self.check_cancel()
      self.post(self.set_status, "Đang tạo lối tắt...")
      try:
        create_shortcut(desktop_dir() / f"{SHORTCUT_NAME}.lnk", target / EXE_NAME)
        create_shortcut(start_menu_dir() / f"{SHORTCUT_NAME}.lnk", target / EXE_NAME)
        self.post(self.log, "Đã tạo lối tắt Desktop và Start Menu")
      except Exception as exc:  # lối tắt lỗi không chặn cài đặt
        self.post(self.log, f"Không tạo được lối tắt: {exc}")

      self.check_cancel()
      self.post(self.set_status, "Đang kiểm tra Ollama...")
      if find_ollama_exe() is None:
        self.post(self.log, "Chưa có Ollama — tải bộ cài chính thức (khoảng 1,7 GB)")
        dest = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
        self.post(self.bar_determinate, 0)
        self.post(self.set_status, "Đang tải bộ cài Ollama (1,7 GB)...")
        ollama_setup.download_setup(
          dest,
          on_progress=lambda done, total: self.post(
            self.on_download_progress, done, total, "Ollama"
          ),
          cancel_event=self.cancel_event,
        )
        self.post(self.bar_indeterminate)
        self.post(self.set_status, "Đang cài Ollama (1–3 phút, không cần bấm gì)...")
        exe = ollama_setup.install(dest)
        self.post(self.log, f"Đã cài Ollama: {exe}")
      else:
        self.post(self.log, "Ollama đã có sẵn")

      self.check_cancel()
      self.post(self.set_status, "Đang bật Ollama...")
      ensure_server(on_status=lambda msg: self.post(self.set_status, msg))

      if has_model(model):
        self.post(self.log, f"Model {model} đã có sẵn")
      else:
        self.post(self.bar_determinate, 0)
        self.post(self.set_status, f"Đang tải model {model} (khoảng 3,4 GB)...")
        pull_model(model, on_status=self.on_model_status)

      self.post(self.on_success)
    except CancelledByUser as exc:
      self.post(self.on_cancelled, str(exc))
    except Exception as exc:  # noqa: BLE001 — hiển thị mọi lỗi cho người dùng
      self.post(self.on_failed, str(exc))

  # ----- callback tiến độ

  def on_extract_progress(self, done: int, total: int) -> None:
    self.set_detail(f"{done / MB:.0f} / {total / MB:.0f} MB")

  def on_download_progress(self, done: int, total: int, label: str) -> None:
    if total:
      pct = int(done * 100 / total)
      self.progress.config(value=pct)
      self.set_detail(f"{label}: {done / GB:.2f} / {total / GB:.2f} GB ({pct}%)")
    else:
      self.set_detail(f"{label}: {done / GB:.2f} GB")

  def on_model_status(self, msg: str) -> None:
    self.set_status(msg)
    match = re.search(r"(\d+)%", msg)
    if match:
      self.progress.config(value=int(match.group(1)))

  # ----- kết thúc

  def on_success(self) -> None:
    self._running = False
    self.progress.stop()
    self.progress.config(mode="determinate", value=100)
    self.set_status("Cài đặt hoàn tất — model đã sẵn sàng.")
    self.set_detail(f"Đã cài vào {install_dir()}")
    self.log("Xong. Bấm «Mở ứng dụng» để chạy.")
    self.primary_btn.config(text="Mở ứng dụng", state="normal")
    self.cancel_btn.config(text="Đóng", state="normal")

  def on_cancelled(self, reason: str) -> None:
    self._running = False
    self.progress.stop()
    self.progress.config(mode="determinate", value=0)
    self.set_status("Đã hủy cài đặt.")
    self.log(reason or "Đã hủy.")
    exe = install_dir() / EXE_NAME
    self.primary_btn.config(
      text="Mở ứng dụng" if exe.exists() else "Đóng",
      state="normal",
    )
    self.cancel_btn.config(text="Đóng", state="normal")

  def on_failed(self, message: str) -> None:
    self._running = False
    self.progress.stop()
    self.progress.config(mode="determinate", value=0)
    first = (message or "Lỗi không rõ").strip().splitlines()[0]
    self.set_status(f"Chưa cài xong: {first[:150]}")
    self.log("LỖI: " + (message or "").strip())
    exe = install_dir() / EXE_NAME
    self.primary_btn.config(
      text="Mở ứng dụng" if exe.exists() else "Đóng",
      state="normal",
    )
    self.cancel_btn.config(text="Đóng", state="normal")

  def on_primary(self) -> None:
    exe = install_dir() / EXE_NAME
    if not exe.exists():
      self.destroy()
      return
    try:
      subprocess.Popen([str(exe)], cwd=str(exe.parent))
    except OSError as exc:
      messagebox.showerror("Không mở được ứng dụng", str(exc))
      return
    self.destroy()

  def on_cancel(self) -> None:
    if self._running:
      self.cancel_event.set()
      self.cancel_btn.config(text="Đang hủy...", state="disabled")
      self.set_status("Đang hủy...")
      return
    self.destroy()

  def on_close(self) -> None:
    if self._running:
      messagebox.showinfo("Đang cài đặt", "Bấm «Hủy» rồi đóng cửa sổ này.")
      return
    self.destroy()


def main() -> int:
  try:
    from ctypes import windll

    windll.shcore.SetProcessDpiAwareness(1)
  except Exception:  # noqa: BLE001 — chỉ để chữ nét hơn
    pass
  window = SetupWindow()
  window.mainloop()
  return 0


if __name__ == "__main__":
  sys.exit(main())
