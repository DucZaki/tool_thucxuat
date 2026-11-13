# Thực Xuất Tool

**Ảnh phiếu + Excel KiotViet → File Excel nhập kho** — đọc ảnh bằng AI chạy trên máy (Ollama).

## Cài đặt (máy dev)

**Cần Python 3.12 (64-bit).**

```bash
cd thuc-xuat-tool
install.bat
# hoặc:
py -3.12 -m pip install -r requirements.txt
py -3.12 app.py
```

## AI đọc ảnh — Ollama local

App dùng **Ollama + gemma3:4b** chạy trên máy: không cần mạng, không cần cookie, không cần API key.

1. Cài [Ollama for Windows](https://ollama.com/download).
2. Tải model đọc ảnh (chỉ 1 lần — app cũng tự hỏi và tải nếu thiếu):
   ```bash
   ollama pull gemma3:4b
   ```
3. Mở app là xong. **App tự bật Ollama** (`ollama serve`, chạy ẩn), tự kiểm tra model và hỏi tải nếu thiếu; trước khi đọc ảnh app kiểm tra lại lần nữa.

Cấu hình trong `config.json` (cạnh app):

| Key | Mặc định | Ý nghĩa |
|-----|----------|---------|
| `ollama_base_url` | `http://127.0.0.1:11434` | Địa chỉ Ollama |
| `ollama_model` | `gemma3:4b` | Model vision (gemma3:12b chính xác hơn) |
| `ollama_bands` | `0` | 0 = tự cắt ảnh thành dải; 1–4 = cố định |
| `ollama_timeout` | `180` | Giây tối đa mỗi lần gọi |
| `auto_open_output` | `true` | Tự mở thư mục chứa file kết quả |
| `output_dir` | `KetQua` | Thư mục chứa file kết quả (tương đối = cạnh app, hoặc đường dẫn tuyệt đối) |
| `export_include_name` | `false` | `true` = file kết quả có thêm cột «Tên sản phẩm» |

**App xử lý ảnh trước khi đưa cho AI:** làm phẳng tờ giấy (sửa nghiêng), dò các đường kẻ dọc của bảng rồi **che trắng đúng cột «Số lượng» in sẵn** — model chỉ còn cột «Thực xuất» viết tay để đọc. Đây là bước quyết định độ chính xác. Nếu không dò được bảng, app tự cắt trang thành các dải ngang có chồng lấn để đọc.

Khi có nhiều phiếu trong một lần chạy, app đọc tên 3 dòng đầu mỗi trang để biết trang thuộc phiếu nào, rồi đọc từng phiếu riêng. Trang nào model đọc sót quá nhiều dòng, app tự đọc lại bằng 2 nửa trang.

**Độ chính xác thực tế (2 phiếu thật, 13 ảnh):** PDN016535 đúng 52/53 dòng; PDN016505 đúng 75/91 dòng. Lỗi còn lại thường là chữ viết tay bị xoay/nghiêng hoặc model ghép thêm chữ số (vd “2T x 24” đọc thành 2764). Vì vậy app có bảng **Kết quả** hiển thị TẤT CẢ dòng: nháy đúp một dòng để sửa ô «Đọc từ ảnh», app tự lưu lại Excel. Dòng bị bỏ (X / trống) hiển thị màu xám.

Muốn chính xác hơn: đổi `ollama_model` sang `gemma3:12b` (cần `ollama pull gemma3:12b`, ~8 GB RAM/VRAM).

## Cách dùng

1. **Thêm Excel gốc** — một hoặc nhiều file `DanhSachChiTietDatHangNhap_*.xlsx`
2. **Thêm tất cả ảnh** phiếu
3. **Tạo file Excel** → AI đọc ảnh, xuất `PDNxxxxx_MaSanPham_ThucXuat.xlsx` vào thư mục **`KetQua`** cạnh app
4. **Kiểm tra bảng Kết quả**, sửa dòng sai bằng nháy đúp
5. Bấm **Mở thư mục xuất** để mở thư mục `KetQua` trong Explorer

### Xuất kèm cột «Tên sản phẩm»

Tick **«Xuất kèm cột Tên sản phẩm»** trước khi chạy → file có 3 cột
`Mã sản phẩm | Tên sản phẩm | Thực xuất`, tên file thêm hậu tố `_KemTen`
(vd `PDN014324_MaSanPham_ThucXuat_KemTen.xlsx`) để không ghi đè bản 2 cột.

Đã chạy xong rồi mới đổi ý: đổi tick rồi bấm **«Xuất lại file kết quả»** —
app xuất lại từ kết quả đã có, **không đọc lại ảnh**.

- File **2 cột** dùng để nhập KiotViet.
- File **3 cột** dùng để rà soát/đối chiếu với phiếu giấy.

Thư mục xuất đổi được trong `config.json` (`output_dir`), mặc định `KetQua`.

### Sửa file đã xuất (kể cả file của lần chạy trước)

Khung **3. Kết quả** có ô **«Sửa file đã xuất»**:

1. Chọn 1 file `.xlsx` trong danh sách (hoặc **«Chọn file khác...»** nếu file nằm ngoài `KetQua`)
2. Bấm **«Mở để sửa...»** — bảng hiện Mã SP + Thực xuất của file đó
3. Nháy đúp 1 dòng để sửa số (để trống hoặc nhập `x` = xóa dòng), `Delete` để xóa dòng
4. **Ctrl+S** hoặc **«Lưu (xuất lại file)»** — ghi đè đúng file đang sửa;
   **«Lưu thành file mới...»** để giữ bản cũ

Không cần ảnh hay Excel gốc, không cần chạy lại AI. Dùng được sau khi tắt/mở app.


## Quy tắc nghiệp vụ

| Phiếu (cột Thực xuất) | Kết quả |
|-----------------------|---------|
| `X` / `x` | Bỏ dòng |
| `Y` / `y` (nét gạch chéo bị đọc nhầm) | Bỏ dòng — phiếu không có chữ Y |
| `3T*3`, `5T x 24`, `3TX6` | Có dấu nhân thì nhân: 9, 120, 18 |
| `10T`, `4T`, `1T`, `12` | Bỏ chữ, giữ nguyên số: 10, 4, 1, 12 |
| `30cm`, `10 gói`, `30kg`, `18 quả` | Bỏ đơn vị, giữ nguyên số: 30, 10, 30, 18 |
| `5b x 30kg` | 5 (30kg là trọng lượng nên không nhân) |
| Chỉ `✓` `v` `V` `>` `/` `√` | Số lượng đặt (cột 4 Excel) |
| Trống / không có STT trên ảnh | Không xuất dòng |
| `5,5` | 5.5 (giữ số lẻ) |

STT trên phiếu = thứ tự dòng Excel (dòng 2 = STT 1).

**Không quy đổi theo ĐVT.** Cột ĐVT đã nói rõ đơn vị (vd `thùng 20 hộp`),
nên phiếu ghi `10T` nghĩa là 10 thùng → xuất **10**, không phải 200.
Chỉ nhân khi chính ô viết tay có dấu nhân (`5T x 24` → 120 hộp lẻ).
Bảng Kết quả có cột **ĐVT** cạnh cột «Đọc từ ảnh» để bạn đối chiếu từng dòng.

## CLI

```bash
py -3.12 cli.py process -s PDN014044.xlsx -i anh1.jpg -i anh2.jpg
py -3.12 cli.py build -s file.xlsx -m map.json -o out.xlsx
```

Mặc định CLI cũng ghi vào thư mục `KetQua`; đổi bằng `--out-dir`,
xuất kèm tên sản phẩm bằng `--with-name`.

## Test

```bash
py -3.12 -m unittest discover -s tests -v
```

## Cập nhật & phát hành bản mới

App tự so phiên bản với GitHub Releases của repo `DucZaki/tool_thucxuat`:

- Mở app → tự kiểm tra (không có mạng thì im lặng, app vẫn chạy).
- Có bản mới → nút **«Cập nhật lên vX.Y.Z»** hiện ở hàng tuỳ chọn.
- Bấm → tải (có %), thay thế file, tự mở lại. `config.json` và thư mục `KetQua\` **giữ nguyên**.

### Phát hành (người sửa code)

1. Sửa `thuc_xuat/version.py`, ví dụ `1.1.1`.
2. Commit + `git push origin main`.
3. GitHub Actions (`.github/workflows/release.yml`) tự chạy test, build `.exe` trên Windows runner rồi tạo Release `v1.1.1` kèm `ThucXuatTool-win64.zip` (cho tự cập nhật) và `ThucXuatTool-Setup.exe` (bộ cài cho người dùng mới).

Máy người dùng sẽ thấy nút cập nhật ở lần mở app kế tiếp (hoặc bấm «Kiểm tra cập nhật»).

Lưu ý:

- **Không tăng version = không có bản mới** (tag đã tồn tại nên workflow bỏ qua).
- Bản cài trước 1.1.0 chưa có nút cập nhật → gửi file zip **một lần**.
- Repo phải public để app kiểm tra không cần token; đổi repo thì sửa `GITHUB_REPO` trong `thuc_xuat/updater.py`.
- Kiểm tra nhanh trên máy dev: `py -3.12 cli.py check-update`.

## Cài đặt cho người dùng

### Cách 1 (khuyến nghị): `ThucXuatTool-Setup.exe`

Chỉ cần gửi **một file exe**. Người dùng bấm chạy, bộ cài tự:

1. Chép app vào `%LOCALAPPDATA%\Programs\ThucXuatTool` (không cần quyền admin).
2. Tạo lối tắt **«Thực Xuất»** ở Desktop và Start Menu.
3. Cài Ollama nếu máy chưa có (tải bộ cài chính thức ~1,7 GB, chạy silent).
4. Tải model `gemma3:4b` (~3,4 GB) — có thanh tiến độ.
5. Bấm «Mở ứng dụng» là chạy.

Chạy lại `Setup.exe` = cập nhật đè bản mới; `config.json` và `KetQua\` **giữ nguyên**.
Lần cài đầu cần mạng; các lần sau app tự cập nhật (mục trên).

Gỡ cài: xoá thư mục `%LOCALAPPDATA%\Programs\ThucXuatTool` và hai lối tắt.

### Cách 2: file zip (bản chạy trực tiếp)

Trên máy dev (Python 3.12 64-bit):

```bash
build.bat
```

Tạo:
- `dist/ThucXuatTool/` — folder chạy trực tiếp
- `dist/ThucXuatTool-win64.zip` — gửi cho người dùng
- `dist/ThucXuatTool-Setup.exe` — bộ cài một chạm (build thêm: `py -3.12 build_installer.py`)

Người nhận: giải nén → chạy `ThucXuatTool.exe` → xem `HUONG-DAN-NGUOI-DUNG.txt`.
