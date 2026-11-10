"""Chuẩn hoá giá trị AI đọc được -> giá trị ghi vào Excel.

Thêm một hàm chuẩn hoá mới: viết hàm `def post_xxx(value, ctx)` rồi đăng ký vào POST,
sau đó dùng `post: xxx` trong fields.yaml. Hàm trả (giá_trị, cảnh_báo | None).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz, process

from .config import Config, Field


# ---------------------------------------------------------------- helpers
def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def norm(s: str) -> str:
    s = strip_accents(str(s)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def best_match(value: str, names: list[str], threshold: float, key=norm) -> tuple[str | None, float]:
    if not value or not names:
        return None, 0.0
    normed = {n: key(n) for n in names}
    hit = process.extractOne(key(value), normed, scorer=fuzz.WRatio)
    if hit and hit[1] >= threshold:
        return hit[2], hit[1]
    return None, hit[1] if hit else 0.0


@dataclass
class Lookups:
    """Danh mục lấy từ chính file mẫu Medinet (tự cập nhật khi Medinet đổi danh mục)."""
    provinces: dict[str, int] = field(default_factory=dict)             # tên -> CityId
    wards: list[tuple[str, int, int]] = field(default_factory=list)     # (tên, WardId, CityId)
    occupations: dict[str, int] = field(default_factory=dict)           # tên -> mã


@dataclass
class Ctx:
    cfg: Config
    lookups: Lookups
    rec: dict            # các giá trị đã chuẩn hoá của bệnh nhân này (theo thứ tự fields.yaml)
    field: Field


# ---------------------------------------------------------------- post functions
def post_upper(v, ctx): return v.upper(), None
def post_title(v, ctx): return v.strip().title(), None
def post_digits_list(v, ctx): return ",".join(re.findall(r"\d+", v)), None
def post_before_slash(v, ctx): return v.split("/")[0].strip(), None
def post_icd(v, ctx): return re.sub(r"\s+", "", v.upper()).replace(";", ","), None


def post_cccd(v, ctx):
    d = re.sub(r"\D", "", v)
    return d, (None if len(d) == 12 else f"CCCD có {len(d)} số (cần 12)")


def post_phone(v, ctx):
    d = re.sub(r"[^\d+]", "", v)
    if d.startswith("+84"):
        d = "0" + d[3:]
    elif d.startswith("84") and len(d) == 11:
        d = "0" + d[2:]
    return d, (None if re.fullmatch(r"0\d{9}", d) else "SĐT bất thường")


def post_bhyt(v, ctx):
    s = re.sub(r"[^0-9A-Za-z]", "", v).upper()
    return s, (None if not s or len(s) == 15 else f"Thẻ BHYT có {len(s)} ký tự (cần 15)")


def post_province(v, ctx):
    names = list(ctx.lookups.provinces)
    alias = ctx.cfg.settings.get("province_aliases", {})
    cand = alias.get(norm(v), v)
    thr = ctx.cfg.settings.get("match_threshold", {}).get("province", 80)
    hit, _ = best_match(cand, names, thr)
    return (hit, None) if hit else (v, f"Không khớp tỉnh/thành trong danh mục: {v!r}")


def _strip_prefix(s: str) -> str:
    return re.sub(r"^(phuong|xa|thi tran|dac khu)\s+", "", norm(s))


def post_ward(v, ctx):
    city_name = ctx.rec.get(ctx.field.city or "city_id", "")
    city_id = ctx.lookups.provinces.get(city_name)
    pool = [w for w in ctx.lookups.wards if city_id is None or w[2] == city_id]
    names = [w[0] for w in pool]
    thr = ctx.cfg.settings.get("match_threshold", {}).get("ward", 85)
    hit, _ = best_match(v, names, thr, key=_strip_prefix)
    return (hit, None) if hit else (v, f"Không khớp phường/xã trong danh mục: {v!r} (có thể là tên cũ)")


def post_occupation(v, ctx):
    thr = ctx.cfg.settings.get("match_threshold", {}).get("occupation", 88)
    hit, _ = best_match(v, list(ctx.lookups.occupations), thr)
    return (hit, None) if hit else (None, f"Nghề {v!r} không có trong danh mục -> dùng mặc định")


POST = {k[5:]: v for k, v in globals().items() if k.startswith("post_")}


# ---------------------------------------------------------------- generic converters
def parse_date(raw: str) -> str | None:
    s = str(raw).strip()
    m = re.search(r"(\d{1,2})\D+(\d{1,2})\D+(\d{4})", s)
    if not m and re.fullmatch(r"\d{8}", s):
        m = re.match(r"(\d{2})(\d{2})(\d{4})", s)
    if not m:
        return None
    d, mo, y = map(int, m.groups())
    try:
        date(y, mo, d)
    except ValueError:
        return None
    return f"{d:02d}/{mo:02d}/{y}"


def parse_number(raw: str, scale: float | None, sep: str) -> tuple[str | None, float | None]:
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(raw))
    if not m:
        return None, None
    x = float(m.group(0).replace(",", "."))
    if scale:
        x *= scale
    s = format(x, ".10g")
    return s.replace(".", sep), x


def _label_to_value(f: Field, label) -> str | None:
    if label is None or label == "":
        return None
    label = str(label)
    for val, lab in (f.choices or {}).items():
        if label == lab:
            return val
    for val in (f.choices or {}):       # AI trả thẳng mã
        if label == val:
            return val
    return None


# ---------------------------------------------------------------- main entry
def finalize(cfg: Config, raw: dict, sample: dict, keywords: set[str], lookups: Lookups,
             required: set[str]) -> tuple[dict, dict]:
    """raw: {keyword: giá trị AI trả}. Trả (rec, flags). rec: {keyword: str} cho MỌI cột có keyword trong mẫu."""
    sep = cfg.settings["excel"].get("decimal_separator", ",")
    fill_new = cfg.settings["excel"].get("fill_new_columns_from_sample", True)
    rec: dict[str, str] = {}
    flags: dict[str, list[str]] = {}

    def flag(kw, msg):
        flags.setdefault(kw, []).append(msg)

    for kw in raw.get("_uncertain") or []:
        flag(kw, "AI không chắc chắn - kiểm tra lại")

    for kw, f in cfg.fields.items():
        if kw not in keywords and not f.virtual:
            continue
        v: str = ""
        if f.source == "pdf":
            r = raw.get(kw)
            if f.type == "choice":
                v = _label_to_value(f, r) or ""
                if r not in (None, "") and not v:
                    flag(kw, f"Giá trị lạ: {r!r}")
            elif f.type == "multichoice":
                vals = [x for x in (_label_to_value(f, i) for i in (r or [])) if x]
                v = ", ".join(sorted(set(vals), key=list(f.choices).index))
            elif f.type == "date":
                if r:
                    d = parse_date(r)
                    v = d or ""
                    if not d:
                        flag(kw, f"Không đọc được ngày: {r!r}")
            elif f.type == "number":
                if r not in (None, ""):
                    s, x = parse_number(r, f.scale, sep)
                    v = s or ""
                    if s is None:
                        flag(kw, f"Không đọc được số: {r!r}")
                    elif f.plausible and not (f.plausible[0] <= x <= f.plausible[1]):
                        flag(kw, f"Ngoài khoảng thường gặp {f.plausible} (sau đổi đơn vị): {s} - kiểm tra đơn vị")
            else:
                v = str(r).strip() if r not in (None, "") else ""
        elif f.source == "const":
            v = str(f.value or "")
        elif f.source == "sample":
            v = sample.get(kw, "")
        elif f.source == "derive":
            v = str(raw.get(f.of) or "").strip()
        elif f.source in ("formula", "skip"):
            v = ""

        if v and f.post and f.post in POST:
            v, w = POST[f.post](v, Ctx(cfg, lookups, rec, f))
            v = v or ""
            if w:
                flag(kw, w)
        if f.female_only and rec.get("gioi_tinh") == "1":
            v = ""
        if not v and f.default is not None and f.source in ("pdf", "derive"):
            v = sample.get(kw, "") if f.default == "sample" else f.default
        rec[kw] = v

    # cột mới Medinet thêm, chưa khai báo
    for kw in keywords:
        if kw not in cfg.fields:
            rec[kw] = sample.get(kw, "") if fill_new else ""

    for kw in required:
        if kw in rec and kw in keywords and not rec[kw] and cfg.fields.get(kw, Field(kw)).source not in ("formula",):
            flag(kw, "Cột bắt buộc (*) còn trống")
    return {k: v for k, v in rec.items() if not k.startswith("_")}, flags
