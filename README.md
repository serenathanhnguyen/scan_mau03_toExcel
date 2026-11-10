# Mẫu 03 (PDF scan) → Excel import Medinet

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...     # hoặc nhập trong thanh bên của app
streamlit run app.py
```
Đặt file mẫu Excel mới nhất của Medinet vào `templates/` (hoặc tải lên trong app).

## Cách hoạt động
1. PDF → bỏ trang trắng (mặt sau) → chia hồ sơ (mặc định 12 trang/bệnh nhân = 9 trang có nội dung; hoặc để AI tìm trang đầu).
2. Mỗi hồ sơ gửi cho Claude (đọc ảnh) → trả về JSON theo schema **tự sinh từ `config/fields.yaml`**.
3. `m03/transform.py` chuẩn hoá: ngày dd/MM/yyyy, đổi đơn vị xét nghiệm (g/dL→g/L, %→L/L…), khớp tỉnh/phường/nghề với
   các sheet danh mục **trong chính file mẫu**, ô tick → mã 1/0…
4. Bạn xem ảnh trang + sửa ô bị cảnh báo → xuất Excel. File xuất được tạo bằng cách ghi trực tiếp vào bản sao file mẫu
   (giữ nguyên dropdown, bảng, công thức tra cứu), bắt đầu từ dòng 5.

## Khi Medinet đổi mẫu
| Medinet… | Bạn làm |
|---|---|
| thêm cột | Mở app: tool liệt kê "cột mới chưa khai báo" và tạm điền theo dòng 5. Muốn đọc từ PDF → thêm 1 dòng trong `fields.yaml` (copy một dòng tương tự, đổi keyword + `ask`). |
| bỏ cột | Không cần làm gì. |
| đổi keyword | Sửa tên keyword trong `fields.yaml`. |
| thêm chuyên khoa khám | Thêm 1 dòng trong `specialties:` (tự sinh 4 cột). |
| đổi danh mục (đối tượng, đề nghị…) | Sửa `choices:` của trường đó. Phường/tỉnh/nghề lấy thẳng từ sheet trong mẫu nên tự cập nhật. |
| đổi vị trí cột công thức (GE/GG/GH) | Sửa `formula_cache` trong `settings.yaml`. |

Thứ tự cột, vị trí cột, dòng bắt đầu… **không** phải khai báo vì tool đọc từ dòng 4 của mẫu. Lưu `fields.yaml` là app tự nạp lại.

## Kiểm thử
`python -m pytest tests -q` (không cần API key; dùng dữ liệu AI giả lập trong `tests/fixtures`).

## Lưu ý
- Giả định cần bạn xác nhận (sửa trong yaml nếu khác cách đơn vị bạn làm): `MID#` → bạch cầu đơn nhân; `loai_kham` luôn = 2
  (định kỳ); cột "đối tượng khám", "hình thức chi trả", "đề nghị" dùng giá trị dòng 5 khi phiếu không ghi.
- Nghề nghiệp ghi tự do (vd "Stylist") không có trong danh mục → dùng mặc định "Lao động tự do" và có cảnh báo.
- Dữ liệu sức khỏe được gửi tới Anthropic API; kiểm tra quy định của đơn vị (Nghị định 13/2023/NĐ-CP về bảo vệ dữ liệu cá nhân).
