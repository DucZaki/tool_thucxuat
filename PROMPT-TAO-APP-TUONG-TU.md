# Prompt pack — tạo app kiểu “Thực xuất phiếu kho”

> **Lịch sử:** tài liệu này ghi lại cách xây bản đầu (dùng Gemini Web). Bản hiện tại chỉ dùng
> **Ollama local** (gemma3:4b) — bỏ qua mọi phần cookie / API key / provider bên dưới.

Tài liệu này **giữ nguyên ý prompt + cách làm** từ đoạn chat xây app đọc ảnh phiếu xuất + Excel KiotViet. Dùng để prompt AI khác (hoặc session mới) tạo ứng dụng tương tự.

---

## 0) Prompt copy-paste (bắt đầu session mới)

```
Bạn là senior Google-style engineer. Luôn trả lời tiếng Việt.

Xây desktop app Windows: ảnh phiếu xuất (cột Thực xuất ghi tay) + file Excel KiotViet DanhSachChiTietDatHangNhap_*.xlsx → Excel 2 cột "Mã sản phẩm | Thực xuất" (số nguyên).

### Input
- 1 hoặc nhiều Excel gốc KiotViet (dòng 2 = STT 1, cột 1 mã SP, cột 2 tên, cột 4 số lượng đặt)
- Tất cả ảnh phiếu thêm 1 lần (không gán thủ công từng phiếu). AI đọc mã PDN trên header ảnh.

### Output
- Mỗi Excel gốc → 1 file: PDNxxxxx_MaSanPham_ThucXuat.xlsx, cùng thư mục Excel gốc
- Chỉ 2 cột: Mã sản phẩm | Thực xuất
- STT không có trên ảnh → không xuất dòng đó

### Quy tắc nghiệp vụ (bắt buộc, test bằng unit test)
1. Có chữ X / x trong thực xuất → BỎ dòng
2. Chỉ lấy SỐ ĐẦU TIÊN — bỏ T, gói, kg, vỉ, con, hộp, dây, túi...
   Ví dụ: "5T x 24" → 5 ; "3TX6" → 3 ; "10 gói" → 10 ; "30 con" → 30 ; "10+3" → 10 ; "5.5" → 5
3. Chỉ tick ✓ / v / V / > / / / √ và không có số → lấy số lượng ĐẶT (cột 4 Excel)
4. Ô trống / không đọc được / không có STT trên ảnh → không xuất

### AI đọc ảnh
- Mặc định Gemini Web qua gemini_webapi (cookie __Secure-1PSID + __Secure-1PSIDTS), không bắt buộc API key
- Cookie copy tay từ https://gemini.google.com (Chrome/Cốc Cốc F12 → Application → Cookies). Tắt "dùng cookie trình duyệt" khi dán tay (Cốc Cốc không đọc auto được)
- Cũng hỗ trợ Gemini API official và OpenAI (API key) nhưng luồng chính là gemini_web
- Model Gemini Web: gemini-3-flash
- Gửi 1 ẢNH / 1 LẦN GỌI (không nhồi 6+ ảnh: OCR lẫn STT/PDN, thiếu trang)
- Nhiều ảnh → nhiều lần gọi → merge STT: giá trị thật đã có thì giữ, không để "chuỗi dài hơn thắng"
- Ảnh trả 0 STT → retry
- Timeout ~120s/ảnh, có nút Dừng hủy request
- Prompt OCR: đọc HẾT dòng thấy được trên trang; không đoán; không lẫn PDN; X→"X"; tick→"✓"

### GUI (tkinter)
1. Thêm Excel gốc (nhiều file)
2. Thêm tất cả ảnh
3. Cài đặt: provider, cookie, API key, checkbox tự mở Explorer tới file kết quả
4. Nút Tạo file Excel + nút Dừng
5. Status hiện từng ảnh: "ảnh 3/7: xxx.jpg | đã có N STT"
6. Preview bảng STT / Mã / Tên / Đặt / Đọc từ ảnh / Thực xuất

### Packaging
- Python 3.12 64-bit (không dùng 3.14 32-bit — thiếu wheel curl-cffi)
- PyInstaller folder: ThucXuatTool.exe + _internal (bắt buộc cạnh nhau)
- build.bat → zip ThucXuatTool-win64.zip. Người nhận giải nén cả folder, không copy lẻ .exe
- config.json ghi cạnh .exe (frozen). Không bundle cookie
- Windows English locale hay crash charmap khi loguru in tiếng Việt → ép UTF-8 + tắt log gemini_webapi

### Bài học đã đụng
- openpyxl read_only=True → max_row=None → "không có sản phẩm" → bỏ read_only
- Cookie cache %TEMP%\gemini_webapi ghi đè cookie mới → nút xóa cache trước login
- Gemini Web stream treo / CID / 1100 khi nhiều ảnh
- cookie hết hạn → UNAUTHENTICATED
- sort tên file phải natural sort (page2 trước page10)

Làm app hoàn chỉnh: processor, pipeline, vision, GUI, tests cho parse_qty/merge/chunk, run.bat + install.bat + build.bat.
Trả lời tiếng Việt. Không commit trừ khi được yêu cầu.
```

---

## 1) Các prompt gốc trong chat (giữ ý, rút gọn)

Thứ tự gần đúng diễn biến:

1. Xây tool tự động hóa: ảnh phiếu (cột Thực xuất ghi tay) + Excel KiotViet → Excel 2 cột Mã SP | Thực xuất. STT trên phiếu = thứ tự dòng Excel (dòng 2 = STT 1).
2. Quy tắc: bỏ X; chỉ số đầu (bỏ T/gói/kg…); chỉ tick → số lượng đặt; STT không có trên ảnh → không xuất.
3. «thêm tính năng tự mở vị trí file khi tạo được»
4. «ok cách cài trên máy khác nhanh»
5. «tôi muốn xuất bản cái này, người kia chỉ cần download về dùng»
6. «nếu import vào 2 file excel thì đầu ra là bao nhiêu file excel» → **1 Excel gốc = 1 file ra**
7. «lấy cookie từ cốc cốc có được k» → được, copy tay F12, đừng auto browser cookie
8. Screenshot lỗi `'charmap' codec can't encode '\u1ef1'` trên máy khác
9. «k cần chạy lại cd thuc-xuat-tool build.bat à» — zip đã build sẵn thì không
10. Lỗi `Failed to load Python DLL ...\_internal\python312.dll` — thiếu folder `_internal`
11. «nếu cho ít ảnh thì ok 4-5, từ 6 ảnh đổ lên sai loạn» — hỏi nguyên nhân + cách fix (lúc đó **chỉ nêu**, không tự fix trừ khi được bảo)
12. «dùng deepseek thì sao» / «đổi API được không» / «Gemini Web chỉnh model không»
13. «khi chọn model thì bản nào đọc nhiều ảnh ổn nhất» → Flash
14. «có cách fix thiếu trang mà vẫn dùng Gemini không» → chia lô nhỏ
15. «Chọn Flash + prompt chặt» — lúc này mới được phép sửa code
16. «còn API nào free mạnh hơn Gemini k» — gần như không
17. App chạy 5 phút chưa xong 3 ảnh → giám sát process/TCP; Gemini Web treo stream
18. «làm 1 cái nút ép dừng»
19. «XEM LẠI APP… CHẠY TOÀN BỊ THIẾU TRANG. TEST LẠI»
20. «PHÁT HÀNH APP KIỂU GÌ»
21. «LẤY COOKIE TỪ CÁI NÀO» → `https://gemini.google.com`
22. «làm lại quán này… tự chính mày làm theo yêu cầu, chứ k phải dùng app» — session chat tự OCR ảnh + map Excel + xuất file (không gọi GUI)

Cách trả lời mong muốn: **lead bằng đáp án**, tiếng Việt, senior, không tự commit, không tự sửa khi user chỉ hỏi nguyên nhân.

---

## 2) Spec nghiệp vụ (giữ nguyên)

| Phiếu (cột Thực xuất) | Kết quả |
|----------------------|---------|
| `X` / `x` | Bỏ dòng |
| `5T x 24`, `3Tx20`, `10 gói`, `30kg`, `20 vỉ` | Số đầu: 5, 3, 10, 30, 20 |
| Chỉ `✓` `v` `V` `>` `/` `√` | Số lượng đặt Excel |
| Trống / không thấy STT | Không xuất |
| `10+3` | 10 (số đầu, rule đã chốt) |

Map: **STT trên phiếu = index dòng Excel** (dòng header 1, data bắt đầu dòng 2 = STT 1).

Nhiều phiếu: user thả tất cả Excel + tất cả ảnh một lần. AI đọc PDN trên header ảnh, xuất từng `PDNxxxxx_MaSanPham_ThucXuat.xlsx`.

---

## 3) Kiến trúc đã làm (làm y vậy)

```
thuc-xuat-tool/
  app.py, gui.py, cli.py, config.json
  run.bat, install.bat, build.bat
  ThucXuatTool.spec
  thuc_xuat/
    processor.py      # parse_qty, load_products, build_output, save_output
    pipeline.py       # process_batch_like_chat
    vision_extract.py # AI ảnh → JSON STT→thực xuất
    gemini_client.py  # cookie, xóa cache, verify login
    image_match.py    # PDN từ tên file Excel
    shell_open.py     # explorer /select,
    config.py, cancel.py, win_utf8.py, app_paths.py
  tests/              # parse_qty, merge, chunk, integration
```

Luồng GUI: Thêm Excel → Thêm ảnh → Tạo file → `process_batch_like_chat` → save xlsx + json mapping → (optional) mở Explorer.

Auth: `__Secure-1PSID` bắt buộc + `__Secure-1PSIDTS` nên có. Sau khi dán: Xóa cache cookie → Kiểm tra đăng nhập → Lưu.

---

## 4) Cách làm trong chat (giữ nguyên method)

1. **Chốt rule trước khi code** — parse_qty có test từng format thật trên phiếu.
2. **Đừng nhồi nhiều ảnh 1 request** — thực tế ≥6 ảnh Gemini Web OCR loạn / thiếu trang. Chốt **1 ảnh/lần**, merge an toàn.
3. **Frozen exe**: `app_dir()` = thư mục `.exe`; config.json cạnh exe.
4. **Phát hành**: PyInstaller COLLECT (folder), không onefile. Zip cả folder. Hướng dẫn: không copy lẻ exe.
5. **Khi user hỏi “nguyên nhân / cách fix”**: chỉ giải thích, **không sửa code** trừ khi user chọn option và bảo làm.
6. **Khi user nói “tự làm, không dùng app”**: đọc ảnh + Excel bằng tay/session, gọi `load_products` + `build_output` + `save_output`, mở Explorer.
7. **Không commit** trừ khi user yêu cầu.
8. **Giám sát treo**: PID python, CPU ~0, TCP HTTPS Google established = đang chờ Gemini, không phải app chết. Timeout + nút Dừng.

---

## 5) Checklist test tối thiểu

- `5T x 24` → 5; `X` → skip; tick + ordered 12 → 12
- 2 Excel → 2 output
- 7 ảnh 1 phiếu → 7 lần gọi, merge không mất STT trang sau
- Natural sort page2 < page10
- Login fail khi cookie hết hạn: UNAUTHENTICATED + hướng dẫn F12
- Máy Windows English: không crash charmap

---

## 6) Phát hành nhanh

```bat
cd thuc-xuat-tool
build.bat
```

Gửi `dist\ThucXuatTool-win64.zip`. Người nhận: giải nén → `ThucXuatTool.exe` + `_internal\` → dán cookie Gemini của họ.
