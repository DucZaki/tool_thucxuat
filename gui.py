# -*- coding: utf-8 -*-
"""GUI: Excel + ảnh phiếu → file Excel kết quả."""
from __future__ import annotations

import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from thuc_xuat.cancel import CancelledByUser
from thuc_xuat.config import (
  auto_open_output_folder,
  export_include_name,
  get_ollama_settings,
  output_dir,
  set_config_value,
)
from thuc_xuat.image_match import pdn_from_excel
from thuc_xuat.image_prep import warp_available
from thuc_xuat.io_json import save_mapping
from thuc_xuat.pipeline import BatchResult, ProcessResult, process_batch_like_chat
from thuc_xuat.processor import (
  build_output,
  default_output_path,
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


@dataclass
class OrderJob:
  excel_path: Path
  product_count: int = 0


class ThucXuatApp(tk.Tk):
  def __init__(self) -> None:
    super().__init__()
    self.title(f"Thực Xuất v{__version__} — Ảnh phiếu + Excel → Excel nhập kho")
    self.geometry("1150x780")
    self.minsize(950, 640)

    self.jobs: list[OrderJob] = []
    self.all_images: list[Path] = []
    self.results_by_excel: dict[Path, ProcessResult] = {}
    self.preview_job: OrderJob | None = None
    self._cancel_event = threading.Event()
    self._busy = False
    self._multi_ok = False
    self.include_name_var = tk.BooleanVar(value=export_include_name())
    self._update_info: UpdateInfo | None = None
    self._checking_update = False
    self._updating = False

    self._build_ui()
    self.after(400, self._startup_check_ollama)
    self.after(1500, self._check_update_silent)

  def _build_ui(self) -> None:
    top = ttk.LabelFrame(self, text="1. Chọn file", padding=10)
    top.pack(fill=tk.X, padx=8, pady=8)

    excel_bar = ttk.Frame(top)
    excel_bar.pack(fill=tk.X, pady=4)
    ttk.Button(excel_bar, text="Thêm Excel gốc...", command=self.add_excels).pack(side=tk.LEFT)
    ttk.Button(excel_bar, text="Xóa phiếu đã chọn", command=self.remove_selected_job).pack(
      side=tk.LEFT, padx=6
    )
    self.excel_summary = ttk.Label(excel_bar, text="0 phiếu", foreground="#666")
    self.excel_summary.pack(side=tk.LEFT, padx=10)

    self.job_list = tk.Listbox(top, height=4, exportselection=False)
    self.job_list.pack(fill=tk.X, pady=4)
    self.job_list.bind("<<ListboxSelect>>", self._on_job_select)

    img_bar = ttk.Frame(top)
    img_bar.pack(fill=tk.X, pady=4)
    ttk.Label(img_bar, text="Ảnh phiếu (thêm tất cả ảnh của các phiếu):").pack(side=tk.LEFT)
    ttk.Button(img_bar, text="Thêm ảnh...", command=self.add_images).pack(side=tk.LEFT, padx=8)
    ttk.Button(img_bar, text="Xóa ảnh đã chọn", command=self.remove_selected_image).pack(
      side=tk.LEFT
    )
    self.img_label = ttk.Label(img_bar, text="0 ảnh", foreground="#666")
    self.img_label.pack(side=tk.LEFT, padx=10)

    self.img_list = tk.Listbox(top, height=4, exportselection=False)
    self.img_list.pack(fill=tk.X, pady=4)

    action = ttk.LabelFrame(self, text="2. Xử lý", padding=10)
    action.pack(fill=tk.X, padx=8, pady=4)

    bar = ttk.Frame(action)
    bar.pack(fill=tk.X)
    self.run_btn = ttk.Button(
      bar,
      text="▶  Tạo file Excel (đọc ảnh + xuất kết quả)",
      command=self.run_process,
    )
    self.run_btn.pack(side=tk.LEFT)
    self.stop_btn = ttk.Button(
      bar,
      text="■  Dừng",
      command=self.stop_process,
      state=tk.DISABLED,
    )
    self.stop_btn.pack(side=tk.LEFT, padx=8)
    ttk.Button(bar, text="Hướng dẫn", command=self.show_help).pack(side=tk.LEFT, padx=8)

    opts = ttk.Frame(action)
    opts.pack(fill=tk.X, pady=(8, 0))
    ttk.Checkbutton(
      opts,
      text="Xuất kèm cột «Tên sản phẩm» (để đối chiếu)",
      variable=self.include_name_var,
      command=self._on_include_name_toggle,
    ).pack(side=tk.LEFT)
    ttk.Button(opts, text="Mở thư mục xuất", command=self.open_output_folder).pack(
      side=tk.LEFT, padx=12
    )
    self.update_btn = ttk.Button(
      opts, text="Kiểm tra cập nhật", command=self.on_update_click
    )
    self.update_btn.pack(side=tk.LEFT)

    self.progress = ttk.Progressbar(action, mode="indeterminate")
    self.progress.pack(fill=tk.X, pady=8)

    preview = ttk.LabelFrame(
      self,
      text="3. Kết quả — nháy đúp 1 dòng để sửa ô «Đọc từ ảnh» (tự lưu lại Excel)",
      padding=8,
    )
    preview.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    result_bar = ttk.Frame(preview)
    result_bar.pack(fill=tk.X, pady=(0, 6))
    self.reexport_btn = ttk.Button(
      result_bar,
      text="Xuất lại file kết quả",
      command=self.reexport_results,
      state=tk.DISABLED,
    )
    self.reexport_btn.pack(side=tk.LEFT)
    self.folder_label = ttk.Label(result_bar, text="", foreground="#666")
    self.folder_label.pack(side=tk.LEFT, padx=10)
    self._refresh_folder_label()

    cols = ("stt", "code", "name", "unit", "ordered", "raw", "qty")
    self.tree = ttk.Treeview(preview, columns=cols, show="headings")
    for c, t, w in [
      ("stt", "STT", 45),
      ("code", "Mã SP", 120),
      ("name", "Tên hàng", 330),
      ("unit", "ĐVT", 110),
      ("ordered", "Đặt", 50),
      ("raw", "Đọc từ ảnh", 110),
      ("qty", "Thực xuất", 70),
    ]:
      self.tree.heading(c, text=t)
      self.tree.column(c, width=w, anchor=tk.CENTER if c in ("stt", "ordered", "qty") else tk.W)

    self.tree.tag_configure("skip", foreground="#999")
    self.tree.tag_configure("edited", background="#eef7ee")
    self.tree.bind("<Double-1>", self._edit_selected_row)

    scroll = ttk.Scrollbar(preview, orient=tk.VERTICAL, command=self.tree.yview)
    self.tree.configure(yscrollcommand=scroll.set)
    self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)

    self.status = ttk.Label(self, text="Sẵn sàng", padding=8, relief=tk.SUNKEN)
    self.status.pack(fill=tk.X, side=tk.BOTTOM)

  def _selected_job_index(self) -> int | None:
    sel = self.job_list.curselection()
    return int(sel[0]) if sel else None

  def _selected_job(self) -> OrderJob | None:
    idx = self._selected_job_index()
    if idx is None or idx >= len(self.jobs):
      return None
    return self.jobs[idx]

  def _job_label(self, job: OrderJob) -> str:
    pdn = pdn_from_excel(job.excel_path) or "?"
    return f"{job.excel_path.name} — {job.product_count} SP — {pdn}"

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
      self._refresh_image_list()
      self._fill_preview()

  def _on_job_select(self, _event=None) -> None:
    job = self._selected_job()
    self.preview_job = job
    self._refresh_image_list()
    self._fill_preview()

  def _refresh_image_list(self) -> None:
    self.img_list.delete(0, tk.END)
    for p in self.all_images:
      self.img_list.insert(tk.END, p.name)
    n_excel = len(self.jobs)
    self.img_label.config(text=f"{len(self.all_images)} ảnh / {n_excel} phiếu")

  def _startup_check_ollama(self) -> None:
    """Mở app là bật Ollama + kiểm tra model."""
    model = get_ollama_settings()["model"]

    def on_status(msg: str) -> None:
      self.after(0, lambda m=msg: self.status.config(text=m))

    def worker() -> None:
      from thuc_xuat.ollama_client import OllamaError, ensure_server, has_model

      try:
        server = ensure_server(on_status=on_status)
      except OllamaError as exc:
        first = str(exc).splitlines()[0]
        self.after(0, lambda e=first: self.status.config(text=f"Ollama: {e}"))
        self.after(0, lambda e=exc: messagebox.showwarning("Chưa chạy được Ollama", str(e)))
        return
      try:
        ready = has_model(model)
      except OllamaError:
        ready = False
      if ready:
        text = (
          f"Ollama sẵn sàng — model {model}"
          if server == "running"
          else f"Đã tự bật Ollama — model {model}"
        )
        self.after(0, lambda: self.status.config(text=text))
        return
      self.after(0, lambda: self._offer_pull_model(model))

    threading.Thread(target=worker, daemon=True).start()

  def _offer_pull_model(self, model: str) -> None:
    if not messagebox.askyesno(
      "Thiếu model",
      f"Ollama đã chạy nhưng chưa có model «{model}».\n\n"
      "Tải ngay bây giờ? (khoảng 3 GB, chỉ tải 1 lần)",
    ):
      self.status.config(
        text=f"Thiếu model {model} — chạy: ollama pull {model}"
      )
      return
    self.status.config(text=f"Đang tải model {model}...")

    def on_status(msg: str) -> None:
      self.after(0, lambda m=msg: self.status.config(text=m))

    def worker() -> None:
      from thuc_xuat.ollama_client import OllamaError, pull_model

      try:
        pull_model(model, on_status=on_status)
      except OllamaError as exc:
        self.after(0, lambda: self.status.config(text="Không tải được model"))
        self.after(0, lambda e=exc: messagebox.showerror("Lỗi tải model", str(e)))
        return
      self.after(0, lambda: self.status.config(text=f"Đã tải xong model {model}"))

    threading.Thread(target=worker, daemon=True).start()

  def _refresh_folder_label(self) -> None:
    try:
      folder = output_dir()
    except OSError as exc:
      self.folder_label.config(text=f"Thư mục xuất: lỗi — {exc}")
      return
    self.folder_label.config(text=f"Thư mục xuất: {folder}")

  def open_output_folder(self) -> None:
    """Mở thư mục chứa file kết quả trong Explorer (tạo nếu chưa có)."""
    folder = output_dir()
    reveal_in_folder(folder)
    self.status.config(text=f"Thư mục xuất: {folder}")

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

  def _on_include_name_toggle(self) -> None:
    enabled = bool(self.include_name_var.get())
    set_config_value("export_include_name", enabled)
    self.status.config(
      text=(
        f"File xuất tiếp theo {'CÓ' if enabled else 'KHÔNG'} cột Tên sản phẩm "
        "— tuỳ chọn đã lưu vào config.json"
      )
    )

  def reexport_results(self) -> None:
    """Xuất lại các phiếu đã xử lý theo tuỳ chọn cột hiện tại (không đọc lại ảnh)."""
    if not self.results_by_excel:
      messagebox.showinfo("Chưa có kết quả", "Bấm «Tạo file Excel» trước.")
      return
    include_name = bool(self.include_name_var.get())
    folder = output_dir()
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
    self._fill_preview()
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
    if self.all_images and not warp_available():
      self.status.config(
        text="CẢNH BÁO: thiếu OpenCV (cv2) — ảnh không được làm phẳng/che cột số lượng, kết quả sẽ kém chính xác"
      )

  def add_excels(self) -> None:
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
    idx = self._selected_job_index()
    if idx is None:
      return
    removed = self.jobs.pop(idx)
    self.results_by_excel.pop(removed.excel_path, None)
    self._refresh_job_list(select_index=min(idx, len(self.jobs) - 1) if self.jobs else None)

  def add_images(self) -> None:
    paths = filedialog.askopenfilenames(
      title="Chọn ảnh phiếu (có thể nhiều trang, nhiều phiếu)",
      filetypes=[
        ("Ảnh", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"),
        ("Tất cả", "*.*"),
      ],
    )
    if not paths:
      return
    for p in paths:
      pp = Path(p)
      if pp not in self.all_images:
        self.all_images.append(pp)
    if not self.jobs:
      self._refresh_image_list()
      self._update_status_ready()
      return
    self._refresh_image_list()
    self._update_status_ready()

  def remove_selected_image(self) -> None:
    sel = self.img_list.curselection()
    if not sel:
      return
    for idx in reversed(sel):
      del self.all_images[idx]
      self.img_list.delete(idx)
    self._update_status_ready()

  def show_help(self) -> None:
    messagebox.showinfo(
      "Cách dùng",
      "1. Thêm file DanhSachChiTietDatHangNhap_*.xlsx (1 hoặc nhiều phiếu)\n"
      "2. Thêm TẤT CẢ ảnh chụp phiếu\n"
      "3. Bấm «Tạo file Excel»\n\n"
      "AI đọc ảnh, lấy STT → Thực xuất, rồi tạo file:\n"
      "Mã sản phẩm + Thực xuất.\n\n"
      "File kết quả nằm trong thư mục «KetQua» cạnh app.\n"
      "Bấm «Mở thư mục xuất» để mở thư mục đó.\n\n"
      "Muốn đối chiếu dễ hơn: tick «Xuất kèm cột Tên sản phẩm»\n"
      "→ file có thêm cột Tên (tên file ..._KemTen.xlsx).\n"
      "Sau khi chạy, đổi tick rồi bấm «Xuất lại file kết quả»\n"
      "để xuất bản còn lại mà không phải đọc ảnh lại.\n\n"
      "Quy tắc:\n"
      "• X → bỏ dòng\n"
      "• Có dấu nhân thì nhân: 3T*3 → 9, 5T x 24 → 120\n"
      "• Ghi đơn vị lẻ thì giữ nguyên: 10 gói → 10, 30kg → 30\n"
      "• Ghi đơn vị ngoài thì nhân hệ số trong ĐVT:\n"
      "    «thùng 40 gói» + 2T → 80\n"
      "    «túi 2kg» + 3T → 3 (2kg là trọng lượng, bỏ qua)\n"
      "• Chỉ tick ✓ → lấy số lượng đặt\n\n"
      "AI: Ollama local (gemma3:4b) — không cần mạng.\n"
      "App tự bật Ollama và tự kiểm tra model khi mở.\n"
      "Đổi model: sửa «ollama_model» trong config.json cạnh app\n"
      "(vd gemma3:12b chính xác hơn, cần tải trước).\n\n"
      "Cập nhật: app tự kiểm tra bản mới khi mở (cần mạng).\n"
      "Có bản mới thì nút «Cập nhật lên v...» hiện ra — bấm là\n"
      "app tự tải, thay thế và mở lại. File trong KetQua giữ nguyên.\n\n"
      "Lưu ý: model 4b đọc tốt nhưng có thể sai vài dòng.\n"
      "Xem bảng «Kết quả»; nháy đúp 1 dòng để sửa «Đọc từ ảnh»\n"
      "(app tự lưu lại file Excel).",
    )

  def _warn_multi_order_local(self) -> bool:
    """Nhắc 1 lần/phiên khi chạy nhiều phiếu cùng lúc."""
    if len(self.jobs) <= 1 or self._multi_ok:
      return True
    ok = messagebox.askyesno(
      "Nhiều phiếu trong 1 lần chạy",
      f"{len(self.jobs)} phiếu + {len(self.all_images)} ảnh.\n\n"
      "App tự chia ảnh về từng phiếu: đọc tên 3 sản phẩm đầu trang rồi so với "
      "danh sách từng phiếu (kèm mã PDN nếu ảnh có trên header).\n\n"
      "Nếu một trang bị chia nhầm phiếu, phiếu đó sẽ thiếu dòng — "
      "kiểm tra bảng «Kết quả» sau khi chạy.\n\n"
      "Chỉ hỏi 1 lần mỗi phiên.\n\nTiếp tục?",
    )
    self._multi_ok = bool(ok)
    return ok

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

    if not self._warn_multi_order_local():
      return

    from thuc_xuat.config import get_ollama_settings

    self._busy = True
    self._cancel_event.clear()
    self.run_btn.config(state=tk.DISABLED)
    self.stop_btn.config(state=tk.NORMAL)
    self.progress.start(12)
    self.status.config(text=f"Đang gọi Ollama ({get_ollama_settings()['model']})... đọc từng ảnh")

    excels = [j.excel_path for j in self.jobs]
    images = list(self.all_images)
    cancel_event = self._cancel_event
    out_dir = output_dir()
    include_name = bool(self.include_name_var.get())

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
      messagebox.showerror("Lỗi", str(err))
      return
    assert batch is not None

    for result in batch.results:
      self.results_by_excel[result.source_path] = result
      save_mapping(
        result.output_path.with_suffix(".json"),
        result.thuc_xuat_map,
        meta={"source": str(result.source_path)},
      )

    self._fill_preview()
    self.reexport_btn.config(state=tk.NORMAL)

    if batch.ok_count and auto_open_output_folder():
      reveal_outputs([r.output_path for r in batch.results])

    if batch.fail_count and batch.ok_count:
      self.status.config(text=f"Xong: {batch.ok_count} OK, {batch.fail_count} lỗi")
      err_lines = "\n".join(f"• {p.name}: {msg}" for p, msg in batch.errors[:5])
      ok_lines = "\n".join(
        f"• {r.output_path.name} ({len(r.included)} dòng)" for r in batch.results[:5]
      )
      messagebox.showwarning(
        "Hoàn tất một phần",
        f"Thành công ({batch.ok_count}):\n{ok_lines}\n\n"
        f"Lỗi ({batch.fail_count}):\n{err_lines}",
      )
      return

    if batch.fail_count:
      self.status.config(text="Lỗi")
      err_lines = "\n".join(f"• {p.name}: {msg}" for p, msg in batch.errors)
      messagebox.showerror("Lỗi", err_lines)
      return

    total_rows = sum(len(r.included) for r in batch.results)
    mode = "Mã + Tên SP + Thực xuất" if include_name else "Mã + Thực xuất"
    if len(batch.results) == 1:
      r = batch.results[0]
      self.status.config(text=f"Xong: {len(r.included)} dòng → {r.output_path.name}")
      messagebox.showinfo(
        "Hoàn tất",
        f"Đã tạo file:\n{r.output_path}\n\n"
        f"• {r.product_count} SP trong Excel gốc\n"
        f"• {r.extracted_stt} STT đọc từ ảnh\n"
        f"• {len(r.included)} dòng xuất file\n"
        f"• {len(r.skipped)} dòng bỏ (X/không có)\n"
        f"• Cột: {mode}",
      )
      return

    self.status.config(text=f"Xong: {batch.ok_count} phiếu, {total_rows} dòng")
    lines = "\n".join(
      f"• {r.output_path.name} — {len(r.included)} dòng" for r in batch.results
    )
    messagebox.showinfo(
      "Hoàn tất",
      f"Đã tạo {batch.ok_count} file ({mode}) trong:\n"
      f"{out_dir}\n\n{lines}\n\nTổng {total_rows} dòng xuất.",
    )

  def _fill_preview(self) -> None:
    for item in self.tree.get_children():
      self.tree.delete(item)

    job = self.preview_job
    if not job:
      return

    result = self.results_by_excel.get(job.excel_path)
    thuc_xuat_map = result.thuc_xuat_map if result else {}
    products = load_products(job.excel_path) if job.product_count else []
    if not products:
      return

    for p in products:
      raw = thuc_xuat_map.get(p.stt, "")
      qty = parse_qty(raw, p.ordered_qty, p.unit)
      tag = "skip" if qty is None else ""
      self.tree.insert(
        "",
        tk.END,
        values=(
          p.stt,
          p.code,
          p.name[:70],
          p.unit,
          p.ordered_qty,
          raw,
          qty if qty is not None else "",
        ),
        tags=(tag,) if tag else (),
      )

  def _edit_selected_row(self, _event=None) -> None:
    """Sửa tay ô «Đọc từ ảnh» của dòng đang chọn, rồi lưu lại Excel."""
    sel = self.tree.selection()
    job = self.preview_job
    if not sel or not job:
      return
    result = self.results_by_excel.get(job.excel_path)
    if not result:
      messagebox.showinfo(
        "Chưa có kết quả",
        "Bấm «Tạo file Excel» trước, sau đó mới sửa được từng dòng.",
      )
      return
    values = self.tree.item(sel[0], "values")
    try:
      stt = int(values[0])
    except (TypeError, ValueError, IndexError):
      return

    current = result.thuc_xuat_map.get(stt, "")
    new_value = simpledialog.askstring(
      "Sửa thực xuất",
      f"STT {stt} — {values[2]}\nĐVT: {values[3]}\n\n"
      "Nhập số (vd 10), X (bỏ dòng), ✓ (lấy số đặt),\n"
      "hoặc nguyên văn trên phiếu:\n"
      "• 3T*3 → 9, 5T x 24 → 120 (có dấu nhân thì nhân)\n"
      "• 10 gói → 10 (ghi đơn vị lẻ thì giữ nguyên)\n"
      "• 2T → 2 × hệ số ĐVT (ĐVT «thùng 40 gói» → 80)\n\n"
      "Để trống = không xuất dòng này.",
      initialvalue=current,
      parent=self,
    )
    if new_value is None:
      return

    new_value = new_value.strip()
    if new_value:
      result.thuc_xuat_map[stt] = new_value
    else:
      result.thuc_xuat_map.pop(stt, None)

    products = load_products(job.excel_path)
    included, skipped = build_output(products, result.thuc_xuat_map)
    save_output(included, result.output_path, include_name=result.include_name)
    save_mapping(
      result.output_path.with_suffix(".json"),
      result.thuc_xuat_map,
      meta={"source": str(job.excel_path), "edited": True},
    )
    result.included = included
    result.skipped = skipped
    result.extracted_stt = len(result.thuc_xuat_map)

    self._fill_preview()
    self.status.config(
      text=f"Đã sửa STT {stt} → lưu lại {result.output_path.name} "
      f"({len(included)} dòng xuất)"
    )


def run_gui() -> None:
  ThucXuatApp().mainloop()