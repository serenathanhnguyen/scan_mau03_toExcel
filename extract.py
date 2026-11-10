"""Gọi Claude (vision) để đọc hồ sơ. Schema gửi cho AI được SINH TỰ ĐỘNG từ fields.yaml,
nên thêm/bớt cột chỉ cần sửa yaml, không sửa code ở đây."""
from __future__ import annotations

import base64

import anthropic

from . import pdf as pdfmod
from .config import Config, Field

SYSTEM = """Bạn đọc hồ sơ "Giấy khám sức khỏe định kỳ - Mẫu 03" (Việt Nam) đã scan: phần lớn là chữ viết tay \
và ô tick trên mẫu in sẵn, kèm phiếu KẾT QUẢ XÉT NGHIỆM in máy. Các trang của MỘT bệnh nhân được gửi theo thứ tự.
Quy tắc:
- Chỉ ghi những gì NHÌN THẤY. Không suy đoán, không bịa. Không đọc được hoặc không có -> null.
- Ô vuông được coi là đã đánh dấu khi có dấu tick, x hoặc gạch chéo trong/đè lên ô. Ô trống in sẵn = chưa đánh dấu. \
Với cặp Có/Không, dấu thường nằm ở ô Không. Nếu không ô nào có dấu -> null.
- Chữ ký, con dấu, tên bác sĩ ở lề không phải dữ liệu. Chữ trong con dấu chữ nhật (vd "TÁI KHÁM ĐỊNH KỲ") có thể là nội dung "Đề nghị".
- Giữ nguyên tiếng Việt có dấu. Số: chép đúng như in/viết (kể cả dấu thập phân), không đổi đơn vị.
- Các trường ở `_uncertain`: liệt kê keyword của những trường bạn đọc còn nghi ngờ (chữ viết xấu, dấu tick mờ).
- Kiểm tra họ tên trên phiếu xét nghiệm có khớp bệnh nhân; nếu lệch hãy ghi vào `_notes`.
Gọi công cụ `ghi_ho_so` đúng một lần."""

TOOL_NAME = "ghi_ho_so"


def _prop(f: Field) -> dict:
    desc = f.ask or f.keyword
    if f.type == "choice":
        enum = list(f.choices.values()) + [None]
        return {"type": ["string", "null"], "enum": enum, "description": desc}
    if f.type == "multichoice":
        return {"type": "array", "items": {"type": "string", "enum": list(f.choices.values())}, "description": desc}
    return {"type": ["string", "null"], "description": desc + " (null nếu trống)"}


def build_tool(cfg: Config, keywords: set[str] | None = None) -> dict:
    """keywords: nếu truyền vào, chỉ hỏi những cột đang có trong file mẫu (giảm token)."""
    props = {}
    for f in cfg.pdf_fields():
        if f.virtual or keywords is None or f.keyword in keywords:
            props[f.keyword] = _prop(f)
    props["_uncertain"] = {"type": "array", "items": {"type": "string"},
                           "description": "keyword các trường đọc không chắc chắn"}
    props["_notes"] = {"type": ["string", "null"], "description": "ghi chú bất thường (tên lệch, trang thiếu...)"}
    return {"name": TOOL_NAME, "description": "Ghi kết quả đọc hồ sơ một bệnh nhân.",
            "input_schema": {"type": "object", "properties": props, "required": list(props)}}


def _img(b: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(b).decode()}}


def _call(client, cfg, content, tool) -> dict:
    s = cfg.settings["claude"]
    resp = client.messages.create(
        model=s["model"], max_tokens=s["max_tokens"], system=SYSTEM,
        tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": content}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("AI không trả kết quả có cấu trúc")


def extract_patient(client: anthropic.Anthropic, cfg: Config, pdf: bytes, pages: list[int],
                    keywords: set[str] | None = None) -> dict:
    p = cfg.settings["pdf"]
    imgs = pdfmod.render(pdf, pages, p["dpi"], p["jpeg_quality"])
    content = []
    for n, b in zip(pages, imgs):
        content += [{"type": "text", "text": f"[Trang {n + 1} của file PDF]"}, _img(b)]
    content.append({"type": "text", "text": "Đọc toàn bộ hồ sơ trên và ghi kết quả."})
    return _call(client, cfg, content, build_tool(cfg, keywords))


def detect_patient_starts(client: anthropic.Anthropic, cfg: Config, pdf: bytes, pages: list[int]) -> list[int]:
    """Nhờ AI chỉ ra trang đầu của mỗi hồ sơ (trang có tiêu đề 'Mẫu 3. MẪU GIẤY KHÁM SỨC KHỎE...')."""
    thumbs = pdfmod.render(pdf, pages, dpi=45, quality=60)
    content = []
    for n, b in zip(pages, thumbs):
        content += [{"type": "text", "text": f"[Trang {n + 1}]"}, _img(b)]
    content.append({"type": "text", "text": "Liệt kê số trang là TRANG ĐẦU của mỗi hồ sơ khám sức khỏe mới "
                    "(trang có tiêu đề 'Mẫu 3. MẪU GIẤY KHÁM SỨC KHỎE' và mục THÔNG TIN HÀNH CHÍNH)."})
    tool = {"name": "bao_cao", "description": "Trang bắt đầu hồ sơ",
            "input_schema": {"type": "object", "properties": {"start_pages": {"type": "array", "items": {"type": "integer"}}},
                             "required": ["start_pages"]}}
    out = _call(client, cfg, content, tool)
    return [int(x) - 1 for x in out["start_pages"]]
