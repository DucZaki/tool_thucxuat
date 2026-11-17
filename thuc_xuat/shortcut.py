# -*- coding: utf-8 -*-
"""Tạo lối tắt .lnk ngoài Desktop / Start Menu — không cần PowerShell.

Dùng IShellLinkW của Windows qua ctypes nên chạy được cả khi máy chặn
PowerShell/WSH. Đường dẫn Desktop và Start Menu lấy từ Known Folder API,
đúng cả khi OneDrive chuyển hướng thư mục Desktop.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

_IS_WINDOWS = os.name == "nt"

CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
IID_ISHELLLINKW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPERSISTFILE = "{0000010B-0000-0000-C000-000000000046}"
FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_PROGRAMS = "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}"

CLSCTX_INPROC_SERVER = 0x1
COINIT_APARTMENTTHREADED = 0x2
RPC_E_CHANGED_MODE = 0x80010106
SHCNE_CREATE = 0x00000002
SHCNF_PATHW = 0x0005

# Chỉ số hàm trong bảng ảo (vtable) của các giao diện COM.
_SL_SET_WORKDIR = 9
_SL_SET_ARGUMENTS = 11
_SL_SET_ICON = 17
_SL_SET_PATH = 20
_PF_SAVE = 6
_RELEASE = 2


class _GUID(ctypes.Structure):
  _fields_ = [
    ("Data1", wintypes.DWORD),
    ("Data2", wintypes.WORD),
    ("Data3", wintypes.WORD),
    ("Data4", ctypes.c_ubyte * 8),
  ]


def _guid(text: str) -> _GUID:
  value = text.strip().strip("{}")
  parts = value.split("-")
  if len(parts) != 5:
    raise ValueError(f"GUID không hợp lệ: {text}")
  tail = bytes.fromhex(parts[3] + parts[4])
  if len(tail) != 8:
    raise ValueError(f"GUID không hợp lệ: {text}")
  guid = _GUID()
  guid.Data1 = int(parts[0], 16)
  guid.Data2 = int(parts[1], 16)
  guid.Data3 = int(parts[2], 16)
  for index, byte in enumerate(tail):
    guid.Data4[index] = byte
  return guid


if _IS_WINDOWS:
  _HRESULT = ctypes.c_long
  _PVOID = ctypes.c_void_p

  _ole32 = ctypes.WinDLL("ole32", use_last_error=True)
  _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

  _ole32.CoInitializeEx.argtypes = [_PVOID, wintypes.DWORD]
  _ole32.CoInitializeEx.restype = _HRESULT
  _ole32.CoUninitialize.argtypes = []
  _ole32.CoUninitialize.restype = None
  _ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(_GUID),
    _PVOID,
    wintypes.DWORD,
    ctypes.POINTER(_GUID),
    ctypes.POINTER(_PVOID),
  ]
  _ole32.CoCreateInstance.restype = _HRESULT
  _ole32.CoTaskMemFree.argtypes = [_PVOID]
  _ole32.CoTaskMemFree.restype = None

  _shell32.SHGetKnownFolderPath.argtypes = [
    ctypes.POINTER(_GUID),
    wintypes.DWORD,
    _PVOID,
    ctypes.POINTER(ctypes.c_wchar_p),
  ]
  _shell32.SHGetKnownFolderPath.restype = _HRESULT
  _shell32.SHChangeNotify.argtypes = [
    wintypes.LONG,
    wintypes.UINT,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
  ]
  _shell32.SHChangeNotify.restype = None

  _FN_QUERY_INTERFACE = ctypes.WINFUNCTYPE(
    _HRESULT, _PVOID, ctypes.POINTER(_GUID), ctypes.POINTER(_PVOID)
  )
  _FN_RELEASE = ctypes.WINFUNCTYPE(ctypes.c_ulong, _PVOID)
  _FN_SET_TEXT = ctypes.WINFUNCTYPE(_HRESULT, _PVOID, wintypes.LPCWSTR)
  _FN_SET_ICON = ctypes.WINFUNCTYPE(_HRESULT, _PVOID, wintypes.LPCWSTR, ctypes.c_int)
  _FN_SAVE = ctypes.WINFUNCTYPE(_HRESULT, _PVOID, wintypes.LPCWSTR, wintypes.BOOL)


def _failed(hr: int) -> bool:
  return hr < 0


def _check(hr: int, what: str) -> None:
  if _failed(hr):
    raise OSError(f"{what} lỗi 0x{hr & 0xFFFFFFFF:08X}")


def _method(ptr, index: int, prototype):
  vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
  return prototype(vtable[index])


def _co_initialize() -> bool:
  """Trả về True nếu hàm này đã khởi tạo COM (cần CoUninitialize khi xong)."""
  hr = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
  if (hr & 0xFFFFFFFF) == RPC_E_CHANGED_MODE:
    return False
  _check(hr, "CoInitializeEx")
  return True


def _known_folder(guid_text: str) -> Path | None:
  if not _IS_WINDOWS:
    return None
  guid = _guid(guid_text)
  out = ctypes.c_wchar_p()
  hr = _shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
  if (hr & 0xFFFFFFFF) != 0:
    return None
  try:
    value = out.value or ""
  finally:
    _ole32.CoTaskMemFree(out)
  return Path(value) if value else None


def _user_shell_folder(name: str) -> Path | None:
  """Dự phòng: đọc đường dẫn từ registry (có thể bị OneDrive chuyển hướng)."""
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
  for finder in (lambda: _known_folder(FOLDERID_DESKTOP), lambda: _user_shell_folder("Desktop")):
    try:
      found = finder()
    except OSError:
      found = None
    if found is not None and found.is_dir():
      return found
  return Path(os.environ.get("USERPROFILE") or str(Path.home())) / "Desktop"


def start_menu_dir() -> Path:
  override = os.environ.get("THUCXUAT_STARTMENU_DIR")
  if override:
    return Path(override)
  for finder in (lambda: _known_folder(FOLDERID_PROGRAMS), lambda: _user_shell_folder("Programs")):
    try:
      found = finder()
    except OSError:
      found = None
    if found is not None and found.is_dir():
      return found
  appdata = os.environ.get("APPDATA") or str(Path.home())
  return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def create_shortcut(link: Path | str, target: Path | str, arguments: str = "") -> Path:
  """Tạo file .lnk trỏ tới `target`. Trả về đường dẫn file .lnk."""
  link_path = Path(link)
  target_path = Path(target)
  if not _IS_WINDOWS:
    raise OSError("Chỉ hỗ trợ Windows.")
  if not target_path.is_file():
    raise FileNotFoundError(f"Không thấy tệp ứng dụng: {target_path}")
  link_path.parent.mkdir(parents=True, exist_ok=True)

  initialized = _co_initialize()
  link_ptr = _PVOID()
  persist_ptr = _PVOID()
  try:
    clsid = _guid(CLSID_SHELL_LINK)
    iid_link = _guid(IID_ISHELLLINKW)
    hr = _ole32.CoCreateInstance(
      ctypes.byref(clsid),
      None,
      CLSCTX_INPROC_SERVER,
      ctypes.byref(iid_link),
      ctypes.byref(link_ptr),
    )
    _check(hr, "CoCreateInstance(ShellLink)")

    _check(_method(link_ptr, _SL_SET_PATH, _FN_SET_TEXT)(link_ptr, str(target_path)), "SetPath")
    _check(
      _method(link_ptr, _SL_SET_WORKDIR, _FN_SET_TEXT)(link_ptr, str(target_path.parent)),
      "SetWorkingDirectory",
    )
    if arguments:
      _check(
        _method(link_ptr, _SL_SET_ARGUMENTS, _FN_SET_TEXT)(link_ptr, arguments),
        "SetArguments",
      )
    _check(
      _method(link_ptr, _SL_SET_ICON, _FN_SET_ICON)(link_ptr, str(target_path), 0),
      "SetIconLocation",
    )

    iid_persist = _guid(IID_IPERSISTFILE)
    hr = _method(link_ptr, 0, _FN_QUERY_INTERFACE)(
      link_ptr, ctypes.byref(iid_persist), ctypes.byref(persist_ptr)
    )
    _check(hr, "QueryInterface(IPersistFile)")
    _check(_method(persist_ptr, _PF_SAVE, _FN_SAVE)(persist_ptr, str(link_path), True), "Save")
  finally:
    if persist_ptr:
      _method(persist_ptr, _RELEASE, _FN_RELEASE)(persist_ptr)
    if link_ptr:
      _method(link_ptr, _RELEASE, _FN_RELEASE)(link_ptr)
    if initialized:
      _ole32.CoUninitialize()

  if not link_path.is_file():
    raise OSError(f"Không tạo được lối tắt: {link_path}")
  try:
    _shell32.SHChangeNotify(SHCNE_CREATE, SHCNF_PATHW, str(link_path), None)
  except OSError:
    pass
  return link_path


def ensure_shortcuts(exe: Path | str, name: str = "Thực Xuất") -> list[Path]:
  """Tạo lối tắt Desktop + Start Menu nếu chưa có. Không ném lỗi."""
  exe_path = Path(exe)
  made: list[Path] = []
  for folder in (desktop_dir(), start_menu_dir()):
    link = folder / f"{name}.lnk"
    try:
      if link.is_file():
        made.append(link)
        continue
      made.append(create_shortcut(link, exe_path))
    except Exception:  # noqa: BLE001 — thiếu lối tắt không được làm chết app
      continue
  return made


def is_installed_copy(exe: Path | str | None = None) -> bool:
  """True nếu đang chạy bản cài trong %LOCALAPPDATA%\\Programs\\ThucXuatTool."""
  path = Path(exe) if exe is not None else Path(sys.executable)
  local = os.environ.get("LOCALAPPDATA")
  if not local:
    return False
  try:
    expected = (Path(local) / "Programs" / "ThucXuatTool").resolve()
    return path.resolve().parent == expected
  except OSError:
    return False


def ensure_app_shortcuts(name: str = "Thực Xuất") -> list[Path]:
  """Bản .exe đã cài: tự tạo lại lối tắt nếu người dùng lỡ xoá."""
  if not getattr(sys, "frozen", False):
    return []
  exe = Path(sys.executable)
  if not is_installed_copy(exe):
    return []
  return ensure_shortcuts(exe, name)
