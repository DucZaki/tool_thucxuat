# -*- coding: utf-8 -*-
"""GUI: Excel + ảnh phiếu → file Excel kết quả."""
from __future__ import annotations

import re
import shutil
import sys
import threading
import tkinter as tk
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from thuc_xuat.app_paths import app_dir
from thuc_xuat.cancel import CancelledByUser
from thuc_xuat.config import (
  auto_open_output_folder,
  load_config,
  output_dir,
  set_config_value,
)
from thuc_xuat.nim_client import DEFAULT_MODEL, MODELS, NimError, NimUsage, NimVision
from thuc_xuat.image_match import pdn_from_excel
from thuc_xuat.image_prep import warp_available
from thuc_xuat.io_json import load_mapping, load_sidecar, save_mapping
from thuc_xuat.output_edit import (
  OutputFormatError,
  OutputSheet,
  load_sheet,
  restore_output_mapping,
  save_sheet,
)
from thuc_xuat.pipeline import BatchResult, ProcessResult, process_batch_like_chat
from thuc_xuat.processor import (
  build_output,
  calculate_manual_qty,
  default_output_path,
  expected_qty,
  load_products,
  parse_qty,
  save_output,
)
from thuc_xuat.shell_open import reveal_in_folder, reveal_outputs
from thuc_xuat.updater import (
  UpdateInfo,
  can_write_app_dir,
  check_for_update,
  download_update,
  launch_updater,
  stage_zip,
  work_dir,
)
from thuc_xuat.version import __version__
from thuc_xuat.vision_extract import VisionResume


@dataclass
class OrderJob:
  excel_path: Path
  product_count: int = 0


class ThucXuatApp(tk.Tk):
  def __init__(self) -> None:
    super().__init__()
    self.title(f"Thực Xuất v{__version__} — Ảnh phiếu + Excel → Excel nhập kho")
    self._fit_window()
    self.resizable(True, True)

    self.jobs: list[OrderJob] = []
    self.all_images: list[Path] = []
    self.results_by_excel: dict[Path, ProcessResult] = {}
    self._ocr_resume = VisionResume()
    self._manual_edits: dict[Path, dict[int, str | None]] = {}
    self.preview_job: OrderJob | None = None
    self._standalone_sheet: OutputSheet | None = None
    self._extra_output: Path | None = None
    self._cell_editor: ttk.Entry | None = None
    self._cancel_event = threading.Event()
    self._busy = False
    self._nim_requests = 0
    self._update_info: UpdateInfo | None = None
    self._checking_update = False
    self._updating = False

    self._build_ui()
    self.protocol("WM_DELETE_WINDOW", self._on_close)
    self.after(1500, self._check_update_silent)

  def _fit_window(self) -> None:
    """Mở vừa màn hình để luôn thấy viền dưới (kéo được cả ngang lẫn dọc)."""
    sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
    width = min(1260, max(760, sw - 40))
    height = min(810, max(480, sh - 90))
    x = max(0, (sw - width) // 2)
    y = max(0, (sh - height) // 2 - 20)
    self.geometry(f"{width}x{height}+{x}+{y}")
    self.minsize(min(950, width), min(640, height))

  def report_callback_exception(self, exc, val, tb) -> None:  # noqa: ANN001
    """Lỗi trong 1 nút bấm không được làm app chết im lặng (bản .exe không có console)."""
    detail = "".join(traceback.format_exception(exc, val, tb))
    log_path = app_dir() / "error.log"
    try:
      log_path.write_text(detail, encoding="utf-8")
    except OSError:
      log_path = None
    text = str(val) or exc.__name__
    message = (
      f"{text}\n\n"
      + (f"Chi tiết đã ghi vào:\n{log_path}" if log_path else "Không ghi được log lỗi.")
    )
    try:
      messagebox.showerror("Lỗi ứng dụng", message)
    except Exception:  # noqa: BLE001 — cửa sổ lỗi không được làm app chết
      pass

  def _build_ui(self) -> None:
    style = ttk.Style(self)
    style.theme_use("clam")
    ink, muted, accent = "#172b36", "#526773", "#176854"
    canvas, white, line = "#eef3f4", "#ffffff", "#d6e1e5"
    self.configure(background=canvas)
    style.configure(".", font=("Segoe UI", 10), background=white, foreground=ink)
    style.configure("Canvas.TFrame", background=canvas)
    style.configure("Card.TFrame", background=white)
    style.configure("Title.TLabel", background=canvas, font=("Segoe UI", 22, "bold"))
    style.configure("Subtitle.TLabel", background=canvas, foreground=muted)
    style.configure("Section.TLabel", font=("Segoe UI", 13, "bold"))
    style.configure("Field.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("Muted.TLabel", foreground=muted)
    style.configure("Warning.TLabel", foreground="#965602")
    style.configure("TButton", padding=(10, 6), background="#eaf0f2", bordercolor=line)
    style.map("TButton", background=[("disabled", "#f3f5f6"), ("active", "#dfeaec")])
    style.configure("Primary.TButton", background=accent, foreground=white, font=("Segoe UI", 10, "bold"))
    style.map("Primary.TButton",
              background=[("disabled", "#e2ebea"), ("active", "#125642")],
              foreground=[("disabled", muted)])
    style.configure("TEntry", padding=6, fieldbackground=white, bordercolor=line)
    style.configure("TCombobox", padding=5, arrowsize=14, bordercolor=line)
    style.map("TCombobox", fieldbackground=[("readonly", white)])
    style.configure("Treeview", rowheight=28, background=white, fieldbackground=white, bordercolor=line)
    style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), padding=(6, 8), background="#e8f0f2")
    style.map("Treeview", background=[("selected", "#d3ebe3")], foreground=[("selected", ink)])
    style.configure("Horizontal.TProgressbar", thickness=5, background=accent, troughcolor=line, bordercolor=line)

    shell = ttk.Frame(self, style="Canvas.TFrame", padding=(18, 12))
    shell.pack(fill=tk.BOTH, expand=True)
    header = ttk.Frame(shell, style="Canvas.TFrame")
    header.pack(fill=tk.X, pady=(0, 10))
    ttk.Label(header, text="Thực Xuất", style="Title.TLabel").pack(side=tk.LEFT)
    ttk.Label(header, text=f"NVIDIA NIM  ·  v{__version__}", style="Subtitle.TLabel").pack(side=tk.LEFT, padx=12)
    ttk.Button(header, text="Hướng dẫn", command=self.show_help).pack(side=tk.RIGHT)
    self.update_btn = ttk.Button(header, text="Kiểm tra cập nhật", command=self.on_update_click)
    self.update_btn.pack(side=tk.RIGHT, padx=8)
    self.nim_usage_label = ttk.Label(header, style="Subtitle.TLabel")
    self.nim_usage_label.pack(side=tk.RIGHT, padx=(4, 8))
    self._show_nim_usage(0, None)

    connection = ttk.Frame(shell, style="Card.TFrame", padding=(14, 10))
    connection.pack(fill=tk.X, pady=(0, 12))
    connection.columnconfigure(0, weight=1)
    connection.columnconfigure(1, weight=1)
    ttk.Label(connection, text="API key NVIDIA", style="Field.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(connection, text="Model đọc ảnh", style="Field.TLabel").grid(row=0, column=1, sticky="w", padx=(16, 0))
    self.api_key_var = tk.StringVar()
    key_entry = ttk.Entry(connection, textvariable=self.api_key_var, show="•", width=30)
    key_entry.grid(row=1, column=0, sticky="ew", pady=(4, 1))
    self._show_key = tk.BooleanVar(value=False)
    ttk.Checkbutton(
      connection, text="Hiện key", variable=self._show_key,
      command=lambda: key_entry.configure(show="" if self._show_key.get() else "•"),
    ).grid(row=1, column=2, padx=(10, 0))
    ttk.Label(connection, text="Key dùng trong phiên này", style="Muted.TLabel").grid(row=2, column=0, sticky="w")
    saved_model = load_config().get("nim_model", DEFAULT_MODEL)
    model_label = next((label for label, model in MODELS.items() if model == saved_model), next(iter(MODELS)))
    self.model_var = tk.StringVar(value=model_label)
    model_picker = ttk.Combobox(connection, textvariable=self.model_var, values=list(MODELS), state="readonly", width=34)
    model_picker.grid(row=1, column=1, sticky="ew", padx=(16, 0))
    model_picker.bind("<<ComboboxSelected>>", lambda _event: set_config_value("nim_model", MODELS[self.model_var.get()]))

    footer = ttk.Frame(shell, style="Canvas.TFrame")
    footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
    self.status = ttk.Label(footer, text="Sẵn sàng", style="Subtitle.TLabel", anchor=tk.W)
    self.status.pack(fill=tk.X)
    self.progress = ttk.Progressbar(footer, mode="indeterminate")
    self.progress.pack(fill=tk.X, pady=(5, 0))
    ttk.Sizegrip(footer).pack(side=tk.RIGHT)

    workspace = ttk.Frame(shell, style="Canvas.TFrame")
    workspace.pack(fill=tk.BOTH, expand=True)
    workspace.rowconfigure(0, weight=1)
    workspace.columnconfigure(1, weight=1)
    inputs = ttk.Frame(workspace, style="Card.TFrame", padding=14)
    inputs.grid(row=0, column=0, sticky="ns", padx=(0, 12))

    ttk.Label(inputs, text="Phiếu và ảnh", style="Section.TLabel").pack(anchor=tk.W)
    excel_header = ttk.Frame(inputs)
    excel_header.pack(fill=tk.X, pady=(14, 4))
    ttk.Label(excel_header, text="Excel gốc", style="Field.TLabel").pack(side=tk.LEFT)
    self.excel_summary = ttk.Label(excel_header, text="0 phiếu", style="Muted.TLabel")
    self.excel_summary.pack(side=tk.RIGHT)
    self.job_list = self._input_list(inputs, height=4)
    self.job_list.bind("<<ListboxSelect>>", self._on_job_select)
    excel_actions = ttk.Frame(inputs)
    excel_actions.pack(fill=tk.X, pady=(5, 0))
    ttk.Button(excel_actions, text="Thêm Excel…", command=self.add_excels).pack(side=tk.LEFT)
    ttk.Button(excel_actions, text="Bỏ phiếu", command=self.remove_selected_job).pack(side=tk.RIGHT)

    image_header = ttk.Frame(inputs)
    image_header.pack(fill=tk.X, pady=(14, 4))
    ttk.Label(image_header, text="Ảnh phiếu", style="Field.TLabel").pack(side=tk.LEFT)
    self.img_label = ttk.Label(image_header, text="0 ảnh", style="Muted.TLabel")
    self.img_label.pack(side=tk.RIGHT)
    self.img_list = self._input_list(inputs, height=5)
    self.img_list.configure(selectmode=tk.EXTENDED)
    image_actions = ttk.Frame(inputs)
    image_actions.pack(fill=tk.X, pady=(5, 0))
    ttk.Button(image_actions, text="Thêm ảnh…", command=self.add_images).pack(side=tk.LEFT)
    ttk.Button(image_actions, text="Bỏ ảnh", command=self.remove_selected_image).pack(side=tk.RIGHT)
    ttk.Button(image_actions, text="Xóa tất cả ảnh", command=self.clear_all_images).pack(
      side=tk.RIGHT, padx=(0, 6)
    )

    self.clear_btn = ttk.Button(inputs, text="Reset app", command=self.reset_app)
    self.clear_btn.pack(fill=tk.X, pady=(6, 0))
    actions = ttk.Frame(inputs)
    actions.pack(fill=tk.X, side=tk.BOTTOM, pady=(12, 0))
    self.stop_btn = ttk.Button(actions, text="Dừng", command=self.stop_process, state=tk.DISABLED)
    self.stop_btn.pack(side=tk.RIGHT, padx=(6, 0))
    self.run_btn = ttk.Button(actions, text="Đọc phiếu và xuất Excel", style="Primary.TButton", command=self.run_process)
    self.run_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

    preview = ttk.Frame(workspace, style="Card.TFrame", padding=14)
    preview.grid(row=0, column=1, sticky="nsew")
    result_header = ttk.Frame(preview)
    result_header.pack(fill=tk.X, pady=(0, 10))
    ttk.Label(result_header, text="Kết quả", style="Section.TLabel").pack(side=tk.LEFT)
    ttk.Button(result_header, text="Thư mục xuất", command=self.open_output_folder).pack(side=tk.RIGHT)
    self.reexport_btn = ttk.Button(result_header, text="Xuất lại", command=self.reexport_results, state=tk.DISABLED)
    self.reexport_btn.pack(side=tk.RIGHT, padx=8)

    edit_bar = ttk.Frame(preview)
    edit_bar.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(edit_bar, text="File kết quả", style="Field.TLabel").pack(side=tk.LEFT)
    self.output_var = tk.StringVar()
    self.output_combo = ttk.Combobox(edit_bar, textvariable=self.output_var, state="readonly", width=14)
    self.output_combo.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=8)
    self.output_combo.bind("<<ComboboxSelected>>", self._on_output_selected)
    ttk.Button(edit_bar, text="Mở file…", command=self.browse_output_file).pack(side=tk.LEFT)
    ttk.Button(edit_bar, text="Lưu bản sao…", command=self._save_output_copy).pack(side=tk.LEFT, padx=(6, 0))
    self._output_files: list[Path] = []

    details = ttk.Frame(preview)
    details.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))
    ttk.Label(details, text="Nháy đúp để sửa · Enter để lưu · Ví dụ: 3*8", style="Muted.TLabel").pack(side=tk.LEFT)
    ttk.Label(details, text="Vàng: cần kiểm tra", style="Warning.TLabel").pack(side=tk.RIGHT)
    self.folder_label = ttk.Label(preview, style="Muted.TLabel", anchor=tk.W, width=1)
    self.folder_label.pack(fill=tk.X, side=tk.BOTTOM, pady=(4, 0))
    self._refresh_folder_label()

    table = ttk.Frame(preview)
    table.pack(fill=tk.BOTH, expand=True)
    table.rowconfigure(0, weight=1)
    table.columnconfigure(0, weight=1)
    cols = ("stt", "code", "name", "unit", "ordered", "raw", "qty")
    self.tree = ttk.Treeview(table, columns=cols, show="headings", height=10)
    for column, label, width in (
      ("stt", "STT", 45), ("code", "Mã SP", 106), ("name", "Tên hàng", 270),
      ("unit", "ĐVT", 90), ("ordered", "Đặt", 48), ("raw", "Đọc từ ảnh", 96),
      ("qty", "Thực xuất", 82),
    ):
      self.tree.heading(column, text=label)
      self.tree.column(column, width=width, minwidth=width, stretch=column == "name",
                       anchor=tk.CENTER if column in ("stt", "ordered", "qty") else tk.W)
    self.tree.tag_configure("skip", foreground="#637580", background="#f5f8f9")
    self.tree.tag_configure("warn", background="#fff0cc")
    self.tree.bind("<Double-1>", self._edit_selected_row)
    vscroll = ttk.Scrollbar(table, orient=tk.VERTICAL, command=self.tree.yview)
    hscroll = ttk.Scrollbar(table, orient=tk.HORIZONTAL, command=self.tree.xview)
    self.tree.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
    self.tree.grid(row=0, column=0, sticky="nsew")
    vscroll.grid(row=0, column=1, sticky="ns")
    hscroll.grid(row=1, column=0, sticky="ew")
    self._update_status_ready()

  @staticmethod
  def _input_list(parent: ttk.Frame, height: int) -> tk.Listbox:
    frame = ttk.Frame(parent)
    frame.pack(fill=tk.X)
    listing = tk.Listbox(
      frame, height=height, width=27, exportselection=False,
      font=("Segoe UI", 10), highlightthickness=1, highlightbackground="#d6e1e5",
      borderwidth=0, foreground="#172b36", selectbackground="#d3ebe3", selectforeground="#172b36",
    )
    scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=listing.yview)
    listing.configure(yscrollcommand=scrollbar.set)
    listing.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    return listing

  def _show_nim_usage(self, previous: int, usage: NimUsage | None) -> None:
    self._nim_requests = previous + (usage.request_count if usage else 0)
    remaining = "—"
    if usage is not None and usage.remaining is not None:
      remaining = str(usage.remaining)
    suffix = " · HTTP 429" if usage is not None and usage.http_status == 429 else ""
    self.nim_usage_label.config(text=f"API · {self._nim_requests} gọi · còn {remaining}{suffix}")

  def _selected_job_index(self) -> int | None:
    sel = self.job_list.curselection()
    return int(sel[0]) if sel else None

  def _selected_job(self) -> OrderJob | None:
    idx = self._selected_job_index()
    if idx is None or idx >= len(self.jobs):
      return None
    return self.jobs[idx]

  def _job_label(self, job: OrderJob) -> str:
    return f"{self._order_name(job.excel_path)} · {job.product_count} SP"

  @staticmethod
  def _order_name(excel: Path) -> str:
    return pdn_from_excel(excel) or excel.stem

  def _refresh_job_list(self, select_index: int | None = None) -> None:
    self.job_list.delete(0, tk.END)
    for job in self.jobs:
      self.job_list.insert(tk.END, self._job_label(job))
    self.excel_summary.config(text=f"{len(self.jobs)} phiếu")
    if self.jobs:
      idx = select_index if select_index is not None else min(
        self._selected_job_index() or 0, len(self.jobs) - 1
      )
      self.job_list.selection_set(idx)
      self.job_list.activate(idx)
      self._on_job_select()
    else:
      self.preview_job = None
      self._standalone_sheet = None
      self._refresh_image_list()
      self._refresh_output_files()
      self._fill_preview()

  def _on_job_select(self, _event=None) -> None:
    job = self._selected_job()
    self.preview_job = job
    self._standalone_sheet = None
    self._refresh_image_list()
    result = self.results_by_excel.get(job.excel_path) if job else None
    path = result.output_path if result else None
    self._refresh_output_files(path)
    self._fill_preview()

  def _refresh_image_list(self) -> None:
    selected = self.img_list.curselection()
    scroll = self.img_list.yview()[0]
    self.img_list.delete(0, tk.END)
    for photo in self.all_images:
      self.img_list.insert(tk.END, photo.name)
    for index in selected:
      if index < len(self.all_images):
        self.img_list.selection_set(index)
    self.img_list.yview_moveto(scroll)
    self.img_label.config(text=f"{len(self.all_images)} ảnh")

  def _on_close(self) -> None:
    self._cancel_event.set()
    self.destroy()

  def _refresh_folder_label(self) -> None:
    try:
      folder = output_dir()
    except OSError as exc:
      self.folder_label.config(text=f"Thư mục xuất: lỗi — {exc}")
      return
    self.folder_label.config(text=f"Thư mục xuất: {folder}")

  def _job_output(self, job: OrderJob) -> Path | None:
    result = self.results_by_excel.get(job.excel_path)
    if result is not None and result.output_path.is_file():
      return result.output_path
    folder = output_dir()
    for include_name in (False, True):
      path = default_output_path(job.excel_path, out_dir=folder, include_name=include_name)
      if path.is_file():
        return path
    return None

  def _refresh_output_files(self, select: Path | None = None) -> None:
    """Chỉ hiện một file kết quả cho mỗi phiếu đang mở và file mở tay."""
    files = [path for job in self.jobs if (path := self._job_output(job)) is not None]
    if self._extra_output is not None and self._extra_output.is_file():
      files.append(self._extra_output)
    files = list(dict.fromkeys(files))
    self._output_files = files
    self.output_combo["values"] = [p.name for p in files]
    if select in files:
      self.output_combo.current(files.index(select))
    else:
      self.output_var.set("")

  def _on_output_selected(self, _event=None) -> None:
    index = self.output_combo.current()
    if 0 <= index < len(self._output_files):
      path = self._output_files[index]
      job = next((job for job in self.jobs if self._job_output(job) == path), None)
      if not self._show_output(path, job):
        previous = self._standalone_sheet.path if self._standalone_sheet else (
          self._job_output(self.preview_job) if self.preview_job else None
        )
        self._refresh_output_files(previous)

  def _save_output_copy(self) -> None:
    job = self.preview_job
    result = self.results_by_excel.get(job.excel_path) if job else None
    path = self._standalone_sheet.path if self._standalone_sheet else (
      result.output_path if result else None
    )
    if path is None:
      messagebox.showinfo("Chưa có file", "Hãy chọn một file kết quả để lưu bản sao.")
      return
    picked = filedialog.asksaveasfilename(
      parent=self, title="Lưu bản sao", initialdir=str(path.parent),
      initialfile=f"{path.stem}_sua.xlsx", defaultextension=".xlsx",
      filetypes=[("Excel", "*.xlsx")],
    )
    if not picked or Path(picked).resolve() == path.resolve():
      return
    try:
      target = Path(picked)
      shutil.copy2(path, target)
      sidecar = path.with_suffix(".json")
      if sidecar.is_file():
        shutil.copy2(sidecar, target.with_suffix(".json"))
    except OSError as exc:
      messagebox.showerror("Không lưu được bản sao", str(exc))
      return
    self.status.config(text=f"Đã lưu bản sao {target.name}")

  def browse_output_file(self) -> None:
    """Chọn 1 file kết quả ở bất kỳ đâu trên máy để sửa."""
    initial = output_dir()
    path = filedialog.askopenfilename(
      title="Chọn file kết quả đã xuất để sửa",
      initialdir=str(initial) if initial.is_dir() else None,
      filetypes=[("Excel", "*.xlsx")],
    )
    if not path:
      return
    target = Path(path).resolve()
    job = next((job for job in self.jobs if self._job_output(job) == target), None)
    if self._show_output(target, job):
      if job is None:
        self._extra_output = target
      self._refresh_output_files(target)

  def _show_output(self, path: Path, job: OrderJob | None) -> bool:
    """Mở file trong bảng hiện tại; lấy STT và ĐVT từ Excel gốc khi ghép được."""
    result = self.results_by_excel.get(job.excel_path) if job else None
    sheet = None
    if result is None or result.output_path != path:
      try:
        sheet = load_sheet(path)
      except OutputFormatError as exc:
        messagebox.showerror("Không mở được file", str(exc))
        self._fill_preview()
        return False
      sidecar = path.with_suffix(".json")
      names, source = load_sidecar(sidecar)
      source_path = job.excel_path if job else source
      if source_path:
        try:
          products = load_products(source_path)
        except Exception:
          products = []
        sheet.apply_names({p.code: p.name for p in products})
        if job and products:
          try:
            mapping = load_mapping(sidecar)
          except (OSError, ValueError, TypeError):
            mapping = {}
          restored = restore_output_mapping(products, sheet, mapping)
          if restored is not None:
            included, skipped = build_output(products, restored)
            self.results_by_excel[job.excel_path] = ProcessResult(
              job.excel_path, path, len(products), len(restored), included,
              skipped, restored, sheet.has_name,
            )
            sheet = None
      if sheet is not None:
        sheet.apply_names(names)

    self.preview_job = job
    self._standalone_sheet = sheet
    self.job_list.selection_clear(0, tk.END)
    if job is not None:
      index = self.jobs.index(job)
      self.job_list.selection_set(index)
      self.job_list.activate(index)
    self._fill_preview()
    self.status.config(text=f"Đang sửa {path.name} — nháy đúp một dòng rồi nhấn Enter để lưu")
    return True

  def open_output_folder(self) -> None:
    """Mở thư mục chứa file kết quả trong Explorer (tạo nếu chưa có)."""
    try:
      folder = output_dir()
    except OSError as exc:
      messagebox.showerror("Không mở được thư mục", str(exc))
      return
    self._refresh_folder_label()
    if reveal_in_folder(folder):
      self.status.config(text=f"Đã mở thư mục xuất: {folder}")
      return
    # Không mở được Explorer (bản Windows tối giản, quyền hạn, shell lỗi...)
    # → nói rõ đường dẫn để người dùng mở bằng tay.
    messagebox.showwarning(
      "Không mở được thư mục",
      f"Windows không mở được Explorer cho thư mục:\n{folder}\n\n"
      "Bạn mở bằng tay đường dẫn trên (hoặc Copy rồi dán vào thanh địa chỉ).",
    )
    self.status.config(text=f"Thư mục xuất (mở bằng tay): {folder}")


  def _check_update_silent(self) -> None:
    self._check_update_async(interactive=False)

  def on_update_click(self) -> None:
    if self._busy:
      messagebox.showinfo("Đang xử lý", "Chờ app đọc ảnh xong rồi hãy cập nhật.")
      return
    if self._update_info is not None:
      self._confirm_update()
      return
    self._check_update_async(interactive=True)

  def _set_update_button(self, text: str, state: str = tk.NORMAL) -> None:
    self.update_btn.config(text=text, state=state)

  def _check_update_async(self, interactive: bool) -> None:
    if self._checking_update or self._updating:
      return
    self._checking_update = True
    if interactive:
      self._set_update_button("Đang kiểm tra...", tk.DISABLED)
      self.status.config(text="Đang kiểm tra bản cập nhật...")

    def worker() -> None:
      info: UpdateInfo | None = None
      err: Exception | None = None
      try:
        info = check_for_update()
      except Exception as exc:
        err = exc
      self.after(0, lambda: self._on_update_checked(info, err, interactive))

    threading.Thread(target=worker, daemon=True).start()

  def _on_update_checked(
    self,
    info: UpdateInfo | None,
    err: Exception | None,
    interactive: bool,
  ) -> None:
    self._checking_update = False
    if info is not None:
      self._update_info = info
      self._set_update_button(f"Cập nhật lên v{info.version}")
      self.status.config(
        text=f"Có bản mới v{info.version} (đang dùng v{__version__}) "
        f"— bấm «Cập nhật lên v{info.version}»"
      )
      if interactive:
        self._confirm_update()
      return

    self._update_info = None
    self._set_update_button("Kiểm tra cập nhật")
    if not interactive:
      return
    if err is not None:
      messagebox.showwarning(
        "Không kiểm tra được",
        "Không kết nối được GitHub để kiểm tra bản mới.\n\n"
        f"{str(err)[:300]}\n\nApp vẫn dùng bình thường.",
      )
    else:
      messagebox.showinfo(
        "Bản mới nhất",
        f"Bạn đang dùng bản mới nhất (v{__version__}).",
      )

  def _confirm_update(self) -> None:
    info = self._update_info
    if info is None:
      return
    if not getattr(sys, "frozen", False):
      messagebox.showinfo(
        "Bản chạy thử",
        "Tự cập nhật chỉ chạy trên bản .exe đã đóng gói.\n"
        f"Bản hiện tại: v{__version__}.",
      )
      return
    if not can_write_app_dir():
      messagebox.showerror(
        "Không cập nhật được",
        "Thư mục app không cho ghi (ví dụ nằm trong Program Files).\n\n"
        "Hãy chuyển cả thư mục ThucXuatTool sang Desktop hoặc ổ D: rồi mở lại app.",
      )
      return

    notes = (info.notes or "").strip()
    if len(notes) > 900:
      notes = notes[:900] + "..."
    mb = info.size / (1024 * 1024) if info.size else 0
    text = (
      f"Bản đang dùng: v{__version__}\n"
      f"Bản mới: v{info.version}\n"
      + (f"Dung lượng: {mb:.1f} MB\n" if mb else "")
      + (f"\n{notes}\n" if notes else "")
      + "\nApp sẽ tải, thay thế rồi tự mở lại. Tiếp tục?"
    )
    if not messagebox.askyesno("Cập nhật phần mềm", text):
      return
    self._download_and_apply(info)

  def _download_and_apply(self, info: UpdateInfo) -> None:
    if self._updating:
      return
    self._updating = True
    self.run_btn.config(state=tk.DISABLED)
    self._set_update_button("Đang tải...", tk.DISABLED)
    self.progress.stop()
    self.progress.config(mode="determinate", maximum=100, value=0)
    self.status.config(text=f"Đang tải bản v{info.version}...")

    dest = work_dir() / f"ThucXuatTool-v{info.version}.zip"

    def on_progress(done: int, total: int) -> None:
      self.after(0, lambda: self._set_download_progress(info, done, total))

    def worker() -> None:
      try:
        zip_path = download_update(info, dest, on_progress=on_progress)
        staged = stage_zip(zip_path, info.version)
      except Exception as exc:
        self.after(0, lambda e=exc: self._on_update_failed(e))
        return
      self.after(0, lambda: self._finish_update(staged))

    threading.Thread(target=worker, daemon=True).start()

  def _set_download_progress(self, info: UpdateInfo, done: int, total: int) -> None:
    pct = int(done * 100 / total) if total else 0
    self.progress.config(value=pct)
    if total:
      self.status.config(
        text=f"Đang tải v{info.version}: {pct}% "
        f"({done / 1048576:.0f}/{total / 1048576:.0f} MB)"
      )
    else:
      self.status.config(text=f"Đang tải v{info.version}: {done / 1048576:.0f} MB")

  def _reset_update_ui(self) -> None:
    self._updating = False
    self.run_btn.config(state=tk.NORMAL)
    self.progress.stop()
    self.progress.config(mode="indeterminate", value=0)
    if self._update_info is not None:
      self._set_update_button(f"Cập nhật lên v{self._update_info.version}")
    else:
      self._set_update_button("Kiểm tra cập nhật")

  def _on_update_failed(self, exc: Exception) -> None:
    self._reset_update_ui()
    self.status.config(text="Cập nhật thất bại — vẫn dùng bản hiện tại")
    messagebox.showerror(
      "Cập nhật thất bại",
      f"{exc}\n\nApp vẫn dùng bản cũ bình thường. Thử lại sau.",
    )

  def _finish_update(self, staged: Path) -> None:
    self.status.config(text="Đã tải xong — đang khởi động lại để hoàn tất...")
    try:
      launch_updater(staged)
    except Exception as exc:
      self._on_update_failed(exc)
      return
    messagebox.showinfo(
      "Hoàn tất",
      "App sẽ đóng và mở lại bản mới trong vài giây.\n\n"
      "Nếu không thấy app mở lại, hãy mở ThucXuatTool.exe trong thư mục cũ.",
    )
    self.after(300, self.destroy)

  def reexport_results(self) -> None:
    """Xuất lại các phiếu đã xử lý theo tuỳ chọn cột hiện tại (không đọc lại ảnh)."""
    if self._busy:
      return
    if not self.results_by_excel:
      messagebox.showinfo("Chưa có kết quả", "Bấm «Tạo file Excel» trước.")
      return
    include_name = False
    try:
      folder = output_dir()
    except OSError as exc:
      messagebox.showerror("Không ghi được file", str(exc))
      return
    written: list[Path] = []
    for job in self.jobs:
      result = self.results_by_excel.get(job.excel_path)
      if result is None:
        continue
      try:
        products = load_products(job.excel_path)
        included, skipped = build_output(products, result.thuc_xuat_map)
        out = default_output_path(
          job.excel_path, out_dir=folder, include_name=include_name
        )
        save_output(included, out, include_name=include_name)
        save_mapping(
          out.with_suffix(".json"),
          result.thuc_xuat_map,
          meta={"source": str(job.excel_path)},
          names={row.code: row.name for row in (included + skipped) if row.name},
        )
      except Exception as exc:
        messagebox.showerror("Lỗi xuất lại", f"{job.excel_path.name}:\n{exc}")
        continue
      result.output_path = out
      result.include_name = include_name
      result.included = included
      result.skipped = skipped
      written.append(out)

    if not written:
      return
    self._on_job_select()
    if auto_open_output_folder():
      reveal_outputs(written)
    kind = "kèm Tên sản phẩm" if include_name else "2 cột (Mã + Thực xuất)"
    self.status.config(text=f"Đã xuất lại {len(written)} file — {kind}")
    lines = "\n".join(f"• {p.name}" for p in written[:5])
    messagebox.showinfo(
      "Đã xuất lại",
      f"{len(written)} file ({kind}) trong:\n{folder}\n\n{lines}",
    )

  def _update_status_ready(self) -> None:
    if self.jobs and self.all_images:
      self.status.config(
        text=f"Sẵn sàng: {len(self.jobs)} Excel + {len(self.all_images)} ảnh"
      )
    elif self.jobs:
      self.status.config(text=f"{len(self.jobs)} phiếu — thêm ảnh phiếu")
    elif self.all_images:
      self.status.config(text=f"{len(self.all_images)} ảnh — thêm Excel gốc")
    else:
      self.status.config(text="Sẵn sàng — thêm Excel gốc và ảnh phiếu")
    if self.all_images and not warp_available():
      self.status.config(
        text="CẢNH BÁO: thiếu OpenCV (cv2) — ảnh không được làm phẳng/che cột số lượng, kết quả sẽ kém chính xác"
      )

  def add_excels(self) -> None:
    if self._busy:
      return
    paths = filedialog.askopenfilenames(
      title="Chọn Excel gốc (có thể nhiều phiếu)",
      filetypes=[("Excel", "*.xlsx")],
    )
    if not paths:
      return

    added = 0
    first_new: int | None = None
    existing = {j.excel_path.resolve() for j in self.jobs}
    for path in paths:
      pp = Path(path).resolve()
      if pp in existing:
        continue
      # Người dùng hay chọn nhầm file KẾT QUẢ đã xuất (cũng là .xlsx) làm
      # Excel gốc — app sẽ đọc sai cột. Nhận ra và nhắc thay vì tạo phiếu rác.
      try:
        load_sheet(pp)
      except OutputFormatError:
        pass
      else:
        messagebox.showwarning(
          "Đây là file kết quả",
          f"«{pp.name}» là file kết quả app đã xuất (chỉ có Mã SP + Thực xuất).\n\n"
          "Hãy chọn file Excel gốc (DanhSachChiTietDatHangNhap_*.xlsx).",
        )
        continue
      try:
        products = load_products(pp)
      except Exception as exc:
        messagebox.showerror("Lỗi đọc Excel", f"{pp.name}:\n{exc}")
        continue
      if not products:
        messagebox.showwarning("File trống", f"{pp.name} không có sản phẩm.")
        continue
      if first_new is None:
        first_new = len(self.jobs)
      self.jobs.append(OrderJob(excel_path=pp, product_count=len(products)))
      existing.add(pp)
      added += 1

    if added:
      self._refresh_job_list(select_index=first_new)
      self._update_status_ready()

  def remove_selected_job(self) -> None:
    if self._busy:
      return
    idx = self._selected_job_index()
    if idx is None:
      return
    removed = self.jobs.pop(idx)
    self.results_by_excel.pop(removed.excel_path, None)
    self._manual_edits.pop(removed.excel_path, None)
    self._refresh_job_list(select_index=min(idx, len(self.jobs) - 1) if self.jobs else None)
    if not self.results_by_excel:
      self.reexport_btn.config(state=tk.DISABLED)
    self._update_status_ready()

  def reset_app(self) -> None:
    """Đặt lại phiên xử lý, giữ file đã lưu và cấu hình kết nối."""
    if self._busy:
      messagebox.showinfo(
        "Đang xử lý",
        "Chờ app đọc ảnh xong hoặc bấm «Dừng» trước khi reset.",
      )
      return
    if (self.jobs or self.all_images or self._extra_output is not None) and not messagebox.askyesno(
      "Reset app",
      "Bắt đầu lượt mới và bỏ Excel, ảnh, bảng kết quả đang mở?\n\n"
      "File đã lưu, API key và model đang chọn được giữ lại.",
      parent=self,
    ):
      return

    self.jobs.clear()
    self.all_images.clear()
    self.results_by_excel.clear()
    self._ocr_resume = VisionResume()
    self._manual_edits.clear()
    self.preview_job = None
    self._standalone_sheet = None
    self._extra_output = None
    self.job_list.delete(0, tk.END)
    self.img_list.delete(0, tk.END)
    self.excel_summary.config(text="0 phiếu")
    self.img_label.config(text="0 ảnh")
    self.progress.stop()
    self.progress.config(value=0)
    self._cancel_event.clear()
    self.reexport_btn.config(state=tk.DISABLED)
    self._refresh_output_files()
    self._fill_preview()
    self.tree.xview_moveto(0)
    self.status.config(text="Đã reset — thêm Excel gốc và ảnh để bắt đầu lượt mới.")

  def add_images(self) -> None:
    if self._busy:
      return
    paths = filedialog.askopenfilenames(
      title="Chọn tất cả ảnh của các phiếu",
      filetypes=[
        ("Ảnh", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"),
        ("Tất cả", "*.*"),
      ],
    )
    if not paths:
      return
    existing = set(self.all_images)
    added = 0
    for p in paths:
      photo = Path(p).resolve()
      if photo in existing:
        continue
      existing.add(photo)
      self.all_images.append(photo)
      added += 1
    self._refresh_image_list()
    self._update_status_ready()
    if added:
      self.status.config(
        text=f"Đã thêm {added} ảnh — tổng {len(self.all_images)} ảnh"
      )
    else:
      self.status.config(text="Các ảnh đã chọn đã có trong danh sách.")

  def remove_selected_image(self) -> None:
    if self._busy:
      return
    sel = self.img_list.curselection()
    if not sel:
      return
    for idx in reversed(sel):
      self.all_images.pop(idx)
    self.img_list.selection_clear(0, tk.END)
    self._refresh_image_list()
    self._update_status_ready()

  def clear_all_images(self) -> None:
    """Bỏ toàn bộ ảnh đã thêm (giữ Excel và kết quả đã đọc)."""
    if self._busy:
      messagebox.showinfo(
        "Đang xử lý",
        "Chờ app đọc ảnh xong (hoặc bấm «Dừng») rồi hãy xóa ảnh.",
      )
      return
    if not self.all_images:
      self.status.config(text="Chưa có ảnh nào để xóa")
      return
    n_images = len(self.all_images)
    if not messagebox.askyesno(
      "Xóa tất cả ảnh",
      f"Bỏ {n_images} ảnh khỏi danh sách?\n\n"
      "• Chỉ xóa khỏi app — KHÔNG xóa file trên máy.\n"
      "• Excel, kết quả đã đọc và file trong thư mục xuất vẫn giữ nguyên.",
    ):
      return
    self.all_images.clear()
    self._refresh_image_list()
    self._update_status_ready()
    self.status.config(
      text=f"Đã xóa {n_images} ảnh — thêm ảnh mới để chạy tiếp"
    )

  def show_help(self) -> None:
    messagebox.showinfo(
      "Cách dùng",
      "1. Thêm file DanhSachChiTietDatHangNhap_*.xlsx (1 hoặc nhiều phiếu)\n"
      "2. Thêm tất cả ảnh. App tự nhận diện ảnh thuộc phiếu nào.\n"
      "3. Bấm «Đọc phiếu và xuất Excel» để chạy cả lô.\n\n"
      "AI đọc ảnh, lấy STT → Thực xuất, rồi tạo file:\n"
      "Mã sản phẩm + Thực xuất.\n\n"
      "File kết quả nằm trong thư mục «KetQua» cạnh app.\n"
      "Bấm «Mở thư mục xuất» để mở thư mục đó.\n\n"
      "File xuất gồm 2 cột: Mã sản phẩm + Thực xuất.\n"
      "Tên hàng vẫn hiện trong bảng «Kết quả» và khi sửa file đã xuất.\n\n"
      "Đã chạy xong rồi muốn kiểm lại: nháy đúp 1 dòng để sửa,\n"
      "app tự lưu lại Excel.\n\n"
      "Quy tắc:\n"
      "• X (hoặc nét bị đọc thành Y) → bỏ dòng\n"
      "• Có dấu nhân thì mới nhân: 3T*3 → 9, 5T x 24 → 120\n"
      "• Không có dấu nhân: giữ nguyên số, bỏ chữ:\n"
      "    1T → 1, 4T → 4, 12 → 12, 30cm → 30\n"
      "  (KHÔNG nhân theo hệ số trong ĐVT)\n"
      "• Nét giống ✓ trên ảnh được coi là X → bỏ dòng\n"
      "• Gặp ảnh lỗi: bấm lại nút đọc để thử những ảnh chưa đọc được.\n\n"
      "Dòng tô VÀNG = số xuất lớn bất thường so với SL đặt\n"
      "(nghi AI đọc sai) — nháy đúp để sửa tay.\n"
      "Sửa file ĐÃ XUẤT (kể cả file của lần chạy trước):\n"
      "Chọn file ở khung 3 để hiện ngay trong bảng bên dưới.\n"
      "Nháy đúp một dòng, nhập 3*8, 10+2, 12/3 hoặc số rồi nhấn Enter để lưu.\n"
      "Nút «Reset app» đóng dữ liệu đang mở để bắt đầu lượt mới.\n"
      "File trên máy, API key và model đang chọn được giữ lại.\n\n"
      "AI: dán API key NVIDIA ở đầu cửa sổ và chọn model.\n"
      "Tự động ưu tiên Super VL và chuyển sang Nano Omni nếu endpoint gặp lỗi tạm thời.\n"
      "Khi chạy, ảnh đã tiền xử lý được gửi qua mạng đến NVIDIA NIM.\n\n"
      "Cập nhật: app tự kiểm tra bản mới khi mở (cần mạng).\n"
      "Có bản mới thì nút «Cập nhật lên v...» hiện ra — bấm là\n"
      "app tự tải, thay thế và mở lại. File trong KetQua giữ nguyên.\n\n"
      "Kiểm tra kết quả OCR trước khi nhập vào KiotViet.\n"
      "Xem bảng «Kết quả»; nháy đúp 1 dòng để sửa «Đọc từ ảnh»\n"
      "(app tự lưu lại file Excel).",
    )

  def stop_process(self) -> None:
    if not self._busy:
      return
    self._cancel_event.set()
    self.status.config(text="Đang dừng... (chờ hủy request AI)")
    self.stop_btn.config(state=tk.DISABLED)

  def run_process(self) -> None:
    if self._busy:
      return
    if not self.jobs:
      messagebox.showwarning("Thiếu file", "Thêm ít nhất một file Excel gốc.")
      return
    if not self.all_images:
      messagebox.showwarning("Thiếu ảnh", "Thêm ảnh phiếu trước.")
      return

    excels = [job.excel_path for job in self.jobs]

    previous_requests = self._nim_requests

    def on_usage(usage: NimUsage) -> None:
      self.after(0, self._show_nim_usage, previous_requests, usage)

    try:
      nim = NimVision(MODELS[self.model_var.get()], self.api_key_var.get(), on_usage=on_usage)
    except NimError as exc:
      messagebox.showwarning("Cấu hình NVIDIA NIM", str(exc))
      return

    self._busy = True
    self._cancel_event.clear()
    self.run_btn.config(state=tk.DISABLED)
    self.stop_btn.config(state=tk.NORMAL)
    self.progress.start(12)
    self.status.config(text=f"Đang đọc ảnh với {self.model_var.get()} qua NVIDIA NIM…")

    images = list(self.all_images)
    resume = self._ocr_resume
    manual_edits = {path: dict(edits) for path, edits in self._manual_edits.items()}
    cancel_event = self._cancel_event
    out_dir = output_dir()
    include_name = False

    def worker() -> None:
      err: Exception | None = None
      batch: BatchResult | None = None

      def on_progress(msg: str) -> None:
        self.after(0, lambda m=msg: self.status.config(text=m))

      try:
        batch = process_batch_like_chat(
          excels,
          images,
          on_progress=on_progress,
          cancel_event=cancel_event,
          output_dir=out_dir,
          include_name=include_name,
          nim=nim,
          resume=resume,
          manual_edits=manual_edits,
        )
      except Exception as exc:
        err = exc
      self.after(0, lambda: self._on_done(err, batch))

    threading.Thread(target=worker, daemon=True).start()

  def _on_done(
    self,
    err: Exception | None,
    batch: BatchResult | None,
  ) -> None:
    self._busy = False
    self.progress.stop()
    self.run_btn.config(state=tk.NORMAL)
    self.stop_btn.config(state=tk.DISABLED)
    if err:
      if isinstance(err, CancelledByUser):
        self.status.config(text="Đã dừng")
        messagebox.showinfo("Đã dừng", "Đã hủy đọc ảnh theo yêu cầu.")
        return
      self.status.config(text="Lỗi")
      messagebox.showerror(
        "Lỗi",
        f"{err}\n\nBấm «Đọc phiếu và xuất Excel» để thử lại. Kết quả của ảnh đã đọc được sẽ dùng lại.",
      )
      return
    assert batch is not None
    if not batch.pending_images and not batch.errors:
      self._ocr_resume = VisionResume()

    for result in batch.results:
      self.results_by_excel[result.source_path] = result
      try:
        save_mapping(
          result.output_path.with_suffix(".json"),
          result.thuc_xuat_map,
          meta={"source": str(result.source_path)},
          names={row.code: row.name for row in (result.included + result.skipped) if row.name},
        )
      except OSError:
        pass  # không lưu được JSON phụ cũng không ảnh hưởng file Excel

    if self._selected_job() is None and self.jobs:
      self.job_list.selection_set(0)
    self._on_job_select()
    self.reexport_btn.config(
      state=tk.NORMAL if self.results_by_excel else tk.DISABLED
    )
    if batch.ok_count and auto_open_output_folder():
      reveal_outputs([r.output_path for r in batch.results])

    issue_text = ""
    if batch.warnings:
      lines = [f"• {warning}" for warning in batch.warnings[:8]]
      if len(batch.warnings) > 8:
        lines.append(f"• Và {len(batch.warnings) - 8} ảnh khác cần kiểm tra.")
      issue_text = "\n\nẢnh cần kiểm tra:\n" + "\n".join(lines)
    if batch.pending_images:
      issue_text += (
        f"\n\nCòn {len(batch.pending_images)} ảnh cần đọc lại. "
        "Bấm «Đọc phiếu và xuất Excel» để chạy tiếp."
      )

    if batch.fail_count and batch.ok_count:
      self.status.config(text=f"Xong: {batch.ok_count} OK, {batch.fail_count} lỗi")
      err_lines = "\n".join(f"• {p.name}: {msg}" for p, msg in batch.errors[:5])
      ok_lines = "\n".join(
        f"• {r.output_path.name} ({len(r.included)} dòng)" for r in batch.results[:5]
      )
      messagebox.showwarning(
        "Hoàn tất một phần",
        f"Thành công ({batch.ok_count}):\n{ok_lines}\n\n"
        f"Lỗi ({batch.fail_count}):\n{err_lines}{issue_text}",
      )
      return

    if batch.fail_count:
      self.status.config(text="Lỗi")
      err_lines = "\n".join(f"• {p.name}: {msg}" for p, msg in batch.errors)
      messagebox.showerror("Lỗi", err_lines + issue_text)
      return

    total_rows = sum(len(r.included) for r in batch.results)
    self.status.config(text=f"Đã xuất {batch.ok_count} file · {total_rows} dòng. Kiểm tra số lượng trong bảng kết quả.")
    if issue_text:
      messagebox.showwarning("Đã xuất Excel, còn ảnh cần kiểm tra", f"Đã xuất {batch.ok_count} file.{issue_text}")

  def _fill_preview(self) -> None:
    self._cancel_cell_editor()
    for item in self.tree.get_children():
      self.tree.delete(item)

    if self._standalone_sheet is not None:
      for position, row in enumerate(self._standalone_sheet.rows, 1):
        qty = row.qty if row.qty is not None else ""
        self.tree.insert("", tk.END, values=(position, row.code, row.name, "", "", "", qty))
      return

    job = self.preview_job
    if not job:
      return

    result = self.results_by_excel.get(job.excel_path)
    thuc_xuat_map = result.thuc_xuat_map if result else {}
    try:
      products = load_products(job.excel_path) if job.product_count else []
    except Exception as exc:
      self.status.config(text=f"Không đọc lại được {job.excel_path.name}: {exc}")
      return
    if not products:
      return

    for p in products:
      raw = thuc_xuat_map.get(p.stt, "")
      qty = parse_qty(raw, p.ordered_qty, p.unit)
      tags: list[str] = []
      if qty is None:
        tags.append("skip")
      else:
        expected = expected_qty(p.ordered_qty, raw)
        # Số xuất lớn bất thường so với SL đặt = nghi AI đọc sai (vd "4T" → "47").
        if expected and qty > expected * 5 and qty - expected > 20:
          tags.append("warn")
      self.tree.insert(
        "",
        tk.END,
        values=(
          p.stt,
          p.code,
          p.name[:70],
          p.unit,
          p.ordered_qty,
          raw.removeprefix("="),
          qty if qty is not None else "",
        ),
        tags=tuple(tags),
      )

  def _cancel_cell_editor(self, _event=None) -> None:
    if self._cell_editor is not None:
      entry = self._cell_editor
      self._cell_editor = None
      entry.destroy()

  def _edit_selected_row(self, event) -> None:
    """Đặt ô nhập lên dòng đang xem, chỉ ghi file khi nhấn Enter."""
    if self._busy:
      return
    row_id = self.tree.identify_row(event.y)
    column = "qty" if self.tree.identify_column(event.x) == "#7" else "raw"
    bounds = self.tree.bbox(row_id, column) if row_id else ()
    if not bounds:
      return
    job = self.preview_job
    result = self.results_by_excel.get(job.excel_path) if job else None
    if self._standalone_sheet is None and result is None:
      self.status.config(text="Bấm «Tạo file Excel» trước để sửa từng dòng.")
      return
    self._cancel_cell_editor()
    row = self.tree.item(row_id, "values")
    x, y, width, height = bounds
    entry = ttk.Entry(self.tree, justify=tk.CENTER)
    self._cell_editor = entry
    entry.place(x=x, y=y, width=width, height=height)
    entry.insert(0, row[5] or row[6])
    entry.selection_range(0, tk.END)
    entry.focus_set()
    self.status.config(text="Nhập số hoặc phép tính, ví dụ 3*8, (10+2)/3. Enter lưu, Esc hủy.")

    def commit(_event) -> str:
      try:
        value = entry.get().strip()
        self._save_preview_edit(int(row[0]), value)
      except (ValueError, OSError) as exc:
        self.status.config(text=f"Chưa lưu: {exc}")
        self.bell()
        return "break"
      self._fill_preview()
      match = next(
        (item for item in self.tree.get_children() if self.tree.item(item, "values")[0] == row[0]),
        None,
      )
      if match is not None:
        self.tree.selection_set(match)
        self.tree.see(match)
      self.tree.focus_set()
      return "break"

    entry.bind("<Return>", commit)
    entry.bind("<KP_Enter>", commit)
    entry.bind("<Escape>", self._cancel_cell_editor)
    entry.bind("<FocusOut>", self._cancel_cell_editor)

  def _save_preview_edit(self, stt: int, value: str) -> None:
    value = value.removeprefix("=").strip()
    skip = not value or value.lower() in ("x", "y")
    sheet = self._standalone_sheet
    if sheet is not None:
      rows = list(sheet.rows)
      if skip:
        rows.pop(stt - 1)
      else:
        rows[stt - 1] = replace(rows[stt - 1], qty=calculate_manual_qty(value))
      save_sheet(replace(sheet, rows=rows))
      sheet.rows = rows
      self.status.config(text=f"Đã lưu {sheet.path.name} ({len(rows)} dòng xuất)")
      return

    job = self.preview_job
    result = self.results_by_excel[job.excel_path]
    products = load_products(job.excel_path)
    product = next((p for p in products if p.stt == stt), None)
    if product is None:
      raise ValueError(f"Không tìm thấy STT {stt} trong Excel gốc.")
    mapping = dict(result.thuc_xuat_map)
    if skip:
      if value:
        mapping[stt] = "X"
      else:
        mapping.pop(stt, None)
    else:
      try:
        calculate_manual_qty(value)
      except ValueError:
        if not re.fullmatch(
          r"(?:[✓✔vV>/√]|\d+(?:[.,]\d+)?\s*[a-zà-ỹ]+(?:\s*[*x×]\s*\d+(?:[.,]\d+)?\s*[a-zà-ỹ]*)?)",
          value, re.IGNORECASE,
        ) or parse_qty(value, product.ordered_qty, product.unit) is None:
          raise
        mapping[stt] = value
      else:
        mapping[stt] = f"={value}"

    included, skipped = build_output(products, mapping)
    save_output(included, result.output_path, include_name=result.include_name)
    result.thuc_xuat_map = mapping
    self._manual_edits.setdefault(job.excel_path, {})[stt] = mapping.get(stt)
    result.included = included
    result.skipped = skipped
    result.extracted_stt = len(mapping)
    try:
      save_mapping(
        result.output_path.with_suffix(".json"), mapping,
        meta={"source": str(job.excel_path), "edited": True},
        names={row.code: row.name for row in included + skipped if row.name},
      )
    except OSError:
      self.status.config(text=f"Đã lưu {result.output_path.name}, chưa ghi được JSON phụ.")
      return
    self.status.config(text=f"Đã lưu STT {stt} vào {result.output_path.name} ({len(included)} dòng xuất)")


def run_gui() -> None:
  _ensure_shortcuts_in_background()
  ThucXuatApp().mainloop()


def _ensure_shortcuts_in_background() -> None:
  """Bản .exe đã cài: thiếu lối tắt Desktop/Start Menu thì tự tạo lại."""
  try:
    from thuc_xuat.shortcut import ensure_app_shortcuts

    threading.Thread(target=ensure_app_shortcuts, daemon=True).start()
  except Exception:  # noqa: BLE001 — lối tắt không được làm chết app
    pass
