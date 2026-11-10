"""Đọc config/fields.yaml + settings.yaml và mở rộng thành danh sách Field."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

YESNO = {"1": "Có", "0": "Không"}
CLASSES = {"1": "Loại I", "2": "Loại II", "3": "Loại III", "4": "Loại IV", "5": "Loại V"}


@dataclass
class Field:
    keyword: str
    source: str = "skip"            # pdf | const | sample | derive | formula | skip
    type: str = "text"              # text | number | date | choice | multichoice
    ask: str = ""
    choices: dict[str, str] | None = None   # {giá_trị_Excel: tên_trên_phiếu}
    default: str | None = None
    post: str | None = None
    scale: float | None = None
    plausible: list[float] | None = None
    value: str | None = None        # cho source=const
    of: str | None = None           # cho source=derive
    city: str | None = None         # cho post=ward: keyword chứa tỉnh đã chuẩn hoá
    female_only: bool = False
    virtual: bool = False

    @property
    def needs_pdf(self) -> bool:
        return self.source == "pdf" or self.virtual


@dataclass
class Config:
    fields: dict[str, Field] = field(default_factory=dict)
    settings: dict = field(default_factory=dict)

    def pdf_fields(self) -> list[Field]:
        return [f for f in self.fields.values() if f.needs_pdf]


def _mk(keyword: str, spec: dict, **extra) -> Field:
    spec = {**spec, **extra}
    if spec.get("type") == "yesno":
        spec["type"], spec["choices"] = "choice", dict(YESNO)
    allowed = Field.__dataclass_fields__.keys()
    return Field(keyword=keyword, **{k: v for k, v in spec.items() if k in allowed})


def load_config(config_dir: Path | str = CONFIG_DIR) -> Config:
    config_dir = Path(config_dir)
    raw = yaml.safe_load((config_dir / "fields.yaml").read_text(encoding="utf-8"))
    settings = yaml.safe_load((config_dir / "settings.yaml").read_text(encoding="utf-8"))
    cfg = Config(settings=settings)

    for key, spec in raw["fields"].items():
        if key == "yesno_group":
            for kw, item in spec["items"].items():
                cfg.fields[kw] = _mk(kw, {
                    "source": "pdf", "type": "choice", "choices": dict(YESNO),
                    "ask": f'{spec["ask_prefix"]}{item["n"]} "{item["label"]}": ô Có hay Không được đánh dấu.',
                })
        elif key == "specialties":
            for sp in spec:
                p, name = sp["prefix"], sp["name"]
                kws = sp.get("keywords", {})
                fem = bool(sp.get("female_only"))
                k_chua = kws.get("chua", f"{p}_chuaphathienbatthuong")
                k_sobo = kws.get("sobo", f"{p}_chandoansobo_icd")
                k_xd = kws.get("xacdinh", f"{p}_chandoanxacdinh_icd")
                k_pl = kws.get("phanloai", f"{p}_phanloai")
                cfg.fields[k_chua] = _mk(k_chua, {
                    "source": "pdf", "type": "choice", "female_only": fem,
                    "choices": {"1": "Chưa phát hiện bất thường", "0": "Có bất thường/chẩn đoán"},
                    "ask": f"{name}: nếu ô 'Chưa phát hiện bất thường' được đánh dấu -> chọn 'Chưa phát hiện bất thường'; "
                           f"nếu thay vào đó đánh dấu 'Chẩn đoán sơ bộ/xác định' -> 'Có bất thường/chẩn đoán'; mục không được đánh dấu gì -> null.",
                })
                cfg.fields[k_sobo] = _mk(k_sobo, {
                    "source": "pdf", "type": "text", "post": "icd", "female_only": fem,
                    "ask": f"{name}: mã ICD viết ở dòng 'Chẩn đoán sơ bộ' (chỉ mã, vd J32.8; nhiều mã cách bằng dấu phẩy). Trống -> null.",
                })
                cfg.fields[k_xd] = _mk(k_xd, {
                    "source": "pdf", "type": "text", "post": "icd", "female_only": fem,
                    "ask": f"{name}: mã ICD ở dòng 'Chẩn đoán xác định'. Trống -> null.",
                })
                cfg.fields[k_pl] = _mk(k_pl, {
                    "source": "pdf", "type": "choice", "choices": dict(CLASSES), "female_only": fem,
                    "ask": f"{name}: 'Phân loại' Loại I-V được đánh dấu.",
                })
        else:
            cfg.fields[key] = _mk(key, spec)

    for key, spec in (raw.get("virtual") or {}).items():
        cfg.fields[key] = _mk(key, spec, source="pdf", virtual=True)
    return cfg
