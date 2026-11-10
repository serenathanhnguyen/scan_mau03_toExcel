"""Xử lý PDF scan: render trang, bỏ trang trắng, chia theo bệnh nhân."""
from __future__ import annotations

import fitz  # PyMuPDF


def page_count(pdf: bytes) -> int:
    with fitz.open(stream=pdf, filetype="pdf") as d:
        return d.page_count


def render(pdf: bytes, pages: list[int], dpi: int = 150, quality: int = 82) -> list[bytes]:
    """Render các trang (đánh số từ 0) thành JPEG."""
    out = []
    with fitz.open(stream=pdf, filetype="pdf") as d:
        for p in pages:
            pix = d[p].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
            out.append(pix.tobytes("jpeg", jpg_quality=quality))
    return out


def blank_flags(pdf: bytes, ink_ratio: float = 0.001) -> list[bool]:
    """True nếu trang gần như trắng (mặt sau tờ scan). Dấu mộc/chữ mờ lọt qua giấy vẫn coi là trắng."""
    flags = []
    with fitz.open(stream=pdf, filetype="pdf") as d:
        for page in d:
            pix = page.get_pixmap(dpi=72, colorspace=fitz.csGRAY)
            s = pix.samples
            dark = sum(1 for b in s if b < 100)
            flags.append(dark / max(len(s), 1) < ink_ratio)
    return flags


def split_fixed(n_pages: int, per_patient: int, blank: list[bool] | None = None, skip_blank=True) -> list[list[int]]:
    """Chia theo số trang cố định/bệnh nhân; trang trắng vẫn được tính vào chu kỳ nhưng không gửi đi."""
    groups = []
    for start in range(0, n_pages, per_patient):
        idx = list(range(start, min(start + per_patient, n_pages)))
        if skip_blank and blank:
            idx = [i for i in idx if not blank[i]]
        if idx:
            groups.append(idx)
    return groups


def split_by_starts(n_pages: int, starts: list[int], blank: list[bool] | None = None, skip_blank=True) -> list[list[int]]:
    starts = sorted(set(s for s in starts if 0 <= s < n_pages))
    if not starts or starts[0] != 0:
        starts = [0] + starts
    groups = []
    for a, b in zip(starts, starts[1:] + [n_pages]):
        idx = list(range(a, b))
        if skip_blank and blank:
            idx = [i for i in idx if not blank[i]]
        if idx:
            groups.append(idx)
    return groups
