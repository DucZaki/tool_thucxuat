# Thực Xuất Tool

**Ảnh phiếu + Excel KiotViet → File Excel nhập kho** — đọc ảnh qua NVIDIA NIM.

App có hai trang **Thực xuất** và **Kiểm kê**, dùng chung API key và model NVIDIA NIM.

Trang **Kiểm kê** tự nạp danh mục và mẫu nhập hàng trong thư mục `data`.
Hai nút **Chọn Excel…** cho phép thay danh mục hoặc mẫu và nhớ lựa chọn cho lần mở sau.
Đường dẫn đã chọn bị mất thì app tự quay về dữ liệu được đóng gói.
App đọc tên sản phẩm viết tay, ĐVT và số thực tế, tìm ứng viên từ toàn bộ danh mục,
rồi dùng NIM đối chiếu để chọn mã hàng. File nhập tham chiếu trong `data`
giúp ưu tiên mã thường đã sử dụng giữa nhiều biến thể cùng tên.
Có thể xuất ngay sau khi đọc, ghi chú độ tin cậy không yêu cầu xác nhận từng dòng.
Nháy đúp để sửa nếu muốn. Biểu thức như `1+8` được tính tự động.
Với quy cách khay 3 con, `1 khay 3 con` là 1 khay, còn `1 khay + 3 con` là 2 khay.
Bấm đọc lại để tiếp tục ảnh lỗi, dữ liệu đã đọc được dùng lại.
Khi xuất, app giữ cấu trúc và định dạng mẫu, xóa dòng ví dụ, dùng **Giá vốn**
trong danh mục cho **Đơn giá**, để trống **Giảm giá** và **Giảm giá (%)**.
Có thể chọn Giá bán trên giao diện.
Mỗi ảnh kiểm kê xuất thành một file Excel riêng. Có thể chọn từng file đã xuất,
mở file cũ và sửa mã hàng hoặc số lượng trực tiếp trong tab Kiểm kê.

File **Thực xuất** có hai cột **Mã hàng** và **Số lượng chuyển**, được tạo thành
Excel Table. App vẫn mở và sửa được file dùng tên cột cũ.

## Cài đặt (máy dev)

**Cần Python 3.12 (64-bit).**

```bash
cd thuc-xuat-tool
install.bat
# hoặc:
py -3.12 -m pip install -r requirements.txt
py -3.12 app.py
```

## AI đọc ảnh qua NVIDIA NIM

1. Tạo API key trong [NVIDIA Build](https://build.nvidia.com/nvidia/nemotron-3.5-super-vl-120b-a12b) và dán vào ô **API key NVIDIA** ở đầu cửa sổ.
2. Chọn **Tự động · Super VL → Nano Omni** để ưu tiên Super VL và chuyển sang Nano Omni nếu endpoint gặp lỗi tạm thời. Có thể chọn riêng một model. App nhớ model đã chọn. Key nhập trong GUI chỉ dùng trong phiên đang mở. Có thể đặt `NVIDIA_API_KEY` để dùng cho GUI hoặc CLI.
3. Thêm Excel và ảnh rồi bấm **Đọc phiếu và xuất Excel**. Ảnh đã tiền xử lý được gửi tới NVIDIA NIM.

Một lô có thể dùng nhiều request vì app nhận diện phiếu, đọc từng trang hoặc dải ảnh, và đọc lại vùng khó. NVIDIA Build cung cấp endpoint thử nghiệm và giới hạn phụ thuộc vào model và tải. [FAQ NVIDIA NIM](https://forums.developer.nvidia.com/t/nvidia-nim-faq/300317)

Cấu hình trong `config.json` (cạnh app):

| Key | Mặc định | Ý nghĩa |
|-----|----------|---------|
| `nim_model` | `nim-auto` | Model để đọc ảnh |
| `image_bands` | `0` | Tự chia dải ảnh theo chiều cao hoặc đặt cố định từ 1 đến 4 dải |
| `auto_open_output` | `false` | Tự mở thư mục chứa file kết quả |
| `output_dir` | `KetQua` | Thư mục chứa file kết quả (tương đối = cạnh app, hoặc đường dẫn tuyệt đối) |
| `export_include_name` | `false` | `true` = file kết quả có thêm cột «Tên sản phẩm» |

**App xử lý ảnh trước khi đưa cho AI:** làm phẳng trang và che cột **Số lượng đặt** và **Ghi chú** khi dò được đường kẻ. Model chỉ được đọc cột **Thực xuất**. Nếu ô này trống thì bỏ dòng dù cột Ghi chú có nội dung.

Khi chạy nhiều phiếu, thêm tất cả ảnh cùng lúc. App tự nhóm ảnh theo mã PDN hoặc đối chiếu tên hàng ở nhiều STT trên từng ảnh với Excel. Nếu chưa đủ bằng chứng, app thử nhận diện lại rồi báo tên ảnh còn mơ hồ để bạn kiểm tra.

Kiểm tra số lượng trong bảng **Kết quả** trước khi nhập KiotViet. Nháy đúp một dòng để sửa ô «Đọc từ ảnh», app tự lưu lại Excel. Dòng bị bỏ (X / trống) hiển thị màu xám.

## Cách dùng

1. **Thêm Excel gốc** — một hoặc nhiều file `DanhSachChiTietDatHangNhap_*.xlsx`
2. **Thêm ảnh…** của tất cả các phiếu
3. **Đọc phiếu và xuất Excel** → AI đọc ảnh, xuất `PDNxxxxx_MaSanPham_ThucXuat.xlsx` vào thư mục **`KetQua`** cạnh app
4. **Kiểm tra bảng Kết quả**, sửa dòng sai bằng nháy đúp
5. Bấm **Mở thư mục xuất** để mở thư mục `KetQua` trong Explorer

Nếu còn ảnh lỗi, bấm **Đọc phiếu và xuất Excel** lần nữa để xử lý ảnh chưa đọc được. Trong cùng phiên cửa sổ, app dùng lại kết quả của ảnh đã đọc và giữ các dòng bạn đã sửa tay. **Xóa tất cả ảnh** bỏ nhanh toàn bộ ảnh khỏi danh sách (chỉ bỏ trong app, không xóa file trên máy). **Reset app** bắt đầu phiên xử lý sạch.

### File xuất

File kết quả luôn có **2 cột**: `Mã sản phẩm | Thực xuất` — dùng để nhập KiotViet.
Tên hàng hiển thị trong bảng «Kết quả»
(lấy từ Excel gốc / file JSON phụ cạnh file kết quả), **không** còn cột Tên trong Excel xuất ra.

### Sửa file đã xuất (kể cả file của lần chạy trước)

1. Trong **3. Kết quả**, danh sách hiện một file kết quả cho mỗi phiếu đang mở. Dùng **Chọn file khác...** để mở một file đã xuất trước đó.
2. Chọn file để hiển thị ngay trong bảng bên dưới. Nháy đúp dòng cần sửa, nhập số hoặc phép tính như `3*8`, `10+2`, `(12-2)/2`, rồi nhấn **Enter** để tính và ghi lại file. Nhập `X` hoặc để trống để bỏ dòng.
3. Dùng **Lưu bản sao...** để lưu file hiện tại sang tên mới.

Có thể sửa file cũ mà không cần ảnh hay chạy lại AI. Khi có Excel gốc phù hợp, bảng hiển thị cả STT, ĐVT, số đặt và các dòng bị bỏ.

### Xuất lại file kết quả

Đã chạy xong rồi muốn làm mới file (vd sau khi sửa tay nhiều dòng):
bấm **«Xuất lại file kết quả»** — app xuất lại từ kết quả đã có, **không đọc lại ảnh**.

Thư mục xuất đổi được trong `config.json` (`output_dir`), mặc định `KetQua`.

## Quy tắc nghiệp vụ

| Phiếu (cột Thực xuất) | Kết quả |
|-----------------------|---------|
| `X` / `x` | Bỏ dòng |
| `Y` / `y` (nét gạch chéo bị đọc nhầm) | Bỏ dòng — phiếu không có chữ Y |
| `3T*3`, `5T x 24`, `3TX6` | Có dấu nhân thì nhân: 9, 120, 18 |
| `10T`, `4T`, `1T`, `12` | Bỏ chữ, giữ nguyên số: 10, 4, 1, 12 |
| `120 gói` và ĐVT `thùng(T)/60 gói` | 2 thùng |
| `5T+8` đọc từ ảnh | Ưu tiên cách đọc `5T x 8`, cho kết quả 40 |
| `5b x 30kg` | 5 (30kg là trọng lượng nên không nhân) |
| OCR đọc nét giống `✓`, `✔`, `v`, `V`, `>`, `/`, `√` | Coi như `X`, bỏ dòng |
| Trống / không có STT trên ảnh | Không xuất dòng |
| `5,5` | 5.5 (giữ số lẻ) |

STT trên phiếu = thứ tự dòng Excel (dòng 2 = STT 1).

Khi xuất theo thùng, app đối chiếu quy cách trong cả ĐVT và tên hàng. Với thùng 60 cái, `620 chiếc` cho kết quả 620/60 thùng (10 thùng và 20 chiếc lẻ). Cái và chiếc được tính như nhau. Nếu chỉ ghi `620`, app hiểu là 620 theo ĐVT của Excel.
Nếu ghi gói mà quy cách chỉ có cái, thiếu quy cách hoặc hai cột ghi khác nhau, app giữ chữ viết tay trong bảng, tô vàng dòng và bỏ dòng đó khỏi file xuất. Nháy đúp để nhập số thùng khi xác định được số lượng.
Phép nhân ghi trên phiếu vẫn tính như trước. Phép cộng nhập khi sửa ô vẫn dùng dấu `+`.
Bảng Kết quả có cột **ĐVT** cạnh cột «Đọc từ ảnh» để bạn đối chiếu từng dòng.

## CLI

```bash
py -3.12 cli.py process -s PDN014044.xlsx -i anh1.jpg anh2.jpg
py -3.12 cli.py build -s file.xlsx -m map.json -o out.xlsx
```

Lệnh `process` đọc key từ `NVIDIA_API_KEY` và nhận tùy chọn `--model`. Mặc định CLI cũng ghi vào thư mục `KetQua`; đổi bằng `--out-dir`,
xuất kèm tên sản phẩm bằng `--with-name`.

## Test

```bash
py -3.12 -m unittest discover -s tests -v
```

## Cập nhật & phát hành bản mới

App tự so phiên bản với GitHub Releases của repo `DucZaki/tool_thucxuat`:

- Mở app → tự kiểm tra (không có mạng thì im lặng, app vẫn chạy).
- Có bản mới → nút **«Cập nhật lên vX.Y.Z»** hiện ở hàng tuỳ chọn.
- Bấm một lần → app kiểm tra lại Release mới nhất, tải (có %), thay thế file và tự mở lại. `config.json` và thư mục `KetQua\` **giữ nguyên**.

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
3. Bấm «Mở ứng dụng», dán API key và chọn model.

Chạy lại `Setup.exe` = cập nhật đè bản mới; `config.json` và `KetQua\` **giữ nguyên**.
App cần mạng để đọc ảnh và kiểm tra cập nhật.

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
