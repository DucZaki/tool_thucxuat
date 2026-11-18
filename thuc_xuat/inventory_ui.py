"""Inventory page sharing the main app's NVIDIA NIM connection."""
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

from thuc_xuat.cancel import CancelledByUser
from thuc_xuat.config import output_dir, set_config_value
from thuc_xuat.inventory_defaults import inventory_inputs
from thuc_xuat.inventory import load_catalog, read_inventory_page, match_inventory, export_inventory, quantity
from thuc_xuat.nim_client import NimError, NimVision, MODELS


class InventoryPage(ttk.Frame):
  def __init__(self, parent, app):
    super().__init__(parent, padding=12)
    self.app = app
    self.catalog = {}
    self.photos = []
    self.rows = []
    self.cache = {}
    self.catalog_path = tk.StringVar()
    self.template_path = tk.StringVar()
    self.price_source = tk.StringVar(value="Giá vốn")
    self.events = queue.Queue()
    self.running = False
    self.after(120, self.poll)
    self.columnconfigure(1, weight=1)
    self.rowconfigure(5, weight=1)
    for index, (label, variable) in enumerate((("Danh mục sản phẩm", self.catalog_path), ("Mẫu nhập hàng", self.template_path))):
      ttk.Label(self, text=label).grid(row=index, column=0, sticky="w", padx=(0, 10))
      ttk.Entry(self, textvariable=variable, state="readonly").grid(row=index, column=1, sticky="ew", pady=4)
      ttk.Button(self, text="Chọn Excel…", command=lambda i=index: self.choose_file(i)).grid(row=index, column=2, padx=6)
    actions = ttk.Frame(self)
    actions.grid(row=2, column=0, columnspan=3, sticky="ew", pady=8)
    ttk.Button(actions, text="Thêm ảnh kiểm kê…", command=self.add_photos).pack(side=tk.LEFT)
    ttk.Button(actions, text="Bỏ ảnh đã chọn", command=self.remove_photos).pack(side=tk.LEFT, padx=6)
    ttk.Button(actions, text="Reset kiểm kê", command=self.reset).pack(side=tk.LEFT)
    ttk.Label(actions, text="Đơn giá").pack(side=tk.LEFT, padx=(20, 5))
    ttk.Combobox(actions, textvariable=self.price_source, values=("Giá vốn", "Giá bán"), state="readonly", width=12).pack(side=tk.LEFT)
    ttk.Label(actions, text="Giảm giá để trống", style="Muted.TLabel").pack(side=tk.LEFT, padx=10)
    self.images = tk.Listbox(self, height=3, selectmode=tk.EXTENDED, exportselection=False)
    self.images.grid(row=3, column=0, columnspan=3, sticky="ew")
    tools = ttk.Frame(self)
    tools.grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)
    self.read_button = ttk.Button(tools, text="Đọc ảnh và đối chiếu", command=self.read, style="Primary.TButton")
    self.read_button.pack(side=tk.LEFT)
    ttk.Button(tools, text="Dừng", command=self.stop).pack(side=tk.LEFT, padx=6)
    ttk.Button(tools, text="Sửa dòng", command=self.edit).pack(side=tk.LEFT)
    ttk.Button(tools, text="Xuất file nhập hàng", command=self.export).pack(side=tk.RIGHT)
    holder = ttk.Frame(self)
    holder.grid(row=5, column=0, columnspan=3, sticky="nsew")
    holder.rowconfigure(0, weight=1)
    holder.columnconfigure(0, weight=1)
    columns = ("image", "stt", "raw_name", "actual", "code", "name", "unit", "qty", "issue")
    self.tree = ttk.Treeview(holder, columns=columns, show="headings")
    labels = ("Ảnh", "STT", "Tên trên ảnh", "Thực tế trên ảnh", "Mã hàng", "Tên hàng danh mục", "ĐVT", "Số lượng", "Ghi chú xử lý")
    for column, label, width in zip(columns, labels, (120, 45, 160, 110, 120, 260, 90, 80, 220)):
      self.tree.heading(column, text=label)
      self.tree.column(column, width=width, stretch=column == "name")
    self.tree.tag_configure("warn", background="#fff0cc")
    self.tree.bind("<Double-1>", lambda e: self.edit())
    self.tree.grid(row=0, column=0, sticky="nsew")
    vertical = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
    horizontal = ttk.Scrollbar(holder, orient="horizontal", command=self.tree.xview)
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    self.note = ttk.Label(self, text="Thêm ảnh để đọc và xuất nhập hàng. Nháy đúp nếu muốn sửa.", style="Muted.TLabel")
    self.note.grid(row=6, column=0, columnspan=3, sticky="w", pady=6)
    catalog_path, template_path = inventory_inputs()
    try:
      self.catalog = load_catalog(catalog_path)
      self.catalog_path.set(str(catalog_path))
      self.template_path.set(str(template_path))
      self.note.config(text=f"Đã nạp sẵn {len(self.catalog):,} mã hàng và mẫu nhập hàng.")
    except (OSError, ValueError) as exc:
      self.note.config(text=f"Không nạp được dữ liệu mặc định: {exc}. Bấm Chọn Excel để thay thế.")

  def available(self):
    return not self.app._busy

  def choose_file(self, index):
    if not self.available():
      return
    path = filedialog.askopenfilename(parent=self, filetypes=[("Excel", "*.xlsx")])
    if not path:
      return
    try:
      if index == 0:
        catalog = load_catalog(path)
        if self.rows and not messagebox.askyesno("Đổi danh mục", "Xóa kết quả kiểm kê đang mở để dùng danh mục mới?", parent=self):
          return
        self.catalog = catalog
        self.catalog_path.set(path)
        set_config_value("inventory_catalog", path)
        self.rows.clear()
        self.cache.clear()
        self.refresh()
        self.note.config(text=f"Đã tải {len(catalog):,} mã hàng từ toàn bộ danh mục.")
      else:
        self.template_path.set(path)
        set_config_value("inventory_template", path)
    except Exception as exc:
      messagebox.showerror("Không đọc được danh mục", str(exc), parent=self)

  def add_photos(self):
    if not self.available():
      return
    paths = filedialog.askopenfilenames(parent=self, filetypes=[("Ảnh", "*.png;*.jpg;*.jpeg;*.webp;*.bmp")])
    for path in paths:
      photo = str(Path(path).resolve())
      if photo not in self.photos:
        self.photos.append(photo)
        self.images.insert(tk.END, Path(photo).name)

  def remove_photos(self):
    if not self.available():
      return
    for index in reversed(self.images.curselection()):
      photo = self.photos.pop(index)
      self.rows = [row for row in self.rows if row.image != photo]
      self.cache.pop(photo, None)
      self.images.delete(index)
    self.refresh()

  def reset(self):
    if not self.available():
      return
    if self.rows and not messagebox.askyesno("Reset kiểm kê", "Xóa ảnh và kết quả kiểm kê đang mở?", parent=self):
      return
    self.photos.clear()
    self.rows.clear()
    self.cache.clear()
    self.images.delete(0, tk.END)
    self.refresh()

  def read(self):
    if not self.available():
      return
    if not self.catalog or not self.photos:
      messagebox.showwarning("Thiếu đầu vào", "Chọn danh mục sản phẩm và ảnh kiểm kê.", parent=self)
      return
    previous = self.app._nim_requests
    try:
      client = NimVision(MODELS[self.app.model_var.get()], self.app.api_key_var.get(),
                         on_usage=lambda usage: self.events.put(("usage", (previous, usage))))
    except Exception as exc:
      messagebox.showerror("NVIDIA NIM", str(exc), parent=self)
      return
    self.app._cancel_event.clear()
    self.app._busy = self.running = True
    self.read_button.config(state="disabled")
    self.app.progress.start()
    threading.Thread(target=self.worker, args=(client,), daemon=True).start()

  def worker(self, client):
    errors = []
    def progress(text):
      self.events.put(("status", text))
    try:
      for photo in self.photos:
        try:
          path = Path(photo)
          stat = path.stat()
          revision = (stat.st_size, stat.st_mtime_ns)
          if self.cache.get(photo) != revision:
            progress("Đang đọc " + path.name)
            fresh = read_inventory_page(path, client, self.app._cancel_event, progress)
            self.rows[:] = [row for row in self.rows if row.image != photo] + fresh
            self.cache[photo] = revision
          match_inventory([r for r in self.rows if r.image == photo], self.catalog, client,
                          self.app._cancel_event, progress)
        except (CancelledByUser, NimError):
          raise
        except Exception as exc:
          errors.append(Path(photo).name + ": " + str(exc))
      self.events.put(("done", errors))
    except CancelledByUser:
      self.events.put(("done", ["Đã dừng. Bấm đọc lại để tiếp tục ảnh còn thiếu."]))
    except Exception as exc:
      self.events.put(("done", [str(exc)]))

  def poll(self):
    try:
      while True:
        kind, payload = self.events.get_nowait()
        if kind == "usage":
          self.app._show_nim_usage(*payload)
        elif kind == "status":
          self.app.status.config(text=payload)
        elif kind == "done":
          self.running = self.app._busy = False
          self.read_button.config(state="normal")
          self.app.progress.stop()
          self.refresh()
          self.app.status.config(text=(
            "Kiểm kê còn ảnh chưa xử lý xong." if payload
            else "Kiểm kê đã đọc xong. Có thể xuất file nhập hàng."
          ))
          if payload:
            messagebox.showwarning("Kiểm kê cần kiểm tra", "\n".join(payload), parent=self)
    except queue.Empty:
      pass
    self.after(120, self.poll)

  def stop(self):
    if self.running:
      self.app._cancel_event.set()

  def refresh(self):
    self.tree.delete(*self.tree.get_children())
    for index, row in enumerate(self.rows):
      item = self.catalog.get(row.code)
      values = (Path(row.image).name, row.stt or "", row.name, row.actual, row.code,
                item.name if item else "", item.unit if item else row.unit,
                row.qty if row.qty is not None else "", row.issue)
      self.tree.insert("", "end", iid=str(index), values=values, tags=("warn",) if row.issue or not row.code else ())
    pending = sum(bool(row.issue or not row.code or row.qty is None) for row in self.rows)
    self.note.config(text=f"{len(self.rows)} dòng · {pending} dòng có ghi chú. Có thể xuất ngay hoặc nháy đúp để sửa.")

  def edit(self):
    if not self.available() or not self.tree.selection():
      return
    row = self.rows[int(self.tree.selection()[0])]
    window = tk.Toplevel(self)
    window.title("Xác nhận sản phẩm và số lượng")
    window.geometry("850x300")
    window.transient(self.winfo_toplevel())
    ttk.Label(window, text=f"Ảnh: {Path(row.image).name} · {row.name} · Thực tế: {row.actual}").pack(anchor="w", padx=14, pady=10)
    search = tk.StringVar(value=row.name)
    ttk.Entry(window, textvariable=search).pack(fill="x", padx=14)
    selection = tk.StringVar()
    choices = {}
    picker = ttk.Combobox(window, textvariable=selection, state="readonly")
    picker.pack(fill="x", padx=14, pady=8)
    def find(*args):
      from thuc_xuat.inventory import candidates_for, CountRow
      choices.clear()
      candidates = candidates_for(CountRow("", 0, search.get(), row.unit, ""), self.catalog, 35)
      if row.code and self.catalog[row.code] not in candidates:
        candidates.insert(0, self.catalog[row.code])
      for item in candidates:
        choices[f"{item.code} | {item.name} | {item.unit}"] = item.code
      picker.config(values=list(choices))
      selected = next((label for label, code in choices.items() if code == row.code), next(iter(choices), ""))
      selection.set(selected)
    search.trace_add("write", find)
    find()
    qty = tk.StringVar(value=str(row.qty) if row.qty is not None else row.actual)
    ttk.Label(window, text="Số lượng theo ĐVT của mã đã chọn (ví dụ 1+8 hoặc 3*8)").pack(anchor="w", padx=14)
    ttk.Entry(window, textvariable=qty).pack(fill="x", padx=14, pady=6)
    def save():
      try:
        code = choices.get(selection.get())
        if code not in self.catalog:
          raise ValueError("Chọn mã trong danh mục.")
        value = quantity(qty.get())
        row.code, row.qty, row.issue = code, value, ""
        self.refresh()
        window.destroy()
      except ValueError as exc:
        messagebox.showerror("Kiểm tra dữ liệu", str(exc), parent=window)
    ttk.Button(window, text="Xác nhận mã và số lượng", command=save).pack(pady=10)
    window.grab_set()

  def export(self):
    if not self.available():
      return
    try:
      pending = []
      for photo in self.photos:
        stat = Path(photo).stat()
        if self.cache.get(photo) != (stat.st_size, stat.st_mtime_ns):
          pending.append(Path(photo).name)
      if pending:
        messagebox.showwarning(
          "Còn ảnh chưa đọc", "Bấm Đọc ảnh và đối chiếu để xử lý: " + ", ".join(pending), parent=self,
        )
        return
    except OSError as exc:
      messagebox.showerror("Không mở được ảnh", str(exc), parent=self)
      return
    if not self.template_path.get():
      messagebox.showwarning("Thiếu mẫu", "Chọn file mẫu nhập hàng.", parent=self)
      return
    path = filedialog.asksaveasfilename(parent=self, defaultextension=".xlsx", initialdir=str(output_dir()),
                                      initialfile="KiemKe_NhapHang.xlsx", filetypes=[("Excel", "*.xlsx")])
    if not path:
      return
    try:
      if Path(path).resolve() == Path(self.catalog_path.get()).resolve():
        raise ValueError("Chọn file kết quả khác danh mục gốc.")
      export_inventory(self.rows, self.catalog, self.template_path.get(), path, self.price_source.get())
      messagebox.showinfo("Đã xuất nhập hàng", path, parent=self)
    except Exception as exc:
      messagebox.showerror("Chưa xuất được", str(exc), parent=self)
